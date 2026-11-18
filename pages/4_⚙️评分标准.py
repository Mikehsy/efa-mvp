"""页面 4：评分标准 —— 阈值表格编辑、待配置清单、自定义项目（替代原第 4 页）。"""
import pandas as pd
import streamlit as st

from utils import (
    AGE_GROUPS, AGE_GROUP_LABEL, CONTINUOUS_ITEMS, DESCRIPTIVE_ITEMS, FMS_CLEARING,
    FMS_MOVEMENTS, GENDERS, STANDARD_COLS, STANDARDS_DISCLAIMER, ensure_files,
    generate_default_standards, inject_css, item_catalog, load_standards,
    page_header, render_sidebar, save_standards,
)

st.set_page_config(page_title="评分标准", page_icon="⚙️", layout="wide")
inject_css()
ensure_files()
page_header("评分标准", "按性别 / 年龄组编辑阈值，查看待配置清单，管理自定义项目", "⚙️")
render_sidebar()

st.markdown(f"""<div class="card note">
<b>{STANDARDS_DISCLAIMER}</b>
<p class="hint">内置 216 行阈值（27 项 × 2 性别 × 4 年龄组，已取消大师组）。其中握力、坐位体前屈、
静息心率、30米冲刺、Yo-Yo、引体向上、纵跳等有出处项已替换为已确认常模（中国足协 / 协和 /
国家学生体质标准 / 临床规范，逐项出处见 confirmed_standards.py 与 docs），其余仍为示例常模，
请按本队实测替换。未配置（是否评分 = 否 或阈值留空）的组合不评分，成绩显示「—」；
计分按达标线=0 分、优秀线=100 分换算，阈值一经修改，历史得分全部实时重算。</p></div>""", unsafe_allow_html=True)

std = load_standards()
tab_edit, tab_pending, tab_custom = st.tabs(["阈值表格编辑", "待配置清单", "自定义项目"])

# ------------------------------------------------------------------ 阈值编辑
with tab_edit:
    st.caption("按「性别 + 年龄组 + 类别」筛选后直接改表格，保存即写入 data/standards.csv。")
    e1, e2, e3 = st.columns(3)
    with e1:
        gender = st.selectbox("性别", GENDERS, key="std_gender")
    with e2:
        ag = st.selectbox("年龄组", AGE_GROUPS,
                          format_func=lambda x: AGE_GROUP_LABEL[x], key="std_age")
    with e3:
        cats = ["全部"] + sorted(std["类别"].dropna().astype(str).unique().tolist()) if not std.empty else ["全部"]
        cat = st.selectbox("类别", cats, key="std_cat")

    view = std[(std["性别"] == gender) & (std["年龄组"] == ag)].copy()
    if cat != "全部":
        view = view[view["类别"].astype(str) == cat]
    st.caption(f"当前组合：{gender}|{ag}（{AGE_GROUP_LABEL[ag]}）　共 {len(view)} 行")

    if view.empty:
        st.info("该分组暂无标准行。可在「自定义项目」新增项目，或在「待配置清单」恢复内置示例标准。")
    else:
        editable = view[["测试项目", "类别", "单位", "方向", "达标线", "优秀线",
                         "评分类型", "是否评分"]].reset_index(drop=True)
        edited = st.data_editor(
            editable, hide_index=True, width="stretch", num_rows="fixed",
            disabled=["测试项目", "类别", "单位", "评分类型"],
            column_config={
                "测试项目": st.column_config.TextColumn("测试项目", width="medium"),
                "达标线": st.column_config.NumberColumn("达标线（60 分）", format="%.2f"),
                "优秀线": st.column_config.NumberColumn("优秀线（100 分）", format="%.2f"),
                "方向": st.column_config.SelectboxColumn("方向", options=["越大越好", "越小越好"]),
                "是否评分": st.column_config.SelectboxColumn("是否评分", options=["是", "否"]),
            },
            key="std_editor",
        )
        if st.button("保存本分组阈值", type="primary", width="stretch"):
            full = load_standards().copy()
            for _, r in edited.iterrows():
                mask = ((full["测试项目"] == r["测试项目"]) & (full["性别"] == gender)
                        & (full["年龄组"] == ag))
                for c in ("达标线", "优秀线", "方向", "是否评分"):
                    full.loc[mask, c] = r[c]
            for c in ("达标线", "优秀线"):
                full[c] = pd.to_numeric(full[c], errors="coerce")
            save_standards(full)
            st.success("已保存。历史记录的得分 / 等级会按新阈值自动重算。")
            st.rerun()

# ------------------------------------------------------------------ 待配置清单
with tab_pending:
    pending = std[(std["是否评分"].astype(str) != "是")
                  | std["达标线"].isna() | std["优秀线"].isna()].copy()
    st.metric("待配置组合数", f"{len(pending)} / {len(std)}")
    st.caption("以下组合不评分，录入成绩时得分显示「—」。在「阈值表格编辑」里补齐达标线 / 优秀线"
               "并把「是否评分」改为「是」即可启用。")
    if pending.empty:
        st.success("全部组合均已配置。")
    else:
        st.dataframe(
            pending[["测试项目", "性别", "年龄组", "类别", "单位", "方向",
                     "达标线", "优秀线", "是否评分"]],
            width="stretch", hide_index=True,
            column_config={"达标线": st.column_config.NumberColumn("达标线", format="%.2f"),
                           "优秀线": st.column_config.NumberColumn("优秀线", format="%.2f")})
        grouped = (pending.groupby(["年龄组", "性别"]).size().reset_index(name="待配置数"))
        st.markdown("**按分组汇总**")
        st.dataframe(grouped, width="stretch", hide_index=True)

    st.markdown("---")
    st.subheader("恢复 / 重建内置示例标准")
    st.caption("会用 27 项 × 2 性别 × 4 年龄组 = 216 行内置标准覆盖当前标准表"
               "（含已确认出处项，其余为示例常模），同时清除自定义项目与手工修改的阈值。")
    confirm = st.checkbox("我确认要恢复内置标准")
    if st.button("恢复内置标准（216 行）", disabled=not confirm, width="stretch"):
        save_standards(generate_default_standards())
        st.success("内置示例标准已恢复")
        st.rerun()

# ------------------------------------------------------------------ 自定义项目
with tab_custom:
    st.caption("新增的项目会自动出现在「测试与评估」的录入下拉框中；系统为它生成 "
               "2 性别 × 4 年龄组共 8 行标准记录。")
    catalog = item_catalog()
    existing = set(catalog["测试项目"].astype(str))

    with st.form("form_custom_item", clear_on_submit=True):
        name = st.text_input("项目名称 *", placeholder="例如：30秒波比跳")
        n1, n2, n3 = st.columns(3)
        with n1:
            unit = st.text_input("单位", value="次")
        with n2:
            direction = st.selectbox("方向", ["越大越好", "越小越好"])
        with n3:
            category = st.text_input("类别", value="自定义")
        kind = st.selectbox("评分类型", ["continuous", "descriptive"],
                            format_func=lambda x: "continuous（0～100 分）" if x == "continuous"
                            else "descriptive（仅记录不评分）")
        v1, v2 = st.columns(2)
        with v1:
            pass_line = st.number_input("达标线（60 分）", value=0.0, step=0.1, format="%.2f")
        with v2:
            excellent_line = st.number_input("优秀线（100 分）", value=0.0, step=0.1, format="%.2f")
        enabled = st.checkbox("立即启用评分（需填好达标线 / 优秀线）", value=False)
        submitted = st.form_submit_button("新增自定义项目", type="primary", width="stretch")

    if submitted:
        clean = name.strip()
        usable = (kind == "continuous" and enabled and pass_line != excellent_line
                  and pass_line != 0 and excellent_line != 0)
        if not clean:
            st.error("请填写项目名称。")
        elif clean in existing:
            st.error("该项目已存在，请换一个名称。")
        else:
            rows = []
            for g in GENDERS:
                for a in AGE_GROUPS:
                    rows.append({
                        "测试项目": clean, "性别": g, "年龄组": a,
                        "达标线": pass_line if usable else "",
                        "优秀线": excellent_line if usable else "",
                        "单位": unit.strip(), "方向": direction,
                        "类别": category.strip() or "自定义", "评分类型": kind,
                        "是否评分": "是" if usable else "否",
                    })
            save_standards(pd.concat([std, pd.DataFrame(rows, columns=STANDARD_COLS)],
                                     ignore_index=True))
            st.success(f"已新增「{clean}」及其 {len(rows)} 行标准记录")
            st.rerun()

    st.markdown("---")
    st.subheader("删除自定义项目")
    builtin = (set(CONTINUOUS_ITEMS) | set(DESCRIPTIVE_ITEMS)
               | {n for n, _, _ in FMS_MOVEMENTS} | set(FMS_CLEARING))
    current = load_standards()
    custom = sorted(set(current["测试项目"].astype(str)) - builtin) if not current.empty else []
    if not custom:
        st.info("目前没有自定义项目。")
    else:
        d1, d2 = st.columns([2, 1])
        with d1:
            target = st.selectbox("选择自定义项目", options=custom)
        with d2:
            ok = st.checkbox("确认删除该项目")
            if st.button("删除项目及其标准行", disabled=not ok, width="stretch"):
                save_standards(current[current["测试项目"] != target])
                st.success(f"已删除「{target}」")
                st.rerun()
