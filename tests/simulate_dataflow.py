"""
YukiV7 数据流模拟器
模拟 QQ 收到消息后的完整数据流动过程
在 ai_env conda 环境下运行: python tests/simulate_dataflow.py
"""
import sys
import asyncio
import json
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# 颜色输出
# ============================================================
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    DIM    = "\033[2m"

def banner(text, color=C.CYAN):
    print(f"\n{color}{C.BOLD}{'═'*60}{C.RESET}")
    print(f"{color}{C.BOLD}  {text}{C.RESET}")
    print(f"{color}{C.BOLD}{'═'*60}{C.RESET}")

def step(num, title):
    print(f"\n{C.BLUE}{C.BOLD}[Step {num}]{C.RESET} {C.BOLD}{title}{C.RESET}")
    print(f"{C.DIM}{'─'*50}{C.RESET}")

def data(label, value, color=C.GREEN):
    # 截断过长的值
    s = str(value)
    if len(s) > 120:
        s = s[:117] + "..."
    print(f"  {color}◆ {label}:{C.RESET} {s}")

def arrow(text=""):
    print(f"  {C.YELLOW}▼ {text}{C.RESET}")

# ============================================================
# 模拟数据
# ============================================================

# 模拟 NapCat 推送的原始消息
MOCK_WS_DATA = {
    "post_type": "message",
    "message_type": "group",
    "user_id": 88888888,
    "group_id": 1057020972,
    "raw_message": "Yuki在吗？今天天气好好呀",
    "time": 1747622400,
    "message_id": 123456789,
    "sender": {
        "user_id": 88888888,
        "nickname": "小明",
        "card": "小明同学",
        "role": "member"
    }
}

# 模拟 LLM 回复
MOCK_LLM_RESPONSE = "在的呀~今天阳光确实很棒呢，人家也想出去玩！(伸懒腰)"

# 模拟 RAG 日记
MOCK_DIARIES = [
    {"content": "【日记(2026-05-18 20:00)】：今天和群友们聊了很多关于天气的话题，大家都说想出去玩。", "score": 0.85, "debug": "[语义池] 基准:0.85 + 补偿:0.12"},
    {"content": "【日记(2026-05-17 15:30)】：小明今天问我天气怎么样，我回复说很不错。", "score": 0.72, "debug": "[关键词打捞] 基准:0.60 + 补偿:0.12"},
]


# ============================================================
# 模拟核心组件
# ============================================================

class MockYukiState:
    """模拟 V6 YukiState (brain.py)"""
    def __init__(self):
        self.energy = {}
        self.last_update = {}
        self.message_buffer = {}
        self.buffer_tasks = {}
        self.last_message_time = {}
        self.writing_diary = set()
        self.desire_to_start_topic = {}
        self.ice_break_fail_count = {}
        self.last_sent_meme = {}
        self.group_activity = {}
        self.maid_task_queue = asyncio.Queue()
        self.maid_current_tasks = {}
        self.lock = asyncio.Lock()

    def get_setting(self, mode):
        return "你是 Yuki，一个住在机主电脑里的电子妹妹..."

    def update_energy(self, chat_id):
        self.energy[chat_id] = 94.0
        return 94.0

    def update_desire_to_reply(self, chat_id):
        self.desire_to_start_topic[chat_id] = 62.5

    def consume_energy(self, chat_id):
        if chat_id in self.energy:
            self.energy[chat_id] -= 6

    async def boost_activity(self, chat_id, sensitivity=0.12):
        current = self.group_activity.get(str(chat_id), 0.0)
        self.group_activity[str(chat_id)] = current + (10.0 - current) * sensitivity


# ============================================================
# 主模拟流程
# ============================================================

async def simulate():
    banner("YukiV7 数据流模拟 — QQ 群聊消息", C.CYAN)

    # ===================== Step 1 =====================
    step(1, "NapCat WebSocket 推送原始消息")
    data("来源", "NapCat WebSocket Server → ws_connection.py")
    data("原始JSON", json.dumps(MOCK_WS_DATA, ensure_ascii=False, indent=2))
    arrow("BotConnector.listen() yield data")

    # ===================== Step 2 =====================
    step(2, "QQPlugin.receive() 接收并过滤")
    data("模块", "plugins/platforms/qq_plugin/__init__.py → receive()")

    # 过滤逻辑
    post_type = MOCK_WS_DATA["post_type"]
    msg_type = MOCK_WS_DATA["message_type"]
    user_id = MOCK_WS_DATA["user_id"]
    group_id = MOCK_WS_DATA["group_id"]

    data("post_type", f"{post_type} → {'通过 ✓' if post_type == 'message' else '跳过 ✗'}")
    data("message_type", f"{msg_type} → 进入群聊分支")
    data("target_groups", "[1057020972, 1034986009] → 群号在白名单内 ✓")

    # 命令拦截
    raw_msg = MOCK_WS_DATA["raw_message"]
    commands = ['help', '/help', 'yuki帮助', '帮助', '功能']
    is_command = raw_msg in commands
    data("命令拦截", f"'{raw_msg}' → {'拦截 ✗' if is_command else '通过 ✓'}")

    # 群开关
    data("群开关", f"group_active_state[1057020972] = True → 通过 ✓")

    # 发送者信息
    sender = MOCK_WS_DATA["sender"]
    name = sender.get("card") or sender.get("nickname") or "路人"
    is_bot = "BOT" in name or "机器人" in name
    data("发送者", f"name='{name}', is_bot={is_bot}")

    # 冒充检测
    master_name = "池宇健"
    master_id = 737337230
    is_fake = name == master_name and user_id != master_id
    data("冒充检测", f"name==master_name={name==master_name}, user_id!=master_id={user_id!=master_id} → is_fake={is_fake}")

    content = f'【"{name}"】说: {raw_msg}'
    data("构造消息", content)
    arrow("_enqueue_and_debounce()")

    # ===================== Step 3 =====================
    step(3, "防抖入队 (_enqueue_and_debounce)")
    data("模块", "QQPlugin.__init__.py → _enqueue_and_debounce()")
    data("debounce_time", "32s (被召唤时缩短为5s)")

    # 检查是否被召唤
    robot_name = "yuki"
    is_calling = robot_name in raw_msg.lower()
    data("被召唤检测", f"'{robot_name}' in '{raw_msg}'.lower() → {is_calling}")
    if is_calling:
        data("防抖缩短", "32s → 5s ⚡")

    data("入队", f"message_buffer[1057020972] += [{name}: {raw_msg[:30]}...]")
    data("创建定时器", "asyncio.create_task(_main_process()) → 取消旧的，创建新的")
    arrow("等待防抖完成 (5s)...")

    # ===================== Step 4 =====================
    step(4, "主处理 (_main_process) — 防抖完成后")
    data("模块", "QQPlugin.__init__.py → _main_process()")

    # pop buffer
    data("取出缓冲", f"message_objs = [{name}: {raw_msg}]")

    # 活跃度
    step("4.1", "活跃度提升 (boost_activity)")
    data("公式", "increment = (10.0 - current) * 0.12")
    data("当前活跃度", "0.0 → 1.2 (sigmoid 非线性增长)")

    # 图片理解
    step("4.2", "图片理解 (MemeProcessor)")
    data("检查", f"combined_text 中无图片URL → 跳过视觉理解")

    # CQ码解析
    step("4.3", "CQ码解析 (CQCodeParser)")
    data("输入", content)
    data("输出", content + " (无CQ码，原样返回)")

    # 历史加载
    step("4.4", "加载历史 + 注入系统提示词")
    data("history_file", "./data/chat_history.json")
    data("chat_id", "1057020972")
    data("系统提示词", "你是 Yuki，一个住在机主电脑里的电子妹妹... (V6 prompts)")

    # 追加消息
    current_time = datetime.datetime.now().strftime("%Y年%m月%d日%H:%M")
    data("追加 user 消息", json.dumps({
        "role": "user",
        "content": content,
        "time": current_time
    }, ensure_ascii=False))

    # ===================== Step 5 =====================
    step(5, "decide_to_reply — 精力/欲望/LLM 判断")
    data("模块", "QQPlugin._decide_to_reply()")

    # 精力
    step("5.1", "精力更新 (update_energy)")
    data("计算", "初始精力=100, 恢复率=0.8/min")
    data("当前精力", "94.0/100 (正常)")

    # 欲望
    step("5.2", "欲望计算 (update_desire_to_reply)")
    data("活跃度", "group_activity[1057020972] = 1.2")
    data("归一化", "raw_activity / 5.0 = 0.24")
    data("follow_desire", "= 0.24 * 80 * (94/100) = 18.05")
    data("ice_break_desire", "= (1-0.24) * 60 * max(0, (94-60)/40) = 30.96")
    data("融合", "max(18.05, 30.96) * 生物钟权重")
    data("生物钟权重", f"当前时间={datetime.datetime.now().hour}:00 → 白天基准0.9")
    data("sigmoid", "100 / (1 + exp(-0.08 * (total - 50))) = 62.5%")
    data("最终欲望", "62.5%")

    # 判断逻辑
    step("5.3", "回复判断")
    data("force_reply", f"yuki in raw_msg → True → 强制回复 ✅")
    data("结论", "→ should_reply = True")

    arrow("构造 PlatformEvent → 放入 _event_queue")

    # ===================== Step 6 =====================
    step(6, "ContextBus 接收事件")
    data("模块", "yuki_core/bus.py → _listen_platform() → receive()")
    data("事件来源", "_event_queue (由 QQPlugin 放入)")

    # 构造 PlatformEvent
    event = {
        "source": "qq",
        "event_type": "message",
        "content": content,
        "user_id": "88888888",
        "user_name": "小明同学",
        "session_id": "1057020972",
        "session_type": "group",
        "metadata": {
            "raw_message": content,
            "sender_name": "小明同学",
            "is_bot": False,
            "group_id": 1057020972,
        }
    }
    data("PlatformEvent", json.dumps(event, ensure_ascii=False, indent=2))

    step("6.1", "Bus 校验事件")
    data("source", "'qq' → 通过 ✓")
    data("user_id", "'88888888' → 通过 ✓")
    data("session_id", "自动填充 → '1057020972'")

    step("6.2", "Bus._decide_to_reply (防bot套娃)")
    data("逻辑", "检查是否只有BOT在召唤Yuki")
    data("raw_message", f"'{content}' → 无关键词召唤 → 返回 True (交给mind)")

    step("6.3", "注入身份上下文")
    data("platform_identity", "qq → IdentityContext(platform='qq', ...)")
    data("context_prompt", "你现在正在一个QQ群里陪大家聊天...")
    data("capabilities", "['sticker_search', 'meme_layout', 'group_manager']")

    arrow("→ Mind.process()")

    # ===================== Step 7 =====================
    step(7, "YukiMind 处理 — V7 理想路径 (实际被绕过)")
    data("模块", "yuki_core/mind.py → process()")
    data("⚠️ 说明", "QQPlugin 已在 Step 4-5 完成了全部处理")
    data("实际流程", "engine.api_reply() → build_chat_context() → yuki_chat()")

    step("7.1", "build_chat_context — 构建 LLM 上下文")
    data("模块", "plugins/.../core/prompts.py → build_chat_context()")

    # RAG 检索
    step("7.1.1", "RAG 记忆检索")
    data("查询文本", content)
    data("chat_id", "1057020972")
    data("dynamic_top_k", "10 (文本>100字) 或 8")
    data("语义池", "ChromaDB vector query → SentenceTransformer embedding")
    data("关键词池", "jieba提取 → 全量扫描匹配")
    data("检索结果", f"{len(MOCK_DIARIES)} 条日记")

    for i, d in enumerate(MOCK_DIARIES, 1):
        data(f"回忆#{i}", f"得分:{d['score']:.2f} | {d['debug']}")
        data(f"内容", f"{d['content'][:60]}...")

    # 构建 messages
    step("7.1.2", "最终 LLM Messages")
    messages = [
        {"role": "system", "content": "你是 Yuki，一个住在机主电脑里的电子妹妹..."},
        {"role": "system", "content": f"【回忆】{MOCK_DIARIES[0]['content']}"},
        {"role": "system", "content": f"【回忆】{MOCK_DIARIES[1]['content']}"},
        {"role": "user", "content": f"【时间：{current_time}】{content}"},
        {"role": "user", "content": f" (当前时间:{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}){content}"},
    ]
    data("messages数量", f"{len(messages)} 条")
    for i, m in enumerate(messages):
        preview = m["content"][:70] + "..." if len(m["content"]) > 70 else m["content"]
        data(f"msg[{i}] {m['role']}", preview)

    step("7.2", "LLM 调用")
    data("路径", "llm_call.py → yuki_chat() → robust_chat() → chat_completion()")
    data("模型", "deepseek-chat")
    data("base_url", "https://api.deepseek.com/v1")
    data("参数", "temperature=0.7, top_p=0.75, max_tokens=100")
    data("备用", "deepseek-chat (同一key)")
    data("LLM回复", MOCK_LLM_RESPONSE)

    step("7.3", "后处理标签解析")
    data("清除 FINISHED", "正则替换 → 无变化")
    data("提取布局", "正则 <布局>...</布局> → 无")
    data("提取委托", "正则 [DELEGATE_TO_MAID:...] → 无")
    data("提取表情包", "正则 [MEME_SEARCH:...] → 无")
    data("合并换行", "re.sub(r'\\n+', ' ') → 单行文本")
    data("最终回复", MOCK_LLM_RESPONSE)

    # ===================== Step 8 =====================
    step(8, "QQPlugin.send() — 发送回复")
    data("模块", "QQPlugin.__init__.py → send()")
    data("chat_id", "1057020972")
    data("mode", "group")

    # 拆分
    step("8.1", "拆分文字和表情包")
    parts = [MOCK_LLM_RESPONSE]
    data("正则", r"(\[CQ:image,...sub_type=1\])")
    data("拆分结果", f"{len(parts)} 段 (纯文字，无表情包)")

    step("8.2", "逐段发送")
    for i, part in enumerate(parts):
        data(f"发送段{i+1}", f"sender.send(1057020972, '{part[:40]}...', mode='group')")
        data("底层", "ws_sender → ws_connection → NapCat WebSocket → QQ")

    step("8.3", "记录日志")
    data("append_to_log", f"[Yuki] {MOCK_LLM_RESPONSE[:50]}...")

    # ===================== 完成 =====================
    banner("模拟完成 ✅", C.GREEN)
    print(f"""
{C.BOLD}数据流总结:{C.RESET}

  NapCat WS → BotConnector.listen() → QQPlugin.receive()
       ↓
  过滤(白名单/开关/命令) → 防抖入队(32s)
       ↓
  _main_process() (防抖后)
  ├─ 活跃度提升 (sigmoid)
  ├─ 图片理解 (跳过,无图)
  ├─ CQ码解析 (跳过,无CQ码)
  ├─ 加载历史 + 注入 system prompt
  ├─ decide_to_reply (精力94/欲望62.5/人类召唤→YES)
  └─ 构造 PlatformEvent → _event_queue
       ↓
  ContextBus.receive() (校验/防套娃/注入身份)
       ↓
  ⚠️ 实际走 V6 路径: engine.api_reply()
  ├─ build_chat_context()
  │   ├─ system prompt (V6 prompts)
  │   ├─ RAG 检索 2条日记
  │   ├─ 历史对话 (最近10条)
  │   └─ 当前消息 (带时间戳)
  ├─ yuki_chat() → robust_chat() → deepseek-chat
  ├─ 后处理 (清标签/去换行)
  └─ return 回复文本
       ↓
  QQPlugin.send()
  ├─ 拆分表情包 → 逐段发送
  ├─ ws_sender → NapCat WS → QQ群
  └─ 记录日志

{C.BOLD}耗时估算:{C.RESET}
  防抖等待:     ~5s (被召唤)
  图片理解:     ~0s (跳过)
  decide_to_reply: ~2-4s (LLM YES/NO)
  RAG 检索:     ~0.3s
  LLM 回复:     ~2-5s
  发送:         ~0.5s
  ─────────────────
  总计:         ~10-15s
""")


if __name__ == "__main__":
    asyncio.run(simulate())
