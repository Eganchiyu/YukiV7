# yuki_core/logger.py
"""
日志配置

简洁实现：控制台彩色 + 文件记录
"""

import os
import sys
import time
import logging
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# 日志级别
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


class ColorFormatter(logging.Formatter):
    """控制台彩色 Formatter"""
    
    COLORS = {
        "DEBUG":    "\033[36m",    # 青
        "INFO":     "\033[32m",    # 绿
        "WARNING":  "\033[33m",    # 黄
        "ERROR":    "\033[31m",    # 红
        "CRITICAL": "\033[35m",    # 紫
    }
    RESET = "\033[0m"
    
    # 第三方库缩写
    SHORT_NAMES = {
        "napcat_ws": "WS",
        "message_sender": "Send",
        "cq_parser": "CQ",
        "group_manager": "Grp",
        "qq_plugin": "QQ",
        "mind": "Mind",
        "bus": "Bus",
        "memory": "Mem",
        "history": "Hist",
        "llm": "LLM",
        "config": "Cfg",
        "identity": "ID",
        "plugin": "Plug",
        "maid": "Maid",
    }
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET
        
        # 时间
        t = time.strftime("%H:%M:%S", time.localtime(record.created))
        ms = f".{int(record.msecs):03d}"
        
        # 模块名缩写
        name = record.name
        short = self.SHORT_NAMES.get(name, name.split(".")[-1][:5])
        
        # 消息
        msg = record.getMessage()
        
        return f"{color}{t}{ms} [{short:>5}]{reset} {msg}"


class FileFormatter(logging.Formatter):
    """文件 Formatter（带完整时间戳）"""
    
    def format(self, record):
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        ms = f".{int(record.msecs):03d}"
        level = record.levelname.ljust(7)
        name = record.name
        msg = record.getMessage()
        return f"{t}{ms} {level} [{name}] {msg}"


def setup_logging(debug: bool = True):
    """
    配置日志系统
    
    - 控制台：彩色，INFO 级别（debug=True 时 DEBUG）
    - 文件：完整格式，DEBUG 级别，自动归档
    """
    root = logging.getLogger()
    root.setLevel(TRACE)
    
    # 清除已有 handler
    root.handlers.clear()
    
    # ---- 控制台 ----
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(ColorFormatter())
    root.addHandler(console)
    
    # ---- 文件 ----
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    
    log_file = logs_dir / "yuki.log"
    
    # 归档旧日志
    _archive_log(logs_dir)
    
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileFormatter())
    root.addHandler(file_handler)
    
    # 静默第三方库
    for ns in ["gradio", "httpx", "httpcore", "uvicorn", "fastapi",
               "watchfiles", "PIL", "markdown_it", "starlette",
               "chromadb", "sentence_transformers", "httpcore"]:
        logging.getLogger(ns).setLevel(logging.WARNING)
    
    logging.getLogger("websockets").setLevel(logging.INFO)
    
    logger = logging.getLogger("logger")
    logger.info(f"日志系统已启动 (debug={debug})")
    logger.info(f"日志文件: {log_file}")


def _archive_log(logs_dir: Path, keep: int = 30):
    """启动时归档旧日志"""
    log_file = logs_dir / "yuki.log"
    if not log_file.exists() or log_file.stat().st_size == 0:
        return
    
    mtime = datetime.datetime.fromtimestamp(log_file.stat().st_mtime)
    archive = logs_dir / f"yuki_{mtime.strftime('%Y%m%d_%H%M%S')}.log"
    
    counter = 1
    while archive.exists():
        archive = logs_dir / f"yuki_{mtime.strftime('%Y%m%d_%H%M%S')}_{counter}.log"
        counter += 1
    
    log_file.rename(archive)
    
    # 清理旧归档
    archives = sorted(
        [f for f in logs_dir.glob("yuki_*.log")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old in archives[keep:]:
        old.unlink()
