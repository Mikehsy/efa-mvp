"""PIPL 技术措施回归测试：知情同意、重新同意、撤回、注销与数据可携带。

锁定以下不可回退的行为：
  1) 注册未勾选全部（含敏感信息 / 出境 / 未成年人）单独同意项即被拒绝；
  2) 同意记录持久化且只含去标识化标识；
  3) 历史账号、政策版本变更、撤回同意都必须进入重新同意流程；
  4) 注销删除账号与全部业务数据，但保留去标识化合规留痕；
  5) 个人数据导出不含密码凭据，脱敏副本可用。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st

BASE = Path(__file__).resolve().parents[1]


class PiplTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        # 只 patch 模块属性、不替换 sys.modules：否则同批运行的 PG 兼容测试
        # 会持有旧的 db 引用，patch 不到 auth 实际使用的对象。
        import auth, db, privacy
        self.db, self.auth, self.privacy = db, auth, privacy

        patches = [
            patch.dict(os.environ, {"EFA_USE_DB": "1", "DATABASE_URL": ""}),
            # 防止本机 .streamlit/secrets.toml 把测试连到真实云端数据库。
            patch.object(st, "secrets", {}, create=True),
            patch.object(db, "DATABASE_URL", ""),
            patch.object(db, "IS_PG", False),
            patch.object(db, "USE_DB", True),
            patch.object(db, "DB_PATH", Path(self.tmp.name) / "pipl.db"),
            patch.object(db, "_SCHEMA_READY", False),
            patch.object(privacy, "POLICY_VERSION", "test-v1"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        db.init_db()
        self.items = [key for key, _, _ in privacy.CONSENT_ITEMS]

    def _register(self, name):
        ok, msg = self.auth.register(name, "pass1234", consents=self.items,
                                     policy_version=self.privacy.POLICY_VERSION)
        self.assertTrue(ok, msg)
        ok2, user = self.auth.login(name, "pass1234")
        self.assertTrue(ok2)
        return user

    def test_register_requires_all_consents(self):
        ok, msg = self.auth.register("pipl_a", "pass1234",
                                     consents=self.items[:-1])
        self.assertFalse(ok)
        self.assertIn("同意", msg)
        self.assertIsNone(self.db.get_user("pipl_a"))

    def test_register_records_versioned_deidentified_consent(self):
        ok, _ = self.auth.register("pipl_b", "pass1234", "b@x.com",
                                   consents=self.items,
                                   policy_version=self.privacy.POLICY_VERSION)
        self.assertTrue(ok)
        _, u = self.auth.login("pipl_b", "pass1234")
        rec = self.db.latest_consent(self.privacy.owner_hash(u["id"]))
        self.assertEqual(rec["policy_version"], self.privacy.POLICY_VERSION)
        self.assertEqual(set(rec["items"]), set(self.items))
        self.assertEqual(rec["action"], "grant")
        self.assertEqual(self.auth.consent_status(u), self.auth.CONSENT_OK)
        blob = json.dumps(rec, ensure_ascii=False)
        self.assertNotIn("pipl_b", blob)
        self.assertNotIn("b@x.com", blob)

    def test_legacy_account_must_reconsent(self):
        # consents=None 模拟合规功能上线前已注册的历史账号。
        self.assertTrue(self.auth.register("pipl_c", "pass1234")[0])
        _, u = self.auth.login("pipl_c", "pass1234")
        self.assertEqual(self.auth.consent_status(u), self.auth.CONSENT_MISSING)
        ok, msg = self.auth.grant_consent(u["id"], self.items)
        self.assertTrue(ok, msg)
        self.assertEqual(self.auth.consent_status(u), self.auth.CONSENT_OK)

    def test_policy_version_change_requires_reconsent(self):
        u = self._register("pipl_d")
        with patch.object(self.privacy, "POLICY_VERSION", "test-v2"):
            self.assertEqual(self.auth.consent_status(u), self.auth.CONSENT_VERSION)
            ok, msg = self.auth.grant_consent(u["id"], self.items)
            self.assertTrue(ok, msg)
            self.assertEqual(self.auth.consent_status(u), self.auth.CONSENT_OK)

    def test_withdraw_stops_processing_and_can_be_restored(self):
        u = self._register("pipl_e")
        self.auth.withdraw_consent(u["id"])
        self.assertEqual(self.auth.consent_status(u), self.auth.CONSENT_WITHDRAWN)
        ok, u2 = self.auth.login("pipl_e", "pass1234")
        self.assertTrue(ok, "撤回后仍须能验证身份，以行使重新同意或删除权")
        self.assertEqual(u2["status"], "withdrawn")
        self.auth.grant_consent(u["id"], self.items)
        self.assertEqual(self.auth.consent_status(u2), self.auth.CONSENT_OK)

    def test_delete_account_removes_data_keeps_deidentified_trace(self):
        u = self._register("pipl_f")
        uid = u["id"]
        self.db.save_frame(uid, "athlete",
                           [{"运动员ID": "1", "姓名": "张三"}], ["运动员ID", "姓名"])
        self.assertEqual(self.db.load_frame(uid, "athlete")[0][0]["姓名"], "张三")
        self.auth.delete_account(uid)
        self.assertIsNone(self.db.get_user_by_id(uid))
        self.assertEqual(self.db.load_frame(uid, "athlete"), (None, None))
        hist = self.db.list_consents(self.privacy.owner_hash(uid))
        self.assertGreaterEqual(len(hist), 2)
        self.assertEqual(hist[-1]["action"], "delete")
        blob = json.dumps(hist, ensure_ascii=False)
        for secret in ("pipl_f", "张三", "pass1234"):
            self.assertNotIn(secret, blob)

    def test_export_excludes_credentials_and_mask_works(self):
        u = self._register("pipl_g")
        uid = u["id"]
        self.db.save_frame(uid, "athlete",
                           [{"运动员ID": "1", "姓名": "张三"}], ["运动员ID", "姓名"])
        full = json.loads(self.auth.collect_personal_data(uid))
        self.assertNotIn("password_hash", full["账号信息"])
        self.assertNotIn("salt", full["账号信息"])
        self.assertEqual(full["运动员档案"][0]["姓名"], "张三")
        self.assertEqual(full["政策版本"], self.privacy.POLICY_VERSION)
        masked = json.loads(self.auth.collect_personal_data(uid, mask=True))
        self.assertTrue(masked["脱敏"])
        self.assertNotEqual(masked["账号信息"]["username"], "pipl_g")
        self.assertNotEqual(masked["运动员档案"][0]["姓名"], "张三")

    def test_policy_and_consent_definitions_are_complete(self):
        text = self.privacy.policy_text()
        for kw in ("敏感个人信息", "单独同意", "撤回", "注销", "未成年人", "境外",
                   self.privacy.POLICY_VERSION):
            self.assertIn(kw, text)
        self.assertFalse(self.privacy.consent_valid(self.items[:-1]))
        self.assertTrue(self.privacy.consent_valid(self.items))
        with patch.object(self.privacy, "OPERATOR_NAME", "【待填写：运营主体全称】"):
            self.assertFalse(self.privacy.operator_configured())

    def test_retention_query_never_deletes_automatically(self):
        u = self._register("pipl_h")
        uid = u["id"]
        self.db.save_frame(uid, "test", [{"记录ID": "1"}], ["记录ID"])
        with self.db.connect() as (conn, ph):
            self.db._exec(conn.cursor(),
                          "UPDATE user_data SET updated_at=? WHERE owner_id=?",
                          ph, ("2000-01-01T00:00:00", str(uid)))
        self.assertIn(str(uid), self.db.expired_owner_ids(1))
        self.assertIsNotNone(self.db.get_user_by_id(uid),
                             "保存期限查询不得自动删除个人信息")


if __name__ == "__main__":
    unittest.main()
