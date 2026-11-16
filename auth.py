"""认证层（MVP）：注册 / 登录 / 会话，零新增依赖。

- 密码用 hashlib.pbkdf2_hmac + 随机盐，绝不存明文。
- 会话存 Streamlit session_state；正式版改为签名 cookie（见改造方案）。
- 当前用户 id 用于数据隔离：db.fetch_rows/replace_rows 都按 owner_id 过滤。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

import db

SESSION_KEY = "efa_user"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 200_000).hex()
    return digest, salt


def verify_password(password: str, digest: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, digest)


def validate_username(name: str) -> str | None:
    """返回错误信息或 None。用户名 3–20 位字母数字下划线。"""
    name = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
        return "用户名需为 3–20 位字母、数字或下划线。"
    return None


def validate_password(pw: str) -> str | None:
    if len(pw or "") < 6:
        return "密码至少 6 位。"
    return None


def register(username: str, password: str, email: str = "") -> tuple[bool, str]:
    username = (username or "").strip()
    if err := validate_username(username):
        return False, err
    if err := validate_password(password):
        return False, err
    if db.get_user(username):
        return False, "用户名已存在。"
    digest, salt = hash_password(password)
    try:
        db.create_user(username, email.strip(), digest, salt)
        return True, "注册成功，请登录。"
    except Exception:
        return False, "注册失败，请稍后重试。"


def login(username: str, password: str) -> tuple[bool, str]:
    user = db.get_user((username or "").strip())
    if not user or user.get("status") != "active":
        return False, "用户名或密码错误。"
    if not verify_password(password, user["password_hash"], user["salt"]):
        return False, "用户名或密码错误。"
    return True, user


def current_user(st_module) -> dict | None:
    return st_module.session_state.get(SESSION_KEY)


def sign_in(st_module, user: dict) -> None:
    st_module.session_state[SESSION_KEY] = {
        "id": user["id"], "username": user["username"],
        "role": user.get("role", "user"),
    }


def sign_out(st_module) -> None:
    st_module.session_state.pop(SESSION_KEY, None)
