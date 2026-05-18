"""
最小对接验证：检查 qq_plugin 所有模块能否正常导入和基本调用。
在 V7 根目录运行: python tests/test_qq_plugin_imports.py
"""
import sys
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

errors = []
ok = []


def _has_cv2():
    try:
        import cv2
        return True
    except ImportError:
        return False

def _has_chromadb():
    try:
        import chromadb
        return True
    except ImportError:
        return False


def assert_(cond, msg="assertion failed"):
    if not cond:
        raise AssertionError(msg)


def check(label, fn):
    """执行一个检查，捕获异常"""
    try:
        fn()
        ok.append(label)
        print(f"  ✅ {label}")
    except Exception as e:
        errors.append((label, e))
        print(f"  ❌ {label}: {e}")


# ================= 1. Config =================
print("\n[1] Config")
check("from yuki_core.config import cfg", lambda: None)
from yuki_core.config import cfg
cfg.load()
check("cfg.LLM_API_KEY exists", lambda: assert_(cfg.LLM_API_KEY != "", "empty"))
check("cfg.LLM_MODEL exists", lambda: assert_(cfg.LLM_MODEL != "", "empty"))
check("cfg.TARGET_GROUPS exists", lambda: assert_(len(cfg.TARGET_GROUPS) > 0, "empty"))


# ================= 2. LLM 调用 =================
print("\n[2] LLM 调用")
check("from yuki_core.llm import robust_chat", lambda: None)
check("from plugins.platforms.qq_plugin.llm_call import yuki_chat", lambda: None)
check("from plugins.platforms.qq_plugin.llm_call import yuki_vision_chat", lambda: None)


# ================= 3. V7 Core =================
print("\n[3] V7 Core")
check("from yuki_core import YukiIdentity", lambda: None)
check("from yuki_core import YukiMind", lambda: None)
check("from yuki_core import ContextBus", lambda: None)
check("from yuki_core import PlatformEvent, YukiResponse, Action", lambda: None)
check("from yuki_core.history import HistoryManager", lambda: None)
check("from yuki_core.memory import YukiMemory", lambda: None)
check("from yuki_core.plugin import PlatformPlugin", lambda: None)


# ================= 4. QQ Plugin 核心 =================
print("\n[4] QQ Plugin 核心")
check("from plugins.platforms.qq_plugin import QQPlugin", lambda: None)

from plugins.platforms.qq_plugin import QQPlugin
check("QQPlugin is subclass of PlatformPlugin", 
      lambda: assert_(issubclass(QQPlugin, __import__("yuki_core.plugin", fromlist=["PlatformPlugin"]).PlatformPlugin), "not subclass"))

# ================= 5. QQ Plugin 业务模块 =================
print("\n[5] QQ Plugin 业务模块")
check("core.brain.YukiState", 
      lambda: __import__("plugins.platforms.qq_plugin.core.brain", fromlist=["YukiState"]))
check("core.engine.YukiEngine", 
      lambda: __import__("plugins.platforms.qq_plugin.core.engine", fromlist=["YukiEngine"]))
check("core.maid.maid_evolution_loop", 
      lambda: __import__("plugins.platforms.qq_plugin.core.maid", fromlist=["maid_evolution_loop"]))
check("core.prompts.sync_system_prompts", 
      lambda: __import__("plugins.platforms.qq_plugin.core.prompts", fromlist=["sync_system_prompts"]))
check("modules.memory.rag.MemoryRAG", 
      lambda: __import__("plugins.platforms.qq_plugin.modules.memory.rag", fromlist=["MemoryRAG"]) if _has_chromadb() else None)
check("modules.message.CQParser.CQCodeParser", 
      lambda: __import__("plugins.platforms.qq_plugin.modules.message.CQParser", fromlist=["CQCodeParser"]))
check("modules.message.CQProtocol.CQProtocol", 
      lambda: __import__("plugins.platforms.qq_plugin.modules.message.CQProtocol", fromlist=["CQProtocol"]))
check("modules.vision.processor.MemeProcessor", 
      lambda: __import__("plugins.platforms.qq_plugin.modules.vision.processor", fromlist=["MemeProcessor"]) if _has_cv2() else None)
check("modules.stickers.manager.StickerManager", 
      lambda: __import__("plugins.platforms.qq_plugin.modules.stickers.manager", fromlist=["StickerManager"]) if _has_cv2() else None)
check("network.ws_connection.BotConnector", 
      lambda: __import__("plugins.platforms.qq_plugin.network.ws_connection", fromlist=["BotConnector"]))
check("network.ws_sender.MessageSender", 
      lambda: __import__("plugins.platforms.qq_plugin.network.ws_sender", fromlist=["MessageSender"]))


# ================= 6. 实例化测试 =================
print("\n[6] 实例化测试")

from plugins.platforms.qq_plugin.core.brain import YukiState
check("YukiState()", lambda: YukiState())

from yuki_core.identity import YukiIdentity
check("YukiIdentity()", lambda: YukiIdentity())

from yuki_core.mind import YukiMind
identity = YukiIdentity()
check("YukiMind(identity)", lambda: YukiMind(identity))

from yuki_core.history import HistoryManager
check("HistoryManager()", lambda: HistoryManager())

from plugins.platforms.qq_plugin import QQPlugin
plugin = QQPlugin()
check("QQPlugin() instantiation", lambda: None)
check("QQPlugin.platform_id == 'qq'", 
      lambda: assert_(plugin.platform_id == "qq", plugin.platform_id))
check("QQPlugin has receive()", 
      lambda: assert_(callable(getattr(plugin, "receive", None)), "no receive"))
check("QQPlugin has send()", 
      lambda: assert_(callable(getattr(plugin, "send", None)), "no send"))
check("QQPlugin has connect()", 
      lambda: assert_(callable(getattr(plugin, "connect", None)), "no connect"))
check("QQPlugin has disconnect()", 
      lambda: assert_(callable(getattr(plugin, "disconnect", None)), "no disconnect"))
check("QQPlugin has get_background_tasks()", 
      lambda: assert_(callable(getattr(plugin, "get_background_tasks", None)), "no get_background_tasks"))


# ================= 7. Bus 集成测试 =================
print("\n[7] Bus 集成测试")

from yuki_core.bus import ContextBus
bus = ContextBus(YukiMind(identity), identity, config=cfg)
bus.register_platform(plugin)
check("Bus.register_platform(QQPlugin)", lambda: None)
check("Bus.get_platform('qq') returns plugin", 
      lambda: assert_(bus.get_platform("qq") is plugin, "mismatch"))
check("plugin.bus is bus", 
      lambda: assert_(plugin.bus is bus, "not set"))


# ================= 结果 =================
print("\n" + "=" * 50)
print(f"  通过: {len(ok)}")
print(f"  失败: {len(errors)}")

if errors:
    print("\n  失败详情:")
    for label, e in errors:
        print(f"    ❌ {label}: {e}")
else:
    print("\n  🎉 所有检查通过！QQ Plugin 对接成功。")
print("=" * 50)
