"""分析报告引擎回归：双模式、按项目、三要素与 Markdown 导出。"""
import unittest

from reports import (ALL_SPORTS, ATHLETE_MODE, MODES, TEAM_MODE,
                     generate_report, to_markdown)
from tests.base import prepare
from utils import item_catalog, load_athletes, load_tests as load_test_records


def setUpModule():
    prepare()


class ReportEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tests, cls.athletes, cls.catalog = load_test_records(), load_athletes(), item_catalog()

    def gen(self, **kwargs):
        return generate_report(self.tests, self.athletes, self.catalog, **kwargs)

    def test_modes_constant(self):
        self.assertEqual(MODES, [TEAM_MODE, ATHLETE_MODE])

    def test_team_report(self):
        r = self.gen(mode=TEAM_MODE)
        self.assertEqual(r["summary"]["测试记录"], len(self.tests))
        self.assertEqual(r["sport"], ALL_SPORTS)

    def test_sport_filter_and_focus(self):
        r = self.gen(mode=TEAM_MODE, sport="短跑")
        self.assertLess(r["summary"]["测试记录"], len(self.tests))
        self.assertEqual(r["focus"], ["速度", "爆发力", "灵敏协调"])
        self.assertTrue(all(t == "短跑" for t in r["details"]["运动项目"].astype(str)))

    def test_athlete_report_uses_profile_sport(self):
        r = self.gen(mode=ATHLETE_MODE, athlete_id="1")
        self.assertEqual(r["sport"], "短跑")
        self.assertIn("林扬", r["scope"])
        self.assertTrue((r["details"]["运动员ID"].astype(str) == "1").all())

    def test_athlete_explicit_sport_override(self):
        r = self.gen(mode=ATHLETE_MODE, athlete_id="1", sport="中长跑")
        self.assertEqual(r["sport"], "中长跑")

    def test_three_required_elements(self):
        r = self.gen(mode=TEAM_MODE)
        self.assertGreater(len(r["gaps"]), 0)
        self.assertGreater(len(r["reasons"]), 0)
        self.assertGreater(len(r["advice"]), 0)
        self.assertTrue(all(k in r for k in ("summary", "caveats", "item_stats",
                                             "category_stats", "fms", "details")))

    def test_markdown_six_chapters(self):
        md = to_markdown(self.gen(mode=TEAM_MODE))
        for chapter in ("一、概览", "二、不足项", "三、原因分析",
                        "四、下一步改善建议", "五、按能力域汇总", "六、数据局限与说明"):
            self.assertIn(chapter, md)

    def test_markdown_for_athlete(self):
        self.assertIn("原因分析", to_markdown(self.gen(mode=ATHLETE_MODE, athlete_id="1")))

    def test_empty_scope_is_safe(self):
        r = self.gen(mode=TEAM_MODE, start="1900-01-01", end="1900-01-02")
        self.assertEqual(r["summary"]["测试记录"], 0)
        self.assertTrue(r["reasons"])

    def test_excluding_fms(self):
        r = self.gen(mode=TEAM_MODE, include_fms=False)
        self.assertEqual(r["summary"]["FMS筛查"], 0)
        self.assertFalse(any(a["优先级"] == "P0" for a in r["advice"]))


if __name__ == "__main__":
    unittest.main()
