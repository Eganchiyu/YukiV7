# Yuki Agent 开发日志

> **最后更新**: 2026-05-17
> **当前进度**: Phase 2 核心完成，待补表情包/破冰/帮助指令
> **项目路径**: `D:\Projects\YukiV7`
> **源项目**: `D:\Projects\YukiV6` (YukiV6 QQ Bot)

---

## 一、项目概述

### 1.1 项目目标

将 Yuki 从一个 **QQ 群聊机器人** (YukiV6) 重构为一个 **多平台 AI Agent** (YukiV7)，核心变化：

1. **人格独立** - 核心人格不依赖任何平台
2. **插件化架构** - 平台和功能都以插件形式接入
3. **多平台身份感知** - Yuki 能意识到自己在不同平台的不同身份
4. **可扩展性** - 便于未来添加语音、网页、智能家居等能力

### 1.2 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    Yuki Agent Core                       │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Identity │  │ Memory  │  │  Mind    │  │  Maid    │ │
│  │ 身份内核 │  │ 记忆系统 │  │ 决策引擎 │  │ 进化系统 │ │
│  └────┬────┘  └────┬────┘  └────┬─────┘  └────┬─────┘ │
│       └────────────┴────────────┴──────────────┘       │
│              ┌──────────▼──────────┐                   │
│              │   Context Bus       │                   │
│              └──────────┬──────────┘                   │
└─────────────────────────┼───────────────────────────────┘
                          │
    ┌─────────────────────┼─────────────────────┐
    ▼                     ▼                     ▼
┌────────┐          ┌────────┐          ┌────────┐
│QQ Plugin│          │Web Plugin│        │Voice   │
└────────┘          └────────┘          └────────┘
```

---

## 二、Phase 0 成果（文档与准备）

### 2.1 创建的文件

| 文件 | 说明 |
|------|------|
| `docs/ARCHITECTURE.md` | 完整架构设计文档 (33KB) |
| `docs/EVENT_FORMAT.md` | PlatformEvent 统一事件格式定义 |
| `docs/PLUGIN_SPEC.md` | Plugin 接口规范 (PlatformPlugin/CapabilityPlugin) |
| `docs/MIGRATION_CHECKLIST.md` | YukiV6 → YukiV7 代码迁移清单 |
| `configs/identities.yaml` | 多平台身份配置 |
| `configs/plugins.yaml` | 插件配置 |
| `configs/config.example.yaml` | 主配置模板 |
| `README.md` | 项目说明 |
| `pyproject.toml` | 项目依赖 |
| `.gitignore` | Git 忽略规则 |

### 2.2 核心设计规范

#### PlatformEvent（统一事件格式）

```python
@dataclass
class PlatformEvent:
    source: str           # "qq", "web", "voice"
    event_type: str       # "message", "image", "file"
    content: str          # 消息内容
    user_id: str          # 用户ID
    user_name: str        # 用户名
    session_id: str       # 会话ID
    session_type: str     # "private", "group"
    timestamp: float
    metadata: dict        # 平台特有数据
    attachments: list     # 附件
    identity_context: dict  # 由Bus注入的身份上下文
```

#### Plugin 接口

```python
class PlatformPlugin(ABC):
    name: str
    platform_id: str
    
    async def connect(self) -> bool
    async def disconnect(self) -> None
    async def receive(self) -> AsyncIterator[PlatformEvent]
    async def send(self, event, response) -> bool
    def translate_in(self, raw_data) -> PlatformEvent
    def translate_out(self, event, response) -> any

class CapabilityPlugin(ABC):
    name: str
    description: str
    parameters_schema: dict
    
    async def execute(self, params: dict) -> dict
    def get_schema(self) -> dict
```

---

## 三、Phase 1 成果（Core 抽离）

### 3.1 创建的模块

#### `yuki_core/models.py` - 数据模型

```python
# 核心数据结构
EventType       # MESSAGE, IMAGE, FILE, VOICE, ACTION, SYSTEM
SessionType     # PRIVATE, GROUP, CHANNEL
ActionType      # REPLY, IGNORE, CAPABILITY, DELEGATE

PlatformEvent   # 跨平台统一事件
YukiResponse    # Yuki 的回复
Action          # Yuki 的行动
Decision        # 决策结果
IdentityContext # 身份上下文
MemoryEntry     # 记忆条目
Attachment      # 附件信息
```

#### `yuki_core/identity.py` - 身份定义

```python
class SpeakingStyle:
    self_reference: str   # 自称
    address_master: str   # 称呼主人
    tone: str             # 语气

class Relationship:
    name: str
    relation: str
    style: str

class Persona:
    name: str
    display_name: str
    personality: list
    speaking_style: SpeakingStyle
    relationships: dict
    maid_setting: str     # 小女仆设定
    
    def get_base_prompt() -> str    # 基础人格 prompt
    def get_full_prompt() -> str    # 包含小女仆的完整 prompt

class YukiIdentity:
    persona: Persona
    platform_identities: dict  # {platform_id: IdentityContext}
    
    def get_platform_identity(platform) -> IdentityContext
    def get_system_prompt(platform) -> str

# 快捷函数（兼容旧代码）
get_system_prompt(platform) -> str
get_yuki_setting_private() -> str
get_yuki_setting_group() -> str
get_summary_prompt() -> str
```

#### `yuki_core/mind.py` - 决策引擎

```python
class EnergySystem:
    """精力值管理"""
    def get(session_id) -> float     # 获取精力（含恢复计算）
    def consume(session_id) -> float # 消耗精力

class ActivityTracker:
    """活跃度追踪"""
    def update(session_id) -> float  # 非线性增长
    def get(session_id) -> float     # 获取（含衰减）

class DesireCalculator:
    """欲望计算（Sigmoid）"""
    def calculate(activity, energy, time_weight) -> float
    def should_reply(desire) -> bool

class BioClock:
    """生物钟（高斯活跃峰）"""
    @staticmethod
    def get_time_weight() -> float

class YukiMind:
    """主决策引擎"""
    energy: EnergySystem
    activity: ActivityTracker
    desire: DesireCalculator
    bioclock: BioClock
    
    async def process(event: PlatformEvent) -> YukiResponse
    def get_status(session_id) -> dict
```

**关键参数**:
- 精力: 初始100, 每分钟恢复0.8, 每次回复消耗6
- 活跃度: 灵敏度0.12, 衰减系数0.65, 5分钟半衰
- 欲望: Sigmoid中心50, 陡峭度0.08, 阈值30

#### `yuki_core/memory.py` - 记忆系统

```python
class YukiMemory:
    """向量检索 + 关键词补偿"""
    
    def remember(content, session_id, source, metadata) -> bool
    def recall(context, session_id, limit) -> list[MemoryEntry]
    
    def load_history() -> dict
    def save_history(history)
    def append_message(session_id, role, content)
    def get_recent_messages(session_id, limit) -> list[dict]
    def clear_session(session_id)
```

**依赖**: chromadb, sentence-transformers, jieba
**惰性初始化**: 首次调用时才加载模型

#### `yuki_core/maid.py` - 小女仆系统

```python
class MaidAgent:
    """自主编程进化"""
    task_queue: asyncio.Queue
    
    async def delegate(task_desc, session_id) -> str
    def list_all_skills() -> list[str]
    def get_current_task(session_id) -> str
```

**工具**: list_skills, read_skill, write_skill, run_skill, install_package, finish

#### `yuki_core/bus.py` - 上下文总线

```python
class ContextBus:
    """连接 Core 和 Plugin"""
    mind: YukiMind
    identity: YukiIdentity
    platform_plugins: dict
    capability_plugins: dict
    
    def register_platform(plugin)
    def register_capability(plugin)
    async def start()          # 启动所有监听
    async def stop()           # 停止
    async def receive(event) -> YukiResponse  # 核心入口
    def get_capabilities_schema() -> list[dict]
```

#### `yuki_core/plugin.py` - 插件基类

```python
class PlatformPlugin(ABC):
    name: str
    platform_id: str
    bus: ContextBus
    
    async def connect() -> bool
    async def disconnect() -> None
    async def receive() -> AsyncIterator[PlatformEvent]
    async def send(event, response) -> bool
    def translate_in(raw) -> PlatformEvent
    def translate_out(event, response) -> any

class CapabilityPlugin(ABC):
    name: str
    description: str
    parameters_schema: dict
    
    async def execute(params) -> dict
    def get_schema() -> dict

def load_plugins_from_config(bus, config_path)
```

### 3.2 验证结果

```bash
$ python main.py

============================================================
  Yuki Agent v0.1.0
  多平台 AI Agent 框架
============================================================

[Phase 1] 验证 Core 模块...
  ✅ Identity: Yuki
     人格: 活泼, 温柔, 偶尔吐槽, 忠诚
  ✅ System Prompt: QQ群聊 (649 chars), 私聊 (529 chars)
  ✅ Mind: 精力系统、活跃度追踪、欲望计算、生物钟
  ✅ Memory: 向量检索 + 关键词补偿
  ✅ Maid: 自主编程进化系统
  ✅ Bus: 上下文总线

[Phase 1] ✅ Core 抽离完成！

[Demo] 模拟事件处理...
  事件: Yuki在吗
  回复: [Mind] 收到来自 主人 的消息: Yuki在吗...
  状态: {'energy': 94.0, 'desire': 0.4545, 'decision_reason': '欲望值 45.5% 超过阈值'}
```

---

## 四、Phase 2 成果（Plugin 框架 + QQ 迁移）

### 4.1 创建的文件

| 文件 | 说明 | 来源 |
|------|------|------|
| `plugins/__init__.py` | 插件包 | 新建 |
| `plugins/platforms/__init__.py` | 平台插件包 | 新建 |
| `plugins/platforms/qq_plugin/__init__.py` | QQ 插件入口 | 新建 |
| `plugins/platforms/qq_plugin/adapter.py` | QQ PlatformPlugin 核心实现 | YukiV6 main.py |
| `plugins/platforms/qq_plugin/napcat_ws.py` | NapCat WebSocket 连接管理 | YukiV6 ws_connection.py |
| `plugins/platforms/qq_plugin/cq_parser.py` | CQ码解析器 (合并3模块) | YukiV6 CQParser+CQProtocol+GetMeta |
| `plugins/platforms/qq_plugin/message_sender.py` | 消息发送器 | YukiV6 ws_sender.py |
| `plugins/platforms/qq_plugin/group_manager.py` | 群聊开关管理 | YukiV6 main.py |
| `plugins/platforms/qq_plugin/prompts.py` | QQ 特化系统提示词 | YukiV6 core/prompts.py |
| `tests/test_qq_plugin.py` | 集成测试 (8项全通过) | 新建 |

### 4.2 模块设计

#### `adapter.py` - QQ 核心适配器

```python
class QQPlugin(PlatformPlugin):
    name = "QQ Bot"
    platform_id = "qq"
    
    # 子模块
    ws: NapCatWS           # WebSocket 连接
    sender: MessageSender  # 消息发送
    parser: CQCodeParser   # CQ码解析
    group_manager: GroupManager  # 群聊开关
    
    # 消息缓冲
    _buffers: dict[str, list]     # chat_id → 消息列表
    _buffer_tasks: dict[str, Task]  # chat_id → 防抖任务
    _real_time_debounce: float    # 动态防抖时间
    
    async def connect() -> bool
    async def disconnect() -> None
    async def receive() -> AsyncIterator[PlatformEvent]  # 核心循环
    async def send(event, response) -> bool
    def translate_in(raw_data) -> PlatformEvent
    def translate_out(event, response) -> str
```

**关键逻辑迁移**:
- 消息缓冲防抖 → adapter.py 内部 `_buffers` + `_buffer_tasks`
- 群聊开关 → group_manager.py 独立模块
- 冒充检测 → translate_in() 中检测
- 帮助指令 → mind.py 已内置

#### `napcat_ws.py` - WebSocket 连接

```python
class NapCatWS:
    async def connect() -> bool
    async def ensure_connection() -> WebSocket  # 自动重连
    async def listen() -> AsyncIterator[dict]   # 事件流
    async def send_raw(data: dict)              # 发送原始数据
    async def send_request(action, params, echo) -> dict  # 请求-响应
    async def disconnect()
```

#### `cq_parser.py` - CQ码解析器 (合并3模块)

从 YukiV6 的 3 个文件合并为 1 个：
- `CQProtocol.py` → 工具函数 (smart_truncate, replace_media_cq_codes 等)
- `GetMeta.py` → MetaGetter 类 (通过 WebSocket 获取用户信息/回复内容)
- `CQParser.py` → CQCodeParser 类 (整合解析流程)

#### `group_manager.py` - 群聊开关

```python
class GroupManager:
    def is_active(group_id) -> bool      # 检查群是否开启
    def set_active(group_id, active)     # 设置开关
    def handle_command(msg, uid, gid) -> str  # 处理 /开启 /关闭
```

### 4.3 配置更新

`configs/plugins.yaml` 新增全局配置：

```yaml
robot_name: "yuki"
master_name: "主人"
master_id: 0           # 主人 QQ 号
keywords:              # 注意力关键词
  - 主人
  - 哥哥
  - yuki
```

`main.py` 启动流程：

```
1. 初始化 Core (Identity → Mind → Memory → Maid → Bus)
2. 加载插件 (plugins.yaml → load_plugins_from_config)
3. 注入全局配置 (master_id 等 → QQ Plugin)
4. 启动 Bus (连接 NapCat → 监听消息 → 事件循环)
```

### 4.4 测试结果

```bash
$ python tests/test_qq_plugin.py

============================================================
  QQ Plugin 集成测试
  Phase 2: Plugin 框架 + QQ 迁移
============================================================

[Test] 导入测试...
  ✅ yuki_core 导入成功
  ✅ QQ Plugin 导入成功

[Test] 消息截断测试...
  ✅ 短消息不截断
  ✅ 超长消息截断: 200 → 83 chars
  ✅ CQ 码完整性保留

[Test] CQ 码解析测试...
  ✅ 多媒体 CQ 码替换
  ✅ @ QQ 号提取
  ✅ 回复 ID 提取
  ✅ @ Yuki 检测

[Test] 群聊管理测试...
  ✅ 默认状态为开启
  ✅ 关闭群聊
  ✅ 开启群聊
  ✅ 非主人被拒绝
  ✅ 主人关闭成功
  ✅ 主人开启成功
  ✅ 非管理指令返回 None

[Test] 提示词生成测试...
  ✅ 群聊提示词: 755 chars
  ✅ 私聊提示词: 189 chars
  ✅ 破冰提示词: 182 chars
  ✅ 视觉提示词: 123 chars

[Test] 消息转换测试...
  ✅ 群聊转换: 【"小明"】说: 大家好
  ✅ 私聊转换: 在吗
  ✅ 冒充检测: 主人(冒充)

[Test] 插件配置加载测试...
  ✅ plugins.yaml 解析成功
  ✅ QQ 插件加载成功: QQ Bot v1.0.0

[Test] Bus 事件接收测试...
  ✅ 事件处理成功: [Mind] 收到来自 主人 的消息: Yuki在吗...
     状态: {'energy': 94.0, 'desire': 0.4336, 'decision_reason': '欲望值 43.4% 超过阈值'}

============================================================
  结果: 8/8 通过, 0 失败
============================================================
```

### 4.5 与 YukiV6 的差异

| 特性 | YukiV6 | YukiV7 Phase 2 |
|------|--------|-----------------|
| 入口 | main.py 一体化 | main.py → Bus → Plugin |
| QQ 逻辑 | 散布在 main.py | 封装在 qq_plugin/ |
| 配置 | config.py 单例 | plugins.yaml + identities.yaml |
| 事件格式 | 原始 dict | PlatformEvent dataclass |
| 决策 | brain.py + engine.py | yuki_core/mind.py |
| 可扩展性 | 仅 QQ | 插件化，可加 Web/Voice |

---

## 五、Phase 2 开发计划（已完成）

### 4.1 目标

1. 实现插件加载机制
2. 把 YukiV6 的 QQ 功能迁移为第一个插件
3. 端到端测试：QQ消息 → Core处理 → 回复

### 4.2 任务清单

| # | 任务 | 来源文件 | 目标文件 | 预估工时 |
|---|------|----------|----------|----------|
| 1 | QQ Plugin 目录结构 | - | `plugins/platforms/qq_plugin/` | 0.5h |
| 2 | NapCat WebSocket | `network/ws_connection.py` | `qq_plugin/napcat_ws.py` | 2h |
| 3 | CQ码解析器 | `modules/message/CQParser.py` | `qq_plugin/cq_parser.py` | 2h |
| 4 | 消息发送器 | `network/ws_sender.py` | `qq_plugin/message_sender.py` | 1h |
| 5 | QQ适配器主逻辑 | `main.py` (消息缓冲/防抖) | `qq_plugin/adapter.py` | 4h |
| 6 | 群聊管理器 | `main.py` (群聊开关) | `qq_plugin/group_manager.py` | 1h |
| 7 | QQ系统提示词 | `core/prompts.py` (群聊部分) | `qq_plugin/prompts.py` | 1h |
| 8 | 集成测试 | - | `tests/test_qq_plugin.py` | 2h |

### 4.3 文件迁移映射

```
YukiV6                          YukiV7
─────────────────────────────────────────────────────────────
main.py (消息缓冲/防抖)     →   plugins/platforms/qq_plugin/adapter.py
main.py (群聊开关)          →   plugins/platforms/qq_plugin/group_manager.py
main.py (冒充检测)          →   plugins/platforms/qq_plugin/adapter.py
modules/message/CQParser.py →   plugins/platforms/qq_plugin/cq_parser.py
network/ws_connection.py    →   plugins/platforms/qq_plugin/napcat_ws.py
network/ws_sender.py        →   plugins/platforms/qq_plugin/message_sender.py
core/prompts.py (群聊prompt) →  plugins/platforms/qq_plugin/prompts.py
core/engine.py (破冰)       →   plugins/social/ice_breaker/ (Phase 5)
modules/vision/processor.py →   plugins/social/sticker_system/ (Phase 5)
modules/stickers/           →   plugins/social/sticker_system/ (Phase 5)
```

### 4.4 QQ Plugin 目录结构

```
plugins/platforms/qq_plugin/
├── __init__.py
├── adapter.py          # PlatformPlugin 实现（核心）
├── napcat_ws.py        # NapCat WebSocket 连接管理
├── cq_parser.py        # CQ码解析器
├── message_sender.py   # 消息发送器
├── group_manager.py    # 群聊开关管理
├── prompts.py          # QQ 特化的系统提示词
└── config.py           # QQ 特有配置（可选）
```

### 4.5 adapter.py 核心实现

```python
# plugins/platforms/qq_plugin/adapter.py

from yuki_core import PlatformPlugin, PlatformEvent, YukiResponse

class QQPlugin(PlatformPlugin):
    name = "QQ Bot"
    platform_id = "qq"
    version = "1.0.0"
    
    def __init__(self, **config):
        self.ws_url = config.get("napcat_ws_url", "ws://localhost:3001")
        self.ws_token = config.get("napcat_ws_token", "")
        self.target_groups = config.get("target_groups", [])
        self.target_qq = config.get("target_qq", 0)
        self.debounce_time = config.get("debounce_time", 32)
        
        self.ws = None
        self.sender = None
        self.parser = None
        self.group_manager = None
        
        # 消息缓冲
        self._buffers: dict[str, list] = {}
        self._buffer_tasks: dict[str, asyncio.Task] = {}
    
    async def connect(self) -> bool:
        from .napcat_ws import NapCatWS
        from .message_sender import MessageSender
        from .cq_parser import CQParser
        from .group_manager import GroupManager
        
        self.ws = NapCatWS(self.ws_url, self.ws_token)
        self.sender = MessageSender(self.ws)
        self.parser = CQParser(self.ws)
        self.group_manager = GroupManager()
        
        return await self.ws.connect()
    
    async def disconnect(self) -> None:
        if self.ws:
            await self.ws.disconnect()
    
    async def receive(self):
        async for raw_data in self.ws.listen():
            # 过滤非消息事件
            if raw_data.get("post_type") != "message":
                continue
            
            # 转换为 PlatformEvent
            event = self.translate_in(raw_data)
            
            if event:
                yield event
    
    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        # 解析回复中的特殊标签（表情包、图片等）
        parts = self._parse_response(response.text)
        
        for part in parts:
            await self.sender.send(event.session_id, part, mode=event.session_type)
            await asyncio.sleep(1.0)  # 模拟人类节奏
        
        return True
    
    def translate_in(self, raw_data: dict) -> PlatformEvent:
        """QQ原始数据 → PlatformEvent"""
        msg_type = raw_data.get("message_type")
        user_id = str(raw_data.get("user_id", ""))
        
        # 获取发送者名称
        sender = raw_data.get("sender", {})
        user_name = sender.get("card") or sender.get("nickname") or "路人"
        
        # 群聊或私聊
        if msg_type == "group":
            session_id = str(raw_data.get("group_id", ""))
            session_type = "group"
        else:
            session_id = user_id
            session_type = "private"
        
        return PlatformEvent(
            source="qq",
            event_type="message",
            content=raw_data.get("raw_message", ""),
            user_id=user_id,
            user_name=user_name,
            session_id=session_id,
            session_type=session_type,
            timestamp=raw_data.get("time", 0),
            metadata={
                "raw_data": raw_data,
                "message_type": msg_type,
            }
        )
    
    def translate_out(self, event: PlatformEvent, response: YukiResponse) -> any:
        """YukiResponse → QQ消息格式"""
        return response.text
```

### 4.6 需要处理的特殊逻辑

1. **消息缓冲防抖** - 在 adapter.py 中实现
2. **群聊开关** - 在 group_manager.py 中实现
3. **冒充检测** - 在 adapter.py 的 translate_in 中处理
4. **特殊指令** - /开启、/关闭、help 等
5. **表情包系统** - 暂不迁移，放到 Phase 5

### 4.7 与 Core 的集成点

```
QQ Plugin                    Core
─────────────────────────────────────
receive()               →   ContextBus.receive()
                              ↓
                         YukiMind.process()
                              ↓
send()              ←    YukiResponse
                              ↑
                         Identity.get_system_prompt("qq")
```

---

## 五、后续阶段概览

### Phase 3: 身份感知（1周）

**目标**: Yuki 能意识到自己在不同平台的不同身份

**任务**:
- 在 `configs/identities.yaml` 中为每个平台配置不同的上下文提示词
- 在 `ContextBus.receive()` 中注入平台身份上下文
- 测试：QQ端表现活泼，网页端表现专业

### Phase 4: 网页端 + API（2-3周）

**目标**: 有第一个非QQ平台

**任务**:
- 创建 `plugins/platforms/web_plugin/`
- 实现 FastAPI 后端
- 实现简单聊天前端（Gradio 或 Vue）
- REST API + WebSocket 实时对话

### Phase 5: 能力扩展（持续迭代）

**目标**: Yuki 能做更多事

**能力插件清单**:
- `web_search` - 联网搜索
- `file_manager` - 文件操作
- `code_executor` - 代码执行
- `calendar` - 日历管理
- `smart_home` - 智能家居

### Phase 6: 语音 + 现实交互（3-4周）

**目标**: Yuki 有声音，能控制物理世界

**任务**:
- 集成 Whisper STT + GPT-SoVITS TTS
- 智能家居控制
- 实体化方向：树莓派 + 屏幕

---

## 六、开发规范

### 6.1 代码规范

- Python 3.10+
- 类型注解
- 异步优先 (async/await)
- 中文注释

### 6.2 Git 规范

```bash
# 分支命名
main        # 稳定版
dev         # 开发版
feature/*   # 功能分支
fix/*       # 修复分支

# 提交信息
feat: 添加 xxx 功能
fix: 修复 xxx 问题
refactor: 重构 xxx
docs: 更新文档
```

### 6.3 测试规范

每个 Phase 完成后必须：
1. 单元测试通过
2. 集成测试通过
3. main.py 能正常运行
4. 现有功能不回退

---

## 七、关键配置文件

### `configs/identities.yaml`

```yaml
core:
  name: yuki
  display_name: Yuki
  personality: [活泼, 温柔, 偶尔吐槽, 忠诚]
  speaking_style:
    self_reference: 人家
    address_master: 主人
  relationships:
    master: {name: 池宇健, relation: 主人}
    momo: {name: Momo, relation: 妹妹}

platforms:
  qq:
    context: "你现在在QQ群里..."
    style_override: {max_length: 40}
  web:
    context: "你是个人助手..."
    style_override: {max_length: 500}
```

### `configs/plugins.yaml`

```yaml
platforms:
  qq:
    enabled: true
    class: "plugins.platforms.qq_plugin.QQPlugin"
    config:
      napcat_ws_url: "ws://localhost:3001"
  
  web:
    enabled: false
    class: "plugins.platforms.web_plugin.WebPlugin"

capabilities:
  web_search:
    enabled: true
    class: "plugins.capabilities.web_search.WebSearchPlugin"
```

---

## 八、常见问题

### Q: 为什么不直接修改 YukiV6？

A: YukiV6 的代码和 QQ 协议强耦合，直接修改会引入大量风险。重构为独立的 Core + Plugin 架构，可以保持 YukiV6 继续运行，同时开发 YukiV7。

### Q: Token 消耗会增加吗？

A: Phase 0-2 基本不变。Phase 3+ 会因为身份上下文注入增加 5-10%。可以通过分层模型策略优化。

### Q: 什么时候可以使用？

A: Phase 2 完成后（约4-5周），QQ 功能可以完整运行。Phase 4 完成后可以使用网页版。

---

## 九、给下一个 LLM 的指导

### 9.1 当前状态

- **项目路径**: `D:\Projects\YukiV7`
- **源项目**: `D:\Projects\YukiV6`
- **运行环境**: `conda ai_env` (D:\Dev\Env\MiniForge\envs\ai_env)
- **已完成**: Phase 0 (文档) + Phase 1 (Core抽离) + Phase 2 核心 (Plugin框架 + QQ迁移)
- **待补**: 表情包系统、帮助指令、破冰、精力衰减、视觉理解
- **下一步**: Phase 2.5 (补齐功能缺口) 或 Phase 3 (身份感知)

### 9.2 开始 Phase 2 前的准备

1. **阅读以下文件**:
   - `D:\Projects\YukiV7\docs\MIGRATION_CHECKLIST.md` - 迁移清单
   - `D:\Projects\YukiV7\docs\PLUGIN_SPEC.md` - 插件规范
   - `D:\Projects\YukiV7\yuki_core\plugin.py` - 插件基类

2. **理解现有 QQ 代码**:
   - `D:\Projects\YukiV6\main.py` - 消息缓冲、防抖、群聊开关
   - `D:\Projects\YukiV6\network\ws_connection.py` - WebSocket 连接
   - `D:\Projects\YukiV6\modules\message\CQParser.py` - CQ码解析
   - `D:\Projects\YukiV6\network\ws_sender.py` - 消息发送

3. **关键设计决策**:
   - QQ Plugin 继承 `PlatformPlugin`
   - 所有 QQ 特有逻辑都在 Plugin 内部
   - Core 不包含任何 QQ 协议相关内容
   - 消息缓冲防抖逻辑在 adapter.py 中实现

### 9.3 Phase 2 执行顺序

```
Step 1: 创建 plugins/platforms/qq_plugin/ 目录
Step 2: 实现 napcat_ws.py (从 ws_connection.py 迁移)
Step 3: 实现 cq_parser.py (从 CQParser.py 迁移)
Step 4: 实现 message_sender.py (从 ws_sender.py 迁移)
Step 5: 实现 adapter.py (从 main.py 迁移消息缓冲/防抖)
Step 6: 实现 group_manager.py (从 main.py 迁移群聊开关)
Step 7: 实现 prompts.py (QQ特化的系统提示词)
Step 8: 更新 main.py 加载 QQ Plugin
Step 9: 端到端测试
```

### 9.4 验证标准

Phase 2 完成后，以下功能必须正常：
- [x] NapCat WebSocket 连接成功
- [x] 接收 QQ 群消息
- [x] 消息防抖正常（短时间内多条消息合并处理）
- [x] 群聊开关 (/开启, /关闭) 正常
- [x] Yuki 能回复消息（LLM 调用 + 上下文构建）
- [x] 私聊功能正常
- [x] 冒充检测正常
- [x] CQ码解析正常（@/回复/图片占位）
- [x] 日记自动写入正常
- [x] RAG 检索正常（融合排名，12条）
- [x] 历史记录持久化正常
- [ ] 表情包系统 [MEME_SEARCH]
- [ ] 帮助指令 (help → 发送帮助图)
- [ ] 破冰主动唤醒
- [ ] 精力衰减后台任务
- [ ] 视觉理解 (MemeProcessor)

### 9.5 Phase 2 功能对齐清单

```
功能                          YukiV6    YukiV7    备注
═══════════════════════════════════════════════════════════════════
消息收发 (WebSocket+CQ码)     ✅        ✅        已对齐
LLM 调用 (主备切换)            ✅        ✅        llm.py
上下文构建 (RAG+历史+prompt)  ✅        ✅        已严格对齐
精力/欲望/生物钟              ✅        ✅        mind.py
群聊开关 (/开启 /关闭)        ✅        ✅        group_manager.py
冒充检测                      ✅        ✅        adapter.py
消息防抖 (32秒缓冲)           ✅        ✅        adapter.py
日记自动写入                  ✅        ✅        bus.py
CQ码解析 (@/回复/图片)        ✅        ✅        cq_parser.py
历史记录管理                  ✅        ✅        history.py
布局标签清理                  ✅        ✅        mind.py
─────────────────────────────────────────────────────────────────
表情包系统 [MEME_SEARCH]      ✅        ❌        P0 待补
帮助指令 (help → 发图)        ✅        ❌        P0 待补
破冰主动唤醒                  ✅        ❌        P1 待补
精力衰减后台任务              ✅        ❌        P1 待补
视觉理解 (MemeProcessor)      ✅        ❌        P2 待补
表情包正反馈 (RLHF)           ✅        ❌        P2 待补
小女仆委托                    ✅        ⚠️        占位，需集成
```

### 9.6 注意事项

1. **不要修改 yuki_core/** - 这是稳定的 Core，Phase 2 只在 plugins/ 下工作
2. **保持向后兼容** - QQ Plugin 的行为必须和 YukiV6 一致
3. **测试先行** - 每个模块迁移后立即测试
4. **日志完善** - 保持详细的日志输出，方便调试

---

*End of Development Log*
