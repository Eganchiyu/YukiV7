# YukiV7 全面核查报告

> 测试结果: 41/41 通过 🎉
> 核查时间: 2026-05-19

---

## 一、架构对比：V6 vs V7

```
V6 (单体架构)                          V7 (插件化架构)
═══════════════                        ═════════════════

main.py (入口+全部业务)                 main.py (纯启动器)
  ├─ manage_buffer()                     ├─ load config
  ├─ main_process()                      ├─ init Core
  ├─ napcat_listen()                     ├─ load plugins
  └─ 启动所有后台任务                     └─ bus.start()

core/                                   yuki_core/ (平台无关)
  ├─ brain.py (YukiState)                ├─ identity.py  (人格定义)
  ├─ engine.py (YukiEngine)              ├─ mind.py      (决策引擎-精简)
  ├─ prompts.py                          ├─ memory.py    (RAG记忆)
  ├─ maid.py                             ├─ maid.py      (小女仆-占位)
  └─ history_manager.py                  ├─ bus.py       (上下文总线)
                                          ├─ plugin.py   (插件基类)
modules/                                ├─ config.py    (配置中心)
  ├─ memory/rag.py                       ├─ llm.py       (LLM调用器)
  ├─ vision/                             ├─ history.py   (历史管理)
  ├─ stickers/                           ├─ models.py    (数据模型)
  ├─ message/                            └─ logger.py    (日志)
  └─ label.py
                                        plugins/
network/                                  platforms/
  ├─ ws_connection.py                     └─ qq_plugin/  (V6业务迁移)
  └─ ws_sender.py                           ├─ __init__.py (QQPlugin类)
                                            ├─ core/brain.py  (V6 YukiState)
providers/ (LLM多供应商)                    ├─ core/engine.py (V6 YukiEngine)
  ├─ registry.py                           ├─ core/prompts.py
  ├─ fallback.py                           ├─ core/maid.py   (V6小女仆)
  ├─ deepseek.py                           ├─ llm_call.py   (适配层)
  └─ ...                                   ├─ modules/      (原V6 modules/)
                                            └─ network/      (原V6 network/)
```

---

## 二、核心改动总结

### 1. 从单体到插件化

- **V6**: 所有代码耦合在 `main.py` 中，`manage_buffer()` → `main_process()` → `napcat_listen()`，后台任务、消息处理、LLM调用全部交织。
- **V7**: 清晰分层 `main.py` → `ContextBus` → `QQPlugin`，Core层完全平台无关，QQPlugin封装所有QQ业务。新增平台只需写一个新的 `PlatformPlugin`。

### 2. LLM 调用器统一

- **V6**: `providers/` 目录下7个文件，`ProviderRegistry` → `FallbackProvider` → `DeepSeekProvider/DashScopeProvider/...`，aiohttp Session 复用，多供应商注册，Fallback链。
- **V7**: `llm.py` 一个文件，`requests` 同步调用 → `asyncio.to_thread` 包装，主备切换用哨兵前缀（`"（"` 开头=错误），每次请求独立线程安全。
  - 优点: 极简，无 aiohttp 依赖
  - 缺点: 没有 Connection 复用，每次请求重新建连

### 3. 身份系统重构

- **V6**: `prompts.py` 里硬编码 `get_yuki_setting_group()` 等，字符串拼接，平台特化写死。
- **V7**: `identity.py` 数据驱动，`Persona` + `SpeakingStyle` + `Relationship` (dataclass)，从 `identities.yaml` 加载。`PlatformIdentity` 支持多平台配置。保持了兼容函数 `get_yuki_setting_group()` / `get_yuki_setting_private()`。

### 4. 决策引擎精简

- **V6**: `YukiMind` 包含精力/欲望/生物钟/破冰/日记全部逻辑。
- **V7**: `YukiMind` 只做: 1. 构建上下文 (system + RAG + 历史 + 当前消息); 2. 调用 LLM; 3. 解析标签 (布局/委托/表情包)。精力/欲望/生物钟/破冰/日记 全部移至 QQPlugin 层。
  - 意义: Mind 可以给任何平台复用（web/voice/Discord 等）

### 5. ContextBus (上下文总线)

- **V6**: 无总线概念，`main_process` 直接调用所有组件。
- **V7**: `bus.py` 统一入口 `Plugin.receive()` → `Bus.receive()` → `Mind.process()` → `Plugin.send()`。职责: 插件注册与生命周期、事件路由、自动重连 (指数退避)、Action 执行调度、后台任务管理。

### 6. 双层 LLM 调用并存

> ⚠️ **重要发现**: V7 存在两套 LLM 调用路径

- **路径A (V7 Core)**: `main.py` → `bus.receive()` → `mind.process()` → `llm_caller()` — 这是 V7 架构设计的理想路径。
- **路径B (V6遗留)**: `QQPlugin._main_process()` → `engine.api_reply()` → `yuki_chat()` — 精力/欲望/日记/破冰等 V6 业务逻辑仍走这条路径。
- **现状**: 路径B 承担了几乎所有实际工作，路径A 虽然代码存在但 QQPlugin 的 `receive()` 已经在内部完成了全部处理。

---

## 三、完整数据流

### 消息处理流程 (实际运行路径)

```
[用户发消息到QQ群]
        │
        ▼
  NapCat WebSocket Server
        │
        ▼
  BotConnector.listen()           ← ws_connection.py (接收原始JSON)
        │
        ▼
  QQPlugin.receive()              ← __init__.py (AsyncIterator yield)
        │
        ├─ 过滤非 message 事件
        ├─ 私聊: _process_private_message()
        ├─ 群聊: 目标群白名单检查
        ├─ /关闭 /开启 命令拦截
        ├─ 群开关状态检查
        │
        ▼
  _enqueue_and_debounce()         ← 防抖 32s / 被召唤 5s
  (消息入队，取消旧定时器，创建新协程)
        │
        ▼
  _main_process()                 ← 防抖等待完成后
  │
  ├─ boost_activity()             ← 群活跃度提升 (sigmoid)
  ├─ 图片理解 (MemeProcessor)     ← 视觉模型 qwen3-vl-flash
  ├─ CQ码解析 (CQCodeParser)      ← @、回复、表情等
  ├─ 记录到日志
  ├─ 加载历史 + 注入系统提示词
  ├─ 追加当前消息到历史
  │
  ├─ _decide_to_reply()           ← 精力/欲望/LLM判断
  │   ├─ update_energy()          ← 精力恢复 (0.8/min)
  │   ├─ update_desire_to_reply() ← 欲望计算 (sigmoid)
  │   │   ├─ follow_desire = 活跃度 * 80 * (精力/100)
  │   │   ├─ ice_break_desire = (1-活跃度) * 60 * ...
  │   │   ├─ 生物钟权重 get_smooth_time_weight()
  │   │   └─ sigmoid 归一化
  │   ├─ 人类@Yuki → 强制回复
  │   ├─ 仅BOT@ → 欲望 * 0.7
  │   ├─ 欲望>=80 → 回复, <=30 → 拒绝
  │   └─ LLM 判断 (YES/NO, max_tokens=10)
  │
  ├─ 构造 PlatformEvent
  └─ 放入 _event_queue
        │
        ▼
  ContextBus._listen_platform()   ← bus.py (从 event_queue 消费)
        │
        ▼
  Bus.receive(event)
  ├─ 校验事件
  ├─ 追踪消息时间
  ├─ _decide_to_reply() (bus层, 防bot套娃)
  ├─ 注入身份上下文
  ├─ Mind.process()
  └─ 执行 actions
        │
        ▼
  QQPlugin.send()
  ├─ 拆分文字和表情包
  ├─ sender.send() ← 通过 WebSocket 发到 NapCat
  └─ 记录日志
```

### 后台任务 (4个常驻)

| 任务 | 间隔 | 触发条件 | 动作 |
|------|------|----------|------|
| `_bg_decay_heartbeat` | 5分钟 | 始终 | 活跃度 * 0.65 衰减 |
| `_bg_idle_diary_checker` | 30秒 | 空闲>180s & 轮数>20 | LLM写日记 → ChromaDB |
| `_bg_ice_break_monitor` | 600~1800s随机 | 活跃度<0.5 & 欲望>75 & 失败<2 | RAG检索 → LLM破冰 |
| `_bg_maid_worker` | 持续监听队列 | 有委托任务 | LLM规划 → 写/运行Python技能 → 汇报 |

### RAG 记忆流

```
[消息到达]
    │
    ├─ engine.api_reply() → build_chat_context()
    │   └─ memory_rag.search_diaries()
    │       ├─ 语义池: ChromaDB vector query (SentenceTransformer)
    │       ├─ 关键词池: jieba提取关键词 → 全量扫描匹配
    │       └─ 合并: 去重 + 分数加权 + 排序
    │
    └─ idle_diary_checker() → do_summarize()
        └─ LLM写日记 → memory_rag.save_diary()
            └─ ChromaDB.add(embedding + content + metadata)
```

---

## 四、潜在问题

1. **⚠️ 双路径并行**: QQPlugin 内部完成了全部处理 (V6 路径)，`Bus.receive()` → `Mind.process()` 几乎没被用到。V7 的插件架构目前只是"外壳"，核心逻辑还是 V6。

2. **⚠️ 两套小女仆实现**: `yuki_core/maid.py` (V7 新版) 和 `plugins/.../core/maid.py` (V6 迁移版) 并存。实际使用中走 V6 版本。

3. **⚠️ websockets 版本兼容**: `ws_connection.py` 使用 websockets 库，环境版本 16.0 有破坏性 API 变更。`ensure_connection()` 里的 `State.OPEN` 检查可能有兼容问题。

4. **⚠️ 人格 prompt 双源**: `identities.yaml` + `identity.py` (V7) 与 `core/prompts.py` (V6) 并存。QQPlugin 实际使用 V6 版本。修改身份时两边都要改。

5. **⚠️ 配置冲突**: `configs/config.yaml` 的 `debounce_time` 是 28，但 `plugins.yaml` 的是 32。QQPlugin 优先用 `plugins.yaml`。

---

## 五、结论

- **V7 可以正常运行** ✅ (41/41 测试通过)
- **实质**: V7 是 V6 的"插件化外壳"，QQPlugin 封装了 V6 全部业务逻辑，Core 层已抽离但 QQPlugin 内部仍走 V6 的 engine→llm_call 路径。
- **要让 V7 真正发挥架构优势**:
  1. 让 QQPlugin 的 `receive()` 只做格式转换，把业务逻辑交给 Mind
  2. 统一 prompt 来源 (identities.yaml)
  3. 清理 `yuki_core/maid.py` 的冗余
