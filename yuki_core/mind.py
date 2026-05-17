# yuki_core/mind.py
"""
Yuki 决策引擎

核心职责：
1. 决定是否回应（精力+欲望+活跃度+生物钟）
2. 构建上下文（system prompt + RAG记忆 + 近期对话 + 当前消息）
3. 调用 LLM 生成回复
4. 解析特殊标签（布局、DELEGATE_TO_MAID、MEME_SEARCH）
"""

import re
import datetime
import math
import asyncio
import logging
from collections import defaultdict
from typing import Optional

from .models import (
    PlatformEvent, YukiResponse, Decision,
    Action, EventType, ActionType
)
from .identity import YukiIdentity

logger = logging.getLogger("mind")


class EnergySystem:
    """精力值管理"""
    
    def __init__(
        self,
        initial: float = 100.0,
        max_energy: float = 100.0,
        recovery_per_min: float = 0.8,
        cost_per_reply: float = 6.0
    ):
        self.initial = initial
        self.max_energy = max_energy
        self.recovery_per_min = recovery_per_min
        self.cost_per_reply = cost_per_reply
        self._energy: dict[str, float] = {}
        self._last_update: dict[str, datetime.datetime] = {}
    
    def get(self, session_id: str) -> float:
        now = datetime.datetime.now()
        if session_id not in self._energy:
            self._energy[session_id] = self.initial
            self._last_update[session_id] = now
            return self._energy[session_id]
        duration_mins = (now - self._last_update[session_id]).total_seconds() / 60
        self._energy[session_id] = min(
            self.max_energy,
            self._energy[session_id] + (duration_mins * self.recovery_per_min)
        )
        self._last_update[session_id] = now
        return self._energy[session_id]
    
    def consume(self, session_id: str) -> float:
        current = self.get(session_id)
        self._energy[session_id] = max(0.0, current - self.cost_per_reply)
        return self._energy[session_id]


class ActivityTracker:
    """活跃度追踪"""
    
    def __init__(self, sensitivity: float = 0.12, decay_level: float = 0.65):
        self.sensitivity = sensitivity
        self.decay_level = decay_level
        self._activity: dict[str, float] = {}
        self._last_decay: dict[str, float] = {}
    
    def update(self, session_id: str) -> float:
        current = self._activity.get(session_id, 0.0)
        increment = (10.0 - current) * self.sensitivity
        new_value = min(10.0, current + increment)
        self._activity[session_id] = new_value
        return new_value
    
    def get(self, session_id: str) -> float:
        current = self._activity.get(session_id, 0.0)
        now = datetime.datetime.now().timestamp()
        last_decay = self._last_decay.get(session_id, 0)
        if now - last_decay > 300:  # 5分钟衰减
            current *= self.decay_level
            self._activity[session_id] = current
            self._last_decay[session_id] = now
            if current < 0.1:
                del self._activity[session_id]
                self._last_decay.pop(session_id, None)
        return current
    
    def decay_all(self):
        for sid in list(self._activity.keys()):
            self._activity[sid] *= self.decay_level
            if self._activity[sid] < 0.1:
                del self._activity[sid]


class DesireCalculator:
    """社交欲望计算 (Sigmoid)"""
    
    def __init__(self, centre: float = 50.0, alpha: float = 0.08, threshold: float = 30.0):
        self.centre = centre
        self.alpha = alpha
        self.threshold = threshold
    
    def calculate(self, activity: float, energy: float, time_weight: float) -> float:
        recent_level = min(activity / 5.0, 1.0)
        follow = recent_level * 80 * (energy / 100)
        ice_break = (1.0 - recent_level) * 60 * max(0, (energy - 60) / 40)
        total = max(follow, ice_break) * time_weight
        normalized = 100 / (1 + math.exp(-self.alpha * (total - self.centre)))
        return round(normalized, 2)
    
    def should_reply(self, desire: float) -> bool:
        return desire > self.threshold


class BioClock:
    """生物钟"""
    
    @staticmethod
    def get_time_weight() -> float:
        now = datetime.datetime.now()
        t = now.hour + now.minute / 60.0
        
        if 0 <= t < 7.8:
            if t < 1.0:
                base = 0.7 - (t / 1.0) * 0.45
            elif t < 7.0:
                base = 0.25
            else:
                base = 0.25 + ((t - 7.0) / 0.8) * 0.45
        elif t >= 23.8:
            base = 0.9 - (t - 23.8) * 0.8
        else:
            base = 0.9
        
        def peak(time, mu, sig, amp):
            return amp * math.exp(-((time - mu) ** 2) / (2 * sig ** 2))
        
        weight = base + peak(t, 8.0, 0.6, 0.5) + peak(t, 12.8, 0.8, 0.4) + peak(t, 20.0, 1.5, 0.4)
        return max(0.2, min(weight, 1.5))


class YukiMind:
    """
    Yuki 决策引擎
    
    处理事件 → 决策 → 构建上下文 → LLM生成 → 解析标签 → 返回
    """
    
    def __init__(self, identity: YukiIdentity = None):
        self.identity = identity or YukiIdentity()
        self.energy = EnergySystem()
        self.activity = ActivityTracker()
        self.desire = DesireCalculator()
        self.bioclock = BioClock()
        self._writing_diary: set = set()
    
    async def process(
        self,
        event: PlatformEvent,
        llm_caller=None,
        history_manager=None,
        memory=None,
    ) -> YukiResponse:
        """
        处理一个事件
        
        Args:
            event: 平台事件
            llm_caller: LLM 调用函数 async fn(messages, **kwargs) -> str
            history_manager: HistoryManager 实例
            memory: YukiMemory 实例（RAG）
        """
        session_id = event.session_id
        
        # 1. 更新活跃度
        self.activity.update(session_id)
        
        # 2. 状态
        energy = self.energy.get(session_id)
        activity = self.activity.get(session_id)
        time_weight = self.bioclock.get_time_weight()
        
        # 3. 决策
        decision = await self._decide(event, energy, activity, time_weight)
        
        if decision.action == "ignore":
            # 仍然记录到历史，但不回复
            if history_manager:
                current_time = event.time_str
                history_manager.append_message(session_id, "user", event.content, current_time)
            return YukiResponse(
                text="",
                actions=[Action(type=ActionType.IGNORE)],
                metadata={"decision": decision.reason, "energy": energy}
            )
        
        # 4. 如果没有 LLM 调用器，返回占位
        if not llm_caller:
            response_text = f"[Mind] 收到来自 {event.user_name or event.user_id} 的消息: {event.content[:50]}..."
            self.energy.consume(session_id)
            return YukiResponse(
                text=response_text,
                actions=[Action(type=ActionType.REPLY, content=response_text)],
                metadata={"energy": self.energy.get(session_id), "desire": decision.confidence}
            )
        
        # 5. 构建上下文
        context = await self._build_context(event, history_manager, memory)
        
        # 6. 调用 LLM
        logger.info(f"[Mind] {session_id} 正在打字...")
        response_text = await llm_caller(context)
        
        # 7. 解析特殊标签
        actions = []
        clean_text = response_text
        
        # 清理 FINISHED
        clean_text = re.sub(r'\s*FINISHED\s*$', '', clean_text, flags=re.IGNORECASE).strip()
        
        # 提取布局（保留但不发送）
        layout_match = re.search(r'<布局>(.*?)</布局>', clean_text, flags=re.DOTALL)
        if layout_match:
            # 布局内容记录到历史但不发送
            clean_text = re.sub(r'<布局>.*?</布局>', '', clean_text, flags=re.DOTALL).strip()
        
        # 提取小女仆委托
        maid_match = re.search(r'\[DELEGATE_TO_MAID:(.*?)\]', clean_text, re.DOTALL)
        if maid_match:
            task_desc = maid_match.group(1).strip()
            clean_text = re.sub(r'\[DELEGATE_TO_MAID:.*?\]', '', clean_text, flags=re.DOTALL).strip()
            actions.append(Action(
                type=ActionType.DELEGATE,
                content=task_desc,
                metadata={"task": task_desc, "session_id": session_id}
            ))
            logger.info(f"[Mind] 委托小女仆: {task_desc}")
        
        # 提取表情包搜索
        meme_match = re.search(r'\[MEME_SEARCH:(.*?)\]', clean_text, re.DOTALL)
        if meme_match:
            query = meme_match.group(1).strip()
            clean_text = re.sub(r'\[MEME_SEARCH:.*?\]', '', clean_text, flags=re.DOTALL).strip()
            actions.append(Action(
                type=ActionType.CAPABILITY,
                capability="sticker_search",
                params={"query": query, "session_id": session_id}
            ))
            logger.info(f"[Mind] 表情包搜索: {query}")
        
        # 合并换行
        clean_text = re.sub(r'\n+', ' ', clean_text).strip()
        
        if not clean_text and not actions:
            clean_text = "..."  # 防止空回复
        
        # 8. 记录到历史
        if history_manager:
            current_time = event.time_str
            history_manager.append_message(session_id, "user", event.content, current_time)
            history_manager.append_message(session_id, "assistant", clean_text, current_time)
        
        # 9. 消耗精力
        self.energy.consume(session_id)
        
        return YukiResponse(
            text=clean_text,
            actions=actions or [Action(type=ActionType.REPLY, content=clean_text)],
            metadata={
                "energy": self.energy.get(session_id),
                "desire": decision.confidence,
                "decision_reason": decision.reason,
            }
        )
    
    async def _decide(
        self,
        event: PlatformEvent,
        energy: float,
        activity: float,
        time_weight: float
    ) -> Decision:
        """决策：是否应该回复"""
        
        # 私聊默认回复
        if event.session_type == "private":
            return Decision(action="reply", confidence=1.0, reason="私聊默认回复")
        
        # 被 @ 必须回复
        if event.metadata.get("at_yuki"):
            return Decision(action="reply", confidence=1.0, reason="被@必须回复")
        
        # 帮助指令
        if event.content.strip() in ["help", "/help", "帮助", "yuki帮助", "yuki功能"]:
            return Decision(action="reply", confidence=1.0, reason="帮助指令")
        
        # 精力不足
        if energy < 25:
            return Decision(action="ignore", confidence=0.8, reason=f"精力不足({energy:.1f})")
        
        # 计算欲望
        desire = self.desire.calculate(activity, energy, time_weight)
        
        if desire >= 80:
            return Decision(action="reply", confidence=desire/100, reason=f"欲望爆表({desire:.1f}%)")
        if desire <= 30:
            return Decision(action="ignore", confidence=1-desire/100, reason=f"欲望低迷({desire:.1f}%)")
        
        if self.desire.should_reply(desire):
            return Decision(action="reply", confidence=desire/100, reason=f"欲望值{desire:.1f}%超过阈值")
        
        return Decision(action="ignore", confidence=1-desire/100, reason=f"欲望值{desire:.1f}%低于阈值")
    
    async def _build_context(
        self,
        event: PlatformEvent,
        history_manager=None,
        memory=None,
    ) -> list[dict]:
        """
        构建 LLM 上下文
        
        结构: system_prompt + [RAG回忆] + 近期对话 + 当前消息
        """
        session_id = event.session_id
        messages = []
        
        # 1. System Prompt
        platform = event.source
        system_prompt = self.identity.get_system_prompt(platform)
        messages.append({"role": "system", "content": system_prompt})
        
        # 2. RAG 记忆
        if memory:
            try:
                diaries = memory.recall(event.content, session_id=session_id, limit=8)
                for entry in reversed(diaries):
                    content = entry.content.replace('\n', ' ')
                    messages.append({"role": "system", "content": f"【回忆】{content}"})
            except Exception as e:
                logger.warning(f"[Mind] RAG 检索失败: {e}")
        
        # 3. 近期对话（从历史记录）
        if history_manager:
            history = history_manager.load()
            chat_history = history.get(session_id, [])
            
            # 取 system prompt 之后的消息
            non_system = [m for m in chat_history if m.get("role") != "system"]
            
            # 保留最近 N 条
            keep = 10
            recent = non_system[-keep:] if len(non_system) > keep else non_system
            
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                time_str = msg.get("time", "")
                
                if role == "user" and time_str:
                    content = f"【时间：{time_str}】{content}"
                
                messages.append({"role": role, "content": content})
        
        # 4. 当前消息
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        messages.append({
            "role": "user",
            "content": f"(当前时间:{current_time}){event.content}"
        })
        
        return messages
    
    def get_status(self, session_id: str) -> dict:
        """获取状态（调试用）"""
        return {
            "session_id": session_id,
            "energy": self.energy.get(session_id),
            "activity": self.activity.get(session_id),
            "time_weight": self.bioclock.get_time_weight(),
        }
