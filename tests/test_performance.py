"""性能回归：582 条基线 + 5000 条合成数据（验收标准 load_tests P95 ≤ 1.5 s）。"""
import time
import unittest

import pandas as pd

from config import TEST_FILE
from reports import TEAM_MODE, generate_report
from tests.base import prepare
from utils import (create_backup, item_catalog, load_athletes,
                   load_tests as load_test_records, restore_backup)


def setUpModule():
    prepare()


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        prepare()
        self.backup = create_backup("perf_case")

    def tearDown(self):
        restore_backup(self.backup)

    def test_582_rows_baseline(self):
        start = time.perf_counter()
        data = load_test_records()
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertEqual(len(data), 582)
        self.assertLess(elapsed_ms, 1000, f"582 条加载耗时 {elapsed_ms:.0f} ms")

    def test_5000_rows_within_budget(self):
        base = pd.read_csv(TEST_FILE, encoding="utf-8-sig")
        reps = 5000 // len(base) + 1
        big = pd.concat([base] * reps, ignore_index=True).head(5000).copy()
        big["记录ID"] = range(1, len(big) + 1)
        big.to_csv(TEST_FILE, index=False, encoding="utf-8-sig")

        start = time.perf_counter()
        data = load_test_records()
        load_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        generate_report(data, load_athletes(), item_catalog(), mode=TEAM_MODE)
        report_ms = (time.perf_counter() - start) * 1000

        self.assertEqual(len(data), 5000)
        self.assertLess(load_ms, 1500, f"5000 条加载耗时 {load_ms:.0f} ms")
        self.assertLess(report_ms, 1000, f"5000 条报告耗时 {report_ms:.0f} ms")


if __name__ == "__main__":
    unittest.main()
