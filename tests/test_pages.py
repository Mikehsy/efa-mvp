"""页面回归：全部页面零异常 + 分析报告页交互 + 隐私页冒烟。"""
import unittest

from streamlit.testing.v1 import AppTest

from reports import ATHLETE_MODE, MODES, TEAM_MODE
from tests.base import PAGES, page_path, prepare


def setUpModule():
    prepare()


class PageSmokeTests(unittest.TestCase):
    def test_six_pages_no_exception(self):
        for rel in PAGES:
            with self.subTest(page=rel):
                at = AppTest.from_file(page_path(rel), default_timeout=90).run()
                self.assertEqual(len(at.exception), 0,
                                 "; ".join(str(e) for e in at.exception))

    def test_privacy_page_no_exception(self):
        """PIPL 自助页在单机模式下安全提示并停止，不得抛异常。"""
        at = AppTest.from_file(page_path("pages/6_🔒隐私与账号.py"),
                               default_timeout=90).run()
        self.assertEqual(len(at.exception), 0,
                         "; ".join(str(e) for e in at.exception))

    def test_report_page_widgets(self):
        at = AppTest.from_file(page_path(PAGES[-1]), default_timeout=90).run()
        self.assertEqual(len(at.radio), 1)
        self.assertIn(at.radio[0].value, MODES)
        text = "\n".join(str(m.value) for m in at.markdown) + "\n" + \
               "\n".join(str(c.value) for c in at.caption)
        for keyword in ("不足项", "原因分析", "下一步改善建议"):
            self.assertIn(keyword, text)
        self.assertEqual(len(at.download_button), 5)

    def test_report_page_mode_switch(self):
        at = AppTest.from_file(page_path(PAGES[-1]), default_timeout=90).run()
        at = at.selectbox[0].set_value("短跑").run()
        self.assertEqual(len(at.exception), 0,
                         "; ".join(str(e) for e in at.exception))
        at = at.radio[0].set_value(ATHLETE_MODE).run()
        self.assertEqual(len(at.exception), 0,
                         "; ".join(str(e) for e in at.exception))
        self.assertEqual(len(at.selectbox), 2)
        at = at.checkbox[0].set_value(False).run()
        self.assertEqual(len(at.exception), 0,
                         "; ".join(str(e) for e in at.exception))
        self.assertEqual(len(at.download_button), 5)


if __name__ == "__main__":
    unittest.main()
