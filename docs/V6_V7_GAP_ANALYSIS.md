# Yuki V6 → V7 差距分析

> 最后更新：2026-05-19 (二次核查后)

---

## 一、功能对比总表

| 子系统 | V6 位置 | V7 位置 | 状态 | 说明 |
|--------|---------|---------|------|------|
| **消息缓冲/防抖** | main.py manage_buffer | qq_plugin._enqueue_and_debounce | ✅ 完整 | 32s / 被召唤5s |
| **Bot 白名单** | main.py | qq_plugin + config.yaml | ✅ 完整 | |
| **Bot 召唤打折** | engine.py decide_to_reply | bus.py _make_decide_to_reply | ✅ 完整 | 仅BOT召唤 → 欲望×0.7 |
| **说话风格/自称** | core/prompts.py | identity.py + identities.yaml | ✅ 完整 | Yuki/人家交替, 亲昵温柔 |
| **群聊拉扯感** | prompts.py | identities.yaml qq context | ✅ 完整 | 布局+拉扯详细指令 |
| **日记系统** | engine.py idle_diary_checker | qq_plugin._bg_idle_diary_checker → engine | ✅ 迁移 | 走 V6 engine 路径 |
| **历史管理** | history_manager.py | yuki_core/history.py | ✅ 迁移 | 原子化写入, 线程安全 |
| **记忆/RAG** | modules/memory/rag.py | yuki_core/memory.py + V6 rag | ⚠️ 并存 | Mind用V7, 后台用V6 |
| **表情包管理** | modules/stickers/manager.py | plugins/.../stickers/manager.py | ✅ V6原样 | |
| **表情包视觉处理** | modules/vision/processor.py | plugins/.../vision/processor.py | ✅ V6原样 | |
| **身份系统** | core/prompts.py | identity.py + YAML | ✅ 重构 | 双源并存但内容一致 |
| **群聊开关** | main.py inline | qq_plugin._handle_group_command | ✅ 完整 | 持久化+回复+buffer清空 |
| **破冰主动唤醒** | engine.py ice_break_monitor | qq_plugin._bg_ice_break_monitor → engine | ✅ 迁移 | 走 V6 engine 路径 |
| **小女仆** | engine.py maid_worker | qq_plugin._bg_maid_worker → engine | ✅ 迁移 | 走 V6 engine 路径 |
| **决策引擎** | engine.py decide_to_reply (LLM) | bus.py _make_decide_to_reply (预过滤+LLM) | ✅ 完整 | 预过滤节省LLM调用 |
| **表情包正反馈(RLHF)** | main.py manage_buffer | qq_plugin._check_meme_feedback | ✅ 完整 | |
| **活跃度衰减** | brain.py decay_heartbeat | brain.py decay_heartbeat (V6原样) | ✅ 完整 | |
| **AsyncIO 锁** | brain.py self.lock | brain.py self.lock (V6原样) | ✅ 完整 | |
| **LLM 传输层** | aiohttp (异步连接池) | requests (同步 to_thread) | ⚠️ 降级 | 无连接复用 |
| **熔断器** | FallbackProvider | robust_chat (主备切换) | ⚠️ 简化 | 字符串前缀判断错误 |
| **群状态持久化** | data/group_state.json | data/group_state.json | ✅ 完整 | |
| **帮助指令** | main.py /help → 发送图片 | qq_plugin._enqueue_and_debounce | ✅ 完整 | |
| **System Prompt 同步** | prompts.py sync_system_prompts | qq_plugin.connect() 调用 | ✅ 完整 | |
| **双路径问题** | 不存在 | — | ✅ 已修复 | QQPlugin只预处理, Mind做LLM |

---

## 二、已修复的问题 (2026-05-19)

### 第一轮修复

| 修复项 | 文件 | 说明 |
|--------|------|------|
| 配置冲突 | config.yaml | debounce 28→32, 与 plugins.yaml 统一 |
| websockets兼容 | ws_connection.py | 移除 State.OPEN, 改用 .open/.closed |
| 双路径并行 | __init__.py, mind.py, bus.py | QQPlugin只预处理, Mind做context+LLM |
| 小女仆冗余 | yuki_core/maid.py | 改为薄包装, 委托V6实现 |
| Prompt统一 | mind.py | 兜底用V7 identity, 非V6 prompts.py |

### 第二轮修复 (二次核查)

| 修复项 | 文件 | 说明 |
|--------|------|------|
| _event_queue未消费 | __init__.py | receive()从queue读取; 新增_listen_websocket() |
| Mind执行顺序 | mind.py | decide_to_reply移到llm_caller check之前 |
| context依赖 | mind.py | context build移到decide_to_reply之前 |

---

## 三、剩余差距

### 高优先级

| # | 差距 | 说明 | 工时 |
|---|------|------|------|
| 1 | engine.py 后台任务仍走V6 | 日记/破冰/小女仆用V6的build_chat_context+yuki_chat, 不经过Mind。功能正常但架构不统一。 | 4h |
| 2 | 两套system prompt并存 | identity.py (V7) vs prompts.py (V6) 内容高度一致但不完全相同。修改人格时两边都要改。 | 1h |
| 3 | LLM错误判断脆弱 | `result.startswith("（")` 可能误判。 | 1h |

### 中优先级

| # | 差距 | 说明 | 工时 |
|---|------|------|------|
| 4 | reconnect时YukiState重置 | 每次重连清空精力/欲望/活跃度, 长时间状态丢失。 | 1h |
| 5 | 两套RAG并存 | YukiMemory (V7) + MemoryRAG (V6) 功能重叠。 | 2h |
| 6 | 小女仆未接入Bus action | Mind解析DELEGATE_TO_MAID但Bus不执行, 依赖V6队列。 | 2h |

### 低优先级

| # | 差距 | 说明 | 工时 |
|---|------|------|------|
| 7 | 无连接池 | requests每次新建TCP, 无aiohttp Session复用。 | 2h |
| 8 | 私聊预过滤不完整 | 私聊不走群活跃度逻辑, desire/energy可能不准。 | 0.5h |

---

## 四、V7 架构优势 (已实现)

1. **新平台只需写 PlatformPlugin** — 不碰核心逻辑
2. **Mind 完全平台无关** — 可复用于 web/voice/Discord
3. **ContextBus 统一入口** — 所有平台走同一条 event→mind→send 路径
4. **身份系统数据驱动** — identities.yaml 可热改, 不用改代码
5. **插件加载器** — plugins.yaml 声明式配置, 动态导入
6. **预过滤节省LLM调用** — desire>=80/<=30 直接跳过LLM判断
7. **后台任务由Bus管理** — 错误隔离, 不会静默挂掉

---

## 五、开发计划

### Phase 3a: 统一后台任务路径 (优先级高)

将 engine.py 的日记/破冰/小女仆改为经过 Mind 处理, 消除 V6 残留。

### Phase 3b: Prompt/RAG 去重 (优先级中)

合并两套 system prompt 和两套 RAG 为单一实现。

### Phase 3c: 稳定性优化 (优先级低)

LLM 错误判断加固, 连接池, reconnect 状态保持。
