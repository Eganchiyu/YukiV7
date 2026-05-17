# main.py
# Yuki Agent 入口

"""
Yuki Agent - 多平台 AI Agent 框架

本文件是项目入口，目前为占位文件。
待 Phase 1 完成后，将实现完整的启动逻辑。

启动流程:
1. 加载配置
2. 初始化 Core (identity, memory, mind, maid)
3. 创建 ContextBus
4. 加载插件
5. 启动监听
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))


async def main():
    """主函数"""
    print("=" * 50)
    print("  Yuki Agent v0.1.0")
    print("  多平台 AI Agent 框架")
    print("=" * 50)
    print()
    print("  项目正在开发中...")
    print("  详细进度请查看 docs/ARCHITECTURE.md")
    print()
    print("  Phase 0: 文档与准备 ✅")
    print("  Phase 1: Core 抽离  ⏳")
    print("  Phase 2: Plugin 框架 ⏳")
    print()
    
    # TODO: Phase 1 实现
    # from yuki_core import YukiMind, YukiMemory, YukiIdentity, ContextBus
    # from yuki_core.plugin import load_plugins
    #
    # # 1. 初始化Core
    # identity = YukiIdentity.from_config("configs/identities.yaml")
    # memory = YukiMemory()
    # mind = YukiMind(memory, identity)
    #
    # # 2. 初始化总线
    # bus = ContextBus(mind, identity)
    #
    # # 3. 加载插件
    # await load_plugins(bus, "configs/plugins.yaml")
    #
    # # 4. 启动
    # print("Yuki Agent 已启动")
    # await bus.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nYuki Agent 已退出")
