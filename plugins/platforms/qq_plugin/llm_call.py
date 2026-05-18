# plugins/platforms/qq_plugin/llm_call.py
"""
薄封装：V6 Provider 接口 → V7 robust_chat

所有 V6 代码中 `await provider.chat(messages=..., model=..., temperature=..., ...)`
统一替换为 `await yuki_chat(messages=..., temperature=..., ...)`
"""

import logging
from yuki_core.llm import robust_chat
from yuki_core.config import cfg

logger = logging.getLogger("llm_call")


async def yuki_chat(messages: list[dict], **kwargs) -> str:
    """
    对齐 V6 Provider.chat() 接口，内部调用 V7 robust_chat。
    
    自动从 cfg 注入 api_key / base_url / backup 等参数，
    调用方只需传 messages + 业务参数（temperature, max_tokens 等）。
    """
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


async def yuki_vision_chat(messages: list[dict], **kwargs) -> str:
    """
    视觉模型调用，使用 IMAGE_PROCESS 通道。
    """
    from yuki_core.llm import chat_completion
    return await chat_completion(
        messages=messages,
        model=cfg.VISION_MODEL,
        api_key=cfg.IMAGE_PROCESS_API_KEY,
        base_url=cfg.IMAGE_PROCESS_API_URL or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        **kwargs,
    )
