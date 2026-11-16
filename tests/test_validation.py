"""边界与输入校验回归：CSV 注入、HTML 转义、日期 / 成绩 / 档案校验。"""
import unittest

from config import ATHLETE_FILE
from tests.base import prepare
from utils import (ATHLETE_COLS, ValidationError, append_row, create_backup,
                   escape_html, load_table, restore_backup, sanitize_text,
                   validate_athlete_row, validate_date, validate_score,
                   validate_test_row)


def setUpModule():
    prepare()


class ValidationTests(unittest.TestCase):
    def test_csv_formula_prefixes_are_escaped(self):
        for raw in ("=1+1", "+SUM(A1)", "-1+1", "@SUM(A1)"):
            with self.subTest(raw=repr(raw)):
                self.assertEqual(sanitize_text(raw), "'" + raw)

    def test_control_character_prefix_is_stripped(self):
        # _clean() 会先 strip 掉前导控制字符，结果本身已不构成公式注入。
        self.assertEqual(sanitize_text("\tcmd"), "cmd")
        self.assertEqual(sanitize_text("\rcmd"), "cmd")

    def test_normal_text_unchanged(self):
        for raw in ("演示数据", "100米", "毫升/公斤/分", ""):
            self.assertEqual(sanitize_text(raw), raw)

    def test_escape_html(self):
        self.assertEqual(escape_html('<b>"x"</b>'), "&lt;b&gt;&quot;x&quot;&lt;/b&gt;")

    def test_valid_date(self):
        from datetime import date
        self.assertEqual(validate_date("2026-11-13"), "2026-11-13")
        self.assertEqual(validate_date(date(2000, 1, 1)), "2000-01-01")

    def test_invalid_dates(self):
        for raw in ("不是日期", "2050-01-01", "1940-01-01", ""):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    validate_date(raw)

    def test_valid_scores(self):
        self.assertEqual(validate_score("12.5", "continuous"), 12.5)
        self.assertEqual(validate_score(2, "ordinal"), 2.0)
        self.assertIsNone(validate_score("阳性", "binary"))

    def test_invalid_scores(self):
        for value, kind in (("abc", "continuous"), (float("inf"), "continuous"),
                            (4, "ordinal"), (-1, "ordinal")):
            with self.subTest(value=value, kind=kind):
                with self.assertRaises(ValidationError):
                    validate_score(value, kind)

    def test_test_row_sanitizes_and_normalizes(self):
        row = validate_test_row({"测试日期": "2026-11-13", "成绩": "12.5",
                                 "评分类型": "continuous", "备注": "@SUM(A1)"})
        self.assertEqual(row["测试日期"], "2026-11-13")
        self.assertEqual(row["备注"], "'@SUM(A1)")

    def test_athlete_required_fields(self):
        with self.assertRaises(ValidationError):
            validate_athlete_row({"运动项目": "短跑"})
        with self.assertRaises(ValidationError):
            validate_athlete_row({"姓名": "张三"})
        row = validate_athlete_row({"姓名": "=cmd", "运动项目": "短跑",
                                    "出生日期": "2000-01-01"})
        self.assertEqual(row["姓名"], "'=cmd")

    def test_written_csv_escapes_formula(self):
        backup = create_backup("validation_case")
        try:
            append_row("athlete", {"姓名": "注入测试", "性别": "男",
                                   "出生日期": "2001-01-01", "所属队伍": "短跑组",
                                   "专项": "100米", "备注": "=1+1", "运动项目": "短跑"})
            df = load_table(ATHLETE_FILE, ATHLETE_COLS)
            self.assertIn("'=1+1", df["备注"].astype(str).tolist())
        finally:
            restore_backup(backup)


if __name__ == "__main__":
    unittest.main()
