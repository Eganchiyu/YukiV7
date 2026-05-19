# plugins/capabilities/maid_plugin/__init__.py
"""
小女仆 CapabilityPlugin — 跨平台自主编程代理

Mind 输出 [DELEGATE_TO_MAID:task] 后，
Bus 通过 CapabilityPlugin 接口调用此插件执行任务。
"""

import logging
from yuki_core.plugin import CapabilityPlugin
from .core import maid_evolution_loop

logger = logging.getLogger("maid")


class MaidPlugin(CapabilityPlugin):
    """
    小女仆自主编程代理
    
    通过 write/read/run/install 技能完成任意编程任务。
    兼容 Bus 的 CapabilityPlugin 接口，跨平台可用。
    """

    name = "maid"
    display_name = "小女仆"
    description = "自主编程代理，能编写、执行和调试 Python 技能来完成任务"
    version = "1.0.0"

    parameters_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "任务描述，小女仆会自主编写代码来完成",
            },
            "session_id": {
                "type": "string",
                "description": "可选的会话 ID",
            },
        },
        "required": ["task"],
    }

    return_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["finished", "timeout"]},
            "result": {"type": "string"},
            "goal": {"type": "string"},
        },
    }

    def __init__(self, **config):
        self.config = config  # CapabilityPlugin 没有自定义 __init__，手动存
        self.max_rounds = config.get("max_rounds", 20)
        self.timeout = config.get("timeout", 60)
        logger.info(f"[MaidPlugin] 小女仆能力已初始化 (max_rounds={self.max_rounds})")

    async def execute(self, params: dict) -> dict:
        """
        执行小女仆任务
        
        Args:
            params: {"task": "任务描述", "session_id": "可选会话ID"}
        
        Returns:
            {"status": "finished"|"timeout", "result": str, "goal": str}
        """
        task = params.get("task", "")
        session_id = params.get("session_id")

        if not task:
            return {"status": "error", "result": "未提供任务描述"}

        logger.info(f"[MaidPlugin] 接到任务: {task}")
        result = await maid_evolution_loop(user_goal=task, chat_id=session_id)
        logger.info(f"[MaidPlugin] 任务完成: {result.get('status')}")

        return result
