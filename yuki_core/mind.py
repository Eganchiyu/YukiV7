# yuki_core/mind.py
"""
Yuki 决策引擎

负责决策 Yuki 是否回应、如何回应
从 core/brain.py + core/engine.py 提取，移除平台耦合
"""

import datetime
import math
import asyncio
from collections import defaultdict
from typing import Optional

from .models import (
    PlatformEvent, YukiResponse, Decision, 
    Action, EventType, ActionType
)
from .identity import YukiIdentity

try:
    from utils.logger import get_logger
except ImportError:
    import logging
    def get_logger(name):
        return logging.getLogger(name)

logger = get_logger("mind")


class EnergySystem:
    """
    精力值管理
    
    跨平台共享，每个平台独立计算恢复
    """
    
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
        """获取当前精力值"""
        now = datetime.datetime.now()
        
        if session_id not in self._energy:
            self._energy[session_id] = self.initial
            self._last_update[session_id] = now
            return self._energy[session_id]
        
        # 计算恢复
        duration_mins = (now - self._last_update[session_id]).total_seconds() / 60
        self._energy[session_id] = min(
            self.max_energy,
            self._energy[session_id] + (duration_mins * self.recovery_per_min)
        )
        self._last_update[session_id] = now
        
        return self._energy[session_id]
    
    def consume(self, session_id: str) -> float:
        """消耗精力值，返回剩余值"""
        current = self.get(session_id)
        self._energy[session_id] = max(0.0, current - self.cost_per_reply)
        return self._energy[session_id]


class ActivityTracker:
    """
    活跃度追踪
    
    追踪每个会话的活跃程度，用于决策
    """
    
    def __init__(
        self,
        sensitivity: float = 0.12,
        decay_level: float = 0.65,
        decay_interval: int = 300  # 秒
    ):
        self.sensitivity = sensitivity
        self.decay_level = decay_level
        self.decay_interval = decay_interval
        
        self._activity: dict[str, float] = {}
        self._last_decay: dict[str, float] = {}
    
    def update(self, session_id: str) -> float:
        """更新活跃度，返回新值"""
        current = self._activity.get(session_id, 0.0)
        
        # 非线性增长：距离上限越近，增量越小
        increment = (10.0 - current) * self.sensitivity
        new_value = min(10.0, current + increment)
        
        self._activity[session_id] = new_value
        return new_value
    
    def get(self, session_id: str) -> float:
        """获取当前活跃度（带衰减）"""
        current = self._activity.get(session_id, 0.0)
        
        # 检查是否需要衰减
        now = datetime.datetime.now().timestamp()
        last_decay = self._last_decay.get(session_id, 0)
        
        if now - last_decay > self.decay_interval:
            # 执行衰减
            current *= self.decay_level
            self._activity[session_id] = current
            self._last_decay[session_id] = now
            
            # 清理低活跃度
            if current < 0.1:
                del self._activity[session_id]
                if session_id in self._last_decay:
                    del self._last_decay[session_id]
        
        return current
    
    def decay_all(self):
        """强制衰减所有活跃度"""
        for session_id in list(self._activity.keys()):
            self._activity[session_id] *= self.decay_level
            
            if self._activity[session_id] < 0.1:
                del self._activity[session_id]


class DesireCalculator:
    """
    社交欲望计算
    
    基于 Sigmoid 非线性映射计算回复欲望
    """
    
    def __init__(
        self,
        sigmoid_centre: float = 50.0,
        sigmoid_alpha: float = 0.08,
        reply_threshold: float = 30.0
    ):
        self.sigmoid_centre = sigmoid_centre
        self.sigmoid_alpha = sigmoid_alpha
        self.reply_threshold = reply_threshold
    
    def calculate(
        self,
        activity: float,
        energy: float,
        time_weight: float
    ) -> float:
        """
        计算回复欲望
        
        Args:
            activity: 活跃度 (0.0 ~ 10.0)
            energy: 精力值 (0.0 ~ 100.0)
            time_weight: 时间权重 (0.2 ~ 1.5)
        
        Returns:
            float: 欲望值 (0 ~ 100)
        """
        # 归一化活跃度到 0.0~1.0
        recent_activity_level = min(activity / 5.0, 1.0)
        
        # 模式 A: 跟风（群聊活跃时参与）
        follow_desire = recent_activity_level * 80 * (energy / 100)
        
        # 模式 B: 破冰（群聊冷清时主动）
        ice_break_desire = (1.0 - recent_activity_level) * 60 * max(0, (energy - 60) / 40)
        
        # 融合，取较大值
        total_desire = max(follow_desire, ice_break_desire) * time_weight
        
        # Sigmoid 非线性归一化
        normalized = 100 / (1 + math.exp(-self.sigmoid_alpha * (total_desire - self.sigmoid_centre)))
        
        return round(normalized, 2)
    
    def should_reply(self, desire: float) -> bool:
        """判断是否应该回复"""
        return desire > self.reply_threshold


class BioClock:
    """
    生物钟
    
    模拟真实作息节律，影响社交欲望
    """
    
    @staticmethod
    def get_time_weight() -> float:
        """
        获取当前时间的权重
        
        基于分段基准 + 高斯活跃峰模型
        """
        now = datetime.datetime.now()
        t = now.hour + now.minute / 60.0
        
        # 1. 基础背景 (Base Line)
        if 0 <= t < 7.8:
            if t < 1.0:
                base = 0.7 - (t / 1.0) * 0.45
            elif 1.0 <= t < 7.0:
                base = 0.25
            else:
                base = 0.25 + ((t - 7.0) / 0.8) * 0.45
        elif t >= 23.8:
            base = 0.9 - (t - 23.8) * 0.8
        else:
            base = 0.9
        
        # 2. 活跃峰值函数 (Gaussian Peaks)
        def peak(time, mu, sig, amp):
            return amp * math.exp(-((time - mu) ** 2) / (2 * sig ** 2))
        
        morning = peak(t, 8.0, 0.6, 0.5)    # 晨间苏醒
        lunch = peak(t, 12.8, 0.8, 0.4)     # 午后高峰
        evening = peak(t, 20.0, 1.5, 0.4)   # 晚间活跃
        
        # 3. 融合并限幅
        weight = base + morning + lunch + evening
        return max(0.2, min(weight, 1.5))


class YukiMind:
    """
    Yuki 决策引擎
    
    负责决定 Yuki 是否回应、如何回应
    """
    
    def __init__(self, identity: YukiIdentity = None):
        self.identity = identity or YukiIdentity()
        
        # 子系统
        self.energy = EnergySystem()
        self.activity = ActivityTracker()
        self.desire = DesireCalculator()
        self.bioclock = BioClock()
        
        # 状态
        self._writing_diary: set = set()  # 正在写日记的会话ID
    
    async def process(self, event: PlatformEvent) -> YukiResponse:
        """
        处理一个事件，决定 Yuki 的反应
        
        Args:
            event: 平台事件
            
        Returns:
            YukiResponse: Yuki 的回复
        """
        session_id = event.session_id
        
        # 1. 更新活跃度
        self.activity.update(session_id)
        
        # 2. 获取当前状态
        energy = self.energy.get(session_id)
        activity = self.activity.get(session_id)
        time_weight = self.bioclock.get_time_weight()
        
        # 3. 决定是否回应
        decision = await self._decide(event, energy, activity, time_weight)
        
        if decision.action == "ignore":
            return YukiResponse(text="", actions=[Action(type=ActionType.IGNORE)])
        
        # 4. 构建回复（这里只是基础版本，实际由 LLM 生成）
        # TODO: 在 Phase 2 中集成 LLM 调用
        response_text = f"[Mind] 收到来自 {event.user_name or event.user_id} 的消息: {event.content[:50]}..."
        
        # 5. 消耗精力
        self.energy.consume(session_id)
        
        return YukiResponse(
            text=response_text,
            actions=[Action(type=ActionType.REPLY, content=response_text)],
            metadata={
                "energy": self.energy.get(session_id),
                "desire": decision.confidence,
                "decision_reason": decision.reason
            }
        )
    
    async def _decide(
        self,
        event: PlatformEvent,
        energy: float,
        activity: float,
        time_weight: float
    ) -> Decision:
        """
        决定是否回应
        """
        session_id = event.session_id
        
        # 私聊默认回复
        if event.session_type == "private":
            return Decision(
                action="reply",
                confidence=1.0,
                reason="私聊默认回复"
            )
        
        # 群聊：计算欲望
        desire = self.desire.calculate(activity, energy, time_weight)
        
        # 特殊情况：被@必须回复
        if event.metadata.get("at_yuki"):
            return Decision(
                action="reply",
                confidence=1.0,
                reason="被@必须回复"
            )
        
        # 特殊情况：帮助指令
        if event.content in ["help", "帮助", "yuki帮助", "yuki功能"]:
            return Decision(
                action="reply",
                confidence=1.0,
                reason="帮助指令"
            )
        
        # 正常决策
        if self.desire.should_reply(desire):
            return Decision(
                action="reply",
                confidence=desire / 100,
                reason=f"欲望值 {desire:.1f}% 超过阈值"
            )
        else:
            return Decision(
                action="ignore",
                confidence=1 - (desire / 100),
                reason=f"欲望值 {desire:.1f}% 低于阈值，继续潜水"
            )
    
    def get_status(self, session_id: str) -> dict:
        """获取当前状态（调试用）"""
        return {
            "session_id": session_id,
            "energy": self.energy.get(session_id),
            "activity": self.activity.get(session_id),
            "time_weight": self.bioclock.get_time_weight(),
        }
