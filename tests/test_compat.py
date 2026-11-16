"""兼容性回归（可自动化部分）。

Python 3.11 / Windows 10 / Chrome / Edge 等跨环境项需按兼容矩阵人工验证，
见 docs/测试与性能基线.md。
"""
import sys
import unittest
from pathlib import Path

import pandas as pd
import plotly
import streamlit

from tests.base import PROJECT, prepare
from utils import load_athletes


def setUpModule():
    prepare()


class CompatibilityTests(unittest.TestCase):
    def test_python_version(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 11))

    def test_streamlit_in_declared_range(self):
        major_minor = tuple(int(x) for x in streamlit.__version__.split(".")[:2])
        self.assertGreaterEqual(major_minor, (1, 30))
        self.assertLess(major_minor, (2, 0))

    def test_lock_matches_runtime(self):
        lock = (PROJECT / "requirements.lock").read_text(encoding="utf-8-sig")
        for name, version in (("streamlit", streamlit.__version__),
                              ("pandas", pd.__version__),
                              ("plotly", plotly.__version__)):
            with self.subTest(package=name):
                self.assertIn(f"{name}=={version}", lock)

    def test_utf8_csv_chinese_roundtrip(self):
        names = load_athletes()["姓名"].astype(str).tolist()
        self.assertIn("林扬", names)
        self.assertIn("陈曦", names)

    def test_config_toml_exists(self):
        config = PROJECT / ".streamlit" / "config.toml"
        self.assertTrue(config.exists())
        self.assertIn("gatherUsageStats = false", config.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
