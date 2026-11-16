"""分析报告生成引擎（纯数据逻辑，不依赖 Streamlit 渲染）。

基于已记录的测试与评估数据，按「运动项目」生成针对性报告：
    * 不足项  —— 由得分、达标率、关键能力域、趋势与 FMS 结果判定；
    * 原因分析 —— 每条结论都附带可核对的数据依据，不做无依据推断；
    * 改善建议 —— 按不足项所属能力域与运动项目侧重给出，按优先级排序。

支持两种模式：队伍整体报告（可按运动项目分别生成）与个体运动员单独报告。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from config import APP_VERSION, STANDARDS_VERSION
from utils import (
    FMS_RISK_LINE, SPORT_UNKNOWN, STANDARDS_DISCLAIMER, fms_sessions,
    physical_scores, sport_focus,
)

PASS_LINE = 60.0
TEAM_MODE = "队伍整体报告"
ATHLETE_MODE = "个体运动员报告"
MODES = [TEAM_MODE, ATHLETE_MODE]
ALL_SPORTS = "全部运动项目"

# 按能力域给出的改善建议（通用示例，需教练结合专项与训练周期确认）。
ADVICE_BY_CATEGORY = {
    "速度": [
        "每周安排 2 次 30～60m 加速跑与起跑技术练习，重点改进加速段身体姿态与步频；"
        "单次总量控制在 300～500m，组间充分恢复，避免以疲劳状态下的成绩评价效果。",
        "以 10米起跑 / 30米冲刺 做 2～4 周复测，确认得分曲线回到 60 分达标线以上再增加强度。",
    ],
    "爆发力": [
        "加入跳跃类与药球类快速伸缩复合练习（跳深、立定多级跳、药球前抛），"
        "先保证落地稳定与动作质量，再逐步增加组数与负荷。",
        "结合 FMS 结果排查踝 / 髋活动度限制；动作质量未改善前不单纯追加力量负荷。",
    ],
    "力量": [
        "以深蹲 / 硬拉等基础力量动作为主建立 4～6 周渐进负荷，配合离心控制与核心稳定练习；"
        "记录每次训练负荷以便与体测得分变化对照。",
        "对左右侧力量差异明显者增加单侧练习，并在下一轮体测中复测握力与 1RM 类项目。",
    ],
    "耐力": [
        "先补足有氧基础量（低强度持续跑 / 专项匀速段），再引入 1～2 次间歇或阈值强度课；"
        "每周总量增幅不超过 10%，并同步监控静息心率与主观疲劳。",
        "用 12分钟跑或 Yo-Yo 间歇跑做 4～6 周复测，比较同标准组下的得分变化。",
    ],
    "灵敏协调": [
        "安排变向减速—再加速、脚步频率与专项移动模式练习，强调制动姿态与重心控制；"
        "每次训练前完成动态热身。",
        "用 T型灵敏测试 / 5-10-5折返跑 复测，并观察与 FMS 旋转稳定性、跨栏步得分是否同步改善。",
    ],
    "柔韧": [
        "针对髋、踝、胸椎安排每日 10～15 分钟活动度练习（动态拉伸 + 末端静态保持），"
        "训练后配合放松，避免只做被动拉伸。",
        "用坐位体前屈 / 直腿抬高角度复测，并与 FMS 主动直腿抬高得分交叉验证。",
    ],
    "身体成分": [
        "结合训练周期评估体成分与恢复状态，保证睡眠与能量摄入，避免以短期快速降重为目标；"
        "该项不替代医学评估。",
        "固定测量时间、设备与条件（晨起、空腹、同一仪器），减少测量误差对得分的干扰。",
    ],
    "身体形态": [
        "保持固定测量条件定期记录身高 / 体重 / BMI，仅作趋势参考，不单独用于评价运动能力。",
    ],
    "自定义": [
        "先确认该自定义项目的评分常模与测量流程是否稳定，再依据复测趋势调整训练安排。",
    ],
}
DEFAULT_ADVICE = ["补齐该项目的连续测试记录后再判断趋势；建议先确认常模与测量条件一致。"]
PAIN_ADVICE = ("优先停止诱发疼痛的测试动作，并按系统红色警示转介医疗或康复评估；"
               "疼痛未解除前不以分数提升为目标，也不安排针对性负荷。")
FMS_ADVICE = ("针对 0～1 分动作安排活动度 / 稳定性纠正练习，4～6 周后复测完整 FMS；"
              "对左右不对称动作先做单侧补强，再进入双侧整合训练。")


def _clean(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _fmt(value, digits=1) -> str:
    try:
        if value is None or pd.isna(value):
            return "—"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def filter_tests(tests: pd.DataFrame, *, sport: str | None = None,
                 athlete_id: str | None = None, start=None, end=None,
                 include_fms: bool = True) -> pd.DataFrame:
    """按运动项目 / 运动员 / 日期筛选测试数据（运动项目取写入时快照）。"""
    if tests.empty:
        return tests.copy()
    out = tests.copy()
    if sport and sport != ALL_SPORTS:
        out = out[out["运动项目"].astype(str) == sport]
    if athlete_id:
        out = out[out["运动员ID"].astype(str) == str(athlete_id)]
    if start is not None:
        out = out[out["测试日期"] >= pd.Timestamp(start)]
    if end is not None:
        out = out[out["测试日期"] <= pd.Timestamp(end)]
    if not include_fms:
        out = out[out["测试类别"] != "FMS"]
    return out.copy()


def _stats(phys: pd.DataFrame, cat_map: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按测试项目与能力域聚合（只使用 continuous 且已评分的记录）。"""
    empty_item = pd.DataFrame(columns=["测试项目", "类别", "记录数", "平均得分", "达标率"])
    empty_cat = pd.DataFrame(columns=["类别", "记录数", "平均得分"])
    if phys.empty:
        return empty_item, empty_cat
    work = phys.copy()
    work["类别"] = work["测试项目"].map(cat_map).fillna("自定义")
    item = (work.groupby("测试项目", as_index=False)
            .agg(类别=("类别", "first"), 记录数=("得分", "size"),
                 平均得分=("得分", "mean"), 达标率=("得分", lambda s: (s >= PASS_LINE).mean())))
    cat = (work.groupby("类别", as_index=False)
           .agg(记录数=("得分", "size"), 平均得分=("得分", "mean"))
           .sort_values("平均得分"))
    return item, cat


def _gaps(item: pd.DataFrame, focus: list[str]) -> pd.DataFrame:
    """识别不足项：均分未达标或达标率偏低；关键能力域命中会提高排序权重。"""
    cols = ["测试项目", "类别", "平均得分", "达标率", "记录数", "严重度", "关键能力域", "依据"]
    if item.empty:
        return pd.DataFrame(columns=cols)
    hit = item[(item["平均得分"] < PASS_LINE) | (item["达标率"] < 0.6)].copy()
    if hit.empty:
        return pd.DataFrame(columns=cols)

    def severity(r):
        if r["平均得分"] < 45 or r["达标率"] < 0.3:
            return "高"
        if r["平均得分"] < PASS_LINE:
            return "中"
        return "观察"

    hit["关键能力域"] = hit["类别"].isin(focus)
    hit["严重度"] = hit.apply(severity, axis=1)
    hit["依据"] = hit.apply(
        lambda r: (f"平均 {r['平均得分']:.1f} 分（达标线 60 分）｜"
                   f"达标率 {r['达标率'] * 100:.0f}%｜{int(r['记录数'])} 条评分记录"), axis=1)
    order = {"高": 0, "中": 1, "观察": 2}
    hit["_s"] = hit["严重度"].map(order)
    hit["_f"] = (~hit["关键能力域"]).astype(int)
    hit = hit.sort_values(["_s", "_f", "平均得分"]).drop(columns=["_s", "_f"])
    return hit[cols].reset_index(drop=True)


def _trend_findings(phys: pd.DataFrame) -> list[str]:
    """最近两次测试之间的得分变化（整体 + 逐项目），只报告明显下降。"""
    out = []
    if phys.empty:
        return out
    work = phys.dropna(subset=["测试日期"]).copy()
    if work.empty:
        return out
    work["日期"] = pd.to_datetime(work["测试日期"], errors="coerce").dt.date
    overall = work.groupby("日期")["得分"].mean().sort_index()
    if len(overall) >= 2:
        delta = float(overall.iloc[-1] - overall.iloc[-2])
        if delta <= -5:
            out.append(f"最近一次测试的整体平均得分 {overall.iloc[-1]:.1f} 分，"
                       f"较上一次 {overall.iloc[-2]:.1f} 分下降 {abs(delta):.1f} 分，"
                       "提示近期训练安排、负荷或恢复状态需要排查。")
    drops = []
    for item, g in work.groupby("测试项目"):
        series = g.groupby("日期")["得分"].mean().sort_index()
        if len(series) >= 2:
            delta = float(series.iloc[-1] - series.iloc[-2])
            if delta <= -8:
                drops.append((delta, f"「{item}」下降 {abs(delta):.1f} 分"))
    if drops:
        drops.sort()
        out.append("单项出现明显回落：" + "、".join(text for _, text in drops[:4]) +
                   "；建议核对测量条件是否一致，再结合训练日志判断。")
    return out


def _fms_findings(fms: pd.DataFrame) -> list[str]:
    out = []
    if fms.empty:
        return out
    risk = int((fms["风险"] == "较高风险").sum())
    pain = int((fms["含疼痛"] == "是").sum())
    asym = int((fms["不对称清单"].astype(str).str.strip() != "").sum())
    mean_total = fms["FMS总分"].mean()
    if risk:
        out.append(f"{risk} 次 FMS 筛查总分 <= {FMS_RISK_LINE} 分（较高风险），"
                   f"平均总分 {mean_total:.1f} / 21；提示基础动作模式的质量或控制能力不足。")
    if pain:
        out.append(f"{pain} 次筛查含 0 分 / 排除测试阳性等疼痛相关结果，"
                   "这类总分已加注「含疼痛项，结果需谨慎解读」，不宜直接用于能力比较。")
    if asym:
        out.append(f"{asym} 次筛查存在左右差 >= 1 的动作不对称，"
                   "提示单侧力量或活动度差异，也可能是导致相关体能项目得分偏低的原因之一。")
    return out


def _advice(gaps: pd.DataFrame, fms: pd.DataFrame, focus: list[str]) -> list[dict]:
    """给出针对具体不足项的下一步改善建议，按优先级排序。"""
    out = []
    if not fms.empty and (fms["含疼痛"] == "是").any():
        out.append({"优先级": "P0", "针对": "疼痛 / 排除测试阳性", "建议": PAIN_ADVICE})
    if not fms.empty and (fms["风险"] == "较高风险").any():
        out.append({"优先级": "P1", "针对": "FMS 高风险与左右不对称", "建议": FMS_ADVICE})

    seen_cat = set()
    for _, r in gaps.iterrows():
        cat = r["类别"]
        priority = "P1" if r["严重度"] == "高" or r["关键能力域"] else "P2"
        if cat in seen_cat:
            # 同类能力域只给一次成套建议，但把项目名补充进「针对」。
            for entry in out:
                if entry.get("_cat") == cat:
                    entry["针对"] += f"、{r['测试项目']}"
            continue
        seen_cat.add(cat)
        tips = ADVICE_BY_CATEGORY.get(cat, DEFAULT_ADVICE)
        text = " ".join(tips)
        entry = {"优先级": priority, "针对": f"{r['测试项目']}（{cat}能力域，{r['平均得分']:.1f} 分）",
                 "建议": text, "_cat": cat}
        out.append(entry)

    if not gaps.empty:
        focus_hit = sorted({r["类别"] for _, r in gaps.iterrows() if r["关键能力域"]})
        if focus_hit:
            out.append({"优先级": "P2", "针对": "训练周期安排",
                        "建议": f"下一周期优先安排「{'、'.join(focus_hit)}」相关训练课，"
                                "并把本次不足项列为 4～6 周后的复测指标；"
                                "每轮复测保持同一标准组配置与测量条件，便于对比。"})
    if not out:
        out.append({"优先级": "P2", "针对": "保持与监测",
                    "建议": "本次未发现明显不足项，建议维持当前安排并定期复测；"
                            "同时补充更多测试记录以提高结论可靠性。"})
    return [{k: v for k, v in row.items() if k != "_cat"} for row in out]


def generate_report(all_tests: pd.DataFrame, athletes: pd.DataFrame,
                    catalog: pd.DataFrame, *, mode: str, sport: str = ALL_SPORTS,
                    athlete_id: str | None = None, start=None, end=None,
                    include_fms: bool = True) -> dict:
    """生成一份分析报告；返回结构化 dict，可渲染到界面或导出 Markdown。"""
    cat_map = (dict(zip(catalog["测试项目"], catalog["类别"]))
               if not catalog.empty else {})
    names = (dict(zip(athletes["运动员ID"].astype(str), athletes["姓名"].astype(str)))
             if not athletes.empty else {})
    athlete_sport = (dict(zip(athletes["运动员ID"].astype(str),
                              athletes["运动项目"].astype(str)))
                     if not athletes.empty and "运动项目" in athletes.columns else {})

    if mode == ATHLETE_MODE and athlete_id:
        # 显式指定的运动项目优先；否则默认按该运动员档案中的运动项目生成。
        explicit = sport if sport and sport != ALL_SPORTS else ""
        scope_sport = explicit or athlete_sport.get(str(athlete_id)) or SPORT_UNKNOWN
        scope = f"{names.get(str(athlete_id), athlete_id)}（{scope_sport}）"
    else:
        scope_sport = sport
        scope = "全部运动项目" if sport == ALL_SPORTS else sport

    tests = filter_tests(all_tests, sport=(scope_sport if scope_sport != ALL_SPORTS else None),
                         athlete_id=(str(athlete_id) if mode == ATHLETE_MODE else None),
                         start=start, end=end, include_fms=include_fms)
    phys = physical_scores(tests)
    focus = sport_focus(scope_sport)
    item, cat_stats = _stats(phys, cat_map)
    gaps = _gaps(item, focus)
    fms = fms_sessions(tests) if include_fms else pd.DataFrame()
    reasons, caveats = [], []

    # 原因分析 ①：能力域整体状况
    overall = phys["得分"].mean() if not phys.empty else None
    if not cat_stats.empty and overall is not None:
        worst = cat_stats.iloc[0]
        reasons.append(f"能力域「{worst['类别']}」平均 {worst['平均得分']:.1f} 分，"
                       f"为本次范围内最低（整体均值 {overall:.1f} 分，"
                       f"共 {int(worst['记录数'])} 条评分记录）。")
        focus_cat = cat_stats[cat_stats["类别"].isin(focus)]
        if not focus_cat.empty:
            f_row = focus_cat.iloc[0]
            diff = overall - f_row["平均得分"]
            if diff > 0:
                reasons.append(f"「{scope_sport}」的关键能力域「{f_row['类别']}」"
                               f"平均 {f_row['平均得分']:.1f} 分，低于整体均值 {diff:.1f} 分；"
                               "该项目专项需求与当前测试结果存在落差，是本报告的首要关注点。")

    # 原因分析 ②：逐项不足
    for _, r in gaps.head(6).iterrows():
        tag = "（关键能力域）" if r["关键能力域"] else ""
        reasons.append(f"不足项「{r['测试项目']}」{tag}：{r['依据']}。")

    # 原因分析 ③：趋势
    reasons.extend(_trend_findings(phys))

    # 原因分析 ④：与基准对比
    if mode == ATHLETE_MODE and athlete_id and not phys.empty:
        peers = physical_scores(all_tests[
            (all_tests["运动项目"].astype(str) == scope_sport)
            & (all_tests["运动员ID"].astype(str) != str(athlete_id))])
        if not peers.empty:
            peer_mean = peers["得分"].mean()
            diff = float(overall) - float(peer_mean)
            word = "高于" if diff >= 0 else "低于"
            reasons.append(f"该运动员平均体能得分 {overall:.1f} 分，"
                           f"{word} 同运动项目（{scope_sport}）其他成员均值 "
                           f"{peer_mean:.1f} 分，差值 {abs(diff):.1f} 分。")
    elif mode == TEAM_MODE and sport != ALL_SPORTS and not phys.empty:
        team = physical_scores(all_tests)
        if not team.empty:
            team_mean = team["得分"].mean()
            diff = float(overall) - float(team_mean)
            word = "高于" if diff >= 0 else "低于"
            reasons.append(f"「{sport}」平均体能得分 {overall:.1f} 分，"
                           f"{word} 全队均值 {team_mean:.1f} 分，差值 {abs(diff):.1f} 分。")

    # 原因分析 ⑤：FMS
    reasons.extend(_fms_findings(fms))

    if not reasons:
        reasons.append("当前筛选范围内可用评分记录不足，尚无法形成有数据支撑的原因判断；"
                       "建议先完成一轮完整测试与评估。")

    # 数据局限
    if phys.empty:
        caveats.append("本次范围内没有可评分的 continuous 记录（可能只录入了 FMS / 描述项，"
                       "或该标准组尚未配置阈值），不足项与得分结论留空。")
    elif len(phys) < 10:
        caveats.append(f"仅 {len(phys)} 条评分记录，样本量偏少，结论仅供初步参考。")
    unscored = int(((tests["评分类型"] == "continuous")
                    & pd.to_numeric(tests["得分"], errors="coerce").isna()).sum()) if not tests.empty else 0
    if unscored:
        caveats.append(f"{unscored} 条体能记录因标准组未配置常模而未评分，未纳入得分分析；"
                       "请到「评分标准」页补齐阈值。")
    if not tests.empty and (tests["运动项目"].astype(str) == SPORT_UNKNOWN).any():
        caveats.append("存在运动项目「未填写」的记录，无法纳入按项目的针对性分析。")
    caveats.append(f"评分常模：{STANDARDS_DISCLAIMER}；报告结论由内置规则基于已记录数据生成，"
                   "不构成医疗诊断或训练处方，需教练结合专项、训练周期与运动员状态确认。")
    caveats.append(f"口径版本：系统 {APP_VERSION} ｜ 常模 {STANDARDS_VERSION}。"
                   "常模替换或阈值调整后历史得分会自动重算，跨版本对比请先核对版本标记。")

    summary = {
        "测试记录": int(len(tests)),
        "评分记录": int(len(phys)),
        "平均体能得分": round(float(overall), 1) if overall is not None and pd.notna(overall) else None,
        "达标率": (round(float((phys["得分"] >= PASS_LINE).mean()) * 100, 1)
                 if not phys.empty else None),
        "不足项数": int(len(gaps)),
        "FMS筛查": int(len(fms)),
        "较高风险": int((fms["风险"] == "较高风险").sum()) if not fms.empty else 0,
        "含疼痛": int((fms["含疼痛"] == "是").sum()) if not fms.empty else 0,
    }
    return {
        "mode": mode, "scope": scope, "sport": scope_sport, "focus": focus,
        "athlete": names.get(str(athlete_id)) if mode == ATHLETE_MODE and athlete_id else None,
        "generated": date.today().isoformat(),
        "period": f"{start or '不限'} 至 {end or '不限'}",
        "app_version": APP_VERSION, "standards_version": STANDARDS_VERSION,
        "summary": summary, "gaps": gaps, "reasons": reasons,
        "advice": _advice(gaps, fms, focus), "caveats": caveats,
        "item_stats": item, "category_stats": cat_stats, "fms": fms,
        "details": tests,
    }


def _df_to_md(df: pd.DataFrame, cols: list[str] | None = None,
              float_cols: tuple[str, ...] = ()) -> str:
    if df is None or df.empty:
        return "（无）"
    d = df[cols].copy() if cols else df.copy()
    for c in float_cols:
        if c in d.columns:
            d[c] = d[c].map(lambda v: _fmt(v))
    head = "| " + " | ".join(map(str, d.columns)) + " |"
    sep = "| " + " | ".join(["---"] * len(d.columns)) + " |"
    rows = ["| " + " | ".join(str(_clean(v) or "—").replace("|", "／").replace("\n", " ")
                              for v in row) + " |"
            for row in d.itertuples(index=False)]
    return "\n".join([head, sep] + rows)


def to_markdown(report: dict) -> str:
    """把结构化报告渲染为可导出的 Markdown 文本。"""
    s = report["summary"]
    lines = [
        f"# {report['scope']} · {report['mode']}",
        "",
        f"- 生成日期：{report['generated']}",
        f"- 统计区间：{report['period']}",
        f"- 运动项目：{report['sport'] or '全部'}",
        f"- 关键能力域：{'、'.join(report['focus'])}",
        f"- 口径版本：系统 {report.get('app_version', APP_VERSION)} ｜ "
        f"常模 {report.get('standards_version', STANDARDS_VERSION)}",
        "",
        "## 一、概览",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 测试记录 | {s['测试记录']} 条 |",
        f"| 评分记录（continuous） | {s['评分记录']} 条 |",
        f"| 平均体能得分 | {_fmt(s['平均体能得分'])} 分 |",
        f"| 达标率 | {_fmt(s['达标率'])} % |",
        f"| 不足项数 | {s['不足项数']} 项 |",
        f"| FMS 筛查 / 较高风险 / 含疼痛 | {s['FMS筛查']} / {s['较高风险']} / {s['含疼痛']} 次 |",
        "",
        "## 二、不足项",
        "",
        _df_to_md(report["gaps"],
                  ["测试项目", "类别", "平均得分", "达标率", "严重度", "关键能力域", "依据"],
                  float_cols=("平均得分",)),
        "",
        "> 判定口径：平均得分 < 60 分达标线，或达标率 < 60%；命中运动项目关键能力域的不足项优先排序。",
        "",
        "## 三、原因分析",
        "",
    ]
    lines += [f"{i}. {text}" for i, text in enumerate(report["reasons"], start=1)]
    lines += ["", "## 四、下一步改善建议", "",
              "| 优先级 | 针对 | 建议 |", "| --- | --- | --- |"]
    for a in report["advice"]:
        lines.append(f"| {a['优先级']} | {a['针对']} | {a['建议'].replace('|', '／')} |")
    lines += ["", "## 五、按能力域汇总", "",
              _df_to_md(report["category_stats"], ["类别", "记录数", "平均得分"],
                        float_cols=("平均得分",)),
              "", "## 六、数据局限与说明", ""]
    lines += [f"- {c}" for c in report["caveats"]]
    lines.append("")
    return "\n".join(lines)
