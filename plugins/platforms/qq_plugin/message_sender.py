# plugins/platforms/qq_plugin/message_sender.py
"""
消息发送器

从 YukiV6 network/ws_sender.py 重构，移除 config 依赖
"""

import json
import os
import asyncio
import logging

logger = logging.getLogger("message_sender")


class MessageSender:
    """
    QQ 消息发送器
    
    通过 NapCat WebSocket 发送消息，支持自动重试
    """
    
    def __init__(self, ws, max_retries: int = 3):
        """
        Args:
            ws: NapCatWS 实例
            max_retries: 最大重试次数
        """
        self.ws = ws
        self.max_retries = max_retries
    
    async def send(self, chat_id, message: str, mode: str = "private") -> bool:
        """
        发送消息
        
        Args:
            chat_id: 群号或 QQ 号
            message: 消息内容（可以包含 CQ 码）
            mode: "private" 或 "group"
            
        Returns:
            bool: 是否发送成功
        """
        for attempt in range(self.max_retries):
            try:
                action = "send_private_msg" if mode == "private" else "send_group_msg"
                params = {
                    "message": message,
                    "user_id" if mode == "private" else "group_id": int(chat_id),
                }
                await self.ws.send_raw({"action": action, "params": params})
                return True
            except Exception as e:
                logger.error(f"[Sender] 发送失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                self.ws.websocket = None  # 标记连接失效
                if attempt == self.max_retries - 1:
                    return False
                await asyncio.sleep(1)
        return False
    
    async def send_local_image(self, chat_id, local_path: str, mode: str = "private") -> bool:
        """发送本地图片"""
        abs_path = os.path.abspath(local_path)
        cq_image = f"[CQ:image,file=file:///{abs_path}]"
        return await self.send(chat_id, cq_image, mode=mode)
    
    async def send_local_voice(self, chat_id, local_path: str, mode: str = "group") -> bool:
        """发送本地语音"""
        abs_path = os.path.abspath(local_path)
        cq_record = f"[CQ:record,file=file:///{abs_path}]"
        return await self.send(chat_id, cq_record, mode=mode)
    
    async def send_ai_voice(self, chat_id, text: str, character_id: str, mode: str = "group") -> bool:
        """发送 AI 语音"""
        for attempt in range(self.max_retries):
            try:
                params = {
                    "group_id": int(chat_id),
                    "character": str(character_id),
                    "text": text,
                }
                await self.ws.send_raw({"action": "send_group_ai_record", "params": params})
                return True
            except Exception as e:
                logger.error(f"[Sender] AI语音发送失败 (尝试 {attempt + 1}): {e}")
                self.ws.websocket = None
                if attempt == self.max_retries - 1:
                    return False
                await asyncio.sleep(1)
        return False
