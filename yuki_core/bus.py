# yuki_core/bus.py
"""
Yuki 上下文总线

连接 Core 和 Plugins，统一事件流转
"""

import asyncio
from typing import Optional, TYPE_CHECKING

from .models import PlatformEvent, YukiResponse, Action, ActionType

if TYPE_CHECKING:
    from .mind import YukiMind
    from .identity import YukiIdentity
    from .plugin import PlatformPlugin, CapabilityPlugin

try:
    from utils.logger import get_logger
except ImportError:
    import logging
    def get_logger(name):
        return logging.getLogger(name)

logger = get_logger("bus")


class ContextBus:
    """
    上下文总线
    
    连接 Core (Mind, Identity) 和 Plugins，统一事件流转
    """
    
    def __init__(
        self,
        mind: 'YukiMind',
        identity: 'YukiIdentity'
    ):
        self.mind = mind
        self.identity = identity
        
        # 插件注册表
        self.platform_plugins: dict[str, 'PlatformPlugin'] = {}
        self.capability_plugins: dict[str, 'CapabilityPlugin'] = {}
        
        # 运行状态
        self._running = False
        self._tasks: list[asyncio.Task] = []
    
    def register_platform(self, plugin: 'PlatformPlugin'):
        """注册平台插件"""
        self.platform_plugins[plugin.platform_id] = plugin
        plugin.bus = self  # 注入反向引用
        logger.info(f"[Bus] 已注册平台插件: {plugin.name} ({plugin.platform_id})")
    
    def register_capability(self, plugin: 'CapabilityPlugin'):
        """注册能力插件"""
        self.capability_plugins[plugin.name] = plugin
        plugin.bus = self
        logger.info(f"[Bus] 已注册能力插件: {plugin.name}")
    
    def get_platform(self, platform_id: str) -> Optional['PlatformPlugin']:
        """获取平台插件"""
        return self.platform_plugins.get(platform_id)
    
    def get_capability(self, name: str) -> Optional['CapabilityPlugin']:
        """获取能力插件"""
        return self.capability_plugins.get(name)
    
    async def start(self):
        """启动所有平台监听"""
        if self._running:
            logger.warning("[Bus] 已经在运行中")
            return
        
        self._running = True
        logger.info("[Bus] 启动上下文总线...")
        
        # 为每个平台插件创建监听任务
        for platform_id, plugin in self.platform_plugins.items():
            task = asyncio.create_task(
                self._listen_platform(plugin),
                name=f"bus-{platform_id}"
            )
            self._tasks.append(task)
            logger.info(f"[Bus] 已启动 {platform_id} 监听")
        
        # 等待所有任务完成（或被取消）
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
    
    async def stop(self):
        """停止所有监听"""
        self._running = False
        
        for task in self._tasks:
            task.cancel()
        
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        
        # 断开所有插件
        for plugin in self.platform_plugins.values():
            try:
                await plugin.disconnect()
            except Exception as e:
                logger.error(f"[Bus] 断开 {plugin.name} 失败: {e}")
        
        logger.info("[Bus] 已停止")
    
    async def _listen_platform(self, plugin: 'PlatformPlugin'):
        """监听单个平台"""
        try:
            # 确保连接
            connected = await plugin.connect()
            if not connected:
                logger.error(f"[Bus] {plugin.name} 连接失败")
                return
            
            # 监听消息
            async for event in plugin.receive():
                if not self._running:
                    break
                
                # 处理事件
                response = await self.receive(event)
                
                # 发送回复
                if response and response.text:
                    await plugin.send(event, response)
                    
        except asyncio.CancelledError:
            logger.info(f"[Bus] {plugin.name} 监听已取消")
        except Exception as e:
            logger.error(f"[Bus] {plugin.name} 监听异常: {e}")
    
    async def receive(self, event: PlatformEvent) -> Optional[YukiResponse]:
        """
        接收事件并处理
        
        这是核心入口，任何平台的消息都通过这里进入
        """
        logger.debug(f"[Bus] 收到事件: source={event.source}, user={event.user_id}, content={event.content[:50]}...")
        
        # 1. 注入身份上下文
        platform_identity = self.identity.get_platform_identity(event.source)
        event.identity_context = {
            "platform_prompt": platform_identity.context_prompt,
            "capabilities": platform_identity.capabilities,
            "style_override": platform_identity.style_override,
        }
        
        # 2. 交给 Mind 处理
        response = await self.mind.process(event)
        
        # 3. 检查是否需要调用能力插件
        if response and response.has_actions:
            for action in response.actions:
                if action.type == ActionType.CAPABILITY:
                    await self._execute_capability(action)
        
        return response
    
    async def _execute_capability(self, action: Action):
        """执行能力插件"""
        cap = self.capability_plugins.get(action.capability)
        if not cap:
            logger.warning(f"[Bus] 能力插件不存在: {action.capability}")
            return
        
        try:
            result = await cap.execute(action.params)
            logger.info(f"[Bus] 能力执行完成: {action.capability}")
            # TODO: 将结果反馈给 Mind
        except Exception as e:
            logger.error(f"[Bus] 能力执行失败: {e}")
    
    def get_capabilities_schema(self) -> list[dict]:
        """获取所有能力的 schema（供 Mind 使用）"""
        return [cap.get_schema() for cap in self.capability_plugins.values()]
    
    def get_status(self) -> dict:
        """获取总线状态"""
        return {
            "running": self._running,
            "platforms": list(self.platform_plugins.keys()),
            "capabilities": list(self.capability_plugins.keys()),
            "tasks": len(self._tasks),
        }
