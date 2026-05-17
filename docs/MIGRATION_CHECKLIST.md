# 现有代码迁移清单

> Phase 0 产物 - YukiV6 → YukiV7 代码迁移映射

## 1. 迁移原则

1. **渐进式迁移**: 每个模块独立迁移，迁移后立即测试
2. **行为一致**: 迁移后功能必须与重构前完全一致
3. **先Core后Plugin**: 先抽离核心模块，再包装成插件
4. **保留备份**: 原代码保留到 `legacy/` 目录

## 2. 核心模块迁移

### 2.1 身份定义

| 源文件 | 目标文件 | 迁移内容 | 复杂度 |
|--------|----------|----------|--------|
| `core/prompts.py` | `yuki_core/identity.py` | 提取 `get_base_setting()` 中的人格定义 | 低 |
| `core/prompts.py` | `yuki_core/identity.py` | 提取 `MAID_SETTING` 小女仆设定 | 低 |
| `configs/config.yaml` | `configs/identities.yaml` | 拆分多平台身份配置 | 中 |

**迁移步骤**:
```python
# 1. 从 prompts.py 提取核心人格
persona = {
    "name": "yuki",
    "role": "电子妹妹",
    "personality": ["活泼", "温柔", "偶尔吐槽"],
    "speaking_style": {
        "self_reference": "人家",
        "address_master": "主人"
    }
}

# 2. 从 prompts.py 提取关系定义
relationships = {
    "master": {"name": "池宇健", "relation": "主人"},
    "momo": {"name": "Momo", "relation": "妹妹"}
}

# 3. 创建多平台身份配置
# → configs/identities.yaml
```

### 2.2 记忆系统

| 源文件 | 目标文件 | 迁移内容 | 复杂度 |
|--------|----------|----------|--------|
| `modules/memory/rag.py` | `yuki_core/memory.py` | 向量检索核心逻辑 | 中 |
| `core/history_manager.py` | `yuki_core/memory.py` | 对话历史管理 | 低 |
| `modules/memory/rag.py` | `yuki_core/memory.py` | 移除QQ特有的 chat_id 过滤 | 中 |

**需要修改的耦合点**:
```python
# 旧: chat_id 硬编码为QQ群号
def search_diaries(self, query, chat_id=None, top_k=10):
    where_filter = {}
    if chat_id:
        where_filter["chat_id"] = str(chat_id)

# 新: chat_id 泛化为 session_id，可选过滤
def recall(self, context: str, session_id: str = None, limit: int = 10):
    where_filter = {}
    if session_id:
        where_filter["session_id"] = session_id
```

### 2.3 决策引擎

| 源文件 | 目标文件 | 迁移内容 | 复杂度 |
|--------|----------|----------|--------|
| `core/brain.py` | `yuki_core/mind.py` | 精力值系统 | 低 |
| `core/brain.py` | `yuki_core/mind.py` | 欲望计算（Sigmoid） | 低 |
| `core/engine.py` | `yuki_core/mind.py` | API调用、上下文构建 | 中 |
| `core/engine.py` | `yuki_core/mind.py` | 移除QQ特化的群聊决策 | 中 |

**需要拆分的逻辑**:
```python
# core/engine.py 中的 decide_to_reply

# === 保留: 通用决策 ===
def calculate_desire(self, activity, energy, time_weight):
    """计算社交欲望（平台无关）"""
    return sigmoid(activity, energy, time_weight)

# === 移到 QQ Plugin: 群聊特化规则 ===
def qq_specific_rules(self, event, history):
    """QQ群聊特有的回复规则"""
    # 1. 被@必须回复
    if event.metadata.get("at_yuki"):
        return True
    # 2. 帮助指令必须回复
    if event.content in ["help", "帮助"]:
        return True
    # ... 其他QQ特有规则
```

### 2.4 小女仆系统

| 源文件 | 目标文件 | 迁移内容 | 复杂度 |
|--------|----------|----------|--------|
| `core/maid.py` | `yuki_core/maid.py` | 直接迁移，改动最小 | 低 |

**注意**: 小女仆系统已经是平台无关的，基本不需要修改。

## 3. 平台插件迁移

### 3.1 QQ 插件

| 源文件 | 目标文件 | 迁移内容 | 复杂度 |
|--------|----------|----------|--------|
| `main.py` (消息缓冲) | `plugins/platforms/qq_plugin/adapter.py` | 消息缓冲防抖逻辑 | 中 |
| `main.py` (群聊开关) | `plugins/platforms/qq_plugin/group_manager.py` | /开启 /关闭 逻辑 | 低 |
| `main.py` (冒充检测) | `plugins/platforms/qq_plugin/adapter.py` | 身份验证逻辑 | 低 |
| `modules/message/CQParser.py` | `plugins/platforms/qq_plugin/cq_parser.py` | CQ码解析 | 低 |
| `network/ws_connection.py` | `plugins/platforms/qq_plugin/napcat_ws.py` | WebSocket连接 | 低 |
| `network/ws_sender.py` | `plugins/platforms/qq_plugin/message_sender.py` | 消息发送 | 低 |
| `core/engine.py` (破冰) | `plugins/social/ice_breaker/` | 破冰主动唤醒 | 中 |
| `modules/vision/processor.py` | `plugins/social/sticker_system/` | 表情包视觉理解 | 中 |
| `modules/stickers/` | `plugins/social/sticker_system/` | 表情包RLHF | 高 |

**QQ Plugin 目录结构**:
```
plugins/platforms/qq_plugin/
├── __init__.py
├── adapter.py          # PlatformPlugin 实现
├── napcat_ws.py        # WebSocket连接管理
├── cq_parser.py        # CQ码解析器
├── message_sender.py   # 消息发送器
├── group_manager.py    # 群聊开关管理
└── config.py           # QQ特有配置
```

### 3.2 Web 插件 (新建)

| 模块 | 文件 | 说明 |
|------|------|------|
| API层 | `plugins/platforms/web_plugin/api.py` | FastAPI 路由 |
| 适配器 | `plugins/platforms/web_plugin/adapter.py` | PlatformPlugin 实现 |
| 前端 | `plugins/platforms/web_plugin/frontend/` | Vue/Gradio |

## 4. 能力插件迁移

### 4.1 从 Maid 泛化

| 现有能力 | 新插件 | 说明 |
|----------|--------|------|
| maid 的代码执行 | `code_executor` | 泛化为通用代码执行 |
| maid 的包安装 | `package_manager` | 泛化为包管理 |
| maid 的技能管理 | `skill_manager` | 保留为技能系统 |

### 4.2 新增能力

| 插件 | 说明 | 优先级 |
|------|------|--------|
| `web_search` | 联网搜索 | P0 |
| `file_manager` | 文件读写 | P0 |
| `weather` | 天气查询 | P1 |
| `calendar` | 日历管理 | P1 |

## 5. 工具模块迁移

| 源文件 | 目标文件 | 说明 |
|--------|----------|------|
| `utils/logger.py` | `yuki_core/utils/logger.py` | 日志系统 |
| `utils/__init__.py` | `yuki_core/utils/__init__.py` | 工具函数 |
| `config.py` | `yuki_core/config.py` | 配置管理（需重构） |

## 6. 不迁移的内容

以下模块属于 QQ 群聊特化优化，留在 QQ Plugin 中：

- 群聊消息缓冲防抖逻辑
- 冒充者检测
- 帮助图片发送
- 特定的表情包处理逻辑

## 7. 迁移顺序

```
Phase 1 (Core抽离):
    Step 1: yuki_core/utils/logger.py     ← 独立，无依赖
    Step 2: yuki_core/identity.py         ← 从 prompts.py 提取
    Step 3: yuki_core/memory.py           ← 从 rag.py + history_manager.py 提取
    Step 4: yuki_core/mind.py             ← 从 brain.py + engine.py 提取
    Step 5: yuki_core/maid.py             ← 直接迁移
    Step 6: yuki_core/bus.py              ← 新建
    Step 7: yuki_core/plugin.py           ← 新建

Phase 2 (Plugin迁移):
    Step 8:  plugins/platforms/qq_plugin/ ← 从 main.py + network/ 迁移
    Step 9:  plugins/social/sticker_system/ ← 从 modules/ 迁移
    Step 10: 测试验证
```

## 8. 测试检查清单

迁移每个模块后，需要验证：

- [ ] 单元测试通过
- [ ] 模块可独立导入
- [ ] 与现有模块的接口兼容
- [ ] 端到端功能正常（QQ发消息→Yuki回复）

---

*Created: 2026-05-17 | Phase 0*
