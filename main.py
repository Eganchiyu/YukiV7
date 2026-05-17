# main.py
# Yuki Agent 入口 - Phase 2: Plugin 框架 + QQ 迁移

"""
Yuki Agent - 多平台 AI Agent 框架

启动流程：
1. 初始化 Core (Identity, Mind, Memory, Maid)
2. 加载插件 (从 configs/plugins.yaml)
3. 启动 ContextBus (连接 Core 和 Plugins)
4. 进入事件循环
"""

import asyncio
import sys
import yaml
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from yuki_core import (
    YukiIdentity, YukiMind, YukiMemory, ContextBus,
    PlatformEvent, get_system_prompt,
)
from yuki_core.plugin import load_plugins_from_config
from yuki_core.maid import MaidAgent


def load_plugins_config(config_path: str = None) -> dict:
    """加载插件配置"""
    if config_path is None:
        config_path = str(PROJECT_ROOT / "configs" / "plugins.yaml")
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def inject_plugin_config(bus: ContextBus, plugin_config: dict):
    """
    将 plugins.yaml 中的全局配置注入到 QQ 插件
    
    QQ 插件需要 master_id, master_name, keywords 等全局配置
    """
    qq_plugin = bus.get_platform("qq")
    if qq_plugin is None:
        return
    
    # 注入全局配置到 QQ 插件
    qq_plugin.master_id = plugin_config.get("master_id", 0)
    qq_plugin.master_name = plugin_config.get("master_name", "主人")
    qq_plugin.robot_name = plugin_config.get("robot_name", "yuki")
    qq_plugin.keywords = plugin_config.get("keywords", ["主人", "哥哥", "yuki"])


async def main():
    """主函数"""
    print("=" * 60)
    print("  Yuki Agent v0.2.0")
    print("  Phase 2: Plugin 框架 + QQ 迁移")
    print("=" * 60)
    print()
    
    # ==================== Phase 1: Core 初始化 ====================
    print("[Phase 1] 初始化 Core 模块...")
    
    # 1. 身份
    identity = YukiIdentity()
    print(f"  ✅ Identity: {identity.persona.display_name}")
    print(f"     人格: {', '.join(identity.persona.personality)}")
    
    # 2. System Prompt
    qq_prompt = get_system_prompt("group")
    private_prompt = get_system_prompt("private")
    print(f"  ✅ System Prompt: QQ群聊 ({len(qq_prompt)} chars), 私聊 ({len(private_prompt)} chars)")
    
    # 3. 决策引擎
    mind = YukiMind(identity)
    print(f"  ✅ Mind: 精力系统、活跃度追踪、欲望计算、生物钟")
    
    # 4. 记忆系统
    memory = YukiMemory()
    print(f"  ✅ Memory: 向量检索 + 关键词补偿")
    
    # 5. 小女仆
    maid = MaidAgent()
    print(f"  ✅ Maid: 自主编程进化系统")
    
    # 6. 上下文总线
    bus = ContextBus(mind, identity)
    print(f"  ✅ Bus: 上下文总线")
    
    print()
    print("[Phase 1] ✅ Core 初始化完成！")
    print()
    
    # ==================== Phase 2: 加载插件 ====================
    print("[Phase 2] 加载插件...")
    
    # 加载插件配置
    plugin_config = load_plugins_config()
    
    # 动态加载插件到 Bus
    load_plugins_from_config(bus, str(PROJECT_ROOT / "configs" / "plugins.yaml"))
    
    # 注入全局配置（master_id 等）到 QQ 插件
    inject_plugin_config(bus, plugin_config)
    
    # 打印加载结果
    for pid, plugin in bus.platform_plugins.items():
        print(f"  ✅ 平台插件: {plugin.name} ({pid}) v{plugin.version}")
    for cid, cap in bus.capability_plugins.items():
        print(f"  ✅ 能力插件: {cap.name}")
    
    if not bus.platform_plugins:
        print("  ⚠️  没有启用的平台插件")
    
    print()
    print("[Phase 2] ✅ 插件加载完成！")
    print()
    
    # ==================== 启动事件循环 ====================
    print("=" * 60)
    print("  启动 Yuki Agent...")
    print("  按 Ctrl+C 停止")
    print("=" * 60)
    print()
    
    try:
        await bus.start()
    except KeyboardInterrupt:
        print("\n收到中断信号，正在停止...")
    finally:
        await bus.stop()
        print("Yuki Agent 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nYuki Agent 已退出")
