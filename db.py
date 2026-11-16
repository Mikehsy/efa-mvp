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
            cur.execute(DDL_USERS.format(
                ID="BIGSERIAL" if IS_PG else "INTEGER",
                AUTO="" if IS_PG else "AUTOINCREMENT"))
            cur.execute(DDL_DATA)
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
            cur.execute("SELECT to_regclass('public.users'), to_regclass('public.user_data')")
            row = cur.fetchone()
            return bool(row and row[0] and row[1])
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users','user_data')")
        return len(cur.fetchall()) == 2
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
