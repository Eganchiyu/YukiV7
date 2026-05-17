# yuki_core/llm.py
"""
轻量 LLM 调用器

直接用 aiohttp 调 OpenAI 兼容 API，不搞 Provider 注册中心那套
"""

import aiohttp
import logging
from typing import Optional

logger = logging.getLogger("llm")

# 全局 session，复用 TCP 连接
_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60, connect=10)
        )
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def chat_completion(
    messages: list[dict],
    model: str = "deepseek-chat",
    api_key: str = "",
    base_url: str = "https://api.deepseek.com",
    temperature: float = 0.7,
    max_tokens: int = 200,
    top_p: float = 0.75,
    frequency_penalty: float = 0.05,
    presence_penalty: float = 0.0,
    disable_thinking: bool = True,
    **kwargs,
) -> str:
    """
    调用 OpenAI 兼容 API
    
    Returns:
        str: 模型回复文本
    """
    session = await _get_session()
    
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
        **kwargs,
    }
    
    # 关闭 thinking
    if disable_thinking:
        payload["reasoning_effort"] = "low"
    
    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip()
            else:
                err = await resp.text()
                logger.error(f"[LLM] API 错误 {resp.status}: {err[:200]}")
                return f"（API 调用失败: HTTP {resp.status}）"
    except asyncio.TimeoutError:
        logger.error("[LLM] 请求超时")
        return "（API 调用超时）"
    except Exception as e:
        logger.error(f"[LLM] 请求异常: {e}")
        return f"（API 调用异常: {e}）"


# 便捷封装：带主备切换的 chat
async def robust_chat(
    messages: list[dict],
    model: str = "",
    api_key: str = "",
    base_url: str = "",
    backup_model: str = "",
    backup_api_key: str = "",
    backup_base_url: str = "",
    disable_thinking: bool = True,
    **kwargs,
) -> str:
    """
    带主备切换的 LLM 调用
    主线路失败自动切备用
    """
    # 主线路
    result = await chat_completion(
        messages=messages,
        model=model,
        api_key=api_key,
        base_url=base_url,
        disable_thinking=disable_thinking,
        **kwargs,
    )
    
    # 如果主线路成功且不是错误消息
    if not result.startswith("（"):
        return result
    
    # 备用线路
    if backup_api_key and backup_base_url:
        logger.warning("[LLM] 主线路失败，切换备用")
        result = await chat_completion(
            messages=messages,
            model=backup_model or model,
            api_key=backup_api_key,
            base_url=backup_base_url,
            disable_thinking=disable_thinking,
            **kwargs,
        )
        if not result.startswith("（"):
            return result
    
    # 都失败了
    return f"（Yuki 好像有点不舒服，暂时连接不上大脑...主人等会再找我好吗？）"
