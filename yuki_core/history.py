# yuki_core/history.py
"""
历史记录管理器

从 YukiV6 core/history_manager.py 简化迁移
职责：加载/保存/追加对话历史
"""

import os
import json
import threading
import logging
import datetime

logger = logging.getLogger("history")


class HistoryManager:
    """
    对话历史管理
    
    - 按 session_id 分组存储
    - 线程安全
    - 原子化写入（防崩溃丢数据）
    """
    
    def __init__(self, history_file: str = "./data/chat_history.json"):
        self.history_file = history_file
        self._cache = None
        self._lock = threading.Lock()
    
    def load(self) -> dict:
        """加载所有历史（带缓存）"""
        with self._lock:
            if self._cache is None:
                self._cache = self._read_from_disk()
            return self._cache
    
    def _read_from_disk(self) -> dict:
        """从磁盘读取"""
        if not os.path.exists(self.history_file):
            return {}
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"[History] 加载失败: {e}")
            return {}
    
    def save(self, data: dict):
        """原子化保存"""
        with self._lock:
            self._cache = data
            temp_file = f"{self.history_file}.tmp"
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.history_file)), exist_ok=True)
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(temp_file, self.history_file)
            except Exception as e:
                logger.error(f"[History] 保存失败: {e}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
    
    def get_chat(self, session_id: str) -> list:
        """获取某个会话的历史（返回副本，防止外部修改污染缓存）"""
        data = self.load()
        return list(data.get(str(session_id), []))
    
    def append_message(self, session_id: str, role: str, content: str, time_str: str = None):
        """追加一条消息"""
        data = self.load()
        cid = str(session_id)
        if cid not in data:
            data[cid] = []
        
        msg = {"role": role, "content": content}
        if time_str:
            msg["time"] = time_str
        else:
            msg["time"] = datetime.datetime.now().strftime("%Y年%m月%d日%H:%M")
        
        data[cid].append(msg)
        self.save(data)
        return data[cid]
    
    def append_to_log(self, chat_id, sender, message):
        """追加到日志文件"""
        log_file = os.path.join(os.path.dirname(self.history_file), "yuki_log.txt")
        time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{time_str}] [{chat_id}] {sender}: {message}\n"
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"[History] 写日志失败: {e}")
    
    def inject_whisper(self, session_id: str, message: str):
        """注入悄悄话"""
        history = self.load()
        cid = str(session_id)
        if cid in history:
            history[cid].append({
                "role": "assistant",
                "content": f"【主人对Yuki的悄悄话】：{message}"
            })
            self.save(history)
            logger.info(f"[History] 悄悄话已注入 {session_id}")
            return True
        return False
