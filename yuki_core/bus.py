# yuki_core/bus.py
"""
Yuki 上下文总线

连接 Core 和 Plugins，统一事件流转
集成 LLM 调用、后台任务
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
        
        # 启动后台任务
        self._tasks.append(asyncio.create_task(self._background_energy_decay()))
        self._tasks.append(asyncio.create_task(self._background_diary_checker()))
        self._tasks.append(asyncio.create_task(self._background_ice_breaker()))
        
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
    
    # ================= 后台任务 =================
    
    async def _background_energy_decay(self):
        """精力衰减 + 活跃度衰减"""
        while self._running:
            await asyncio.sleep(300)  # 5分钟
            try:
                self.mind.activity.decay_all()
                logger.debug("[Bus] 活跃度衰减完成")
            except Exception as e:
                logger.error(f"[Bus] 精力衰减异常: {e}")
    
    async def _background_diary_checker(self):
        """
        后台任务，每30秒检查一次空闲群聊
        
        对齐 V6 idle_diary_checker 逻辑：
        - 用 last_message_time 追踪消息到达时间（而非历史记录中的时间字符串）
        - 达到 min_turns 后空闲 idle_seconds 才触发
        - 达到 max_length 强制触发（V6 main.py:201 逻辑）
        """
        if not self.config or not self.history or not self.memory:
            return
        
        cfg = self.config
        while self._running:
            await asyncio.sleep(30)
            try:
                now = time.time()
                history_data = self.history.load()
                
                for session_id, messages in list(history_data.items()):
                    if session_id in self.mind._writing_diary:
                        continue
                    
                    non_system = [m for m in messages if m.get("role") != "system"]
                    non_system_count = len(non_system)
                    
                    # === 强制触发：历史超过 max_length ===
                    if non_system_count > cfg.DIARY_MAX_LENGTH:
                        logger.info(f"[Bus] {session_id} 历史过长({non_system_count}>{cfg.DIARY_MAX_LENGTH})，强制写日记")
                        self.mind._writing_diary.add(session_id)
                        try:
                            new_history = await self._do_summarize(session_id, messages)
                            if new_history is not None:
                                history_data = self.history.load()
                                history_data[session_id] = new_history
                                self.history.save(history_data)
                        finally:
                            self.mind._writing_diary.discard(session_id)
                        continue
                    
                    # === 空闲触发：轮数不足则跳过 ===
                    if non_system_count < cfg.DIARY_MIN_TURNS:
                        continue
                    
                    # 用 last_message_time 判断空闲（对齐 V6）
                    last_msg_time = self._last_message_time.get(session_id)
                    if last_msg_time is None:
                        continue
                    
                    idle_seconds = now - last_msg_time
                    if idle_seconds < cfg.DIARY_IDLE_SECONDS:
                        continue
                    
                    # 满足条件，触发写日记
                    logger.info(f"[Bus] {session_id} 空闲 {idle_seconds:.0f}s，轮数 {non_system_count}，触发日记")
                    self.mind._writing_diary.add(session_id)
                    try:
                        new_history = await self._do_summarize(session_id, messages)
                        if new_history is not None:
                            history_data = self.history.load()
                            history_data[session_id] = new_history
                            self.history.save(history_data)
                    finally:
                        self.mind._writing_diary.discard(session_id)
            
            except Exception as e:
                logger.error(f"[Bus] 日记检查异常: {e}")
    
    async def _do_summarize(self, session_id: str, history: list):
        """
        写日记总结，返回裁剪后的 history（由调用方负责保存）
        
        对齐 V6 do_summarize：只负责生成日记 + 返回裁剪历史，不自己保存
        """
        from .identity import get_base_setting, get_summary_prompt
        import datetime as dt
        import re
        
        dialogue_msgs = [m for m in history if m.get("role") != "system"]
        content = str(dialogue_msgs)
        
        llm_caller = self._make_llm_caller()
        if not llm_caller:
            return history  # 无法生成日记，返回原历史
        
        messages = [
            {"role": "system", "content": get_base_setting()},
            {"role": "user", "content": f"以下是需要总结的对话内容：\n{content}\n\n---任务指令---\n{get_summary_prompt()}"}
        ]
        
        diary = await llm_caller(messages, max_tokens=200, temperature=0.7)
        diary = re.sub(r'\s*FINISHED\s*$', '', diary, flags=re.IGNORECASE).strip()
        diary = f"【日记({dt.datetime.now().strftime('%Y-%m-%d %H:%M')})】：\n{diary}"
        
        # 保存到记忆库
        if self.memory:
            self.memory.remember(diary, session_id=session_id, source="diary")
        
        # 裁剪历史：保留 system prompt + 最近 N 轮对话
        kept = [m for m in history if m.get("role") == "system"] + dialogue_msgs[-self.config.KEEP_LAST_DIALOGUE:]
        
        logger.info(f"[Bus] {session_id} 日记写入完成，历史 {len(history)} → {len(kept)}")
        return kept
    
    async def _background_ice_breaker(self):
        """破冰主动唤醒（占位）"""
        while self._running:
            await asyncio.sleep(600)  # 10分钟检查一次
            # TODO: Phase 5 集成破冰逻辑
            # 需要：活跃度低 + 欲望高 + 群聊沉默 → 主动开口
    
    def get_status(self) -> dict:
        return {
            "running": self._running,
            "platforms": list(self.platform_plugins.keys()),
            "capabilities": list(self.capability_plugins.keys()),
            "tasks": len(self._tasks),
        }


# 需要在顶部导入
import datetime
