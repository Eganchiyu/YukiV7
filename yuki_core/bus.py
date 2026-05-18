# yuki_core/bus.py
"""
Yuki 上下文总线

连接 Core 和 Plugins，统一事件流转

职责:
  - 插件注册与生命周期管理
  - 事件路由 (Plugin.receive → Bus.receive → Mind.process → Plugin.send)
  - 自动重连与错误隔离
  - Action 执行调度
  - 后台任务管理

后台任务（日记/破冰/精力衰减）已移至 QQ 插件层
"""

import asyncio
import time
import logging
from typing import Optional, TYPE_CHECKING

from .models import PlatformEvent, YukiResponse, Action, ActionType
from .llm import robust_chat, close_session

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

    连接 Core (Mind, Identity) 和 Plugins，统一事件流转。

    事件流:
      Plugin.receive() → Bus.receive(event) → Mind.process() → Plugin.send()

    错误处理:
      - 单条事件异常不影响整个监听循环
      - 连接断开自动重连 (指数退避, 上限60s)
      - 后台任务异常被捕获并记录
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

        # LLM 调用器（启动时创建，避免每次新建闭包）
        self._llm_caller = None

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

        # 启动时创建 LLM 调用器（缓存，不再每次新建）
        self._llm_caller = self._make_llm_caller()

        # 启动平台监听
        for pid, plugin in self.platform_plugins.items():
            task = asyncio.create_task(
                self._listen_platform(plugin),
                name=f"bus-{pid}"
            )
            self._tasks.append(task)

        # 启动各插件的后台任务（由插件自己注册），包装错误捕获
        for pid, plugin in self.platform_plugins.items():
            if hasattr(plugin, 'get_background_tasks'):
                for task_coro in plugin.get_background_tasks():
                    task = asyncio.create_task(
                        self._wrap_bg_task(plugin, task_coro),
                        name=f"bg-{pid}-{task_coro.__name__}"
                    )
                    self._tasks.append(task)

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self, timeout: float = 5.0):
        self._running = False
        logger.info("[Bus] 正在停止...")

        # 取消所有任务
        for task in self._tasks:
            task.cancel()

        # 等待完成，带超时
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks, timeout=timeout)
            if pending:
                logger.warning(f"[Bus] {len(pending)} 个任务超时未完成，强制取消")
                for task in pending:
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()

        # 断开插件
        for plugin in self.platform_plugins.values():
            try:
                await plugin.disconnect()
            except Exception as e:
                logger.error(f"[Bus] 断开 {plugin.name} 失败: {e}")

        close_session()
        logger.info("[Bus] 已停止")

    # ================= 事件处理 =================

    async def _listen_platform(self, plugin: 'PlatformPlugin'):
        """
        单平台监听循环，含自动重连。

        外层: 连接 → 监听 → 断开 → 重连 (指数退避)
        内层: 逐事件 try/except，单条异常不影响下一条
        """
        retry_count = 0
        max_retries = getattr(plugin, 'max_reconnect_retries', 5)

        while self._running:
            try:
                connected = await plugin.connect()
                if not connected:
                    raise ConnectionError(f"{plugin.name} connect() returned False")

                await plugin.on_connect()
                retry_count = 0  # 连接成功，重置计数
                logger.info(f"[Bus] {plugin.name} 已连接")

                async for event in plugin.receive():
                    if not self._running:
                        break

                    # 逐事件 try/except：一条坏了不影响下一条
                    try:
                        response = await self.receive(event)
                        if response and response.text:
                            await plugin.send(event, response)
                    except Exception as e:
                        logger.error(
                            f"[Bus] {plugin.name} 事件处理异常: {e}",
                            exc_info=True
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(
                        f"[Bus] {plugin.name} 超过最大重连次数({max_retries})，放弃"
                    )
                    break
                wait = min(2 ** retry_count, 60)
                logger.warning(
                    f"[Bus] {plugin.name} 连接断开({e})，{wait}s 后重连 (#{retry_count})"
                )
                await asyncio.sleep(wait)

        # 清理
        try:
            await plugin.on_disconnect()
        except Exception:
            pass
        try:
            await plugin.disconnect()
        except Exception:
            pass

    async def receive(self, event: PlatformEvent) -> Optional[YukiResponse]:
        """
        核心入口：接收事件 → Mind处理 → 返回响应

        这是所有平台消息的统一入口。

        流程:
          1. 校验事件
          2. 追踪消息时间
          3. 防 bot 自环
          4. 注入身份上下文
          5. Mind 处理
          6. 执行 actions
          7. 记录日志 + 计时
        """
        t0 = time.time()

        # 1. 校验事件
        if not self._validate_event(event):
            return None

        # 2. 追踪消息到达时间（用于日记空闲判断）
        self._last_message_time[event.session_id] = t0
        logger.debug(
            f"[Bus] ← {event.source}/{event.session_id} "
            f"{event.user_name}: {event.content[:60]}"
        )

        # 3. V6 decide_to_reply 逻辑（防 bot 无限套娃）
        if not self._decide_to_reply(event):
            logger.debug(f"[Bus] 跳过 (bot 防环)")
            return None

        # 4. 注入身份上下文
        platform_identity = self.identity.get_platform_identity(event.source)
        event.identity_context = {
            "platform_prompt": platform_identity.context_prompt,
            "capabilities": platform_identity.capabilities,
            "style_override": platform_identity.style_override,
        }

        # 5. 交给 Mind 处理
        response = await self.mind.process(
            event,
            llm_caller=self._llm_caller,
            history_manager=self.history,
            memory=self.memory,
        )

        # 6. 执行行动（能力插件调用等）
        if response and response.has_actions:
            await self._execute_actions(event, response)

        # 7. 日志 + 计时
        elapsed = time.time() - t0
        if response:
            logger.info(
                f"[Bus] → {event.session_id} ({elapsed:.1f}s) "
                f"{response.text[:60]}"
            )
        else:
            logger.debug(f"[Bus] → {event.session_id} 无回复 ({elapsed:.1f}s)")

        return response

    # ================= 事件校验 =================

    def _validate_event(self, event: PlatformEvent) -> bool:
        """校验事件必填字段，缺字段尝试自动补全"""
        if not event.source:
            logger.warning("[Bus] 事件缺少 source，跳过")
            return False
        if not event.user_id:
            logger.warning("[Bus] 事件缺少 user_id，跳过")
            return False
        if not event.session_id:
            # 自动填充：群聊从 metadata 取 group_id，否则用 user_id
            event.session_id = event.metadata.get("group_id") or event.user_id
        return True

    # ================= V6 decide_to_reply =================

    def _decide_to_reply(self, event: PlatformEvent) -> bool:
        """
        V6 逻辑：判断是否应该回复

        - 有人类 @Yuki → 强制回复
        - 仅 BOT @Yuki → 欲望打折，跳过回复（防无限套娃）
        - 无 @Yuki → 正常回复（由 mind 的活跃度系统决定）

        多重检测:
          1. metadata.is_bot 字段（由 plugin 设置）
          2. sender.role == "bot"（NapCat 标准字段）
          3. 发送者名字匹配已知 bot 名
        """
        content = (event.metadata.get("raw_message") or event.content or "").lower()
        keywords = (self.config.KEYWORDS if self.config else []) + ["yuki"]

        is_calling = any(kw.lower() in content for kw in keywords)
        if not is_calling:
            return True  # 没被召唤，交给 mind 正常判断

        # 被召唤了，检查是否只有 BOT 在召唤
        raw = event.metadata.get("raw", {})
        sender = raw.get("sender", {})

        # 多重 bot 检测
        is_bot = (
            event.metadata.get("is_bot", False)          # plugin 显式标记
            or sender.get("role") == "bot"               # NapCat bot 角色
            or sender.get("bot_id") is not None          # NapCat bot_id 字段
        )

        if is_bot:
            logger.info(f"[Bus] 检测到 BOT 召唤 Yuki，欲望打折跳过")
            return False

        return True

    # ================= LLM 调用器 =================

    def _make_llm_caller(self):
        """构建 LLM 调用函数（启动时创建一次，缓存复用）"""
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

    # ================= Action 执行 =================

    async def _execute_actions(self, event: PlatformEvent, response: YukiResponse):
        """执行回复中的所有 action"""
        for action in response.actions:
            try:
                if action.type == ActionType.CAPABILITY:
                    await self._execute_capability(action)
                elif action.type == ActionType.DELEGATE:
                    await self._execute_delegate(action)
                # REPLY / IGNORE 不需要额外执行
            except Exception as e:
                logger.error(f"[Bus] action 执行失败 ({action.type}): {e}")

    async def _execute_capability(self, action: Action):
        """执行能力插件"""
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

    # ================= 后台任务 =================

    async def _wrap_bg_task(self, plugin: 'PlatformPlugin', coro):
        """包装后台任务，捕获异常防止静默挂掉"""
        task_name = getattr(coro, '__name__', str(coro))
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                f"[Bus] {plugin.platform_id} 后台任务 {task_name} 异常: {e}",
                exc_info=True
            )

    # ================= 查询 =================

    def get_capabilities_schema(self) -> list[dict]:
        return [cap.get_schema() for cap in self.capability_plugins.values()]

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "platforms": {
                pid: {
                    "name": p.name,
                    "version": p.version,
                }
                for pid, p in self.platform_plugins.items()
            },
            "capabilities": list(self.capability_plugins.keys()),
            "active_tasks": len([t for t in self._tasks if not t.done()]),
            "total_tasks": len(self._tasks),
            "last_message_times": dict(self._last_message_time),
        }
