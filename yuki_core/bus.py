# yuki_core/bus.py
"""
Yuki 上下文总线（精简版）

连接 Core 和 Plugins，统一事件流转
后台任务（日记/破冰/精力衰减）已移至 QQ 插件层
"""

import asyncio
import time
import logging
from typing import Optional, TYPE_CHECKING

from .models import PlatformEvent, YukiResponse, Action, ActionType
from .llm import chat_completion, robust_chat, close_session

if TYPE_CHECKING:
    from .mind import YukiMind
    from .identity import YukiIdentity
    from .plugin import PlatformPlugin, CapabilityPlugin
    from .history import HistoryManager
    from .memory import YukiMemory

logger = logging.getLogger("bus")


class ContextBus:
    """
    上下文总线

    连接 Core (Mind, Identity) 和 Plugins，统一事件流转
    """

    def __init__(
        self,
        mind: 'YukiMind',
        identity: 'YukiIdentity',
        config=None,
    ):
        self.mind = mind
        self.identity = identity
        self.config = config  # yuki_core.config.cfg

        # 插件
        self.platform_plugins: dict[str, 'PlatformPlugin'] = {}
        self.capability_plugins: dict[str, 'CapabilityPlugin'] = {}

        # 子系统（由 main.py 注入）
        self.history: Optional['HistoryManager'] = None
        self.memory: Optional['YukiMemory'] = None

        # 运行状态
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # 消息时间追踪（用于日记空闲判断，对齐 V6 last_message_time）
        self._last_message_time: dict[str, float] = {}

    # ================= 插件注册 =================

    def register_platform(self, plugin: 'PlatformPlugin'):
        self.platform_plugins[plugin.platform_id] = plugin
        plugin.bus = self
        logger.info(f"[Bus] 注册平台: {plugin.name} ({plugin.platform_id})")

    def register_capability(self, plugin: 'CapabilityPlugin'):
        self.capability_plugins[plugin.name] = plugin
        plugin.bus = self
        logger.info(f"[Bus] 注册能力: {plugin.name}")

    def get_platform(self, platform_id: str) -> Optional['PlatformPlugin']:
        return self.platform_plugins.get(platform_id)

    def get_capability(self, name: str) -> Optional['CapabilityPlugin']:
        return self.capability_plugins.get(name)

    # ================= 生命周期 =================

    async def start(self):
        if self._running:
            return

        self._running = True
        logger.info("[Bus] 启动...")

        # 启动平台监听
        for pid, plugin in self.platform_plugins.items():
            task = asyncio.create_task(
                self._listen_platform(plugin),
                name=f"bus-{pid}"
            )
            self._tasks.append(task)

        # 启动各插件的后台任务（由插件自己注册）
        for pid, plugin in self.platform_plugins.items():
            if hasattr(plugin, 'get_background_tasks'):
                for task_coro in plugin.get_background_tasks():
                    task = asyncio.create_task(task_coro, name=f"bg-{pid}-{task_coro.__name__}")
                    self._tasks.append(task)

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        for plugin in self.platform_plugins.values():
            try:
                await plugin.disconnect()
            except Exception as e:
                logger.error(f"[Bus] 断开 {plugin.name} 失败: {e}")

        close_session()
        logger.info("[Bus] 已停止")

    # ================= 事件处理 =================

    async def _listen_platform(self, plugin: 'PlatformPlugin'):
        try:
            connected = await plugin.connect()
            if not connected:
                logger.error(f"[Bus] {plugin.name} 连接失败")
                return

            async for event in plugin.receive():
                if not self._running:
                    break

                response = await self.receive(event)

                if response and response.text:
                    await plugin.send(event, response)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[Bus] {plugin.name} 监听异常: {e}")

    async def receive(self, event: PlatformEvent) -> Optional[YukiResponse]:
        """
        核心入口：接收事件 → Mind处理 → 返回响应

        这是所有平台消息的统一入口
        """
        logger.debug(f"[Bus] 事件: {event.source} | {event.user_name}: {event.content[:50]}...")

        # 追踪消息到达时间（用于日记空闲判断）
        self._last_message_time[event.session_id] = time.time()

        # === V6 decide_to_reply 逻辑（防 bot 无限套娃）===
        if not self._decide_to_reply(event):
            logger.info(f"[Bus] {event.session_id} 仅 BOT 在召唤，欲望打折，跳过回复")
            return None

        # 注入身份上下文
        platform_identity = self.identity.get_platform_identity(event.source)
        event.identity_context = {
            "platform_prompt": platform_identity.context_prompt,
            "capabilities": platform_identity.capabilities,
            "style_override": platform_identity.style_override,
        }

        # 构建 LLM 调用器
        llm_caller = self._make_llm_caller()

        # 交给 Mind 处理
        response = await self.mind.process(
            event,
            llm_caller=llm_caller,
            history_manager=self.history,
            memory=self.memory,
        )

        # 执行行动（能力插件调用等）
        if response and response.has_actions:
            for action in response.actions:
                if action.type == ActionType.CAPABILITY:
                    await self._execute_capability(action)
                elif action.type == ActionType.DELEGATE:
                    await self._execute_delegate(action)

        return response

    # ================= V6 decide_to_reply =================

    def _decide_to_reply(self, event: PlatformEvent) -> bool:
        """
        V6 逻辑：判断是否应该回复

        - 有人类 @Yuki → 强制回复
        - 仅 BOT @Yuki → 欲望打折，跳过回复（防无限套娃）
        - 无 @Yuki → 正常回复（由 mind 的活跃度系统决定）
        """
        content = event.metadata.get("raw_message", event.content).lower()
        keywords = (self.config.KEYWORDS if self.config else []) + ["yuki"]

        is_calling = any(kw.lower() in content for kw in keywords)
        if not is_calling:
            return True  # 没被召唤，交给 mind 正常判断

        # 被召唤了，检查是否只有 BOT 在召唤
        is_bot = event.metadata.get("is_bot", False)
        if is_bot:
            logger.info(f"[Bus] 检测到 BOT 召唤 Yuki，欲望打折跳过")
            return False

        return True

    def _make_llm_caller(self):
        """构建 LLM 调用函数"""
        if not self.config:
            return None

        cfg = self.config
        if not cfg.LLM_API_KEY:
            logger.warning("[Bus] 未配置 LLM API Key，LLM 调用不可用")
            return None

        async def caller(messages: list[dict], **kwargs) -> str:
            return await robust_chat(
                messages=messages,
                model=cfg.LLM_MODEL,
                api_key=cfg.LLM_API_KEY,
                base_url=cfg.LLM_BASE_URL or "https://api.deepseek.com",
                backup_model=cfg.BACKUP_MODEL,
                backup_api_key=cfg.BACKUP_API_KEY,
                backup_base_url=cfg.BACKUP_BASE_URL or "https://api.deepseek.com",
                disable_thinking=cfg.DISABLE_THINKING,
                **kwargs,
            )

        return caller

    async def _execute_capability(self, action: Action):
        """执行能力插件（占位）"""
        cap = self.capability_plugins.get(action.capability)
        if not cap:
            logger.info(f"[Bus] 能力插件 {action.capability} 未注册（占位）")
            return
        try:
            await cap.execute(action.params)
        except Exception as e:
            logger.error(f"[Bus] 能力执行失败: {e}")

    async def _execute_delegate(self, action: Action):
        """委托小女仆（占位）"""
        logger.info(f"[Bus] 小女仆委托: {action.content}（占位，待集成）")

    def get_capabilities_schema(self) -> list[dict]:
        return [cap.get_schema() for cap in self.capability_plugins.values()]

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "platforms": list(self.platform_plugins.keys()),
            "capabilities": list(self.capability_plugins.keys()),
            "tasks": len(self._tasks),
        }


# 需要在顶部导入
import datetime
