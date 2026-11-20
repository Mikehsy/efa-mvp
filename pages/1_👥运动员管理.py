"""页面 1：运动员档案的增删改查（按当前日期实时展示适用标准组）。"""
from datetime import date, datetime

import pandas as pd
import streamlit as st

from utils import (
    AGE_GROUP_LABEL, SPORT_UNKNOWN, append_row, athlete_map, calc_age, csv_download,
    delete_rows, ensure_files, inject_css, load_athletes, load_tests, page_header,
    render_sidebar, sport_choices, sport_focus, standard_group, update_row,
)

st.set_page_config(page_title="运动员管理", page_icon="👥", layout="wide")
inject_css()
ensure_files()
page_header("运动员管理", "建立并维护运动员档案，测试与评估记录通过编号关联", "👥")
render_sidebar()

tab_add, tab_list, tab_delete = st.tabs(["新增运动员", "名单与编辑", "删除运动员"])

# ------------------------------------------------------------------ 新增
with tab_add:
    st.caption("带 * 的为必填项。保存后系统会自动分配一个运动员编号。")
    # 性别与出生日期放在表单外，改动后立即刷新「适用标准组」。
    g1, g2 = st.columns(2)
    with g1:
        gender = st.selectbox("性别", ["男", "女"], key="add_gender")
    with g2:
        manual = st.checkbox("手动输入出生日期（可选更早年份）", key="add_birth_manual")
        if manual:
            birth_text = st.text_input("出生日期（YYYY-MM-DD）", value="2002-01-01",
                                       key="add_birth_text")
            try:
                birth = datetime.strptime(birth_text.strip(), "%Y-%m-%d").date()
            except (ValueError, AttributeError):
                birth = date(2002, 1, 1)
                st.caption("日期格式无效，按 2002-01-01 处理。")
        else:
            birth = st.date_input("出生日期", value=date(2002, 1, 1),
                                  min_value=date(1900, 1, 1), max_value=date.today(),
                                  key="add_birth")
    age = calc_age(birth, date.today())
    group = standard_group(gender, birth, date.today())
    label = AGE_GROUP_LABEL.get(group.split("|")[-1], "")
    st.info(f"**适用标准组：{group}**（{label}）｜ 当前实足年龄 {age} 岁。"
            "历史记录按测试日期当天的年龄分组，写入时即为快照，之后修改出生日期不会改变历史标准组。")

    # 运动项目为必填字段；放在表单外以便选择「其他」时实时出现自定义输入框。
    sport_kw = st.text_input("搜索运动项目（输入关键字，如「篮 / 滑雪 / 田径」）",
                             placeholder="可留空浏览全部；支持别名模糊匹配", key="add_sport_kw")
    s1, s2 = st.columns(2)
    with s1:
        sport_sel = st.selectbox("运动项目 *", options=sport_choices(keyword=sport_kw), index=None,
                                 placeholder="请选择（必填）", key="add_sport_sel")
    with s2:
        sport_custom = (st.text_input("自定义运动项目（选择「其他」时填写）",
                                      placeholder="列表外的大项名称", key="add_sport_custom")
                        if sport_sel == "其他" else "")
    sport = (sport_custom.strip() if sport_sel == "其他"
             else ("" if sport_sel is None else str(sport_sel)))
    if sport:
        st.caption(f"已选运动项目：**{sport}** ｜ 该项目的关键能力域：{'、'.join(sport_focus(sport))}。"
                   "测试记录会写入项目快照，分析报告据此按项目生成。")
    else:
        st.caption("运动项目为必填项，用于按项目生成分析报告；同一项目请保持名称一致。")

    with st.form("form_add_athlete", clear_on_submit=True):
        name = st.text_input("姓名 *", placeholder="例如：林扬")
        col3, col4 = st.columns(2)
        with col3:
            team = st.text_input("所属队伍", placeholder="例如：短跑组")
        with col4:
            event = st.text_input("专项", placeholder="例如：100米")
        note = st.text_area("备注", height=80, placeholder="伤病情况、位置、联系方式等（选填）")
        submitted = st.form_submit_button("保存运动员", type="primary", width="stretch")

    if submitted:
        missing = []
        if not name.strip():
            missing.append("姓名")
        if not sport:
            missing.append("运动项目")
        if missing:
            st.error("请填写必填项：" + "、".join(missing))
        else:
            new_id = append_row("athlete", {
                "姓名": name.strip(), "性别": gender, "出生日期": birth.isoformat(),
                "所属队伍": team.strip(), "专项": event.strip(), "备注": note.strip(),
                "运动项目": sport,
            })
            st.success(f"已保存：{name.strip()}（编号 {new_id}）")
            st.rerun()

# ------------------------------------------------------------------ 名单与编辑
with tab_list:
    df = load_athletes()
    if df.empty:
        st.info("还没有运动员，请先到「新增运动员」添加。")
    else:
        keyword = st.text_input("搜索（姓名 / 队伍 / 专项 / 运动项目）", placeholder="输入关键字即时筛选")
        view = df.copy()
        if keyword.strip():
            mask = view.astype(str).apply(
                lambda s: s.str.contains(keyword.strip(), case=False, na=False, regex=False)
            ).any(axis=1)
            view = view[mask]
        view["年龄"] = view["出生日期"].map(calc_age)
        view["标准组"] = [standard_group(g, b, date.today())
                          for g, b in zip(view["性别"], view["出生日期"])]
        view["运动项目"] = view["运动项目"].astype(str).str.strip().replace("", SPORT_UNKNOWN)
        view["出生日期"] = view["出生日期"].dt.strftime("%Y-%m-%d").fillna("")
        cols = ["运动员ID", "姓名", "性别", "出生日期", "年龄", "标准组", "所属队伍",
                "专项", "运动项目", "备注"]
        missing_sport = int((view["运动项目"] == SPORT_UNKNOWN).sum())
        if missing_sport:
            st.warning(f"有 {missing_sport} 名运动员未填写「运动项目」（必填），"
                       "请到下方「修改档案」补全；否则其记录无法按项目生成分析报告。")
        st.caption("名单中的「标准组」按当前日期计算；历史记录按测试日分组，且为写入时快照，"
                   "修改出生日期不会改变历史标准组。「运动项目」同样写入测试记录快照，"
                   "修改档案不会改变已有记录的项目归属。")
        st.dataframe(view[cols], width="stretch", hide_index=True,
                     column_config={"年龄": st.column_config.NumberColumn("年龄", format="%.0f 岁")})
        csv_download(view[cols], "运动员名单.csv", "下载运动员名单 CSV")

        st.markdown("---")
        st.subheader("修改档案")
        names = athlete_map()
        if names:
            aid = st.selectbox("选择要修改的运动员", options=list(names.keys()),
                               format_func=lambda x: f"{x} · {names[x]}")
            row = df[df["运动员ID"] == aid].iloc[0]
            b = pd.to_datetime(row["出生日期"], errors="coerce")
            st.caption("提示：修改出生日期只影响之后新建的测试与评估记录，已有记录的标准组快照保持不变。")
            with st.form("form_edit_athlete"):
                e1, e2 = st.columns(2)
                with e1:
                    new_name = st.text_input("姓名", value=str(row["姓名"]))
                with e2:
                    new_gender = st.selectbox("性别", ["男", "女"],
                                              index=0 if row["性别"] == "男" else 1)
                e3, e4, e5 = st.columns(3)
                with e3:
                    new_birth = st.date_input("出生日期",
                                              value=b.date() if not pd.isna(b) else date(2002, 1, 1),
                                              min_value=date(1900, 1, 1), max_value=date.today())
                with e4:
                    new_team = st.text_input("所属队伍", value=str(row["所属队伍"]))
                with e5:
                    new_event = st.text_input("专项", value=str(row["专项"]))
                cur_sport = str(row["运动项目"]).strip()
                edit_kw = st.text_input("搜索运动项目", placeholder="输入关键字过滤，可留空",
                                        key=f"edit_sport_kw_{aid}")
                sport_opts = sport_choices(keyword=edit_kw)
                if cur_sport and cur_sport not in sport_opts:
                    sport_opts.insert(0, cur_sport)  # 当前值始终可选，避免误改
                new_sport = st.selectbox(
                    "运动项目 *", options=sport_opts,
                    index=sport_opts.index(cur_sport) if cur_sport in sport_opts else None,
                    placeholder="请选择（必填）")
                st.caption("「专项」记录具体小项，「运动项目」记录大项并决定分析报告的分组与针对性。")
                new_note = st.text_area("备注", value=str(row["备注"]), height=70)
                if st.form_submit_button("保存修改", type="primary", width="stretch"):
                    if not new_name.strip() or not new_sport:
                        st.error("姓名与运动项目为必填项。")
                    else:
                        update_row("athlete", aid, {
                            "姓名": new_name.strip(), "性别": new_gender,
                            "出生日期": new_birth.isoformat(), "所属队伍": new_team.strip(),
                            "专项": new_event.strip(), "运动项目": str(new_sport),
                            "备注": new_note.strip(),
                        })
                        st.success("修改已保存（已有测试记录的运动项目快照保持不变）")
                        st.rerun()

# ------------------------------------------------------------------ 删除
with tab_delete:
    st.warning("删除不可恢复。默认会一并删除该运动员的测试与评估记录。")
    names = athlete_map()
    if not names:
        st.info("暂无可删除的运动员。")
    else:
        aid = st.selectbox("选择运动员", options=list(names.keys()),
                           format_func=lambda x: f"{x} · {names[x]}")
        also_delete = st.checkbox("同时删除其测试与评估记录", value=True)
        confirm = st.checkbox("我确认要删除这位运动员")
        if st.button("确认删除", type="primary", disabled=not confirm):
            if also_delete:
                t_df = load_tests()
                t_ids = t_df[t_df["运动员ID"] == aid]["记录ID"].tolist()
                if t_ids:
                    delete_rows("test", t_ids)
            delete_rows("athlete", [aid])
            st.success(f"已删除 {names[aid]}")
            st.rerun()
