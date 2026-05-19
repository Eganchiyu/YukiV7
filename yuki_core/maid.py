# yuki_core/maid.py
"""
Yuki 小女仆系统

V7 Core 层入口 — 实际业务逻辑在 plugins/capabilities/maid_plugin/core.py。
此文件提供 MaidAgent 类供 main.py 导入，内部委托给 maid_plugin。
"""

import logging

logger = logging.getLogger("maid")


class MaidAgent:
    """
    小女仆 Agent（V7 Core 层）

    委托给 plugins.capabilities.maid_plugin.MaidPlugin 执行。
    """

    def __init__(self):
        logger.info("[Maid] MaidAgent 已初始化")

    async def delegate(self, task_desc: str, session_id: str = None) -> str:
        """
        委托任务给小女仆
        """
        try:
            from plugins.capabilities.maid_plugin.core import maid_evolution_loop
            result = await maid_evolution_loop(user_goal=task_desc, chat_id=session_id)
            return result.get("result", "任务完成")
        except Exception as e:
            logger.error(f"[Maid] 委托失败: {e}")
            return f"小女仆任务失败: {e}"
