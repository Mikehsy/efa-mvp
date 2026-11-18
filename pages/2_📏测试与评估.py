"""页面 2：测试与评估 —— 体能单项 / 批量、FMS 录入矩阵、历史与导出、个人成绩单。"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from utils import (
    FMS_CLEARING, FMS_MOVEMENTS, FMS_PAIN_NOTE, FMS_PAIN_WARNING, FMS_RISK_LINE,
    SPORT_UNKNOWN, STANDARDS_DISCLAIMER, append_row, append_rows, athlete_map,
    build_fms_rows, build_report, calc_age, calc_score, csv_download, delete_rows,
    empty_state, ensure_files, fms_sessions, inject_css, item_catalog, load_athletes,
    load_standards, load_tests, lookup_standard, page_header, render_sidebar,
    score_level, sport_map, standard_group, with_names,
)

st.set_page_config(page_title="测试与评估", page_icon="📏", layout="wide")
inject_css()
ensure_files()
page_header("测试与评估", "体能单项 / 批量录入、FMS 完整版筛查矩阵、历史导出与个人成绩单", "📏")
render_sidebar()
st.caption(f"评分标准：{STANDARDS_DISCLAIMER}。未配置组合不评分，得分显示「—」；"
           "FMS 0～3 原始分不进入 0～100 分体系。")

names = athlete_map()
if not names:
    empty_state("还没有运动员档案，请先到「运动员管理」添加运动员。")

athletes = load_athletes()
a_info = {str(r["运动员ID"]): (str(r["性别"]), r["出生日期"])
          for _, r in athletes.iterrows()}
sports = sport_map()
if SPORT_UNKNOWN in sports.values():
    st.warning("有运动员尚未填写必填的「运动项目」，其记录会标记为「未填写」且无法按项目生成报告。"
               "请先到「运动员管理 → 修改档案」补全。")
catalog = item_catalog()
cat_idx = catalog.set_index("测试项目")
std = load_standards()
physical_items = catalog[catalog["测试类别"].isin(["体能", "身体形态"])]["测试项目"].tolist()
all_items = catalog["测试项目"].tolist()


def snapshot(aid, day):
    gender, birth = a_info.get(str(aid), ("", ""))
    return standard_group(gender, birth, day), calc_age(birth, day)


def make_row(aid, day, item, value, note=""):
    meta = cat_idx.loc[item]
    group, age = snapshot(aid, day)
    return {
        "运动员ID": str(aid), "测试日期": day.isoformat(), "测试项目": item,
        "成绩": value, "单位": meta["单位"], "备注": note,
        "测试类别": meta["测试类别"], "评分类型": meta["评分类型"], "侧别": "",
        "标准组": group, "测试时年龄": age if age is not None else "", "疼痛标记": "",
        "运动项目": sports.get(str(aid), SPORT_UNKNOWN),
    }


tab_single, tab_batch, tab_fms, tab_history, tab_report = st.tabs(
    ["体能单项录入", "体能批量录入", "FMS 录入矩阵", "历史记录与导出", "个人成绩单"])

# ------------------------------------------------------------------ 体能单项
with tab_single:
    left, right = st.columns([1.1, 1])
    with left:
        aid = st.selectbox("运动员", options=list(names.keys()),
                           format_func=lambda x: f"{x} · {names[x]}", key="single_aid")
        day = st.date_input("测试日期", value=date.today(), key="single_day")
        item = st.selectbox("测试项目", physical_items, key="single_item")
        meta = cat_idx.loc[item]
        group, age = snapshot(aid, day)
        st.info(f"本次标准组快照：**{group}** ｜ 测试时年龄：{age} 岁 ｜ "
                f"运动项目：**{sports.get(str(aid), SPORT_UNKNOWN)}**"
                "（均为写入时快照，不随档案修改而漂移）")
        value = st.number_input(f"成绩（单位：{meta['单位']}）", value=0.0,
                                step=0.1, format="%.2f", key="single_value")
        note = st.text_input("备注", placeholder="测试条件、场地、状态等（选填）", key="single_note")
        if st.button("保存这条测试与评估记录", type="primary", width="stretch"):
            append_row("test", make_row(aid, day, item, value, note.strip()))
            st.success(f"{names[aid]} 的「{item}」已保存")
            st.rerun()

    with right:
        ref = lookup_standard(std, item, group)
        if ref is None:
            st.markdown(f"""<div class="card note">
            <b>{item} · 该组合未配置</b>
            <p class="hint">标准组 {group} 暂无达标线 / 优秀线或已关闭评分，成绩不评分、显示「—」。
            请到左侧「评分标准」页配置。</p></div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div class="card">
            <b>{item} · 参考标准（{group}）</b>
            <p class="hint">达标线：{ref['pass']:g} {ref['unit']}（0 分）<br>
            优秀线：{ref['excellent']:g} {ref['unit']}（100 分）<br>
            方向：{ref['direction']}　｜　类别：{meta['类别']}</p></div>""", unsafe_allow_html=True)
        preview = calc_score(item, value, group, std) if meta["评分类型"] == "continuous" else None
        c1, c2 = st.columns(2)
        c1.metric("本次预计得分", f"{preview:.1f}" if preview is not None else "—")
        c2.metric("等级", score_level(preview))
        st.caption("评分类型：" + str(meta["评分类型"]) +
                   "；阈值在「评分标准」页按性别 / 年龄组编辑，改动后历史得分自动重算。")

# ------------------------------------------------------------------ 体能批量
with tab_batch:
    st.caption("一次测试多个项目时，直接在「成绩」列填写数字，留空的项目不会保存。")
    b1, b2 = st.columns(2)
    with b1:
        aid_b = st.selectbox("运动员", options=list(names.keys()),
                             format_func=lambda x: f"{x} · {names[x]}", key="batch_aid")
    with b2:
        day_b = st.date_input("测试日期", value=date.today(), key="batch_day")
    note_b = st.text_input("备注（会加到本次所有记录）", key="batch_note")
    group_b, age_b = snapshot(aid_b, day_b)
    st.info(f"本次标准组快照：**{group_b}** ｜ 测试时年龄：{age_b} 岁 ｜ "
            f"运动项目：**{sports.get(str(aid_b), SPORT_UNKNOWN)}**（写入本次每条记录的快照）")

    units = [cat_idx.loc[i, "单位"] for i in physical_items]
    kinds = [cat_idx.loc[i, "评分类型"] for i in physical_items]
    passes, excellents = [], []
    for i in physical_items:
        r = lookup_standard(std, i, group_b)
        passes.append(r["pass"] if r else None)
        excellents.append(r["excellent"] if r else None)
    template = pd.DataFrame({
        "测试项目": physical_items, "单位": units, "评分类型": kinds,
        "达标线": passes, "优秀线": excellents,
        "成绩": [float("nan")] * len(physical_items),
    })
    edited = st.data_editor(
        template, hide_index=True, width="stretch",
        disabled=["测试项目", "单位", "评分类型", "达标线", "优秀线"],
        column_config={"成绩": st.column_config.NumberColumn("成绩", format="%.2f")},
        key="batch_editor",
    )
    filled = edited[edited["成绩"].notna()]
    st.write(f"已填写 {len(filled)} 个项目")
    if st.button("批量保存", type="primary", width="stretch", disabled=filled.empty):
        rows = [make_row(aid_b, day_b, r["测试项目"], float(r["成绩"]), note_b.strip())
                for _, r in filled.iterrows()]
        append_rows("test", rows)
        st.success(f"已保存 {len(rows)} 条记录")
        st.rerun()

# ------------------------------------------------------------------ FMS 矩阵
with tab_fms:
    st.caption("FMS 完整版：7 个动作 0～3 分、5 个双侧动作分别记左右侧、3 项排除性测试；"
               "双侧取较低侧计入，总分 0～21，<=14 分为较高风险。")
    f1, f2 = st.columns(2)
    with f1:
        aid_f = st.selectbox("运动员", options=list(names.keys()),
                             format_func=lambda x: f"{x} · {names[x]}", key="fms_aid")
    with f2:
        day_f = st.date_input("测试日期", value=date.today(), key="fms_day")
    note_f = st.text_input("备注", placeholder="筛查条件、场地等（选填）", key="fms_note")
    group_f, age_f = snapshot(aid_f, day_f)
    st.caption(f"本次标准组快照：{group_f} ｜ 测试时年龄：{age_f} 岁 ｜ "
               f"运动项目：{sports.get(str(aid_f), SPORT_UNKNOWN)}"
               "（FMS 总分与风险线不按年龄组缩放，但同样按运动项目归档）")

    st.markdown("**① 7 个动作评分矩阵**")
    header = st.columns([2.2, 1, 1, 0.8])
    header[0].caption("动作")
    header[1].caption("左 / 单侧（0～3）")
    header[2].caption("右（仅双侧）")
    header[3].caption("疼痛")
    scores, pain = {}, set()
    for name, bilateral, _ in FMS_MOVEMENTS:
        cells = st.columns([2.2, 1, 1, 0.8])
        cells[0].write(name.replace("FMS", ""))
        lv = cells[1].number_input(f"{name} 左", min_value=0, max_value=3, value=2, step=1,
                                   key=f"fms_l_{name}", label_visibility="collapsed")
        rv = cells[2].number_input(f"{name} 右", min_value=0, max_value=3, value=2, step=1,
                                   key=f"fms_r_{name}", label_visibility="collapsed",
                                   disabled=not bilateral)
        flag = cells[3].checkbox(f"{name} 疼痛", key=f"fms_p_{name}", label_visibility="collapsed")
        scores[name] = (int(lv), int(rv)) if bilateral else (int(lv),)
        if flag:
            pain.add(name)

    st.markdown("**② 3 项排除性测试（阳性则对应动作记 0 分）**")
    clearing = {}
    cc = st.columns(3)
    for col, name in zip(cc, FMS_CLEARING):
        clearing[name] = col.selectbox(name.replace("FMS", ""), ["通过", "阳性"],
                                       key=f"fms_c_{name}")
        col.caption("对应动作：" + FMS_CLEARING[name].replace("FMS", ""))
    positives = [k for k, v in clearing.items() if v == "阳性"]
    if positives:
        st.error(FMS_PAIN_WARNING + "（" + "、".join(p.replace("FMS", "") for p in positives) + " 阳性）")

    preview_rows = pd.DataFrame(build_fms_rows(aid_f, day_f, scores, clearing, pain, note_f))
    preview_rows["成绩数值"] = pd.to_numeric(preview_rows["成绩"], errors="coerce")
    ps = fms_sessions(preview_rows)
    if not ps.empty:
        r = ps.iloc[0]
        p1, p2, p3 = st.columns(3)
        p1.metric("本次 FMS 总分", f"{int(r['FMS总分'])} / 21")
        p2.metric("风险", str(r["风险"]), delta=f"风险线 {FMS_RISK_LINE} 分")
        p3.metric("含疼痛", str(r["含疼痛"]))
        if r["疼痛警示"]:
            st.error(r["疼痛警示"])
        if r["结果说明"]:
            st.caption("总分加注：" + FMS_PAIN_NOTE)
        if str(r["不对称清单"]).strip():
            st.warning("左右不对称清单（左右差 >= 1）：" + str(r["不对称清单"]))

    if st.button("保存本次 FMS 筛查（15 行）", type="primary", width="stretch"):
        n = append_rows("test", build_fms_rows(aid_f, day_f, scores, clearing, pain, note_f))
        st.success(f"已保存 {n} 行；7 动作取较低侧求和，FMS 原始分不进入体能得分")
        st.rerun()

# ------------------------------------------------------------------ 历史与导出
with tab_history:
    tests = load_tests()
    if tests.empty:
        st.info("还没有测试与评估记录。")
    else:
        h1, h2, h3, h4, h5 = st.columns([1, 1, 1, 1, 1])
        with h1:
            sel_a = st.multiselect("运动员", options=list(names.keys()),
                                   format_func=lambda x: names[x], default=list(names.keys()))
        with h2:
            cats = sorted(tests["测试类别"].unique().tolist())
            sel_c = st.multiselect("测试类别", options=cats, default=cats)
        with h3:
            sel_i = st.multiselect("测试项目", options=all_items, default=all_items)
        with h4:
            sport_opts = sorted(tests["运动项目"].astype(str).unique().tolist())
            sel_s = st.multiselect("运动项目", options=sport_opts, default=sport_opts)
        with h5:
            period = st.date_input("日期范围",
                                   value=(date.today() - timedelta(days=365), date.today()))
        start, end = (period if isinstance(period, (tuple, list)) and len(period) == 2
                      else (date.today() - timedelta(days=365), date.today()))
        view = tests[tests["运动员ID"].isin(sel_a) & tests["测试类别"].isin(sel_c)
                     & tests["测试项目"].isin(sel_i)
                     & tests["运动项目"].astype(str).isin(sel_s)
                     & (tests["测试日期"] >= pd.Timestamp(start))
                     & (tests["测试日期"] <= pd.Timestamp(end))].copy()
        view = with_names(view).sort_values("测试日期", ascending=False)
        if view.empty:
            st.warning("当前筛选条件下没有记录，请放宽筛选条件。")
        else:
            view["测试日期"] = pd.to_datetime(view["测试日期"], errors="coerce").dt.strftime("%Y-%m-%d")
            cols = ["记录ID", "姓名", "运动项目", "测试日期", "测试类别", "测试项目", "侧别",
                    "结果显示", "单位", "标准组", "测试时年龄", "得分", "等级",
                    "FMS总分", "不对称标记", "疼痛标记", "备注"]
            st.dataframe(view[cols], width="stretch", hide_index=True,
                         column_config={"得分": st.column_config.NumberColumn("得分", format="%.1f")})
            csv_download(view[cols], "测试与评估记录.csv", "下载筛选结果 CSV")

            st.markdown("---")
            d1, d2, d3 = st.columns([2, 1, 1])
            with d1:
                rid = st.selectbox("选择要删除的记录编号", options=view["记录ID"].tolist())
            with d2:
                ok = st.checkbox("确认删除")
            with d3:
                if st.button("删除该记录", disabled=not ok, width="stretch"):
                    delete_rows("test", [rid])
                    st.success("已删除")
                    st.rerun()

            fms_keys = (view[view["测试类别"] == "FMS"][["运动员ID", "测试日期"]]
                        .drop_duplicates())
            if not fms_keys.empty:
                st.caption("一次 FMS 筛查会写入 15 行，可按下述组合整次删除：")
                s1, s2 = st.columns([2, 1])
                with s1:
                    keys = list(fms_keys.itertuples(index=False, name=None))
                    chosen = st.selectbox("选择一次 FMS 筛查（运动员 + 日期）",
                                          options=range(len(keys)),
                                          format_func=lambda i: f"{names.get(keys[i][0], keys[i][0])} · {keys[i][1]}")
                with s2:
                    ok2 = st.checkbox("确认整次删除")
                    if st.button("删除整次 FMS 筛查", disabled=not ok2, width="stretch"):
                        sel_aid, sel_day = keys[chosen]
                        all_tests = load_tests()
                        ids = all_tests[(all_tests["运动员ID"] == sel_aid)
                                        & (all_tests["测试类别"] == "FMS")
                                        & (pd.to_datetime(all_tests["测试日期"]).dt.strftime("%Y-%m-%d") == sel_day)
                                        ]["记录ID"].tolist()
                        if ids:
                            delete_rows("test", ids)
                        st.success(f"已删除 {len(ids)} 行")
                        st.rerun()

    with st.expander("评分口径说明"):
        st.markdown(
            "- **continuous**：按「性别|年龄组」的达标线（0 分）/ 优秀线（100 分）线性换算 0～100 分，"
            "四档等级：优秀 >=90、良好 >=75、达标 >=60、待提升 <60；未配置组合显示「—」。\n"
            "- **ordinal（FMS）**：保留 0～3 原始分，双侧取较低侧，7 动作求和 0～21，<=14 为较高风险。\n"
            "- **binary**：排除性测试「通过 / 阳性」，阳性使对应动作记 0 分。\n"
            "- **descriptive**：身高 / 体重 / BMI 等仅记录不评分。\n"
            "- 得分、等级、FMS总分、不对称标记均为派生量，不落盘；改一处阈值后历史得分自动全部重算。")

# ------------------------------------------------------------------ 个人成绩单
with tab_report:
    r1, r2 = st.columns([1, 1])
    with r1:
        aid_r = st.selectbox("选择运动员", options=list(names.keys()),
                             format_func=lambda x: f"{x} · {names[x]}", key="report_aid")
    report = build_report(aid_r)
    with r2:
        csv_download(report, f"个人成绩单_{names[aid_r]}.csv", "下载个人成绩单 CSV")
    st.caption(f"成绩单先做 CSV 导出：含全部体能 / 描述 / FMS 明细与派生量；"
               f"所属运动项目：**{sports.get(str(aid_r), SPORT_UNKNOWN)}**，"
               "每条记录均按写入时快照关联项目。")
    if report.empty:
        st.info("这位运动员还没有测试与评估记录。")
    else:
        rs = pd.to_numeric(report["得分"], errors="coerce").dropna()
        fms_totals = pd.to_numeric(report["FMS总分"], errors="coerce").dropna()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("记录条数", f"{len(report)} 条")
        k2.metric("平均体能得分", f"{rs.mean():.1f} 分" if len(rs) else "—")
        k3.metric("达标率", f"{(rs >= 60).sum() / len(rs) * 100:.0f}%" if len(rs) else "—")
        k4.metric("最近 FMS 总分", f"{int(fms_totals.iloc[-1])} / 21" if len(fms_totals) else "—")
        if (report["疼痛标记"] == "是").any():
            st.error(FMS_PAIN_WARNING)
            st.caption("含疼痛的筛查总分已加注：" + FMS_PAIN_NOTE)
        st.dataframe(report, width="stretch", hide_index=True, height=420)
