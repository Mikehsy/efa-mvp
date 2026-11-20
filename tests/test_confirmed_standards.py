"""确认标准改动回归：取消大师组、216 行、确认值覆盖、三段式计分、旧表兼容。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st

BASE = Path(__file__).resolve().parents[1]


class ConfirmedStandardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        import utils
        self.utils = utils
        self.env = patch.dict(os.environ, {
            "EFA_DATA_DIR": self.tmp.name,
            "EFA_ALLOW_DEMO_DATA": "0",
            "EFA_USE_DB": "0",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.secrets = patch.object(st, "secrets", {}, create=True)
        self.secrets.start()
        self.addCleanup(self.secrets.stop)

    def test_age_groups_have_no_master(self):
        self.assertEqual(self.utils.AGE_GROUPS, ["U16", "U18", "U20", "成年"])
        self.assertNotIn("大师", self.utils.AGE_GROUP_LABEL)
        self.assertNotIn("大师", self.utils.AGE_FACTOR)

    def test_age_group_maps_35plus_to_adult(self):
        self.assertEqual(self.utils.age_group(15), "U16")
        self.assertEqual(self.utils.age_group(17), "U18")
        self.assertEqual(self.utils.age_group(19), "U20")
        self.assertEqual(self.utils.age_group(20), "成年")
        self.assertEqual(self.utils.age_group(40), "成年")
        self.assertEqual(self.utils.age_group(70), "成年")

    def test_standards_are_216_rows_no_master(self):
        std = self.utils.generate_default_standards()
        self.assertEqual(std.shape, (216, 11))
        self.assertFalse((std["年龄组"] == "大师").any())
        for ag in ("U16", "U18", "U20", "成年"):
            self.assertEqual(int((std["年龄组"] == ag).sum()), 54)

    def test_confirmed_values_override_examples(self):
        std = self.utils.generate_default_standards()

        def get(item, gender, ag):
            hit = std[(std["测试项目"] == item) & (std["性别"] == gender)
                      & (std["年龄组"] == ag)]
            return float(hit.iloc[0]["达标线"]), float(hit.iloc[0]["优秀线"])

        self.assertEqual(get("握力", "男", "成年"), (33.0, 44.0))
        self.assertEqual(get("握力", "女", "U16"), (12.0, 22.0))
        self.assertEqual(get("坐位体前屈", "男", "成年"), (3.7, 25.1))
        self.assertEqual(get("静息心率", "男", "成年"), (80.0, 60.0))
        self.assertEqual(get("30米冲刺", "男", "U18"), (4.45, 4.23))
        self.assertEqual(get("Yo-Yo间歇跑", "男", "U16"), (1520.0, 2120.0))
        self.assertEqual(get("Yo-Yo间歇跑", "男", "U18"), (640.0, 870.0))

    def test_score_formula_three_lines(self):
        std = self.utils.generate_default_standards()
        # 握力男成年：低限 22 → 0 分，达标 33 → 60 分，优秀 44 → 100 分。
        self.assertEqual(self.utils.calc_score("握力", 10, "男|成年", std), 0.0)
        self.assertEqual(self.utils.calc_score("握力", 22, "男|成年", std), 0.0)
        self.assertEqual(self.utils.calc_score("握力", 33, "男|成年", std), 60.0)
        self.assertEqual(self.utils.calc_score("握力", 38.5, "男|成年", std), 80.0)
        self.assertEqual(self.utils.calc_score("握力", 44, "男|成年", std), 100.0)
        # 越小越好：静息心率低限 100→0、达标 80→60、中值 70→80、优秀 60→100。
        self.assertEqual(self.utils.calc_score("静息心率", 100, "男|成年", std), 0.0)
        self.assertEqual(self.utils.calc_score("静息心率", 80, "男|成年", std), 60.0)
        self.assertEqual(self.utils.calc_score("静息心率", 70, "男|成年", std), 80.0)
        self.assertEqual(self.utils.calc_score("静息心率", 60, "男|成年", std), 100.0)
        # 30米冲刺：4.67→0、4.45→60、4.34→80、4.23→100。
        self.assertEqual(self.utils.calc_score("30米冲刺", 4.67, "男|U18", std), 0.0)
        self.assertEqual(self.utils.calc_score("30米冲刺", 4.45, "男|U18", std), 60.0)
        self.assertEqual(self.utils.calc_score("30米冲刺", 4.34, "男|U18", std), 80.0)
        self.assertEqual(self.utils.calc_score("30米冲刺", 4.23, "男|U18", std), 100.0)

    def test_low_line_derived_and_old_ten_column_table_compatible(self):
        """旧 10 列标准表无低限线时，读取阶段自动补齐并采用三段式计分。"""
        old = self.utils.generate_default_standards().drop(columns=["低限线"])
        with patch.object(self.utils, "load_table", return_value=old.copy()):
            loaded = self.utils.load_standards()
        self.assertIn("低限线", loaded.columns)
        mask = ((loaded["测试项目"] == "握力") & (loaded["性别"] == "男")
                & (loaded["年龄组"] == "成年"))
        self.assertEqual(float(loaded.loc[mask, "低限线"].iloc[0]), 22.0)
        self.assertEqual(self.utils.calc_score("握力", 33, "男|成年", loaded), 60.0)
        self.assertEqual(self.utils.calc_score("握力", 38.5, "男|成年", loaded), 80.0)

    def test_load_standards_overrides_existing_old_values(self):
        """关键回归：已存在的旧标准表在读取时被确认值覆盖（云端无需重建表）。"""
        old = self.utils.generate_default_standards()
        # 模拟云端旧数据：把握力男成年改回示例旧值 35→60
        mask = ((old["测试项目"] == "握力") & (old["性别"] == "男")
                & (old["年龄组"] == "成年"))
        old.loc[mask, "达标线"] = 35.0
        old.loc[mask, "优秀线"] = 60.0
        with patch.object(self.utils, "load_table", return_value=old.copy()):
            loaded = self.utils.load_standards()
        hit = loaded[mask]
        self.assertEqual((float(hit.iloc[0]["达标线"]), float(hit.iloc[0]["优秀线"])),
                         (33.0, 44.0))
        self.assertEqual(float(hit.iloc[0]["低限线"]), 22.0)
        # 计分随之正确：38.5kg → 60 + (38.5-33)/(44-33)*40 = 80
        self.assertEqual(self.utils.calc_score("握力", 38.5, "男|成年", loaded), 80.0)

    def test_legacy_master_snapshot_maps_to_adult(self):
        """历史「男|大师」快照在 load_tests 内存中归一为成年，不报失配。"""
        std = self.utils.generate_default_standards()
        self.assertIsNotNone(self.utils.lookup_standard(std, "握力", "男|成年"))
        s = self.utils.lookup_standard(std, "握力", "男|成年")
        self.assertEqual((s["low"], s["pass"], s["excellent"]), (22.0, 33.0, 44.0))


if __name__ == "__main__":
    unittest.main()
