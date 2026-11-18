"""集中配置：路径、运行开关、日志与可靠性参数。

所有项均可用环境变量覆盖，部署时无需修改代码。
开发默认允许演示数据；生产部署脚本会把 EFA_ALLOW_DEMO_DATA 设为 0。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).resolve()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = _path("EFA_DATA_DIR", BASE_DIR / "data")
BACKUP_DIR = _path("EFA_BACKUP_DIR", BASE_DIR / "backups")
LOG_DIR = _path("EFA_LOG_DIR", BASE_DIR / "logs")
SNAPSHOT_DIR = DATA_DIR / ".snapshots"

ATHLETE_FILE = DATA_DIR / "athletes.csv"
TEST_FILE = DATA_DIR / "tests.csv"
STANDARD_FILE = DATA_DIR / "standards.csv"
LOCK_FILE = DATA_DIR / ".write.lock"

ALLOW_DEMO_DATA = _flag("EFA_ALLOW_DEMO_DATA", True)
LOG_LEVEL = os.getenv("EFA_LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = int(os.getenv("EFA_LOG_MAX_BYTES", 5 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.getenv("EFA_LOG_BACKUP_COUNT", 10))

LOCK_TIMEOUT = float(os.getenv("EFA_LOCK_TIMEOUT", 10))
STALE_LOCK_SECONDS = float(os.getenv("EFA_STALE_LOCK_SECONDS", 30))
SNAPSHOT_KEEP = int(os.getenv("EFA_SNAPSHOT_KEEP", 20))
BACKUP_KEEP = int(os.getenv("EFA_BACKUP_KEEP", 30))

APP_VERSION = "1.0.0-dev"
STANDARDS_VERSION = os.getenv(
    "EFA_STANDARDS_VERSION",
    "确认常模 v1（2026-11-18：取消大师组+有出处项已替换，其余仍为示例，待队内实测）")

DATA_FILES = (ATHLETE_FILE, TEST_FILE, STANDARD_FILE)
