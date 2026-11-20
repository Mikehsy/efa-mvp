"""认证层：注册 / 登录 / 会话 / 个人信息权利（PIPL）。

- 密码用 hashlib.pbkdf2_hmac + 随机盐，绝不存明文。
- 会话存 Streamlit session_state；正式版改为签名 cookie（见改造方案）。
- 当前用户 id 用于数据隔离：db.fetch_rows/replace_rows 都按 owner_id 过滤。
- 同意、撤回、重新同意、注销均持久化到 consents 表（去标识化留痕）。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

import db
import privacy
from logging_config import audit

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


def _trace(action: str, owner_id, **fields) -> None:
    """文件审计是补充留痕，失败不得影响 DB 中的合规记录。"""
    try:
        audit(action, page="隐私与账号", target=str(owner_id), **fields)
    except Exception:
        pass


def register(username: str, password: str, email: str = "",
             consents=None, policy_version: str = "") -> tuple[bool, str]:
    """注册账号。

    consents 为界面传入的同意项列表；UI 必须显式传入（空列表会被拒绝）。
    consents is None 仅用于测试夹具与非界面脚本，由调用方自行保证已取得同意。
    """
    username = (username or "").strip()
    if err := validate_username(username):
        return False, err
    if err := validate_password(password):
        return False, err
    if consents is not None and not privacy.consent_valid(consents):
        return False, "请先阅读《隐私政策》并勾选全部同意项后再注册。"
    if db.get_user(username):
        return False, "用户名已存在。"
    digest, salt = hash_password(password)
    try:
        owner_id = db.create_user(username, email.strip(), digest, salt)
        if consents is not None:
            version = policy_version or privacy.POLICY_VERSION
            db.record_consent(privacy.owner_hash(owner_id), version,
                              consents, "grant", detail="注册时同意")
            _trace("pipl_consent_grant", owner_id, policy_version=version,
                   items=list(consents))
        return True, "注册成功，请登录。"
    except Exception:
        return False, "注册失败，请稍后重试。"


def login(username: str, password: str) -> tuple[bool, str]:
    user = db.get_user((username or "").strip())
    # withdrawn：已撤回同意，允许验证身份后进入「重新同意」流程，但不能访问业务数据。
    if not user or user.get("status") not in ("active", "withdrawn"):
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
    st_module.session_state.pop("_efa_consent_ok", None)


# ------------------------------------------------------------------ 同意状态与个人信息权利
CONSENT_OK = "ok"
CONSENT_MISSING = "missing"
CONSENT_WITHDRAWN = "withdrawn"
CONSENT_VERSION = "version"


def consent_status(user) -> str:
    """校验当前账号是否已就现行政策版本取得有效同意。

    历史账号（升级前注册、无同意记录）与政策版本变更的账号都必须重新同意。
    """
    if not user or not user.get("id"):
        return CONSENT_MISSING
    uid = user["id"]
    # 一律以数据库现值为准：调用方持有的 user 常是登录时的快照，
    # 撤回 / 重新同意之后会过期，据此判断会得出错误结论。
    fresh = db.get_user_by_id(uid)
    if not fresh:
        return CONSENT_MISSING
    if fresh.get("status") == "withdrawn":
        return CONSENT_WITHDRAWN
    rec = db.latest_consent(privacy.owner_hash(uid))
    if not rec:
        return CONSENT_MISSING
    # 以同意事件为准，不依赖调用方传入的 user 快照是否最新。
    if rec.get("action") == "withdraw":
        return CONSENT_WITHDRAWN
    if rec.get("action") == "delete":
        return CONSENT_MISSING
    if rec.get("policy_version") != privacy.POLICY_VERSION:
        return CONSENT_VERSION
    if not privacy.consent_valid(rec.get("items")):
        return CONSENT_MISSING
    return CONSENT_OK


def grant_consent(owner_id, items, *, reason: str = "重新同意") -> tuple[bool, str]:
    """记录（重新）同意并恢复账号。"""
    if not privacy.consent_valid(items):
        return False, "必须勾选全部同意项后方可继续使用本服务。"
    db.record_consent(privacy.owner_hash(owner_id), privacy.POLICY_VERSION,
                      items, "grant", detail=reason)
    db.set_user_status(owner_id, "active")
    _trace("pipl_consent_grant", owner_id, policy_version=privacy.POLICY_VERSION,
           reason=reason, items=list(items))
    return True, "已记录你的同意。"


def withdraw_consent(owner_id) -> tuple[bool, str]:
    """PIPL 第 15 条：撤回同意后停止基于同意的处理，账号停用直至重新同意。"""
    db.record_consent(privacy.owner_hash(owner_id), privacy.POLICY_VERSION,
                      [], "withdraw", detail="用户主动撤回同意")
    db.set_user_status(owner_id, "withdrawn")
    _trace("pipl_consent_withdraw", owner_id, policy_version=privacy.POLICY_VERSION)
    return True, "已撤回同意，账号已停用。"


def delete_account(owner_id) -> tuple[bool, str]:
    """PIPL 第 47 条：注销，删除账号信息与全部业务数据，仅留去标识化合规记录。"""
    db.record_consent(privacy.owner_hash(owner_id), privacy.POLICY_VERSION,
                      [], "delete", detail="用户注销账号")
    db.delete_account_data(owner_id)
    _trace("pipl_account_delete", owner_id, policy_version=privacy.POLICY_VERSION)
    return True, "账号及全部个人数据已删除。"


def collect_personal_data(owner_id, *, mask: bool = False) -> bytes:
    """PIPL 第 45 条：查阅、复制并可携带地转移个人数据（JSON）。"""
    frames = {}
    for kind in ("athlete", "test", "standards"):
        records, _ = db.load_frame(owner_id, kind)
        frames[kind] = records or []
    account = db.get_user_by_id(owner_id) or {}
    consents = db.list_consents(privacy.owner_hash(owner_id))
    return privacy.export_bytes(
        privacy.build_export(owner_id, account, frames, consents, mask=mask))
