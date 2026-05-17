# yuki_core - Yuki Agent 核心人格内核

"""
Yuki Core 是 Yuki Agent 的核心模块，包含：

- identity: 身份定义
- memory: 记忆系统
- mind: 决策引擎
- maid: 小女仆系统
- bus: 上下文总线
- plugin: 插件基类
- models: 数据模型

本模块独立于任何平台，可单独使用和测试。
"""

__version__ = "0.1.0"
__author__ = "Eganchiyu"

# 数据模型
from .models import (
    PlatformEvent, YukiResponse, Decision, Action,
    EventType, SessionType, ActionType,
    Attachment, IdentityContext, MemoryEntry
)

# 身份定义
from .identity import (
    YukiIdentity, Persona, SpeakingStyle, Relationship,
    get_system_prompt, get_base_setting,
    get_yuki_setting_private, get_yuki_setting_group,
    get_summary_prompt, VISION_PROMPT
)

# 决策引擎
from .mind import YukiMind, EnergySystem, ActivityTracker, DesireCalculator, BioClock

# 记忆系统
from .memory import YukiMemory

# 小女仆系统
from .maid import MaidAgent

# 历史记录管理
from .history import HistoryManager

# LLM 调用器
from .llm import chat_completion, robust_chat

# 配置中心
from .config import cfg

# 上下文总线
from .bus import ContextBus

# 插件基类
from .plugin import PlatformPlugin, CapabilityPlugin, load_plugins_from_config

__all__ = [
    # 版本
    "__version__",
    
    # 数据模型
    "PlatformEvent", "YukiResponse", "Decision", "Action",
    "EventType", "SessionType", "ActionType",
    "Attachment", "IdentityContext", "MemoryEntry",
    
    # 身份
    "YukiIdentity", "Persona", "SpeakingStyle", "Relationship",
    "get_system_prompt", "get_base_setting",
    "get_yuki_setting_private", "get_yuki_setting_group",
    "get_summary_prompt", "VISION_PROMPT",
    
    # 决策
    "YukiMind", "EnergySystem", "ActivityTracker", 
    "DesireCalculator", "BioClock",
    
    # 记忆
    "YukiMemory",
    
    # 小女仆
    "MaidAgent",
    
    # 总线
    "ContextBus",
    
    # 插件
    "PlatformPlugin", "CapabilityPlugin", "load_plugins_from_config",
]
