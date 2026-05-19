# YukiV7 技术文档

> 最后更新：2026-05-19
> 版本：V7 v0.2.1 (Phase 2+ 修复)

---

## 一、架构总览

```
V6 (单体)                              V7 (插件化)
═══════════                            ═══════════

main.py (全部业务)                      main.py (纯启动器)
  ├─ manage_buffer()                     ├─ load config
  ├─ main_process()                      ├─ init Core
  ├─ napcat_listen()                     ├─ load plugins
  └─ 启动所有后台任务                     └─ bus.start()

core/                                   yuki_core/ (平台无关)
  ├─ brain.py                            ├─ identity.py
  ├─ engine.py                           ├─ mind.py
  ├─ prompts.py                          ├─ memory.py
  ├─ maid.py                             ├─ maid.py (薄包装)
  └─ history_manager.py                  ├─ bus.py
                                          ├─ plugin.py
modules/                                 ├─ config.py
  ├─ memory/rag.py                       ├─ llm.py
  ├─ vision/                             ├─ history.py
  ├─ stickers/                           ├─ models.py
  └─ message/                            └─ logger.py

network/                               
  ├─ ws_connection.py                   plugins/
  └─ ws_sender.py                         platforms/
                                            └─ qq_plugin/
providers/                                  ├─ __init__.py (QQPlugin)
  ├─ registry.py                            ├─ core/
  ├─ fallback.py                            │   ├─ brain.py (V6 YukiState)
  └─ deepseek.py                            │   ├─ engine.py (V6 YukiEngine)
                                            │   ├─ prompts.py (V6 prompts)
                                            │   └─ maid.py (V6 小女仆)
                                            ├─ modules/ (原 V6 modules/)
                                            └─ network/ (原 V6 network/)
```

---

## 二、数据流 (V7 修复后)

```
[用户发消息到QQ群]
        │
        ▼
  NapCat WebSocket Server
        │
        ▼
  BotConnector.listen()              ← ws_connection.py (接收原始JSON)
        │
        ▼
  QQPlugin._listen_websocket()       ← __init__.py (后台任务, 唯一WS入口)
        │
        ├─ 过滤非 message 事件
        ├─ 私聊: _process_private_message()
        ├─ 群聊: 目标群白名单检查
        ├─ /关闭 /开启 命令拦截
        ├─ 群开关状态检查
        │
        ▼
  _enqueue_and_debounce()            ← 防抖 32s / 被召唤 5s
  (消息入队, 取消旧定时器, 创建新协程)
        │
        ▼
  _main_process()                    ← 防抖等待完成后
  │
  ├─ boost_activity()                ← 群活跃度提升 (sigmoid)
  ├─ 图片理解 (MemeProcessor)        ← 视觉模型 qwen3-vl-flash
  ├─ CQ码解析 (CQCodeParser)         ← @、回复、表情等
  ├─ 记录到日志
  ├─ 计算预过滤值 (energy/desire)
  │
  └─ 构造 PlatformEvent
     (metadata.pre_filter = {force_reply, human_calling,
      bot_calling_only, desire, energy, keywords, robot_name})
        │
        ▼
  _event_queue.put(event)
        │
        ▼
  QQPlugin.receive()                 ← yield from _event_queue
        │
        ▼
  ContextBus._listen_platform()      ← bus.py (从 receive() 消费)
        │
        ▼
  Bus.receive(event)
  ├─ 校验事件
  ├─ 追踪消息时间
  ├─ 注入身份上下文 (identity)
  ├─ 构建 decide_to_reply_fn (从 pre_filter)
  │
  ▼
  Mind.process(event, decide_to_reply_fn)
  ├─ _build_context()
  │   ├─ system prompt (从 history 或 identity)
  │   ├─ RAG 检索日记 (ChromaDB + jieba)
  │   ├─ 近期对话 (最近10轮)
  │   └─ 当前消息 (含时间戳)
  │
  ├─ decide_to_reply_fn(event, context)
  │   ├─ force_reply / human_calling → 直接放行
  │   ├─ desire >= 80 → 放行
  │   ├─ desire <= 30 → 拒绝
  │   ├─ energy < MIN_ACTIVE → 拒绝
  │   └─ 中间地带 → LLM YES/NO 判断
  │
  ├─ LLM 生成回复 (robust_chat, 主备切换)
  ├─ 解析标签 (布局/委托/表情包)
  └─ 记录到历史
        │
        ▼
  QQPlugin.send()
  ├─ 拆分文字和表情包
  ├─ sender.send() ← 通过 WebSocket 发到 NapCat
  └─ 记录日志
```

---

## 三、核心改动记录

### 2026-05-19 修复 (本次)

| 问题 | 严重度 | 修复 | 文件 |
|------|--------|------|------|
| `_event_queue` 从未被消费 | 🔴 Critical | `receive()` 改为 yield from queue; 新增 `_listen_websocket()` 后台任务 | `__init__.py` |
| 双路径并行 (QQPlugin+Mind 各做一遍) | 🔴 Critical | QQPlugin 只做预处理+预过滤, Mind 做 context build+LLM | `__init__.py`, `mind.py`, `bus.py` |
| Mind.process() 执行顺序错误 | 🟡 Medium | decide_to_reply 移到 llm_caller check 之前 | `mind.py` |
| decide_to_reply 需要 context | 🟡 Medium | context build 移到 decide_to_reply 之前 | `mind.py` |
| websockets v16 兼容 | 🟡 Medium | 移除 `State.OPEN` 引用, 改用 `.open`/`.closed` | `ws_connection.py` |
| 配置冲突 (debounce 28 vs 32) | 🟢 Low | 统一为 32 | `config.yaml` |
| yuki_core/maid.py 冗余 | 🟢 Low | 改为薄包装, 委托 V6 实现 | `maid.py` |

### 数据流变化

```
修复前:                                    修复后:
                                         
receive() ← connector.listen()            _listen_websocket() ← connector.listen()
  │ (直接 yield 原始数据)                    │
  ▼                                         ▼
Bus.receive()                           _enqueue_and_debounce()
  │                                         │
  ▼                                         ▼
Mind.process()                          _main_process()
  ├─ build context                          ├─ 预处理 (图片/CQ码)
  ├─ LLM 回复                               ├─ 预过滤 (energy/desire)
  └─ 返回                                   └─ _event_queue.put()
                                         
Meanwhile:                                receive() ← _event_queue
QQPlugin._main_process()                   │
  ├─ build context (重复!)                   ▼
  ├─ decide_to_reply (重复!)             Bus.receive()
  ├─ LLM 调用 (重复!)                     ├─ build context (一次)
  └─ _event_queue.put()                   ├─ decide_to_reply (一次)
  (但 queue 从未被消费!)                   └─ LLM 调用 (一次)
```

---

## 四、模块职责

### yuki_core/ (平台无关)

| 模块 | 职责 | 状态 |
|------|------|------|
| `identity.py` | 人格定义, system prompt 生成, identities.yaml 加载 | ✅ 完整 |
| `mind.py` | 构建上下文, decide_to_reply, LLM 生成, 标签解析 | ✅ 完整 |
| `memory.py` | ChromaDB 向量检索 + jieba 关键词补偿, 日记存储 | ✅ 完整 |
| `bus.py` | 插件注册, 事件路由, decide_to_reply_fn 构建, action 执行 | ✅ 完整 |
| `plugin.py` | PlatformPlugin/CapabilityPlugin 基类, 插件加载器 | ✅ 完整 |
| `config.py` | 配置中心, YAML 加载, V6 兼容属性 | ✅ 完整 |
| `llm.py` | requests 同步 → asyncio.to_thread, 主备切换 | ✅ 可用 |
| `history.py` | 原子化 JSON 读写, 线程安全 | ✅ 完整 |
| `models.py` | PlatformEvent, YukiResponse, Action 等数据模型 | ✅ 完整 |
| `maid.py` | 小女仆薄包装, 委托 V6 实现 | ✅ 可用 |
| `logger.py` | 日志配置 | ✅ 完整 |

### plugins/platforms/qq_plugin/ (QQ 平台)

| 模块 | 职责 | 状态 |
|------|------|------|
| `__init__.py` | QQPlugin: WS 监听, 防抖, 预处理, 预过滤, 发送 | ✅ 完整 |
| `core/brain.py` | YukiState: 精力/欲望/活跃度/生物钟, V6 全状态 | ✅ V6 原样 |
| `core/engine.py` | YukiEngine: 日记/破冰/小女仆后台任务 | ✅ V6 原样 |
| `core/prompts.py` | V6 prompts: get_yuki_setting_group 等 | ✅ 保留供后台用 |
| `core/maid.py` | V6 小女仆: maid_evolution_loop | ✅ V6 原样 |
| `llm_call.py` | yuki_chat: V6 接口 → V7 robust_chat | ✅ 适配层 |
| `modules/` | V6 原样迁移: vision/stickers/message/memory | ✅ V6 原样 |
| `network/` | V6 原样迁移: ws_connection/ws_sender | ✅ 已修复兼容 |

---

## 五、剩余问题

### 架构层面

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | **engine.py 后台任务仍走 V6 路径** | 中 | `idle_diary_checker`/`ice_break_monitor`/`break_ice` 直接调 `yuki_chat` + `build_chat_context`, 不经过 Mind。日记写入用 V6 的 `do_summarize` 而非 Mind 的 context build。 |
| 2 | **两套 system prompt 并存** | 中 | `identity.py` (V7) 生成的 prompt 和 `core/prompts.py` (V6) 生成的 prompt 内容高度一致但不完全相同。启动时 `sync_system_prompts` 用 V6 版本写入 history, Mind 兜底用 V7 版本。 |
| 3 | **两套 RAG 实现并存** | 低 | `yuki_core/memory.py` (YukiMemory) 和 `plugins/.../modules/memory/rag.py` (MemoryRAG) 功能重叠。QQPlugin 的后台任务用 V6 RAG, Mind 用 V7 memory。 |
| 4 | **Mind 不感知精力/欲望/活跃度** | 低 | decide_to_reply 依赖 QQPlugin 的 pre_filter 预计算值。如果 pre_filter 缺失 (非QQ平台), Mind 无法自行计算。这是设计选择 (Mind 平台无关), 但意味着新平台需要自己实现类似的预过滤。 |
| 5 | **小女仆未接入 Bus action 系统** | 低 | Mind 解析 `[DELEGATE_TO_MAID]` 标签生成 Action, 但 Bus._execute_delegate() 只打日志不执行。实际执行由 QQPlugin 的 `_bg_maid_worker` 后台任务从 V6 队列消费。 |

### 稳定性

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 6 | **LLM 错误判断用字符串前缀** | 中 | `robust_chat` 用 `result.startswith("（")` 判断是否为错误。如果 LLM 正好以括号开头会误判。 |
| 7 | **无连接池** | 低 | `llm.py` 用 requests 同步调用, 每次新建 TCP 连接。无 aiohttp Session 复用。 |
| 8 | **reconnect 时 YukiState 重置** | 中 | `QQPlugin.connect()` 每次重连都 `YukiState()`, 清空精力/欲望/活跃度。长时间运行的状态会丢失。 |

### 功能缺失

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 9 | **私聊消息无预过滤** | 低 | `_process_private_message` 入队后走 `_main_process`, 但私聊的 pre_filter 中 desire/energy 可能未正确计算 (私聊不走群活跃度逻辑)。 |
| 10 | **Bus._execute_delegate 占位** | 低 | 小女仆委托的 Action 被解析但 Bus 层不执行, 依赖 QQPlugin 后台任务的 V6 队列。 |

---

## 六、配置文件

### configs/config.yaml
主配置: API keys, 模型, 路径, 能量参数, 生物钟参数

### configs/plugins.yaml
插件配置: 平台开关, WebSocket URL, 群白名单, 防抖时间

### configs/identities.yaml
身份配置: 核心人格, 多平台 context prompt, 风格覆盖

**注意**: `config.yaml` 和 `plugins.yaml` 有部分重叠配置 (如 debounce_time, target_groups)。QQPlugin 优先读 `plugins.yaml`, 回退到 `config.yaml`。

---

## 七、启动流程

```
main.py
  ├─ cfg.load()                          # 加载 configs/config.yaml
  ├─ YukiIdentity(identities.yaml)       # V7 身份
  ├─ YukiMind(identity)                  # V7 决策引擎
  ├─ YukiMemory(...)                     # V7 记忆 (ChromaDB)
  ├─ HistoryManager(...)                 # 历史管理
  ├─ MaidAgent()                         # V7 小女仆占位
  ├─ ContextBus(mind, identity, cfg)     # 上下文总线
  ├─ load_plugins_from_config(bus, ...)  # 加载 QQPlugin
  ├─ inject_global_config(bus, ...)      # 注入 master_id/name/keywords
  └─ bus.start()
      ├─ _listen_platform(qq)            # 连接 + 监听 + 重连
      │   └─ plugin.receive() → bus.receive() → mind.process() → plugin.send()
      ├─ _bg_listen_websocket            # WS 监听 → 防抖 → 队列
      ├─ _bg_decay_heartbeat             # 精力衰减 (5min)
      ├─ _bg_idle_diary_checker          # 空闲日记 (30s)
      ├─ _bg_ice_break_monitor           # 破冰监控 (600-1800s)
      └─ _bg_maid_worker                 # 小女仆 worker
```

---

## 八、与 V6 的行为差异

| 行为 | V6 | V7 | 影响 |
|------|----|----|------|
| LLM 调用次数 (欲望中间地带) | 2次 (decide+reply) | 2次 (decide+reply) | 一致 |
| LLM 调用次数 (force/desire>=80) | 1次 (reply only) | 1次 (reply only) | 一致 |
| LLM 调用次数 (desire<=30) | 0次 | 0次 | 一致 |
| system prompt 来源 | core/prompts.py 硬编码 | history[0] 或 identity.py | 内容一致, 格式微调 |
| 日记写入 | engine.do_summarize (V6) | engine.do_summarize (V6) | 一致 (走 V6) |
| 破冰 | engine.break_ice (V6) | engine.break_ice (V6) | 一致 (走 V6) |
| 小女仆 | engine.maid_worker (V6) | engine.maid_worker (V6) | 一致 (走 V6) |
| 新平台支持 | 不支持 | 只需写 PlatformPlugin | V7 优势 |

---

## 九、测试

```bash
# 运行测试 (需要在项目根目录)
cd D:\Projects\YukiV7
python tests/test_qq_plugin.py
```

测试覆盖:
- 模块导入
- 消息截断 (smart_truncate)
- CQ 码解析
- 群聊管理 (/开启 /关闭)
- 提示词生成
- 插件配置加载
- Bus 事件接收流程
