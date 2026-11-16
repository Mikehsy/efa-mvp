"""可靠性回归：原子写、单写端锁、快照、备份恢复、损坏文件与生产开关。"""
import os
import subprocess
import sys
import threading
import unittest

from config import (ATHLETE_FILE, DATA_FILES, LOCK_FILE, LOG_DIR,
                    SNAPSHOT_DIR, TEST_FILE)
from tests.base import PROJECT, prepare
from utils import (ATHLETE_COLS, TEST_COLS, DataFileError, append_row,
                   atomic_write_csv, create_backup, list_backups, load_table,
                   restore_backup)


def setUpModule():
    prepare()


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        prepare()
        self.backup = create_backup("unit_case")

    def tearDown(self):
        restore_backup(self.backup)

    def test_initial_files_and_no_stale_lock(self):
        self.assertTrue(all(p.exists() for p in DATA_FILES))
        self.assertFalse(LOCK_FILE.exists())
        self.assertTrue(SNAPSHOT_DIR.exists())

    def test_atomic_write_leaves_no_temp_file(self):
        df = load_table(TEST_FILE, TEST_COLS)
        atomic_write_csv(df, TEST_FILE)
        self.assertEqual(len(load_table(TEST_FILE, TEST_COLS)), len(df))
        self.assertEqual(list(TEST_FILE.parent.glob(".*.tmp")), [])

    def test_backup_is_complete_and_restores(self):
        self.assertTrue(all((self.backup / p.name).exists() for p in DATA_FILES))
        self.assertTrue((self.backup / "manifest.json").exists())
        self.assertTrue(any(b["complete"] for b in list_backups()))
        before = len(load_table(ATHLETE_FILE, ATHLETE_COLS))
        append_row("athlete", {"姓名": "临时", "性别": "男", "出生日期": "2001-01-01",
                               "所属队伍": "短跑组", "专项": "100米", "备注": "单测",
                               "运动项目": "短跑"})
        self.assertEqual(len(load_table(ATHLETE_FILE, ATHLETE_COLS)), before + 1)
        restore_backup(self.backup)
        self.assertEqual(len(load_table(ATHLETE_FILE, ATHLETE_COLS)), before)

    def test_concurrent_writes_are_not_lost(self):
        before = len(load_table(ATHLETE_FILE, ATHLETE_COLS))
        errors = []

        def add_one(i):
            try:
                append_row("athlete", {"姓名": f"并发{i}", "性别": "男",
                                       "出生日期": "2001-01-01", "所属队伍": "短跑组",
                                       "专项": "100米", "备注": "并发", "运动项目": "短跑"})
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=add_one, args=(i,)) for i in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(load_table(ATHLETE_FILE, ATHLETE_COLS)), before + 10)
        self.assertFalse(LOCK_FILE.exists())

    def test_corrupted_csv_is_handled(self):
        TEST_FILE.write_bytes(b"\xff\xfe\x00not-a-csv")
        df = load_table(TEST_FILE, TEST_COLS)
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), TEST_COLS)
        log = LOG_DIR / "app.log"
        self.assertTrue(log.exists())
        self.assertIn("读取数据表", log.read_text(encoding="utf-8"))

    def test_audit_log_records_writes(self):
        log = LOG_DIR / "audit.log"
        self.assertTrue(log.exists())
        self.assertIn("write_csv", log.read_text(encoding="utf-8"))

    def test_demo_data_disabled_in_production(self):
        code = (
            "import sys\n"
            "sys.path.insert(0, r'" + str(PROJECT) + "')\n"
            "import utils\n"
            "from utils import DataFileError\n"
            "print('ALLOW=' + str(utils.ALLOW_DEMO_DATA))\n"
            "try:\n"
            "    utils.seed_demo_data()\n"
            "    print('SEED=allowed')\n"
            "except DataFileError:\n"
            "    print('SEED=blocked')\n"
        )
        env = dict(os.environ, EFA_ALLOW_DEMO_DATA="0")
        out = subprocess.check_output([sys.executable, "-c", code], env=env,
                                      cwd=str(PROJECT), text=True)
        self.assertIn("ALLOW=False", out)
        self.assertIn("SEED=blocked", out)


if __name__ == "__main__":
    unittest.main()
