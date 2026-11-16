"""公共工具模块：数据读写、项目目录、评分标准、FMS 评分、成绩单、演示数据、界面样式。

数据契约
--------
* athletes.csv：原 7 列 + 「运动项目」（建档必填）。「专项」保留为具体小项，
  「运动项目」记录大项，供按项目生成分析报告；「标准组」按当前日期实时展示，不落盘。
* tests.csv：原 7 列之后追加「测试类别、评分类型、侧别、标准组、测试时年龄、疼痛标记、运动项目」。
  其中「运动项目」与「标准组」同为写入时快照，不随档案修改而漂移。
  得分 / 等级 / FMS总分 / 不对称标记 / 结果说明 / 结果显示 均为派生量，不落盘。
* standards.csv：测试项目,性别,年龄组,达标线,优秀线,单位,方向,类别,评分类型,是否评分。
  由 27 个 continuous 评分项 × 2 性别 × 5 年龄组 = 270 行程序生成，界面可编辑。
* 所有测试数据都通过「运动项目」快照与对应运动项目关联；load_table() 对缺列一律补空，
  可直接读取旧格式备份文件，迁移时按运动员档案回填快照。
"""
from __future__ import annotations

import functools
import html
import json
import math
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from config import (
    ALLOW_DEMO_DATA, APP_VERSION, ATHLETE_FILE, BACKUP_DIR, BACKUP_KEEP,
    BASE_DIR, DATA_DIR, DATA_FILES, LOCK_FILE, LOCK_TIMEOUT, SNAPSHOT_DIR,
    SNAPSHOT_KEEP, STALE_LOCK_SECONDS, STANDARD_FILE, STANDARDS_VERSION, TEST_FILE,
)
from logging_config import audit, get_logger, log_exception

logger = get_logger()

# ---------------------------------------------------------------- 基础路径
# 路径、开关、日志与锁参数统一来自 config.py；下方只保留数据契约定义。
ATHLETE_COLS =["运动员ID", "姓名", "性别", "出生日期", "所属队伍", "专项", "备注", "运动项目"]
TEST_COLS = ["记录ID", "运动员ID", "测试日期", "测试项目", "成绩", "单位", "备注",
             "测试类别", "评分类型", "侧别", "标准组", "测试时年龄", "疼痛标记", "运动项目"]
STANDARD_COLS = ["测试项目", "性别", "年龄组", "达标线", "优秀线", "单位", "方向",
                 "类别", "评分类型", "是否评分"]

TABLES = {
    "athlete": (ATHLETE_FILE, ATHLETE_COLS, "运动员ID"),
    "test": (TEST_FILE, TEST_COLS, "记录ID"),
}


# ---------------------------------------------------------------- 每用户数据目录（MVP 隔离）
# DB 模式登录后，把本模块的数据路径常量重定向到 data/users/<用户ID>/，
# 从而复用全部既有 CSV 读写逻辑实现按用户隔离。非 DB 模式（含测试）不调用，
# 路径保持 config 默认值，行为与旧版完全一致。
_DEFAULT_PATHS = {
    "DATA_DIR": DATA_DIR,
    "ATHLETE_FILE": ATHLETE_FILE,
    "TEST_FILE": TEST_FILE,
    "STANDARD_FILE": STANDARD_FILE,
    "LOCK_FILE": LOCK_FILE,
    "SNAPSHOT_DIR": SNAPSHOT_DIR,
    "DATA_FILES": DATA_FILES,
}
_ACTIVE_USER_ID = None


def activate_user_data_dir(user_id) -> None:
    """把数据路径切换到指定用户目录；user_id=None 恢复默认（全局）目录。"""
    global _ACTIVE_USER_ID, TABLES
    _ACTIVE_USER_ID = user_id
    if user_id is None:
        for k, v in _DEFAULT_PATHS.items():
            globals()[k] = v
    else:
        # 以默认目录为基准，避免多次切换用户时路径累进嵌套。
        base = _DEFAULT_PATHS["DATA_DIR"] / "users" / str(user_id)
        athlete = base / "athletes.csv"
        test = base / "tests.csv"
        standard = base / "standards.csv"
        globals().update({
            "DATA_DIR": base,
            "ATHLETE_FILE": athlete,
            "TEST_FILE": test,
            "STANDARD_FILE": standard,
            "LOCK_FILE": base / ".write.lock",
            "SNAPSHOT_DIR": base / ".snapshots",
            "DATA_FILES": (athlete, test, standard),
        })
    TABLES = {
        "athlete": (ATHLETE_FILE, ATHLETE_COLS, "运动员ID"),
        "test": (TEST_FILE, TEST_COLS, "记录ID"),
    }


def active_user_id():
    return _ACTIVE_USER_ID


def db_mode_enabled() -> bool:
    """DB 模式开关：优先 Streamlit secrets，回退环境变量。"""
    try:
        if hasattr(st, "secrets") and "EFA_USE_DB" in st.secrets:
            return str(st.secrets["EFA_USE_DB"]).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        pass
    import os
    return os.getenv("EFA_USE_DB", "0").strip().lower() in {"1", "true", "yes", "on"}


def _db_user_id():
    """DB 模式下返回当前登录用户 id，否则 None。"""
    if not db_mode_enabled():
        return None
    u = st.session_state.get("efa_user") or {}
    return u.get("id")


def _kind_from_path(path) -> str:
    name = Path(path).name
    return {"athletes.csv": "athlete", "tests.csv": "test",
            "standards.csv": "standards"}.get(name, name)

GENDERS = ["男", "女"]
AGE_GROUPS = ["U16", "U18", "U20", "成年", "大师"]
AGE_GROUP_LABEL = {
    "U16": "U16（<16 岁）", "U18": "U18（16-17 岁）", "U20": "U20（18-19 岁）",
    "成年": "成年（20-34 岁）", "大师": "大师（>=35 岁）",
}
FMS_RISK_LINE = 14
STANDARDS_DISCLAIMER = "非专业常模，请按本队实测替换"
FMS_PAIN_WARNING = "存在疼痛，建议转介医疗或康复评估"
FMS_PAIN_NOTE = "含疼痛项，结果需谨慎解读"

# ---------------------------------------------------------------- 项目目录
# 原 9 项名称与阈值一字不改，保证现有 192 条旧记录仍可评分。
# 27 个 continuous 评分项 + 4 个 descriptive 描述项 = 约 31 项内置体能项目。
CONTINUOUS_ITEMS = {
    "30米冲刺": {"unit": "秒", "direction": "越小越好", "category": "速度", "pass": 4.8, "excellent": 4.0},
    "10米起跑": {"unit": "秒", "direction": "越小越好", "category": "速度", "pass": 2.0, "excellent": 1.6},
    "60米冲刺": {"unit": "秒", "direction": "越小越好", "category": "速度", "pass": 8.5, "excellent": 7.2},
    "100米冲刺": {"unit": "秒", "direction": "越小越好", "category": "速度", "pass": 13.5, "excellent": 11.5},
    "立定跳远": {"unit": "厘米", "direction": "越大越好", "category": "爆发力", "pass": 220, "excellent": 280},
    "纵跳摸高": {"unit": "厘米", "direction": "越大越好", "category": "爆发力", "pass": 45, "excellent": 70},
    "立定三级跳远": {"unit": "米", "direction": "越大越好", "category": "爆发力", "pass": 6.5, "excellent": 8.5},
    "药球前抛": {"unit": "米", "direction": "越大越好", "category": "爆发力", "pass": 7.0, "excellent": 11.0},
    "原地纵跳": {"unit": "厘米", "direction": "越大越好", "category": "爆发力", "pass": 40, "excellent": 65},
    "深蹲1RM": {"unit": "公斤", "direction": "越大越好", "category": "力量", "pass": 80, "excellent": 140},
    "引体向上": {"unit": "次", "direction": "越大越好", "category": "力量", "pass": 8, "excellent": 20},
    "卧推1RM": {"unit": "公斤", "direction": "越大越好", "category": "力量", "pass": 60, "excellent": 110},
    "硬拉1RM": {"unit": "公斤", "direction": "越大越好", "category": "力量", "pass": 100, "excellent": 170},
    "握力": {"unit": "公斤", "direction": "越大越好", "category": "力量", "pass": 35, "excellent": 60},
    "平板支撑": {"unit": "秒", "direction": "越大越好", "category": "力量", "pass": 60, "excellent": 180},
    "12分钟跑": {"unit": "米", "direction": "越大越好", "category": "耐力", "pass": 2400, "excellent": 3200},
    "Yo-Yo间歇跑": {"unit": "米", "direction": "越大越好", "category": "耐力", "pass": 1200, "excellent": 2400},
    "3000米跑": {"unit": "秒", "direction": "越小越好", "category": "耐力", "pass": 780, "excellent": 660},
    "最大摄氧量": {"unit": "毫升/公斤/分", "direction": "越大越好", "category": "耐力", "pass": 45, "excellent": 65},
    "T型灵敏测试": {"unit": "秒", "direction": "越小越好", "category": "灵敏协调", "pass": 11.5, "excellent": 9.0},
    "5-10-5折返跑": {"unit": "秒", "direction": "越小越好", "category": "灵敏协调", "pass": 5.5, "excellent": 4.5},
    "伊利诺伊灵敏跑": {"unit": "秒", "direction": "越小越好", "category": "灵敏协调", "pass": 17.5, "excellent": 15.0},
    "坐位体前屈": {"unit": "厘米", "direction": "越大越好", "category": "柔韧", "pass": 5, "excellent": 20},
    "直腿抬高角度": {"unit": "度", "direction": "越大越好", "category": "柔韧", "pass": 70, "excellent": 95},
    "体脂率": {"unit": "%", "direction": "越小越好", "category": "身体成分", "pass": 18, "excellent": 10},
    "静息心率": {"unit": "次/分", "direction": "越小越好", "category": "身体成分", "pass": 70, "excellent": 52},
    "肌肉量": {"unit": "公斤", "direction": "越大越好", "category": "身体成分", "pass": 45, "excellent": 60},
}

LEGACY_ITEMS = ["30米冲刺", "立定跳远", "纵跳摸高", "深蹲1RM", "引体向上",
                "12分钟跑", "Yo-Yo间歇跑", "体脂率", "静息心率"]

DESCRIPTIVE_ITEMS = {
    "身高": {"unit": "厘米", "category": "身体形态"},
    "体重": {"unit": "公斤", "category": "身体形态"},
    "BMI": {"unit": "千克/米²", "category": "身体形态"},
    "腰臀比": {"unit": "", "category": "身体形态"},
}

# FMS 完整版：7 动作（0～3 分）、5 个双侧动作、3 项排除性测试。
# (名称, 是否双侧, 关联的排除性测试)
FMS_MOVEMENTS = [
    ("FMS深蹲", False, ""),
    ("FMS跨栏步", True, ""),
    ("FMS直线弓步", True, ""),
    ("FMS肩部灵活性", True, "FMS肩部排除测试"),
    ("FMS主动直腿抬高", True, ""),
    ("FMS躯干稳定俯卧撑", False, "FMS俯卧撑排除测试"),
    ("FMS旋转稳定性", True, "FMS旋转稳定性排除测试"),
]
FMS_CLEARING = {
    "FMS肩部排除测试": "FMS肩部灵活性",
    "FMS俯卧撑排除测试": "FMS躯干稳定俯卧撑",
    "FMS旋转稳定性排除测试": "FMS旋转稳定性",
}
FMS_BILATERAL = {name for name, bilateral, _ in FMS_MOVEMENTS if bilateral}

# 示例常模缩放系数：以「成年男」为 1.00，其余按年龄组 / 性别缩放。
AGE_FACTOR = {"U16": 0.80, "U18": 0.92, "U20": 0.98, "成年": 1.00, "大师": 0.88}
GENDER_FACTOR = {"男": 1.00, "女": 0.88}
# 示意性留白：这些组合默认不评分，用于演示「待配置清单」与「—」。
UNCONFIGURED = {
    "U16": {"深蹲1RM", "卧推1RM", "硬拉1RM"},
    "大师": {"100米冲刺", "Yo-Yo间歇跑", "3000米跑", "最大摄氧量", "药球前抛", "硬拉1RM"},
}

# ---------------------------------------------------------------- 运动项目
# 「运动项目」是建档必填字段，也是分析报告的分组与针对性依据。
# 「专项」保留为具体小项（如 100米），「运动项目」记录大项（如 短跑）。
# 能力域取值（与 standards.csv 的「类别」列一致，报告据此归类不足项）
# 速度 / 爆发力 / 力量 / 耐力 / 灵敏协调 / 柔韧 / 身体成分
SPORT_FOCUS = {
    # --- 田径分项（历史名称，保留以兼容已有档案与测试） ---
    "短跑": ["速度", "爆发力", "灵敏协调"],
    "中长跑": ["耐力", "力量", "身体成分"],
    "长跑": ["耐力", "身体成分", "力量"],
    "跳跃": ["爆发力", "速度", "柔韧"],
    "投掷": ["力量", "爆发力", "灵敏协调"],
    "全能": ["速度", "力量", "耐力", "爆发力"],
    # --- 夏季奥运大项 ---
    "田径": ["速度", "爆发力", "耐力", "力量"],
    "游泳": ["耐力", "柔韧", "力量"],
    "跳水": ["柔韧", "爆发力", "灵敏协调"],
    "花样游泳": ["耐力", "柔韧", "灵敏协调"],
    "水球": ["耐力", "力量", "灵敏协调"],
    "射箭": ["力量", "灵敏协调"],
    "射击": ["力量", "灵敏协调"],
    "羽毛球": ["灵敏协调", "速度", "耐力"],
    "篮球": ["爆发力", "灵敏协调", "耐力"],
    "三人篮球": ["爆发力", "灵敏协调", "耐力"],
    "足球": ["耐力", "灵敏协调", "速度"],
    "排球": ["爆发力", "灵敏协调", "力量"],
    "沙滩排球": ["爆发力", "灵敏协调", "耐力"],
    "乒乓球": ["灵敏协调", "速度"],
    "网球": ["灵敏协调", "爆发力", "速度"],
    "手球": ["爆发力", "灵敏协调", "耐力"],
    "曲棍球": ["耐力", "灵敏协调", "速度"],
    "橄榄球": ["力量", "爆发力", "速度"],
    "棒垒球": ["爆发力", "速度", "灵敏协调"],
    "高尔夫": ["灵敏协调", "柔韧"],
    "拳击": ["速度", "爆发力", "灵敏协调"],
    "跆拳道": ["柔韧", "速度", "爆发力"],
    "摔跤": ["力量", "爆发力", "柔韧"],
    "柔道": ["力量", "柔韧", "灵敏协调"],
    "举重": ["力量", "爆发力"],
    "体操": ["柔韧", "力量", "爆发力"],
    "艺术体操": ["柔韧", "灵敏协调", "爆发力"],
    "蹦床": ["爆发力", "柔韧", "灵敏协调"],
    "自行车": ["耐力", "力量"],
    "铁人三项": ["耐力", "力量", "身体成分"],
    "赛艇": ["耐力", "力量"],
    "皮划艇": ["耐力", "力量", "灵敏协调"],
    "帆船": ["耐力", "灵敏协调"],
    "马术": ["灵敏协调", "柔韧"],
    "击剑": ["速度", "灵敏协调", "爆发力"],
    "现代五项": ["耐力", "力量", "速度"],
    "滑板": ["灵敏协调", "爆发力"],
    "冲浪": ["耐力", "灵敏协调", "柔韧"],
    "攀岩": ["力量", "柔韧", "耐力"],
    # --- 冬季奥运大项 ---
    "速度滑冰": ["速度", "耐力", "力量"],
    "短道速滑": ["速度", "灵敏协调", "爆发力"],
    "花样滑冰": ["柔韧", "灵敏协调", "爆发力"],
    "冰球": ["力量", "速度", "灵敏协调"],
    "冰壶": ["灵敏协调", "力量"],
    "高山滑雪": ["力量", "灵敏协调", "爆发力"],
    "越野滑雪": ["耐力", "力量"],
    "跳台滑雪": ["爆发力", "灵敏协调"],
    "北欧两项": ["耐力", "爆发力"],
    "自由式滑雪": ["爆发力", "灵敏协调", "柔韧"],
    "单板滑雪": ["爆发力", "灵敏协调", "柔韧"],
    "冬季两项": ["耐力", "力量", "灵敏协调"],
    "雪车": ["爆发力", "力量"],
    "钢架雪车": ["爆发力", "力量"],
    "雪橇": ["爆发力", "灵敏协调"],
    # --- 兼容旧值与其他 ---
    "冰雪": ["爆发力", "力量", "灵敏协调"],
    "其他": ["速度", "爆发力", "力量", "耐力"],
}
# 搜索用别名：输入习惯名/旧名也能命中标准项目名（大小写不敏感）。
SPORT_ALIASES = {
    "田径": "田径", "短跑": "短跑", "中长跑": "中长跑", "长跑": "长跑",
    "跳远": "跳跃", "跳高": "跳跃", "三级跳": "跳跃", "撑杆跳": "跳跃",
    "铅球": "投掷", "标枪": "投掷", "铁饼": "投掷", "链球": "投掷",
    "100米": "短跑", "200米": "短跑", "400米": "短跑", "800米": "中长跑",
    "1500米": "中长跑", "5000米": "长跑", "10000米": "长跑", "马拉松": "长跑",
    "篮": "篮球", "足": "足球", "排": "排球", "羽毛": "羽毛球",
    "乒乓": "乒乓球", "滑冰": "速度滑冰", "滑雪": "高山滑雪",
    "冰": "冰球", "雪": "高山滑雪", "游泳": "游泳", "举重": "举重",
}
SPORT_OPTIONS = list(SPORT_FOCUS)
SPORT_UNKNOWN = "未填写"
DEFAULT_FOCUS = ["速度", "爆发力", "力量", "耐力"]

# 迁移用：由「专项 / 所属队伍」推断运动项目大项（按具体关键词优先匹配）。
EVENT_TO_SPORT = {
    "马拉松": "长跑", "越野": "长跑",
    "100米": "短跑", "200米": "短跑", "400米": "短跑", "短跑": "短跑",
    "800米": "中长跑", "1500米": "中长跑", "5000米": "中长跑", "10000米": "中长跑",
    "中长跑": "中长跑",
    "跳远": "跳跃", "跳高": "跳跃", "三级跳": "跳跃", "撑杆": "跳跃", "跳跃": "跳跃",
    "铅球": "投掷", "标枪": "投掷", "铁饼": "投掷", "链球": "投掷", "投掷": "投掷",
    "全能": "全能", "游泳": "游泳", "篮球": "篮球", "足球": "足球", "排球": "排球",
    "羽毛球": "羽毛球", "乒乓球": "乒乓球", "网球": "网球", "举重": "举重",
    "体操": "体操", "自行车": "自行车", "赛艇": "赛艇", "拳击": "拳击",
    "跆拳道": "跆拳道", "冰雪": "冰雪",
    "长跑": "长跑",  # 兜底关键词放最后，避免先于「中长跑」命中
}


def sport_from_event(event, team="") -> str:
    """由「专项 / 所属队伍」推断运动项目大项；具体专项优先，队伍仅作兜底。"""
    for text in (_clean(event), _clean(team)):
        if not text:
            continue
        for keyword, sport in EVENT_TO_SPORT.items():
            if keyword in text:
                return sport
    return ""


def sport_focus(sport) -> list[str]:
    """该运动项目的关键能力域，用于生成针对性分析。"""
    return SPORT_FOCUS.get(_clean(sport), DEFAULT_FOCUS)


def sport_choices(*extra, keyword: str = "") -> list[str]:
    """表单选项：预置大项 + 历史值；keyword 按名称/别名模糊过滤。

    - 保留全部现有选项以兼容历史档案与回归测试；
    - keyword 命中别名时优先把对应的标准项目名放在列表最前，便于快速选择；
    - keyword 为空时返回全部（含历史值），行为与旧版一致。
    """
    opts = list(SPORT_OPTIONS)
    for value in extra:
        value = _clean(value)
        if value and value not in opts:
            opts.append(value)
    kw = _clean(keyword).lower()
    if not kw:
        return opts
    matched, aliases = [], []
    for name in opts:
        if kw in name.lower():
            matched.append(name)
        elif SPORT_ALIASES.get(name, "").lower().find(kw) >= 0:
            aliases.append(name)
        elif any(kw in alias.lower() for alias, target in SPORT_ALIASES.items()
                 if target == name):
            aliases.append(name)
    return matched + aliases


REPORT_COLS = ["姓名", "性别", "运动项目", "测试日期", "测试类别", "评分类型", "测试项目", "侧别",
               "结果显示", "单位", "标准组", "测试时年龄", "得分", "等级",
               "FMS总分", "不对称标记", "疼痛标记", "结果说明", "备注"]


def _clean(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


# ---------------------------------------------------------------- 年龄分档
def calc_age(birth, on: date | None = None) -> int | None:
    """按指定日期（默认今天）计算实足年龄。"""
    b = pd.to_datetime(birth, errors="coerce")
    if pd.isna(b):
        return None
    ref = on or date.today()
    return ref.year - b.year - ((ref.month, ref.day) < (b.month, b.day))


def age_group(age) -> str:
    if age is None:
        return ""
    try:
        if pd.isna(age):
            return ""
    except (TypeError, ValueError):
        pass
    age = int(age)
    if age < 16:
        return "U16"
    if age < 18:
        return "U18"
    if age < 20:
        return "U20"
    if age < 35:
        return "成年"
    return "大师"


def group_by_age(gender: str, age) -> str:
    """分组键「性别|年龄组」。"""
    g = age_group(age)
    return f"{_clean(gender)}|{g}" if g else ""


def standard_group(gender, birth, on: date | None = None) -> str:
    """按测试日期当天实足年龄自动分档，返回写入时的标准组快照。"""
    return group_by_age(_clean(gender), calc_age(birth, on))


# ---------------------------------------------------------------- CSV 读写
def ensure_files() -> None:
    """首次运行时创建空表格与内置示例标准，并完成一次性旧数据迁移。

    DB 模式下改为：为当前登录用户在数据库初始化一套空表 + 默认标准（仅一次）。
    """
    uid = _db_user_id()
    if uid is not None:
        # 每个会话/每个用户只初始化一次，避免每次读表都多一次跨网络查询。
        flag = "_efa_db_ready"
        try:
            ready = st.session_state.get(flag)
        except Exception:
            ready = None
        if ready == uid:
            return
        import db
        db.init_db()
        if not db.has_data(uid):
            db.save_frame(uid, "athlete", [], ATHLETE_COLS)
            db.save_frame(uid, "test", [], TEST_COLS)
            std = generate_default_standards()
            db.save_frame(uid, "standards",
                          std[STANDARD_COLS].to_dict(orient="records"), STANDARD_COLS)
        for k in ("athlete", "test", "standards"):
            _invalidate_frame(uid, k)
        try:
            st.session_state[flag] = uid
        except Exception:
            pass
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path, cols in ((ATHLETE_FILE, ATHLETE_COLS), (TEST_FILE, TEST_COLS)):
        if not path.exists():
            atomic_write_csv(pd.DataFrame(columns=cols), path, snapshot=False)
    if not STANDARD_FILE.exists():
        atomic_write_csv(generate_default_standards(), STANDARD_FILE, snapshot=False)
    migrate_legacy_athletes()
    migrate_legacy_tests()


def generate_default_standards() -> pd.DataFrame:
    """程序生成 27 项 × 2 性别 × 5 年龄组 = 270 行内置示意性示例常模。"""
    rows = []
    for item, s in CONTINUOUS_ITEMS.items():
        for gender in GENDERS:
            for ag in AGE_GROUPS:
                factor = AGE_FACTOR[ag] * GENDER_FACTOR[gender]
                if s["direction"] == "越小越好":
                    pass_line, excellent_line = s["pass"] / factor, s["excellent"] / factor
                else:
                    pass_line, excellent_line = s["pass"] * factor, s["excellent"] * factor
                configured = item not in UNCONFIGURED.get(ag, set())
                rows.append({
                    "测试项目": item, "性别": gender, "年龄组": ag,
                    "达标线": round(pass_line, 2) if configured else "",
                    "优秀线": round(excellent_line, 2) if configured else "",
                    "单位": s["unit"], "方向": s["direction"], "类别": s["category"],
                    "评分类型": "continuous", "是否评分": "是" if configured else "否",
                })
    return pd.DataFrame(rows, columns=STANDARD_COLS)


def migrate_legacy_athletes() -> None:
    """旧 7 列 athletes.csv 一次性迁移：追加必填字段「运动项目」。

    优先保留已有值；否则由「专项 / 所属队伍」推断大项。推断不出的留空，
    界面会标为「未填写」并提示补全。
    """
    try:
        raw = pd.read_csv(ATHLETE_FILE, encoding="utf-8-sig", dtype=str)
    except Exception:
        return
    if raw.empty:
        return
    if "运动项目" not in raw.columns:
        raw["运动项目"] = ""
    blank = raw["运动项目"].fillna("").astype(str).str.strip().eq("")
    if not blank.any():
        return
    values = []
    for _, r in raw.iterrows():
        current = _clean(r.get("运动项目"))
        values.append(current or sport_from_event(r.get("专项"), r.get("所属队伍")))
    raw["运动项目"] = values
    atomic_write_csv(raw[ATHLETE_COLS], ATHLETE_FILE)
    audit("migrate_legacy_athletes", target=ATHLETE_FILE.name, rows=len(raw))


def migrate_legacy_tests() -> None:
    """旧格式 tests.csv 一次性迁移：补齐契约列、标准组与运动项目快照。

    侧别 / 疼痛标记属于 FMS 专用列，旧行留空。已有的标准组快照不会被覆盖。
    只有 7 列的旧备份仍可由 load_table() 的「缺列补空」兼容读取，不会报错。
    """
    try:
        raw = pd.read_csv(TEST_FILE, encoding="utf-8-sig", dtype=str)
    except Exception:
        return
    if raw.empty:
        return
    missing = [c for c in TEST_COLS if c not in raw.columns]
    sport_blank = ("运动项目" not in raw.columns
                   or raw["运动项目"].fillna("").astype(str).str.strip().eq("").any())
    if not missing and not sport_blank:
        return
    try:
        athletes = pd.read_csv(ATHLETE_FILE, encoding="utf-8-sig", dtype=str)
    except Exception:
        athletes = pd.DataFrame(columns=ATHLETE_COLS)
    index, sport_index = {}, {}
    if not athletes.empty:
        for _, r in athletes.iterrows():
            aid = _clean(r.get("运动员ID"))
            index[aid] = (_clean(r.get("性别")), r.get("出生日期", ""))
            sport_index[aid] = (_clean(r.get("运动项目"))
                                or sport_from_event(r.get("专项"), r.get("所属队伍")))
    for c in TEST_COLS:
        if c not in raw.columns:
            raw[c] = ""
    groups, ages, categories, types, sports = [], [], [], [], []
    for _, r in raw.iterrows():
        aid = _clean(r.get("运动员ID"))
        gender, birth = index.get(aid, ("", ""))
        parsed = pd.to_datetime(r.get("测试日期"), errors="coerce")
        ref = parsed.date() if pd.notna(parsed) else date.today()
        age = calc_age(birth, ref) if birth else None
        groups.append(standard_group(gender, birth, ref) if gender else "")
        ages.append(str(int(age)) if age is not None else "")
        categories.append("体能")
        types.append("continuous" if _clean(r.get("测试项目")) in CONTINUOUS_ITEMS else "")
        sports.append(_clean(r.get("运动项目")) or sport_index.get(aid, ""))
    for col, values in (("标准组", groups), ("测试时年龄", ages),
                        ("测试类别", categories), ("评分类型", types),
                        ("运动项目", sports)):
        raw[col] = [old if _clean(old) else new for old, new in zip(raw[col], values)]
    atomic_write_csv(raw[TEST_COLS], TEST_FILE)
    audit("migrate_legacy_tests", target=TEST_FILE.name, rows=len(raw))


class DataFileError(RuntimeError):
    """数据文件损坏 / 无法写入；错误信息可直接展示给用户。"""


class DataLockTimeout(RuntimeError):
    """单写端约束被触发：另一处正在写入。"""


def _user_error(message: str) -> None:
    """页面上下文中给出可读提示；非页面上下文只写日志。"""
    logger.error(message)
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            st.error(message)
    except Exception:
        pass


# ---------------------------------------------------------------- 输入校验与安全
class ValidationError(ValueError):
    """录入数据不合法；错误信息可直接展示给用户。"""


# 只有文本类字段需要 CSV 公式注入转义；数值列（成绩 / 阈值 / 年龄 / ID）保持原样。
_TEXT_COLS = {"姓名", "性别", "所属队伍", "专项", "备注", "运动项目", "测试项目",
              "单位", "测试类别", "评分类型", "侧别", "标准组", "疼痛标记",
              "类别", "方向", "年龄组", "记录ID", "运动员ID"}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_text(value, field: str = "") -> str:
    """防止 CSV 公式注入：以 = + - @ 等开头的文本前置单引号。"""
    text = _clean(value)
    if text and text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def escape_html(value) -> str:
    """用于 unsafe_allow_html 场景的显示层转义。"""
    return html.escape(_clean(value), quote=True)


def validate_date(value, *, allow_future_days: int = 1) -> str:
    """校验日期可解析且落在合理范围，返回 YYYY-MM-DD。"""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValidationError(f"日期「{_clean(value)}」无法识别，请按 YYYY-MM-DD 填写。")
    day = parsed.date()
    if day > date.today() + timedelta(days=allow_future_days):
        raise ValidationError("测试日期不能晚于明天，请检查是否填错年份。")
    if day < date(1950, 1, 1):
        raise ValidationError("日期不能早于 1950-01-01，请检查。")
    return day.isoformat()


def validate_score(value, kind: str) -> float | None:
    """校验成绩：数值有限；ordinal 必须在 0～3；binary 不做数值校验。"""
    if _clean(kind) == "binary":
        return None
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num):
        raise ValidationError(f"成绩「{_clean(value)}」不是有效数字，请重新填写。")
    num = float(num)
    if not math.isfinite(num):
        raise ValidationError("成绩不能是无穷大或非数值。")
    if _clean(kind) == "ordinal" and not 0 <= num <= 3:
        raise ValidationError("FMS 动作得分必须在 0～3 之间。")
    return num


def validate_test_row(row: dict) -> dict:
    """写入前校验测试记录，返回补上合法日期与净化文本的新 row。"""
    out = dict(row)
    if _clean(out.get("测试日期")):
        out["测试日期"] = validate_date(out["测试日期"])
    validate_score(out.get("成绩"), out.get("评分类型", "continuous"))
    for col in ("备注", "测试项目", "单位", "运动项目", "侧别", "标准组"):
        if col in out:
            out[col] = sanitize_text(out[col], col)
    return out


def validate_athlete_row(row: dict) -> dict:
    """写入前校验档案：姓名与运动项目必填、出生日期合法。"""
    out = dict(row)
    if not _clean(out.get("姓名")):
        raise ValidationError("姓名为必填项。")
    if not _clean(out.get("运动项目")):
        raise ValidationError("运动项目为必填项，请在建档时选择。")
    if _clean(out.get("出生日期")):
        out["出生日期"] = validate_date(out["出生日期"])
    for col in ("姓名", "所属队伍", "专项", "备注", "运动项目", "性别"):
        if col in out:
            out[col] = sanitize_text(out[col], col)
    return out


_hooks_installed = False


def install_error_hooks() -> None:
    """把未捕获异常（主线程 / 子线程）写入日志与审计，前端仍由 Streamlit 展示。"""
    global _hooks_installed
    if _hooks_installed:
        return
    original = sys.excepthook
    original_thread = threading.excepthook

    def hook(exc_type, exc, tb):
        logger.error("未捕获异常", exc_info=(exc_type, exc, tb))
        audit("unhandled_exception", result="error",
              error=f"{exc_type.__name__}: {exc}")
        original(exc_type, exc, tb)

    def thread_hook(args):
        logger.error("线程未捕获异常",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        audit("unhandled_exception", result="error", thread=args.thread.name if args.thread else "",
              error=f"{args.exc_type.__name__}: {args.exc_value}")
        original_thread(args)

    sys.excepthook = hook
    threading.excepthook = thread_hook
    _hooks_installed = True


_lock_depth = threading.local()


@contextmanager
def data_lock(timeout: float | None = None):
    """进程内可重入 + 跨进程文件锁，覆盖整个「读-改-写」过程。

    V1.0 约定单写端；锁同时防止两个 Streamlit 会话并发追加导致丢记录。
    """
    depth = getattr(_lock_depth, "value", 0)
    if depth:
        _lock_depth.value = depth + 1
        try:
            yield
        finally:
            _lock_depth.value = depth
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (LOCK_TIMEOUT if timeout is None else timeout)
    fd = None
    while fd is None:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > STALE_LOCK_SECONDS:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise DataLockTimeout(
                    "另一处正在写入数据（单写端约束）。请稍后重试；"
                    "若持续提示，请检查 data/.write.lock 是否为残留锁文件。")
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    _lock_depth.value = 1
    try:
        yield
    finally:
        _lock_depth.value = 0
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def with_data_lock(fn):
    """把整个函数体纳入单写端锁，避免逐处缩进改动。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with data_lock():
            return fn(*args, **kwargs)
    return wrapper


def _prune(folder: Path, keep: int) -> None:
    """按目录名倒序保留最近 keep 份快照 / 备份。"""
    if not folder.exists() or keep <= 0:
        return
    entries = sorted((p for p in folder.iterdir() if p.is_dir()),
                     key=lambda p: p.name, reverse=True)
    for old in entries[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def _snapshot(path: Path) -> Path | None:
    """写前把当前文件复制到 data/.snapshots/<时间戳>/，保留最近若干份。"""
    if not path.exists() or path.stat().st_size == 0:
        return None
    folder = SNAPSHOT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    shutil.copy2(path, target)
    _prune(SNAPSHOT_DIR, SNAPSHOT_KEEP)
    return target


def atomic_write_csv(df: pd.DataFrame, path: Path, *, snapshot: bool = True) -> None:
    """写前快照 → 同目录临时文件 → fsync → os.replace 原子替换。"""
    with data_lock():
        try:
            snap = _snapshot(path) if snapshot else None
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp",
                                       dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
                    df.to_csv(fh, index=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            audit("write_csv", target=path.name, rows=int(len(df)),
                  snapshot=snap.parent.name if snap else "")
        except Exception as exc:
            log_exception("写入数据表", target=path.name, error=str(exc))
            raise DataFileError(f"无法写入 {path.name}：{exc}") from exc


def create_backup(label: str = "manual") -> Path:
    """把 3 个 CSV 整体复制到 backups/<时间戳>_<label>/，并写清单。"""
    ensure_files()
    folder = BACKUP_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{label}"
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"created": datetime.now().isoformat(timespec="seconds"),
                "label": label, "app_version": APP_VERSION,
                "standards_version": STANDARDS_VERSION, "files": {}}
    with data_lock():
        for src in DATA_FILES:
            if src.exists():
                shutil.copy2(src, folder / src.name)
                manifest["files"][src.name] = {"bytes": src.stat().st_size}
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("backup", target=folder.name, files=len(manifest["files"]))
    _prune(BACKUP_DIR, BACKUP_KEEP)
    return folder


def list_backups() -> list[dict]:
    """按时间倒序列出备份，标注是否完整（3 个 CSV 齐全）。"""
    if not BACKUP_DIR.exists():
        return []
    out = []
    for folder in sorted((p for p in BACKUP_DIR.iterdir() if p.is_dir()),
                         key=lambda p: p.name, reverse=True):
        files = {p.name for p in folder.glob("*.csv")}
        out.append({"name": folder.name, "path": folder,
                    "complete": all(f.name in files for f in DATA_FILES),
                    "files": len(files)})
    return out


def restore_backup(folder) -> int:
    """校验备份完整性 → 先备份当前状态 → 原子替换 3 个 CSV。"""
    folder = Path(folder)
    missing = [f.name for f in DATA_FILES if not (folder / f.name).exists()]
    if missing:
        raise DataFileError(f"备份不完整，缺少：{'、'.join(missing)}；已取消恢复。")
    for src in DATA_FILES:
        try:
            pd.read_csv(folder / src.name, encoding="utf-8-sig", nrows=1)
        except Exception as exc:
            raise DataFileError(f"备份中的 {src.name} 无法读取：{exc}；已取消恢复。") from exc
    create_backup("before_restore")
    with data_lock():
        for src in DATA_FILES:
            tmp = src.with_name(src.name + ".restore.tmp")
            shutil.copy2(folder / src.name, tmp)
            os.replace(tmp, src)
    audit("restore", target=folder.name, files=len(DATA_FILES))
    return len(DATA_FILES)


# DB 模式读表缓存：一次页面渲染里同一张表常被读多次（侧栏、首页、卡片），
# 每次都跨洋查库会让每次交互都变慢。按 (用户ID, 表类型) 缓存，短 TTL 兜底，
# 写入时立即失效，避免看到旧数据或跨用户串数据。
_FRAME_CACHE = {}
_FRAME_CACHE_TTL = 5.0


def _load_frame(uid, kind: str):
    import db
    key = (str(uid), kind)
    now = time.monotonic()
    hit = _FRAME_CACHE.get(key)
    if hit and now - hit[0] < _FRAME_CACHE_TTL:
        return hit[1], hit[2]
    records, saved_cols = db.load_frame(uid, kind)
    _FRAME_CACHE[key] = (now, records, saved_cols)
    return records, saved_cols


def _invalidate_frame(uid, kind: str) -> None:
    _FRAME_CACHE.pop((str(uid), kind), None)


def load_table(path: Path, cols: list[str]) -> pd.DataFrame:
    """读取表格；缺列自动补空；损坏文件给出恢复指引并返回空表，不白屏。

    DB 模式下按当前登录用户从数据库读取；否则读 CSV 文件（旧行为）。
    """
    ensure_files()
    uid = _db_user_id()
    if uid is not None:
        records, saved_cols = _load_frame(uid, _kind_from_path(path))
        if records is None:
            return pd.DataFrame(columns=cols).fillna("")
        df = pd.DataFrame(records)
        use_cols = saved_cols or cols
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols].copy().fillna("")
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        log_exception("读取数据表", target=path.name, error=str(exc))
        _user_error(f"数据文件 {path.name} 无法读取（可能损坏、编码不符或被 Excel 占用）。"
                    "请关闭该文件，或从「备份与恢复」选择最近的备份恢复；本次按空表处理，"
                    "请勿在此状态下保存数据。")
        return pd.DataFrame(columns=cols).fillna("")
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols].copy().fillna("")


def save_table(df: pd.DataFrame, path: Path, cols: list[str]) -> None:
    """统一格式化日期列后落盘：DB 模式写数据库，否则写 CSV。"""
    ensure_files()
    out = df[cols].copy()
    for c in ("出生日期", "测试日期"):
        if c in out.columns:
            out[c] = pd.to_datetime(out[c].astype(str), errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    for c in out.columns:
        if c in _TEXT_COLS:
            out[c] = out[c].map(lambda v: sanitize_text(v, c))
    uid = _db_user_id()
    if uid is not None:
        import db
        kind = _kind_from_path(path)
        db.save_frame(uid, kind, out.to_dict(orient="records"), list(cols))
        _invalidate_frame(uid, kind)
        return
    atomic_write_csv(out, path)


def load_athletes() -> pd.DataFrame:
    df = load_table(ATHLETE_FILE, ATHLETE_COLS)
    df["运动员ID"] = df["运动员ID"].astype(str).str.strip()
    df["出生日期"] = pd.to_datetime(df["出生日期"], errors="coerce")
    return df


def _athlete_index() -> dict:
    """{运动员ID: (性别, 出生日期, 运动项目)}。"""
    df = load_table(ATHLETE_FILE, ATHLETE_COLS)
    return {_clean(r.get("运动员ID")): (_clean(r.get("性别")), r.get("出生日期", ""),
                                        _clean(r.get("运动项目")))
            for _, r in df.iterrows()}


def sport_map() -> dict[str, str]:
    """{运动员ID: 运动项目}；缺项统一标为「未填写」，便于界面提示补全。"""
    return {aid: (info[2] or SPORT_UNKNOWN) for aid, info in _athlete_index().items()}


def _fill_sport(rows: list[dict]) -> list[dict]:
    """写入测试记录前补上运动项目快照，保证每条测试数据都关联到运动项目。"""
    sports = sport_map()
    out = []
    for row in rows:
        row = dict(row)
        if not _clean(row.get("运动项目")):
            row["运动项目"] = sports.get(_clean(row.get("运动员ID")), SPORT_UNKNOWN)
        out.append(row)
    return out


def load_standards() -> pd.DataFrame:
    df = load_table(STANDARD_FILE, STANDARD_COLS)
    for c in ("达标线", "优秀线"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@with_data_lock
def save_standards(df: pd.DataFrame) -> None:
    """编辑评分标准会重算全部历史得分，写入前自动留快照并记审计。"""
    save_table(df, STANDARD_FILE, STANDARD_COLS)  # DB/CSV 模式自动分流
    audit("save_standards", target=STANDARD_FILE.name, rows=int(len(df)))


def item_catalog() -> pd.DataFrame:
    """内置 31 项体能项目 + 10 项 FMS 项目 + standards.csv 中的自定义项目。"""
    rows = []
    for name, s in CONTINUOUS_ITEMS.items():
        rows.append({"测试项目": name, "单位": s["unit"], "方向": s["direction"],
                     "类别": s["category"], "测试类别": "体能",
                     "评分类型": "continuous", "是否评分": "是"})
    for name, s in DESCRIPTIVE_ITEMS.items():
        rows.append({"测试项目": name, "单位": s["unit"], "方向": "无",
                     "类别": s["category"], "测试类别": "身体形态",
                     "评分类型": "descriptive", "是否评分": "否"})
    for name, _, _ in FMS_MOVEMENTS:
        rows.append({"测试项目": name, "单位": "分", "方向": "无", "类别": "FMS",
                     "测试类别": "FMS", "评分类型": "ordinal", "是否评分": "否"})
    for name in FMS_CLEARING:
        rows.append({"测试项目": name, "单位": "", "方向": "无", "类别": "FMS",
                     "测试类别": "FMS", "评分类型": "binary", "是否评分": "否"})
    builtin = {r["测试项目"] for r in rows}
    std = load_standards()
    if not std.empty:
        for name, g in std.groupby("测试项目", sort=False):
            if name in builtin:
                continue
            r = g.iloc[0]
            kind = _clean(r.get("评分类型")) or "continuous"
            rows.append({"测试项目": name, "单位": _clean(r.get("单位")),
                         "方向": _clean(r.get("方向")) or "越大越好",
                         "类别": _clean(r.get("类别")) or "自定义",
                         "测试类别": "身体形态" if kind == "descriptive" else "体能",
                         "评分类型": kind, "是否评分": "是"})
    return pd.DataFrame(rows)


def next_id(df: pd.DataFrame, id_col: str) -> str:
    nums = pd.to_numeric(df[id_col], errors="coerce").dropna()
    return str(int(nums.max()) + 1) if not nums.empty else "1"


@with_data_lock
def append_row(kind: str, row: dict) -> str:
    """新增一行，自动生成编号。"""
    if kind == "test":
        row = _fill_sport([row])[0]
    path, cols, id_col = TABLES[kind]
    df = load_table(path, cols)
    data = {c: row.get(c, "") for c in cols}
    data[id_col] = next_id(df, id_col)
    save_table(pd.concat([df, pd.DataFrame([data])], ignore_index=True), path, cols)
    audit("append_row", target=kind, id=data[id_col], table=path.name)
    return data[id_col]


@with_data_lock
def append_rows(kind: str, rows: list[dict]) -> int:
    """一次写入多行（FMS 一次筛查会写 15 行），编号连续且只落盘一次。"""
    if not rows:
        return 0
    if kind == "test":
        rows = _fill_sport(rows)
    path, cols, id_col = TABLES[kind]
    df = load_table(path, cols)
    nums = pd.to_numeric(df[id_col], errors="coerce").dropna()
    start = int(nums.max()) if not nums.empty else 0
    payload = []
    for i, row in enumerate(rows, start=1):
        data = {c: row.get(c, "") for c in cols}
        data[id_col] = str(start + i)
        payload.append(data)
    save_table(pd.concat([df, pd.DataFrame(payload)], ignore_index=True), path, cols)
    audit("append_rows", target=kind, count=len(payload), table=path.name)
    return len(payload)


@with_data_lock
def update_row(kind: str, id_value: str, changes: dict) -> None:
    path, cols, id_col = TABLES[kind]
    df = load_table(path, cols)
    mask = df[id_col].astype(str) == str(id_value)
    for c, v in changes.items():
        if c in cols and c != id_col:
            df.loc[mask, c] = v
    save_table(df, path, cols)
    audit("update_row", target=kind, id=str(id_value), fields=list(changes))


@with_data_lock
def delete_rows(kind: str, ids) -> None:
    path, cols, id_col = TABLES[kind]
    df = load_table(path, cols)
    ids = {str(i) for i in ids}
    save_table(df[~df[id_col].astype(str).isin(ids)], path, cols)
    audit("delete_rows", target=kind, count=len(ids))


def athlete_map() -> dict[str, str]:
    """返回 {运动员ID: 姓名}。"""
    a = load_table(ATHLETE_FILE, ATHLETE_COLS)
    return {_clean(k): _clean(v) for k, v in zip(a["运动员ID"], a["姓名"]) if _clean(k)}


def with_names(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    names = athlete_map()
    out["姓名"] = out["运动员ID"].astype(str).map(names).fillna("（已删除）")
    return out[["姓名"] + [c for c in out.columns if c != "姓名"]]


# ---------------------------------------------------------------- 评分器
def score_level(score) -> str:
    if score is None:
        return "—"
    try:
        if pd.isna(score):
            return "—"
    except (TypeError, ValueError):
        return "—"
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "达标"
    return "待提升"


def lookup_standard(std: pd.DataFrame, item: str, group: str) -> dict | None:
    """按「测试项目 + 性别|年龄组」查常模；未配置 / 是否评分=否 返回 None。"""
    if std.empty or not group or "|" not in str(group):
        return None
    gender, ag = str(group).split("|", 1)
    hit = std[(std["测试项目"] == item) & (std["性别"] == gender) & (std["年龄组"] == ag)]
    if hit.empty:
        return None
    r = hit.iloc[0]
    if _clean(r.get("是否评分")) != "是" or _clean(r.get("评分类型")) != "continuous":
        return None
    p, e = r.get("达标线"), r.get("优秀线")
    if p is None or e is None or pd.isna(p) or pd.isna(e) or float(p) == float(e):
        return None
    return {"pass": float(p), "excellent": float(e),
            "direction": _clean(r.get("方向")) or "越大越好",
            "unit": _clean(r.get("单位"))}


def calc_score(item: str, value, group: str, std: pd.DataFrame | None = None) -> float | None:
    """评分器按「评分类型」分派：仅 continuous 线性换算 0～100 分。

    ordinal（FMS 0～3）、binary（排除性测试）、descriptive（身高/体重/BMI）
    均返回 None —— 绝不进入平均体能得分、达标率或 0～100 坐标轴。
    """
    if value is None or pd.isna(value):
        return None
    s = lookup_standard(load_standards() if std is None else std, item, group)
    if s is None:
        return None
    p, e = float(value), s["pass"]
    ratio = ((s["pass"] - p) / (s["pass"] - s["excellent"])
             if s["direction"] == "越小越好"
             else (p - s["pass"]) / (s["excellent"] - s["pass"]))
    return round(max(0.0, min(100.0, 60 + ratio * 40)), 1)


def is_positive(row) -> bool:
    """排除性测试是否阳性（兼容「阳性」文本与 1/0 数值两种写法）。"""
    if _clean(row.get("成绩")) == "阳性":
        return True
    if _clean(row.get("评分类型")) == "binary" and _clean(row.get("疼痛标记")) == "是":
        return True
    v = pd.to_numeric(row.get("成绩数值"), errors="coerce")
    return bool(pd.notna(v) and float(v) == 1.0)


def result_display(row) -> str:
    """把成绩显示成人类可读文本（派生量）。"""
    kind = _clean(row.get("评分类型"))
    if kind == "binary":
        return "阳性" if is_positive(row) else "通过"
    v = pd.to_numeric(row.get("成绩数值"), errors="coerce")
    if pd.isna(v):
        return _clean(row.get("成绩"))
    if kind == "ordinal":
        return f"{int(v)} 分"
    return f"{v:g} {_clean(row.get('单位'))}".strip()


def physical_scores(df: pd.DataFrame) -> pd.DataFrame:
    """只保留 continuous 且已评分的记录（FMS 永不被计入 0～100 体系）。"""
    if df.empty:
        return df
    return df[(df["评分类型"] == "continuous")
              & pd.to_numeric(df["得分"], errors="coerce").notna()].copy()


def load_tests() -> pd.DataFrame:
    """读取测试记录并派生全部得分 / 等级 / FMS 汇总字段（均不落盘）。"""
    df = load_table(TEST_FILE, TEST_COLS)
    df["运动员ID"] = df["运动员ID"].astype(str).str.strip()
    df["测试日期"] = pd.to_datetime(df["测试日期"], errors="coerce")
    df["成绩数值"] = pd.to_numeric(df["成绩"], errors="coerce").astype(float)
    if df.empty:
        # 空表直接补齐派生列返回，避免空 dtype 在向量化运算中冲突。
        for c in ("得分", "FMS总分"):
            df[c] = pd.Series(dtype=float)
        for c in ("等级", "结果显示", "结果说明", "不对称标记",
                  "评分类型", "测试类别", "标准组", "测试时年龄", "侧别",
                  "疼痛标记", "单位", "运动项目"):
            df[c] = pd.Series(dtype=str)
        return df

    catalog = item_catalog()
    meta = catalog.set_index("测试项目").to_dict("index") if not catalog.empty else {}
    athlete_idx = _athlete_index()
    std = load_standards()

    types, categories, groups, ages, sides, pain, units, sports = \
        [], [], [], [], [], [], [], []
    for _, r in df.iterrows():
        m = meta.get(_clean(r["测试项目"]), {})
        a = athlete_idx.get(str(r["运动员ID"]), ("", "", ""))
        parsed = r["测试日期"]
        ref = parsed.date() if pd.notna(parsed) else date.today()
        group = _clean(r["标准组"])
        if not group and a[0]:
            group = standard_group(a[0], a[1], ref)
        age = _clean(r["测试时年龄"])
        if not age and a[1]:
            computed = calc_age(a[1], ref)
            age = str(int(computed)) if computed is not None else ""
        types.append(_clean(r["评分类型"]) or m.get("评分类型", ""))
        categories.append(_clean(r["测试类别"]) or m.get("测试类别", "体能"))
        groups.append(group)
        ages.append(age)
        sides.append(_clean(r["侧别"]))
        pain.append(_clean(r["疼痛标记"]))
        units.append(_clean(r["单位"]) or m.get("单位", ""))
        sports.append(_clean(r["运动项目"]) or a[2] or SPORT_UNKNOWN)
    for col, values in (("评分类型", types), ("测试类别", categories), ("标准组", groups),
                        ("测试时年龄", ages), ("侧别", sides), ("疼痛标记", pain),
                        ("单位", units), ("运动项目", sports)):
        df[col] = values

    # 得分 / 等级 / 结果显示：一次性构建常模查表后向量化，避免逐行过滤 270 行标准表。
    usable = std[(std["是否评分"].astype(str) == "是")
                 & (std["评分类型"].astype(str) == "continuous")].copy()
    usable["达标线"] = pd.to_numeric(usable["达标线"], errors="coerce")
    usable["优秀线"] = pd.to_numeric(usable["优秀线"], errors="coerce")
    usable = usable.dropna(subset=["达标线", "优秀线"])
    usable = usable[usable["达标线"] != usable["优秀线"]]
    std_map = {
        (str(item), f"{gender}|{ag}"): (float(p_line), float(e_line), str(direction))
        for item, gender, ag, p_line, e_line, direction in zip(
            usable["测试项目"], usable["性别"], usable["年龄组"],
            usable["达标线"], usable["优秀线"], usable["方向"])
    }
    params = [std_map.get((item, group))
              for item, group in zip(df["测试项目"].astype(str), df["标准组"].astype(str))]
    pass_line = pd.Series([x[0] if x else float("nan") for x in params],
                          index=df.index, dtype=float)
    exc_line = pd.Series([x[1] if x else float("nan") for x in params],
                         index=df.index, dtype=float)
    smaller = pd.Series([bool(x and x[2] == "越小越好") for x in params],
                        index=df.index, dtype=bool)
    value = df["成绩数值"]
    denom_big = (exc_line - pass_line).replace(0, float("nan"))
    denom_small = (pass_line - exc_line).replace(0, float("nan"))
    ratio = ((value - pass_line) / denom_big).where(
        ~smaller, (pass_line - value) / denom_small)
    score = (60 + ratio * 40).clip(0, 100).round(1)
    score = score.where((df["评分类型"] == "continuous") & score.notna(), float("nan"))
    df["得分"] = score
    df["等级"] = (pd.cut(score, bins=[float("-inf"), 60, 75, 90, float("inf")],
                        labels=["待提升", "达标", "良好", "优秀"], right=False)
                  .astype(object).where(score.notna(), "—"))
    num_text = value.map(lambda v: "" if pd.isna(v) else f"{v:g}")
    display = (num_text + " " + df["单位"].astype(str)).str.strip()
    display = display.where(value.notna(), df["成绩"].astype(str))
    ordinal = df["评分类型"] == "ordinal"
    display = display.where(~ordinal,
                            value.map(lambda v: "" if pd.isna(v) else f"{int(v)} 分"))
    binary = df["评分类型"] == "binary"
    positive = ((df["成绩"].astype(str).str.strip() == "阳性")
                | (binary & (df["疼痛标记"].astype(str).str.strip() == "是"))
                | (value == 1.0))
    display = display.where(~binary, pd.Series(
        ["阳性" if x else "通过" for x in positive], index=df.index))
    df["结果显示"] = display

    sessions = fms_sessions(df)
    session_map = {(str(s["运动员ID"]), s["测试日期"]): s for _, s in sessions.iterrows()}
    fms_mask = (df["测试类别"] == "FMS").to_numpy()
    pairs = zip(df["运动员ID"].astype(str), df["测试日期"])
    totals, notes = [], []
    for is_fms, pair in zip(fms_mask, pairs):
        found = session_map.get(pair) if is_fms else None
        totals.append(float("nan") if found is None else found["FMS总分"])
        notes.append("" if found is None else found["结果说明"])
    df["FMS总分"] = totals
    df["结果说明"] = notes

    asym_rows = set()
    fms = df[df["测试类别"] == "FMS"]
    if not fms.empty:
        for _, g in fms.groupby(["运动员ID", "测试日期"], dropna=False):
            for name in FMS_BILATERAL:
                sub = g[g["测试项目"] == name]
                if sub.empty:
                    continue
                left = pd.to_numeric(sub[sub["侧别"] == "左"]["成绩数值"], errors="coerce").dropna()
                right = pd.to_numeric(sub[sub["侧别"] == "右"]["成绩数值"], errors="coerce").dropna()
                if not left.empty and not right.empty and abs(float(left.iloc[0]) - float(right.iloc[0])) >= 1:
                    asym_rows.update(sub.index.tolist())
    df["不对称标记"] = ["是" if i in asym_rows else "" for i in df.index]
    return df


def fms_sessions(tests: pd.DataFrame | None = None) -> pd.DataFrame:
    """按 (运动员ID, 测试日期) 汇总一次 FMS 筛查：0～21 总分、风险、疼痛、左右不对称。"""
    cols = ["运动员ID", "姓名", "测试日期", "FMS总分", "风险", "含疼痛", "疼痛警示",
            "结果说明", "不对称清单", "动作完整数", "各动作得分"]
    if tests is None:
        tests = load_tests()
    if tests.empty or "测试类别" not in tests.columns:
        return pd.DataFrame(columns=cols)
    fms = tests[tests["测试类别"] == "FMS"]
    if fms.empty:
        return pd.DataFrame(columns=cols)
    names = athlete_map()
    rows = []
    for (aid, day), g in fms.groupby(["运动员ID", "测试日期"], dropna=False):
        pain = False
        positives = set()
        for _, r in g.iterrows():
            if _clean(r.get("疼痛标记")) == "是":
                pain = True
            if _clean(r.get("评分类型")) == "binary" and is_positive(r):
                pain = True
                target = FMS_CLEARING.get(_clean(r.get("测试项目")))
                if target:
                    positives.add(target)
        scores, asym, detail = {}, [], []
        for name, bilateral, _ in FMS_MOVEMENTS:
            sub = g[g["测试项目"] == name]
            if sub.empty:
                continue
            vals = pd.to_numeric(sub["成绩数值"], errors="coerce").dropna()
            if vals.empty:
                continue
            if bilateral:
                left = pd.to_numeric(sub[sub["侧别"] == "左"]["成绩数值"], errors="coerce").dropna()
                right = pd.to_numeric(sub[sub["侧别"] == "右"]["成绩数值"], errors="coerce").dropna()
                lv = float(left.iloc[0]) if not left.empty else None
                rv = float(right.iloc[0]) if not right.empty else None
                candidates = [v for v in (lv, rv) if v is not None]
                if not candidates:
                    continue
                low = min(candidates)
                if lv is not None and rv is not None and abs(lv - rv) >= 1:
                    asym.append(f"{name.replace('FMS', '')}（左{int(lv)}/右{int(rv)}）")
                    detail.append(f"{name.replace('FMS', '')} {int(lv)}/{int(rv)}")
                else:
                    detail.append(f"{name.replace('FMS', '')} {int(low)}")
            else:
                low = float(vals.iloc[0])
                detail.append(f"{name.replace('FMS', '')} {int(low)}")
            if name in positives:
                low = 0.0
                pain = True
            if low == 0:
                pain = True
            scores[name] = int(low)
        total = int(sum(scores.values()))
        rows.append({
            "运动员ID": str(aid), "姓名": names.get(str(aid), "（已删除）"), "测试日期": day,
            "FMS总分": total,
            "风险": "较高风险" if total <= FMS_RISK_LINE else "一般",
            "含疼痛": "是" if pain else "否",
            "疼痛警示": FMS_PAIN_WARNING if pain else "",
            "结果说明": FMS_PAIN_NOTE if pain else "",
            "不对称清单": "；".join(asym),
            "动作完整数": len(scores),
            "各动作得分": "｜".join(detail),
        })
    return pd.DataFrame(rows, columns=cols)


def build_report(aid: str) -> pd.DataFrame:
    """个人成绩单（CSV）：一位运动员的全部测试与评估明细 + 派生量。"""
    tests = load_tests()
    sub = tests[tests["运动员ID"] == str(aid)].copy()
    if sub.empty:
        return pd.DataFrame(columns=REPORT_COLS)
    names, genders = athlete_map(), {}
    a = load_table(ATHLETE_FILE, ATHLETE_COLS)
    for _, r in a.iterrows():
        genders[_clean(r["运动员ID"])] = _clean(r["性别"])
    sub["姓名"] = names.get(str(aid), "（已删除）")
    sub["性别"] = genders.get(str(aid), "")
    sub["测试日期"] = pd.to_datetime(sub["测试日期"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    out = sub.reindex(columns=REPORT_COLS).fillna("")
    return out.sort_values("测试日期")


def csv_download(df: pd.DataFrame, filename: str, label: str = "下载 CSV 表格") -> None:
    text = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(label, text, file_name=filename, mime="text/csv", width="stretch")


# ---------------------------------------------------------------- FMS 录入
def build_fms_rows(aid: str, day, scores: dict, clearing: dict, pain: set,
                   note: str = "") -> list[dict]:
    """把一次 FMS 筛查矩阵展开成待写入 tests.csv 的 15 行。

    scores: {动作: (左, 右)} 或 {动作: (分值,)}；clearing: {排除测试: "通过"/"阳性"}；
    pain: 勾选了疼痛的动作集合。阳性排除测试使对应动作记 0 分。
    """
    gender, birth, sport = _athlete_index().get(str(aid), ("", "", ""))
    parsed = pd.to_datetime(day, errors="coerce")
    ref = parsed.date() if pd.notna(parsed) else date.today()
    age = calc_age(birth, ref) if birth else None
    group = standard_group(gender, birth, ref) if gender else ""
    positives = {FMS_CLEARING[k] for k, v in clearing.items() if _clean(v) == "阳性"}
    base = {
        "运动员ID": str(aid), "测试日期": pd.to_datetime(day).strftime("%Y-%m-%d"),
        "测试类别": "FMS", "标准组": group, "运动项目": sport,
        "测试时年龄": str(int(age)) if age is not None else "", "备注": note,
    }
    rows = []
    for name, bilateral, _ in FMS_MOVEMENTS:
        pair = scores.get(name, ())
        values = (("左", pair[0]), ("右", pair[1])) if bilateral and len(pair) >= 2 else (("", pair[0] if pair else None),)
        for side, value in values:
            if value is None:
                continue
            forced = name in positives
            v = 0 if (forced or name in pain) else int(value)
            flagged = name in pain or v == 0 or forced
            rows.append({**base, "测试项目": name, "评分类型": "ordinal", "侧别": side,
                         "成绩": v, "单位": "分", "疼痛标记": "是" if flagged else ""})
    for name in FMS_CLEARING:
        result = _clean(clearing.get(name)) or "通过"
        rows.append({**base, "测试项目": name, "评分类型": "binary", "侧别": "",
                     "成绩": result, "单位": "",
                     "疼痛标记": "是" if result == "阳性" else ""})
    return rows


# ---------------------------------------------------------------- 演示数据
@with_data_lock
def seed_demo_data() -> None:
    """重做演示数据：保留原 192 条旧记录的结构与项目名，追加新项目、描述项与 FMS 筛查。"""
    if not ALLOW_DEMO_DATA:
        raise DataFileError("演示数据已在生产配置中禁用（EFA_ALLOW_DEMO_DATA=0）。")
    rnd = random.Random(42)
    today = date.today()
    athletes = [
        ("林扬", "男", "2000-04-12", "短跑组", "100米"),
        ("陈曦", "女", "2001-08-03", "短跑组", "200米"),
        ("周锐", "男", "1999-11-25", "中长跑组", "1500米"),
        ("赵一诺", "女", "2002-02-17", "中长跑组", "800米"),
        ("孙浩然", "男", "1998-06-09", "投掷组", "铅球"),
        ("吴梦", "女", "2001-12-30", "跳跃组", "跳远"),
        ("何嘉树", "男", "2010-05-06", "短跑组", "200米"),
        ("苏晴", "女", "2011-03-18", "跳跃组", "跳高"),
        ("郑逸", "男", "1988-09-01", "中长跑组", "马拉松"),
        ("罗敏", "女", "2007-06-20", "投掷组", "标枪"),
    ]
    rows_a = [{"运动员ID": str(i), "姓名": name, "性别": gender, "出生日期": birth,
               "所属队伍": team, "专项": event, "备注": "演示数据",
               "运动项目": sport_from_event(event, team)}
              for i, (name, gender, birth, team, event) in enumerate(athletes, start=1)]
    save_table(pd.DataFrame(rows_a), ATHLETE_FILE, ATHLETE_COLS)

    rows_t = []
    tid = 1
    for a in rows_a[:6]:  # 与原演示数据完全一致的 192 条旧记录
        gender, birth = a["性别"], a["出生日期"]
        for k in range(4):
            day = today - timedelta(days=90 - 30 * k)
            ref = day
            group = standard_group(gender, birth, ref)
            age = calc_age(birth, ref)
            for item in LEGACY_ITEMS:
                if item in ("体脂率", "静息心率") and k % 2 == 1:
                    continue
                s = CONTINUOUS_ITEMS[item]
                progress = k / 3
                base = max(-0.25, min(1.15, 0.12 + 0.62 * progress + rnd.uniform(-0.2, 0.2)))
                value = s["pass"] + (s["excellent"] - s["pass"]) * base
                rows_t.append({"记录ID": str(tid), "运动员ID": a["运动员ID"],
                               "测试日期": day.isoformat(), "测试项目": item,
                               "成绩": round(value, 1), "单位": s["unit"], "备注": "演示数据",
                               "测试类别": "体能", "评分类型": "continuous", "侧别": "",
                               "标准组": group, "测试时年龄": str(age), "疼痛标记": "",
                               "运动项目": a["运动项目"]})
                tid += 1

    def add(aid, day, item, meta, value, category, kind, note="演示数据"):
        nonlocal tid
        birth = rows_a[int(aid) - 1]["出生日期"]
        gender = rows_a[int(aid) - 1]["性别"]
        rows_t.append({"记录ID": str(tid), "运动员ID": aid, "测试日期": day.isoformat(),
                       "测试项目": item, "成绩": value, "单位": meta.get("unit", ""),
                       "备注": note, "测试类别": category, "评分类型": kind, "侧别": "",
                       "标准组": standard_group(gender, birth, day),
                       "测试时年龄": calc_age(birth, day), "疼痛标记": "",
                       "运动项目": rows_a[int(aid) - 1]["运动项目"]})
        tid += 1

    for a in rows_a:
        for item in ("10米起跑", "立定三级跳远", "握力", "T型灵敏测试", "坐位体前屈"):
            s = CONTINUOUS_ITEMS[item]
            factor = 0.8 + rnd.uniform(-0.12, 0.28)
            value = (s["pass"] / factor if s["direction"] == "越小越好" else s["pass"] * factor)
            add(a["运动员ID"], today, item, s, round(value, 1), "体能", "continuous")
        for item, meta in DESCRIPTIVE_ITEMS.items():
            value = {"身高": 165 + rnd.uniform(-12, 18), "体重": 60 + rnd.uniform(-15, 20),
                     "BMI": 21 + rnd.uniform(-2, 3), "腰臀比": 0.8 + rnd.uniform(-0.06, 0.08)}[item]
            add(a["运动员ID"], today, item, meta, round(value, 2), "身体形态", "descriptive")

    for a in rows_a:
        for offset in (45, 0):
            day = today - timedelta(days=offset)
            scores = {}
            for name, bilateral, _ in FMS_MOVEMENTS:
                if bilateral:
                    lv = rnd.choice([1, 2, 2, 3])
                    rv = lv if rnd.random() < 0.6 else max(0, min(3, lv + rnd.choice([-1, 1])))
                    scores[name] = (lv, rv)
                else:
                    scores[name] = (rnd.choice([1, 2, 2, 3]),)
            clearing = {name: "通过" for name in FMS_CLEARING}
            pain = set()
            if a["运动员ID"] in ("3", "8") and offset == 0:
                scores["FMS主动直腿抬高"] = (0, 2)   # 0 分 + 左右不对称
                pain.add("FMS主动直腿抬高")
            if a["运动员ID"] in ("5", "9") and offset == 0:
                clearing["FMS肩部排除测试"] = "阳性"  # 阳性 → 肩部灵活性记 0
                scores["FMS深蹲"] = (1,)
                scores["FMS躯干稳定俯卧撑"] = (1,)
            for row in build_fms_rows(a["运动员ID"], day, scores, clearing, pain, "演示数据"):
                row["记录ID"] = str(tid)
                rows_t.append(row)
                tid += 1

    save_table(pd.DataFrame(rows_t), TEST_FILE, TEST_COLS)
    if load_standards().empty:
        save_standards(generate_default_standards())
    audit("seed_demo_data", target="demo", rows=len(rows_t), athletes=len(rows_a))


@with_data_lock
def clear_all_data() -> None:
    """清空运动员与测试与评估记录；评分标准属于配置，予以保留。"""
    if not db_mode_enabled():
        create_backup("before_clear")
    for kind in ("athlete", "test"):
        path, cols, _ = TABLES[kind]
        save_table(pd.DataFrame(columns=cols), path, cols)
    audit("clear_all_data", target="athlete,test")


# ---------------------------------------------------------------- 界面样式
def require_access_gate() -> None:
    """访问闸口，优先级：DB 注册登录 > 演示多账号 > 单口令 > 放行。

    - EFA_USE_DB=1：数据库注册/登录，用户自助注册、数据按用户隔离（MVP）。
    - demo_users.json 存在：演示多账号登录。
    - EFA_ACCESS_PASSWORD 非空：单口令（旧行为）。
    - 都未配置：不拦截，兼容个人自用 / 测试。

    所有页面经 inject_css() 自动受保护。
    """
    import json
    import os

    if st.session_state.get("efa_user") or st.session_state.get("_efa_user"):
        # DB 模式下每次 rerun 确保当前用户数据已初始化。
        if db_mode_enabled():
            ensure_files()
        _render_user_badge()
        return

    if db_mode_enabled():
        _db_auth_gate()
        return

    users_file = Path(os.getenv("EFA_DEMO_USERS", str(BASE_DIR / "demo_users.json")))
    accounts = []
    if users_file.exists():
        try:
            data = json.loads(users_file.read_text(encoding="utf-8"))
            accounts = [a for a in data.get("users", [])
                        if a.get("username") and a.get("password")]
        except Exception:
            accounts = []

    if not accounts:
        pw = (os.environ.get("EFA_ACCESS_PASSWORD") or "").strip()
        if not pw:
            return
        st.title("🔐 访问验证")
        st.info("本系统包含运动员个人体能数据，请输入访问口令后进入。")
        with st.form("efa_pw_gate"):
            val = st.text_input("访问口令", type="password")
            if st.form_submit_button("进入系统", type="primary"):
                if val == pw:
                    st.session_state["_efa_user"] = {"name": "用户", "role": "用户"}
                    st.rerun()
                st.error("口令错误，请重试。")
        st.stop()

    st.title("🔐 登录 · 运动员体能与功能评估系统")
    st.caption("请选择账号登录。演示账号仅用于产品试用，正式版将接入数据库账户。")
    with st.form("efa_login_gate"):
        col1, col2 = st.columns(2)
        with col1:
            username = st.text_input("用户名")
        with col2:
            password = st.text_input("密码", type="password")
        if st.form_submit_button("登录", type="primary"):
            hit = next((a for a in accounts
                        if a["username"] == username and a["password"] == password), None)
            if hit:
                st.session_state["_efa_user"] = {
                    "name": hit.get("name", hit["username"]),
                    "role": hit.get("role", "用户"),
                }
                st.rerun()
            st.error("用户名或密码错误。")
    st.stop()


def _db_auth_gate() -> None:
    """数据库模式：用户自助注册 / 登录。"""
    import auth
    import db as _db
    _db.init_db()
    st.title("🔐 运动员体能与功能评估系统")
    st.caption("免费试用版 · 注册后即可使用，数据按账号独立保存。")
    tab_login, tab_reg = st.tabs(["登录", "注册新账号"])
    with tab_login:
        with st.form("db_login"):
            u = st.text_input("用户名", key="db_login_u")
            p = st.text_input("密码", type="password", key="db_login_p")
            if st.form_submit_button("登录", type="primary"):
                ok, info = auth.login(u, p)
                if ok:
                    auth.sign_in(st, info)
                    st.rerun()
                else:
                    st.error(info)
    with tab_reg:
        with st.form("db_register"):
            nu = st.text_input("用户名（3-20 位字母数字下划线）", key="db_reg_u")
            em = st.text_input("邮箱（选填）", key="db_reg_e")
            np = st.text_input("密码（至少 6 位）", type="password", key="db_reg_p")
            cp = st.text_input("确认密码", type="password", key="db_reg_c")
            if st.form_submit_button("注册", type="primary"):
                if np != cp:
                    st.error("两次输入的密码不一致。")
                else:
                    ok, msg = auth.register(nu, np, em)
                    (st.success(msg) if ok else st.error(msg))
    st.stop()


def _render_user_badge() -> None:
    u = (st.session_state.get("efa_user")
         or st.session_state.get("_efa_user") or {})
    name = u.get("username") or u.get("name", "用户")
    role = u.get("role", "用户")
    st.sidebar.markdown("---")
    st.sidebar.caption(f"当前账号：**{name}**（{role}）")
    if st.sidebar.button("退出登录", key="efa_logout"):
        st.session_state.pop("efa_user", None)
        st.session_state.pop("_efa_user", None)
        st.rerun()


def inject_css() -> None:
    install_error_hooks()
    st.markdown("""
    <style>
        .main .block-container {padding-top: 1.8rem; max-width: 1400px;}

        /* ---- 页头 hero：图标与标题同行对齐 ---- */
        .hero {
            background: linear-gradient(115deg, #12305c 0%, #1e5b98 55%, #18a58b 100%);
            border-radius: 18px; padding: 24px 30px; color: #fff; margin-bottom: 22px;
            box-shadow: 0 8px 24px rgba(18,48,92,.18);
        }
        .hero .tag {display:inline-block; background:rgba(255,255,255,.18); border-radius:20px;
                    padding:3px 12px; font-size:12px; margin-bottom:12px; letter-spacing:.5px;}
        .hero h1 {display:flex; align-items:center; gap:10px; color:#fff; font-size:28px;
                  margin:0 0 8px 0; letter-spacing:1px; line-height:1.2;}
        .hero p {margin:0; opacity:.9; font-size:15px; line-height:1.6;}

        /* ---- 标题层级与分段 ---- */
        h2 {font-size:19px; font-weight:600; color:#12305c; margin:24px 0 10px;
            padding-left:10px; border-left:4px solid #1e5b98;}
        h3 {font-size:16px; font-weight:600; color:#1e5b98; margin:16px 0 8px;}
        .stMarkdown hr, [data-testid="stDivider"] {border:none; border-top:1px solid #e6ecf3; margin:18px 0;}
        .stCaption, [data-testid="stCaption"] {color:#5b6b80; font-size:13px; line-height:1.7;}

        /* ---- 卡片与指标 ---- */
        div[data-testid="stMetric"] {
            background:#fff; border:1px solid #e6ecf3; border-radius:14px;
            padding:14px 16px; box-shadow:0 3px 12px rgba(20,50,90,.06);
        }
        /* 窄窗口下 5 列指标卡不再被省略号截断 */
        [data-testid="stMetricValue"] {font-size:1.8rem; line-height:1.3;}
        [data-testid="stMetricLabel"] {white-space:normal; line-height:1.35;}
        .card {background:#fff; border:1px solid #e6ecf3; border-radius:14px;
               padding:18px 20px; box-shadow:0 3px 12px rgba(20,50,90,.06); margin-bottom:16px;}
        .card.warn {border-left:5px solid #d9534f; background:#fff6f5;}
        .card.note {border-left:5px solid #e08b2c; background:#fffaf2;}
        .hint {color:#5b6b80; font-size:14px; line-height:1.7;}

        /* ---- 表格：统一外框与 Markdown 表格样式 ---- */
        [data-testid="stDataFrame"], [data-testid="stTable"] {
            border:1px solid #e6ecf3; border-radius:12px; overflow:hidden; background:#fff;
        }
        [data-testid="stDataFrame"] table, [data-testid="stTable"] table, table.dataframe {
            border-collapse:collapse; width:100%; font-size:13px;
        }
        [data-testid="stDataFrame"] th, [data-testid="stTable"] th, table.dataframe th {
            background:#f2f6fb; color:#12305c; font-weight:600; text-align:left;
            padding:8px 12px; border:1px solid #e6ecf3; white-space:nowrap;
        }
        [data-testid="stDataFrame"] td, [data-testid="stTable"] td, table.dataframe td {
            padding:7px 12px; border:1px solid #e6ecf3; vertical-align:middle;
            white-space:normal; word-break:break-word;
        }
        [data-testid="stDataFrame"] tbody tr:nth-child(even),
        [data-testid="stTable"] tbody tr:nth-child(even),
        table.dataframe tbody tr:nth-child(even) {background:#fafcff;}
        [data-testid="stDataFrame"] tbody tr:hover,
        [data-testid="stTable"] tbody tr:hover {background:#eef4fb;}

        /* ---- 标签页 ---- */
        .stTabs [data-baseweb="tab-list"] {gap:8px;}
        .stTabs [data-baseweb="tab"] {background:#f2f6fb; border-radius:10px; padding:8px 16px;}

        /* ---- 侧边栏 ---- */
        section[data-testid="stSidebar"] {background:#f7fafd;}
        /* 首页入口：保留布局与点击行为，仅把显示文字改为带图标的「🏅 主页」 */
        nav[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"] span[label="app"]
            > div[data-testid="stMarkdownContainer"] {display:none;}
        nav[data-testid="stSidebarNav"] a[data-testid="stSidebarNavLink"] span[label="app"]::before {
            content:"🏅 主页"; visibility:visible; white-space:nowrap; pointer-events:none;
        }
    </style>
    """, unsafe_allow_html=True)
    require_access_gate()


def page_header(title: str, subtitle: str, icon: str = "") -> None:
    st.markdown(
        f'<div class="hero"><span class="tag">运动员体能与功能评估系统</span>'
        f'<h1>{escape_html(icon)} {escape_html(title)}</h1><p>{escape_html(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    st.sidebar.markdown("---")
    st.sidebar.markdown("**数据概览**")
    st.sidebar.write(f"运动员：{len(load_athletes())} 人　｜　测试与评估记录：{len(load_tests())} 条")
    configured = load_standards()
    configured = int((configured["是否评分"] == "是").sum()) if not configured.empty else 0
    st.sidebar.write(f"已配置评分标准：{configured} 组")
    if db_mode_enabled():
        st.sidebar.caption("数据按账号独立保存在云端数据库，换设备登录后自动同步。")
    else:
        st.sidebar.caption("数据保存在项目 `data` 文件夹的 CSV 中，可直接用 Excel 打开、复制备份。")
    st.sidebar.markdown("---")
    st.sidebar.caption(f"系统 {APP_VERSION} ｜ 常模：{STANDARDS_VERSION}")
    st.sidebar.caption("Python + Streamlit · 云端账号独立存储" if db_mode_enabled()
                      else "Python + Streamlit · 本地运行，数据不上传")


def empty_state(message: str = "目前还没有数据。") -> None:
    if ALLOW_DEMO_DATA:
        st.info(message + " 可以点击下方按钮载入一套演示数据，先看看系统能做什么。")
        if st.button("一键载入演示数据", type="primary"):
            seed_demo_data()
            st.success("演示数据已载入！")
            st.rerun()
    else:
        st.info(message + " 请到「运动员管理」建档，或由运维从备份恢复正式数据"
               "（见《部署与运维手册》）。生产配置已禁用演示数据。")
    st.stop()


def plotly_font(fig):
    fig.update_layout(font=dict(family="Microsoft YaHei, SimHei, Arial", size=13),
                      paper_bgcolor="white", plot_bgcolor="white",
                      margin=dict(l=20, r=20, t=50, b=20))
    return fig
