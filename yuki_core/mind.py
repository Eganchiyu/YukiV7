# yuki_core/mind.py
"""
Yuki 决策引擎（精简版）

核心职责：
1. 构建上下文（system prompt + RAG记忆 + 近期对话 + 当前消息）
2. 调用 LLM 生成回复
3. 解析特殊标签（布局、DELEGATE_TO_MAID、MEME_SEARCH）

精力/欲望/活跃度/生物钟/破冰/日记 等业务逻辑全部移至 QQ 插件层
"""

import re
import datetime
import logging
from typing import Optional

from .models import (
    PlatformEvent, YukiResponse, Decision,
    Action, EventType, ActionType
)
from .identity import YukiIdentity

logger = logging.getLogger("mind")


class YukiMind:
    """
    Yuki 决策引擎（精简版）

    处理事件 → 构建上下文 → LLM生成 → 解析标签 → 返回
    不包含精力/欲望等业务逻辑，由插件层决定是否调用
    """

    def __init__(self, identity: YukiIdentity = None, keep_last_dialogue: int = 10):
        self.identity = identity or YukiIdentity()
        self._writing_diary: set = set()  # 防止并发写日记（预留）
        self._keep_last_dialogue = keep_last_dialogue

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

        # 1. 如果没有 LLM 调用器，返回占位
        if not llm_caller:
            response_text = f"[Mind] 收到来自 {event.user_name or event.user_id} 的消息: {event.content[:50]}..."
            return YukiResponse(
                text=response_text,
                actions=[Action(type=ActionType.REPLY, content=response_text)],
                metadata={"decision": "no_llm_caller"}
            )

        # 2. 构建上下文
        context = await self._build_context(event, history_manager, memory)

        # 3. 调用 LLM
        logger.info(f"[Mind] {session_id} 正在打字...")
        response_text = await llm_caller(context)

        # 4. 解析特殊标签
        actions = []
        clean_text = response_text

        # 清理 FINISHED
        clean_text = re.sub(r'\s*FINISHED\s*$', '', clean_text, flags=re.IGNORECASE).strip()

        # 提取布局（保留但不发送）
        layout_match = re.search(r'<布局>(.*?)</布局>', clean_text, flags=re.DOTALL)
        if layout_match:
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

        # 5. 记录到历史
        if history_manager:
            current_time = event.time_str
            history_manager.append_message(session_id, "user", event.content, current_time)
            history_manager.append_message(session_id, "assistant", clean_text, current_time)

        return YukiResponse(
            text=clean_text,
            actions=actions or [Action(type=ActionType.REPLY, content=clean_text)],
            metadata={
                "decision_reason": "llm_reply",
            }
        )

    async def _build_context(
        self,
        event: PlatformEvent,
        history_manager=None,
        memory=None,
    ) -> list[dict]:
        """
        构建 LLM 上下文 — 严格对齐 YukiV6 build_chat_context

        结构: system_prompt + [RAG回忆] + 近期对话 + 当前消息
        """
        session_id = event.session_id
        combined_text = event.content
        history_dict = history_manager.load() if history_manager else {}

        # 1. 基础人设 — 优先从历史记录取，保证与启动时一致
        chat_history = history_dict.get(session_id, [])
        if chat_history and chat_history[0].get("role") == "system":
            system_prompt = chat_history[0]["content"]
        else:
            system_prompt = self.identity.get_system_prompt(event.source)
        messages = [{"role": "system", "content": system_prompt}]

        # 2. RAG 检索日记 — 对齐 YukiV6 search_diaries 参数
        if memory:
            try:
                dynamic_top_k = 10 if len(combined_text) > 100 else 8
                relevant_diaries = memory.search_diaries(
                    combined_text, session_id=session_id, top_k=dynamic_top_k
                )
                for diary_obj in reversed(relevant_diaries):
                    content = diary_obj['content']
                    messages.append({"role": "system", "content": f"【回忆】{content}"})

                for i, diary_obj in enumerate(relevant_diaries, 1):
                    content_preview = diary_obj['content'].replace('\n', ' ')[:120]
                    logger.info(
                        f"[RAG] 回忆#{i} 得分:{diary_obj['score']:.2f} | {content_preview}..."
                    )
                    logger.debug(f"[RAG]   详情: {diary_obj.get('debug', '')}")
                logger.info(f"[RAG] 共检索到 {len(relevant_diaries)} 条日记")
            except Exception as e:
                logger.warning(f"[Mind] RAG 检索失败: {e}")

        # 3. 近期对话 — 对齐 YukiV6 的切片和时间处理
        if chat_history:
            keep = self._keep_last_dialogue  # 默认 10，可通过 config 配置
            recent_raw = chat_history[-keep - 1:-1] if len(chat_history) > 1 else []
            recent_raw = [m for m in recent_raw if m.get("role") != "system"]

            for msg in recent_raw:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                msg_time = msg.get("time", "")

                if msg_time:
                    if role == "user":
                        content = f"【时间：{msg_time}】{content}"

                messages.append({"role": role, "content": content})

        # 4. 当前消息 — 对齐 YukiV6 格式
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        messages.append({
            "role": "user",
            "content": f" (当前时间:{current_time}){combined_text}"
        })

        return messages

    def get_status(self, session_id: str) -> dict:
        """获取状态（调试用）"""
        return {
            "session_id": session_id,
        }
