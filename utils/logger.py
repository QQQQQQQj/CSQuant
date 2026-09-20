"""统一日志：stdlib logging（控制台 + logs/ 滚动文件）。

未引入 loguru 以保持核心零依赖；接口保持一致，后续可平移。
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_initialized = False


def get_logger(name: str = "csquant", level: str = "INFO") -> logging.Logger:
    global _initialized
    logger = logging.getLogger(name)
    if _initialized:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_dir / "csquant.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    _initialized = True
    return logger


def mask_secret(value: str | None) -> str:
    """日志脱敏：只保留后4位。"""
    if not value:
        return "<empty>"
    return "***" + value[-4:] if len(value) > 4 else "***"
