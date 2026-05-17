# yuki_core/bus.py
"""
Yuki 上下文总线

连接 Core 和 Plugins，统一事件流转
集成 LLM 调用、后台任务
"""

import asyncio
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
        
        await close_session()
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
        """空闲时自动写日记"""
        if not self.config or not self.history or not self.memory:
            return
        
        cfg = self.config
        while self._running:
            await asyncio.sleep(30)
            try:
                now = __import__("time").time()
                history_data = self.history.load()
                
                for session_id, messages in list(history_data.items()):
                    if session_id in self.mind._writing_diary:
                        continue
                    
                    non_system = [m for m in messages if m.get("role") != "system"]
                    if len(non_system) < cfg.DIARY_MIN_TURNS:
                        continue
                    
                    # 简单判断：最后一条消息的时间
                    last_msg = non_system[-1]
                    last_time = last_msg.get("time", "")
                    if last_time:
                        try:
                            # 解析 "2026年05月17日14:30" 格式
                            dt = datetime.datetime.strptime(last_time, "%Y年%m月%d日%H:%M")
                            idle_secs = (datetime.datetime.now() - dt).total_seconds()
                            if idle_secs < cfg.DIARY_IDLE_SECONDS:
                                continue
                        except Exception:
                            continue
                    
                    # 触发写日记
                    logger.info(f"[Bus] {session_id} 空闲超时，触发日记")
                    self.mind._writing_diary.add(session_id)
                    try:
                        await self._do_summarize(session_id, messages)
                    finally:
                        self.mind._writing_diary.discard(session_id)
            
            except Exception as e:
                logger.error(f"[Bus] 日记检查异常: {e}")
    
    async def _do_summarize(self, session_id: str, history: list):
        """写日记总结"""
        from .identity import get_base_setting, get_summary_prompt
        import datetime as dt
        import re
        
        dialogue_msgs = [m for m in history if m.get("role") != "system"]
        content = str(dialogue_msgs)
        
        llm_caller = self._make_llm_caller()
        if not llm_caller:
            return
        
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
        
        # 裁剪历史
        kept = [m for m in history if m.get("role") == "system"] + dialogue_msgs[-cfg.KEEP_LAST_DIALOGUE:]
        history_data = self.history.load()
        history_data[session_id] = kept
        self.history.save(history_data)
        
        logger.info(f"[Bus] {session_id} 日记写入完成")
    
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
