# plugins/platforms/qq_plugin/group_manager.py
"""
群聊开关管理

从 YukiV6 main.py 的群聊状态管理逻辑提取
职责：管理群聊的开启/关闭状态，持久化到文件
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger("group_manager")


class GroupManager:
    """
    群聊开关管理器
    
    功能：
    - 管理每个群聊的开关状态（默认开启）
    - 持久化状态到 JSON 文件
    - 处理 /开启 /关闭 指令的权限验证
    """
    
    def __init__(
        self,
        state_file: str = "data/group_state.json",
        master_id: int = 0,
        master_only_commands: list = None,
    ):
        self.state_file = state_file
        self.master_id = master_id
        self.master_only_commands = master_only_commands or ["/开启", "/关闭"]
        self._state: dict[str, bool] = {}
        self._load_state()
    
    def _load_state(self):
        """从文件加载群聊状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
                logger.info(f"[GroupMgr] 已加载 {len(self._state)} 个群聊状态")
            except Exception as e:
                logger.warning(f"[GroupMgr] 读取群聊状态失败: {e}")
                self._state = {}
        else:
            self._state = {}
    
    def _save_state(self):
        """保存群聊状态到文件"""
        try:
            os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[GroupMgr] 保存群聊状态失败: {e}")
    
    def is_active(self, group_id: str) -> bool:
        """检查群聊是否处于开启状态（默认开启）"""
        return self._state.get(str(group_id), True)
    
    def set_active(self, group_id: str, active: bool):
        """设置群聊开关状态"""
        self._state[str(group_id)] = active
        self._save_state()
        state_text = "开启" if active else "关闭"
        logger.info(f"[GroupMgr] 群 {group_id} 已{state_text}")
    
    def handle_command(self, raw_message: str, user_id: int, group_id: int) -> Optional[str]:
        """
        处理群聊管理指令
        
        Args:
            raw_message: 原始消息文本
            user_id: 发送者 QQ 号
            group_id: 群号
            
        Returns:
            str: 回复消息（如果是指令的话），None 表示不是管理指令
        """
        msg = raw_message.strip()
        
        if msg in self.master_only_commands:
            if user_id != self.master_id:
                return "只有哥哥大人才能操作哦！"
            
            if msg == "/关闭":
                self.set_active(str(group_id), False)
                return "Yuki 已进入休眠模式，不打扰大家啦~"
            
            elif msg == "/开启":
                self.set_active(str(group_id), True)
                return "Yuki 重新上线"
        
        return None
