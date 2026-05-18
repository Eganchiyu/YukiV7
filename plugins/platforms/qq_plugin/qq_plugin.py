# plugins/platforms/qq_plugin/qq_plugin.py
"""
QQ Bot 适配器 (NapCat)
继承 PlatformPlugin，实现 QQ 平台的收发消息
"""

import asyncio
import time
import json
import re
from typing import AsyncIterator, Optional, Dict, Any
import logging

from yuki_core.plugin import PlatformPlugin
from yuki_core.models import PlatformEvent, YukiResponse, EventType, SessionType
from yuki_core.config import cfg

from .network import BotConnector
from .sender import MessageSender
from .parser import CQCodeParser
from .processor import MemeProcessor
from .sticker_manager import StickerManager

logger = logging.getLogger("qq_plugin")


class QQPlugin(PlatformPlugin):
    """
    QQ 平台插件
    
    通过 NapCat WebSocket 连接实现消息收发
    """
    
    name = "QQ Bot"
    platform_id = "qq"
    version = "0.1.0"
    
    def __init__(self, **config):
        super().__init__(**config)
        
        # 从配置中读取参数
        self.napcat_ws_url = config.get("napcat_ws_url", "ws://127.0.0.1:3001")
        self.napcat_ws_token = config.get("napcat_ws_token", "")
        self.target_groups = config.get("target_groups", [])
        self.target_qq = config.get("target_qq", 0)
        self.debounce_time = config.get("debounce_time", 32)
        self.max_message_length = config.get("max_message_length", 150)
        
        # 主人信息（由 main.py 注入）
        self.master_id = 0
        self.master_name = "主人"
        self.robot_name = "yuki"
        self.keywords = ["主人", "哥哥", "yuki"]
        
        # 核心组件
        self.connector: Optional[BotConnector] = None
        self.sender: Optional[MessageSender] = None
        self.parser: Optional[CQCodeParser] = None
        self.meme_processor: Optional[MemeProcessor] = None
        self.sticker_manager: Optional[StickerManager] = None
        
        # 消息缓冲（对齐 V6 的 message_buffer）
        self.message_buffer: Dict[int, list] = {}
        self.buffer_tasks: Dict[int, asyncio.Task] = {}
        
        # 群聊状态（对齐 V6 的 group_active_state）
        self.group_active_state: Dict[str, bool] = {}
        
        # 上次发送表情包的记录（用于 RLHF）
        self.last_sent_meme: Dict[str, str] = {}
        
        # 破冰失败计数器
        self.ice_break_fail_count: Dict[str, int] = {}
        
        # 运行状态
        self._running = False
        self._listen_task: Optional[asyncio.Task] = None
        
    async def connect(self) -> bool:
        """建立 WebSocket 连接"""
        try:
            self.connector = BotConnector(self.napcat_ws_url, self.napcat_ws_token)
            self.sender = MessageSender(self.connector)
            self.parser = CQCodeParser(self.connector)
            self.meme_processor = MemeProcessor()
            self.sticker_manager = StickerManager()
            
            # 测试连接
            ws = await self.connector.ensure_connection()
            if ws:
                logger.info(f"[QQ] 连接成功: {self.napcat_ws_url}")
                return True
            else:
                logger.error("[QQ] 连接失败")
                return False
        except Exception as e:
            logger.error(f"[QQ] 连接异常: {e}")
            return False
    
    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        # 取消所有未处理的 debounce 定时器
        for task in self.buffer_tasks.values():
            task.cancel()
        self.buffer_tasks.clear()
        if self.connector:
            await self.connector.close()
        logger.info("[QQ] 已断开连接")
    
    # ================= V6 消息缓冲 + 防抖 =================

    def _is_bot_message(self, sender_name: str) -> bool:
        """判断是否为机器人消息（V6 逻辑）"""
        return "BOT" in sender_name or "机器人" in sender_name

    def _is_bot_whitelisted(self, user_id: str) -> bool:
        """判断是否为白名单机器人（V6 逻辑：特定 QQ 即使带 BOT 前缀也放行）"""
        whitelist = self.config.BOT_WHITELIST if self.config else []
        return int(user_id) in whitelist if user_id and user_id.isdigit() else False

    def pop_buffer(self, chat_id) -> list:
        """原子化取出并清空缓冲区（对齐 V6 brain.pop_buffer）"""
        msgs = self.message_buffer.get(chat_id, [])
        self.message_buffer[chat_id] = []
        self.buffer_tasks.pop(chat_id, None)
        return msgs

    async def process_buffered(self, session_id: str, mode: str):
        """
        防抖回调：等待 N 秒后合并处理缓冲消息（对齐 V6 main_process）
        
        被 @yuki 时 N=5，否则 N=DEBOUNCE_TIME
        """
        debounce_time = self.debounce_time
        
        # 如果被 @ 了，缩短等待时间
        buffer = self.message_buffer.get(session_id, [])
        for msg in buffer:
            raw = msg.get("raw_text", "")
            if self.robot_name.lower() in raw.lower():
                debounce_time = 5
                break
        
        await asyncio.sleep(debounce_time)
        
        # 醒来后检查群是否被关闭（V6 终极拦截）
        if mode == "group" and not self.group_active_state.get(session_id, True):
            logger.info(f"[QQ] [{session_id}] 协程醒来，但群已被静音，丢弃遗留消息")
            self.pop_buffer(session_id)
            return
        
        # 弹出所有缓冲消息
        message_objs = self.pop_buffer(session_id)
        if not message_objs:
            return
        
        # 合并为一条消息（V6 逻辑：多条消息拼成一条给 LLM）
        combined_parts = []
        for msg in message_objs:
            name = msg.get("name", "???")
            content = msg.get("content", "")
            combined_parts.append(f'【"{name}"】说: {content}')
        
        combined_text = "\n".join(combined_parts)
        
        # 构造合并后的 PlatformEvent，交给 bus 处理
        from yuki_core.models import PlatformEvent, EventType, SessionType
        
        first = message_objs[0]
        event = PlatformEvent(
            source="qq",
            event_type=EventType.MESSAGE,
            content=combined_text,
            user_id=first.get("user_id", ""),
            user_name=first.get("name", ""),
            session_id=session_id,
            session_type=SessionType.GROUP if mode == "group" else SessionType.PRIVATE,
            metadata={
                "group_id": first.get("group_id"),
                "sender_name": first.get("name", ""),
                "message_count": len(message_objs),
                "is_combined": len(message_objs) > 1,
                "is_bot": all(m.get("is_bot", False) for m in message_objs),
            }
        )
        
        # 通过 bus 处理
        if self.bus:
            response = await self.bus.receive(event)
            if response and response.text:
                await self.send(event, response)

    async def receive(self) -> AsyncIterator[PlatformEvent]:
        """
        接收消息（异步迭代器）
        
        V6 逻辑：消息先入缓冲区，防抖 N 秒后合并处理
        """
        self._running = True
        
        while self._running:
            try:
                async for data in self.connector.listen():
                    if not self._running:
                        break
                    
                    # 只处理消息事件
                    if data.get("post_type") != "message":
                        continue
                    
                    event = self.translate_in(data)
                    if not event:
                        continue
                    
                    # === V6 防抖逻辑 ===
                    session_id = event.session_id
                    
                    # 过滤机器人消息（V6 逻辑：BOT 消息默认不进缓冲，白名单除外）
                    sender_name = event.user_name
                    user_id = event.user_id
                    is_bot = self._is_bot_message(sender_name)
                    is_whitelisted = self._is_bot_whitelisted(user_id)
                    
                    if is_bot and not is_whitelisted:
                        # 非白名单机器人消息：不进缓冲，但仍更新 last_message_time
                        if self.bus:
                            self.bus._last_message_time[session_id] = time.time()
                        continue
                    
                    # 追踪最后消息时间（用于日记空闲判断）
                    if self.bus:
                        self.bus._last_message_time[session_id] = time.time()
                    
                    # 入队（V6 逻辑：白名单 bot 也进缓冲，标记 is_bot）
                    if session_id not in self.message_buffer:
                        self.message_buffer[session_id] = []
                    
                    raw_text = event.metadata.get("raw_message", event.content)
                    self.message_buffer[session_id].append({
                        "name": sender_name,
                        "content": event.content,
                        "raw_text": raw_text,
                        "user_id": user_id,
                        "group_id": event.metadata.get("group_id"),
                        "is_bot": is_bot,
                    })
                    
                    # 取消旧定时器，启动新定时器
                    if session_id in self.buffer_tasks:
                        self.buffer_tasks[session_id].cancel()
                    
                    mode = "group" if event.session_type == SessionType.GROUP else "private"
                    self.buffer_tasks[session_id] = asyncio.create_task(
                        self.process_buffered(session_id, mode)
                    )
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[QQ] 监听异常: {e}")
                await asyncio.sleep(3)
    
    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        """
        发送回复
        
        Args:
            event: 原始事件
            response: Yuki 的回复
            
        Returns:
            bool: 是否成功
        """
        try:
            # 获取会话信息
            session_id = event.session_id
            session_type = event.session_type
            
            # 处理回复文本
            text = response.text
            if not text:
                return True
            
            # 拆分文本和表情包（对齐 V6 的逻辑）
            parts = re.split(r'(\[CQ:image,[^\]]*?sub_type=1\])', text, flags=re.IGNORECASE)
            
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                # 发送消息
                await self.sender.send(
                    int(session_id),
                    part,
                    mode=session_type
                )
                
                # 模拟人类节奏
                if len(parts) > 1:
                    await asyncio.sleep(1.0)
            
            return True
            
        except Exception as e:
            logger.error(f"[QQ] 发送失败: {e}")
            return False
    
    def translate_in(self, raw_data: dict) -> Optional[PlatformEvent]:
        """
        将 NapCat 原始数据转为 PlatformEvent
        
        Args:
            raw_data: NapCat WebSocket 数据
            
        Returns:
            PlatformEvent: 统一格式事件
        """
        try:
            msg_type = raw_data.get("message_type")
            user_id = str(raw_data.get("user_id", ""))
            raw_msg = raw_data.get("raw_message", "")
            
            # 私聊消息
            if msg_type == "private" and user_id == str(self.target_qq):
                return PlatformEvent(
                    source="qq",
                    event_type=EventType.MESSAGE,
                    content=raw_msg,
                    user_id=user_id,
                    user_name="主人",
                    session_id=user_id,
                    session_type=SessionType.PRIVATE,
                    metadata={
                        "raw_data": raw_data,
                    }
                )
            
            # 群聊消息
            elif msg_type == "group":
                group_id = raw_data.get("group_id")
                gid_str = str(group_id)
                
                # 检查目标群白名单
                if self.target_groups and group_id not in self.target_groups:
                    return None
                
                # 检查群聊开关
                if not self.group_active_state.get(gid_str, True):
                    return None
                
                # 获取发送者信息
                sender_info = raw_data.get("sender", {})
                name = sender_info.get("card") or sender_info.get("nickname") or "路人"
                
                # 检测冒充
                is_fake = name == self.master_name and user_id != str(self.master_id)
                if is_fake:
                    name = f"{name}(冒充)"
                
                # 处理群聊开关命令
                msg_clean = raw_msg.strip()
                if msg_clean in ["/关闭", "/开启"]:
                    # 这些命令需要特殊处理，返回一个特殊事件
                    return PlatformEvent(
                        source="qq",
                        event_type=EventType.SYSTEM,
                        content=msg_clean,
                        user_id=user_id,
                        user_name=name,
                        session_id=gid_str,
                        session_type=SessionType.GROUP,
                        metadata={
                            "command": msg_clean,
                            "group_id": group_id,
                            "is_master": user_id == str(self.master_id),
                        }
                    )
                
                # 格式化消息内容
                content = f'【"{name}"】说: {raw_msg}'
                
                return PlatformEvent(
                    source="qq",
                    event_type=EventType.MESSAGE,
                    content=content,
                    user_id=user_id,
                    user_name=name,
                    session_id=gid_str,
                    session_type=SessionType.GROUP,
                    metadata={
                        "group_id": group_id,
                        "sender_name": name,
                        "raw_message": raw_msg,
                        "raw_data": raw_data,
                    }
                )
            
            return None
            
        except Exception as e:
            logger.error(f"[QQ] 解析事件失败: {e}")
            return None
    
    def translate_out(self, event: PlatformEvent, response: YukiResponse) -> dict:
        """
        将 YukiResponse 转为 NapCat 格式
        
        Args:
            event: 原始事件
            response: Yuki 的回复
            
        Returns:
            dict: NapCat 格式数据
        """
        # 这个方法在 send 中已经处理，这里返回空字典
        return {}
    
    # ================= 可选实现 =================
    
    async def on_connect(self) -> None:
        """连接成功回调"""
        logger.info("[QQ] 连接成功回调")
    
    async def on_disconnect(self) -> None:
        """断开连接回调"""
        logger.info("[QQ] 断开连接回调")
    
    def get_capabilities(self) -> list[str]:
        """返回该平台支持的能力列表"""
        return ["sticker_search", "meme_layout", "ai_voice"]