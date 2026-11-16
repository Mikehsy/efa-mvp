"""布局参数守卫：防止 width 参数被误写成布尔值导致 1px 宽度。"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = [ROOT / "app.py", ROOT / "utils.py", *sorted((ROOT / "pages").glob("*.py"))]


class WidthParamTest(unittest.TestCase):
    def test_width_never_boolean(self):
        bad = []
        for path in FILES:
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "width=True" in line or "use_container_width" in line:
                    bad.append(f"{path.name}:{no}")
        self.assertEqual(
            bad, [],
            'width 必须为 "stretch"/"content"/整数，布尔 True 会被当作 1 像素: '
            + ", ".join(bad),
        )
