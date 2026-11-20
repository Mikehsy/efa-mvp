"""PostgreSQL(psycopg2) 行为兼容回归测试。

云端故障复盘：sqlite3 的 cursor.execute() 返回 cursor，而 psycopg2 返回 None。
早期 `_exec` 直接 `return cur.execute(...)`，导致 PG 下 `cur = _exec(...)` 变成
None，注册时 cur.fetchone() 抛 AttributeError。本测试用假驱动精确模拟
psycopg2 的三个差异，防止本地 SQLite 测试全绿、云端却崩的情况再次发生：
  1) execute() 返回 None
  2) fetchone() 返回 tuple（无 .keys() / 字符串下标）
  3) 占位符为 %s
"""
import unittest
from unittest.mock import patch

import db


class FakeCursor:
    """模拟 psycopg2 cursor：execute 返回 None，行是 tuple。"""

    def __init__(self):
        self.description = None
        self.lastrowid = 1
        self._rows = []
        self._tables = set()
        self._users = {}          # username -> tuple
        self._frames = {}         # (owner_id, kind) -> payload
        self._consents = []       # 同意事件 tuple
        self._status = {}         # user_id -> status（撤回/恢复）
        self._next_id = 1
        self._next_consent_id = 1

    def _with_status(self, row):
        return row[:6] + (self._status.get(row[0], "active"),) + row[7:]

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if "pg_advisory_xact_lock" in s:
            pass
        elif s.startswith("CREATE TABLE IF NOT EXISTS users"):
            self._tables.add("users")
        elif s.startswith("CREATE TABLE IF NOT EXISTS user_data"):
            self._tables.add("user_data")
        elif "to_regclass" in s:
            self._rows = [("users" in self._tables,
                           "user_data" in self._tables)]
        elif "SELECT * FROM users WHERE username" in s:
            u = self._users.get(params[0])
            self._rows = [self._with_status(u)] if u else []
            self.description = [("id",), ("username",), ("email",),
                                ("password_hash",), ("salt",), ("role",),
                                ("status",), ("created_at",)]
        elif "SELECT * FROM users WHERE id" in s:
            u = next((r for r in self._users.values() if r[0] == params[0]), None)
            self._rows = [self._with_status(u)] if u else []
            self.description = [("id",), ("username",), ("email",),
                                ("password_hash",), ("salt",), ("role",),
                                ("status",), ("created_at",)]
        elif s.startswith("UPDATE users SET status"):
            self._status[params[1]] = params[0]
        elif s.startswith("INSERT INTO consents"):
            cid = self._next_consent_id
            self._next_consent_id += 1
            self._consents.append((cid,) + tuple(params))
        elif "FROM consents" in s:
            hits = [c for c in self._consents if c[1] == params[0]]
            self._rows = hits[-1:] if "ORDER BY id DESC" in s else hits
        elif s.startswith("DELETE FROM user_data"):
            self._frames = {k: v for k, v in self._frames.items()
                            if k[0] != params[0]}
        elif s.startswith("DELETE FROM users"):
            self._users = {n: r for n, r in self._users.items()
                           if r[0] != params[0]}
        elif "SELECT MAX(updated_at)" in s:
            self._rows = [(None,)]
        elif "INSERT INTO users" in s:
            uid = self._next_id
            self._next_id += 1
            row = (uid, params[0], params[1], params[2], params[3],
                   params[4], "active", params[5])
            self._users[params[0]] = row
            if "RETURNING id" in s:
                self._rows = [(uid,)]     # RETURNING 的 fetchone 结果
        elif "SELECT COUNT(*) FROM users" in s:
            self._rows = [(len(self._users),)]
        elif "SELECT payload FROM user_data" in s:
            key = (params[0], params[1])
            self._rows = [(self._frames[key],)] if key in self._frames else []
        elif "INSERT INTO user_data" in s:
            self._frames[(params[0], params[1])] = params[2]
        elif "SELECT 1 FROM user_data" in s:
            self._rows = [(1,)] if any(k[0] == params[0] for k in self._frames) else []
        else:
            raise AssertionError(f"未预期的 SQL: {s}")
        return None                      # 关键：psycopg2 行为

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class PgCompatTest(unittest.TestCase):
    def setUp(self):
        self.cur = FakeCursor()
        self.conn = FakeConn(self.cur)
        self.patches = [
            patch.object(db, "IS_PG", True),
            patch.object(db, "_SCHEMA_READY", True),
            patch.object(db, "_new_conn", lambda: (self.conn, "%s")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_register_login_flow(self):
        import auth
        ok, msg = auth.register("cloud_user", "pass1234", "u@x.com")
        self.assertTrue(ok, msg)
        ok2, user = auth.login("cloud_user", "pass1234")
        self.assertTrue(ok2)
        self.assertEqual(user["id"], 1)
        self.assertEqual(user["username"], "cloud_user")
        self.assertEqual(db.user_count(), 1)
        # 重复注册应被拒绝，且不能抛 AttributeError
        ok3, _ = auth.register("cloud_user", "pass1234")
        self.assertFalse(ok3)
        # 错误密码
        self.assertFalse(auth.login("cloud_user", "wrong")[0])

    def test_frame_read_write(self):
        cols = ["运动员ID", "姓名"]
        db.save_frame("7", "athlete", [{"运动员ID": "1", "姓名": "张三"}], cols)
        records, saved_cols = db.load_frame("7", "athlete")
        self.assertEqual(saved_cols, cols)
        self.assertEqual(records[0]["姓名"], "张三")
        self.assertTrue(db.has_data("7"))
        self.assertEqual(db.load_frame("8", "athlete"), (None, None))
        self.assertFalse(db.has_data("8"))

    def test_pipl_flow_on_pg(self):
        """云端 PG 下同意、撤回、重新同意、导出、注销全链路不因 cursor 差异崩溃。"""
        import auth
        import privacy
        items = [key for key, _, _ in privacy.CONSENT_ITEMS]
        ok, msg = auth.register("pg_pipl", "pass1234", consents=items,
                                policy_version=privacy.POLICY_VERSION)
        self.assertTrue(ok, msg)
        ok2, user = auth.login("pg_pipl", "pass1234")
        self.assertTrue(ok2)
        self.assertEqual(auth.consent_status(user), auth.CONSENT_OK)

        auth.withdraw_consent(user["id"])
        self.assertEqual(auth.consent_status(user), auth.CONSENT_WITHDRAWN)
        auth.grant_consent(user["id"], items)
        self.assertEqual(auth.consent_status(user), auth.CONSENT_OK)
        self.assertIn("账号信息",
                      auth.collect_personal_data(user["id"]).decode("utf-8"))

        auth.delete_account(user["id"])
        self.assertIsNone(db.get_user_by_id(user["id"]))
        self.assertGreaterEqual(
            len(db.list_consents(privacy.owner_hash(user["id"]))), 3,
            "去标识化留痕须在注销后保留")

    def test_no_hardcoded_question_mark_in_pg_sql(self):
        """确保传给 PG 的语句用 %s，不含 ? 占位符。"""
        captured = []
        orig = self.cur.execute

        def spy(sql, params=()):
            captured.append(sql)
            return orig(sql, params)

        self.cur.execute = spy
        db.save_frame("1", "standards", [], ["测试项目"])
        self.assertTrue(any("%s" in s for s in captured))
        self.assertFalse(any("?" in s for s in captured))


if __name__ == "__main__":
    unittest.main()
