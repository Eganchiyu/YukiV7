# plugins/platforms/qq_plugin/cq_parser.py
"""
CQ码解析器

从 YukiV6 的 CQParser.py + CQProtocol.py + GetMeta.py 合并重构
职责：解析和替换 QQ 消息中的 CQ 码（@、回复、图片等）
"""

import re
import logging
from typing import Optional, Dict

logger = logging.getLogger("cq_parser")


# ================= CQ码工具函数 =================

def smart_truncate(content: str, max_len: int = 150, suffix: str = "...") -> str:
    """
    智能截断超长消息，保留 CQ 码完整性
    从 YukiV6 CQProtocol.py 迁移
    """
    if len(content) <= max_len:
        return content
    
    logger.info(f"[CQ] 检测到超长消息 ({len(content)} 字符)")
    parts = re.split(r'(\[CQ:.*?\])', content)
    result = []
    
    for part in parts:
        if not part:
            continue
        if part.startswith('[CQ:') and part.endswith(']'):
            result.append(part)
        else:
            if len(part) > 100:
                half = 40
                part = part[:half] + suffix + part[-half:]
            result.append(part)
    
    content = ''.join(result)
    logger.info(f"[CQ] 压缩后长度: {len(content)} 字符")
    return content


def replace_media_cq_codes(text: str) -> str:
    """把所有多媒体 CQ 码换成占位符"""
    text = re.sub(r'\[CQ:image[^\]]*\]', '[图片]', text)
    text = re.sub(r'\[CQ:face[^\]]*\]', '[表情]', text)
    text = re.sub(r'\[CQ:record[^\]]*\]', '[语音]', text)
    text = re.sub(r'\[CQ:video[^\]]*\]', '[视频]', text)
    text = re.sub(r'\[CQ:file[^\]]*\]', '[文件]', text)
    text = re.sub(r'\[CQ:json[^\]]*\]', '[小程序]', text)
    return text


def is_at_me(text: str, self_id: str) -> bool:
    """检查消息是否 @ 了 Yuki"""
    return f"[CQ:at,qq={self_id}]" in text


def extract_at_uids(text: str) -> list:
    """提取消息中所有被 @ 的 QQ 号"""
    return re.findall(r'\[CQ:at,qq=(\d+|all)\]', text)


def extract_reply_ids(text: str) -> list:
    """提取消息中所有引用回复的消息 ID"""
    return re.findall(r'\[CQ:reply,id=(\d+)\]', text)


def replace_at_placeholder(text: str, qq: str, nickname: str) -> str:
    """将特定的 @ CQ 码替换为昵称"""
    pattern = rf'\[CQ:at,qq={re.escape(qq)}[^\]]*\]'
    return re.sub(pattern, f"@{nickname}", text)


def replace_reply_placeholder(reply_data: Optional[dict]) -> str:
    """将回复消息数据替换为可读文本"""
    if not reply_data:
        return "【引用不明历史消息】"
    sender = reply_data.get("sender", {}).get("nickname", "人")
    raw_msg = reply_data.get("raw_message", "")
    # 去掉回复消息中的 CQ 码
    text = re.sub(r'\[CQ:.*?\]', '', raw_msg)
    text = smart_truncate(text)
    return f"【引用{sender}的消息: {text}】"


# ================= MetaGetter (NapCat API 请求) =================

class MetaGetter:
    """通过 NapCat WebSocket 获取消息元数据"""
    
    def __init__(self, ws):
        """
        Args:
            ws: NapCatWS 实例（用于 send_request）
        """
        self.ws = ws
    
    async def get_user_info(self, user_id: str) -> Optional[Dict]:
        """获取用户信息（昵称等）"""
        try:
            uid = int(user_id) if user_id.isdigit() else user_id
            response = await self.ws.send_request(
                "get_stranger_info",
                {"user_id": uid, "no_cache": False},
                f"get_user_{user_id}"
            )
            if response and response.get("retcode") == 0:
                return response.get("data")
        except Exception as e:
            logger.error(f"[Meta] 获取用户信息失败: {e}")
        return None
    
    async def get_reply_text(self, msg_id: str) -> Optional[dict]:
        """获取被回复消息的内容"""
        try:
            response = await self.ws.send_request(
                "get_msg",
                {"message_id": int(msg_id)},
                f"rp_{msg_id}"
            )
            if response and response.get("status") == "ok":
                return response.get("data")
        except Exception as e:
            logger.error(f"[Meta] 获取回复消息失败: {e}")
        return None


# ================= CQCodeParser =================

class CQCodeParser:
    """
    CQ码解析器
    
    整合了原 YukiV6 的 CQParser + CQProtocol + GetMeta 三个模块
    职责：解析消息中的 CQ 码，替换为可读文本
    """
    
    def __init__(self, ws):
        """
        Args:
            ws: NapCatWS 实例
        """
        self.ws = ws
        self.meta = MetaGetter(ws)
        self.nickname_cache: Dict[str, str] = {}
    
    async def get_user_nickname(self, user_id: str) -> str:
        """获取用户昵称（带缓存）"""
        if user_id in self.nickname_cache:
            return self.nickname_cache[user_id]
        if user_id.lower() == "all":
            return "全体成员"
        
        user_info = await self.meta.get_user_info(user_id)
        if user_info and user_info.get("nickname"):
            nickname = user_info["nickname"]
            self.nickname_cache[user_id] = nickname
            return nickname
        return f"用户{user_id}"
    
    async def parse_at_codes(self, text: str) -> str:
        """替换所有 @ CQ 码为昵称"""
        uids = extract_at_uids(text)
        for uid in set(uids):
            name = await self.get_user_nickname(uid)
            text = replace_at_placeholder(text, uid, name)
        return text
    
    async def parse_reply_codes(self, text: str) -> str:
        """替换所有引用回复 CQ 码"""
        reply_ids = extract_reply_ids(text)
        for mid in reply_ids:
            reply_data = await self.meta.get_reply_text(mid)
            replacement = replace_reply_placeholder(reply_data)
            text = text.replace(f"[CQ:reply,id={mid}]", replacement)
        return text
    
    async def parse_all(self, text: str) -> str:
        """
        完整解析：回复 → @ → 其他多媒体码
        """
        text = await self.parse_reply_codes(text)
        text = await self.parse_at_codes(text)
        text = replace_media_cq_codes(text)
        return text
    
    def is_at_me(self, text: str, self_id: str) -> bool:
        """检查是否 @ 了 Yuki"""
        return is_at_me(text, self_id)
