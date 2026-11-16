"""页面 3：数据分析 —— 体能分析 / FMS 分析两个 tab。"""
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from utils import (
    FMS_MOVEMENTS, FMS_PAIN_NOTE, FMS_PAIN_WARNING, FMS_RISK_LINE,
    STANDARDS_DISCLAIMER, athlete_map, csv_download, empty_state, ensure_files,
    fms_sessions, inject_css, item_catalog, load_tests, page_header,
    physical_scores, plotly_font, render_sidebar, with_names,
)

st.set_page_config(page_title="数据分析", page_icon="📊", layout="wide")
inject_css()
ensure_files()
page_header("数据分析", "体能分析看 0~100 分进步曲线，FMS 分析看 0~21 分功能风险", "📊")
render_sidebar()
st.caption(f"评分标准：{STANDARDS_DISCLAIMER}。FMS 的 0~3 原始分绝不进入平均体能得分、"
           "达标率或 0~100 坐标轴。")

tests = load_tests()
names = athlete_map()
if tests.empty:
    empty_state("还没有测试与评估数据，无法分析。")

tab_physical, tab_fms = st.tabs(["体能分析", "FMS 分析"])

# ------------------------------------------------------------------ 体能分析
with tab_physical:
    base = physical_scores(tests)
    f1, f2, f3 = st.columns([1.2, 1.2, 1])
    with f1:
        sel_a = st.multiselect("运动员（可多选）", options=list(names.keys()),
                               format_func=lambda x: names[x], default=list(names.keys()))
    with f2:
        options_i = sorted(base["测试项目"].unique().tolist()) if not base.empty else []
        sel_i = st.multiselect("测试项目（可多选）", options=options_i, default=options_i)
    with f3:
        period = st.date_input("日期范围",
                               value=(date.today() - timedelta(days=365), date.today()))
    start, end = (period if isinstance(period, (tuple, list)) and len(period) == 2
                  else (date.today() - timedelta(days=365), date.today()))

    data = base[base["运动员ID"].isin(sel_a) & base["测试项目"].isin(sel_i)
                & (base["测试日期"] >= pd.Timestamp(start))
                & (base["测试日期"] <= pd.Timestamp(end))].copy()
    data = with_names(data)

    if data.empty:
        st.info("当前筛选下没有可评分的体能记录（可能只录入了 FMS / 描述项，"
                "或该标准组尚未配置阈值）。FMS 结果请切到右侧「FMS 分析」。")
    else:
        catalog = item_catalog().set_index("测试项目")

        st.subheader("成绩进步趋势")
        t1, t2 = st.columns(2)
        with t1:
            trend_item = st.selectbox("选择要看的项目", options=sorted(data["测试项目"].unique()))
        with t2:
            trend_people = st.multiselect("对比运动员", options=sorted(data["姓名"].unique()),
                                          default=sorted(data["姓名"].unique()))
        trend = data[(data["测试项目"] == trend_item) & (data["姓名"].isin(trend_people))]
        trend = trend.groupby(["测试日期", "姓名"], as_index=False)["得分"].mean().sort_values("测试日期")
        if trend.empty:
            st.info("该项目暂无可绘制的数据。")
        else:
            unit = catalog.loc[trend_item, "单位"] if trend_item in catalog.index else ""
            fig = px.line(trend, x="测试日期", y="得分", color="姓名", markers=True,
                          range_y=[0, 100],
                          labels={"得分": "得分", "测试日期": "测试日期"},
                          title=f"{trend_item} · 得分趋势（单位 {unit}，达标 60 分）")
            fig.add_hline(y=60, line_dash="dash", line_color="#e08b2c")
            fig.add_hline(y=90, line_dash="dot", line_color="#18a58b")
            fig.update_traces(line_width=2.5, marker_size=8)
            # 数据集中在同一天时，Plotly 默认会生成带毫秒的刻度，这里固定日期格式。
            fig.update_xaxes(type="date", tickformat="%Y-%m-%d", nticks=8)
            st.plotly_chart(plotly_font(fig), width="stretch")

        left, right = st.columns([1.3, 1])
        with left:
            st.subheader("各项目最新得分对比")
            latest = data.sort_values("测试日期").groupby(["姓名", "测试项目"], as_index=False).tail(1)
            fig2 = px.bar(latest, x="测试项目", y="得分", color="姓名", barmode="group",
                          range_y=[0, 100], labels={"得分": "最新得分"})
            fig2.add_hline(y=60, line_dash="dash", line_color="#e08b2c")
            st.plotly_chart(plotly_font(fig2), width="stretch")

        with right:
            st.subheader("能力雷达图（0~100）")
            radar_person = st.selectbox("选择运动员", options=sorted(data["姓名"].unique()))
            items = sorted(data["测试项目"].unique())
            one = data[data["姓名"] == radar_person].groupby("测试项目")["得分"].mean()
            all_avg = data.groupby("测试项目")["得分"].mean()
            radar = pd.DataFrame({
                "测试项目": items,
                radar_person: [one.get(i, 0) for i in items],
                "全队平均": [all_avg.get(i, 0) for i in items],
            }).melt(id_vars="测试项目", var_name="对象", value_name="得分")
            fig3 = px.line_polar(radar, r="得分", theta="测试项目", color="对象",
                                 line_close=True, range_r=[0, 100])
            fig3.update_traces(fill="toself", opacity=0.3, line_width=2)
            st.plotly_chart(plotly_font(fig3), width="stretch")

        c1, c2 = st.columns([1, 2])
        with c1:
            st.subheader("达标等级分布")
            counts = data["等级"].value_counts().reset_index()
            counts.columns = ["等级", "记录数"]
            fig4 = px.pie(counts, values="记录数", names="等级", hole=0.45,
                          color="等级",
                          color_discrete_map={"优秀": "#18a58b", "良好": "#1e5b98",
                                              "达标": "#7fb2df", "待提升": "#e08b2c"})
            st.plotly_chart(plotly_font(fig4), width="stretch")
        with c2:
            st.subheader("筛选后的明细数据")
            detail = data.sort_values("测试日期", ascending=False).copy()
            detail["测试日期"] = pd.to_datetime(detail["测试日期"], errors="coerce").dt.strftime("%Y-%m-%d")
            cols = ["姓名", "测试日期", "测试项目", "结果显示", "单位", "标准组",
                    "得分", "等级", "备注"]
            st.dataframe(detail[cols], width="stretch", hide_index=True, height=320,
                         column_config={"得分": st.column_config.NumberColumn("得分", format="%.1f")})
            csv_download(detail[cols], "体能分析明细.csv", "下载体能分析明细 CSV")

# ------------------------------------------------------------------ FMS 分析
with tab_fms:
    sessions = fms_sessions(tests)
    if sessions.empty:
        st.info("暂无 FMS 功能筛查记录。到「测试与评估 → FMS 录入矩阵」录入。")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("受测人次", f"{len(sessions)} 次")
        m2.metric("平均 FMS 总分", f"{sessions['FMS总分'].mean():.1f} / 21")
        m3.metric("较高风险人次", f"{int((sessions['风险'] == '较高风险').sum())} 次")
        m4.metric("含疼痛人次", f"{int((sessions['含疼痛'] == '是').sum())} 次")

        pain_rows = sessions[sessions["含疼痛"] == "是"]
        if not pain_rows.empty:
            st.error(FMS_PAIN_WARNING + f"（{len(pain_rows)} 次筛查）")
            st.caption("这些筛查的总分已加注：" + FMS_PAIN_NOTE)

        view = sessions.copy()
        view["测试日期"] = pd.to_datetime(view["测试日期"], errors="coerce").dt.strftime("%Y-%m-%d")

        fl, fr = st.columns([1.35, 1])
        with fl:
            st.subheader("FMS 总分分布（0~21，风险线 14）")
            fig = px.bar(view, x="姓名", y="FMS总分", color="风险", barmode="group",
                         range_y=[0, 21],
                         color_discrete_map={"一般": "#1e5b98", "较高风险": "#d9534f"},
                         labels={"FMS总分": "FMS 总分"})
            fig.add_hline(y=FMS_RISK_LINE, line_dash="dash", line_color="#d9534f",
                          annotation_text="风险线 14 分")
            st.plotly_chart(plotly_font(fig), width="stretch")

        with fr:
            st.subheader("各动作平均原始分（0~3）")
            fms_rows = tests[tests["测试类别"] == "FMS"]
            detail = []
            for _, g in fms_rows.groupby(["运动员ID", "测试日期"], dropna=False):
                for name, _, _ in FMS_MOVEMENTS:
                    sub = g[g["测试项目"] == name]
                    vals = pd.to_numeric(sub["成绩数值"], errors="coerce").dropna()
                    if not vals.empty:
                        detail.append({"动作": name.replace("FMS", ""), "原始分": float(vals.min())})
            if detail:
                mov = (pd.DataFrame(detail).groupby("动作", as_index=False)["原始分"].mean()
                       .sort_values("原始分"))
                fig2 = px.bar(mov, x="原始分", y="动作", orientation="h", range_x=[0, 3],
                              labels={"原始分": "平均原始分（0~3）"},
                              color_discrete_sequence=["#18a58b"])
                st.plotly_chart(plotly_font(fig2), width="stretch")
            else:
                st.caption("暂无动作原始分。")

        st.subheader("左右不对称清单（左右差 >= 1）")
        asym = view[view["不对称清单"].astype(str).str.strip() != ""]
        if asym.empty:
            st.caption("暂无左右不对称记录。")
        else:
            st.dataframe(asym[["姓名", "测试日期", "FMS总分", "不对称清单", "风险"]],
                         width="stretch", hide_index=True)

        st.subheader("FMS 筛查明细")
        cols = ["姓名", "测试日期", "FMS总分", "风险", "含疼痛", "动作完整数",
                "不对称清单", "各动作得分", "疼痛警示", "结果说明"]
        st.dataframe(view[cols], width="stretch", hide_index=True, height=320)
        csv_download(view[cols], "FMS筛查明细.csv", "下载 FMS 筛查明细 CSV")
