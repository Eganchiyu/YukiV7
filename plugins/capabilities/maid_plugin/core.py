# plugins/capabilities/maid_plugin/core.py
"""
小女仆自主编程代理 — 核心进化循环

从 plugins/platforms/qq_plugin/core/maid.py 迁移而来。
LLM 调用改为 yuki_core.llm.robust_chat，不依赖 QQ 插件。
"""

import json
import subprocess
import os
import sys
import asyncio
import re
import logging
from datetime import datetime

from yuki_core.config import cfg
from yuki_core.llm import robust_chat

logger = logging.getLogger("maid")

# ================= 目录 =================
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

os.makedirs(SKILLS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


# ================= LLM 调用 =================

async def _call_llm(messages: list[dict]) -> str:
    """调用 LLM，使用 yuki_core 的通用接口，带主备切换"""
    result = await robust_chat(
        messages=messages,
        model=cfg.LLM_MODEL,
        api_key=cfg.LLM_API_KEY,
        base_url=cfg.LLM_BASE_URL or "https://api.deepseek.com",
        backup_model=cfg.BACKUP_MODEL,
        backup_api_key=cfg.BACKUP_API_KEY,
        backup_base_url=cfg.BACKUP_BASE_URL or "https://api.deepseek.com",
        disable_thinking=cfg.DISABLE_THINKING,
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return _clean_json_output(result)


# ================= 工具函数 =================

def _clean_json_output(text: str) -> str:
    """提取第一个 { 到最后一个 } 之间的内容"""
    if not text:
        return ""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text.strip()


def _clean_code_block(raw_code: str) -> str:
    """清洗模型输出的代码，移除 Markdown 标记"""
    if not raw_code:
        return ""
    code = raw_code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


# ================= 技能操作 =================

def write_skill(name: str, code: str) -> str:
    if not name or name == "None":
        return "错误：你没有为技能提供有效的 'name'。"
    path = os.path.join(SKILLS_DIR, f"{name}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return f"技能 {name} 已保存。"


async def run_skill(name: str) -> str:
    path = os.path.join(SKILLS_DIR, f"{name}.py")
    if not os.path.exists(path):
        available = os.listdir(SKILLS_DIR)
        return f"找不到技能 '{name}'，当前可用技能: {available}"

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(SKILLS_DIR),  # 在 skill 目录的上级运行
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except (asyncio.TimeoutError, asyncio.exceptions.TimeoutError):
            # 超时强制终止进程树
            try:
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                    capture_output=True, check=False,
                )
            except Exception:
                process.kill()
            await process.wait()
            return "错误：执行超时（60s）。进程已被强制终止。"

        def decode_output(output_bytes):
            if not output_bytes:
                return ""
            for enc in ['utf-8', 'gbk', 'cp936']:
                try:
                    return output_bytes.decode(enc)
                except UnicodeDecodeError:
                    continue
            return output_bytes.decode('utf-8', errors='replace')

        stdout_res = decode_output(stdout).strip()
        stderr_res = decode_output(stderr).strip()

        if process.returncode == 0:
            if not stdout_res:
                return "执行成功，但没有任何输出（请确保代码内有 print 语句输出结果）"
            return f"执行成功！输出：\n{stdout_res}"
        else:
            error_msg = stderr_res if stderr_res else stdout_res
            return f"代码执行失败 (ReturnCode: {process.returncode})\n报错详情：\n{error_msg}"

    except Exception as e:
        return f"系统异常：{str(e)}"


def install_package(pkg: str) -> str:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return f"成功安装依赖包: {pkg}"
    except Exception as e:
        return f"安装失败: {str(e)}"


def list_skills() -> list:
    if not os.path.exists(SKILLS_DIR):
        return []
    return [f[:-3] for f in os.listdir(SKILLS_DIR) if f.endswith(".py")]


def read_skill(name: str) -> str:
    if not name or name == "None":
        return "错误：你没有为技能提供有效的 'name'。"
    path = os.path.join(SKILLS_DIR, f"{name}.py")
    if not os.path.exists(path):
        available = list_skills()
        return f"错误：找不到技能 '{name}'，当前可用技能: {available}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        return f"--- 技能 {name}.py 源码 ---\n{code}\n-------------------------"
    except Exception as e:
        return f"读取技能文件失败：{str(e)}"


# ================= 系统提示词 =================

MAID_SYSTEM_PROMPT = f"""
你是一个具备高度自主进化能力的 AI 智能体，代号：**小女仆**。
你的上位机是Yuki，一个有点笨的AI智能体。她住在机主的电脑里，通过QQ和外界连通，你是她的内部帮手，可以操作本地电脑环境来帮助她。所以如果指令不清楚，可以直接返回告诉她。
如果任务内容包括发送消息，请不要执行发送，直接返回发送的文件的绝对路径或内容。
如果任务是知识类任务，你可以在第一轮直接依靠知识库做返回。

### 核心使命
通过编写、优化和复用 Python 技能（Skills）来完成用户指令。

### 运行上下文
- **当前路径**: {os.getcwd()}
- **操作系统**: {os.name} (请确保编写的代码跨平台兼容)
- **技能存储**: 所有技能存放在 `{SKILLS_DIR}` 目录下，以 `.py` 结尾。

### 进化法则（行为规范）
1. **检索优先**: 面对任务，首先调用 `list_skills` 检查是否有现成或类似的技能。
2. **模块化编写**: 编写技能时，务必包含必要的 try-except 块，并确保输出结果易于被你解析。
3. **即写即用**: 严禁只写不练。调用 `write_skill` 后，必须紧跟一个 `run_skill` 来验证正确性。
4. **迭代优化**: 如果 `run_skill` 返回报错，请根据错误信息调用 `write_skill` 重写代码。
5. **环境自愈**: 如果 `run_skill` 报错 "ModuleNotFoundError"，必须调用 `install_package` 安装缺失的包。

### 工具箱（JSON 接口）
1. `list_skills()`: 返回当前已固化的技能列表（返回的名称不含 .py 后缀）。
2. `read_skill(name)`: 读取现有技能的源代码。不用加上.py后缀。
3. `write_skill(name, code)`: 
   - 'name': 必须是一个简短的英文标识符（如 'get_memory'），严禁不填或填 None，不用加上.py后缀。
   - 'code': 完整的 Python 代码（直接写代码，不要加 ```python 标记），需要包含print()语句来输出你需要的数据信息。请务必书写主程序，以免定义了函数但没有被调用而返回None。
4. `run_skill(name)`: 执行技能并获取标准输出（stdout）。不用加上.py后缀。
5. `install_package(pkg)`: 安装缺失的 pip 包。
6. `finish(reason)`: 
   - **禁止盲目结束**：严禁在没有看到成功结果或输出的具体数据的情况下调用此工具。
   - **必须总结结果**：在 `reason` 中必须包含你获取到的实际数据（例如：'任务完成，CPU温度为 65.3°C'）。
   - **例外情况**：注意！如果给你的指令不清不楚，不确定性太大，可以直接调用来打回任务，并说明任务不明确。
   - **reason格式**：如果任务涉及文件书写操作，reason中应包含保存的文件的绝对路径。

### 输出格式限制
你必须且只能输出合法的 JSON 格式，严禁包含任何正文说明。格式如下：
{{
    "thought": "此处填写你对当前局势的深度思考，以及接下来的行动逻辑",
    "tool": "函数名",
    "args": {{"参数名": "值"}}
}}

结束程序示例：
{{
    "thought": "任务已完成，结果符合预期。",
    "tool": "finish",
    "args": {{"reason": "当前系统时间：2026-04-15 22:23:31"}}
}}
"""


# ================= 进化循环 =================

async def maid_evolution_loop(user_goal: str, chat_id: str = None) -> dict:
    """
    小女仆自主进化循环：LLM 驱动的 think-act-observe 循环，最多 20 轮。
    
    Args:
        user_goal: 任务目标描述
        chat_id: 可选的会话 ID，用于日志追踪
    
    Returns:
        {"status": "finished"|"timeout", "result": str, "goal": str}
    """
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOGS_DIR, f"trace_{task_id}.md")
    created_skill_files = []

    current_skills = list_skills()
    messages = [
        {"role": "system", "content": MAID_SYSTEM_PROMPT},
        {"role": "user", "content": f"目标：{user_goal}\n当前技能：{current_skills}"}
    ]

    logger.info(f"[Maid] 🚀 任务启动: {user_goal}")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"# 小女仆任务追踪: {task_id}\n\n**任务目标**: {user_goal}（如果涉及发送图片到群聊的任务，只需要保存文件，并最终返回该文件的绝对路径，说明这个图片可以被发送即可）\n\n---\n")

    for i in range(1, 21):
        logger.info(f"[Maid] 🔍 第 {i} 轮决策中...")

        content = await _call_llm(messages)

        if f"{cfg.ROBOT_NAME.title()} 好像有点不舒服" in content:
            logger.error("[Maid] ❌ 线路全线崩溃，停止尝试。")
            break

        try:
            call = json.loads(content)
            thought = call.get("thought", "思考中...")
            tool = call.get("tool")
            args = call.get("args", {})

            logger.info(f"[Maid] 💭 思考: {thought}")
            logger.info(f"[Maid] 🛠️  动作: {tool}")

            if tool == "list_skills":
                res = list_skills()
            elif tool == "write_skill":
                skill_name = args.get('name')
                res = write_skill(skill_name, _clean_code_block(args.get('code', '')))
                if skill_name:
                    file_path = os.path.join(SKILLS_DIR, f"{skill_name}.py")
                    if file_path not in created_skill_files:
                        created_skill_files.append(file_path)
            elif tool == "run_skill":
                res = await run_skill(args.get('name'))
            elif tool == "install_package":
                pkg_name = args.get('pkg') or args.get('pkg_name')
                logger.info(f"[Maid] 📦 正在安装依赖: {pkg_name}")
                res = install_package(pkg_name.strip()) if pkg_name else "错误：未提供包名"
            elif tool == "read_skill":
                skill_name = args.get('name')
                logger.info(f"[Maid] 正在查阅技能源码: {skill_name}")
                res = read_skill(skill_name)
            elif tool == "finish":
                reason = args.get('reason', '任务完成')
                logger.info(f"[Maid] ✅ 任务达成: {reason}")

                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"### 任务完成\n**结果**: {reason}\n")
                return {"status": "finished", "result": reason, "goal": user_goal}
            else:
                res = f"错误：未知工具 {tool}"

            # 写入日志
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"### 步骤 {i}\n**思考**: {thought}\n\n**动作**: `{tool}`({args})\n\n**结果**: \n{res}\n\n")

            messages.append({"role": "assistant", "content": content})
            feedback = f"执行结果：\n{res}"
            if "NameError" in str(res):
                feedback += "\n[系统提示]: 你似乎忘记在代码中 'import' 必要的库了。"
            messages.append({"role": "user", "content": feedback})

        except json.JSONDecodeError:
            logger.warning("[Maid] ⚠️ JSON 解析失败，正在反馈给模型重试...")
            messages.append({"role": "user", "content": "错误：请务必输出纯净的 JSON 格式。"})
        except Exception as e:
            logger.error(f"[Maid] 🧨 运行异常: {str(e)}")
            messages.append({"role": "user", "content": f"运行中发生异常：{str(e)}（如果任务涉及发送图片任务，只需要保存文件，并在finish中返回该文件的绝对路径，说明这个图片可以被发送即可）"})

    # 超时清理
    for path in created_skill_files:
        if os.path.exists(path):
            os.remove(path)
    return {"status": "timeout", "result": "任务处理超时。", "goal": user_goal}
