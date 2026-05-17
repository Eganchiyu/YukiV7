# yuki_core/config.py
"""
Yuki Agent 配置中心

从 configs/config.yaml 读取所有配置
简洁实现，不过度工程化
"""

import os
import yaml
import logging
from pathlib import Path

logger = logging.getLogger("config")

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent


class Config:
    """配置单例"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def load(self, path: str = None):
        """加载配置文件"""
        if path is None:
            path = str(PROJECT_ROOT / "configs" / "config.yaml")

        if not os.path.exists(path):
            # 尝试 example
            example = path.replace("config.yaml", "config.example.yaml")
            if os.path.exists(example):
                logger.warning(f"[Config] config.yaml 不存在，使用 config.example.yaml")
                path = example
            else:
                logger.error(f"[Config] 配置文件不存在: {path}")
                self._data = {}
                self._loaded = True
                return

        with open(path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

        self._loaded = True
        logger.info(f"[Config] 已加载配置: {path}")

    def _check(self):
        if not self._loaded:
            self.load()

    def get(self, *keys, default=None):
        """嵌套取值: cfg.get("api", "llm_api_key")"""
        self._check()
        d = self._data
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    # ================= 快捷属性 =================

    @property
    def ROBOT_NAME(self) -> str:
        return self.get("robot_name", default="yuki")

    @property
    def MASTER_NAME(self) -> str:
        return self.get("master_name", default="主人")

    @property
    def LLM_PLATFORM(self) -> str:
        return self.get("api", "llm_platform", default="deepseek")

    @property
    def LLM_API_KEY(self) -> str:
        return self.get("api", "llm_api_key", default="")

    @property
    def LLM_BASE_URL(self) -> str:
        return self.get("api", "llm_base_url", default="")

    @property
    def LLM_MODEL(self) -> str:
        return self.get("model", "llm", default="deepseek-chat")

    @property
    def BACKUP_PLATFORM(self) -> str:
        return self.get("api", "backup_platform", default="deepseek")

    @property
    def BACKUP_API_KEY(self) -> str:
        return self.get("api", "backup_api_key", default="")

    @property
    def BACKUP_BASE_URL(self) -> str:
        return self.get("api", "backup_base_url", default="")

    @property
    def BACKUP_MODEL(self) -> str:
        return self.get("model", "backup", default="deepseek-chat")

    @property
    def VISION_PLATFORM(self) -> str:
        return self.get("api", "vision_platform", default="dashscope")

    @property
    def VISION_API_KEY(self) -> str:
        return self.get("api", "vision_api_key", default="")

    @property
    def VISION_MODEL(self) -> str:
        return self.get("model", "vision", default="")

    @property
    def DISABLE_THINKING(self) -> bool:
        return self.get("model", "disable_thinking", default=True)

    @property
    def NAPCAT_WS_URL(self) -> str:
        return self.get("connection", "napcat_ws_url", default="ws://localhost:3001")

    @property
    def NAPCAT_WS_TOKEN(self) -> str:
        return self.get("connection", "napcat_ws_token", default="")

    @property
    def TARGET_QQ(self) -> int:
        return self.get("target", "qq", default=0)

    @property
    def TARGET_GROUPS(self) -> list:
        groups = self.get("target", "groups", default=[])
        if isinstance(groups, str):
            return [int(g.strip()) for g in groups.split(",") if g.strip()]
        return [int(g) for g in groups] if groups else []

    @property
    def DEBOUNCE_TIME(self) -> float:
        return self.get("timing", "debounce_time", default=32)

    @property
    def MAX_MESSAGE_LENGTH(self) -> int:
        return self.get("max_message_length", default=150)

    @property
    def INITIAL_ENERGY(self) -> float:
        return self.get("energy", "initial", default=100)

    @property
    def MAX_ENERGY(self) -> float:
        return self.get("energy", "max", default=100.0)

    @property
    def RECOVERY_PER_MIN(self) -> float:
        return self.get("energy", "recovery_per_min", default=0.8)

    @property
    def COST_PER_REPLY(self) -> float:
        return self.get("energy", "cost_per_reply", default=6)

    @property
    def MIN_ACTIVE_ENERGY(self) -> float:
        return self.get("energy", "min_active", default=25)

    @property
    def SENSITIVITY(self) -> float:
        return self.get("attention", "sensitivity", default=0.12)

    @property
    def DECAY_LEVEL(self) -> float:
        return self.get("attention", "decay_level", default=0.65)

    @property
    def SIGMOID_CENTRE(self) -> float:
        return self.get("attention", "sigmoid_centre", default=50.0)

    @property
    def SIGMOID_ALPHA(self) -> float:
        return self.get("attention", "sigmoid_alpha", default=0.08)

    @property
    def KEYWORDS(self) -> list:
        base = list(self.get("attention", "keywords", default=["主人", "哥哥"]))
        robot = self.ROBOT_NAME
        if robot and robot not in base:
            base.append(robot)
        return base

    @property
    def DIARY_IDLE_SECONDS(self) -> int:
        return self.get("diary", "idle_seconds", default=120)

    @property
    def DIARY_MIN_TURNS(self) -> int:
        return self.get("diary", "min_turns", default=15)

    @property
    def DIARY_MAX_LENGTH(self) -> int:
        return self.get("diary", "max_length", default=50)

    @property
    def RETRIEVAL_TOP_K(self) -> int:
        return self.get("rag", "retrieval_top_k", default=20)

    @property
    def KEEP_LAST_DIALOGUE(self) -> int:
        return self.get("rag", "keep_last_dialogue", default=10)

    @property
    def VECTOR_DB_PATH(self) -> str:
        p = self.get("paths", "vector_db", default="./yuki_memory")
        if p and p.startswith("./"):
            return str(PROJECT_ROOT / p[2:])
        return p

    @property
    def EMBED_MODEL(self) -> str:
        p = self.get("paths", "embed_model", default="./models/text2vec-base-chinese")
        if p and p.startswith("./"):
            return str(PROJECT_ROOT / p[2:])
        return p

    @property
    def HISTORY_FILE(self) -> str:
        p = self.get("paths", "history_file", default="./data/chat_history.json")
        if p and p.startswith("./"):
            return str(PROJECT_ROOT / p[2:])
        return p

    @property
    def LOG_FILE(self) -> str:
        p = self.get("paths", "log_file", default="./data/yuki_log.txt")
        if p and p.startswith("./"):
            return str(PROJECT_ROOT / p[2:])
        return p

    @property
    def DEBUG(self) -> bool:
        return self.get("debug", default=True)


# 全局实例
cfg = Config()
