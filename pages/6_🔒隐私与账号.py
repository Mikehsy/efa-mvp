"""PIPL 权利自助页：政策查阅、数据复制、撤回同意、注销账号。

本页由 inject_css() 的访问闸口保护；同意失效时闸口会先展示「重新同意」，
不会让未取得同意的会话读取或写入任何业务数据。
"""
from datetime import datetime

import pandas as pd
import streamlit as st

import auth
import db
import privacy
import utils
from utils import db_mode_enabled, inject_css, page_header

st.set_page_config(page_title="隐私与账号 | 体能与功能评估系统", page_icon="🔒", layout="wide")
inject_css()
page_header("隐私与账号", "查阅隐私政策、导出个人数据、撤回同意与注销账号", "🔒")

if not db_mode_enabled():
    st.info("当前为本地单机模式（无账号体系），本页仅在云端账号模式下可用。")
    st.stop()

u = st.session_state.get("efa_user") or {}
uid = u.get("id")
user = db.get_user_by_id(uid) or {}
owner_hash = privacy.owner_hash(uid)
history = db.list_consents(owner_hash)
last_write = db.last_data_write(uid)

if not privacy.operator_configured():
    st.warning("⚠️ 运营主体名称与个人信息保护负责人联系方式尚未配置。"
               "正式商用前须由运营方在 Streamlit Cloud Secrets 中填写真实信息："
               "`EFA_OPERATOR_NAME`、`EFA_PRIVACY_CONTACT`。")

# ------------------------------------------------------------------ 状态
st.subheader("处理活动概况")
c1, c2, c3 = st.columns(3)
c1.metric("政策版本", privacy.POLICY_VERSION)
c2.metric("同意状态", "有效" if auth.consent_status(user) == auth.CONSENT_OK else "需重新同意")
c3.metric("保存期限", f"{privacy.RETENTION_DAYS} 天")
st.caption(f"最后一次写入数据：{last_write or '暂无'}；保存期限自最后一次写入起算。"
           "到期后可在本页注销账号，或联系运营方按留存策略清理。"
           "注销将立即删除账号与全部业务数据，仅保留去标识化的合规留痕。")

# ------------------------------------------------------------------ 政策全文
with st.expander(f"《隐私政策》全文（版本 {privacy.POLICY_VERSION}）", expanded=False):
    st.markdown(privacy.policy_text())
st.caption(f"个人信息保护负责人联系方式：{privacy.PRIVACY_CONTACT}　｜　"
           f"处理者：{privacy.OPERATOR_NAME}")

# ------------------------------------------------------------------ 查阅 / 复制
st.markdown('<div class="cal-sensitive"><div class="cal-label">SENSITIVE · PIPL</div></div>',
            unsafe_allow_html=True)
st.subheader("查阅与复制我的个人数据")
st.caption("导出内容为 JSON，包含账号信息（不含密码哈希与盐）、运动员档案、"
           "测试与评估记录、评分标准配置和同意记录；可选择脱敏副本用于对外提供。")
today = datetime.now().strftime("%Y%m%d")
e1, e2 = st.columns(2)
with e1:
    st.download_button("下载完整个人数据（JSON）",
                       auth.collect_personal_data(uid),
                       file_name=f"efa_personal_data_{today}.json",
                       mime="application/json", width="stretch")
with e2:
    st.download_button("下载脱敏副本（JSON）",
                       auth.collect_personal_data(uid, mask=True),
                       file_name=f"efa_personal_data_masked_{today}.json",
                       mime="application/json", width="stretch")

with st.expander("同意与处理活动留痕", expanded=False):
    if history:
        action_label = {"grant": "同意 / 重新同意", "withdraw": "撤回同意",
                        "delete": "注销删除"}
        # 全部转成字符串，避免混合 dtype 触发 Arrow 类型推断失败。
        rows = [{
            "时间": str(r.get("ts") or ""),
            "事件": action_label.get(r.get("action"), str(r.get("action") or "")),
            "政策版本": str(r.get("policy_version") or ""),
            "同意项": "、".join(r.get("items") or []),
            "说明": str(r.get("detail") or ""),
        } for r in history]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("暂无记录。")

# ------------------------------------------------------------------ 撤回同意
st.subheader("撤回同意")
with st.form("efa_withdraw"):
    st.warning("撤回后我们停止基于同意的处理，账号随即停用，你将无法访问业务数据；"
               "可重新同意后恢复。建议撤回前先导出个人数据。撤回不影响撤回前已进行的处理。")
    ack = st.checkbox("我已阅读并了解撤回同意的后果", key="efa_withdraw_ack")
    if st.form_submit_button("撤回同意并停用账号"):
        if not ack:
            st.error("请先勾选确认。")
        else:
            auth.withdraw_consent(uid)
            st.session_state.pop("_efa_consent_ok", None)
            st.rerun()

# ------------------------------------------------------------------ 注销删除
st.subheader("注销账号并删除全部数据")
with st.form("efa_delete_account"):
    st.error("此操作不可逆：将删除账号信息、运动员档案、测试记录与评分标准配置。"
             "去标识化的合规留痕依法保留，不含可直接识别你的信息。")
    typed = st.text_input(f"请输入用户名「{user.get('username', '')}」以确认",
                          key="efa_delete_typed")
    if st.form_submit_button("永久删除账号与全部数据"):
        if (typed or "").strip() != user.get("username"):
            st.error("用户名不匹配，未执行删除。")
        else:
            auth.delete_account(uid)
            getattr(utils, "_FRAME_CACHE", {}).clear()
            auth.sign_out(st)
            st.session_state.pop("_efa_consent_ok", None)
            st.rerun()

st.markdown("---")
with st.expander("运营方仍需完成的合规事项（技术措施不替代法律义务）"):
    st.markdown("""
1. 填写真实运营主体名称、个人信息保护负责人联系方式，并完成法务审阅。
2. 完成个人信息保护影响评估（PIA），尤其是敏感个人信息与未成年人信息处理。
3. 就数据出境落实 PIPL 第 38 条的合法路径（安全评估 / 标准合同备案 / 保护认证），或改用境内存储。
4. 实际取得并留存不满 14 周岁运动员监护人的同意证明，制定专门处理规则。
5. 按保存期限执行留存策略复核与清理，并配置异地备份（云端免费方案的容器临时盘不具容灾能力）。
6. 完成域名、备案、ICP 等属地化要求，保留政策版本与同意记录以备查验。

详见 `docs/PIPL合规实施说明.md`。
""")
