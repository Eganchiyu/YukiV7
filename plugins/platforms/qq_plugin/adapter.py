# plugins/platforms/qq_plugin/adapter.py
"""
QQ 适配器 - PlatformPlugin 实现

从 YukiV6 main.py 的消息缓冲/防抖/冒充检测逻辑迁移
核心职责：将 QQ 消息转为 PlatformEvent，将 YukiResponse 转回 QQ 消息
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
from .prompts import get_qq_group_extra_prompt, get_qq_private_extra_prompt

logger = logging.getLogger("qq_plugin")


class QQPlugin(PlatformPlugin):
    """
    QQ Bot 适配器
    
    通过 NapCat WebSocket 与 QQ 通信
    实现 PlatformPlugin 接口
    """
    
    name = "QQ Bot"
    platform_id = "qq"
    version = "1.0.0"
    
    def __init__(self, **config):
        super().__init__(**config)
        
        # NapCat 连接配置
        self.ws_url = config.get("napcat_ws_url", "ws://localhost:3001")
        self.ws_token = config.get("napcat_ws_token", "")
        
        # 目标配置
        self.target_groups = config.get("target_groups", [])
        self.target_qq = config.get("target_qq", 0)
        
        # 行为配置
        self.debounce_time = config.get("debounce_time", 32)
        self.max_message_length = config.get("max_message_length", 150)
        self.master_name = config.get("master_name", "主人")
        self.master_id = config.get("master_id", 0)
        self.robot_name = config.get("robot_name", "yuki")
        self.keywords = config.get("keywords", ["主人", "哥哥", "yuki"])
        
        # 子模块（在 connect 时初始化）
        self.ws: Optional[NapCatWS] = None
        self.sender: Optional[MessageSender] = None
        self.parser: Optional[CQCodeParser] = None
        self.group_manager: Optional[GroupManager] = None
        
        # 消息缓冲系统
        self._buffers: dict[str, list[dict]] = {}
        self._buffer_tasks: dict[str, asyncio.Task] = {}
        self._real_time_debounce: float = self.debounce_time
        
        # 冒充检测缓存
        self._self_id: str = ""
    
    async def connect(self) -> bool:
        """建立与 NapCat 的 WebSocket 连接"""
        logger.info(f"[QQ] 正在连接 NapCat: {self.ws_url}")
        
        # 初始化子模块
        self.ws = NapCatWS(self.ws_url, self.ws_token)
        self.sender = MessageSender(self.ws)
        self.parser = CQCodeParser(self.ws)
        self.group_manager = GroupManager(
            master_id=self.master_id,
        )
        
        connected = await self.ws.connect()
        if connected:
            logger.info("[QQ] ✅ NapCat 连接成功")
        else:
            logger.error("[QQ] ❌ NapCat 连接失败")
        return connected
    
    async def disconnect(self) -> None:
        """断开连接"""
        # 取消所有缓冲任务
        for task in self._buffer_tasks.values():
            task.cancel()
        self._buffer_tasks.clear()
        
        if self.ws:
            await self.ws.disconnect()
        logger.info("[QQ] 已断开连接")
    
    async def receive(self) -> AsyncIterator[PlatformEvent]:
        """
        接收 QQ 消息并转为 PlatformEvent
        
        这是核心循环：NapCat 原始数据 → 过滤 → 转换 → yield
        """
        async for raw_data in self.ws.listen():
            # 只处理消息事件
            if raw_data.get("post_type") != "message":
                continue
            
            msg_type = raw_data.get("message_type")
            user_id = raw_data.get("user_id", 0)
            raw_msg = raw_data.get("raw_message", "")
            
            # === 私聊模式 ===
            if msg_type == "private" and user_id == self.target_qq:
                event = self.translate_in(raw_data)
                if event:
                    yield event
            
            # === 群聊模式 ===
            elif msg_type == "group":
                group_id = raw_data.get("group_id", 0)
                gid_str = str(group_id)
                
                # 白名单检查
                if self.target_groups and group_id not in self.target_groups:
                    continue
                
                # 管理指令拦截（/开启、/关闭）
                cmd_reply = self.group_manager.handle_command(
                    raw_msg, user_id, group_id
                )
                if cmd_reply is not None:
                    await self.sender.send(group_id, cmd_reply, mode="group")
                    continue
                
                # 群开关检查
                if not self.group_manager.is_active(gid_str):
                    continue
                
                # 转换为事件
                event = self.translate_in(raw_data)
                if event:
                    yield event
    
    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        """
        发送 Yuki 的回复到 QQ
        
        处理：
        1. 文本中的表情包标签 [MEME_SEARCH:...]
        2. 布局标签 <布局>...</布局> 的清理
        3. 多段消息的节奏控制
        """
        if not response.text or not response.text.strip():
            return True
        
        text = response.text
        
        # 清理布局标签（不应发送给用户）
        text = re.sub(r'<布局>.*?</布局>', '', text, flags=re.DOTALL).strip()
        
        # 清理 FINISHED 标记
        text = re.sub(r'\s*FINISHED\s*$', '', text, flags=re.IGNORECASE).strip()
        
        # 合并换行
        text = re.sub(r'\n+', ' ', text).strip()
        
        if not text:
            return True
        
        # 拆分文本和表情包 CQ 码
        parts = re.split(r'(\[CQ:image,[^\]]*?sub_type=1\])', text, flags=re.IGNORECASE)
        
        session_type = event.session_type
        chat_id = event.session_id
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            await self.sender.send(chat_id, part, mode=session_type)
            # 模拟人类发消息节奏
            await asyncio.sleep(1.0)
        
        logger.info(f"[QQ] 已回复 {chat_id}: {text[:80]}...")
        return True
    
    def translate_in(self, raw_data: dict) -> Optional[PlatformEvent]:
        """
        QQ 原始数据 → PlatformEvent
        
        处理：
        - 消息类型识别（群聊/私聊）
        - 发送者名称提取
        - 冒充检测
        - 消息缓冲格式化
        """
        msg_type = raw_data.get("message_type")
        user_id = str(raw_data.get("user_id", ""))
        raw_msg = raw_data.get("raw_message", "")
        
        sender_info = raw_data.get("sender", {})
        name = sender_info.get("card") or sender_info.get("nickname") or "路人"
        
        # === 冒充检测 ===
        is_fake = (
            name == self.master_name
            and user_id != str(self.master_id)
        )
        if is_fake:
            logger.warning(
                f"[QQ] 检测到疑似冒充消息: {name} (QQ:{user_id})"
            )
            name = f"{name}(冒充)"
        
        # === 会话信息 ===
        if msg_type == "group":
            group_id = raw_data.get("group_id", 0)
            session_id = str(group_id)
            session_type = "group"
            # 群聊格式化：【"姓名"】说: 消息内容
            content = f'【"{name}"】说: {raw_msg}'
        else:
            session_id = user_id
            session_type = "private"
            content = raw_msg
        
        # 截断超长消息
        content = smart_truncate(content, max_len=self.max_message_length)
        
        # 构建事件
        event = PlatformEvent(
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
        
        return event
    
    def translate_out(self, event: PlatformEvent, response: YukiResponse) -> str:
        """YukiResponse → QQ 消息格式"""
        return response.text
    
    def get_capabilities(self) -> list[str]:
        """该平台支持的能力"""
        return ["sticker_search", "meme_layout"]
