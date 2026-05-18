# main.py
# Yuki Agent 入口

"""
Yuki Agent - 多平台 AI Agent 框架
"""

import asyncio
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from yuki_core.logger import setup_logging
setup_logging(debug=True)

from yuki_core.config import cfg
from yuki_core import (
    YukiIdentity, YukiMind, ContextBus,
    PlatformEvent, get_system_prompt,
)
from yuki_core.plugin import load_plugins_from_config
from yuki_core.maid import MaidAgent
from yuki_core.history import HistoryManager
from yuki_core.memory import YukiMemory


def load_plugins_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = str(PROJECT_ROOT / "configs" / "plugins.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def inject_global_config(bus: ContextBus, plugin_config: dict):
    """将 plugins.yaml 的全局配置注入到 QQ 插件"""
    qq = bus.get_platform("qq")
    if qq is None:
        return
    qq.master_id = plugin_config.get("master_id", cfg.TARGET_QQ or 0)
    qq.master_name = plugin_config.get("master_name", cfg.MASTER_NAME)
    qq.robot_name = plugin_config.get("robot_name", cfg.ROBOT_NAME)
    qq.keywords = plugin_config.get("keywords", cfg.KEYWORDS)


async def main():
    print("=" * 60)
    print("  Yuki Agent v0.2.0")
    print("  Phase 2: Plugin 框架 + QQ 迁移")
    print("=" * 60)
    print()
    
    # 加载配置
    cfg.load()
    
    # Core 初始化
    print("[Core] 初始化...")
    identity = YukiIdentity(config_path=str(PROJECT_ROOT / "configs" / "identities.yaml"))
    print(f"  ✅ Identity: {identity.persona.display_name}")
    
    mind = YukiMind(identity, keep_last_dialogue=cfg.KEEP_LAST_DIALOGUE)
    print(f"  ✅ Mind: 精力/活跃度/欲望/生物钟")
    
    memory = YukiMemory(
        vector_db_path=cfg.VECTOR_DB_PATH,
        embed_model_path=cfg.EMBED_MODEL,
        history_file=cfg.HISTORY_FILE,
    )
    print(f"  ✅ Memory: 向量检索 + 关键词补偿")
    
    history = HistoryManager(history_file=cfg.HISTORY_FILE)
    print(f"  ✅ History: 对话历史管理")
    
    maid = MaidAgent()
    print(f"  ✅ Maid: 小女仆系统")
    
    bus = ContextBus(mind, identity, config=cfg)
    bus.history = history
    bus.memory = memory
    print(f"  ✅ Bus: 上下文总线")
    
    print()
    print("[Plugins] 加载...")
    
    plugin_config = load_plugins_config()
    load_plugins_from_config(bus, str(PROJECT_ROOT / "configs" / "plugins.yaml"))
    inject_global_config(bus, plugin_config)
    
    for pid, plugin in bus.platform_plugins.items():
        print(f"  ✅ {plugin.name} ({pid}) v{plugin.version}")
    
    print()
    print("[Core] ✅ 初始化完成")
    print()
    
    # 启动
    print("=" * 60)
    print("  启动 Yuki Agent... 按 Ctrl+C 停止")
    print("=" * 60)
    
    try:
        await bus.start()
    except KeyboardInterrupt:
        print("\n收到中断信号...")
    finally:
        await bus.stop()
        print("Yuki Agent 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nYuki Agent 已退出")
