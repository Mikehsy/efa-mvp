"""防止界面文字里的半角 ~ 被 Markdown 解析成删除线。

现象：报告中「4~6 周」「0~100 分」的成对 ~ 会被渲染成删除线，
文字被划掉且 ~ 消失（显示为 46 周 / 0100 分）。显示文本统一用全角 ～。
代码里的 pandas 位运算 ~df / ~mask 是合法用法，不在禁止之列。
"""
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
FILES = [BASE / "app.py", BASE / "reports.py", BASE / "utils.py",
         *sorted((BASE / "pages").glob("*.py"))]

# 匹配「数字~数字」这种范围写法（会成对被解析成删除线）
RANGE_TILDE = re.compile(r"\d~\d")
# 匹配字符串里孤立的 " ~ "（也可能与别处配对）
SPACED_TILDE = re.compile(r"['\"][^'\"]*\s~\s[^'\"]*['\"]")


class DisplayTildeTest(unittest.TestCase):
    def test_no_halfwidth_range_tilde_in_source(self):
        offenders = []
        for f in FILES:
            for lineno, line in enumerate(
                    f.read_text(encoding="utf-8").splitlines(), start=1):
                code = line.split("#", 1)[0]
                if RANGE_TILDE.search(code) or SPACED_TILDE.search(code):
                    offenders.append(f"{f.name}:{lineno}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "显示文本含半角 ~（会被 Markdown 渲染成删除线），请改用全角 ～：\n"
                         + "\n".join(offenders))

    def test_range_text_uses_fullwidth_tilde(self):
        # 抽查关键文案确实改成了全角，避免"全删光"式的假修复
        found = False
        for f in FILES:
            if re.search(r"\d～\d", f.read_text(encoding="utf-8")):
                found = True
                break
        self.assertTrue(found, "未找到全角 ～ 范围写法，替换可能未生效")


if __name__ == "__main__":
    unittest.main()
