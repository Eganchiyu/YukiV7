# yuki_core/maid.py
"""
Yuki 小女仆系统

V7 占位层 — 实际业务逻辑在 plugins/platforms/qq_plugin/core/maid.py
此文件提供 MaidAgent 类供 main.py 导入，内部委托给 V6 实现。
"""

import logging

logger = logging.getLogger("maid")


class MaidAgent:
    """
    小女仆 Agent（V7 占位）

    实际的 maid_evolution_loop 在 plugins/platforms/qq_plugin/core/maid.py。
    此类仅用于 main.py 的初始化打印和未来 V7 Core 层集成。
    """

    def __init__(self, llm_caller=None):
        self.llm_caller = llm_caller
        logger.info("[Maid] MaidAgent 已初始化（V7 占位层）")

    async def delegate(self, task_desc: str, session_id: str = None) -> str:
        """
        委托任务给小女仆

        当前直接调用 V6 实现，未来可替换为纯 V7 流程。
        """
        try:
            from plugins.platforms.qq_plugin.core.maid import maid_evolution_loop
            result = await maid_evolution_loop(user_goal=task_desc, chat_id=session_id)
            return result.get("result", "任务完成")
        except Exception as e:
            logger.error(f"[Maid] 委托失败: {e}")
            return f"小女仆任务失败: {e}"
