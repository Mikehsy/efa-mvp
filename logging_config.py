"""结构化日志与操作审计。

* logs/app.log   —— 运行日志（异常栈、警告、关键信息），按大小轮转。
* logs/audit.log —— 追加写操作留痕：时间 / 操作 / 页面 / 对象 / 结果 / 附加字段。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import LOG_BACKUP_COUNT, LOG_DIR, LOG_LEVEL, LOG_MAX_BYTES

_lock = threading.Lock()
_loggers: dict[str, logging.Logger] = {}


def _build(name: str, filename: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"efa.{name}")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    handler = RotatingFileHandler(
        LOG_DIR / filename, maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "app") -> logging.Logger:
    """返回带轮转文件 handler 的 logger；重复调用不会重复添加 handler。"""
    with _lock:
        if name not in _loggers:
            _loggers[name] = _build(name, "audit.log" if name == "audit" else "app.log")
        return _loggers[name]


def audit(action: str, *, page: str = "", target: str = "",
          result: str = "ok", **fields) -> None:
    """记录一次写操作，字段值统一序列化为 JSON 便于检索。"""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action, "page": page, "target": target, "result": result,
        **fields,
    }
    get_logger("audit").info(json.dumps(record, ensure_ascii=False, default=str))


def log_exception(action: str, *, page: str = "", target: str = "", **fields) -> None:
    """记录异常及其完整堆栈，供故障定位；页面仍负责给出用户可读提示。"""
    suffix = json.dumps(fields, ensure_ascii=False, default=str)
    get_logger().exception(f"{action} 失败 | page={page} target={target} | {suffix}")
    audit(action, page=page, target=target, result="error", **fields)
