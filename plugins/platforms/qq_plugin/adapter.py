# plugins/platforms/qq_plugin/adapter.py
"""
QQ 适配器 - PlatformPlugin 实现

从 YukiV6 main.py 迁移消息缓冲/防抖/冒充检测逻辑
核心：NapCat原始数据 → PlatformEvent → Bus → Mind → Response → QQ
"""

import re
import time
import asyncio
import logging
from typing import AsyncIterator, Optional

from yuki_core.plugin import PlatformPlugin
from yuki_core.models import PlatformEvent, YukiResponse, Action, ActionType

from .napcat_ws import NapCatWS
from .cq_parser import CQCodeParser, smart_truncate
from .message_sender import MessageSender
from .group_manager import GroupManager

logger = logging.getLogger("qq_plugin")


class QQPlugin(PlatformPlugin):
    """QQ Bot 适配器"""
    
    name = "QQ Bot"
    platform_id = "qq"
    version = "1.0.0"
    
    def __init__(self, **config):
        super().__init__(**config)
        
        # 连接
        self.ws_url = config.get("napcat_ws_url", "ws://localhost:3001")
        self.ws_token = config.get("napcat_ws_token", "")
        
        # 目标
        self.target_groups = config.get("target_groups", [])
        self.target_qq = config.get("target_qq", 0)
        
        # 行为
        self.debounce_time = config.get("debounce_time", 32)
        self.max_message_length = config.get("max_message_length", 150)
        self.master_name = config.get("master_name", "主人")
        self.master_id = config.get("master_id", 0)
        self.robot_name = config.get("robot_name", "yuki")
        self.keywords = config.get("keywords", ["主人", "哥哥", "yuki"])
        
        # 子模块
        self.ws: Optional[NapCatWS] = None
        self.sender: Optional[MessageSender] = None
        self.parser: Optional[CQCodeParser] = None
        self.group_manager: Optional[GroupManager] = None
        
        # 消息缓冲
        self._buffers: dict[str, list[dict]] = {}
        self._buffer_tasks: dict[str, asyncio.Task] = {}
        self._real_time_debounce: float = self.debounce_time
        
        # 活跃度追踪 (兼容 YukiV6)
        self._group_activity: dict[str, float] = {}
        self._last_message_time: dict[str, float] = {}
    
    async def connect(self) -> bool:
        logger.info(f"[QQ] 连接 NapCat: {self.ws_url}")
        
        self.ws = NapCatWS(self.ws_url, self.ws_token)
        self.sender = MessageSender(self.ws)
        self.parser = CQCodeParser(self.ws)
        self.group_manager = GroupManager(master_id=self.master_id)
        
        connected = await self.ws.connect()
        if connected:
            logger.info("[QQ] ✅ 连接成功")
        else:
            logger.error("[QQ] ❌ 连接失败")
        return connected
    
    async def disconnect(self):
        for task in self._buffer_tasks.values():
            task.cancel()
        self._buffer_tasks.clear()
        if self.ws:
            await self.ws.disconnect()
        logger.info("[QQ] 已断开")
    
    async def receive(self) -> AsyncIterator[PlatformEvent]:
        """
        接收 QQ 消息
        
        核心循环：NapCat → 过滤 → 缓冲 → 转换 → yield
        """
        async for raw_data in self.ws.listen():
            if raw_data.get("post_type") != "message":
                continue
            
            msg_type = raw_data.get("message_type")
            user_id = raw_data.get("user_id", 0)
            raw_msg = raw_data.get("raw_message", "")
            
            # === 私聊 ===
            if msg_type == "private" and user_id == self.target_qq:
                event = self.translate_in(raw_data)
                if event:
                    yield event
            
            # === 群聊 ===
            elif msg_type == "group":
                group_id = raw_data.get("group_id", 0)
                gid_str = str(group_id)
                
                # 白名单
                if self.target_groups and group_id not in self.target_groups:
                    continue
                
                # 管理指令
                cmd_reply = self.group_manager.handle_command(raw_msg, user_id, group_id)
                if cmd_reply is not None:
                    await self.sender.send(group_id, cmd_reply, mode="group")
                    continue
                
                # 群开关
                if not self.group_manager.is_active(gid_str):
                    continue
                
                # === 消息缓冲防抖 ===
                await self._buffer_message(
                    group_id, raw_msg, raw_data, user_id
                )
    
    async def _buffer_message(
        self, group_id: int, raw_msg: str, raw_data: dict, user_id: int
    ):
        """
        消息缓冲防抖
        
        短时间内的多条消息合并处理，避免频繁调用 LLM
        """
        gid_str = str(group_id)
        self._last_message_time[gid_str] = time.time()
        
        sender_info = raw_data.get("sender", {})
        name = sender_info.get("card") or sender_info.get("nickname") or "路人"
        
        # 冒充检测
        is_fake = (name == self.master_name and user_id != self.master_id)
        if is_fake:
            name = f"{name}(冒充)"
        
        is_bot = "BOT" in name or "机器人" in name
        
        # 入队
        if gid_str not in self._buffers:
            self._buffers[gid_str] = []
        
        if not is_bot:
            content = f'【"{name}"】说: {raw_msg}'
            content = smart_truncate(content, max_len=self.max_message_length)
            
            self._buffers[gid_str].append({
                "name": name,
                "content": content,
                "raw_text": raw_msg,
                "is_bot": is_bot,
                "user_id": user_id,
            })
        
        # 被 @ 时缩短防抖
        if self.robot_name.lower() in raw_msg.lower():
            self._real_time_debounce = 5
        
        # 取消旧的防抖任务，启动新的
        if gid_str in self._buffer_tasks:
            self._buffer_tasks[gid_str].cancel()
        
        self._buffer_tasks[gid_str] = asyncio.create_task(
            self._process_buffer(group_id)
        )
    
    async def _process_buffer(self, group_id: int):
        """防抖到期后处理缓冲消息"""
        import asyncio as _asyncio  # 防御性导入
        gid_str = str(group_id)
        
        try:
            await _asyncio.sleep(self._real_time_debounce)
            self._real_time_debounce = self.debounce_time
            
            # 群开关再次检查
            if not self.group_manager.is_active(gid_str):
                self._buffers.pop(gid_str, None)
                return
            
            buffered = self._buffers.pop(gid_str, [])
            if not buffered:
                return
            
            # 合并所有消息
            combined_text = "\n".join(m["content"] for m in buffered)
            
            # CQ码解析 — 对齐 YukiV6 parser.parse_all_cq_codes()
            if self.parser:
                combined_text = await self.parser.parse_all(combined_text)
            combined_text = combined_text.replace("\n", " ").strip()
            
            # 找到第一个非 bot 的发送者作为主发送者
            main_sender = next(
                (m for m in buffered if not m["is_bot"]),
                buffered[0]
            )
            
            # 构建事件
            event = PlatformEvent(
                source="qq",
                event_type="message",
                content=combined_text,
                user_id=str(main_sender["user_id"]),
                user_name=main_sender["name"],
                session_id=gid_str,
                session_type="group",
                timestamp=time.time(),
                metadata={
                    "group_id": group_id,
                    "sender_name": main_sender["name"],
                    "is_fake": main_sender["name"].endswith("(冒充)"),
                    "message_count": len(buffered),
                },
            )
            
            # 通过 bus 处理
            if self.bus:
                response = await self.bus.receive(event)
                if response and response.text:
                    await self.send(event, response)
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[QQ] 缓冲处理异常: {e}")
    
    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        """发送回复到 QQ"""
        if not response.text or not response.text.strip():
            return True
        
        text = response.text
        
        # 清理
        text = re.sub(r'<布局>.*?</布局>', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'\s*FINISHED\s*$', '', text, flags=re.IGNORECASE).strip()
        text = re.sub(r'\n+', ' ', text).strip()
        
        if not text:
            return True
        
        # 拆分文本和表情包
        parts = re.split(r'(\[CQ:image,[^\]]*?sub_type=1\])', text, flags=re.IGNORECASE)
        
        chat_id = event.session_id
        mode = event.session_type
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            await self.sender.send(chat_id, part, mode=mode)
            await asyncio.sleep(1.0)
        
        logger.info(f"[QQ] 已回复 {chat_id}: {text[:80]}...")
        return True
    
    def translate_in(self, raw_data: dict) -> Optional[PlatformEvent]:
        """QQ 原始数据 → PlatformEvent"""
        msg_type = raw_data.get("message_type")
        user_id = str(raw_data.get("user_id", ""))
        raw_msg = raw_data.get("raw_message", "")
        
        sender_info = raw_data.get("sender", {})
        name = sender_info.get("card") or sender_info.get("nickname") or "路人"
        
        # 冒充检测
        is_fake = (name == self.master_name and user_id != str(self.master_id))
        if is_fake:
            name = f"{name}(冒充)"
        
        if msg_type == "group":
            group_id = raw_data.get("group_id", 0)
            session_id = str(group_id)
            session_type = "group"
            content = f'【"{name}"】说: {raw_msg}'
        else:
            session_id = user_id
            session_type = "private"
            content = raw_msg
        
        content = smart_truncate(content, max_len=self.max_message_length)
        
        return PlatformEvent(
            source="qq",
            event_type="message",
            content=content,
            user_id=user_id,
            user_name=name,
            session_id=session_id,
            session_type=session_type,
            timestamp=raw_data.get("time", time.time()),
            metadata={
                "raw_data": raw_data,
                "raw_message": raw_msg,
                "message_type": msg_type,
                "group_id": raw_data.get("group_id"),
                "sender_name": name,
                "is_fake": is_fake,
            },
        )
    
    def translate_out(self, event: PlatformEvent, response: YukiResponse) -> str:
        return response.text
    
    def get_capabilities(self) -> list[str]:
        return ["sticker_search", "meme_layout"]
