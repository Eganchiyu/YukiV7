# yuki_core/llm.py
"""
轻量 LLM 调用器

用 requests 同步调 OpenAI 兼容 API，通过 asyncio.to_thread 包装为协程
每次请求独立，不用 Session 复用（线程安全）
"""

import asyncio
import requests
import logging

logger = logging.getLogger("llm")

# 错误前缀哨兵：所有 LLM 错误返回都以此开头，robust_chat 靠此判断是否需要切换备用
_ERROR_SENTINEL = "（"


def close_session():
    """兼容接口，requests 无全局 session 需要关闭"""
    pass


def _chat_completion_sync(
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
    """同步调用 OpenAI 兼容 API（每次独立请求，线程安全）"""
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

    if disable_thinking:
        # reasoning_effort 只对推理模型(deepseek-reasoner)有效
        # deepseek-chat 等普通模型不支持此参数，会导致空响应
        # 暂时不加 reasoning_effort，靠 prompt 控制输出
        pass

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=(10, 60))
        if resp.status_code == 200:
            data = resp.json()
            # 防御性访问：API 可能返回空 choices 或格式异常
            choices = data.get("choices", [])
            if not choices:
                logger.error(f"[LLM] API 返回空 choices, response={resp.text[:200]}")
                return f"{_ERROR_SENTINEL}API 返回空响应）"
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                # 可能是 reasoning 模型，content 在 reasoning_content 里
                reasoning = choices[0].get("message", {}).get("reasoning_content", "")
                if reasoning:
                    logger.warning("[LLM] content 为空但有 reasoning_content，使用 reasoning")
                    return reasoning.strip()
                logger.error(f"[LLM] API 返回空 content, full={resp.text[:300]}")
                return f"{_ERROR_SENTINEL}API 返回空内容）"
            return content.strip()
        else:
            err = resp.text[:200]
            logger.error(f"[LLM] API 错误 {resp.status_code}: {err}")
            return f"{_ERROR_SENTINEL}API 调用失败: HTTP {resp.status_code}）"
    except requests.exceptions.Timeout:
        logger.error("[LLM] 请求超时")
        return f"{_ERROR_SENTINEL}API 调用超时）"
    except Exception as e:
        # 记录完整错误，但不泄露给用户
        logger.error(f"[LLM] 请求异常: {e}")
        return f"{_ERROR_SENTINEL}API 调用异常）"


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
    """异步接口：在线程池中执行同步请求"""
    return await asyncio.to_thread(
        _chat_completion_sync,
        messages=messages,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
        disable_thinking=disable_thinking,
        **kwargs,
    )


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
    """带主备切换的 LLM 调用"""
    result = await chat_completion(
        messages=messages,
        model=model,
        api_key=api_key,
        base_url=base_url,
        disable_thinking=disable_thinking,
        **kwargs,
    )

    # 用哨兵前缀判断是否为错误（_chat_completion_sync 所有错误都以 "（" 开头）
    if not result.startswith(_ERROR_SENTINEL):
        return result

    logger.warning(f"[LLM] 主线路失败: {result}")

    if backup_api_key and backup_base_url:
        logger.warning("[LLM] 切换备用线路")
        result = await chat_completion(
            messages=messages,
            model=backup_model or model,
            api_key=backup_api_key,
            base_url=backup_base_url,
            disable_thinking=disable_thinking,
            **kwargs,
        )
        if not result.startswith(_ERROR_SENTINEL):
            return result

    return "（Yuki 好像有点不舒服，暂时连接不上大脑...主人等会再找我好吗？）"
