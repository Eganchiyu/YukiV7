# yuki_core/models.py
"""
Yuki Agent 核心数据模型
平台无关，所有平台共享这些数据结构
"""

from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """事件类型枚举"""
    MESSAGE = "message"      # 文本消息
    IMAGE = "image"          # 图片消息
    FILE = "file"            # 文件消息
    VOICE = "voice"          # 语音消息
    ACTION = "action"        # 动作事件
    SYSTEM = "system"        # 系统事件


class SessionType(str, Enum):
    """会话类型枚举"""
    PRIVATE = "private"
    GROUP = "group"
    CHANNEL = "channel"


class ActionType(str, Enum):
    """行动类型枚举"""
    REPLY = "reply"          # 回复消息
    IGNORE = "ignore"        # 忽略
    CAPABILITY = "capability"  # 调用能力插件
    DELEGATE = "delegate"    # 委托小女仆


@dataclass
class Attachment:
    """附件信息"""
    type: str                # "image", "file", "voice"
    url: str = ""            # 远程URL
    path: str = ""           # 本地路径
    name: str = ""           # 文件名
    size: int = 0            # 文件大小(bytes)
    is_meme: bool = False    # 是否是表情包


@dataclass
class PlatformEvent:
    """
    跨平台统一事件格式
    
    所有平台的消息统一转换为这个格式，供 Core 处理
    """
    
    # === 必填字段 ===
    source: str              # 来源平台: "qq", "web", "voice", "discord"
    event_type: str          # 事件类型: "message", "image", "file", "voice"
    content: str             # 主要内容（文本/描述）
    
    # === 用户信息 ===
    user_id: str             # 平台内用户ID
    user_name: str = ""      # 用户显示名称
    
    # === 会话信息 ===
    session_id: str = ""     # 会话ID（群聊=group_id, 私聊=user_id, 网页=session_id）
    session_type: str = ""   # "private", "group", "channel"
    
    # === 时间 ===
    timestamp: float = 0.0   # Unix时间戳
    
    # === 平台特有数据 ===
    metadata: dict = field(default_factory=dict)
    """
    QQ平台特有:
      - group_id: 群号
      - sender_name: 发送者群名片
      - reply_to: 回复的消息ID
      - at_users: 被@的用户列表
    
    Web平台特有:
      - session_token: 认证token
    
    Voice平台特有:
      - audio_duration: 语音时长
      - confidence: 语音识别置信度
    """
    
    # === 图片/文件 ===
    attachments: list = field(default_factory=list)
    
    # === 上下文注入(由Bus层填充) ===
    identity_context: Optional[dict] = None
    """
    由ContextBus注入，包含:
    - platform_prompt: 该平台的身份提示词
    - capabilities: 该平台可用的能力列表
    - style_override: 风格覆盖配置
    """
    
    def __post_init__(self):
        """初始化后处理"""
        if self.timestamp == 0.0:
            self.timestamp = datetime.now().timestamp()
    
    @property
    def time_str(self) -> str:
        """返回格式化的时间字符串"""
        return datetime.fromtimestamp(self.timestamp).strftime("%Y年%m月%d日%H:%M")
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "source": self.source,
            "event_type": self.event_type,
            "content": self.content,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "session_id": self.session_id,
            "session_type": self.session_type,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "attachments": self.attachments,
        }


@dataclass
class Action:
    """
    Yuki 的行动
    
    表示 Yuki 想做什么（回复、忽略、调用能力等）
    """
    type: str                # ActionType 的值
    content: str = ""        # 回复内容
    capability: str = ""     # 能力插件名称
    params: dict = field(default_factory=dict)  # 能力参数
    metadata: dict = field(default_factory=dict)  # 附加数据


@dataclass
class YukiResponse:
    """
    Yuki 的回复
    
    包含回复内容和可能的行动列表
    """
    text: str = ""           # 回复文本
    actions: list = field(default_factory=list)  # Action 列表
    metadata: dict = field(default_factory=dict)  # 附加数据
    
    @property
    def has_actions(self) -> bool:
        """是否有行动"""
        return len(self.actions) > 0
    
    def add_action(self, action: Action):
        """添加行动"""
        self.actions.append(action)


@dataclass
class Decision:
    """
    决策结果
    
    表示 Yuki 决定做什么
    """
    action: str              # "reply", "observe", "ignore"
    confidence: float = 1.0  # 置信度 0.0~1.0
    reason: str = ""         # 决策原因（调试用）


@dataclass
class IdentityContext:
    """
    身份上下文
    
    包含 Yuki 在特定平台的身份配置
    """
    platform: str            # 平台标识
    context_prompt: str      # 该平台的上下文提示词
    style_override: dict = field(default_factory=dict)  # 风格覆盖
    capabilities: list = field(default_factory=list)     # 可用能力列表
    
    def get_system_prompt(self, base_persona: str) -> str:
        """
        构建完整的 system prompt
        
        Args:
            base_persona: 基础人格定义
            
        Returns:
            str: 完整的 system prompt
        """
        parts = [base_persona]
        
        if self.context_prompt:
            parts.append(f"\n## 【当前平台】\n{self.context_prompt}")
        
        return "\n".join(parts)


@dataclass
class MemoryEntry:
    """
    记忆条目
    
    存储在记忆系统中的单条记忆
    """
    id: str                  # 唯一标识
    content: str             # 记忆内容（日记/对话摘要）
    source: str = ""         # 来源平台（可选）
    session_id: str = ""     # 会话ID（可选）
    timestamp: float = 0.0   # 时间戳
    embedding: list = field(default_factory=list)  # 向量嵌入
    metadata: dict = field(default_factory=dict)   # 元数据
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = datetime.now().timestamp()
