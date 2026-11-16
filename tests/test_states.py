"""多数据状态回归：空数据 / 仅档案 / 档案缺项 / 完整数据，各跑六页。"""
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from config import ATHLETE_FILE
from tests.base import PAGES, page_path, prepare
from utils import append_row, clear_all_data, create_backup, restore_backup


def setUpModule():
    prepare()


def assert_pages_ok(case: unittest.TestCase) -> None:
    for rel in PAGES:
        with case.subTest(page=rel):
            at = AppTest.from_file(page_path(rel), default_timeout=90).run()
            case.assertEqual(len(at.exception), 0,
                             "; ".join(str(e) for e in at.exception))


class DataStateTests(unittest.TestCase):
    def setUp(self):
        prepare()
        self.backup = create_backup("state_case")

    def tearDown(self):
        try:
            restore_backup(self.backup)
        except Exception:
            prepare()

    def test_empty_data(self):
        clear_all_data()
        assert_pages_ok(self)

    def test_only_athletes(self):
        clear_all_data()
        append_row("athlete", {"姓名": "状态测试", "性别": "男", "出生日期": "2002-01-01",
                               "所属队伍": "短跑组", "专项": "100米", "备注": "",
                               "运动项目": "短跑"})
        assert_pages_ok(self)

    def test_athlete_missing_sport(self):
        a = pd.read_csv(ATHLETE_FILE, encoding="utf-8-sig")
        a.loc[0, "运动项目"] = ""
        a.to_csv(ATHLETE_FILE, index=False, encoding="utf-8-sig")
        assert_pages_ok(self)

    def test_full_demo_data(self):
        assert_pages_ok(self)


if __name__ == "__main__":
    unittest.main()
