# Plugin 接口规范

> Phase 0 产物 - 插件系统接口定义

## 1. 插件类型

| 类型 | 基类 | 用途 | 示例 |
|------|------|------|------|
| PlatformPlugin | `PlatformPlugin` | 平台适配器，负责消息收发 | QQ, Web, Voice |
| CapabilityPlugin | `CapabilityPlugin` | 能力扩展，提供可调用的功能 | 搜索, 文件操作 |
| SocialPlugin | `SocialPlugin` | 社交优化（可选加载） | 表情包, 破冰 |

## 2. PlatformPlugin 接口

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from dataclasses import dataclass

class PlatformPlugin(ABC):
    """平台插件基类"""
    
    # === 必须定义的属性 ===
    name: str              # 显示名称，如 "QQ Bot"
    platform_id: str       # 唯一标识，如 "qq", "web", "voice"
    version: str           # 版本号，如 "1.0.0"
    
    # === 由 ContextBus 注入 ===
    bus: 'ContextBus' = None
    identity_config: dict = None
    
    # === 生命周期方法 ===
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        建立与平台的连接
        
        Returns:
            bool: 连接是否成功
            
        异常:
            ConnectionError: 连接失败
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接，清理资源"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            bool: 是否健康
        """
        pass
    
    # === 消息收发 ===
    
    @abstractmethod
    async def receive(self) -> AsyncIterator[PlatformEvent]:
        """
        接收消息（异步迭代器）
        
        Yields:
            PlatformEvent: 统一格式的事件
            
        实现要求:
            - 内部循环监听平台消息
            - 调用 translate_in() 转换格式
            - yield PlatformEvent 对象
            - 连接断开时自动重连
        """
        pass
    
    @abstractmethod
    async def send(self, event: PlatformEvent, response: 'YukiResponse') -> bool:
        """
        发送回复
        
        Args:
            event: 原始事件（用于获取目标位置）
            response: Yuki的回复
            
        Returns:
            bool: 是否发送成功
        """
        pass
    
    # === 格式转换 ===
    
    @abstractmethod
    def translate_in(self, raw_data: any) -> PlatformEvent:
        """
        将平台原始数据转为统一格式
        
        Args:
            raw_data: 平台特有的原始数据
            
        Returns:
            PlatformEvent: 统一格式事件
        """
        pass
    
    @abstractmethod
    def translate_out(self, event: PlatformEvent, response: 'YukiResponse') -> any:
        """
        将Yuki回复转为平台格式
        
        Args:
            event: 原始事件
            response: Yuki的回复
            
        Returns:
            any: 平台特有的发送格式
        """
        pass
    
    # === 可选实现 ===
    
    async def on_connect(self) -> None:
        """连接成功后的回调"""
        pass
    
    async def on_disconnect(self) -> None:
        """断开连接后的回调"""
        pass
    
    def get_platform_capabilities(self) -> list[str]:
        """返回该平台支持的能力列表"""
        return []
```

## 3. CapabilityPlugin 接口

```python
class CapabilityPlugin(ABC):
    """能力插件基类"""
    
    # === 必须定义的属性 ===
    name: str              # 能力名称，如 "web_search"
    display_name: str      # 显示名称，如 "联网搜索"
    description: str       # 功能描述
    version: str           # 版本号
    
    # === JSON Schema ===
    parameters_schema: dict = {}
    """
    定义参数格式，供Yuki理解如何调用
    
    示例:
    {
        "query": {
            "type": "string",
            "description": "搜索关键词",
            "required": True
        },
        "num_results": {
            "type": "integer",
            "description": "返回结果数量",
            "default": 5,
            "required": False
        }
    }
    """
    
    return_schema: dict = {}
    """
    定义返回格式
    
    示例:
    {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "snippet": {"type": "string"}
                }
            }
        }
    }
    """
    
    # === 核心方法 ===
    
    @abstractmethod
    async def execute(self, params: dict) -> dict:
        """
        执行能力
        
        Args:
            params: 符合 parameters_schema 的参数
            
        Returns:
            dict: 符合 return_schema 的结果
            
        异常:
            ValueError: 参数错误
            RuntimeError: 执行失败
        """
        pass
    
    def get_schema(self) -> dict:
        """
        返回完整的能力描述，供Core决策使用
        
        Returns:
            dict: {
                "name": "web_search",
                "display_name": "联网搜索",
                "description": "搜索互联网获取实时信息",
                "parameters": {...},
                "returns": {...}
            }
        """
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "parameters": self.parameters_schema,
            "returns": self.return_schema
        }
    
    # === 可选实现 ===
    
    async def validate_params(self, params: dict) -> bool:
        """验证参数是否有效"""
        return True
    
    def requires_permission(self) -> bool:
        """是否需要用户授权才能执行"""
        return False
    
    async def get_permission_prompt(self) -> str:
        """返回权限请求提示"""
        return f"Yuki 想要使用 {self.display_name} 功能，是否允许？"
```

## 4. SocialPlugin 接口

```python
class SocialPlugin(ABC):
    """社交优化插件（QQ群聊等场景）"""
    
    # === 必须定义的属性 ===
    name: str              # 如 "sticker_system"
    display_name: str      # 如 "表情包系统"
    target_platforms: list[str]  # 适用的平台，如 ["qq"]
    
    # === 核心方法 ===
    
    @abstractmethod
    async def should_activate(self, event: PlatformEvent, context: dict) -> bool:
        """
        判断是否应该激活该社交优化
        
        Args:
            event: 当前事件
            context: 上下文信息
            
        Returns:
            bool: 是否激活
        """
        pass
    
    @abstractmethod
    async def process(self, event: PlatformEvent, response: 'YukiResponse') -> 'YukiResponse':
        """
        处理/增强Yuki的回复
        
        Args:
            event: 原始事件
            response: Yuki的原始回复
            
        Returns:
            YukiResponse: 增强后的回复（可能添加表情包等）
        """
        pass
```

## 5. 插件注册与加载

### 5.1 插件配置文件

```yaml
# configs/plugins.yaml

platforms:
  qq:
    enabled: true
    class: "plugins.platforms.qq_plugin.QQPlugin"
    config:
      napcat_ws_url: "ws://localhost:3001"
      napcat_ws_token: ""
      target_groups: [789012]
      
  web:
    enabled: false
    class: "plugins.platforms.web_plugin.WebPlugin"
    config:
      host: "127.0.0.1"
      port: 8080
      
  voice:
    enabled: false
    class: "plugins.platforms.voice_plugin.VoicePlugin"
    config:
      whisper_model: "base"
      tts_model: "gpt-sovits"

capabilities:
  web_search:
    enabled: true
    class: "plugins.capabilities.web_search.WebSearchPlugin"
    config:
      api_key: ""
      
  file_manager:
    enabled: true
    class: "plugins.capabilities.file_manager.FileManagerPlugin"
    config:
      allowed_dirs: ["~/Documents", "~/Desktop"]
      
  code_executor:
    enabled: false
    class: "plugins.capabilities.code_executor.CodeExecutorPlugin"
    config:
      timeout: 30
      max_memory: "512MB"

social:
  sticker_system:
    enabled: true
    class: "plugins.social.sticker_system.StickerSystem"
    platforms: ["qq"]
    
  ice_breaker:
    enabled: true
    class: "plugins.social.ice_breaker.IceBreaker"
    platforms: ["qq"]
```

### 5.2 插件加载器

```python
# yuki_core/plugin_loader.py

import importlib
from pathlib import Path
from typing import Optional

class PluginLoader:
    """插件加载器"""
    
    def __init__(self, bus: 'ContextBus'):
        self.bus = bus
        self.plugins = {}
    
    async def load_from_config(self, config_path: str) -> None:
        """从配置文件加载插件"""
        import yaml
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 加载平台插件
        for name, plugin_config in config.get('platforms', {}).items():
            if plugin_config.get('enabled', False):
                await self._load_platform_plugin(name, plugin_config)
        
        # 加载能力插件
        for name, plugin_config in config.get('capabilities', {}).items():
            if plugin_config.get('enabled', False):
                await self._load_capability_plugin(name, plugin_config)
        
        # 加载社交插件
        for name, plugin_config in config.get('social', {}).items():
            if plugin_config.get('enabled', False):
                await self._load_social_plugin(name, plugin_config)
    
    async def _load_platform_plugin(self, name: str, config: dict) -> Optional[PlatformPlugin]:
        """加载单个平台插件"""
        try:
            class_path = config['class']
            module_path, class_name = class_path.rsplit('.', 1)
            
            module = importlib.import_module(module_path)
            plugin_class = getattr(module, class_name)
            
            plugin = plugin_class(**config.get('config', {}))
            plugin.bus = self.bus
            
            await plugin.connect()
            self.bus.register_plugin(plugin)
            self.plugins[name] = plugin
            
            print(f"[PluginLoader] 已加载平台插件: {name}")
            return plugin
            
        except Exception as e:
            print(f"[PluginLoader] 加载平台插件失败 {name}: {e}")
            return None
    
    async def _load_capability_plugin(self, name: str, config: dict) -> Optional[CapabilityPlugin]:
        """加载单个能力插件"""
        # 类似实现...
        pass
    
    async def _load_social_plugin(self, name: str, config: dict) -> Optional[SocialPlugin]:
        """加载单个社交插件"""
        # 类似实现...
        pass
    
    async def unload_all(self) -> None:
        """卸载所有插件"""
        for name, plugin in self.plugins.items():
            if isinstance(plugin, PlatformPlugin):
                await plugin.disconnect()
        self.plugins.clear()
```

## 6. 插件开发示例

### 6.1 最小化平台插件

```python
# plugins/platforms/example_plugin/__init__.py

from yuki_core.plugin import PlatformPlugin
from yuki_core.models import PlatformEvent, YukiResponse

class ExamplePlugin(PlatformPlugin):
    name = "Example"
    platform_id = "example"
    version = "1.0.0"
    
    def __init__(self, **config):
        self.config = config
        self.connected = False
    
    async def connect(self) -> bool:
        print(f"[Example] 连接到 {self.config.get('endpoint', 'N/A')}")
        self.connected = True
        return True
    
    async def disconnect(self) -> None:
        self.connected = False
        print("[Example] 已断开")
    
    async def health_check(self) -> bool:
        return self.connected
    
    async def receive(self):
        """示例：从标准输入接收"""
        while self.connected:
            # 实际实现应该是从WebSocket/API接收
            line = await asyncio.get_event_loop().run_in_executor(None, input)
            event = PlatformEvent(
                source="example",
                event_type="message",
                content=line,
                user_id="user1",
                session_id="session1",
                session_type="private"
            )
            yield event
    
    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        print(f"[Example] 发送: {response.text}")
        return True
    
    def translate_in(self, raw_data: any) -> PlatformEvent:
        return PlatformEvent(
            source="example",
            event_type="message",
            content=raw_data.get("text", ""),
            user_id=raw_data.get("user_id", "unknown"),
            session_id=raw_data.get("session_id", "default")
        )
    
    def translate_out(self, event: PlatformEvent, response: YukiResponse) -> any:
        return {"text": response.text}
```

### 6.2 最小化能力插件

```python
# plugins/capabilities/example_cap/__init__.py

from yuki_core.plugin import CapabilityPlugin

class ExampleCapPlugin(CapabilityPlugin):
    name = "example_calc"
    display_name = "计算器"
    description = "执行简单的数学计算"
    version = "1.0.0"
    
    parameters_schema = {
        "expression": {
            "type": "string",
            "description": "数学表达式，如 '2+3*4'",
            "required": True
        }
    }
    
    return_schema = {
        "result": {
            "type": "number",
            "description": "计算结果"
        }
    }
    
    async def execute(self, params: dict) -> dict:
        expression = params["expression"]
        
        # 安全的数学表达式求值
        import ast
        try:
            tree = ast.parse(expression, mode='eval')
            result = eval(compile(tree, '<string>', 'eval'))
            return {"result": result}
        except Exception as e:
            raise ValueError(f"计算错误: {e}")
```

---

*Created: 2026-05-17 | Phase 0*
