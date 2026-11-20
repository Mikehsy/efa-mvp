"""数据契约与旧数据兼容性回归。"""
import unittest
from pathlib import Path

import pandas as pd

from config import ATHLETE_FILE, STANDARD_FILE, TEST_FILE
from tests.base import PROJECT, prepare
from utils import (ATHLETE_COLS, STANDARD_COLS, TEST_COLS, load_athletes,
                   load_standards, load_tests as load_test_records)


def setUpModule():
    prepare()


class DataContractTests(unittest.TestCase):
    def test_column_contract(self):
        self.assertEqual(list(pd.read_csv(ATHLETE_FILE, encoding="utf-8-sig").columns),
                         ATHLETE_COLS)
        self.assertEqual(list(pd.read_csv(TEST_FILE, encoding="utf-8-sig").columns),
                         TEST_COLS)
        self.assertEqual(list(pd.read_csv(STANDARD_FILE, encoding="utf-8-sig").columns),
                         STANDARD_COLS)

    def test_sport_is_mandatory_and_complete(self):
        a = load_athletes()
        self.assertEqual(a.shape, (10, 8))
        self.assertTrue(a["运动项目"].astype(str).str.strip().ne("").all())

    def test_all_tests_linked_to_sport(self):
        t = load_test_records()
        self.assertEqual(t.shape[0], 582)
        sports = t["运动项目"].astype(str).str.strip()
        self.assertTrue((sports.ne("") & sports.ne("未填写")).all())
        self.assertTrue(t[t["测试类别"] == "FMS"]["运动项目"].astype(str).str.strip().ne("").all())

    def test_standards_rows(self):
        # 用户确认取消大师组：27 项 × 2 性别 × 4 年龄组 = 216 行。
        self.assertEqual(load_standards().shape, (216, 11))

    def test_legacy_192_rows_unchanged(self):
        """只读校验项目真实数据与 v1 备份：旧 192 条前 7 列逐格一致。"""
        baseline = Path(r"C:\Users\10692\CodeBuddy\20261113101802_data_backup_20261113\tests.csv")
        live = PROJECT / "data" / "tests.csv"
        if not baseline.exists() or not live.exists():
            self.skipTest("v1 备份或真实数据不存在，跳过历史一致性校验")
        old = pd.read_csv(baseline, encoding="utf-8-sig")
        now = pd.read_csv(live, encoding="utf-8-sig")
        cols = list(old.columns[:7])
        self.assertEqual(len(old), 192)
        pd.testing.assert_frame_equal(
            now[cols].head(len(old)).astype(str).reset_index(drop=True),
            old[cols].astype(str).reset_index(drop=True))


if __name__ == "__main__":
    unittest.main()
