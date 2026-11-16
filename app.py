"""运动员体能与功能评估系统 —— 首页 / 测试与评估总览。

运行方式：在项目文件夹中执行  streamlit run app.py
"""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from utils import (
    FMS_PAIN_WARNING, FMS_RISK_LINE, STANDARDS_DISCLAIMER, athlete_map,
    build_report, clear_all_data, csv_download, empty_state, ensure_files,
    fms_sessions, inject_css, load_athletes, load_tests, page_header,
    physical_scores, plotly_font, render_sidebar, with_names,
)

st.set_page_config(page_title="总览 | 体能与功能评估系统", page_icon="🏅", layout="wide")
inject_css()
ensure_files()

page_header("测试与评估总览", "一屏掌握运动员、体能得分、达标情况与 FMS 功能筛查风险", "🏠")
render_sidebar()

athletes = load_athletes()
tests = load_tests()

if athletes.empty:
    empty_state("系统里还没有运动员资料。")

st.caption(f"评分标准：{STANDARDS_DISCLAIMER}。达标率与平均体能得分只统计 continuous 评分项；"
           "FMS 的 0~3 原始分不参与 0~100 分体系。")

# ------------------------------------------------------------------ 4 个 KPI
physical = physical_scores(tests)
scores = (pd.to_numeric(physical["得分"], errors="coerce").dropna()
          if not physical.empty else pd.Series(dtype=float))
this_month = pd.Timestamp(date.today().replace(day=1))
month_tests = int((tests["测试日期"] >= this_month).sum()) if not tests.empty else 0
pass_rate = f"{(scores >= 60).sum() / len(scores) * 100:.0f}%" if len(scores) else "—"
needs = int((scores < 60).sum()) if len(scores) else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("在册运动员", f"{len(athletes)} 人")
c2.metric("测试与评估记录", f"{len(tests)} 条", delta=f"本月 +{month_tests}")
c3.metric("平均体能得分", f"{scores.mean():.1f} 分" if len(scores) else "—")
c4.metric("达标率（仅体能项）", pass_rate,
          delta=f"待提升 {needs} 人次" if len(scores) else None)

st.markdown("")

# ------------------------------------------------------------------ 体能区
st.subheader("体能区")
if physical.empty:
    st.info("暂无可评分的体能记录（可能缺少标准配置，或只有 FMS / 描述项）。"
            "到「测试与评估」录入，或在「评分标准」页配置阈值。")
else:
    left, right = st.columns([1.25, 1])
    with left:
        st.markdown("**全队体能得分趋势**")
        trend = (physical.dropna(subset=["测试日期"])
                 .groupby("测试日期", as_index=False)["得分"].mean()
                 .sort_values("测试日期"))
        trend["测试日期"] = pd.to_datetime(trend["测试日期"]).dt.strftime("%Y-%m-%d")
        fig = px.line(trend, x="测试日期", y="得分", markers=True, range_y=[0, 100],
                      labels={"得分": "平均得分"}, color_discrete_sequence=["#1e5b98"])
        fig.add_hline(y=60, line_dash="dash", line_color="#e08b2c", annotation_text="达标线 60 分")
        fig.add_hline(y=90, line_dash="dot", line_color="#18a58b", annotation_text="优秀线 90 分")
        fig.update_traces(line_width=3, marker_size=9)
        st.plotly_chart(plotly_font(fig), width="stretch")

    with right:
        st.markdown("**各项目平均得分**")
        by_item = physical.groupby("测试项目", as_index=False)["得分"].mean().sort_values("得分")
        fig2 = px.bar(by_item, x="得分", y="测试项目", orientation="h", range_x=[0, 100],
                      color="得分", color_continuous_scale=["#e08b2c", "#1e5b98", "#18a58b"],
                      labels={"得分": "平均得分"})
        fig2.add_vline(x=60, line_dash="dash", line_color="#888")
        st.plotly_chart(plotly_font(fig2), width="stretch")

# ------------------------------------------------------------------ FMS 概览区
st.subheader("FMS 概览区（0~21 分，风险线 14 分）")
sessions = fms_sessions(tests)
if sessions.empty:
    st.info("暂无 FMS 功能筛查记录。到「测试与评估 → FMS 录入矩阵」录入一次完整筛查。")
else:
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("FMS 受测人次", f"{len(sessions)} 次")
    f2.metric("平均 FMS 总分", f"{sessions['FMS总分'].mean():.1f} / 21")
    f3.metric("较高风险人次", f"{int((sessions['风险'] == '较高风险').sum())} 次")
    f4.metric("含疼痛人次", f"{int((sessions['含疼痛'] == '是').sum())} 次")

    pain_sessions = sessions[sessions["含疼痛"] == "是"]
    if not pain_sessions.empty:
        st.error(f"{FMS_PAIN_WARNING}（共 {len(pain_sessions)} 次筛查；"
                 "这些总分已加注「含疼痛项，结果需谨慎解读」）")
    asym = sessions[sessions["不对称清单"].astype(str).str.strip() != ""]
    if not asym.empty:
        st.markdown("**左右不对称清单（左右差 >= 1）**")
        st.dataframe(asym[["姓名", "测试日期", "FMS总分", "不对称清单"]].assign(
            测试日期=lambda d: pd.to_datetime(d["测试日期"]).dt.strftime("%Y-%m-%d")),
            width="stretch", hide_index=True)
    else:
        st.caption("暂无左右不对称记录。")

    fl, fr = st.columns([1.4, 1])
    with fl:
        chart = sessions.sort_values("测试日期").copy()
        chart["测试日期"] = pd.to_datetime(chart["测试日期"]).dt.strftime("%Y-%m-%d")
        fig3 = px.bar(chart, x="测试日期", y="FMS总分", color="姓名", barmode="group",
                      range_y=[0, 21], labels={"FMS总分": "FMS 总分（0~21）"})
        fig3.add_hline(y=FMS_RISK_LINE, line_dash="dash", line_color="#d9534f",
                       annotation_text="风险线 14 分")
        st.plotly_chart(plotly_font(fig3), width="stretch")
    with fr:
        st.markdown("**筛查明细（最近 10 次）**")
        show = sessions.sort_values("测试日期", ascending=False).head(10).copy()
        show["测试日期"] = pd.to_datetime(show["测试日期"]).dt.strftime("%Y-%m-%d")
        st.dataframe(show[["姓名", "测试日期", "FMS总分", "风险", "含疼痛", "结果说明"]],
                     width="stretch", hide_index=True)

# ------------------------------------------------------------------ 最近记录
st.subheader("最近 10 条测试与评估记录")
if tests.empty:
    st.caption("暂无记录。")
else:
    recent = with_names(tests).sort_values("测试日期", ascending=False).head(10).copy()
    recent["测试日期"] = pd.to_datetime(recent["测试日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    show_cols = ["姓名", "测试日期", "测试类别", "测试项目", "侧别", "结果显示",
                 "单位", "标准组", "得分", "等级", "疼痛标记"]
    st.dataframe(recent[show_cols], width="stretch", hide_index=True,
                 column_config={"得分": st.column_config.NumberColumn("得分", format="%.1f")})
    csv_download(recent[show_cols], "最近测试与评估记录.csv", "下载最近记录 CSV")

# ------------------------------------------------------------------ 个人成绩单
st.subheader("个人成绩单（CSV 导出）")
names = athlete_map()
if not names:
    st.caption("暂无运动员。")
else:
    r1, r2 = st.columns([1, 1])
    with r1:
        aid = st.selectbox("选择运动员", options=list(names.keys()),
                           format_func=lambda x: f"{x} · {names[x]}", key="home_report_aid")
    report = build_report(aid)
    with r2:
        st.caption("成绩单含全部体能 / 描述 / FMS 明细及派生得分、等级、FMS 总分、疼痛注记。")
        csv_download(report, f"个人成绩单_{names[aid]}.csv", "下载个人成绩单 CSV")
    if report.empty:
        st.caption("这位运动员还没有测试与评估记录。")
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
        with st.expander("预览成绩单"):
            st.dataframe(report, width="stretch", hide_index=True)

# ------------------------------------------------------------------ 数据管理
with st.expander("数据管理（演示数据 / 清空 / 备份）"):
    st.write("所有内容都保存在项目 `data` 文件夹的 3 个 CSV 文件里："
             "`athletes.csv`（运动员）、`tests.csv`（测试与评估）、`standards.csv`（评分标准）。"
             "复制这个文件夹就等于完整备份。")
    b1, b2 = st.columns(2)
    with b1:
        st.caption("载入演示数据会重做运动员与测试与评估记录（保留原 192 条旧记录结构，"
                   "追加新项目、描述项与 FMS 筛查）；评分标准配置不会被覆盖。")
        if st.button("重新载入演示数据", width="stretch"):
            from utils import seed_demo_data
            seed_demo_data()
            st.success("已载入演示数据")
            st.rerun()
    with b2:
        st.caption("清空后不可恢复，请先备份 data 文件夹（评分标准配置会保留）。")
        confirm = st.checkbox("我确认要清空全部数据")
        if st.button("清空全部数据", disabled=not confirm, width="stretch"):
            clear_all_data()
            st.warning("数据已清空")
            st.rerun()
