"""个人信息保护（PIPL）合规配置、同意项与隐私政策正文（单一来源）。

技术措施与法律义务分离：本模块提供
  - 政策版本与正文（界面展示、注册同意、重新同意共用同一份文本）
  - 同意项定义（一般同意 + 敏感个人信息/跨境/未成年人的单独同意）
  - 必须由运营方填写的配置（处理者名称、联系方式、保存期限…）
  - 个人数据导出与脱敏

⚠️ 运营方仍需自行完成：真实主体信息填报、个人信息保护影响评估（PIA）、
   跨境传输的合法路径、未成年人监护人同意的实际取得与留档、法务审阅。
   见 docs/PIPL合规实施说明.md。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime


def _cfg(name: str, default: str = "") -> str:
    """云端从 Streamlit secrets 读取，本地回退环境变量。"""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


# ------------------------------------------------------------------ 运营方配置
# 以下配置须由运营方确认；前两项为正式商用必填的真实信息。
OPERATOR_NAME = _cfg("EFA_OPERATOR_NAME", "【待填写：运营主体全称】")
PRIVACY_CONTACT = _cfg("EFA_PRIVACY_CONTACT", "【待填写：个人信息保护负责人邮箱/电话】")
RETENTION_DAYS = int(_cfg("EFA_RETENTION_DAYS", "730"))
OVERSEAS_REGION = _cfg("EFA_OVERSEAS_REGION", "东北亚（东京）")

POLICY_VERSION = _cfg("EFA_POLICY_VERSION", "2026-11-18-v1")
EFFECTIVE_DATE = _cfg("EFA_POLICY_EFFECTIVE_DATE", "2026-11-18")
HASH_SALT = _cfg("EFA_PRIVACY_HASH_SALT", "efa-pipl-v1")


def operator_configured() -> bool:
    """运营方是否已填写真实主体信息与联系方式。"""
    return "【待填写" not in OPERATOR_NAME and "【待填写" not in PRIVACY_CONTACT


# ------------------------------------------------------------------ 同意项
# PIPL 第 29 条：处理敏感个人信息应取得「单独同意」；
# 第 39 条：向境外提供个人信息应取得「单独同意」；
# 第 31 条：处理不满 14 周岁未成年人信息须取得监护人同意并制定专门规则。
CONSENT_ITEMS = (
    ("basic", "我已阅读并同意《隐私政策》，同意为提供本服务而处理我的账号信息与录入的业务数据。", True),
    ("sensitive", "单独同意：同意处理运动员的出生日期、体成分、成绩、FMS 疼痛标记等健康与体能相关敏感个人信息。", True),
    ("cross_border", "单独同意：同意将上述个人信息传输、存储至境外（详见政策第四节）。", True),
    ("minor", "我确认：如录入不满 14 周岁运动员的信息，已事先取得其父母或监护人同意，并已向其告知本政策。", True),
)

REQUIRED_ITEMS = tuple(k for k, _, required in CONSENT_ITEMS if required)


def consent_valid(items) -> bool:
    """校验同意项覆盖了全部必需项。"""
    got = set(items or [])
    return set(REQUIRED_ITEMS).issubset(got)


def owner_hash(owner_id) -> str:
    """去标识化标识：账号注销后保留合规留痕，但不含用户名/邮箱等个人信息。"""
    return hashlib.sha256(f"{HASH_SALT}:{owner_id}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 政策正文
def policy_text() -> str:
    """生成完整隐私政策正文（界面与文档共用，避免版本漂移）。"""
    return f"""# 隐私政策

**版本：{POLICY_VERSION}　生效日期：{EFFECTIVE_DATE}**

本政策依据《中华人民共和国个人信息保护法》（以下简称"PIPL"）制定，说明
**{OPERATOR_NAME}**（以下简称"我们"）如何通过"运动员体能与功能评估系统"处理你的个人信息。

## 一、我们处理哪些个人信息

| 类别 | 具体字段 | 来源 |
| --- | --- | --- |
| 账号信息 | 用户名、密码（仅存不可逆哈希与随机盐）、邮箱（选填）、注册时间 | 你注册时提供 |
| 运动员档案 | 姓名、性别、出生日期、所属队伍、专项、备注、运动项目 | 你录入 |
| 测试与评估数据 | 成绩、测试日期、单位、标准组、测试时年龄、侧别、疼痛标记、备注 | 你录入 |
| 评分标准配置 | 达标线、优秀线、单位、方向等 | 系统内置或你编辑 |

其中**出生日期、体成分、体能成绩、FMS 疼痛标记、功能筛查结果**属于健康与体能相关
信息，在 PIPL 下按**敏感个人信息**处理，需你的单独同意。我们**不收集**身份证号、
生物识别、行踪轨迹、金融账户等信息。

## 二、处理目的与必要性

仅用于：建立运动员档案、计算得分与等级、生成趋势分析与报告、按账号隔离保存数据。
上述处理是提供本服务**所必需**的；若你不同意，我们无法为你提供需要处理个人信息
的服务（PIPL 第 16 条）。我们**不会**将你的数据用于广告、用户画像营销、自动化选拔
决策或对外售卖。

系统内置评分阈值为示例常模，报告结论不构成医疗诊断或训练处方，不得单独用于
选拔、诊断等重大决定。

## 三、保存期限与安全措施

- 保存期限：自账号最后一次写入数据起 **{RETENTION_DAYS} 天**；到期后可申请删除，
  或由运营方按留存策略清理。注销账号后立即删除账号信息与全部业务数据。
- 安全措施：密码 PBKDF2-SHA256 加盐哈希存储；按账号隔离数据访问；全站 HTTPS 传输；
  写操作审计留痕；同意与撤回、注销事件以去标识化方式留档。
- 日志与备份：运行日志写在应用容器内；云端免费方案的容器临时盘不具长期容灾能力，
  运营方应另行配置异地备份。

## 四、境外提供（单独同意项）

本服务部署于 Streamlit Community Cloud（美国），数据库使用 Supabase
（项目区域：{OVERSEAS_REGION}）。你的个人信息可能被传输、存储并在上述境外
地点处理。境外接收方为本服务的云托管与数据库服务商；我们仅在其提供服务所必需的
范围内委托处理，不会向其转移数据所有权。

> ⚠️ 合规提示：PIPL 第 38 条要求个人信息出境具备安全评估、标准合同备案或保护认证
> 等法定条件。运营方须在正式商用前完成相应法律程序，或改用境内存储方案。

## 五、你的权利（PIPL 第 44–50 条）

你有权：查阅与复制你的个人信息、更正或补充、删除、撤回同意、注销账号、
要求解释处理规则。可在「🔒 隐私与账号」页面自助完成：

- **查阅/复制**：导出账号信息、全部运动员档案、测试记录、评分标准与同意记录（JSON）。
- **更正**：在各业务页面直接编辑。
- **撤回同意**：撤回后我们停止基于同意的处理，账号随即停用；你可重新同意后恢复。
  撤回不影响撤回前已进行的处理活动。
- **删除/注销**：删除账号信息与全部业务数据；去标识化的合规留痕依法留存。
- **拒绝**：我们不以自动化决策方式对个人权益产生重大影响。

行使权利或投诉、咨询，请联系：**{PRIVACY_CONTACT}**。我们将在收到请求后
15 个工作日内答复。你也有权向履行个人信息保护职责的部门投诉。

## 六、未成年人保护

我们不向不满 14 周岁的个人直接提供注册。教练/管理员若录入不满 14 周岁运动员
信息，**必须事先取得其父母或其他监护人的同意**，并制定专门处理规则；注册时
需对此作出确认。运营方负责取得并留存该同意的证明。

## 七、政策更新

政策变更会更新版本号并**要求重新同意**；未重新同意前将无法继续使用服务。
历史同意记录以去标识化方式留存，用于证明处理活动的合法性。

## 八、委托处理与共享

除第四节的云托管与数据库服务商外，我们不向第三方共享个人信息；因法律法规
要求或为保护生命健康等法定情形除外。
"""


# ------------------------------------------------------------------ 个人数据导出
def build_export(owner_id, account: dict, frames: dict, consents: list[dict],
                 mask: bool = False) -> dict:
    """组装可携带的个人数据副本。mask=True 时对直接标识字段脱敏。"""
    def hide(v):
        if not mask or v is None or v == "":
            return v
        s = str(v)
        return s[0] + "*" * max(1, len(s) - 1) if len(s) > 1 else "*"

    athletes = list(frames.get("athlete") or [])
    tests = list(frames.get("test") or [])
    if mask:
        athletes = [{**r, "姓名": hide(r.get("姓名")), "备注": hide(r.get("备注"))}
                    for r in athletes]
        tests = [{**r, "备注": hide(r.get("备注"))} for r in tests]
    acct = dict(account or {})
    for k in ("password_hash", "salt"):
        acct.pop(k, None)          # 永不导出密码凭据
    if mask:
        acct["username"] = hide(acct.get("username"))
        acct["email"] = hide(acct.get("email"))
    return {
        "导出时间": datetime.now().isoformat(timespec="seconds"),
        "政策版本": POLICY_VERSION,
        "保存期限_天": RETENTION_DAYS,
        "脱敏": mask,
        "账号信息": acct,
        "同意记录": list(consents or []),
        "运动员档案": athletes,
        "测试与评估记录": tests,
        "评分标准": list(frames.get("standards") or []),
    }


def export_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
