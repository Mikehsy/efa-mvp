"""页面 5：分析报告 —— 按运动项目生成队伍整体 / 个体运动员针对性报告。

报告三要素：不足项、原因分析、下一步改善建议；全部结论由已记录的测试数据驱动。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from reports import (
    ALL_SPORTS, ATHLETE_MODE, MODES, PASS_LINE, TEAM_MODE,
    generate_report, to_markdown,
)
from logging_config import log_exception
from utils import (
    SPORT_UNKNOWN, STANDARDS_DISCLAIMER, cal_advice, cal_report_title, csv_download,
    empty_state, ensure_files, escape_html, inject_css, item_catalog, load_athletes,
    load_tests, page_header, plotly_font, render_sidebar,
)

st.set_page_config(page_title="分析报告", page_icon="📝", layout="wide")
inject_css()
ensure_files()
page_header("分析报告",
            "基于已记录的测试数据，按运动项目输出不足项、原因分析与下一步改善建议",
            "📝")
render_sidebar()
st.caption(f"评分常模：{STANDARDS_DISCLAIMER}。报告结论由内置规则基于已记录数据生成，"
           "不构成医疗诊断或训练处方，需教练结合专项、训练周期与运动员状态确认。")

tests = load_tests()
athletes = load_athletes()
if tests.empty or athletes.empty:
    empty_state("还没有足够的测试与评估数据，无法生成分析报告。")

catalog = item_catalog()
known_sports = ({str(s).strip() for s in tests["运动项目"].dropna()}
                | {str(s).strip() for s in athletes["运动项目"].dropna()})
sport_values = sorted(s for s in known_sports if s and s != SPORT_UNKNOWN)
unknown_count = int((tests["运动项目"].astype(str).str.strip() == SPORT_UNKNOWN).sum())
if unknown_count:
    st.warning(f"有 {unknown_count} 条测试记录的运动项目为「{SPORT_UNKNOWN}」，无法纳入按项目的针对性分析。"
               "请到「01 运动员管理」补全档案中的运动项目，之后新录入的记录会自动带上项目快照。")

# ------------------------------------------------------------------ ① 报告范围
cal_report_title("①", "报告范围")
c1, c2 = st.columns([1, 1.5])
with c1:
    mode = st.radio("报告模式", MODES, horizontal=True)
    if mode == TEAM_MODE:
        st.caption("汇总所选范围内全部运动员，可按运动项目分别生成针对性报告。")
    else:
        st.caption("只汇总一位运动员，并与其同项目其他成员做对比。")
with c2:
    athlete_id = None
    if mode == TEAM_MODE:
        sport = st.selectbox("运动项目", options=[ALL_SPORTS] + sport_values, index=0)
        st.caption(f"选择具体运动项目会按该项目的关键能力域生成针对性报告；"
                   f"「{ALL_SPORTS}」为队伍总览，使用通用能力域。")
    else:
        people = {str(r["运动员ID"]): (str(r["姓名"]),
                                       str(r["运动项目"]).strip() or SPORT_UNKNOWN)
                  for _, r in athletes.iterrows()}
        ids = list(people)
        athlete_id = st.selectbox(
            "运动员", options=ids,
            format_func=lambda x: f"{people[x][0]} · {people[x][1]}")
        own = people[athlete_id][1] if athlete_id else SPORT_UNKNOWN
        own_options = ([own] if own and own != SPORT_UNKNOWN else []) + \
                      [s for s in sport_values if s != own]
        if not own_options:
            own_options = [SPORT_UNKNOWN]
        sport = st.selectbox("按运动项目生成", options=own_options, index=0)
        st.caption("默认按该运动员档案中的运动项目生成；也可以切换到其他项目，"
                   "查看其测试记录中对应项目快照的分析。")

c3, c4 = st.columns([1.5, 1])
valid_dates = tests["测试日期"].dropna()
dmin = valid_dates.min().date() if not valid_dates.empty else date.today() - timedelta(days=365)
dmax = valid_dates.max().date() if not valid_dates.empty else date.today()
with c3:
    period = st.date_input("统计区间", value=(dmin, dmax))
    start, end = (period if isinstance(period, (tuple, list)) and len(period) == 2
                  else (dmin, dmax))
with c4:
    include_fms = st.checkbox("纳入 FMS 筛查结果", value=True)
    st.caption("FMS 的 0～3 原始分不进入 0～100 得分体系，仅用于风险、疼痛与左右不对称的原因分析。")

if start and end and start > end:
    st.error("开始日期不能晚于结束日期，请重新选择统计区间。")
    st.stop()

st.caption("调整上方任意条件后，下方报告会自动重新生成。")
try:
    report = generate_report(tests, athletes, catalog, mode=mode, sport=sport,
                             athlete_id=athlete_id, start=start, end=end,
                             include_fms=include_fms)
except Exception as exc:
    log_exception("生成分析报告", page="05 分析报告", mode=mode,
                  sport=sport, error=str(exc))
    st.error("报告生成失败：当前筛选条件下的数据无法完成分析。请放宽统计区间、"
             "改选运动项目，或到「02 测试与评估」检查记录；详细原因已写入日志。")
    st.stop()

# ------------------------------------------------------------------ ② 概览
st.markdown("---")
cal_report_title("②", "概览")
st.caption(f"报告对象：**{escape_html(report['scope'])}**　｜　模式：{mode}　｜　"
           f"统计区间：{report['period']}　｜　生成日期：{report['generated']}")
st.caption(f"口径版本：系统 {report.get('app_version', '—')} ｜ "
           f"常模 {report.get('standards_version', '—')}")
st.markdown("**关键能力域**：" + "　".join(f"`{c}`" for c in report["focus"])
            + ("（按运动项目专项需求确定）" if sport != ALL_SPORTS
               else "（未指定项目，使用通用能力域）"))

s = report["summary"]


def _num(value, digits=1, suffix=""):
    return "—" if value is None or pd.isna(value) else f"{float(value):.{digits}f}{suffix}"


m = st.columns(5)
m[0].metric("测试记录", f"{s['测试记录']} 条")
m[1].metric("评分记录", f"{s['评分记录']} 条")
m[2].metric("平均体能得分", _num(s["平均体能得分"], 1, " 分"))
m[3].metric("达标率", _num(s["达标率"], 1, " %"))
m[4].metric("不足项", f"{s['不足项数']} 项")
st.caption(f"FMS 筛查 {s['FMS筛查']} 次　｜　较高风险 {s['较高风险']} 次　｜　"
           f"含疼痛 {s['含疼痛']} 次" + ("（本次未纳入 FMS）" if not include_fms else ""))
if report["details"].empty:
    st.info("当前筛选条件下没有测试记录，请调整运动项目、运动员或统计区间；"
            "下方结论会显示为数据不足提示。")

# ------------------------------------------------------------------ ③ 不足项
cal_report_title("③", "不足项")
gaps = report["gaps"]
if gaps.empty:
    st.success("本次范围内未发现明显不足项（所有已评分项目的平均得分与达标率均在口径之上）。")
else:
    shown = gaps.copy()
    shown["平均得分"] = shown["平均得分"].map(lambda v: f"{v:.1f}")
    shown["达标率"] = shown["达标率"].map(lambda v: f"{v * 100:.0f}%")
    shown["关键能力域"] = shown["关键能力域"].map(lambda v: "是" if v else "—")
    st.dataframe(shown[["测试项目", "类别", "平均得分", "达标率", "记录数",
                        "严重度", "关键能力域", "依据"]],
                 hide_index=True, width="stretch")
    st.caption(f"判定口径：平均得分 < {PASS_LINE:.0f} 分达标线，或达标率 < 60%；"
               "命中运动项目关键能力域、严重度高的不足项优先排序。")

# ------------------------------------------------------------------ ④ 原因分析
cal_report_title("④", "原因分析")
for i, text in enumerate(report["reasons"], start=1):
    st.markdown(f"**{i}.** {text}")

# ------------------------------------------------------------------ ⑤ 改善建议
cal_report_title("⑤", "下一步改善建议")
for item in report["advice"]:
    cal_advice(item["优先级"], item["针对"], item["建议"])

# ------------------------------------------------------------------ 汇总与明细
cal_report_title("⑥", "支撑数据")
with st.expander("按能力域汇总（图表与表格）", expanded=False):
    cat = report["category_stats"]
    if cat.empty:
        st.info("本次范围内没有可评分的 continuous 记录，无法按能力域汇总。")
    else:
        fig = px.bar(cat, x="类别", y="平均得分", range_y=[0, 100],
                     labels={"平均得分": "平均得分", "类别": "能力域"},
                     title="各能力域平均得分（0～100，虚线 PASS 60）")
        fig.add_hline(y=PASS_LINE, line_dash="dash", line_color="#E08B2C",
                      annotation_text="PASS 60")
        st.plotly_chart(plotly_font(fig), width="stretch")
        cat_shown = cat.copy()
        cat_shown["平均得分"] = cat_shown["平均得分"].round(1)
        st.dataframe(cat_shown, hide_index=True, width="stretch")

if include_fms and not report["fms"].empty:
    fms_shown = report["fms"][["姓名", "测试日期", "FMS总分", "风险", "含疼痛",
                               "不对称清单", "结果说明"]].copy()
    fms_shown["测试日期"] = pd.to_datetime(fms_shown["测试日期"], errors="coerce") \
        .dt.strftime("%Y-%m-%d").fillna("")
    with st.expander(f"FMS 筛查明细（{len(fms_shown)} 次）", expanded=False):
        st.dataframe(fms_shown, hide_index=True, width="stretch")

cal_report_title("⑦", "数据局限与说明")
for caveat in report["caveats"]:
    st.caption("• " + caveat)

# ------------------------------------------------------------------ ⑧ 导出
cal_report_title("⑧", "导出报告")
safe_scope = re.sub(r'[\\/:*?"<>|\s（）·]+', "_", report["scope"])
stamp = report["generated"].replace("-", "")

gaps_csv = report["gaps"].copy()
if not gaps_csv.empty:
    gaps_csv["平均得分"] = gaps_csv["平均得分"].round(1)
    gaps_csv["达标率"] = (gaps_csv["达标率"] * 100).round(1)
advice_csv = pd.DataFrame(report["advice"])
cat_csv = report["category_stats"].copy()
if not cat_csv.empty:
    cat_csv["平均得分"] = cat_csv["平均得分"].round(1)
detail_cols = ["运动员ID", "测试日期", "运动项目", "测试类别", "测试项目", "侧别",
               "成绩", "单位", "标准组", "得分", "等级", "结果显示",
               "FMS总分", "疼痛标记", "备注"]
detail_csv = report["details"].reindex(columns=detail_cols).copy()
if not detail_csv.empty:
    detail_csv["测试日期"] = pd.to_datetime(detail_csv["测试日期"], errors="coerce") \
        .dt.strftime("%Y-%m-%d").fillna("")

e1, e2, e3 = st.columns(3)
with e1:
    st.download_button("下载 Markdown 报告", to_markdown(report).encode("utf-8"),
                       file_name=f"分析报告_{safe_scope}_{stamp}.md",
                       mime="text/markdown", width="stretch")
with e2:
    csv_download(gaps_csv, f"不足项_{safe_scope}_{stamp}.csv", "下载不足项 CSV")
with e3:
    csv_download(advice_csv, f"改善建议_{safe_scope}_{stamp}.csv", "下载改善建议 CSV")
e4, e5 = st.columns(2)
with e4:
    csv_download(cat_csv, f"能力域汇总_{safe_scope}_{stamp}.csv", "下载能力域汇总 CSV")
with e5:
    csv_download(detail_csv, f"报告明细_{safe_scope}_{stamp}.csv", "下载明细数据 CSV")

st.caption("Markdown 报告含完整的六章内容（概览 / 不足项 / 原因分析 / 改善建议 / "
           "按能力域汇总 / 数据局限）；CSV 便于在 Excel 中做进一步整理。")
