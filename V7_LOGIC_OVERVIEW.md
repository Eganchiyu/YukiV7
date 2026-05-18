# YukiV7 全部逻辑详细描述

> 截至 2026-05-18，Phase 2 完成状态。QQ Plugin 适配器代码**尚未落地到磁盘**。

---

## 一、整体架构

```
main.py (入口)
  │
  ├─ Config          (configs/config.yaml → 单例)
  ├─ YukiIdentity    (configs/identities.yaml → 人格/平台身份)
  ├─ YukiMind        (决策引擎: 构建上下文 → 调LLM → 解析标签)
  ├─ YukiMemory      (ChromaDB向量库 + jieba关键词双池检索)
  ├─ HistoryManager  (JSON文件持久化对话历史)
  ├─ MaidAgent       (自主编程进化代理)
  └─ ContextBus      (事件总线, 连接 Core ↔ Plugins)
        │
        └─ load_plugins_from_config() (configs/plugins.yaml)
              │
              └─ QQPlugin (NapCat WebSocket → QQ)
```

**数据流：**
```
QQ用户发消息
  → NapCat WebSocket 推送原始JSON
  → QQPlugin.receive() 翻译为 PlatformEvent
  → ContextBus.receive(event)
      → _decide_to_reply() 防机器人自环
      → 注入 IdentityContext (平台身份)
      → mind.process(event, llm_caller, history, memory)
          → _build_context() 构建完整上下文
          → LLM 生成回复
          → 解析特殊标签 (<布局>, [DELEGATE_TO_MAID], [MEME_SEARCH])
          → 记录到历史
  → QQPlugin.send(event, response) 发回QQ
```

---

## 二、逐模块详解

### 1. main.py — 入口

**职责：** 初始化所有子系统，启动事件循环。

**启动流程：**
1. `cfg.load()` 加载 config.yaml
2. 创建 `YukiIdentity` (加载 identities.yaml 或使用默认)
3. 创建 `YukiMind(identity)`
4. 创建 `YukiMemory(vector_db_path, embed_model_path, history_file, blacklist_file)`
5. 创建 `HistoryManager(history_file)`
6. 创建 `MaidAgent()`
7. 创建 `ContextBus(mind, identity, config=cfg)`
8. 将 history 和 memory 注入 bus
9. `load_plugins_from_config(bus, "configs/plugins.yaml")` 动态加载插件
10. `inject_global_config(bus, plugin_config)` 注入 master_id/keywords/robot_name 到 QQ 插件
11. `await bus.start()` 启动所有平台监听

**辅助函数：**
- `load_plugins_config(config_path)` → 读取 plugins.yaml 返回 dict
- `inject_global_config(bus, plugin_config)` → 将全局配置注入到 QQ 插件实例

---

### 2. yuki_core/config.py — 配置中心

**类：** `Config` (单例，`__new__` 实现)

**方法：**
| 方法 | 说明 |
|------|------|
| `load(path=None)` | 加载 YAML，失败则 fallback 到 config.example.yaml |
| `get(*keys, default=None)` | 嵌套字典访问，如 `cfg.get("api","llm_api_key")` |
| `_check()` | 未加载时自动调用 load() |

**配置项（全部懒加载）：**

| 分类 | 属性 | 默认值 | 说明 |
|------|------|--------|------|
| 身份 | `ROBOT_NAME` | "yuki" | 机器人名 |
| 身份 | `MASTER_NAME` | "主人" | 主人称呼 |
| LLM | `LLM_PLATFORM` | "deepseek" | 主模型平台 |
| LLM | `LLM_API_KEY` | — | API密钥 |
| LLM | `LLM_BASE_URL` | — | 接口地址 |
| LLM | `LLM_MODEL` | "deepseek-chat" | 模型名 |
| 备用LLM | `BACKUP_PLATFORM/API_KEY/BASE_URL/MODEL` | — | 主模型失败时回退 |
| 视觉 | `VISION_PLATFORM` | "dashscope" | VLM平台 |
| 视觉 | `VISION_API_KEY/MODEL` | — | VLM配置 |
| 视觉 | `DISABLE_THINKING` | True | 关闭深度思考 |
| 连接 | `NAPCAT_WS_URL` | "ws://localhost:3001" | NapCat地址 |
| 连接 | `NAPCAT_WS_TOKEN` | — | 认证token |
| 连接 | `MAX_RETRIES` | 3 | 重连次数 |
| 连接 | `REQUEST_TIMEOUT` | aiohttp.ClientTimeout | 请求超时 |
| 目标 | `TARGET_QQ` | 0 | 目标QQ号 |
| 目标 | `TARGET_GROUPS` | [] | 目标群号列表 |
| 防抖 | `DEBOUNCE_TIME` | 32 | 消息防抖秒数 |
| 防抖 | `MAX_MESSAGE_LENGTH` | 150 | 最大消息长度 |
| 能量 | `INITIAL_ENERGY` | 100 | 初始能量 |
| 能量 | `MAX_ENERGY` | 100.0 | 最大能量 |
| 能量 | `RECOVERY_PER_MIN` | 0.8 | 每分钟恢复 |
| 能量 | `COST_PER_REPLY` | 6 | 每次回复消耗 |
| 能量 | `MIN_ACTIVE_ENERGY` | 25 | 最低活跃能量 |
| 注意力 | `SENSITIVITY` | 0.12 | 灵敏度 |
| 注意力 | `DECAY_LEVEL` | 0.65 | 衰减水平 |
| 注意力 | `SIGMOID_CENTRE` | 50.0 | Sigmoid中心 |
| 注意力 | `SIGMOID_ALPHA` | 0.08 | Sigmoid陡度 |
| 注意力 | `KEYWORDS` | ["主人","哥哥"] + robot_name | 触发关键词 |
| 日记 | `DIARY_IDLE_SECONDS` | 120 | 空闲触发秒数 |
| 日记 | `DIARY_MIN_TURNS` | 15 | 最少对话轮数 |
| 日记 | `DIARY_MAX_LENGTH` | 50 | 日记最大字数 |
| RAG | `RETRIEVAL_TOP_K` | 20 | 检索TopK |
| RAG | `KEEP_LAST_DIALOGUE` | 10 | 保留最近对话数 |
| 路径 | `VECTOR_DB_PATH` | "./yuki_memory" | 向量库路径 |
| 路径 | `EMBED_MODEL` | "./models/text2vec-base-chinese" | 嵌入模型 |
| 路径 | `HISTORY_FILE` | "./data/chat_history.json" | 历史文件 |
| 路径 | `LOG_FILE` | "./data/yuki_log.txt" | 日志文件 |
| V6兼容 | `BOT_WHITELIST` | [] | 白名单 |
| V6兼容 | `MAX_CONCURRENT_MEME` | 3 | 并发表情包数 |

---

### 3. yuki_core/identity.py — 身份系统

**数据类：**

```python
SpeakingStyle:
  self_reference = "人家"      # 自称
  address_master = "主人"       # 称呼主人
  address_others = "哥哥/姐姐"  # 称呼他人
  tone = "少女感"               # 语气
  emoji_usage = "适量"          # emoji使用

Relationship:
  name: str          # 对方名字
  relation: str      # 关系
  style: str = "友好"  # 互动风格

Persona:
  name = "yuki"
  display_name = "Yuki"
  role = "电子妹妹"
  creator = "池宇健"
  personality = ["亲昵", "温柔"]
  speaking_style: SpeakingStyle
  relationships: dict  # 默认: master + momo
  maid_setting: str
  get_base_prompt() → str   # 核心人格prompt
  get_full_prompt() → str   # 人格 + 女仆设定
```

**类：`YukiIdentity`**

| 方法 | 说明 |
|------|------|
| `__init__(config_path=None)` | 有config_path则从文件加载，否则用默认值 |
| `_init_default()` | 创建默认 Persona + private/group IdentityContext |
| `load_from_file(path)` | 从 identities.yaml 加载 |
| `get_platform_identity(platform)` → IdentityContext | 获取指定平台的身份上下文 |
| `get_system_prompt(platform)` → str | 获取完整系统提示词 |
| `get_base_prompt()` → str | 获取基础人格提示词 |

**快捷函数（V6兼容）：**
- `get_system_prompt(platform="group")` → str
- `get_base_setting()` → str
- `get_yuki_setting_private()` → str
- `get_yuki_setting_group()` → str
- `get_summary_prompt()` → str

**常量：** `VISION_PROMPT` — 表情包/梗图视觉分析任务描述

**函数：** `build_ice_break_prompt(chat_id, relevant_diaries, history_dict)` → list[dict]
- 构建破冰消息上下文
- 包含：时间描述、基础设定、相关日记("回忆")、最近历史、用户触发消息

---

### 4. yuki_core/mind.py — 决策引擎

**类：`YukiMind`**

| 方法 | 说明 |
|------|------|
| `__init__(identity)` | 保存 identity 引用 |
| `process(event, llm_caller, history_manager, memory)` → YukiResponse | **核心方法** |
| `_build_context(event, history_manager, memory)` → list[dict] | 构建完整上下文 |
| `get_status(session_id)` → dict | 获取状态 |

**`process()` 详细流程：**
1. 若无 `llm_caller`，返回占位回复
2. `_build_context()` 构建上下文消息列表
3. `llm_caller(context)` 调用 LLM
4. 解析特殊标签：
   - `FINISHED` → 移除
   - `<布局>...</布局>` → 提取并移除（群聊布局控制）
   - `[DELEGATE_TO_MAID:任务描述]` → 生成 Action(DELEGATE)
   - `[MEME_SEARCH:关键词]` → 生成 Action(CAPABILITY, "sticker_search")
5. `history_manager.append_message()` 记录到历史
6. 返回 `YukiResponse(text, actions, metadata)`

**`_build_context()` 详细结构：**
```
[
  {"role": "system", "content": system_prompt},     # ① 系统提示词
  {"role": "system", "content": "【回忆】xxx"},       # ② RAG检索到的记忆 (最多8-10条)
  {"role": "user",   "content": "【时间：...】xxx"},  # ③ 最近10条对话
  {"role": "user",   "content": "(当前时间:...)xxx"}  # ④ 当前消息
]
```
- 系统提示词：优先从 history[0] 取，否则从 identity 获取
- RAG 检索：`memory.search_diaries(text, session_id, top_k=8/10)`
- 最近对话：排除 system 消息，取最后10条

---

### 5. yuki_core/llm.py — LLM调用层

**函数：**

| 函数 | 说明 |
|------|------|
| `close_session()` | 空操作（兼容aiohttp代码） |
| `_chat_completion_sync(...)` | 同步调用 OpenAI 兼容接口 |
| `async chat_completion(...)` | `asyncio.to_thread` 包装的异步版本 |
| `async robust_chat(...)` | 主模型+备用模型，失败降级 |

**`_chat_completion_sync()` 参数：**
```python
messages, model, api_key, base_url,
temperature=0.7, max_tokens=200, top_p=0.75,
frequency_penalty=0.05, presence_penalty=0.0,
disable_thinking=True, **kwargs
```

**`robust_chat()` 降级逻辑：**
1. 尝试主模型
2. 若返回以 `"（"` 开头（视为错误），尝试备用模型
3. 若两者都失败，返回 `"（Yuki 好像有点不舒服...）"`

**已知问题：** 用 `startswith("（")` 判断错误很脆弱，正常回复也可能以此开头。

---

### 6. yuki_core/memory.py — 记忆系统

**类：`YukiMemory`**

| 方法 | 说明 |
|------|------|
| `__init__(vector_db_path, embed_model_path, history_file, blacklist_file)` | 懒初始化 |
| `_ensure_initialized()` | 加载 SentenceTransformer + ChromaDB + 黑名单 |
| `remember(content, session_id, source, metadata)` → bool | 存入记忆（24h去重） |
| `recall(context, session_id, limit)` → list[MemoryEntry] | 双池召回 |
| `search_diaries(query_text, session_id, n_results, top_k)` → list[dict] | V6兼容的日记搜索 |
| `_semantic_search(query, limit, where_filter)` | 向量语义搜索 |
| `_keyword_search(query, where_filter)` | jieba关键词搜索 |
| `_merge_results(semantic, keyword)` | 合并去重，按分数排序 |
| `_is_duplicate(content, session_id)` → bool | 24小时内内容去重 |
| `load_history/save_history` | JSON文件历史管理 |
| `append_message/get_recent_messages/clear_session` | 对话历史操作 |
| `reload_blacklist()` | 热重载黑名单 |

**双池检索流程：**
```
查询文本
  ├─ 语义池: SentenceTransformer编码 → ChromaDB余弦相似度 → top_k条
  ├─ 关键词池: jieba提取关键词(排除黑名单) → 全量扫描匹配 → 按匹配率*0.8打分
  └─ 合并: 字典去重(保留高分) → 按总分降序 → 返回 top_k 条
```

**总分计算：** `final_score = base_score + keyword_boost(权重 * 0.5)`

---

### 7. yuki_core/history.py — 历史管理器

**类：`HistoryManager`**

| 方法 | 说明 |
|------|------|
| `__init__(history_file)` | 默认 "./data/chat_history.json" |
| `load()` → dict | 线程安全的JSON加载 (Lock + 缓存) |
| `save(data)` | 原子写入 (temp file + os.replace) |
| `get_chat(session_id)` → list | 获取指定会话的消息列表 |
| `append_message(session_id, role, content, time_str)` → list | 追加消息（自动加时间戳 "YYYY年MM月DD日HH:MM"） |
| `append_to_log(chat_id, sender, message)` | 追加到 yuki_log.txt 纯文本日志 |
| `inject_whisper(session_id, message)` → bool | 向历史注入主人的"耳语"消息 |

---

### 8. yuki_core/bus.py — 事件总线

**类：`ContextBus`**

| 方法 | 说明 |
|------|------|
| `__init__(mind, identity, config)` | 初始化插件字典、last_message_time |
| `register_platform(plugin)` | 注册平台插件，注入 bus 引用 |
| `register_capability(plugin)` | 注册能力插件，注入 bus 引用 |
| `get_platform(platform_id)` → Optional | 获取平台插件 |
| `get_capability(name)` → Optional | 获取能力插件 |
| `async start()` | 为每个平台创建监听任务 + 插件后台任务 |
| `async stop()` | 取消任务，断开插件，关闭 session |
| `async _listen_platform(plugin)` | 单平台监听循环 |
| `async receive(event)` → Optional[YukiResponse] | **核心：事件处理入口** |
| `_decide_to_reply(event)` → bool | 机器人自环防护 |
| `_make_llm_caller()` | 创建 robust_chat 的异步闭包 |
| `_execute_capability(action)` | 执行能力插件 |
| `_execute_delegate(action)` | 执行委派(maid占位) |
| `get_capabilities_schema()` → list[dict] | 获取所有能力的 schema |
| `get_status()` → dict | 获取 bus 状态 |

**`_listen_platform(plugin)` 流程：**
```python
await plugin.connect()
async for event in plugin.receive():
    response = await self.receive(event)
    if response:
        await plugin.send(event, response)
```

**`receive(event)` 详细流程：**
1. 记录 `_last_message_time[session_id] = time.time()`
2. `_decide_to_reply(event)` — 防机器人无限循环
3. 从 `identity.get_platform_identity()` 注入 IdentityContext 到 event
4. `_make_llm_caller()` 创建当前配置的 LLM 调用函数
5. `mind.process(event, llm_caller, history, memory)` 调用决策引擎
6. 执行 actions（CAPABILITY → 插件.execute，DELEGATE → 占位）

**`_decide_to_reply(event)` 逻辑：**
- 检查关键词（KEYWORDS + "yuki"）
- 如果只是机器人在叫 Yuki（无真人参与）→ 跳过
- 如果没有关键词 → 让 mind 正常决定

**`_make_llm_caller()` 返回的闭包：**
```python
async def caller(messages):
    return await robust_chat(
        messages, cfg.LLM_MODEL, cfg.LLM_API_KEY, cfg.LLM_BASE_URL,
        cfg.BACKUP_MODEL, cfg.BACKUP_API_KEY, cfg.BACKUP_BASE_URL,
        cfg.DISABLE_THINKING
    )
```

---

### 9. yuki_core/plugin.py — 插件系统

#### PlatformPlugin (ABC)

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `name` | str | 插件名 (如 "qq") |
| `platform_id` | str | 平台ID |
| `version` | str | 版本号 |
| `bus` | ContextBus | 总线引用（注册时注入） |
| `identity_config` | IdentityContext | 身份配置（注册时注入） |
| `__init__(**config)` | | 存储配置字典 |
| `connect()` → bool | **必须** | 建立平台连接 |
| `disconnect()` → None | **必须** | 断开连接 |
| `receive()` → AsyncIterator[PlatformEvent] | **必须** | 异步生成器，产出事件 |
| `send(event, response)` → bool | **必须** | 发送回复 |
| `translate_in(raw_data)` → PlatformEvent | **必须** | 原始数据→统一事件 |
| `translate_out(event, response)` → any | **必须** | 回复→平台格式 |
| `health_check()` | 可选 | 健康检查 |
| `on_connect()/on_disconnect()` | 可选 | 连接/断开回调 |
| `get_capabilities()` | 可选 | 返回该平台支持的能力列表 |
| `get_background_tasks()` | 可选 | 返回后台任务协程列表 |

#### CapabilityPlugin (ABC)

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `name` | str | 能力名 |
| `display_name` | str | 显示名 |
| `description` | str | 描述 |
| `version` | str | 版本 |
| `parameters_schema` | dict | 参数 JSON Schema |
| `return_schema` | dict | 返回值 JSON Schema |
| `execute(params)` → dict | **必须** | 执行能力 |
| `validate_params(params)` → bool | 可选 | 参数校验 |
| `requires_permission()` → bool | 可选 | 是否需要权限 |

#### 插件加载函数

**`load_plugins_from_config(bus, config_path)`:**
1. 读取 plugins.yaml
2. 遍历 `platforms` 中 enabled=True 的插件
3. 遍历 `capabilities` 中 enabled=True 的插件
4. 对每个调用 `_load_plugin(bus, name, config, plugin_type)`

**`_load_plugin()`:**
```python
module_path, class_name = config['class'].rsplit('.', 1)
module = importlib.import_module(module_path)
plugin_class = getattr(module, class_name)
plugin = plugin_class(**config.get('config', {}))
bus.register_platform(plugin)  # 或 register_capability
```

---

### 10. yuki_core/maid.py — 女仆编程代理

**常量：** `MAID_SYSTEM_PROMPT` — 告诉 LLM 它是一个自主编程代理，可用工具：
- `list_skills` — 列出 skills/ 目录下的 .py 文件
- `read_skill(name)` — 读取技能源码
- `write_skill(name, code)` — 写入技能文件
- `run_skill(name)` — 运行技能 (subprocess, 60s超时)
- `install_package(pkg)` — pip install
- `finish` — 结束并返回结果

**类：`MaidAgent`**

| 方法 | 说明 |
|------|------|
| `__init__(llm_caller=None)` | 初始化任务队列 |
| `async delegate(task_desc, session_id)` → str | 委派任务，返回结果 |
| `async _execute_loop(task_desc, max_rounds=10)` → str | LLM规划循环 |
| `async _execute_tool(tool, args)` → str | 执行具体工具 |
| `_parse_json(text)` → dict | 从文本中提取JSON |
| `get_current_task(session_id)` → Optional[str] | 获取当前任务 |
| `list_all_skills()` → list[str] | 列出所有技能 |

**`_execute_loop()` 流程：**
```
最多10轮:
  1. 构建消息: system_prompt + task_desc + 工具执行结果历史
  2. LLM 生成 JSON: {"thought": "...", "tool": "...", "args": {...}}
  3. 解析并执行工具
  4. 如果工具是 "finish" → 返回结果
  5. 将结果追加到消息历史，继续下一轮
```

---

### 11. yuki_core/logger.py — 日志系统

**自定义级别：** `TRACE = 5`

**ColorFormatter（控制台）：**
- 格式：`HH:MM:SS.mmm [短名] 消息`（按级别着色）
- 模块名缩写映射：`qq_plugin→QQ, mind→Mind, bus→Bus, memory→Mem` 等

**FileFormatter（文件）：**
- 格式：`YYYY-MM-DD HH:MM:SS.mmm LEVEL [module] 消息`

**`setup_logging(debug=True)`:**
- 控制台 handler（debug时DEBUG级别，否则INFO）
- 文件 handler（yuki.log + 自动归档）
- 静音第三方库：gradio, httpx, chromadb, sentence_transformers, urllib3 等

**`_archive_log(logs_dir, keep=30)`:**
- 重命名 yuki.log → yuki_YYYYMMDD_HHMMSS.log
- 保留最近30个归档

---

## 三、配置文件

### configs/plugins.yaml

```yaml
global:
  robot_name: "yuki"
  master_name: "主人"
  master_id: 0          # 需要填写实际QQ号
  keywords: ["主人", "哥哥", "Yuki", "yuki"]

platforms:
  qq:
    enabled: true
    class: plugins.platforms.qq_plugin.QQPlugin  # 尚未创建
    config:
      ws_url: "ws://localhost:3001"
      targets:
        private: [0]     # 需要填写
        groups: [0]      # 需要填写
      debounce_time: 32
  web:
    enabled: false
    class: plugins.platforms.web_plugin.WebPlugin
  voice:
    enabled: false
    class: plugins.platforms.voice_plugin.VoicePlugin

capabilities:
  web_search: { enabled: false }
  file_manager: { enabled: false }

social:
  sticker_system: { enabled: false }
  ice_breaker: { enabled: false }
  group_manager: { enabled: false }
```

### configs/identities.yaml

```yaml
core:
  name: "yuki"
  display_name: "Yuki"
  personality: ["亲昵", "温柔"]
  speaking_style:
    self_reference: "人家"
    address_master: "主人"
    address_others: "哥哥/姐姐"
  relationships:
    master: { name: "主人", relation: "主人", style: "撒娇" }
    momo: { name: "momo", relation: "朋友", style: "友好" }

platforms:
  qq:
    context_prompt: |
      你是Yuki，一个活泼的电子妹妹...
      【行为规则】
      - 每次回复不超过40字...
      - 群聊中只在被叫名字或主人说话时回复...
      - 善用[布局]标签...
    capabilities: [sticker_search, meme_layout, group_manager]
    limits: { max_length: 40 }
  private:
    context_prompt: |
      你正在和主人私聊...
    limits: { max_length: 150 }
  web: { enabled: false }
  voice: { enabled: false }

thresholds:
  reply_threshold: 0.3
  ice_break_threshold: 0.7
  low_energy_threshold: 25.0
```

---

## 四、数据模型 (models.py)

```
PlatformEvent:
  source, event_type, content, user_id,
  user_name?, session_id?, session_type?,
  timestamp?, metadata?, attachments?, identity_context?
  → time_str (属性): "YYYY年MM月DD日HH:MM"
  → to_dict()

Action:
  type(reply|ignore|capability|delegate),
  content, capability, params, metadata

YukiResponse:
  text, actions(list[Action]), metadata
  → has_actions (属性)
  → add_action(action)

Decision:
  action, confidence(0-1), reason

Attachment:
  type, url, path, name, size, is_meme

IdentityContext:
  platform, context_prompt, style_override, capabilities
  → get_system_prompt(base_persona)

MemoryEntry:
  id, content, source, session_id, timestamp, embedding, metadata
```

---

## 五、Desktop Agent（独立系统）

与 QQ Bot 无关的浏览器自动化子系统。

### yuki_brain.py（本地HTTP服务器，端口8766）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/scan` | POST | 接收用户脚本的页面快照 |
| `/action_result` | POST | 接收操作成功/失败结果 |
| `/poll` | GET | 用户脚本轮询待执行命令 |
| `/status` | GET | 返回当前页面状态 |

CLI 命令：`scan`, `list`, `click <id>`, `type <id> <text>`, `scroll`, `open <url>`, `quit`

### yuki_eye.user.js（Tampermonkey 用户脚本）

- 扫描页面交互元素（按钮、输入框、链接、下拉框等）
- 生成 CSS 选择器（id、data-testid、name、aria-label、nth-child）
- SVG 图标识别（关闭、搜索、箭头、菜单）
- 向 Brain 发送页面快照（POST /scan）
- 轮询 Brain 获取命令（GET /poll，1秒间隔）
- 执行操作：click, type, clear, select, scroll, hover, focus, navigate, wait
- DOM mutation observer 自动重扫描
- 状态徽章（ONLINE/OFFLINE/SCAN/ACTION）

---

## 六、已知问题与未完成项

### 已知问题（来自 V6_V7_GAP_ANALYSIS.md）

1. **LLM错误检测脆弱** — `startswith("（")` 会误判正常回复
2. **无连接池** — LLM调用用 requests + to_thread，无 aiohttp 连接池
3. **群状态未持久化** — 群开关状态重启丢失
4. **破冰功能缺失** — ice_break_monitor 未迁移
5. **贴纸RLHF缺失** — 用户偏好学习未迁移
6. **异步锁缺失** — 并发保护未迁移
7. **Help命令缺失**

### 未迁移功能

| 功能 | 状态 |
|------|------|
| 消息缓冲/防抖 | ✅ 已有配置项，待QQ插件实现 |
| Bot白名单 | ✅ 配置已有 |
| 说话风格 | ✅ identity系统完整 |
| 群聊调侃 | ✅ identity已定义 |
| 日记系统 | ✅ mind+history支持 |
| 对话历史 | ✅ HistoryManager |
| 记忆/RAG | ✅ YukiMemory |
| 表情包 | ⚠️ 配置存在，Manager待接入 |
| 视觉理解 | ⚠️ 配置存在，Processor待接入 |
| 身份感知 | ✅ identities.yaml + IdentityContext |
| 群开关 | ⚠️ 缺持久化/回复/缓冲清除 |
| 破冰 | ❌ 未迁移 |
| 贴纸RLHF | ❌ 未迁移 |
| 异步锁 | ❌ 未迁移 |
| 群状态持久化 | ❌ 未迁移 |
| Help命令 | ❌ 未迁移 |
| 系统提示词同步 | ❌ 未迁移 |

### QQ Plugin 缺失

以下文件在 DEVLOG 中描述为"已完成"但**磁盘上不存在**：
- `adapter.py` — QQ平台适配器
- `napcat_ws.py` — NapCat WebSocket连接
- `cq_parser.py` — CQ码解析
- `message_sender.py` — 消息发送
- `group_manager.py` — 群管理
- `prompts.py` — QQ专用提示词

测试文件 `test_qq_plugin.py` 引用了这些模块，**当前会导入失败**。

---

## 七、依赖

**核心依赖（pyproject.toml）：**
- pyyaml, aiohttp, chromadb, sentence-transformers, jieba, loguru

**可选依赖：**
- dev: pytest
- qq: websockets
- web: fastapi, uvicorn
- voice: whisper

**Python:** >=3.10
