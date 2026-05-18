# yuki_core/plugin.py
"""
Yuki 插件基类

定义 PlatformPlugin 和 CapabilityPlugin 的接口
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Any, Optional, TYPE_CHECKING
from pathlib import Path
import importlib
import yaml

from .models import PlatformEvent, YukiResponse

if TYPE_CHECKING:
    from .bus import ContextBus

try:
    from utils.logger import get_logger
except ImportError:
    import logging
    def get_logger(name):
        return logging.getLogger(name)

logger = get_logger("plugin")


class PlatformPlugin(ABC):
    """
    平台插件基类
    
    所有平台适配器必须继承此类
    """
    
    # === 必须定义的属性 ===
    name: str = "Unknown"           # 显示名称
    platform_id: str = "unknown"    # 唯一标识
    version: str = "0.1.0"          # 版本号
    
    # === 由 ContextBus 注入 ===
    bus: Optional['ContextBus'] = None
    identity_config: Optional[dict] = None
    
    def __init__(self, **config):
        """初始化"""
        self.config = config
    
    # === 生命周期方法 ===
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        建立连接
        
        Returns:
            bool: 是否成功
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass
    
    async def health_check(self) -> bool:
        """健康检查"""
        return True
    
    # === 消息收发 ===
    
    @abstractmethod
    async def receive(self) -> AsyncIterator[PlatformEvent]:
        """
        接收消息（异步迭代器）
        
        Yields:
            PlatformEvent: 统一格式事件
        """
        pass
    
    @abstractmethod
    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        """
        发送回复
        
        Args:
            event: 原始事件
            response: Yuki 的回复
            
        Returns:
            bool: 是否成功
        """
        pass
    
    # === 格式转换 ===
    
    @abstractmethod
    def translate_in(self, raw_data: Any) -> PlatformEvent:
        """将平台原始数据转为统一格式"""
        pass
    
    @abstractmethod
    def translate_out(self, event: PlatformEvent, response: YukiResponse) -> Any:
        """将 Yuki 回复转为平台格式"""
        pass
    
    # === 可选实现 ===
    
    async def on_connect(self) -> None:
        """连接成功回调"""
        pass
    
    async def on_disconnect(self) -> None:
        """断开连接回调"""
        pass
    
    def get_capabilities(self) -> list[str]:
        """返回该平台支持的能力列表"""
        return []


class CapabilityPlugin(ABC):
    """
    能力插件基类
    
    所有能力扩展必须继承此类
    """
    
    # === 必须定义的属性 ===
    name: str = "unknown"
    display_name: str = "Unknown"
    description: str = ""
    version: str = "0.1.0"
    
    # === JSON Schema ===
    # 注意：子类应在 __init__ 中设置，避免可变默认值共享问题
    parameters_schema: dict = None
    return_schema: dict = None
    
    # === 由 ContextBus 注入 ===
    bus: Optional['ContextBus'] = None
    
    @abstractmethod
    async def execute(self, params: dict) -> dict:
        """
        执行能力
        
        Args:
            params: 参数
            
        Returns:
            dict: 结果
        """
        pass
    
    def get_schema(self) -> dict:
        """获取能力描述"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "parameters": self.parameters_schema or {},
            "returns": self.return_schema or {},
        }
    
    async def validate_params(self, params: dict) -> bool:
        """验证参数"""
        return True
    
    def requires_permission(self) -> bool:
        """是否需要授权"""
        return False


# ================= 插件加载器 =================

def load_plugins_from_config(bus: 'ContextBus', config_path: str):
    """
    从配置文件加载插件
    
    Args:
        bus: 上下文总线
        config_path: 配置文件路径
    """
    config_path = Path(config_path)
    if not config_path.exists():
        logger.warning(f"[PluginLoader] 配置文件不存在: {config_path}")
        return
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 加载平台插件
    for name, plugin_config in config.get('platforms', {}).items():
        if plugin_config.get('enabled', False):
            _load_plugin(bus, name, plugin_config, 'platform')
    
    # 加载能力插件
    for name, plugin_config in config.get('capabilities', {}).items():
        if plugin_config.get('enabled', False):
            _load_plugin(bus, name, plugin_config, 'capability')
    
    logger.info(f"[PluginLoader] 加载完成: {len(bus.platform_plugins)} 平台, {len(bus.capability_plugins)} 能力")


def _load_plugin(bus: 'ContextBus', name: str, config: dict, plugin_type: str):
    """加载单个插件"""
    try:
        class_path = config.get('class', '')
        if not class_path:
            logger.warning(f"[PluginLoader] 插件 {name} 未指定 class")
            return

        # 动态导入
        if '.' not in class_path:
            logger.warning(f"[PluginLoader] 插件 {name} class 路径格式错误: {class_path}")
            return
        module_path, class_name = class_path.rsplit('.', 1)
        module = importlib.import_module(module_path)
        plugin_class = getattr(module, class_name)
        
        # 创建实例
        plugin_config = config.get('config', {})
        plugin = plugin_class(**plugin_config)
        
        # 注册
        if plugin_type == 'platform':
            bus.register_platform(plugin)
        else:
            bus.register_capability(plugin)
        
    except Exception as e:
        logger.error(f"[PluginLoader] 加载插件 {name} 失败: {e}")
