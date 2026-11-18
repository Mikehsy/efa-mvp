"""数据库层（MVP）：本地 SQLite / 云端 Supabase PostgreSQL 双驱动。

- 零配置本地运行：未设 DATABASE_URL 时用标准库 sqlite3（data/efa.db）。
- 上云：设 DATABASE_URL=postgresql://... 即切换到 Supabase（需 psycopg2-binary）。
- 所有业务数据按 owner_id（注册用户 ID）隔离。
- 上层只调本模块函数，不感知底层驱动。

环境：
  EFA_USE_DB=1            启用数据库模式（注册登录 + 数据库读写）
  DATABASE_URL=postgres…  有值时用 PostgreSQL，否则 SQLite
  EFA_DB_PATH=…           SQLite 文件位置（默认 data/efa.db）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config import DATA_DIR


def _secret(name: str, default: str = "") -> str:
    """云上从 Streamlit secrets 读取，本地回退环境变量。"""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


DATABASE_URL = _secret("DATABASE_URL", "").strip()
DB_PATH = Path(_secret("EFA_DB_PATH", str(DATA_DIR / "efa.db")))
USE_DB = _secret("EFA_USE_DB", "0").strip().lower() in {"1", "true", "yes", "on"}
IS_PG = DATABASE_URL.lower().startswith(("postgres://", "postgresql://"))


DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id          {ID} PRIMARY KEY {AUTO},
    username    TEXT NOT NULL UNIQUE,
    email       TEXT,
    password_hash TEXT NOT NULL,
    salt        TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
)
"""

DDL_DATA = """
CREATE TABLE IF NOT EXISTS user_data (
    owner_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (owner_id, kind)
)
"""

# PIPL 第 13/14/16 条：同意、撤回、重新同意、注销等处理活动须可追溯留痕。
# 只存去标识化的 owner_hash（由 privacy.owner_hash 生成），账号注销删除全部
# 个人信息后仍能证明处理活动的合法性，且留痕本身不含可直接识别个人的信息。
DDL_CONSENTS = """
CREATE TABLE IF NOT EXISTS consents (
    id             {ID} PRIMARY KEY {AUTO},
    owner_hash     TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    items          TEXT NOT NULL,
    action         TEXT NOT NULL,
    ts             TEXT NOT NULL,
    detail         TEXT
)
"""

DDL_CONSENT_IDX = """
CREATE INDEX IF NOT EXISTS idx_consents_owner ON consents(owner_hash, id)
"""


# ------------------------------------------------------------------ 建表（并发安全）
# PostgreSQL 的 CREATE TABLE IF NOT EXISTS 并非原子：多会话并发时可能同时
# 插入系统目录并撞 pg_type 唯一索引（UniqueViolation）。因此：
#   1) 进程内只建一次（双检锁），
#   2) 用事务级 advisory lock 串行化跨进程并发，
#   3) 万一仍撞唯一约束，视为"别人已建好"，重试读取而非报错。
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_ADVISORY_KEY = 2026111601


_POOL = None
_POOL_LOCK = threading.Lock()


class _PooledConn:
    """把池化连接包装成普通连接：close() 归还池而不是真的断开。

    这样 connect() 无需改动、上层与单元测试的 mock 也不受影响。
    """

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
        self._discard = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            # 连接已被服务端断开（Supabase 空闲超时），标记丢弃以便重连。
            self._discard = True

    def close(self):
        self._pool.putconn(self._conn, close=self._discard)


def _get_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                from psycopg2.pool import ThreadedConnectionPool
                _POOL = ThreadedConnectionPool(1, 4, DATABASE_URL)
    return _POOL


def _new_conn():
    if IS_PG:
        # 复用连接，避免每次查询都做一次跨洋 TCP + TLS 握手。
        pool = _get_pool()
        conn = pool.getconn()
        if getattr(conn, "closed", 0):
            pool.putconn(conn, close=True)   # 服务端已断开，换新连接
            conn = pool.getconn()
        return _PooledConn(pool, conn), "%s"
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn, "?"


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn, _ = _new_conn()
        try:
            cur = conn.cursor()
            if IS_PG:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_KEY,))
            ddl_args = dict(
                ID="BIGSERIAL" if IS_PG else "INTEGER",
                AUTO="" if IS_PG else "AUTOINCREMENT")
            cur.execute(DDL_USERS.format(**ddl_args))
            cur.execute(DDL_DATA)
            cur.execute(DDL_CONSENTS.format(**ddl_args))
            cur.execute(DDL_CONSENT_IDX)
            conn.commit()
            _SCHEMA_READY = True
        except Exception as exc:
            conn.rollback()
            # 并发下另一个会话已建好表：确认表确实存在即可继续。
            if _tables_exist(conn):
                _SCHEMA_READY = True
            else:
                raise
        finally:
            conn.close()


def _tables_exist(conn) -> bool:
    try:
        cur = conn.cursor()
        if IS_PG:
            cur.execute("SELECT to_regclass('public.users'), to_regclass('public.user_data'), "
                        "to_regclass('public.consents')")
            row = cur.fetchone()
            return bool(row and row[0] and row[1] and row[2])
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('users','user_data','consents')")
        return len(cur.fetchall()) == 3
    except Exception:
        return False


@contextmanager
def connect():
    """返回 (conn, ph)；ph 是该驱动的占位符（ psycopg2=%s, sqlite=? ）。"""
    _ensure_schema()
    conn, ph = _new_conn()
    try:
        yield conn, ph
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _exec(cur, sql: str, ph: str, params=()):
    """执行并返回 cursor。

    注意：sqlite3 的 execute() 返回 cursor，psycopg2 的 execute() 返回 None，
    因此不能直接 `return cur.execute(...)`，否则 PG 下后续 fetchone() 会得到
    AttributeError。显式返回 cur 让两种驱动行为一致。
    """
    cur.execute(sql.replace("?", ph), params)
    return cur


def init_db() -> None:
    """显式初始化入口，保持向后兼容。"""
    _ensure_schema()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ 用户
def create_user(username, email, password_hash, salt, role="user"):
    with connect() as (conn, ph):
        cur = conn.cursor()
        sql = ("INSERT INTO users(username,email,password_hash,salt,role,created_at) "
               "VALUES(?,?,?,?,?,?)")
        params = (username, email, password_hash, salt, role, now())
        if IS_PG:
            # 用 RETURNING 取 ID，避免 LASTVAL() 在连接池/并发会话下取值不稳。
            cur = _exec(cur, sql + " RETURNING id", ph, params)
            return int(cur.fetchone()[0])
        cur = _exec(cur, sql, ph, params)
        return int(cur.lastrowid)


def get_user(username: str):
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(), "SELECT * FROM users WHERE username=?", ph, (username,))
        row = cur.fetchone()
        if not row:
            return None
        if IS_PG:
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        return dict(row)


def user_count() -> int:
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(), "SELECT COUNT(*) FROM users", ph)
        return int(cur.fetchone()[0])


def _as_id(owner_id):
    """users.id 是整数列；Cloud 会话里可能是字符串，统一转换避免 PG 类型不匹配。"""
    try:
        return int(owner_id)
    except (TypeError, ValueError):
        return owner_id


def get_user_by_id(owner_id):
    """PIPL 第 45 条：按 ID 取账号信息，供个人数据查阅/复制使用。"""
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(), "SELECT * FROM users WHERE id=?", ph, (_as_id(owner_id),))
        row = cur.fetchone()
        cols = [d[0] for d in cur.description] if (IS_PG and row) else None
    if not row:
        return None
    if cols:
        return dict(zip(cols, row))
    return dict(row)


def set_user_status(owner_id, status: str) -> None:
    """撤回同意时停用账号；重新同意后恢复。"""
    with connect() as (conn, ph):
        _exec(conn.cursor(), "UPDATE users SET status=? WHERE id=?", ph,
              (status, _as_id(owner_id)))


def last_data_write(owner_id):
    """最后一次写入业务数据的时间，用于展示保存期限起算点。"""
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(),
                    "SELECT MAX(updated_at) FROM user_data WHERE owner_id=?",
                    ph, (str(owner_id),))
        row = cur.fetchone()
    return row[0] if row else None


def delete_account_data(owner_id) -> None:
    """PIPL 第 47 条：注销即删除账号信息与全部业务数据（不可逆）。

    consents 表只含去标识化的 owner_hash，作为合法性证明依法留存，不随账号删除。
    """
    with connect() as (conn, ph):
        cur = conn.cursor()
        _exec(cur, "DELETE FROM user_data WHERE owner_id=?", ph, (str(owner_id),))
        _exec(cur, "DELETE FROM users WHERE id=?", ph, (_as_id(owner_id),))


def expired_owner_ids(days: int) -> list[str]:
    """返回最后一次写入已超过 days 天的账号 ID，供运营方按留存策略清理。

    这里刻意不做自动删除：删除个人信息不可逆，须由运营方依据 PIA 结论、
    经复核后调用 delete_account_data()。
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(),
                    "SELECT DISTINCT owner_id FROM user_data WHERE updated_at < ?",
                    ph, (cutoff,))
        rows = cur.fetchall()
    return [str(r[0]) for r in rows]


# ------------------------------------------------------------------ 业务数据（JSON blob，按 owner+kind）
def load_frame(owner_id, kind: str):
    """返回 (records, cols)；无数据返回 (None, None)。"""
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(), "SELECT payload FROM user_data WHERE owner_id=? AND kind=?",
                    ph, (str(owner_id), kind))
        row = cur.fetchone()
    if not row:
        return None, None
    raw = row[0] if IS_PG else row["payload"]
    obj = json.loads(raw)
    return obj["records"], obj["cols"]


def save_frame(owner_id, kind: str, records: list[dict], cols: list[str]) -> None:
    payload = json.dumps({"cols": cols, "records": records}, ensure_ascii=False)
    upsert = (
        "INSERT INTO user_data(owner_id,kind,payload,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(owner_id,kind) DO UPDATE SET "
        "payload=excluded.payload, updated_at=excluded.updated_at"
    )
    with connect() as (conn, ph):
        _exec(cur=conn.cursor(), sql=upsert, ph=ph,
              params=(str(owner_id), kind, payload, now()))


def has_data(owner_id) -> bool:
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(), "SELECT 1 FROM user_data WHERE owner_id=? LIMIT 1",
                    ph, (str(owner_id),))
        return cur.fetchone() is not None


# ------------------------------------------------------------------ 同意记录（PIPL 留痕）
_CONSENT_COLS = ("id", "owner_hash", "policy_version", "items", "action", "ts", "detail")


def record_consent(owner_hash: str, policy_version: str, items,
                   action: str, detail: str = "") -> None:
    """持久化一次同意/撤回/重新同意/注销事件。

    owner_hash 为去标识化标识，不存用户名、邮箱等可直接识别个人的信息。
    action: grant | reconsent | withdraw | delete
    """
    payload = json.dumps(sorted(set(items or [])), ensure_ascii=False)
    with connect() as (conn, ph):
        _exec(conn.cursor(),
              "INSERT INTO consents(owner_hash,policy_version,items,action,ts,detail) "
              "VALUES(?,?,?,?,?,?)", ph,
              (str(owner_hash), str(policy_version), payload, action, now(),
               (detail or "")[:500]))


def _row_to_dict(row) -> dict:
    if IS_PG or not hasattr(row, "keys"):
        return dict(zip(_CONSENT_COLS, row))
    return dict(row)


def latest_consent(owner_hash: str):
    """最近一条同意事件；无记录返回 None（历史账号需重新同意）。"""
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(),
                    "SELECT id,owner_hash,policy_version,items,action,ts,detail "
                    "FROM consents WHERE owner_hash=? ORDER BY id DESC LIMIT 1",
                    ph, (str(owner_hash),))
        row = cur.fetchone()
    if not row:
        return None
    rec = _row_to_dict(row)
    try:
        rec["items"] = json.loads(rec["items"])
    except (TypeError, ValueError):
        rec["items"] = []
    return rec


def list_consents(owner_hash: str) -> list[dict]:
    """按时间顺序返回全部同意事件，供个人数据导出与争议追溯。"""
    with connect() as (conn, ph):
        cur = _exec(conn.cursor(),
                    "SELECT id,owner_hash,policy_version,items,action,ts,detail "
                    "FROM consents WHERE owner_hash=? ORDER BY id",
                    ph, (str(owner_hash),))
        rows = cur.fetchall()
    out = []
    for row in rows:
        rec = _row_to_dict(row)
        try:
            rec["items"] = json.loads(rec["items"])
        except (TypeError, ValueError):
            rec["items"] = []
        out.append(rec)
    return out
