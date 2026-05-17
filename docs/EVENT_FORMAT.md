# PlatformEvent 格式定义

> Phase 0 产物 - 统一的跨平台事件格式规范

## 1. 设计目标

- **平台无关**: 所有平台的消息统一为同一种格式
- **可扩展**: 支持不同平台的特有元数据
- **向后兼容**: 与现有 QQ 消息格式能互相转换

## 2. 事件类型

| event_type | 说明 | content 示例 |
|------------|------|--------------|
| `message` | 文本消息 | "你好" |
| `image` | 图片消息 | "[图片]" 或 URL |
| `file` | 文件消息 | 文件名或路径 |
| `voice` | 语音消息 | 语音转文字 |
| `action` | 动作事件 | "join_group", "leave_group" |
| `system` | 系统事件 | "bot_online", "bot_offline" |

## 3. 核心数据结构

```python
from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime

@dataclass
class PlatformEvent:
    """跨平台统一事件格式"""
    
    # === 必填字段 ===
    source: str              # 来源平台: "qq", "web", "voice", "discord"
    event_type: str          # 事件类型: "message", "image", "file", "voice", "action", "system"
    content: str             # 主要内容（文本/描述）
    
    # === 用户信息 ===
    user_id: str             # 平台内用户ID
    user_name: str = ""      # 用户显示名称
    
    # === 会话信息 ===
    session_id: str = ""     # 会话ID（群聊=group_id, 私聊=user_id, 网页=session_id）
    session_type: str = ""   # "private", "group", "channel"
    
    # === 时间 ===
    timestamp: float = 0.0   # Unix时间戳
    
    # === 平台特有数据 ===
    metadata: dict = field(default_factory=dict)
    """
    QQ平台特有:
      - group_id: 群号
      - sender_name: 发送者群名片
      - reply_to: 回复的消息ID
      - at_users: 被@的用户列表
    
    Web平台特有:
      - session_token: 认证token
    
    Voice平台特有:
      - audio_duration: 语音时长
      - confidence: 语音识别置信度
    """
    
    # === 图片/文件 ===
    attachments: list = field(default_factory=list)
    """
    格式:
    [
        {
            "type": "image",        # "image", "file", "voice"
            "url": "https://...",   # 远程URL
            "path": "/local/path", # 本地路径
            "name": "sticker.png", # 文件名
            "size": 12345,         # 文件大小(bytes)
            "is_meme": True        # 是否是表情包(仅QQ)
        }
    ]
    """
    
    # === 上下文注入(由Bus层填充) ===
    identity_context: Optional[dict] = None
    """
    由ContextBus注入，包含:
    - platform_prompt: 该平台的身份提示词
    - capabilities: 该平台可用的能力列表
    - style_override: 风格覆盖配置
    """
```

## 4. 各平台转换示例

### 4.1 QQ → PlatformEvent

```python
# NapCat WebSocket 原始数据
raw_data = {
    "post_type": "message",
    "message_type": "group",
    "user_id": 123456,
    "group_id": 789012,
    "raw_message": "Yuki在吗",
    "sender": {
        "card": "池宇健",
        "nickname": "eganchiyu"
    },
    "time": 1684300000
}

# 转换为 PlatformEvent
event = PlatformEvent(
    source="qq",
    event_type="message",
    content="Yuki在吗",
    user_id="123456",
    user_name="池宇健",
    session_id="789012",
    session_type="group",
    timestamp=1684300000,
    metadata={
        "group_id": 789012,
        "sender_name": "池宇健",
        "raw_message": "Yuki在吗"
    }
)
```

### 4.2 Web → PlatformEvent

```python
# HTTP 请求
request = {
    "message": "帮我查一下天气",
    "session_id": "web_session_abc123"
}

# 转换为 PlatformEvent
event = PlatformEvent(
    source="web",
    event_type="message",
    content="帮我查一下天气",
    user_id="web_user_001",  # 从token解析
    user_name="主人",
    session_id="web_session_abc123",
    session_type="private",
    timestamp=time.time(),
    metadata={}
)
```

### 4.3 PlatformEvent → QQ回复

```python
# Yuki的回复
response = YukiResponse(
    text="主人~人家在呢！有什么事吗？",
    actions=[]
)

# 转换为QQ消息格式
qq_message = {
    "action": "send_group_msg",
    "group_id": event.session_id,
    "message": response.text
}
```

## 5. 扩展字段规范

如果需要添加新的平台，必须提供:

1. **source 名称**: 全小写英文，如 `"telegram"`, `"bilibili"`
2. **转换函数**: `translate_in(raw) -> PlatformEvent` 和 `translate_out(response) -> raw`
3. **metadata 文档**: 说明该平台特有的元数据字段

## 6. 与现有代码的兼容

### 从 chat_history.json 迁移

现有格式:
```json
{
  "chat_id": {
    "messages": [
      {"role": "user", "content": "消息内容", "time": "2026-05-17 14:00"}
    ]
  }
}
```

新格式（保持兼容）:
```json
{
  "chat_id": {
    "platform": "qq",  // 新增
    "messages": [
      {
        "role": "user",
        "content": "消息内容",
        "time": "2026-05-17 14:00",
        "event_type": "message"  // 新增（可选）
      }
    ]
  }
}
```

---

*Created: 2026-05-17 | Phase 0*
