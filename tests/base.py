"""测试公共准备：确保隔离目录里有一套完整演示数据。"""
from tests import PROJECT

from utils import ensure_files, load_tests, seed_demo_data

PAGES = ["app.py", "pages/1_👥运动员管理.py", "pages/2_📏测试与评估.py",
         "pages/3_📊数据分析.py", "pages/4_⚙️评分标准.py", "pages/5_📝分析报告.py"]


def prepare() -> None:
    ensure_files()
    if load_tests().empty:
        seed_demo_data()


def page_path(rel: str) -> str:
    return str(PROJECT / rel)
