# -*- coding: utf-8 -*-
"""每日汇总报告：把板块层（Step 3/4）与个股层（Step 5/6）合成一份 Markdown 日报。

设计约束：
  - 只读已有产物（data/*.csv 与 output/*.json），不重算、不修改任何 Step 1–6 逻辑。
  - 板块层与个股层日期可能不一致（两层各自独立运行），报告中分段标注，不混算。
  - 只输出结构 / 状态 / 资格描述，不输出 BUY / NO TRADE / 仓位 / 买卖点。

用法：
    python daily_report_build.py                    # 自动取各层最新日期
    python daily_report_build.py --date 20260917    # 指定板块层日期
    python daily_report_build.py --trend-days 10    # 趋势回看天数（默认 5）
    python daily_report_build.py --print            # 只打印到终端，不写文件
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

TREND_DAYS_DEFAULT = 5
DIVERGENCE_DROP = -10.0     # 量价背离判定：健康度 5 日跌幅阈值
DIVERGENCE_DROP_1 = -20.0   # 量价背离判定：健康度单日跌幅阈值
DIVERGENCE_TOP_N = 10       # 只在前 N 大成交额占比板块中找背离
TOP_N = 10
STOCK_TOP_N = 5             # 每个主题展示的候选股数量
THEME_TOP_N = 6             # 第五节展示的优先主题数量（按主题机会分排序）
POOL_TOP_N = 30             # 6.4 STRUCTURE_POOL 明细展示数量（按结构质量排序）

STATE_CN = {
    "STRONG": "强势", "HEALTHY": "健康", "NEUTRAL": "中性",
    "WEAK": "走弱", "DETERIORATING": "恶化", "DATA_INVALID": "数据异常",
}
PHASE_CN = {
    "DORMANT": "沉寂", "EARLY": "萌芽", "EMERGING": "发酵", "CONFIRMING": "确认",
    "STRONG": "主升", "COOLING": "降温", "DETERIORATING": "退潮",
    "EXITING": "退出", "DATA_INVALID": "数据异常",
}
ROTATION_CN = {"ROTATION_IN": "轮入", "ROTATION_OUT": "轮出", "ROTATION_NEUTRAL": "中性"}
GROUP_STATE_CN = {
    "STRONG": "强势", "IMPROVING": "转好", "NEUTRAL": "中性",
    "WEAKENING": "转弱", "DATA_INVALID": "数据异常",
}
QUAL_CN = {
    "QUALIFIED": "结构合格", "CONDITIONAL": "有条件合格",
    "WATCH": "观察", "REJECTED": "不合格",
}

# ── 板块层 Step 3/4 状态枚举 ────────────────────────────────────────────
CORE_PATTERN_CN = {
    "A_HEALTHY_DIFFUSION": "健康扩散", "B_EDGE_DRIVEN": "边缘股驱动",
    "C_EARLY_CORE_ONLY": "仅核心改善", "D_MIXED": "混合",
    "INSUFFICIENT_DATA": "数据不足",
}
STARTUP_QUALITY_CN = {
    "HEALTHY_EXPANSION": "健康扩散", "NARROW_LEADERSHIP": "龙头独走",
    "VOLUME_SPIKE": "放量脉冲", "FAILED_EXPANSION": "扩散失败",
    "NEUTRAL": "中性", "INSUFFICIENT_DATA": "数据不足",
}
FLAG_CN = {
    "DATA_INVALID": "数据异常", "DATA_INCOMPLETE": "数据不完整",
    "LOW_COVERAGE": "覆盖不足", "HIGH_CONCENTRATION": "高集中度",
    "LOW_BREADTH": "广度不足", "CORE_WEAK": "核心走弱",
    "MEMBERSHIP_UNSTABLE": "成员不稳定",
    "FAILED_EXPANSION": "扩散失败", "HIGH_EXTENSION": "过度延伸",
    "HIGH_CROWDING": "高拥挤", "VOLUME_SPIKE": "放量脉冲",
    "LEADER_CLIMAX": "龙头冲刺", "LOW_BREADTH_SUPPORT": "广度支撑不足",
    "WEAK_CORE_SUPPORT": "核心支撑偏弱", "THEME_COOLING": "主题降温",
    "THEME_DIVERGENCE": "主题背离",
    "LOW_MEMBERSHIP_CONFIDENCE": "成员置信度低",
    "FUNDAMENTAL_WEAK": "基本面偏弱",
}

# ── 个股层 Step 5 主题与候选股枚举 ──────────────────────────────────────
BUCKET_CN = {
    "EARLY_WATCH": "萌芽观察", "EMERGING_WATCH": "发酵观察",
    "CONFIRMING_WATCH": "确认观察", "STRONG_CONTINUE": "主升延续",
    "COOLING_WATCH": "降温观察", "RISK_WATCH": "风险观察", "NONE": "无",
}
DIFFUSION_TYPE_CN = {
    "CORE_EXPANSION": "核心扩散", "PRIMARY_EXPANSION": "主要成员扩散",
    "SECOND_LINE_EXPANSION": "二线成员扩散",
    "FULL_BREADTH_EXPANSION": "全面扩散", "NO_DIFFUSION": "未扩散",
}
THEME_ROLE_CN = {
    "CORE_LEADER": "核心龙头", "CORE_SUPPLIER": "核心供应商",
    "CORE_EQUIPMENT": "核心设备", "CORE_MATERIAL": "核心材料",
    "CORE_COMPONENT": "核心零部件", "CORE_APPLICATION": "核心应用",
    "INFRASTRUCTURE": "基础设施", "SERVICE": "配套服务",
    "SECOND_LINE": "二线成员", "CROSS_THEME": "跨主题成员",
    "CONCEPT_ONLY": "仅概念", "UNKNOWN": "未明确",
}
MEMBERSHIP_TYPE_CN = {
    "CORE": "核心成员", "PRIMARY": "主要成员", "SECOND_LINE": "二线成员",
    "THEMATIC": "主题成员", "OBSERVATION": "观察成员", "NONE": "非成员",
}
CANDIDATE_TYPE_CN = {
    "THEME_CORE": "主题核心", "THEME_DIFFUSION": "主题扩散",
    "THEME_SECOND_LINE": "主题二线", "THEME_LEADER": "主题龙头",
    "THEME_RELATIVE_STRENGTH": "主题相对强度", "THEME_PULLBACK": "主题回踩",
    "THEME_WATCH": "主题观察", "EXCLUDE": "剔除",
}
CANDIDATE_STATUS_CN = {"CANDIDATE": "候选", "WATCH": "观察", "EXCLUDE": "剔除"}
CROWDING_CLASS_CN = {"LOW": "低", "MEDIUM": "中", "HIGH": "高", "EXTREME": "极高"}

# ── 个股层 Step 6 结构 / HVT 枚举（HVT = 天量换手）──────────────────────
STRUCTURE_STATE_CN = {
    "BASE": "筑底", "HVT": "天量事件态", "ADJUSTING": "调整", "LOCKING": "锁定",
    "BREAKOUT_READY": "待突破", "BREAKOUT": "突破", "RETEST": "回踩",
    "REBREAKOUT": "再突破", "EXTENDED": "过度延伸", "FAILED": "失败",
    "WATCH": "观察", "INVALID": "数据无效",
}
STRUCTURE_CLASS_CN = {
    "STRUCTURE_BASE": "结构筑底", "HVT_EVENT": "天量事件",
    "HVT_ADJUSTING": "天量后调整", "HVT_LOCKING": "天量后锁定",
    "BREAKOUT_READY": "待突破", "BREAKOUT_CONFIRMED": "突破已确认",
    "RETEST_PENDING": "回踩待确认", "RETEST_SUCCESS": "回踩确认",
    "REBREAKOUT_CONFIRMED": "再突破已确认", "PULLBACK_HEALTHY": "健康回踩",
    "EXTENDED": "过度延伸", "FAILED": "失败", "WATCH": "观察",
    "INVALID": "数据无效",
}
HVT_STATE_CN = {
    "HVT_NONE": "无天量事件", "HVT_EVENT": "事件登记", "HVT_ADJUSTING": "调整",
    "HVT_LOCKING": "锁定", "HVT_REBREAKOUT": "再突破", "HVT_RETEST": "回踩",
    "HVT_FAILED": "失败",
}
HVT_STAGE_CN = {
    "HVT_NONE": "无天量事件", "HVT_EVENT": "事件登记", "HVT_ADJUSTING": "调整",
    "HVT_CONSOLIDATING": "整固", "HVT_REACCUMULATION": "再蓄势",
    "HVT_REBREAKOUT": "再突破", "HVT_FAILED": "失败",
}
BREAKOUT_STATE_CN = {
    "NO_BREAKOUT": "未突破", "BREAKOUT_READY": "待突破",
    "BREAKOUT_CONFIRMED": "突破已确认", "REBREAKOUT_CONFIRMED": "再突破已确认",
    "BREAKOUT_FAILED": "突破失败",
}
RETEST_STATE_CN = {
    "NO_RETEST": "无回踩", "RETEST_PENDING": "回踩待确认",
    "RETEST_SUCCESS": "回踩确认", "RETEST_FAILED": "回踩失败",
}
EXT_RISK_CN = {"LOW": "低", "MEDIUM": "中", "HIGH": "高", "EXTREME": "极高"}
MEMBERSHIP_STATUS_CN = {
    "VERSIONED": "已版本化", "STATIC_ONLY": "仅静态快照", "PARTIAL": "部分可用",
}
POOL_CN = {
    "STRUCTURE_POOL": "结构池", "HVT_POOL": "天量换手池",
    "REBREAKOUT_POOL": "再突破池", "RETEST_POOL": "回踩确认池",
}
# Step 6 产物 reason 文案里的补充枚举
ADJUSTMENT_CN = {
    "NO_ADJUSTMENT": "未调整", "SHALLOW_ADJUSTMENT": "浅调",
    "HEALTHY_ADJUSTMENT": "调整充分", "DEEP_ADJUSTMENT": "深度调整",
    "FAILED_ADJUSTMENT": "调整失败",
}
PLATFORM_QUALITY_CN = {
    "PLATFORM_STRONG": "平台强势", "PLATFORM_HEALTHY": "平台健康",
    "PLATFORM_WEAK": "平台偏弱", "NO_PLATFORM": "无平台",
}
SUPPORT_BAND_CN = {
    "above_s1": "支撑1上方", "above_s1_near": "贴近支撑1",
    "above_s2": "支撑2上方", "below": "支撑下方",
}

# ── 指标 / 字段名 ──────────────────────────────────────────────────────
FIELD_CN = {
    "breadth": "广度", "breadth_delta_5": "广度Δ5",
    "breadth_expansion_5": "广度扩张Δ5", "breadth_acceleration": "广度加速度",
    "core_breadth": "核心广度", "core_breadth_delta_5": "核心广度Δ5",
    "primary_breadth_delta_3": "主要成员广度Δ3",
    "secondary_breadth_delta_5": "二线成员广度Δ5",
    "theme_ret_1": "主题当日收益", "theme_ret_5": "主题5日收益",
    "top5_concentration": "Top5集中度",
    "ref_breadth": "参照窗广度", "ref_breadth_delta": "参照窗广度变化",
    "rs_turn_5": "相对强度拐点Δ5",
    "amount_share_delta_5": "成交额占比Δ5",
    "extension_penalty": "延伸惩罚",
    "volume_ratio_5": "量比Δ5", "relative_strength": "相对强度",
}
METRIC_CN = {
    "Breadth": "广度", "Core Breadth": "核心广度", "Core": "核心",
    "RS turn": "相对强度拐点", "extension_penalty": "延伸惩罚",
}

# ── 自由文本里的英文状态词表（用于译文枚举混排的 reason / 依据文案）────────
_TEXT_CN: dict = {}
for _m in (STRUCTURE_STATE_CN, STRUCTURE_CLASS_CN, HVT_STATE_CN, HVT_STAGE_CN,
           BREAKOUT_STATE_CN, RETEST_STATE_CN, EXT_RISK_CN,
           ADJUSTMENT_CN, PLATFORM_QUALITY_CN, SUPPORT_BAND_CN,
           PHASE_CN, STATE_CN, QUAL_CN, MEMBERSHIP_TYPE_CN, THEME_ROLE_CN,
           CANDIDATE_TYPE_CN, CANDIDATE_STATUS_CN, CROWDING_CLASS_CN,
           MEMBERSHIP_STATUS_CN, BUCKET_CN, DIFFUSION_TYPE_CN, FLAG_CN, METRIC_CN):
    _TEXT_CN.update(_m)
_TEXT_CN.update({
    "HVT": "天量换手", "confidence": "置信度",
    "CORE成员": "核心成员", "PRIMARY成员": "主要成员",
    "SECOND_LINE成员": "二线成员", "THEMATIC成员": "主题成员",
    "OBSERVATION成员": "观察成员",
})
_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    + "|".join(sorted((re.escape(k) for k in _TEXT_CN), key=len, reverse=True))
    + r")(?![A-Za-z0-9_])")
# Step 6 reason 的「标签 值」结构：补中文冒号，避免标签与值连读
_LABEL_RE = re.compile(
    r"(结构状态|结构分类|当前\s*天量换手\s*状态|调整充分度|支撑位置|突破状态|回踩状态|扩张风险|结构质量)\s+(?=\S)")


# ════════════════════════════════════════════════════════════════════════
# 基础工具
# ════════════════════════════════════════════════════════════════════════
def _read_csv(name: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).str.strip()
    return df


def _read_json(name: str) -> dict:
    path = os.path.join(OUTPUT_DIR, name)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _num(series):
    return pd.to_numeric(series, errors="coerce")


def _f(v, nd: int = 2) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if x != x:
        return "-"
    return f"{x:,.{nd}f}"


def _pct(v, nd: int = 2) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if x != x:
        return "-"
    return f"{x * 100:+.{nd}f}%"


def _pct0(v, nd: int = 2) -> str:
    """不带正负号的百分比。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if x != x:
        return "-"
    return f"{x * 100:.{nd}f}%"


def _cn(mapping: dict, key) -> str:
    """状态枚举取中文；未收录时回落原值，保证不丢信息。"""
    k = "" if key is None else str(key)
    if not k:
        return "-"
    return mapping.get(k, k)


def _cn_seq(mapping: dict, key) -> str:
    """把 `A->B` 形式的迁移串逐段译为中文。"""
    k = "" if key is None else str(key)
    if not k:
        return "-"
    return "→".join(mapping.get(p, p) for p in k.split("->"))


def _cn_text(text) -> str:
    """把自由文本（理由 / 依据）里的英文状态枚举按词边界译成中文。"""
    s = "" if text is None else str(text)
    if not s:
        return "-"
    s = _TEXT_RE.sub(lambda m: _TEXT_CN[m.group(1)], s)
    s = _LABEL_RE.sub(r"\1：", s)
    # 译文后中文字符之间的多余空格（原文里英文词两侧的分隔）一并收掉
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)


def _md_table(headers: list, rows: list) -> str:
    def cell(v):
        return str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(cell(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(cell(v) for v in r) + " |")
    return "\n".join(out) + "\n"


def _bullet(items: list) -> str:
    return "\n".join(f"- {i}" for i in items) + "\n" if items else "（无）\n"


# ════════════════════════════════════════════════════════════════════════
# 板块层数据装配
# ════════════════════════════════════════════════════════════════════════
class BoardData:
    """板块层（Step 3 / Step 4）截面与历史的统一容器。"""

    def __init__(self, date: str | None):
        self.state = _read_csv("sector_state_history.csv")
        self.seos = _read_csv("sector_seos_daily.csv")
        self.stats = _read_csv("sector_daily_stats.csv")
        self.strength = _read_csv("sector_strength.csv")
        self.rotation = _read_csv("sector_rotation.csv")
        self.state_json = _read_json("sector_state_today.json")
        self.seos_json = _read_json("sector_seos_today.json")
        self.pool_json = _read_json("sector_opportunity_pool.json")
        self.date = date or self._auto_date()
        self.dates = sorted(self.seos["trade_date"].unique()) if not self.seos.empty else []
        self._merge()

    def _auto_date(self) -> str:
        for df in (self.seos, self.state):
            if not df.empty:
                return str(df["trade_date"].max())
        return ""

    def _merge(self) -> None:
        """把状态层、SEOS 层、统计层、强度层按 sector_id 横向拼成截面。"""
        if self.seos.empty:
            self.x = pd.DataFrame()
            return
        x = self.seos[self.seos["trade_date"] == self.date].copy()
        x["sector_id"] = x["sector_id"].astype(str)
        for df, cols in (
            (self.state, ["sector_health", "current_state", "anomaly_flags", "state_reason",
                          "sector_purity", "member_count"]),
            (self.stats, ["up_ratio", "weighted_breadth", "core_breadth", "primary_breadth"]),
            (self.strength, ["ew_ret_1", "ew_ret_3", "ew_ret_5", "ew_ret_20", "vs_market_5"]),
            (self.rotation, ["rotation_reason"]),
        ):
            if df.empty or "sector_id" not in df.columns:
                continue
            d = df[df["trade_date"] == self.date].copy()
            d["sector_id"] = d["sector_id"].astype(str)
            keep = ["sector_id"] + [c for c in cols if c in d.columns and c not in x.columns]
            x = x.merge(d[keep], on="sector_id", how="left")
        for c in ("sector_health", "seos_score", "theme_health", "theme_health_delta_1",
                  "theme_health_delta_5",
                  "breadth", "breadth_delta_5", "core_breadth", "core_breadth_delta_5",
                  "rs_turn_5", "relative_strength", "theme_amount_share",
                  "amount_share_delta_5", "volume_ratio_5", "extension_penalty",
                  "ew_ret_1", "ew_ret_5", "ew_ret_20", "vs_market_5", "up_ratio"):
            if c in x.columns:
                x[c] = _num(x[c])
        self.x = x

    def market_trend(self, n: int) -> pd.DataFrame:
        """按交易日算全市场健康度均值与上涨家数占比均值。"""
        if self.state.empty:
            return pd.DataFrame()
        s = self.state.copy()
        s["sector_health"] = _num(s["sector_health"])
        g = s.groupby("trade_date")["sector_health"].mean().rename("health_mean")
        if not self.stats.empty:
            t = self.stats.copy()
            t["up_ratio"] = _num(t["up_ratio"])
            g = g.to_frame().join(t.groupby("trade_date")["up_ratio"].mean(), how="left")
        return g.tail(n)

    def group_names(self) -> dict:
        return {str(g.get("rotation_group")): str(g.get("group_name"))
                for g in self.seos_json.get("groups", [])}


def _state_count(df: pd.DataFrame, col: str, key: str) -> int:
    if df.empty or col not in df.columns:
        return 0
    return int((df[col].astype(str) == key).sum())


# ════════════════════════════════════════════════════════════════════════
# 报告各节
# ════════════════════════════════════════════════════════════════════════
def sec_header(cfg: dict, bd: BoardData, sd: dict, cand: dict, gen_at: str) -> list:
    b_date = bd.date or "NA"
    s_date = sd.get("trade_date", "NA")
    c_date = cand.get("trade_date", "NA")
    same = (b_date == s_date)
    return [
        "# A股主题量化系统 · 每日汇总报告（板块 + 个股）\n",
        f"> 生成时间：{gen_at}　|　脚本：`daily_report_build.py`\n",
        "## 0. 数据口径与日期说明\n",
        _md_table(["层级", "步骤", "交易日期", "产物"], [
            ["板块层 · 状态与宽度", "第 3 步", b_date, "`data/sector_state_history.csv`"],
            ["板块层 · SEOS 与轮动", "第 4 步", b_date, "`data/sector_seos_daily.csv` / `sector_rotation.csv`"],
            ["个股层 · 主题候选池", "第 5 步", c_date, "`output/sector_stock_candidate_top.json`"],
            ["个股层 · 结构 / 天量换手资格", "第 6 步", s_date, "`output/stock_structure_today.json`"],
        ]),
        "> **术语**：HVT = 天量换手（历史天量换手事件及其后续演化）；"
        "SEOS（Sector Early Signal & Opportunity System）为本系统的板块机会分，属专有名词，全篇保留缩写；"
        "Breadth = 广度，Core Breadth = 核心广度，RS turn = 相对强度拐点。\n",
        (f"\n> **口径提示**：板块层为 **{b_date}**，个股层为 **{s_date}**，两层各自独立运行、"
         f"日期不一致，本报告分段标注、不做跨层混算。\n" if not same else
         f"\n> 板块层与个股层同为 **{b_date}**。\n"),
    ]


def sec_market(cfg: dict, bd: BoardData) -> list:
    L = [f"\n## 一、市场温度（板块层 {bd.date}）\n"]
    if bd.x.empty:
        return L + ["（板块层数据缺失）\n"]

    n = cfg["trend_days"]
    tr = bd.market_trend(n)
    L.append(f"### 1.1 近 {n} 个交易日全市场温度\n")
    if not tr.empty:
        rows = [[d, _f(r["health_mean"], 1), _pct0(r["up_ratio"], 2) if "up_ratio" in tr.columns else "-"]
                for d, r in tr.iterrows()]
        L.append(_md_table(["交易日", "板块健康度均值", "上涨家数占比均值"], rows))
        if len(tr) >= 2:
            d1 = tr["health_mean"].iloc[-1] - tr["health_mean"].iloc[-2]
            trend = "升温" if d1 > 2 else ("降温" if d1 < -2 else "基本持平")
            L.append(f"\n今日板块健康度均值 **{_f(tr['health_mean'].iloc[-1], 1)}**，"
                     f"较前一交易日 **{d1:+.1f}**，判定为**{trend}**。\n")

    sc = bd.state_json.get("state_counts", {})
    ac = bd.state_json.get("anomaly_counts", {})
    L.append("### 1.2 板块状态分布\n")
    L.append(_md_table(["状态", "数量"], [
        [_cn(STATE_CN, k), v] for k, v in sc.items() if v or k in ("HEALTHY", "NEUTRAL", "WEAK")
    ]))
    anom = {k: v for k, v in ac.items() if v}
    L.append("\n**异常标记**：" + ("、".join(f"{_cn(FLAG_CN, k)} {v} 个" for k, v in anom.items()) if anom else "无") + "\n")
    return L


def sec_rotation(cfg: dict, bd: BoardData) -> list:
    L = [f"\n## 二、板块轮动（板块层 {bd.date}）\n"]
    if bd.x.empty:
        return L + ["（板块层数据缺失）\n"]
    gn = bd.group_names()

    rc = bd.seos_json.get("rotation_counts", {})
    L.append("### 2.1 轮动信号\n")
    L.append(_md_table(["信号", "数量"], [[_cn(ROTATION_CN, k), v] for k, v in rc.items()]))

    moved = bd.x[bd.x["rotation_signal"].astype(str) != "ROTATION_NEUTRAL"]
    L.append("\n### 2.2 轮入 / 轮出明细\n")
    if moved.empty:
        L.append("今日无轮入 / 轮出信号。\n")
    else:
        rows = [[_cn(ROTATION_CN, r["rotation_signal"]), f"{r['sector_id']} {r['sector_name']}",
                 r.get("rotation_group", "-"), _f(r.get("breadth"), 3),
                 _f(r.get("breadth_delta_5"), 3), _f(r.get("rs_turn_5"), 4),
                 _cn_text(r.get("rotation_reason", ""))] for _, r in moved.iterrows()]
        L.append(_md_table(["信号", "板块", "轮动组", "广度", "广度Δ5", "相对强度拐点Δ5", "判定依据"], rows))

    L.append("\n### 2.3 阶段分布与迁移\n")
    pc = bd.seos_json.get("phase_counts", {})
    L.append(_md_table(["阶段", "数量"], [[_cn(PHASE_CN, k), v] for k, v in pc.items() if v]))
    trans = bd.x[bd.x["phase_transition"].astype(str).str.contains("->", na=False)]
    if not trans.empty:
        L.append("\n**今日阶段迁移**：\n")
        L.append(_md_table(["板块", "轮动组", "迁移", "SEOS", "健康度"],
                           [[f"{r['sector_id']} {r['sector_name']}", r.get("rotation_group", "-"),
                             _cn_seq(PHASE_CN, r["phase_transition"]),
                             _f(r.get("seos_score")), _f(r.get("theme_health"))]
                            for _, r in trans.sort_values("seos_score", ascending=False).iterrows()]))

    L.append("\n### 2.4 轮动组状态（R01–R08）\n")
    rows = []
    for g in bd.seos_json.get("groups", []):
        rows.append([f"{g.get('rotation_group')} {gn.get(str(g.get('rotation_group')), '')}",
                     g.get("group_theme_count"), _f(g.get("group_health"), 1),
                     _f(g.get("group_seos"), 1), _f(g.get("group_seos_delta_5"), 1),
                     _pct0(g.get("group_amount_share"), 2),
                     _cn(GROUP_STATE_CN, g.get("group_rotation_state"))])
    L.append(_md_table(["轮动组", "板块数", "组健康度", "组SEOS", "SEOSΔ5",
                        "组内成交额占比加总", "组状态"], rows))
    return L


def sec_ranking(cfg: dict, bd: BoardData) -> list:
    L = [f"\n## 三、板块强弱榜（板块层 {bd.date}）\n"]
    if bd.x.empty:
        return L + ["（板块层数据缺失）\n"]
    x = bd.x.copy()

    def tbl(title, sub, by, asc, extra=None):
        r = sub.nlargest(TOP_N, by) if not asc else sub.nsmallest(TOP_N, by)
        out = [f"\n### {title}\n"]
        rows = [[f"{v['sector_id']} {v['sector_name']}", v.get("rotation_group", "-"),
                 _f(v.get("sector_health"), 1), _f(v.get("seos_score"), 1),
                 _pct0(v.get("theme_amount_share"), 2),
                 _pct(v.get("ew_ret_1")), _pct(v.get("ew_ret_5"))] for _, v in r.iterrows()]
        out.append(_md_table(["板块", "轮动组", "健康度", "SEOS", "成交额占比",
                              "当日等权", "5日等权"], rows))
        return out

    L += tbl(f"3.1 板块健康度 TOP{TOP_N}", x, "sector_health", False)
    L += tbl(f"3.2 板块健康度 BOTTOM{TOP_N}", x, "sector_health", True)
    L += tbl(f"3.3 SEOS 分数 TOP{TOP_N}", x, "seos_score", False)
    L += tbl(f"3.4 当日等权收益 TOP{TOP_N}", x, "ew_ret_1", False)

    L.append(f"\n### 3.5 成交额占比 TOP{TOP_N}（含 5 日变化）\n")
    r = x.nlargest(TOP_N, "theme_amount_share")
    L.append(_md_table(["板块", "轮动组", "成交额占比", "占比Δ5", "健康度", "健康度Δ5", "SEOS"],
                       [[f"{v['sector_id']} {v['sector_name']}", v.get("rotation_group", "-"),
                         _pct0(v.get("theme_amount_share"), 2), _pct(v.get("amount_share_delta_5"), 2),
                         _f(v.get("theme_health"), 1), _f(v.get("theme_health_delta_5"), 1),
                         _f(v.get("seos_score"), 1)] for _, v in r.iterrows()]))
    return L


def sec_insight(cfg: dict, bd: BoardData) -> tuple:
    """规则化自动解读。返回 (markdown 行列表, 结论要点列表)。"""
    L = [f"\n## 四、自动解读（板块层 {bd.date}）\n"]
    pts = []
    if bd.x.empty:
        return L + ["（板块层数据缺失）\n"], pts
    x = bd.x.copy()
    gn = bd.group_names()
    n = cfg["trend_days"]
    tr = bd.market_trend(n)

    # 1) 温度方向
    if len(tr) >= 2:
        d1 = tr["health_mean"].iloc[-1] - tr["health_mean"].iloc[-2]
        if "up_ratio" in tr.columns:
            u1 = tr["up_ratio"].iloc[-1] - tr["up_ratio"].iloc[-2]
        else:
            u1 = float("nan")
        word = "升温" if d1 > 2 else ("降温" if d1 < -2 else "基本持平")
        txt = (f"全市场板块健康度均值 {_f(tr['health_mean'].iloc[-1], 1)}（前一日 "
               f"{_f(tr['health_mean'].iloc[-2], 1)}，{d1:+.1f}），上涨家数占比均值 "
               f"{_pct0(tr['up_ratio'].iloc[-1], 2)}（{_pct(u1, 2)}），整体**{word}**。")
        pts.append(txt)
        L.append("**1. 市场温度**\n\n" + txt + "\n")

    # 2) 轮动
    moved = x[x["rotation_signal"].astype(str) != "ROTATION_NEUTRAL"]
    if moved.empty:
        txt = "今日无轮入 / 轮出信号，48 个以上板块维持中性——属同步波动日而非轮动日。"
    else:
        ins = moved[moved["rotation_signal"].astype(str) == "ROTATION_IN"]["sector_name"].tolist()
        outs = moved[moved["rotation_signal"].astype(str) == "ROTATION_OUT"]["sector_name"].tolist()
        tot = len(x)
        txt = (f"全场 {tot} 个板块中，仅 {len(ins)} 个轮入（{'、'.join(ins) or '无'}）、"
               f"{len(outs)} 个轮出（{'、'.join(outs) or '无'}），其余全部中性，"
               f"**属同步波动日，无强轮动信号**。")
    pts.append(txt)
    L.append("**2. 轮动信号**\n\n" + txt + "\n")

    # 3) 资金去向：健康板块的轮动组集中度
    healthy = x[x["current_state"].astype(str) == "HEALTHY"] if "current_state" in x.columns else pd.DataFrame()
    if not healthy.empty:
        vc = healthy["rotation_group"].astype(str).value_counts()
        head = vc.index[0]
        names = healthy[healthy["rotation_group"].astype(str) == head]["sector_name"].tolist()
        share = int(vc.iloc[0])
        rest = "、".join(f"{k} {gn.get(k, '')}（{v} 个）" for k, v in vc.iloc[1:].items()) or "无"
        txt = (f"{len(healthy)} 个健康板块中，有 {share} 个集中在 **{head} "
               f"{gn.get(head, '')}**（{'、'.join(names)}），"
               f"说明资金偏向该方向；其余健康板块分布于 {rest}。")
        pts.append(txt)
        L.append("**3. 资金去向**\n\n" + txt + "\n")

    # 4) 弱势集中
    weak = x[x["current_state"].astype(str) == "WEAK"] if "current_state" in x.columns else pd.DataFrame()
    if not weak.empty:
        vc = weak["rotation_group"].astype(str).value_counts()
        txt = (f"{len(weak)} 个走弱板块："
               + "；".join(f"**{k} {gn.get(k, '')}**——{'、'.join(weak[weak['rotation_group'].astype(str) == k]['sector_name'].tolist())}"
                          for k in vc.index))
        pts.append(txt)
        L.append("**4. 弱势集中**\n\n" + txt + "\n")

    # 5) 量价背离：成交额占比靠前但健康度大跌（单日或 5 日）
    div = x.nlargest(DIVERGENCE_TOP_N, "theme_amount_share")
    div = div[(div["theme_health_delta_5"] <= DIVERGENCE_DROP)
              | (div["theme_health_delta_1"] <= DIVERGENCE_DROP_1)]
    if not div.empty:
        rows = [[f"{v['sector_id']} {v['sector_name']}", _pct0(v["theme_amount_share"], 2),
                 _pct(v.get("amount_share_delta_5"), 2), _f(v.get("theme_health"), 1),
                 _f(v.get("theme_health_delta_1"), 1), _f(v.get("theme_health_delta_5"), 1),
                 _pct(v.get("ew_ret_5"))]
                for _, v in div.iterrows()]
        txt = (f"成交额占比 TOP{DIVERGENCE_TOP_N} 中有 {len(div)} 个板块健康度大幅回落"
               f"（单日 ≤ {DIVERGENCE_DROP_1:.0f} 分或 5 日 ≤ {DIVERGENCE_DROP:.0f} 分），"
               f"而成交额占比未退（占比Δ5 多为正），呈现**价格走弱但资金未撤离**的背离，"
               f"属分歧格局而非资金出清：")
        pts.append(txt + " " + "、".join(f"{v['sector_name']}（单日 {_f(v.get('theme_health_delta_1'), 1)}）"
                                        for _, v in div.iterrows()) + "。")
        L.append("**5. 量价背离**\n\n" + txt + "\n")
        L.append(_md_table(["板块", "成交额占比", "占比Δ5", "健康度", "健康度Δ1", "健康度Δ5", "5日等权"], rows))
    else:
        txt = (f"成交额占比 TOP{DIVERGENCE_TOP_N} 中无板块出现量价背离"
               f"（健康度单日跌幅均大于 {DIVERGENCE_DROP_1:.0f} 分、5 日跌幅均大于 {DIVERGENCE_DROP:.0f} 分）。")
        L.append("**5. 量价背离**\n\n" + txt + "\n")

    # 6) 阶段迁移
    trans = x[x["phase_transition"].astype(str).str.contains("->", na=False)]
    if not trans.empty:
        txt = (f"{len(trans)} 个板块发生阶段迁移（"
               + "；".join(f"{v['sector_name']} {_cn_seq(PHASE_CN, v['phase_transition'])}"
                           for _, v in trans.iterrows())
               + "），是零星的活化迹象。")
        pts.append(txt)
        L.append("**6. 阶段迁移**\n\n" + txt + "\n")
    else:
        L.append("**6. 阶段迁移**\n\n今日无板块发生阶段迁移。\n")

    # 7) 数据提示
    wr = x["warmup_ready"].astype(str).str.lower()
    n_false = int(wr.isin(["false", "0"]).sum())
    notes = []
    if n_false:
        notes.append(f"{n_false} 个板块预热未就绪（单日模式回看缓冲不足），其 SEOS 分项可能失真："
                     + "、".join(x[wr.isin(['false', '0'])]["sector_name"].tolist()))
    if "data_invalid" in x.columns:
        nd = int((x["data_invalid"].astype(str).str.lower().isin(["true", "1"])).sum())
        if nd:
            notes.append(f"{nd} 个板块标记数据无效")
    L.append("**7. 数据提示**\n\n" + (_bullet(notes) if notes else "板块层数据完整。\n"))
    return L, pts


def sec_stock_candidate(cfg: dict, cand: dict, bd: BoardData) -> list:
    L = [f"\n## 五、主题 → 个股候选池（个股层 第 5 步，{cand.get('trade_date', 'NA')}）\n"]
    if not cand:
        return L + ["（第 5 步产物缺失）\n"]
    themes = cand.get("top_themes", []) or cand.get("theme_opportunities", [])
    all_themes = sorted(themes, key=lambda t: -(t.get("theme_opportunity_score") or 0))
    total = len(all_themes)
    themes = all_themes[:THEME_TOP_N]
    notice = cand.get("notice") or ""
    L.append(f"本层共输出 {total} 个优先主题，按主题机会分取 TOP{len(themes)} 展开。"
             + (f">{_cn_text(notice)}" if notice else "") + "\n")
    L.append("\n**全部优先主题**：" + "；".join(
        f"{t.get('theme_id')} {t.get('theme_name')}（{_cn(PHASE_CN, t.get('phase'))}，"
        f"SEOS {_f(t.get('seos'), 1)}，机会分 {_f(t.get('theme_opportunity_score'), 1)}，"
        f"{_cn(BUCKET_CN, t.get('bucket'))}）" for t in all_themes) + "\n")

    for t in themes:
        L.append(f"\n### {t.get('theme_id')} {t.get('theme_name')}"
                 f"（{t.get('rotation_group', '-')}）\n")
        L.append(_md_table(["项目", "值"], [
            ["阶段", _cn(PHASE_CN, t.get("phase"))],
            ["SEOS", _f(t.get("seos"), 2)],
            ["主题健康度", _f(t.get("health"), 2)],
            ["主题机会分", _f(t.get("theme_opportunity_score"), 2)],
            ["桶", _cn(BUCKET_CN, t.get("bucket"))],
            ["扩散类型", _cn(DIFFUSION_TYPE_CN, t.get("diffusion_type"))],
            ["候选股数量", t.get("candidate_count", 0)],
        ]))
        ds = t.get("diffusion_state") or {}
        if ds:
            L.append("\n扩散状态：" + "、".join(
                f"{FIELD_CN.get(k, k)}={_f(v, 3)}" for k, v in ds.items() if v is not None) + "\n")
        cs = (t.get("top_candidates") or [])[:STOCK_TOP_N]
        if cs:
            L.append(f"\n**候选股 TOP{len(cs)}**\n")
            L.append(_md_table(["代码", "名称", "角色", "成员层级", "候选类型", "状态",
                                "候选分", "拥挤度", "扩张惩罚", "当日", "5日"],
                               [[c.get("ts_code"), c.get("name"),
                                 _cn(THEME_ROLE_CN, c.get("theme_role")),
                                 f"{_cn(MEMBERSHIP_TYPE_CN, c.get('membership_type'))}"
                                 f"({_f(c.get('membership_confidence'), 2)})",
                                 _cn(CANDIDATE_TYPE_CN, c.get("candidate_type")),
                                 _cn(CANDIDATE_STATUS_CN, c.get("candidate_status")),
                                 _f(c.get("theme_candidate_score"), 2),
                                 f"{_cn(CROWDING_CLASS_CN, c.get('crowding_class'))}"
                                 f"({_f(c.get('crowding_score'), 2)})",
                                 _f(c.get("extension_penalty"), 2),
                                 _pct(c.get("ret_1")), _pct(c.get("ret_5"))] for c in cs]))
            for c in cs[:3]:
                L.append(f"\n- {c.get('name')}（{c.get('ts_code')}）理由："
                         + _cn_text("；".join(c.get("candidate_reason", []))))
            L.append("")
    return L


def sec_structure(cfg: dict, sd: dict, pools: dict, theme_names: dict) -> list:
    L = [f"\n## 六、个股结构 / 天量换手资格（个股层 第 6 步，{sd.get('trade_date', 'NA')}）\n"]
    if not sd:
        return L + ["（第 6 步产物缺失）\n"]
    sm = sd.get("summary", {})
    L.append("### 6.1 资格分布\n")
    L.append(_md_table(["资格", "数量"], [
        [_cn(QUAL_CN, "QUALIFIED"), sm.get("qualified_count", 0)],
        [_cn(QUAL_CN, "CONDITIONAL"), sm.get("conditional_count", 0)],
        [_cn(QUAL_CN, "WATCH"), sm.get("watch_count", 0)],
        [_cn(QUAL_CN, "REJECTED"), sm.get("rejected_count", 0)],
        ["候选总数", sm.get("candidate_count", 0)],
        ["成员快照状态", _cn(MEMBERSHIP_STATUS_CN, sm.get("membership_version_status"))],
    ]))

    def stock_table(rows):
        return _md_table(["代码", "名称", "主主题", "结构状态", "结构质量",
                          "天量换手状态", "天量换手阶段", "天量换手分", "扩张风险", "突破状态"],
                         [[r.get("ts_code"), r.get("name"),
                           f"{r.get('primary_theme_id')} {theme_names.get(str(r.get('primary_theme_id')), '')}",
                           _cn(STRUCTURE_STATE_CN, r.get("structure_state")),
                           _f(r.get("structure_quality"), 1),
                           _cn(HVT_STATE_CN, r.get("hvt_state")),
                           _cn(HVT_STAGE_CN, r.get("hvt_stage")),
                           _f(r.get("hvt_quality_score"), 1),
                           _cn(EXT_RISK_CN, r.get("extension_risk")),
                           _cn(BREAKOUT_STATE_CN, r.get("breakout_state"))] for r in rows])

    q = sd.get("qualified", [])
    L.append(f"\n### 6.2 结构合格（{len(q)} 只）\n")
    if q:
        L.append(stock_table(q))
        for r in q:
            L.append(f"\n- {r.get('name')}（{r.get('ts_code')}）：{_cn_text(r.get('reason', ''))}")
        L.append("")
    else:
        L.append("今日无结构合格个股。\n")

    c = sd.get("conditional", [])
    L.append(f"\n### 6.3 有条件合格（{len(c)} 只）TOP{min(TOP_N, len(c))}\n")
    if c:
        cc = sorted(c, key=lambda r: -(r.get("structure_quality") or 0))[:TOP_N]
        L.append(stock_table(cc))
    else:
        L.append("无。\n")

    L.append("\n### 6.4 结构池计数\n")
    L.append(_md_table(["结构池", "数量", "含义"], [
        [_cn(POOL_CN, "STRUCTURE_POOL"), pools.get("structure", {}).get("count", 0), "结构状态池，非买入池"],
        [_cn(POOL_CN, "HVT_POOL"), pools.get("hvt", {}).get("count", 0), "天量换手锁定 / 再突破状态池"],
        [_cn(POOL_CN, "REBREAKOUT_POOL"), pools.get("rebreakout", {}).get("count", 0), "再突破池"],
        [_cn(POOL_CN, "RETEST_POOL"), pools.get("retest", {}).get("count", 0), "回踩确认池"],
    ]))
    stk = [s for s in pools.get("structure", {}).get("stocks", []) if isinstance(s, dict)]
    if stk:
        stk = sorted(stk, key=lambda s: -(s.get("structure_quality") or 0))
        head = stk[:POOL_TOP_N]
        L.append(f"\n**结构池明细（共 {len(stk)} 只，按结构质量取 TOP{len(head)}）**\n")
        L.append(_md_table(["代码", "名称", "结构状态", "结构分类", "结构质量",
                            "天量换手状态", "扩张风险", "资格"],
                           [[s.get("ts_code"), s.get("name"),
                             _cn(STRUCTURE_STATE_CN, s.get("structure_state")),
                             _cn(STRUCTURE_CLASS_CN, s.get("structure_class")),
                             _f(s.get("structure_quality"), 1),
                             _cn(HVT_STATE_CN, s.get("hvt_state")),
                             _cn(EXT_RISK_CN, s.get("extension_risk")),
                             _cn(QUAL_CN, s.get("qualification"))] for s in head]))
        if len(stk) > len(head):
            L.append(f"\n其余 {len(stk) - len(head)} 只见 `output/stock_structure_pool.json`。\n")
    hvt = pools.get("hvt", {}).get("stocks", [])
    if hvt:
        codes = [s if isinstance(s, str) else s.get("ts_code") for s in hvt]
        L.append(f"\n**天量换手池明细（{len(codes)} 只）**：" + "、".join(codes) + "\n")
    return L


def sec_conclusion(cfg: dict, bd: BoardData, pts: list, sd: dict, cand: dict) -> list:
    L = ["\n## 七、结论要点\n"]
    if bd.date:
        L.append(f"**板块层（{bd.date}）**\n\n" + _bullet(pts))
    if sd:
        sm = sd.get("summary", {})
        L.append(f"**个股层（{sd.get('trade_date', 'NA')}）**\n")
        L.append(_bullet([
            f"候选域 {sm.get('candidate_count', 0)} 只中，结构合格 {sm.get('qualified_count', 0)} 只、"
            f"有条件合格 {sm.get('conditional_count', 0)} 只、观察 {sm.get('watch_count', 0)} 只、"
            f"不合格 {sm.get('rejected_count', 0)} 只。",
            f"主题层优先主题 {len(cand.get('top_themes', []))} 个："
            + "、".join(f"{t.get('theme_name')}（{_cn(PHASE_CN, t.get('phase'))}，SEOS {_f(t.get('seos'), 1)}）"
                        for t in cand.get("top_themes", [])),
            f"成员快照状态 {_cn(MEMBERSHIP_STATUS_CN, sm.get('membership_version_status'))}，"
            f"结构资格受静态成员快照限制。",
        ]))
    return L


def sec_disclaimer(cfg: dict) -> list:
    return [
        "\n## 八、声明\n",
        _bullet([
            "本报告只描述板块状态与个股结构资格，**不构成 BUY / NO TRADE / 仓位 / 止损 / 目标价**。",
            f"所有结构池（{_cn(POOL_CN, 'STRUCTURE_POOL')} / {_cn(POOL_CN, 'HVT_POOL')} / "
            f"{_cn(POOL_CN, 'REBREAKOUT_POOL')} / {_cn(POOL_CN, 'RETEST_POOL')}）均为结构池，不是买入池。",
            "执行层（第 7 步）未实现。",
            "板块层与个股层交易日期可能不同，已在第 0 节标注；跨层比较请以各自日期为准。",
        ]),
    ]


# ════════════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════════════
def build(cfg: dict) -> str:
    gen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bd = BoardData(cfg.get("date"))
    cand = _read_json("sector_stock_candidate_top.json")
    sd = _read_json("stock_structure_today.json")
    pools = {
        "structure": _read_json("stock_structure_pool.json"),
        "hvt": _read_json("stock_hvt_pool.json"),
        "rebreakout": _read_json("stock_rebreakout_pool.json"),
        "retest": _read_json("stock_retest_pool.json"),
    }
    theme_names = {}
    if not bd.seos.empty:
        theme_names = dict(zip(bd.seos["sector_id"].astype(str), bd.seos["sector_name"].astype(str)))

    L = []
    L += sec_header(cfg, bd, sd, cand, gen_at)
    L += sec_market(cfg, bd)
    L += sec_rotation(cfg, bd)
    L += sec_ranking(cfg, bd)
    insight, pts = sec_insight(cfg, bd)
    L += insight
    L += sec_stock_candidate(cfg, cand, bd)
    L += sec_structure(cfg, sd, pools, theme_names)
    L += sec_conclusion(cfg, bd, pts, sd, cand)
    L += sec_disclaimer(cfg)
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="每日汇总报告：板块层（Step 3/4）+ 个股层（Step 5/6）合成一份 Markdown 日报（只读产物）")
    ap.add_argument("--date", default=None, metavar="YYYYMMDD", help="板块层交易日（默认取最新）")
    ap.add_argument("--trend-days", type=int, default=TREND_DAYS_DEFAULT, help="趋势回看交易日数（默认 5）")
    ap.add_argument("--out", default=None, help="输出路径（默认 output/daily_report_<date>.md）")
    ap.add_argument("--print", dest="to_stdout", action="store_true", help="只打印到终端，不写文件")
    args = ap.parse_args(argv)

    cfg = {"date": args.date, "trend_days": args.trend_days}
    md = build(cfg)

    if args.to_stdout:
        print(md)
        return 0

    date = args.date
    if not date:
        seos = _read_csv("sector_seos_daily.csv")
        date = str(seos["trade_date"].max()) if not seos.empty else datetime.now().strftime("%Y%m%d")
    out = args.out or os.path.join(OUTPUT_DIR, f"daily_report_{date}.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"写出 {out}（{len(md.splitlines())} 行 / {len(md)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
