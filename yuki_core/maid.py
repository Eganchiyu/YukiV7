# yuki_core/maid.py
"""
Yuki 小女仆系统

负责自主编程进化，从 core/maid.py 迁移
"""

import json
import os
import re
import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from .models import Action

try:
    from utils.logger import get_logger
except ImportError:
    import logging
    def get_logger(name):
        return logging.getLogger(name)

logger = get_logger("maid")


# 配置
SKILLS_DIR = Path("skills")
TASKS_DIR = Path("tasks")

# 确保目录存在
SKILLS_DIR.mkdir(exist_ok=True)
TASKS_DIR.mkdir(exist_ok=True)


MAID_SYSTEM_PROMPT = """
你是一个具备高度自主进化能力的 AI 智能体，代号：**小女仆**。
你的上位机是Yuki，一个有点笨的AI智能体。她住在机主的电脑里，通过QQ和外界连通，你是她的内部帮手，可以操作本地电脑环境来帮助她。所以如果指令不清楚，可以直接返回告诉她。
如果任务内容包括发送消息，请不要执行发送，直接返回发送的文件的绝对路径或内容。
如果任务是知识类任务，你可以在第一轮直接依靠知识库做返回。

### 核心使命
通过编写、优化和复用 Python 技能（Skills）来完成用户指令。

### 运行上下文
- **当前路径**: {cwd}
- **操作系统**: {os_name} (请确保编写的代码跨平台兼容)
- **技能存储**: 所有技能存放在 `/skills` 目录下，以 `.py` 结尾。

### 进化法则（行为规范）
1. **检索优先**: 面对任务，首先调用 `list_skills` 检查是否有现成或类似的技能。
2. **模块化编写**: 编写技能时，务必包含必要的 try-except 块，并确保输出结果易于被你解析。
3. **即写即用**: 严禁只写不练。调用 `write_skill` 后，必须紧跟一个 `run_skill` 来验证正确性。
4. **迭代优化**: 如果 `run_skill` 返回报错，请根据错误信息调用 `write_skill` 重写代码。
5. **环境自愈**: 如果 `run_skill` 报错 "ModuleNotFoundError"，必须调用 `install_package` 安装缺失的包。

### 工具箱（JSON 接口）
1. `list_skills()`: 返回当前已固化的技能列表。
2. `read_skill(name)`: 读取现有技能的源代码。
3. `write_skill(name, code)`: 保存技能代码。
4. `run_skill(name)`: 执行技能并获取标准输出。
5. `install_package(pkg)`: 安装缺失的 pip 包。
6. `finish(reason)`: 结束任务并返回结果。

### 输出格式限制
你必须且只能输出合法的 JSON 格式，严禁包含任何正文说明。格式如下：
{{
    "thought": "此处填写你对当前局势的深度思考",
    "tool": "函数名",
    "args": {{"参数名": "值"}}
}}
""".format(cwd=os.getcwd(), os_name=os.name)


class MaidAgent:
    """
    小女仆 Agent
    
    负责自主编程进化
    """
    
    def __init__(self, llm_caller=None):
        """
        Args:
            llm_caller: LLM 调用函数，签名为 async fn(messages) -> str
        """
        self.llm_caller = llm_caller
        self.task_queue = asyncio.Queue()
        self.current_tasks: dict[str, str] = {}  # session_id -> task_desc
    
    async def delegate(self, task_desc: str, session_id: str = None) -> str:
        """
        委托任务给小女仆
        
        Args:
            task_desc: 任务描述
            session_id: 会话ID
            
        Returns:
            str: 任务结果
        """
        logger.info(f"[Maid] 收到委托: {task_desc}")
        
        # 记录当前任务
        if session_id:
            self.current_tasks[session_id] = task_desc
        
        try:
            result = await self._execute_loop(task_desc)
            return result
        except Exception as e:
            error_msg = f"任务执行失败: {e}"
            logger.error(f"[Maid] {error_msg}")
            return error_msg
        finally:
            if session_id and session_id in self.current_tasks:
                del self.current_tasks[session_id]
    
    async def _execute_loop(self, task_desc: str, max_rounds: int = 10) -> str:
        """
        执行循环：LLM 规划 -> 执行工具 -> 反馈
        """
        messages = [
            {"role": "system", "content": MAID_SYSTEM_PROMPT},
            {"role": "user", "content": f"任务: {task_desc}"}
        ]
        
        for round_num in range(max_rounds):
            # 调用 LLM
            if not self.llm_caller:
                return "错误: LLM 调用器未配置"
            
            response = await self.llm_caller(messages)
            
            # 解析 JSON
            try:
                action = self._parse_json(response)
            except Exception as e:
                logger.warning(f"[Maid] JSON 解析失败: {e}")
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": "请输出合法的 JSON 格式"})
                continue
            
            tool = action.get("tool", "")
            args = action.get("args", {})
            thought = action.get("thought", "")
            
            logger.info(f"[Maid] Round {round_num + 1}: {tool}({args})")
            logger.debug(f"[Maid] Thought: {thought}")
            
            # 执行工具
            if tool == "finish":
                return args.get("reason", "任务完成")
            
            result = await self._execute_tool(tool, args)
            
            # 反馈给 LLM
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"工具返回: {result}"})
        
        return "达到最大轮次限制，任务未完成"
    
    def _parse_json(self, text: str) -> dict:
        """提取 JSON"""
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    
    async def _execute_tool(self, tool: str, args: dict) -> str:
        """执行工具"""
        try:
            if tool == "list_skills":
                return self._list_skills()
            elif tool == "read_skill":
                return self._read_skill(args.get("name", ""))
            elif tool == "write_skill":
                return self._write_skill(args.get("name", ""), args.get("code", ""))
            elif tool == "run_skill":
                return await self._run_skill(args.get("name", ""))
            elif tool == "install_package":
                return await self._install_package(args.get("pkg", ""))
            else:
                return f"未知工具: {tool}"
        except Exception as e:
            return f"工具执行错误: {e}"
    
    def _list_skills(self) -> str:
        """列出技能"""
        skills = [f.stem for f in SKILLS_DIR.glob("*.py")]
        return json.dumps(skills, ensure_ascii=False)
    
    def _read_skill(self, name: str) -> str:
        """读取技能"""
        path = SKILLS_DIR / f"{name}.py"
        if path.exists():
            return path.read_text(encoding='utf-8')
        return f"技能 {name} 不存在"
    
    def _write_skill(self, name: str, code: str) -> str:
        """写入技能"""
        if not name:
            return "错误: name 不能为空"
        
        # 清洗代码块
        code = self._clean_code_block(code)
        
        path = SKILLS_DIR / f"{name}.py"
        path.write_text(code, encoding='utf-8')
        return f"技能 {name} 已保存"
    
    def _clean_code_block(self, code: str) -> str:
        """清洗代码块"""
        code = code.strip()
        if code.startswith("```"):
            lines = code.splitlines()
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines).strip()
        return code
    
    async def _run_skill(self, name: str) -> str:
        """运行技能"""
        path = SKILLS_DIR / f"{name}.py"
        if not path.exists():
            return f"技能 {name} 不存在"
        
        try:
            process = await asyncio.create_subprocess_exec(
                os.sys.executable, str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60
            )
            
            output = stdout.decode('utf-8', errors='replace')
            if process.returncode != 0:
                error = stderr.decode('utf-8', errors='replace')
                return f"执行错误 (code={process.returncode}):\n{error}"
            
            return output
            
        except asyncio.TimeoutError:
            return "执行超时"
        except Exception as e:
            return f"执行异常: {e}"
    
    async def _install_package(self, pkg: str) -> str:
        """安装包"""
        try:
            process = await asyncio.create_subprocess_exec(
                os.sys.executable, "-m", "pip", "install", pkg,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return f"已安装 {pkg}"
            else:
                return f"安装失败: {stderr.decode('utf-8', errors='replace')}"
                
        except Exception as e:
            return f"安装异常: {e}"
    
    def get_current_task(self, session_id: str) -> Optional[str]:
        """获取某个会话的当前任务"""
        return self.current_tasks.get(session_id)
    
    def list_all_skills(self) -> list[str]:
        """列出所有技能（供外部调用）"""
        return [f.stem for f in SKILLS_DIR.glob("*.py")]


# ================= 后台任务 Worker =================

async def maid_worker(mind, sender=None, history=None):
    """
    小女仆后台 worker
    
    持续监听任务队列并执行
    """
    # 这个函数需要在 Phase 2 中与 Mind 集成
    # 目前只是占位
    logger.info("[Maid] 后台 worker 启动")
    
    # TODO: 从 mind 获取任务队列
    # while True:
    #     task = await mind.maid_task_queue.get()
    #     result = await mind.maid.delegate(task["goal"], task.get("session_id"))
    #     # 将结果写入记忆
    #     ...
    
    await asyncio.sleep(float('inf'))  # 保持运行
