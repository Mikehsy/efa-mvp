"""固化测试的运行环境。

全程使用隔离临时目录（data / backups / logs），绝不读写项目真实 data。
环境变量必须在任何 utils / config 导入之前设置，因此放在包初始化里。
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

TMP = Path(tempfile.mkdtemp(prefix="efa_tests_"))
os.environ["EFA_DATA_DIR"] = str(TMP / "data")
os.environ["EFA_BACKUP_DIR"] = str(TMP / "backups")
os.environ["EFA_LOG_DIR"] = str(TMP / "logs")
os.environ["EFA_ALLOW_DEMO_DATA"] = "1"
# 测试环境禁用演示登录闸口：指向隔离目录中不存在的文件（不读写项目 demo_users.json）。
os.environ["EFA_DEMO_USERS"] = str(TMP / "demo_users_disabled.json")

atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
