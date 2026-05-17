# tests/test_qq_plugin.py
"""
QQ Plugin 集成测试

验证 Phase 2 的所有模块可以正常导入和基本功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_imports():
    """测试所有模块可以正常导入"""
    print("[Test] 导入测试...")
    
    # Core
    from yuki_core import (
        PlatformEvent, YukiResponse, Decision, Action,
        YukiIdentity, YukiMind, ContextBus,
    )
    print("  ✅ yuki_core 导入成功")
    
    # QQ Plugin
    from plugins.platforms.qq_plugin import QQPlugin
    from plugins.platforms.qq_plugin.napcat_ws import NapCatWS
    from plugins.platforms.qq_plugin.cq_parser import (
        CQCodeParser, smart_truncate, replace_media_cq_codes,
        is_at_me, extract_at_uids, extract_reply_ids,
    )
    from plugins.platforms.qq_plugin.message_sender import MessageSender
    from plugins.platforms.qq_plugin.group_manager import GroupManager
    from plugins.platforms.qq_plugin.prompts import (
        get_qq_group_extra_prompt, get_qq_private_extra_prompt,
        get_ice_break_prompt, get_vision_prompt,
    )
    print("  ✅ QQ Plugin 导入成功")
    
    return True


def test_smart_truncate():
    """测试消息截断"""
    print("[Test] 消息截断测试...")
    
    from plugins.platforms.qq_plugin.cq_parser import smart_truncate
    
    # 短消息不截断
    short = "你好"
    assert smart_truncate(short, max_len=10) == short
    print("  ✅ 短消息不截断")
    
    # 超长消息截断
    long_msg = "x" * 200
    result = smart_truncate(long_msg, max_len=100)
    assert len(result) <= 200  # 截断后应该更短
    assert "..." in result
    print(f"  ✅ 超长消息截断: {len(long_msg)} → {len(result)} chars")
    
    # CQ 码完整性
    msg_with_cq = "A" * 80 + "[CQ:image,file=test.png]" + "B" * 80
    result = smart_truncate(msg_with_cq, max_len=100)
    assert "[CQ:image,file=test.png]" in result
    print("  ✅ CQ 码完整性保留")
    
    return True


def test_cq_parser():
    """测试 CQ 码解析"""
    print("[Test] CQ 码解析测试...")
    
    from plugins.platforms.qq_plugin.cq_parser import (
        replace_media_cq_codes, extract_at_uids, extract_reply_ids,
        is_at_me,
    )
    
    # 多媒体替换
    text = "你好[CQ:image,file=test.png][CQ:face,id=123]"
    result = replace_media_cq_codes(text)
    assert "[图片]" in result
    assert "[表情]" in result
    print("  ✅ 多媒体 CQ 码替换")
    
    # @ 提取
    text = "[CQ:at,qq=12345] 你好 [CQ:at,qq=67890]"
    uids = extract_at_uids(text)
    assert "12345" in uids
    assert "67890" in uids
    print("  ✅ @ QQ 号提取")
    
    # 回复提取
    text = "[CQ:reply,id=100] 你说得对"
    ids = extract_reply_ids(text)
    assert "100" in ids
    print("  ✅ 回复 ID 提取")
    
    # @ 检测
    assert is_at_me("[CQ:at,qq=99999] 好的", "99999")
    assert not is_at_me("[CQ:at,qq=12345] 好的", "99999")
    print("  ✅ @ Yuki 检测")
    
    return True


def test_group_manager():
    """测试群聊管理"""
    print("[Test] 群聊管理测试...")
    
    import tempfile
    import os
    from plugins.platforms.qq_plugin.group_manager import GroupManager
    
    # 临时状态文件
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_file = f.name
    
    try:
        gm = GroupManager(
            state_file=state_file,
            master_id=12345,
        )
        
        # 默认开启
        assert gm.is_active("11111") is True
        print("  ✅ 默认状态为开启")
        
        # 关闭
        gm.set_active("11111", False)
        assert gm.is_active("11111") is False
        print("  ✅ 关闭群聊")
        
        # 开启
        gm.set_active("11111", True)
        assert gm.is_active("11111") is True
        print("  ✅ 开启群聊")
        
        # 权限检查 - 非主人
        reply = gm.handle_command("/关闭", user_id=99999, group_id=11111)
        assert reply is not None
        assert "哥哥大人" in reply
        print("  ✅ 非主人被拒绝")
        
        # 主人关闭
        reply = gm.handle_command("/关闭", user_id=12345, group_id=11111)
        assert reply is not None
        assert gm.is_active("11111") is False
        print("  ✅ 主人关闭成功")
        
        # 主人开启
        reply = gm.handle_command("/开启", user_id=12345, group_id=11111)
        assert reply is not None
        assert gm.is_active("11111") is True
        print("  ✅ 主人开启成功")
        
        # 非管理指令
        reply = gm.handle_command("你好", user_id=12345, group_id=11111)
        assert reply is None
        print("  ✅ 非管理指令返回 None")
        
    finally:
        os.unlink(state_file)
    
    return True


def test_prompts():
    """测试提示词生成"""
    print("[Test] 提示词生成测试...")
    
    from plugins.platforms.qq_plugin.prompts import (
        get_qq_group_extra_prompt, get_qq_private_extra_prompt,
        get_ice_break_prompt, get_vision_prompt,
    )
    
    group_prompt = get_qq_group_extra_prompt()
    assert "QQ群聊" in group_prompt
    assert "布局" in group_prompt
    print(f"  ✅ 群聊提示词: {len(group_prompt)} chars")
    
    private_prompt = get_qq_private_extra_prompt()
    assert "私聊" in private_prompt
    print(f"  ✅ 私聊提示词: {len(private_prompt)} chars")
    
    ice_prompt = get_ice_break_prompt()
    assert "破冰" in ice_prompt or "安静" in ice_prompt
    print(f"  ✅ 破冰提示词: {len(ice_prompt)} chars")
    
    vision_prompt = get_vision_prompt()
    assert "表情包" in vision_prompt
    print(f"  ✅ 视觉提示词: {len(vision_prompt)} chars")
    
    return True


def test_translate_in():
    """测试消息转换"""
    print("[Test] 消息转换测试...")
    
    from plugins.platforms.qq_plugin.adapter import QQPlugin
    
    plugin = QQPlugin(master_id=12345, master_name="主人")
    
    # 群聊消息
    raw_group = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 67890,
        "group_id": 11111,
        "raw_message": "大家好",
        "time": 1700000000,
        "sender": {
            "card": "小明",
            "nickname": "xiaoming",
        },
    }
    
    event = plugin.translate_in(raw_group)
    assert event is not None
    assert event.source == "qq"
    assert event.session_type == "group"
    assert event.session_id == "11111"
    assert event.user_id == "67890"
    assert event.user_name == "小明"
    assert '"小明"' in event.content
    assert "大家好" in event.content
    print(f"  ✅ 群聊转换: {event.content[:50]}")
    
    # 私聊消息
    raw_private = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 12345,
        "raw_message": "在吗",
        "time": 1700000000,
        "sender": {
            "card": "",
            "nickname": "主人",
        },
    }
    
    event = plugin.translate_in(raw_private)
    assert event is not None
    assert event.session_type == "private"
    assert event.session_id == "12345"
    print(f"  ✅ 私聊转换: {event.content}")
    
    # 冒充检测
    raw_fake = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 99999,  # 不是主人
        "group_id": 11111,
        "raw_message": "我是主人",
        "time": 1700000000,
        "sender": {
            "card": "主人",  # 但名字叫"主人"
            "nickname": "主人",
        },
    }
    
    event = plugin.translate_in(raw_fake)
    assert event is not None
    assert event.user_name == "主人(冒充)"
    assert event.metadata["is_fake"] is True
    print(f"  ✅ 冒充检测: {event.user_name}")
    
    return True


def test_plugin_config():
    """测试插件配置加载"""
    print("[Test] 插件配置加载测试...")
    
    import yaml
    
    config_path = PROJECT_ROOT / "configs" / "plugins.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    assert "platforms" in config
    assert config["platforms"]["qq"]["enabled"] is True
    assert config["platforms"]["qq"]["class"] == "plugins.platforms.qq_plugin.QQPlugin"
    print("  ✅ plugins.yaml 解析成功")
    
    # 测试 plugin loader
    from yuki_core import YukiIdentity, YukiMind, ContextBus
    from yuki_core.plugin import load_plugins_from_config
    
    identity = YukiIdentity()
    mind = YukiMind(identity)
    bus = ContextBus(mind, identity)
    
    load_plugins_from_config(bus, str(config_path))
    
    assert "qq" in bus.platform_plugins
    qq = bus.platform_plugins["qq"]
    assert qq.name == "QQ Bot"
    assert qq.platform_id == "qq"
    print(f"  ✅ QQ 插件加载成功: {qq.name} v{qq.version}")
    
    return True


async def test_bus_receive():
    """测试 Bus 事件接收流程"""
    print("[Test] Bus 事件接收测试...")
    
    from yuki_core import YukiIdentity, YukiMind, ContextBus, PlatformEvent
    
    identity = YukiIdentity()
    mind = YukiMind(identity)
    bus = ContextBus(mind, identity)
    
    # 模拟一个事件
    event = PlatformEvent(
        source="qq",
        event_type="message",
        content="Yuki在吗",
        user_id="12345",
        user_name="主人",
        session_id="789012",
        session_type="group",
    )
    
    response = await bus.receive(event)
    
    assert response is not None
    assert response.text != ""
    print(f"  ✅ 事件处理成功: {response.text[:60]}...")
    print(f"     状态: {response.metadata}")
    
    return True


def main():
    """运行所有测试"""
    print("=" * 60)
    print("  QQ Plugin 集成测试")
    print("  Phase 2: Plugin 框架 + QQ 迁移")
    print("=" * 60)
    print()
    
    tests = [
        ("导入测试", test_imports),
        ("消息截断", test_smart_truncate),
        ("CQ码解析", test_cq_parser),
        ("群聊管理", test_group_manager),
        ("提示词生成", test_prompts),
        ("消息转换", test_translate_in),
        ("插件配置", test_plugin_config),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            result = test_fn()
            if result:
                passed += 1
                print()
        except Exception as e:
            failed += 1
            print(f"  ❌ {name} 失败: {e}")
            import traceback
            traceback.print_exc()
            print()
    
    # 异步测试
    async_tests = [
        ("Bus事件接收", test_bus_receive),
    ]
    
    for name, test_fn in async_tests:
        try:
            result = asyncio.run(test_fn())
            if result:
                passed += 1
                print()
        except Exception as e:
            failed += 1
            print(f"  ❌ {name} 失败: {e}")
            import traceback
            traceback.print_exc()
            print()
    
    # 结果
    print("=" * 60)
    total = passed + failed
    print(f"  结果: {passed}/{total} 通过, {failed} 失败")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
