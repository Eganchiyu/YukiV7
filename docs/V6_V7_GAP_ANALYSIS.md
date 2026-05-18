# Yuki V7 开发计划

> 对照 V6 功能完整性，梳理 V7 缺失项和优化方向  
> 更新时间：2026-05-18

## 一、V6 vs V7 功能对比总表

| 子系统 | V6 位置 | V7 位置 | 状态 | 说明 |
|--------|---------|---------|------|------|
| **消息缓冲/防抖** | main.py manage_buffer | qq_plugin.py process_buffered | ✅ 已补回 | 本次修复 |
| **Bot 白名单** | main.py (user_id==1390249127) | qq_plugin.py + config.yaml | ✅ 已补回 | 本次修复 |
| **Bot 召唤打折** | engine.py decide_to_reply | bus.py _decide_to_reply | ✅ 已补回 | V7 跳过回复，V6 欲望×0.7 |
| **说话风格/自称** | core/prompts.py | identity.py + identities.yaml | ✅ 已补回 | 恢复 V6 亲昵温柔 + Yuki/人家交替 |
| **群聊拉扯感** | prompts.py get_yuki_setting_group | identities.yaml qq context | ✅ 已补回 | 恢复布局+拉扯详细指令 |
| **日记系统** | engine.py idle_diary_checker | bus.py _background_diary_checker | ✅ 已迁移 | V7 增加了 max_length 强制触发 |
| **历史管理** | history_manager.py | history.py | ✅ 已迁移 | 几乎一致 |
| **记忆/RAG** | modules/memory/rag.py | memory.py | ✅ 已迁移 | V7 增加优雅降级 |
| **表情包管理** | modules/stickers/manager.py | sticker_manager.py | ✅ 已迁移 | LLM 调用方式不同 |
| **表情包视觉处理** | modules/vision/processor.py | processor.py | ✅ 已迁移 | LLM 调用方式不同 |
| **身份系统** | core/prompts.py | identity.py + YAML | ✅ 重构 | 更结构化，可配置 |
| **群聊开关(/开启/关闭)** | main.py inline | qq_plugin.py translate_in | ⚠️ 部分迁移 | 缺持久化、回复消息、buffer 清空 |
| **破冰主动唤醒** | engine.py ice_break_monitor | bus.py _background_ice_breaker | ❌ 未迁移 | 仅占位 sleep(600) |
| **小女仆集成** | engine.py maid_worker + queue | maid.py (独立) | ⚠️ 简化 | worker 未接入 bus，无任务队列 |
| **决策引擎** | engine.py decide_to_reply (LLM) | mind.py _decide (纯数学) | ⚠️ 简化 | V6 用 LLM 判断，V7 纯阈值 |
| **表情包正反馈(RLHF)** | main.py manage_buffer | — | ❌ 未迁移 | last_sent_meme + 反馈词检测 |
| **活跃度衰减** | brain.py decay_heartbeat (全局) | mind.py ActivityTracker (按需) | ⚠️ 简化 | V7 不活跃群永远不衰减 |
| **消息时间追踪** | brain.py last_message_time | bus.py _last_message_time | ✅ 已迁移 | |
| **AsyncIO 锁** | brain.py self.lock | — | ❌ 缺失 | 能量/活跃度计算无并发保护 |
| **LLM 传输层** | aiohttp (异步连接池) | requests (同步 to_thread) | ⚠️ 降级 | 无连接复用，性能下降 |
| **熔断器** | FallbackProvider (circuit-breaker) | robust_chat (字符串判断) | ⚠️ 简化 | V7 用 startswith("（") 判断错误 |
| **群状态持久化** | data/group_state.json | — | ❌ 缺失 | 重启后群开关状态丢失 |
| **帮助指令** | main.py /help → 发送图片 | — | ❌ 缺失 | |
| **System Prompt 启动同步** | prompts.py sync_system_prompts | — | ❌ 缺失 | 旧 prompt 可能覆盖新 prompt |

## 二、已知问题

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | LLM 错误判断脆弱 | 中 | `result.startswith("（")` 可能误判（LLM 回复以括号开头） |
| 2 | 无连接池 | 低 | 每次 LLM 调用新建 TCP 连接，延迟略高 |
| 3 | 群开关状态不持久 | 中 | 重启后所有群恢复默认状态 |
| 4 | 破冰功能缺失 | 中 | 群长时间沉默时不会主动开口 |
| 5 | 小女仆未接入 | 低 | delegate 指令被解析但不执行 |
| 6 | 活跃度衰减不完整 | 低 | 不活跃群的活跃度永远不衰减 |
| 7 | KEEP_LAST_DIALOGUE 硬编码 | 低 | mind.py 写死 10，应读 config |

## 三、开发计划

### Phase 3a: 功能补全（优先级高）

| # | 任务 | 涉及文件 | 预估工时 | 说明 |
|---|------|----------|----------|------|
| 3a-1 | 群聊开关持久化 | qq_plugin.py, config | 1h | /开启 /关闭 → 保存到 data/group_state.json，启动时加载 |
| 3a-2 | 群聊开关回复消息 | qq_plugin.py | 0.5h | /关闭 → "Yuki已进入休眠模式"，/开启 → "Yuki重新上线"，非主人 → 拒绝 |
| 3a-3 | 群聊开关清空 buffer | qq_plugin.py | 0.5h | /关闭时 cancel buffer_tasks + pop_buffer |
| 3a-4 | 破冰功能移植 | bus.py, identity.py, qq_plugin.py | 3h | 移植 V6 ice_break_monitor + build_ice_break_prompt |
| 3a-5 | KEEP_LAST_DIALOGUE 读 config | mind.py | 5min | `keep = 10` → `keep = cfg.KEEP_LAST_DIALOGUE` |

### Phase 3b: 稳定性优化（优先级中）

| # | 任务 | 涉及文件 | 预估工时 | 说明 |
|---|------|----------|----------|------|
| 3b-1 | LLM 错误判断加固 | llm.py | 1h | 用 try/except + HTTP status 判断，不依赖字符串前缀 |
| 3b-2 | aiohttp 连接池 | llm.py | 2h | 用 aiohttp.ClientSession 替代 requests，复用连接 |
| 3b-3 | AsyncIO 锁 | mind.py | 0.5h | energy/activity 计算加 Lock |
| 3b-4 | 小女仆接入 bus | maid.py, bus.py | 3h | 任务队列 → 执行 → 结果注入 history → 触发回复 |

### Phase 3c: 行为优化（优先级低）

| # | 任务 | 涉及文件 | 预估工时 | 说明 |
|---|------|----------|----------|------|
| 3c-1 | 活跃度衰减完善 | mind.py | 1h | 后台定时衰减所有群，不只按需 |
| 3c-2 | 表情包正反馈(RLHF) | qq_plugin.py, sticker_manager.py | 2h | V6 的 last_sent_meme + 反馈词检测 |
| 3c-3 | 帮助指令 | qq_plugin.py | 0.5h | /help → 发送帮助图片 |
| 3c-4 | System Prompt 启动同步 | main.py | 0.5h | 启动时覆写 history 中的 system prompt |
| 3c-5 | 决策引擎 LLM 回归（可选） | mind.py | 2h | V6 用 LLM 做 YES/NO 判断，V7 可选开启 |

## 四、当前已完成的修复（2026-05-18）

| 修复项 | 文件 | 说明 |
|--------|------|------|
| meta.py import 修复 | plugins/platforms/qq_plugin/meta.py | `from network.ws_connection` → `from .network` |
| cache.py import 修复 | plugins/platforms/qq_plugin/cache.py | `from config` → `from yuki_core.config`，utils.logger → logging |
| Config 缺失属性 | yuki_core/config.py | 补 CACHE_DIR, CACHE_FILE, MAX_RETRIES, MAX_CONCURRENT_MEME 等 |
| 消息缓冲/防抖 | plugins/platforms/qq_pluginqq_plugin.py | 完整移植 V6 的 manage_buffer + main_process 逻辑 |
| Bot 白名单 | configs/config.yaml + config.py + qq_plugin.py | bot_whitelist 配置 + 过滤逻辑 |
| Bot 召唤打折 | yuki_core/bus.py | _decide_to_reply：仅 BOT @Yuki → 跳过回复 |
| 说话风格恢复 | identity.py + identities.yaml | 自称 Yuki/人家交替，性格亲昵温柔 |
| 群聊拉扯感恢复 | identities.yaml | 补回 V6 的布局+拉扯+多媒体发送指令 |
