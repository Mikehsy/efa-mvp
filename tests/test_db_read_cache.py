"""DB 模式读表缓存：减少跨网络查询，且写入后必须立刻可见。

云端每次交互都重跑脚本，一个页面会重复读同一张表多次。若每次都查库，
用户会感到「每次操作都卡很久」。本测试锁定：
  1) 同一用户同一张表在一次渲染内只查库一次；
  2) 写入后缓存立即失效，不能读到旧数据；
  3) 缓存按用户隔离，不同用户不会串数据。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st

BASE = Path(__file__).resolve().parents[1]


class ReadCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "EFA_USE_DB": "1",
            "EFA_DB_PATH": str(Path(self.tmp.name) / "cache.db"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

        for name in ("db", "auth", "utils"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(BASE))
        import db, auth, utils
        self.db, self.auth, self.utils = db, auth, utils
        db.init_db()
        auth.register("cache_user", "pass1234")
        _, u = auth.login("cache_user", "pass1234")
        self.uid = u["id"]

        self._orig_ss = st.session_state
        st.session_state = {"efa_user": {"id": self.uid,
                                         "username": "cache_user", "role": "user"}}
        self.addCleanup(setattr, st, "session_state", self._orig_ss)
        utils._FRAME_CACHE.clear()

    def test_repeat_reads_hit_cache(self):
        counts = {"load": 0}
        orig = self.db.load_frame

        def counting(*a, **k):
            counts["load"] += 1
            return orig(*a, **k)

        with patch.object(self.db, "load_frame", counting):
            for _ in range(3):
                self.utils.load_athletes()
                self.utils.load_tests()
                self.utils.load_standards()
        self.assertEqual(counts["load"], 3,
                         "9 次读表应只查库 3 次（每表一次）")

    def test_write_invalidates_cache(self):
        row = {c: "" for c in self.utils.ATHLETE_COLS}
        row.update({"运动员ID": "1", "姓名": "新数据", "运动项目": "篮球"})
        self.utils.load_athletes()          # 预热缓存
        self.utils.append_row("athlete", row)
        names = self.utils.load_athletes()["姓名"].tolist()
        self.assertEqual(names, ["新数据"], "写入后不能读到缓存里的旧数据")

    def test_cache_isolated_by_user(self):
        self.db.save_frame(self.uid, "athlete",
                           [{"运动员ID": "1", "姓名": "甲"}], ["运动员ID", "姓名"])
        self.utils._FRAME_CACHE.clear()
        name_a = self.utils._load_frame(self.uid, "athlete")[0][0]["姓名"]
        name_b = self.utils._load_frame(999999, "athlete")[0]
        self.assertEqual(name_a, "甲")
        self.assertIsNone(name_b, "不同用户不能读到彼此数据")


if __name__ == "__main__":
    unittest.main()
