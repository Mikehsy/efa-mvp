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


@contextmanager
def connect():
    """返回 (conn, ph)；ph 是该驱动的占位符（ psycopg2=%s, sqlite=? ）。"""
    if IS_PG:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        ph = "%s"
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        ph = "?"
    try:
        # 幂等建表：保证任何入口（含首次注册）都能安全使用，云端无需手工初始化。
        cur = conn.cursor()
        if IS_PG:
            cur.execute(DDL_USERS.format(ID="BIGSERIAL", AUTO=""))
        else:
            cur.execute(DDL_USERS.format(ID="INTEGER", AUTO="AUTOINCREMENT"))
        cur.execute(DDL_DATA)
        yield conn, ph
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _exec(cur, sql: str, ph: str, params=()):
    return cur.execute(sql.replace("?", ph), params)


def init_db() -> None:
    with connect() as (conn, ph):
        cur = conn.cursor()
        if IS_PG:
            cur.execute(DDL_USERS.format(ID="BIGSERIAL", AUTO=""))
        else:
            cur.execute(DDL_USERS.format(ID="INTEGER", AUTO="AUTOINCREMENT"))
        cur.execute(DDL_DATA)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ 用户
def create_user(username, email, password_hash, salt, role="user"):
    with connect() as (conn, ph):
        cur = conn.cursor()
        _exec(cur,
              "INSERT INTO users(username,email,password_hash,salt,role,created_at) "
              "VALUES(?,?,?,?,?,?)", ph,
              (username, email, password_hash, salt, role, now()))
        if IS_PG:
            cur.execute("SELECT LASTVAL()")
            return int(cur.fetchone()[0])
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
