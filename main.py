# main.py
# Yuki Agent 入口

"""
Yuki Agent - 多平台 AI Agent 框架
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from yuki_core import (
    YukiIdentity, YukiMind, YukiMemory, ContextBus,
    PlatformEvent, get_system_prompt
)


async def main():
    """主函数"""
    print("=" * 60)
    print("  Yuki Agent v0.1.0")
    print("  多平台 AI Agent 框架")
    print("=" * 60)
    print()
    
    # Phase 1: Core 抽离验证
    print("[Phase 1] 验证 Core 模块...")
    
    # 1. 初始化身份
    identity = YukiIdentity()
    print(f"  ✅ Identity: {identity.persona.display_name}")
    print(f"     人格: {', '.join(identity.persona.personality)}")
    
    # 2. 测试 system prompt 生成
    qq_prompt = get_system_prompt("group")
    web_prompt = get_system_prompt("private")
    print(f"  ✅ System Prompt: QQ群聊 ({len(qq_prompt)} chars), 私聊 ({len(web_prompt)} chars)")
    
    # 3. 初始化决策引擎
    mind = YukiMind(identity)
    print(f"  ✅ Mind: 精力系统、活跃度追踪、欲望计算、生物钟")
    
    # 4. 初始化记忆系统（惰性初始化）
    memory = YukiMemory()
    print(f"  ✅ Memory: 向量检索 + 关键词补偿")
    
    # 5. 初始化小女仆
    from yuki_core.maid import MaidAgent
    maid = MaidAgent()
    print(f"  ✅ Maid: 自主编程进化系统")
    
    # 6. 初始化总线
    bus = ContextBus(mind, identity)
    print(f"  ✅ Bus: 上下文总线")
    
    print()
    print("[Phase 1] ✅ Core 抽离完成！")
    print()
    
    # 演示事件处理
    print("[Demo] 模拟事件处理...")
    event = PlatformEvent(
        source="qq",
        event_type="message",
        content="Yuki在吗",
        user_id="123456",
        user_name="主人",
        session_id="789012",
        session_type="group"
    )
    
    response = await mind.process(event)
    print(f"  事件: {event.content}")
    print(f"  回复: {response.text}")
    print(f"  状态: {response.metadata}")
    
    print()
    print("=" * 60)
    print("  下一步: Phase 2 - Plugin 框架 + QQ 迁移")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nYuki Agent 已退出")
