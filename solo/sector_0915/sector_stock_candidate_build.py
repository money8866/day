# -*- coding: utf-8 -*-
"""
第五步 — Theme-to-Stock Candidate Layer（主题 → 个股候选池）
==========================================================

定位
----
在 Step 4（SEOS：主题早期信号与轮动）之上，把「主题机会」映射到主题内
真正具有产业归属、基本质量与价格结构基础的 A 股个股，形成主题驱动的
个股候选池，供 Step 6（Stock Structure / HVT）继续研究。

链路：
    sector_master.json → theme_mapping → theme_membership
        → Step3 Theme State → Step4 SEOS
        → Step5 Theme-to-Stock Candidate（本脚本）
        → candidate_pool → Step6 Stock Structure / HVT → Step7 Execution

绝对边界（需求 §一、§四十二、§四十三）
--------------------------------------
本步骤【不是】交易执行系统，【不是】BUY 系统：
* 输出只有 CANDIDATE / WATCH / EXCLUDE，绝不输出 BUY / NO TRADE。
* 不输出买入价 / 止损价 / 止盈价 / 仓位 / T+1 执行。
* 不实现 HVT / HVT-BULL / IGE / F120 / Execution Score / TradeRank /
  最终交易决策 —— 这些全部属于后续步骤，本脚本绝不提前实现。
* 本脚本不取 Tushare、不重建主题定义、不重建成员关系、不引入新数据供应商。

禁止污染架构（需求 §二）
------------------------
* 唯一主题定义来源：sector_master.json（≡ 需求中的 theme_master.json）
* 唯一正式成员关系来源：config/sector_mapping.json + data/sector_membership.csv
* theme_config.json / subtheme_map.json / theme.json / theme_master.json /
  theme_mapping.json 一律 LEGACY / REFERENCE ONLY，本程序绝不读取
  （由 --validate 的 legacy 隔离检查强制）。
* legacy_config_used 恒为 false。

核心逻辑（需求 §三、§十一）
--------------------------
    不做：强主题 → 主题全部股票 → 按涨幅排序
    而做：SEOS Opportunity → Theme Quality → Theme Membership →
          Industry/Product Role → Stock Fundamental Quality →
          Stock Price Structure → Theme Diffusion → Candidate Pool
最值得研究的是产业地位强但价格尚未扩张的 Theme Core（B）与刚被主题
扩散到的 Theme Diffusion（D），而不是已被充分交易的 Theme Leader（A）。

反过拟合纪律（需求 §三十二）
----------------------------
所有阈值集中在 config/sector_stock_candidate_config.json，代码不得硬编码。
ramp 区间按全样本无条件分位数固定；回测结果不得用于反向调参。
每次修改配置必须 version++ 并在 change_log 记录。

术语映射（项目约定）
--------------------
    theme_id / theme_name / theme_master.json / theme_membership.csv
        ≡ sector_id / sector_name / sector_master.json / data/sector_membership.csv
    theme_* 输出文件 ≡ sector_* 输出文件
    theme_stock_candidate_build.py ≡ sector_stock_candidate_build.py
语义一一对应，不改变任何字段含义。

用法
----
    python sector_stock_candidate_build.py --full              # 全量重算并写出
    python sector_stock_candidate_build.py --date 20260915     # 只重算指定交易日
    python sector_stock_candidate_build.py --validate          # 全项验证
    python sector_stock_candidate_build.py --full --validate
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# ────────────────────────────────────────────────────────────────────────────
# 路径
# ────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(BASE_DIR)
for _p in (PROJ_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")

from sli.utils import setup_logging  # noqa: E402

log = setup_logging(LOG_DIR, name="sector_stock_candidate")

CFG_PATH = os.path.join(CONFIG_DIR, "sector_stock_candidate_config.json")
MASTER_PATH = os.path.join(BASE_DIR, "sector_master.json")

IN_MEMBERSHIP = os.path.join(DATA_DIR, "sector_membership.csv")
IN_SEOS_DAILY = os.path.join(DATA_DIR, "sector_seos_daily.csv")
IN_STATE_HISTORY = os.path.join(DATA_DIR, "sector_state_history.csv")
IN_SEOS_TODAY = os.path.join(OUTPUT_DIR, "sector_seos_today.json")
IN_OPP_POOL = os.path.join(OUTPUT_DIR, "sector_opportunity_pool.json")

OUT_CANDIDATE_DAILY = os.path.join(DATA_DIR, "sector_stock_candidate_daily.csv")
OUT_DIFFUSION = os.path.join(DATA_DIR, "sector_stock_diffusion.csv")
OUT_OVERLAP = os.path.join(DATA_DIR, "sector_stock_overlap.csv")
OUT_POLLUTION = os.path.join(DATA_DIR, "sector_candidate_pollution.csv")
OUT_TODAY = os.path.join(OUTPUT_DIR, "sector_stock_candidate_today.json")
OUT_POOL = os.path.join(OUTPUT_DIR, "sector_stock_candidate_pool.json")
OUT_TOP = os.path.join(OUTPUT_DIR, "sector_stock_candidate_top.json")
OUT_BACKTEST = os.path.join(OUTPUT_DIR, "sector_stock_candidate_backtest.md")
OUT_AUDIT = os.path.join(OUTPUT_DIR, "sector_stock_candidate_audit.md")
OUT_VALIDATION = os.path.join(OUTPUT_DIR, "sector_stock_candidate_validation.csv")

LEGACY_NAMES = ("theme_config.json", "subtheme_map.json", "theme.json",
                "theme_master.json", "theme_mapping.json")

# ────────────────────────────────────────────────────────────────────────────
# 枚举（需求 §七、§十三、§十五、§二十一、§二十四、§二十六）
# ────────────────────────────────────────────────────────────────────────────

PHASES = ["DORMANT", "EARLY", "EMERGING", "CONFIRMING", "STRONG",
          "COOLING", "DETERIORATING", "EXITING", "DATA_INVALID"]

CANDIDATE_TYPES = ["THEME_CORE", "THEME_DIFFUSION", "THEME_SECOND_LINE", "THEME_LEADER",
                   "THEME_RELATIVE_STRENGTH", "THEME_PULLBACK", "THEME_WATCH", "EXCLUDE"]

CANDIDATE_STATUSES = ["CANDIDATE", "WATCH", "EXCLUDE"]

THEME_ROLES = ["CORE_LEADER", "CORE_SUPPLIER", "CORE_EQUIPMENT", "CORE_MATERIAL",
               "CORE_COMPONENT", "CORE_APPLICATION", "INFRASTRUCTURE", "SERVICE",
               "SECOND_LINE", "CROSS_THEME", "CONCEPT_ONLY", "UNKNOWN"]

DIFFUSION_TYPES = ["LEADER_ONLY", "CORE_EXPANSION", "PRIMARY_EXPANSION",
                   "SECOND_LINE_EXPANSION", "FULL_BREADTH_EXPANSION",
                   "FAILED_DIFFUSION", "NO_DIFFUSION"]

# P1-03：扩散形态（三级 breadth 变化量）与扩散阶段；与 diffusion_type（绝对水平口径）并存，不互相替代。
DIFFUSION_PATTERNS = ["LEADER_ONLY", "CORE_EXPANSION", "SECOND_LINE_EMERGING",
                      "FULL_BREADTH_EXPANSION", "FAILED_DIFFUSION", "NO_DIFFUSION"]
DIFFUSION_STAGES = ["NO_DIFFUSION", "EARLY_DIFFUSION", "FULL_DIFFUSION", "FAILED_DIFFUSION"]

# P1-05：跨主题污染分级
POLLUTION_LEVELS = ["LOW", "MEDIUM", "HIGH"]

POLLUTION_TYPES = ["CONCEPT_POLLUTION", "LOW_MEMBERSHIP_CONFIDENCE", "INDUSTRY_CONFLICT",
                   "CONCEPT_ONLY", "OVER_EXTENSION", "VOLUME_SPIKE", "THEME_EXITING",
                   "DATA_INVALID", "CROSS_THEME_CONFLICT"]

RISK_FLAGS = ["HIGH_EXTENSION", "HIGH_CROWDING", "VOLUME_SPIKE", "LEADER_CLIMAX",
              "LOW_BREADTH_SUPPORT", "WEAK_CORE_SUPPORT", "THEME_COOLING",
              "THEME_DIVERGENCE", "LOW_MEMBERSHIP_CONFIDENCE", "FUNDAMENTAL_WEAK",
              "DATA_INCOMPLETE"]

# 角色语义化描述（仅用于 candidate_reason 文案，非阈值）
ROLE_LABEL = {
    "CORE_LEADER": "主题核心龙头产业地位",
    "CORE_SUPPLIER": "主题核心供应商地位",
    "CORE_EQUIPMENT": "主题核心设备环节",
    "CORE_MATERIAL": "主题核心材料环节",
    "CORE_COMPONENT": "主题核心零部件环节",
    "CORE_APPLICATION": "主题核心应用环节",
    "INFRASTRUCTURE": "主题基础设施环节",
    "SERVICE": "主题配套服务环节",
    "SECOND_LINE": "主题二线产业链成员",
    "CROSS_THEME": "跨主题产业重合成员",
    "CONCEPT_ONLY": "仅有概念关系、无产业归属",
    "UNKNOWN": "产业角色未明确",
}

DIFFUSION_LABEL = {
    "CORE_EXPANSION": "由核心成员向外扩散",
    "PRIMARY_EXPANSION": "向主要成员扩散",
    "SECOND_LINE_EXPANSION": "向二线成员扩散",
    "FULL_BREADTH_EXPANSION": "全面扩散",
}

# 需求 §四十四：Step 5 收尾声明（审计报告必须逐条列出，且由验证项核对）
STEP5_STOP_NOTICE = [
    "Step 5 completed.",
    "No BUY / NO TRADE decision generated.",
    "No HVT / IGE / F120 / Execution logic introduced.",
    "No legacy theme configuration used.",
]

# 全程序打开过的文件（用于 legacy 隔离验证）
READ_FILES: list = []

# 输出列（需求 §三十五）
CANDIDATE_DAILY_COLS = [
    "trade_date", "ts_code", "name", "sector_id", "sector_name", "rotation_group", "theme_phase",
    "theme_seos", "theme_health", "membership_type", "membership_confidence", "membership_weight",
    "theme_role", "candidate_type", "fundamental_quality", "structure_quality_precheck",
    "relative_theme_strength", "theme_diffusion_score", "crowding_score", "extension_penalty",
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "distance_ma20", "distance_ma60",
    "distance_ma120", "volume_ratio_5", "volume_ratio_20", "turnover_5", "turnover_20",
    "high_20", "high_60", "high_120", "drawdown_20", "drawdown_60", "drawdown_120",
    "price_extension", "volume_extension", "trend_quality",
    "candidate_score", "candidate_status", "risk_flags", "candidate_reason", "data_quality",
    "crowding_class", "diffusion_type", "diffusion_pattern", "diffusion_stage",
    "theme_opportunity_score", "calibrated_opportunity_base", "calibration_status", "industry_role",
    "industry_conflict", "primary_theme", "primary_theme_id", "secondary_themes",
    "primary_theme_confidence", "theme_overlap_count", "candidate_theme_count",
    "is_context_theme", "cross_theme_pollution_flag", "multi_theme_crowding_flag",
    "cross_theme_bonus", "cross_theme_score_penalty",
    "price_extension_score", "volume_extension_score", "listed_days",
    "role_evidence", "industry",
]

POLLUTION_COLS = ["trade_date", "ts_code", "name", "sector_id", "sector_name",
                  "membership_type", "pollution_type", "reason", "action"]

DIFFUSION_COLS = [
    "trade_date", "sector_id", "sector_name", "rotation_group", "theme_phase", "theme_seos",
    "theme_health", "breadth", "breadth_delta_5", "core_breadth", "core_breadth_delta_3",
    "core_breadth_delta_5", "primary_breadth", "primary_breadth_delta_3", "secondary_breadth",
    "secondary_breadth_delta_3", "secondary_breadth_delta_5",
    "core_breadth_slope", "primary_breadth_slope", "secondary_breadth_slope",
    "theme_ret_1", "theme_ret_5", "top5_concentration", "theme_opportunity_score",
    "theme_bucket", "diffusion_type", "diffusion_pattern", "diffusion_stage",
    "diffusion_score_theme", "candidate_count",
]


# ────────────────────────────────────────────────────────────────────────────
# 基础工具（与 Step 4 风格对齐）
# ────────────────────────────────────────────────────────────────────────────

def _read_csv(path: str, **kw) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少输入文件: {path}")
    READ_FILES.append(os.path.abspath(path))
    kw.setdefault("dtype", {})
    if isinstance(kw["dtype"], dict):
        kw["dtype"].setdefault("trade_date", str)
        kw["dtype"].setdefault("sector_id", str)
        kw["dtype"].setdefault("ts_code", str)
    return pd.read_csv(path, **kw)


def load_json(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少配置文件: {path}")
    READ_FILES.append(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj, path: str):
    """写出严格合法 JSON：非有限浮点一律写为 null。"""
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, (np.floating, float)):
            v = float(o)
            return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(o, np.bool_):
            return bool(o)
        return o

    clean = _clean(obj)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, allow_nan=False)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def write_merge_csv(df: pd.DataFrame, path: str, key_cols=("trade_date", "ts_code", "sector_id")):
    """时间序列合并写出：保留历史行，只替换本次重算覆盖到的键。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key_cols = [c for c in key_cols if c in df.columns]
    if not os.path.exists(path):
        df.to_csv(path, index=False, encoding="utf-8-sig")
        log.info("写出 %s (%d 行, 新建)", os.path.relpath(path, BASE_DIR), len(df))
        return
    old = pd.read_csv(path, dtype={c: str for c in key_cols})
    if list(old.columns) != list(df.columns):
        shutil.copy2(path, path + ".bak")
        log.warning("表结构变化，已备份 %s，按新结构写出", os.path.basename(path) + ".bak")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        log.info("写出 %s (%d 行, 结构更新)", os.path.relpath(path, BASE_DIR), len(df))
        return
    keys = set(map(tuple, df[key_cols].astype(str).values))
    keep = ~old[key_cols].astype(str).apply(tuple, axis=1).isin(keys)
    out = pd.concat([old[keep], df], ignore_index=True)
    out = out.sort_values(key_cols, kind="mergesort").reset_index(drop=True)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("写出 %s (%d 行, 合并保留 %d 行)", os.path.relpath(path, BASE_DIR),
             len(out), int(keep.sum()))


def _fv(v) -> float:
    if v is None:
        return float("nan")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(x) or math.isinf(x):
        return float("nan")
    return x


def _num(v, default=float("nan")) -> float:
    x = _fv(v)
    return default if math.isnan(x) else x


def ramp(x, lo: float, hi: float, inverted: bool = False):
    """线性 ramp：
    升序区间 hi>lo：x<=lo → 0，x>=hi → 100；
    降序区间 hi<lo（配置中以 [lo, hi] 表达反向指标，如 debt_to_assets_inv=[65,30]）：
    x<=hi → 100，x>=lo → 0；
    inverted=True 时取 100 − ramp（用于 near_gap_120 等「越靠近历史高点越差」的指标）。
    NaN 保持 NaN，绝不填 0。
    """
    if isinstance(x, pd.Series):
        v = pd.to_numeric(x, errors="coerce").astype(float)
        if math.isclose(lo, hi):
            r = pd.Series(np.nan, index=v.index)
        elif hi > lo:
            r = ((v - lo) / (hi - lo) * 100.0).clip(0.0, 100.0)
        else:
            r = ((lo - v) / (lo - hi) * 100.0).clip(0.0, 100.0)
        return (100.0 - r) if inverted else r
    v = _fv(x)
    if math.isnan(v) or math.isclose(lo, hi):
        return float("nan")
    if hi > lo:
        r = 0.0 if v <= lo else (100.0 if v >= hi else (v - lo) / (hi - lo) * 100.0)
    else:
        r = 100.0 if v <= hi else (0.0 if v >= lo else (lo - v) / (lo - hi) * 100.0)
    return (100.0 - r) if inverted else r


def neutral_ramp(x, lo: float, hi: float, neutral: float, inverted: bool = False) -> float:
    """缺值 → neutral（配置化 neutral_fill_score），不人为填 0。"""
    r = ramp(x, lo, hi, inverted)
    return float(neutral) if (isinstance(r, float) and math.isnan(r)) else r


def r4(x):
    x = _fv(x)
    return None if math.isnan(x) else round(x, 4)


def b_round(x, nd: int = 4):
    x = _fv(x)
    return None if math.isnan(x) else round(x, nd)


def wmean(pairs, default: float = float("nan")) -> float:
    """加权平均，自动跳过 NaN 项并按剩余权重归一。"""
    num = den = 0.0
    for val, wt in pairs:
        v = _fv(val)
        if math.isnan(v):
            continue
        num += v * wt
        den += wt
    if den <= 0:
        return default
    return num / den


def cls_of(score, bounds: dict, order: list, default: str = "UNKNOWN") -> str:
    """按 {类别: 上界} 归入类别，bounds 按递增上界配置。"""
    s = _fv(score)
    if math.isnan(s):
        return default
    for name in order:
        b = _fv(bounds.get(name))
        if math.isnan(b):
            continue
        if s < b:
            return name
    return order[-1] if order else default


# ────────────────────────────────────────────────────────────────────────────
# 输入层（纯消费 Step 1~4 产出 + 既有本地缓存，不新增数据供应商）
# ────────────────────────────────────────────────────────────────────────────

def load_master_meta() -> tuple:
    """主题唯一真源：sector_master.json（≡ theme_master.json）。"""
    raw = load_json(MASTER_PATH)
    meta = {}
    for s in raw.get("sectors", []) or []:
        sid = str(s.get("sector_id"))
        meta[sid] = {
            "sector_id": sid,
            "sector_name": str(s.get("sector_name", "")),
            "rotation_group": str(s.get("rotation_group", "")),
            "sector_type": str(s.get("sector_type", "")),
            "aliases": [str(a) for a in (s.get("aliases") or [])],
            "subsectors": [str(a) for a in (s.get("subsectors") or [])],
            "keywords": [str(a) for a in (s.get("keywords") or [])],
            "industry_keywords": [str(a) for a in (s.get("eastmoney_industry_keywords") or [])],
            "concept_keywords": [str(a) for a in (s.get("eastmoney_concept_keywords") or [])],
            "exclude_keywords": [str(a) for a in (s.get("exclude_keywords") or [])],
        }
    groups = {}
    for g in raw.get("rotation_groups", []) or []:
        if isinstance(g, dict):
            groups[str(g.get("id"))] = str(g.get("name", ""))
    log.info("主题真源载入：%d 个主题 / %d 个轮动组", len(meta), len(groups))
    return meta, groups


def load_membership() -> pd.DataFrame:
    """唯一正式成员关系：data/sector_membership.csv（由 config/sector_mapping.json 生成）。"""
    mb = _read_csv(IN_MEMBERSHIP, dtype=str)
    READ_FILES.append(os.path.abspath(os.path.join(CONFIG_DIR, "sector_mapping.json")))
    for c in ("membership_confidence", "membership_weight"):
        mb[c] = pd.to_numeric(mb[c], errors="coerce")
    mb["is_static"] = mb["is_static"].astype(str).str.lower().isin(["true", "1", "yes"])
    mb["effective_date"] = mb["effective_date"].astype(str)
    log.info("成员关系载入：%d 行 / %d 股票 / %d 主题", len(mb),
             mb["ts_code"].nunique(), mb["sector_id"].nunique())
    return mb


def load_theme_panel() -> pd.DataFrame:
    """Step 3/4 主题日频面板（seos_daily）。"""
    cols = ["trade_date", "sector_id", "sector_name", "rotation_group", "theme_health",
            "seos_score", "theme_phase", "breadth", "breadth_delta_5", "core_breadth",
            "core_breadth_delta_3", "core_breadth_delta_5", "primary_breadth",
            "primary_breadth_delta_3", "secondary_breadth", "top5_concentration",
            "volume_ratio_5", "relative_strength", "ew_ret_5", "ew_ret_10", "ew_ret_20",
            "data_quality_score", "warmup_ready", "data_invalid"]
    df = _read_csv(IN_SEOS_DAILY, dtype=str)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"sector_seos_daily.csv 缺少必需列: {missing}")
    df = df[cols].copy()
    bool_cols = ("warmup_ready", "data_invalid")
    for c in cols:
        if c in ("trade_date", "sector_id", "sector_name", "rotation_group", "theme_phase") or c in bool_cols:
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in bool_cols:
        df[c] = df[c].astype(str).str.strip().str.lower().isin(["true", "1", "yes", "y"])
    df = df.sort_values(["sector_id", "trade_date"], kind="mergesort").reset_index(drop=True)
    # secondary_breadth 无现成 delta 列，由本层在 <=D 序列上自算（需求 §十三 二线扩散）
    g = df.groupby("sector_id", sort=False)["secondary_breadth"]
    df["secondary_breadth_delta_5"] = df["secondary_breadth"] - g.shift(5)
    # P1-03：补齐三级 breadth 的 3 日变化量与斜率，供 diffusion_pattern 区分「刚开始改善」与「已全面扩散」。
    #   全部只用到 t 及 t 之前的面板值（shift 为正），PIT 可得。
    df["secondary_breadth_delta_3"] = df["secondary_breadth"] - g.shift(3)
    for _lyr in ("core", "primary", "secondary"):
        df[f"{_lyr}_breadth_slope"] = pd.to_numeric(
            df[f"{_lyr}_breadth_delta_3"], errors="coerce") / 3.0
    log.info("主题面板载入：%d 行 / %d 主题 / %s → %s", len(df), df["sector_id"].nunique(),
             df["trade_date"].min(), df["trade_date"].max())
    return df


def load_stock_basic() -> pd.DataFrame:
    """股票基础信息（既有本地 parquet 缓存，不新增数据供应商）。"""
    path = r"D:\mystock\solo\sli\cache\stock_basic.parquet"
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少股票基础信息: {path}")
    READ_FILES.append(path)
    sb = pd.read_parquet(path)
    keep = [c for c in ("ts_code", "name", "industry", "list_status", "list_date") if c in sb.columns]
    sb = sb[keep].copy()
    sb["ts_code"] = sb["ts_code"].astype(str)
    sb["list_date"] = sb["list_date"].astype(str)
    return sb.drop_duplicates("ts_code", keep="first")


DB_PATH = r"D:\mystock\cache_daily\stock_data.db"


def _db_query(sql: str, params: tuple) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"缺少行情缓存库: {DB_PATH}")
    READ_FILES.append(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def load_prices(d_start: str, d_end: str, codes: list) -> tuple:
    """行情 + 每日指标 + 复权因子（既有 SQLite 缓存）。"""
    code_set = set(codes)
    px = _db_query(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, amount "
        "FROM daily_cache WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    db = _db_query(
        "SELECT ts_code, trade_date, turnover_rate, volume_ratio, total_mv, circ_mv, pe_ttm, pb "
        "FROM daily_basic_cache WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    af = _db_query(
        "SELECT ts_code, trade_date, adj_factor FROM adj_factor_cache "
        "WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    for f in (px, db, af):
        f["ts_code"] = f["ts_code"].astype(str)
        f["trade_date"] = f["trade_date"].astype(str)
    px = px[px["ts_code"].isin(code_set)]
    db = db[db["ts_code"].isin(code_set)]
    af = af[af["ts_code"].isin(code_set)]
    log.info("行情载入：daily %d 行 / daily_basic %d 行 / adj %d 行", len(px), len(db), len(af))
    return px, db, af


def load_fundamentals(max_ann_date: str, min_ann_date: str) -> pd.DataFrame:
    """财务指标（PIT：只保留 ann_date <= signal_date 才可用，见 merge_asof 向后匹配）。"""
    fina = _db_query(
        "SELECT ts_code, ann_date, end_date, roe, grossprofit_margin, or_yoy, netprofit_yoy, "
        "netprofit_2yoy, ocf_to_or, ocf_yoy, debt_to_assets FROM fina_indicator_cache "
        "WHERE ann_date <= ? AND ann_date >= ?", (max_ann_date, min_ann_date))
    fina["ts_code"] = fina["ts_code"].astype(str)
    fina["ann_date"] = fina["ann_date"].astype(str)
    fina["end_date"] = fina["end_date"].astype(str)
    fina = fina.dropna(subset=["ann_date"]).drop_duplicates(["ts_code", "ann_date"], keep="last")
    fina["ann_dt_int"] = pd.to_numeric(fina["ann_date"], errors="coerce")
    fina = fina.dropna(subset=["ann_dt_int"]).sort_values("ann_dt_int", kind="mergesort")
    log.info("财务指标载入：%d 行 / %d 股票 / ann_date ≤ %s", len(fina), fina["ts_code"].nunique(),
             max_ann_date)
    return fina.reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# 个股价格结构（需求 §十、§十四；全部只用 <=D 的行情，无未来函数）
# ────────────────────────────────────────────────────────────────────────────

def _wide(df: pd.DataFrame, val: str, calendar: list) -> pd.DataFrame:
    d = df[["trade_date", "ts_code", val]].drop_duplicates(["trade_date", "ts_code"], keep="last")
    w = d.pivot(index="trade_date", columns="ts_code", values=val)
    return w.reindex(calendar)


def compute_stock_features(px: pd.DataFrame, dbb: pd.DataFrame, af: pd.DataFrame,
                           calendar: list, panel_dates: list, horizons: list) -> pd.DataFrame:
    """宽表向量化计算个股结构指标，最后只保留 panel_dates 的长表。

    所有指标严格使用 trade_date <= D 的数据；forward return 仅供回测使用，
    绝不写入任何对外输出文件（由 --validate 强制）。
    close 只做 ffill（处理停牌），绝不做 bfill —— 避免用上市后价格回填上市前区间。
    """
    raw_close = _wide(px, "close", calendar)
    close_w = raw_close.ffill()
    high_w = _wide(px, "high", calendar).ffill()
    vol_raw = _wide(px, "vol", calendar)
    fct_w = _wide(af, "adj_factor", calendar).ffill().bfill()

    adj = close_w * fct_w
    adj_high = high_w * fct_w

    out = {}

    # §十 收益率
    for k in (1, 3, 5, 10, 20, 60):
        out[f"ret_{k}"] = adj / adj.shift(k) - 1.0

    # §十 均线距离
    for k in (20, 60, 120):
        ma = adj.rolling(k, min_periods=k).mean()
        out[f"distance_ma{k}"] = adj / ma - 1.0

    # §十 量能
    vprev5 = vol_raw.shift(1).rolling(5, min_periods=3).mean()
    vprev20 = vol_raw.shift(1).rolling(20, min_periods=10).mean()
    out["volume_ratio_5"] = vol_raw / vprev5.replace(0.0, np.nan)
    out["volume_ratio_20"] = vol_raw / vprev20.replace(0.0, np.nan)

    tov = _wide(dbb, "turnover_rate", calendar)
    out["turnover_5"] = tov.rolling(5, min_periods=3).mean()
    out["turnover_20"] = tov.rolling(20, min_periods=10).mean()

    # §十 高点与回撤（均为复权口径）
    for k in (20, 60, 120):
        h = adj_high.rolling(k, min_periods=k).max()
        out[f"high_{k}"] = h
        out[f"drawdown_{k}"] = adj / h - 1.0
    # 贴近历史高点程度：越小越贴近（配置中登记为 inverted ramp）
    out["near_gap_120"] = (out["high_120"] - adj) / adj

    # 趋势质量（均线多头排列程度，0-100；数据不足 → NaN，不填 0）
    ma20 = adj.rolling(20, min_periods=20).mean()
    ma60 = adj.rolling(60, min_periods=60).mean()
    ma120 = adj.rolling(120, min_periods=120).mean()
    enough = ma20.notna() & ma60.notna()
    c_up = ma20 >= ma60
    c_deep = ma60 >= ma120
    arr = np.where(c_up, 75.0, 25.0)
    arr = np.where(c_up & c_deep, 100.0, arr)
    arr = np.where(c_up & (~c_deep) & ma120.notna(), 60.0, arr)
    arr = np.where(enough, arr, np.nan)
    out["trend_quality"] = pd.DataFrame(arr, index=adj.index, columns=adj.columns)

    # §十 实际行情历史天数（含停牌日不计；用原始未 ffill 的收盘价）
    out["price_hist_days"] = raw_close.notna().cumsum()
    out["traded_today"] = vol_raw.fillna(0.0) > 0

    # 每日指标（估值只作 valuation_context，不参与主题归属与核心评分；需求 §九）
    out["total_mv"] = _wide(dbb, "total_mv", calendar)
    out["circ_mv"] = _wide(dbb, "circ_mv", calendar)
    out["pe_ttm"] = _wide(dbb, "pe_ttm", calendar)
    out["pb"] = _wide(dbb, "pb", calendar)

    # 回测用前向收益（内部专用）
    for h in horizons:
        out[f"_fwd_ret_{h}"] = adj.shift(-h) / adj - 1.0

    parts = {}
    for name, w in out.items():
        parts[name] = w.loc[panel_dates].stack()
    long = pd.DataFrame(parts)
    long.index.names = ["trade_date", "ts_code"]
    long = long.reset_index()
    log.info("个股结构特征：%d 行 × %d 列（仅 %d 个面板日）", len(long), long.shape[1], len(panel_dates))
    return long


def compute_fundamental_pit(panel_keys: pd.DataFrame, fina: pd.DataFrame) -> pd.DataFrame:
    """PIT 财务对齐：对每个 (ts_code, trade_date) 取 ann_date <= trade_date 的最新一条。"""
    left = panel_keys.copy()
    left["td_int"] = pd.to_numeric(left["trade_date"], errors="coerce")
    left = left.dropna(subset=["td_int"]).sort_values("td_int", kind="mergesort")
    right = fina.sort_values("ann_dt_int", kind="mergesort")
    merged = pd.merge_asof(left, right, left_on="td_int", right_on="ann_dt_int",
                           by="ts_code", direction="backward")
    merged = merged.drop(columns=["ann_dt_int"], errors="ignore")
    # 半年报/季报为年内累计值 → 按 12/月数 年化（仅 roe / or_yoy 无需年化）
    months = pd.to_numeric(merged["end_date"].astype(str).str.slice(4, 6), errors="coerce")
    merged["fina_months"] = months.where(months.between(1, 12))
    merged["roe_annualized"] = merged["roe"] * (12.0 / merged["fina_months"])
    # 财务信息陈旧度（交易日 − 报告期）
    td = pd.to_datetime(merged["trade_date"], format="%Y%m%d", errors="coerce")
    ed = pd.to_datetime(merged["end_date"], format="%Y%m%d", errors="coerce")
    merged["fina_stale_days"] = (td - ed).dt.days
    return merged


# ────────────────────────────────────────────────────────────────────────────
# PIT 成员口径（P0-01 修复）：Step 5 内所有「成员 → 主题层」聚合的唯一入口
#   禁止任何位置绕过本层直接用全表 membership 回填历史。
# ────────────────────────────────────────────────────────────────────────────

def pit_membership_mask(df: pd.DataFrame, dates) -> pd.Series:
    """PIT 成员判定的唯一实现（需求 §三十）。

    规则：is_static=true 视为整个回看窗口内恒定的正式定义；否则要求
    effective_date <= D；若成员表含 membership_end_date，则额外要求 D < end。
    """
    if np.isscalar(dates):
        d = pd.Series(str(dates), index=df.index)
    else:
        d = pd.Series(np.asarray(dates, dtype=object), index=df.index).astype(str)
    ok = df["is_static"].fillna(False).astype(bool) | (df["effective_date"].astype(str) <= d)
    if "membership_end_date" in df.columns:
        end = df["membership_end_date"].astype(str).str.strip()
        has_end = ~end.str.lower().isin(["", "nan", "none", "nat", "null"])
        ok = ok & ((~has_end) | (d < end))
    return ok


def get_pit_membership(membership: pd.DataFrame, date) -> pd.DataFrame:
    """按 as-of 日期 D 返回 PIT 成员快照（唯一 accessor，供测试与审计调用）。"""
    return membership[pit_membership_mask(membership, str(date))].copy()


def apply_pit_membership(pairs: pd.DataFrame, date_col: str = "trade_date") -> pd.DataFrame:
    """对含日期列的成员对（ts_code × sector_id × D）应用 PIT 过滤（唯一实现）。"""
    return pairs[pit_membership_mask(pairs, pairs[date_col])].copy()


def test_theme_return_pit(panel: pd.DataFrame, stock_long: pd.DataFrame, membership: pd.DataFrame,
                          theme: pd.DataFrame, sample_dates: int = 6) -> tuple:
    """P0-01 自动测试：主题层等权收益聚合必须与个股层同口径（逐日 PIT）。

    1) 泄漏测试：合成「非 static 且 effective_date > D」成员，验证 D 的成员快照排除它、
       且在 effective_date 之后可见（证明过滤是 PIT 而非整体丢弃）；
    2) 独立重算：抽样日期上 theme_member_valid 必须等于
       「PIT 成员 ∩ 当日有 ret_5 值的股票」的独立计数。
    """
    msgs = []
    # 1) 泄漏测试
    if len(membership):
        mb = membership.copy()
        i = mb.index[0]
        was_static = bool(mb.loc[i, "is_static"])
        mb.loc[i, "is_static"] = False
        mb.loc[i, "effective_date"] = "20991231"
        key = (str(mb.loc[i, "ts_code"]), str(mb.loc[i, "sector_id"]))
        snap_d = get_pit_membership(mb, "20250630")
        in_d = key in set(zip(snap_d["ts_code"].astype(str), snap_d["sector_id"].astype(str)))
        snap_f = get_pit_membership(mb, "20991231")
        in_f = key in set(zip(snap_f["ts_code"].astype(str), snap_f["sector_id"].astype(str)))
        leak_ok = (not in_d) and in_f
        msgs.append(f"leakage: 未来成员进入 D={in_d} 到期后可见={in_f} 原is_static={was_static}")
    else:
        leak_ok = False
        msgs.append("leakage: 空成员表")

    # 2) 独立重算
    if "theme_member_valid" not in theme.columns:
        return (False, "主题层缺少 theme_member_valid；" + "；".join(msgs))
    t = theme[["trade_date", "sector_id", "theme_member_valid"]].copy()
    t["trade_date"] = t["trade_date"].astype(str)
    ds = sorted(set(t["trade_date"]))
    if len(ds) > sample_dates:
        step = max(1, len(ds) // sample_dates)
        ds = ds[::step][:sample_dates]
    sf = stock_long[["trade_date", "ts_code", "ret_5"]].copy()
    sf["trade_date"] = sf["trade_date"].astype(str)
    bad, checked = 0, 0
    for d in ds:
        sfd = set(sf.loc[sf["trade_date"].eq(d) & sf["ret_5"].notna(), "ts_code"].astype(str))
        p = get_pit_membership(membership, d)
        p = p[p["ts_code"].astype(str).isin(sfd)]
        cnt = p.groupby(p["sector_id"].astype(str), sort=False).size().to_dict()
        for _, r in t[t["trade_date"].eq(d)].iterrows():
            checked += 1
            exp = int(cnt.get(str(r["sector_id"]), 0))
            got = pd.to_numeric(r["theme_member_valid"], errors="coerce")
            got = 0 if pd.isna(got) else int(got)
            if exp != got:
                bad += 1
    msgs.append(f"独立重算不一致={bad}/{checked}")
    return (bool(leak_ok and bad == 0 and checked > 0), "；".join(msgs))


# ────────────────────────────────────────────────────────────────────────────
# P0-02 机会状态校准层（Opportunity Calibration，严格 PIT）
#   Step 4 的 SEOS 保持不变（structural signal）；Step 5 新增本层，
#   用 signal_date < D 的滚动历史统计各 phase 的相对 alpha，禁用「EMERGING 天然 85」。
# ────────────────────────────────────────────────────────────────────────────

def compute_opportunity_calibration(panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """按 (trade_date, theme_phase) 输出 PIT 机会校准结果。

    口径（全部只用到 D 当日及之前的信息）：
      fwd_h(s) = ew_ret_h(s+h)                     # 主题 s 之后 h 日的等权收益（面板自带滚动收益）
      ex_h(s)  = fwd_h(s) - 当日全主题均值           # 横截面去均值 → 剥离市场 beta
      alpha(D, p) = mean{ ex_h(s) : phase(s)=p, s+h+gap <= D, s >= D-lookback }

    输出：calibration_alpha / calibration_sample_size / calibration_t_stat /
          calibration_status / phase_quality_multiplier / calibrated_base_cap。
    """
    cc = cfg["theme_opportunity"]["opportunity_calibration"]
    out_cols = ["trade_date", "theme_phase", "calibration_alpha", "calibration_sample_size",
                "calibration_t_stat", "calibration_status", "phase_quality_multiplier",
                "calibration_base_cap"]
    if not cc.get("enabled", True):
        d0 = panel[["trade_date", "theme_phase"]].drop_duplicates().copy()
        d0["calibration_alpha"] = np.nan
        d0["calibration_sample_size"] = 0
        d0["calibration_t_stat"] = np.nan
        d0["calibration_status"] = "CALIBRATION_DISABLED"
        d0["phase_quality_multiplier"] = 1.0
        d0["calibration_base_cap"] = 100.0
        return d0[out_cols]

    hz = [int(h) for h in cc["horizons"]]
    lookback = int(cc["lookback_days"])
    min_n = int(cc["min_sample_size"])
    gap = int(cc.get("min_gap_days", 0))
    buckets = [(float(b), float(m)) for b, m in cc["alpha_multiplier_buckets"]]

    d = panel[["trade_date", "sector_id", "theme_phase"] + [f"ew_ret_{h}" for h in hz]].copy()
    d["trade_date"] = d["trade_date"].astype(str)
    d = d.sort_values(["sector_id", "trade_date"], kind="mergesort").reset_index(drop=True)
    dates = sorted(d["trade_date"].unique())
    pos = {dt: i for i, dt in enumerate(dates)}
    d["_pos"] = d["trade_date"].map(pos)

    recs = []
    for h in hz:
        fwd = d.groupby("sector_id", sort=False)[f"ew_ret_{h}"].shift(-h)   # t+h 日滚动 h 日收益 = t→t+h 前瞻
        ex = fwd - fwd.groupby(d["trade_date"]).transform("mean")           # 同日横截面去均值
        t = pd.DataFrame({"theme_phase": d["theme_phase"], "_pos": d["_pos"], "_ex": ex})
        recs.append(t.dropna(subset=["_ex"]))
    ob = pd.concat(recs, ignore_index=True) if recs else pd.DataFrame(columns=["theme_phase", "_pos", "_ex"])

    rows = []
    for dt in dates:
        pD = pos[dt]
        sel = ob[(ob["_pos"] + gap <= pD) & (ob["_pos"] >= pD - lookback)]
        if not len(sel):
            continue
        for ph, g in sel.groupby("theme_phase", sort=False):
            v = g["_ex"]
            n = int(len(v))
            a = float(v.mean())
            sd = float(v.std(ddof=1)) if n > 1 else float("nan")
            rows.append({"trade_date": dt, "theme_phase": ph, "calibration_alpha": a,
                         "calibration_sample_size": n,
                         "calibration_t_stat": (a / (sd / math.sqrt(n))) if (n > 1 and sd and sd > 0) else np.nan})
    cal = pd.DataFrame(rows, columns=["trade_date", "theme_phase", "calibration_alpha",
                                      "calibration_sample_size", "calibration_t_stat"])
    base = panel[["trade_date", "theme_phase"]].drop_duplicates().copy()
    base["trade_date"] = base["trade_date"].astype(str)
    cal = base.merge(cal, on=["trade_date", "theme_phase"], how="left")

    n = pd.to_numeric(cal["calibration_sample_size"], errors="coerce").fillna(0.0)
    a = pd.to_numeric(cal["calibration_alpha"], errors="coerce")
    insufficient = n < min_n
    nonpos = (~insufficient) & (a <= 0.0)
    cal["calibration_status"] = np.where(insufficient, "CALIBRATION_INSUFFICIENT",
                                 np.where(nonpos, "CALIBRATION_NONPOSITIVE", "CALIBRATION_POSITIVE"))

    def mult_of(x):
        for ub, m in buckets:
            if x <= ub:
                return m
        return buckets[-1][1]

    m_insuff = float(cc["insufficient_multiplier"])
    mult = [mult_of(v) if v == v else m_insuff for v in a]
    cal["phase_quality_multiplier"] = np.where(insufficient, m_insuff, mult)
    cal["calibration_base_cap"] = np.where(insufficient, float(cc["base_cap_insufficient"]),
                                   np.where(nonpos, float(cc["base_cap_alpha_nonpositive"]), 100.0))
    return cal[out_cols]


def apply_opportunity_calibration(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """把 phase_base 校准为 calibrated_opportunity_base（P0-02）。"""
    cc = cfg["theme_opportunity"]["opportunity_calibration"]
    sup = cc["support_requirement"]
    out = df.copy()
    raw = pd.to_numeric(out["raw_opportunity_base"], errors="coerce")
    mult = pd.to_numeric(out["phase_quality_multiplier"], errors="coerce").fillna(1.0)
    cap = pd.to_numeric(out["calibration_base_cap"], errors="coerce").fillna(100.0)
    base = np.minimum(raw * mult, cap)

    # §六：CONFIRMING / STRONG 保留较高基础分的前提是 breadth / core breadth / RS / health 共同支持
    c = sup["conditions"]
    sup_cnt = ((pd.to_numeric(out["breadth_delta_5"], errors="coerce") >= c["breadth_delta_5_min"]).astype(int)
               + (pd.to_numeric(out["core_breadth_delta_5"], errors="coerce") >= c["core_breadth_delta_5_min"]).astype(int)
               + (pd.to_numeric(out["relative_strength"], errors="coerce") >= c["relative_strength_min"]).astype(int)
               + (pd.to_numeric(out["theme_health"], errors="coerce") >= c["theme_health_min"]).astype(int))
    gate = out["theme_phase"].isin(sup["phases"]) & (sup_cnt < int(sup["min_support_count"]))
    base = np.where(gate, np.minimum(base, float(sup["base_cap_when_unsupported"])), base)

    out["calibrated_opportunity_base"] = pd.Series(base, index=out.index).clip(0.0, 100.0)
    out["calibration_support_count"] = sup_cnt
    out["calibration_support_gate"] = gate
    return out


# ────────────────────────────────────────────────────────────────────────────
# 主题层：机会评分 / 主题收益 / 扩散类型（需求 §四、§十二、§十三、§二十九）
# ────────────────────────────────────────────────────────────────────────────

def compute_theme_layer(panel: pd.DataFrame, stock_long: pd.DataFrame,
                        membership: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """主题层：opportunity 分数、主题等权收益、扩散类型。"""
    df = panel.copy()
    opp_cfg = cfg["theme_opportunity"]
    w = opp_cfg["weights"]
    ramps = opp_cfg["ramps"]

    # P0-02：phase_base 不再直接进入评分，先命名 raw_opportunity_base，再经 PIT 机会校准层。
    df["raw_opportunity_base"] = df["theme_phase"].map(opp_cfg["raw_phase_base_scores"]).astype(float)
    cal = compute_opportunity_calibration(df, cfg)
    df = df.merge(cal, on=["trade_date", "theme_phase"], how="left")
    df = apply_opportunity_calibration(df, cfg)

    def _opp(base_col):
        return (w["phase_base"] * pd.to_numeric(base_col, errors="coerce")
                + w["seos"] * ramp(df["seos_score"], *ramps["seos_score"]).fillna(0.0)
                + w["health"] * ramp(df["theme_health"], *ramps["theme_health"]).fillna(0.0)).clip(0.0, 100.0)

    df["raw_opportunity_score"] = _opp(df["raw_opportunity_base"])
    df["theme_opportunity_score"] = _opp(df["calibrated_opportunity_base"])
    df["theme_bucket"] = df["theme_phase"].map(opp_cfg["phase_to_bucket"]).fillna("NONE")
    df["is_active_bucket"] = df["theme_bucket"].isin(opp_cfg["active_buckets"])
    df["is_risk_bucket"] = df["theme_bucket"].isin(opp_cfg["risk_buckets"])

    # 主题等权收益（PIT 成员 → 等权）：供个股相对主题强弱使用（需求 §十四）
    # P0-01：与个股层同口径 —— 逐日 PIT 成员（唯一 accessor），禁止全表 membership 回填历史。
    sf = stock_long[["trade_date", "ts_code", "ret_1", "ret_3", "ret_5", "ret_10", "ret_20"]]
    panel_dates = pd.DataFrame({"trade_date": sorted(df["trade_date"].astype(str).unique())})
    sf = sf[sf["trade_date"].astype(str).isin(set(panel_dates["trade_date"]))]
    mm = membership[["ts_code", "sector_id", "membership_weight", "is_static", "effective_date"]]
    j = apply_pit_membership(panel_dates.merge(mm, how="cross"))   # (D × 全量成员) → PIT 过滤
    j = j.merge(sf, on=["trade_date", "ts_code"], how="inner")
    agg = j.groupby(["trade_date", "sector_id"], sort=False).agg(
        theme_ret_1=("ret_1", "mean"),
        theme_ret_3=("ret_3", "mean"),
        theme_ret_5=("ret_5", "mean"),
        theme_ret_10=("ret_10", "mean"),
        theme_ret_20=("ret_20", "mean"),
        theme_member_valid=("ret_5", "count"),
    ).reset_index()
    df = df.merge(agg, on=["trade_date", "sector_id"], how="left")

    # 扩散类型（需求 §十三）：按「扩散递进阶梯」判定——后应用的 mask 覆盖先应用的，
    # 故实际优先级为 FAILED_DIFFUSION > FULL_BREADTH > SECOND_LINE > PRIMARY > CORE > LEADER_ONLY，
    # 即越晚期/越具体的阶段优先，LEADER_ONLY 为最低优先级兜底。
    tr = cfg["diffusion_thresholds"]["type_rules"]
    bd5 = df["breadth_delta_5"]
    cd5 = df["core_breadth_delta_5"]
    pd3 = df["primary_breadth_delta_3"]
    sd5 = df["secondary_breadth_delta_5"]
    prior_bd5 = df["breadth_delta_5"].groupby(df["sector_id"], sort=False).shift(
        int(tr["failed_prior_lag"]))

    cond_failed = (prior_bd5 >= tr["failed_prior_expansion_min"]) & (bd5 <= tr["failed_breadth_delta_5_max"])
    cond_full = (bd5 >= tr["full_breadth_breadth_delta_5_min"]) & (sd5 >= tr["full_breadth_secondary_delta_5_min"])
    cond_second = sd5 >= tr["second_line_expansion_min"]
    cond_primary = pd3 >= tr["primary_expansion_min"]
    cond_core = cd5 >= tr["core_expansion_min"]
    cond_leader = (cd5 <= tr["leader_only_max_core_delta"]) & \
                  (df["theme_ret_5"] >= tr["leader_ret_5_min"])

    dtype = pd.Series("NO_DIFFUSION", index=df.index, dtype=object)
    dtype = dtype.mask(cond_leader, "LEADER_ONLY")
    dtype = dtype.mask(cond_core, "CORE_EXPANSION")
    dtype = dtype.mask(cond_primary, "PRIMARY_EXPANSION")
    dtype = dtype.mask(cond_second, "SECOND_LINE_EXPANSION")
    dtype = dtype.mask(cond_full, "FULL_BREADTH_EXPANSION")
    dtype = dtype.mask(cond_failed, "FAILED_DIFFUSION")
    dtype = dtype.mask(df["theme_phase"].eq("DATA_INVALID") | (~df["warmup_ready"]), "NO_DIFFUSION")
    df["diffusion_type"] = dtype

    # ────────────────────────────────────────────────────────────────────────
    # P1-03：扩散形态 diffusion_pattern / diffusion_stage
    #   与 diffusion_type（绝对水平口径）并存：本层表达「扩散阶梯」（谁在改善、改善到什么程度），
    #   用于识别「第二梯队刚开始改善」（SECOND_LINE_EMERGING），避免扩散识别滞后。
    #   全部输入均为 t 日及之前的面板值（delta 由 shift 正方向构造），PIT 可得。
    # ────────────────────────────────────────────────────────────────────────
    pr3 = cfg["diffusion_thresholds"]["pattern_rules"]
    cd3 = pd.to_numeric(df["core_breadth_delta_3"], errors="coerce")
    sd3 = pd.to_numeric(df["secondary_breadth_delta_3"], errors="coerce")
    core_up = cd3 >= pr3["core_up_min"]
    core_stable = (cd3 > pr3["core_stable_low"]) & (cd3 <= pr3["core_stable_high"])
    core_strong = core_up | (pd.to_numeric(df["core_breadth_delta_5"], errors="coerce") >= pr3["core_up_min"])
    prim_up = pd3 >= pr3["primary_up_min"]
    prim_flat = pd3.abs() <= pr3["primary_flat_abs_max"]
    sec_up = (sd3 >= pr3["secondary_up_min"]) & (sd5 >= pr3["secondary_up_min"])
    sec_emerging = (sd3 >= pr3["emerging_delta_3_min"]) & (sd5 <= pr3["emerging_delta_5_max"])
    prim_emerging = (pd3 >= pr3["emerging_delta_3_min"]) & (sd3 <= pr3["emerging_delta_5_max"])

    p_full = core_up & prim_up & sec_up
    p_failed = core_strong & (pd3 <= pr3["failed_primary_delta_3_max"]) \
        & (sd5 <= pr3["failed_secondary_delta_5_max"])
    # §十：第二梯队刚开始改善 = 上层已稳定 + 下层 delta_3 转正但 delta_5 尚未确认 → EARLY_DIFFUSION
    p_emerging = (core_up | core_stable) & (sec_emerging | prim_emerging) & (~p_full)
    p_core = core_up & prim_up
    p_leader = core_up & prim_flat & (~sec_up)

    dpat = pd.Series("NO_DIFFUSION", index=df.index, dtype=object)
    dpat = dpat.mask(p_leader, "LEADER_ONLY")
    dpat = dpat.mask(p_core, "CORE_EXPANSION")
    dpat = dpat.mask(p_emerging, "SECOND_LINE_EMERGING")
    dpat = dpat.mask(p_full, "FULL_BREADTH_EXPANSION")
    dpat = dpat.mask(p_failed, "FAILED_DIFFUSION")
    dpat = dpat.mask(df["theme_phase"].eq("DATA_INVALID") | (~df["warmup_ready"]), "NO_DIFFUSION")
    df["diffusion_pattern"] = dpat
    df["diffusion_stage"] = df["diffusion_pattern"].map(
        cfg["diffusion_thresholds"]["pattern_stage_map"]).fillna("NO_DIFFUSION")

    # 主题层扩散强度（breadth 改善 + 核心改善 + 成交份额参与）
    r = cfg["diffusion_thresholds"]["ramps"]
    sw = cfg["diffusion_thresholds"]["score_weights"]
    df["diffusion_score_theme"] = wmean_pairs_df(
        df,
        [(ramp(bd5, *r["theme_breadth_delta_5"]), sw["theme_breadth_improvement"]),
         (ramp(cd5, *r["theme_core_breadth_delta_5"]), sw["stock_relative_strength_improvement"] * 0.5),
         (ramp(df["volume_ratio_5"], *r["stock_volume_ratio_5"]), sw["stock_volume_participation"] * 0.5)],
        default=0.0,
    )
    # 需求 §十三：FULL_BREADTH 属晚期，额外提高扩张惩罚（在个股层叠加）
    df["full_breadth_extra"] = np.where(dtype.eq("FULL_BREADTH_EXPANSION"),
                                        cfg["diffusion_thresholds"]["full_breadth_extra_extension"], 0.0)
    return df


def wmean_pairs_df(df: pd.DataFrame, pairs, default=float("nan")) -> pd.Series:
    """按行加权平均，自动跳过 NaN 并按剩余权重归一。"""
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for col, wt in pairs:
        v = pd.to_numeric(col, errors="coerce")
        ok = v.notna()
        num = num.add((v.fillna(0.0) * wt).where(ok, 0.0), fill_value=0.0)
        den = den.add(pd.Series(wt, index=df.index).where(ok, 0.0), fill_value=0.0)
    out = num / den.replace(0.0, np.nan)
    return out.fillna(default) if not (isinstance(default, float) and math.isnan(default)) else out


# ────────────────────────────────────────────────────────────────────────────
# 成员质量 / 产业角色（需求 §五、§六、§七）
# ────────────────────────────────────────────────────────────────────────────

def compute_membership_quality(mb: pd.DataFrame, cfg: dict) -> pd.Series:
    m = cfg["membership_thresholds"]
    ts = mb["membership_type"].map(m["type_scores"]).astype(float).fillna(0.0)
    cf = ramp(mb["membership_confidence"], *m["confidence_ramp"])
    wf = ramp(mb["membership_weight"], *m["weight_ramp"])
    ob = mb["membership_origin"].map(m["origin_bonus"]).astype(float).fillna(0.0)
    qw = m["quality_weights"]
    base = (qw["type"] * ts + qw["confidence"] * cf.fillna(0.0) + qw["weight"] * wf.fillna(0.0))
    return (base + ob).clip(0.0, 100.0)


def detect_theme_role(mb: pd.DataFrame, meta: dict, cfg: dict,
                      cross_theme_ids: set) -> pd.DataFrame:
    """产业角色必须来自实际主营 / 产业链关系 / 正式 membership（需求 §七、§六）。
    绝不因为涨幅最大而自动判为 CORE_LEADER。
    同时输出 role_evidence（证据层级），供审计核对「概念关系层不得直接进 CANDIDATE」。
    """
    rc = cfg["industry_role"]
    role_by_origin = rc["role_by_origin"]
    role_by_ev = rc["role_by_evidence_level"]
    ind_boards = rc["industry_board_types"]
    sub_map = rc["subsector_role_map"]
    roles, evidences = [], []
    for row in mb.itertuples(index=False):
        sid = str(getattr(row, "sector_id"))
        s = meta.get(sid, {})
        ind = str(getattr(row, "industry", "") or "")
        btype = str(getattr(row, "board_type", "") or "")
        text = "|".join([ind, str(getattr(row, "source_board", "") or ""),
                         str(getattr(row, "reason", "") or "")])
        origin = str(getattr(row, "membership_origin", "") or "")
        mtype = str(getattr(row, "membership_type", "") or "")
        role, ev = "", ""

        # 1) 正式 membership 的成员来源（最强证据）
        if origin in role_by_origin:
            role, ev = role_by_origin[origin], "COMPANY_ORIGIN"
        # 2) 产业链细分环节命中（实际主营/行业/来源板块）
        if not role:
            for sub in s.get("subsectors", []):
                if sub and sub in text:
                    for key, rl in sub_map.items():
                        if key and key in sub:
                            role, ev = rl, "SUBSECTOR"
                            break
                if role:
                    break
        # 3) stock_basic 行业分类命中主题行业关键词（§六 第 5 层）
        if not role:
            if ind and any(k and (k in ind or ind in k) for k in s.get("industry_keywords", [])):
                role, ev = role_by_ev["INDUSTRY_CLASSIFICATION"], "INDUSTRY_CLASSIFICATION"
        # 4) 主题关键词 / 主题名命中：区分行业板块证据 vs 概念关系证据（§六 第 6-7 层）
        if not role:
            min_chars = int(rc["conflict_check"]["min_master_name_hit_chars"])
            name_hit = len(s.get("sector_name", "")) >= min_chars and s.get("sector_name", "") in text
            kw_pool = s.get("keywords", [])
            kw_hit = any(k and k in text for k in kw_pool)
            if name_hit or kw_hit:
                ind_hit = bool(ind) and any(k and k in ind for k in kw_pool)
                if btype in ind_boards or ind_hit:
                    role, ev = role_by_ev["INDUSTRY_BOARD_KEYWORD"], "INDUSTRY_BOARD_KEYWORD"
                else:
                    role, ev = role_by_ev["CONCEPT_RELATION"], "CONCEPT_RELATION"
        # 5) 跨主题（在其他主题有 CORE/PRIMARY 归属）
        if not role and str(getattr(row, "ts_code", "")) in cross_theme_ids:
            role, ev = "CROSS_THEME", "CROSS_THEME"
        # 6) 成员类型兜底
        if not role:
            ev = "MEMBERSHIP_FALLBACK"
            if mtype == "CORE":
                role = "CORE_SUPPLIER"
            elif mtype in ("PRIMARY", "SECONDARY"):
                role = "SECOND_LINE"
            elif mtype in ("THEMATIC", "OBSERVATION"):
                role = "SECOND_LINE" if str(getattr(row, "mapping_method", "")) in (
                    "DIRECT_INDUSTRY", "INDUSTRY_PRODUCT") else "CONCEPT_ONLY"
            else:
                role = "UNKNOWN"
        roles.append(role)
        evidences.append(ev)
    return pd.DataFrame({"theme_role": roles, "role_evidence": evidences}, index=mb.index)


def detect_industry_conflict(mb: pd.DataFrame, meta: dict, cfg: dict) -> pd.Series:
    """需求 §二十.3：行业与主题行业关键词完全不相交且主题名/关键词无命中 → 冲突（仅降级）。"""
    min_chars = int(cfg["industry_role"]["conflict_check"]["min_master_name_hit_chars"])
    flags = []
    for row in mb.itertuples(index=False):
        s = meta.get(str(getattr(row, "sector_id")), {})
        ind = str(getattr(row, "industry", "") or "")
        text = "|".join([ind, str(getattr(row, "source_board", "") or ""),
                         str(getattr(row, "reason", "") or "")])
        pools = (s.get("industry_keywords", []) + s.get("keywords", [])
                 + s.get("aliases", []) + s.get("subsectors", []))
        hit = any(p and (p in text or text and text in p) for p in pools if p)
        if not hit and len(s.get("sector_name", "")) >= min_chars:
            hit = s.get("sector_name", "") in text
        flags.append(not hit)
    return pd.Series(flags, index=mb.index, dtype=bool)


# ────────────────────────────────────────────────────────────────────────────
# 个股层评分（需求 §八、§九、§十、§十四、§十七、§十八、§二十九）
# ────────────────────────────────────────────────────────────────────────────

FUND_COMPONENTS = ["roe_annualized", "grossprofit_margin", "or_yoy",
                   "netprofit_yoy", "ocf_to_or", "debt_to_assets_inv"]


def compute_fundamental_quality(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """基础质量过滤（需求 §八）：缺值不填 0，按 neutral_fill 处理并计入 data_quality。
    估值(pe/pb)只作 valuation_context，不进入评分（需求 §九）。
    """
    fq = cfg["fundamental_quality"]
    ramps = fq["ramps"]
    weights = fq["weights"]
    neutral = fq["neutral_fill_score"]

    comp = {}
    comp["roe_annualized"] = ramp(df["roe_annualized"], *ramps["roe_annualized"])
    comp["grossprofit_margin"] = ramp(df["grossprofit_margin"], *ramps["grossprofit_margin"])
    comp["or_yoy"] = ramp(df["or_yoy"], *ramps["or_yoy"])
    comp["netprofit_yoy"] = ramp(df["netprofit_yoy"], *ramps["netprofit_yoy"])
    # 口径修正（v1.4）：fina_indicator_cache.ocf_to_or 为比率（茅台 0.78=78%、紫金 0.286=28.6%），
    # 而 config ramp [0,25] 按百分数定义 → 边界 ×100 归一化。未改动任何 ramp 与阈值。
    comp["ocf_to_or"] = ramp(pd.to_numeric(df["ocf_to_or"], errors="coerce") * 100.0,
                             *ramps["ocf_to_or"])
    comp["debt_to_assets_inv"] = ramp(df["debt_to_assets"], *ramps["debt_to_assets_inv"])

    stale_max = _fv(fq["stale_days_max"])
    stale_ok = pd.to_numeric(df["fina_stale_days"], errors="coerce") <= stale_max
    for k in comp:
        comp[k] = comp[k].where(stale_ok, np.nan)

    present = sum(comp[k].notna().astype(float) for k in FUND_COMPONENTS)
    coverage = present / float(len(FUND_COMPONENTS))
    filled = {k: comp[k].fillna(neutral) for k in FUND_COMPONENTS}

    score = pd.Series(0.0, index=df.index)
    for k in FUND_COMPONENTS:
        score = score + filled[k] * weights[k]
    out = df.copy()
    out["fundamental_quality"] = score.clip(0.0, 100.0)
    out["fundamental_data_quality"] = (coverage * 100.0).clip(0.0, 100.0)
    out["fundamental_weak"] = out["fundamental_quality"] < _fv(fq["weak_threshold"])
    return out


def compute_structure_quality(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """价格结构 precheck（需求 §十）：只判断是否值得进入下一阶段结构分析。"""
    sq = cfg["structure_quality"]
    ramps = sq["ramps"]
    weights = sq["weights"]
    neutral = cfg["data_rules"]["neutral_fill_score"]

    c = {
        "dist_ma20": ramp(df["distance_ma20"], *ramps["dist_ma20"]),
        "dist_ma60": ramp(df["distance_ma60"], *ramps["dist_ma60"]),
        "volume_health": ramp(df["volume_ratio_5"], *ramps["volume_ratio_5"]),
        "drawdown_control": ramp(df["drawdown_120"], *ramps["drawdown_120"]),
    }
    out = df.copy()
    out["structure_quality_precheck"] = wmean_pairs_df(
        out, [(c[k].fillna(neutral), weights[k]) for k in c], default=neutral).clip(0.0, 100.0)
    out["trend_broken"] = pd.to_numeric(out["distance_ma60"], errors="coerce") < \
        _fv(sq["trend_broken_max_dist_ma60"])
    return out


def compute_relative_strength(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §十四：相对主题强不是自动加分——高分同时会触发 extension_penalty。"""
    rs = cfg["relative_theme_strength"]
    out = df.copy()
    for k in (1, 5, 10, 20):
        out[f"rel_ret_{k}"] = pd.to_numeric(out[f"ret_{k}"], errors="coerce") - \
            pd.to_numeric(out[f"theme_ret_{k}"], errors="coerce")
    out["rel_ret_3"] = pd.to_numeric(out["ret_3"], errors="coerce") - \
        pd.to_numeric(out["theme_ret_3"], errors="coerce")
    neutral = cfg["data_rules"]["neutral_fill_score"]
    pairs = []
    for k, wt in rs["weights"].items():
        pairs.append((ramp(out[k], *rs["ramps"][k]).fillna(neutral), wt))
    out["relative_theme_strength"] = wmean_pairs_df(out, pairs, default=neutral).clip(0.0, 100.0)
    # 相对强度改善 / 价格加速度（均只用 <=D 的序列，无未来函数）
    out["rel_ret_5_improvement"] = out["rel_ret_5"] - out.groupby(
        ["ts_code", "sector_id"], sort=False)["rel_ret_5"].shift(5)
    out["rel_ret_20_prior"] = out["rel_ret_20"]
    out["ret_accel_5"] = pd.to_numeric(out["ret_5"], errors="coerce") - out.groupby(
        ["ts_code", "sector_id"], sort=False)["ret_5"].shift(5)
    return out


def compute_extension(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §十七/§二十.4-5：独立 extension_penalty；极端扩张/极端成交量直接 EXCLUDE。"""
    ex = cfg["extension_penalty"]
    ramps = ex["ramps"]
    weights = ex["weights"]
    inverted = set(ex.get("inverted_ramps", []))
    out = df.copy()

    # extension_penalty 的指标键 → 数据列名（配置化阈值，列名映射在代码内固定）
    EXT_COL = {
        "ret_5": "ret_5", "ret_20": "ret_20", "dist_ma20": "distance_ma20",
        "volume_ratio_5": "volume_ratio_5", "turnover_5": "turnover_5",
        "near_gap_120": "near_gap_120",
    }
    comp = {}
    for k in weights:
        comp[k] = ramp(out[EXT_COL[k]], *ramps[k], inverted=(k in inverted))
    present = sum(comp[k].notna().astype(float) for k in weights)
    out["extension_component_coverage"] = present / float(len(weights))
    filled = {k: comp[k].fillna(0.0) for k in weights}
    raw = pd.Series(0.0, index=out.index)
    for k in weights:
        raw = raw + filled[k] * weights[k]
    out["extension_score"] = raw.clip(0.0, 100.0)
    out["extension_penalty"] = (raw / 100.0 * _fv(ex["max_penalty_points"])).clip(
        0.0, _fv(ex["max_penalty_points"]))
    out["extension_penalty"] = out["extension_penalty"] + pd.to_numeric(
        out["full_breadth_extra"], errors="coerce").fillna(0.0)

    # 价格 / 成交量扩张分级
    cls = cfg["crowding_thresholds"]["classes"]
    order = cfg["crowding_thresholds"]["class_order"]
    price_score = pd.concat([ramp(out[EXT_COL["ret_5"]], *ramps["ret_5"]),
                             ramp(out[EXT_COL["ret_20"]], *ramps["ret_20"]),
                             ramp(out[EXT_COL["dist_ma20"]], *ramps["dist_ma20"])], axis=1).max(axis=1)
    vol_score = pd.concat([ramp(out[EXT_COL["volume_ratio_5"]], *ramps["volume_ratio_5"]),
                           ramp(out[EXT_COL["turnover_5"]], *ramps["turnover_5"])], axis=1).max(axis=1)
    out["price_extension_score"] = price_score
    out["volume_extension_score"] = vol_score
    out["price_extension"] = [cls_of(v, cls, order, "UNKNOWN") for v in price_score]
    out["volume_extension"] = [cls_of(v, cls, order, "UNKNOWN") for v in vol_score]

    er = ex["extreme_rules"]
    pe = er["price_extension_extreme"]
    ve = er["volume_extension_extreme"]
    out["extreme_price"] = ((pd.to_numeric(out[EXT_COL["ret_5"]], errors="coerce") >= pe["ret_5_min"])
                            | (pd.to_numeric(out[EXT_COL["dist_ma20"]], errors="coerce") >= pe["dist_ma20_min"]))
    out["extreme_volume"] = ((pd.to_numeric(out[EXT_COL["volume_ratio_5"]], errors="coerce") >= ve["volume_ratio_5_min"])
                             | (pd.to_numeric(out[EXT_COL["turnover_5"]], errors="coerce") >= ve["turnover_5_min"]))
    return out


# crowding 权重键 → (数据列, ramp 键, 是否反向)
CROWD_MAP = {
    "ret_20": ("ret_20", "ret_20", False),
    "ret_60": ("ret_60", "ret_60", False),
    "dist_ma20": ("distance_ma20", "dist_ma20", False),
    "dist_ma60": ("distance_ma60", "dist_ma60", False),
    "turnover_20": ("turnover_20", "turnover_20", False),
    "volume_ratio_20": ("volume_ratio_20", "volume_ratio_20", False),
    "near_gap_120_inv": ("near_gap_120", "near_gap_120", True),
    "theme_top5_concentration": ("top5_concentration", "theme_top5_concentration", False),
}


def compute_crowding(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §十八：高拥挤 ≠ 排除，只降低优先级。"""
    cg = cfg["crowding_thresholds"]
    ramps = cg["ramps"]
    weights = cg["weights"]
    inverted = set(cg.get("inverted_ramps", []))
    out = df.copy()
    pairs = []
    for k, wt in weights.items():
        col, rk, inv = CROWD_MAP[k]
        pairs.append((ramp(out[col], *ramps[rk], inverted=(inv or k in inverted)), wt))
    neutral = cfg["data_rules"]["neutral_fill_score"]
    out["crowding_score"] = wmean_pairs_df(
        out, [(p.fillna(neutral), w) for p, w in pairs], default=neutral).clip(0.0, 100.0)
    out["crowding_class"] = [cls_of(v, cg["classes"], cg["class_order"], "UNKNOWN")
                             for v in out["crowding_score"]]
    return out


def compute_diffusion_signal(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §十二、§二十九：theme_stock_diffusion_score（low crowding 只作辅助因子）。"""
    dt = cfg["diffusion_thresholds"]
    r = dt["ramps"]
    sw = dt["score_weights"]
    neutral = cfg["data_rules"]["neutral_fill_score"]
    out = df.copy()
    out["low_crowding_component"] = 100.0 - pd.to_numeric(out["crowding_score"], errors="coerce")
    pairs = [
        (ramp(out["breadth_delta_5"], *r["theme_breadth_delta_5"]), sw["theme_breadth_improvement"]),
        (ramp(out["rel_ret_5_improvement"], *r["stock_rel_ret_5_improvement"]),
         sw["stock_relative_strength_improvement"]),
        (ramp(out["volume_ratio_5"], *r["stock_volume_ratio_5"]), sw["stock_volume_participation"]),
        (ramp(out["membership_quality"], *r["membership_quality"]), sw["membership_quality"]),
        (ramp(out["ret_accel_5"], *r["stock_ret_5_acceleration"]), sw["price_acceleration"]),
        (ramp(out["low_crowding_component"], *r["low_crowding"]), sw["low_crowding"]),
    ]
    out["theme_diffusion_score"] = wmean_pairs_df(
        out, [(p.fillna(neutral), w) for p, w in pairs], default=neutral).clip(0.0, 100.0)

    sig = dt["stock_signal"]
    out["is_theme_diffusion_stock"] = (
        (pd.to_numeric(out["breadth_delta_5"], errors="coerce") >= sig["theme_strength_up_min"])
        & (pd.to_numeric(out["rel_ret_5_improvement"], errors="coerce") >= sig["stock_rel_ret_5_improvement_min"])
        & (pd.to_numeric(out["rel_ret_20_prior"], errors="coerce") <= sig["prior_rel_ret_20_max"])
        & (out["membership_type"].isin(sig["membership"]))
        & (pd.to_numeric(out["theme_diffusion_score"], errors="coerce") >= sig["min_score"])
    )
    return out


# ────────────────────────────────────────────────────────────────────────────
# 候选类型 / 状态 / 原因 / 风险标签（需求 §十五、§二十四、§二十五、§二十六、§十九）
# ────────────────────────────────────────────────────────────────────────────

def classify_candidate_type(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """需求 §十五：类型判定按配置优先级自上而下，首个命中生效。"""
    rules = cfg["candidate_type_rules"]
    st = cfg["status_thresholds"]
    pr = cfg["pullback_rules"]
    sl = cfg["diffusion_thresholds"]["second_line_stock_signal"]
    role_score = df["theme_role"].map(cfg["industry_role"]["role_scores"]).astype(float)
    rc_max = rules["leader_conditions"]
    out = pd.Series("THEME_WATCH", index=df.index, dtype=object)

    # THEME_PULLBACK（§十九）：强主题中的健康调整
    # P1-04：必须经过 Theme Opportunity Gate，禁止「股票超跌/回撤/低于 MA20」直接进入 THEME_PULLBACK。
    pgate = pr["theme_opportunity_gate"]
    gate_bucket = df["is_active_bucket"].fillna(False) if pgate["require_active_bucket"] \
        else pd.Series(True, index=df.index)
    cond_pullback = (
        df["theme_phase"].isin(pr["phases"])
        & df["membership_type"].isin(pr["membership"])
        & (pd.to_numeric(df["distance_ma60"], errors="coerce") >= pr["trend_not_broken"]["dist_ma60_min"])
        & (pd.to_numeric(df["distance_ma120"], errors="coerce") >= pr["trend_not_broken"]["dist_ma120_min"])
        & (pd.to_numeric(df["drawdown_20"], errors="coerce").between(*pr["drawdown_20_range"]))
        & (pd.to_numeric(df["volume_ratio_5"], errors="coerce") <= pr["volume_contraction"]["volume_ratio_5_max"])
        & (pd.to_numeric(df["rel_ret_20"], errors="coerce") >= pr["rel_strength_stable_min_rel_ret_20"])
        & (pd.to_numeric(df["fundamental_quality"], errors="coerce") >= pr["fundamental_min"])
        & (pd.to_numeric(df["calibrated_opportunity_base"], errors="coerce")
           >= _fv(pgate["min_calibrated_opportunity_base"]))
        & df["calibration_status"].isin(pgate["accepted_calibration_status"])
        & gate_bucket
    )
    cond_diffusion = df["is_theme_diffusion_stock"].fillna(False)
    # P1-03：第二梯队「刚开始改善」（diffusion_pattern=SECOND_LINE_EMERGING）不再被漏判
    cond_second = (
        df["membership_type"].isin(sl["membership"])
        & (df["diffusion_type"].isin(sl["diffusion_types"])
           | df["diffusion_pattern"].isin(sl.get("diffusion_patterns", [])))
        & (pd.to_numeric(df["ret_5"], errors="coerce") >= sl["stock_ret_5_min"])
    )
    cond_leader = (
        df["theme_role"].eq(rc_max["role"])
        & df["crowding_class"].isin(rc_max["crowding"])
        & (pd.to_numeric(df["ret_20"], errors="coerce") >= rc_max["ret_20_min"])
    )
    cond_core = (
        df["membership_type"].isin(st["candidate_membership"])
        & (role_score >= st["candidate_role_min"])
        & (pd.to_numeric(df["fundamental_quality"], errors="coerce") >= st["candidate_fundamental_min"])
        & (pd.to_numeric(df["structure_quality_precheck"], errors="coerce") >= st["candidate_structure_min"])
    )
    cond_rel_strength = df["is_risk_bucket"].fillna(False) & (
        (pd.to_numeric(df["rel_ret_5"], errors="coerce")
         >= cfg["theme_opportunity"]["risk_phase_policy"]["structural_rs_min_rel_ret_5"])
        | (pd.to_numeric(df["rel_ret_10"], errors="coerce")
           >= cfg["theme_opportunity"]["risk_phase_policy"]["structural_rs_min_rel_ret_10"])
    )

    out = out.mask(cond_core, "THEME_CORE")
    out = out.mask(cond_leader, "THEME_LEADER")
    out = out.mask(cond_second, "THEME_SECOND_LINE")
    out = out.mask(cond_diffusion, "THEME_DIFFUSION")
    out = out.mask(cond_pullback, "THEME_PULLBACK")
    # 非核心成员（THEMATIC/OBSERVATION）只能进 THEME_WATCH
    out = out.mask(df["membership_type"].isin(cfg["membership_thresholds"]["non_core_types"]),
                   "THEME_WATCH")
    out = out.mask(cond_rel_strength, "THEME_RELATIVE_STRENGTH")
    return out


def compute_cross_theme(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §二十七/§二十八 + P1-05：跨主题不重复计分；primary_theme 只由产业归属证据决定。

    定序（需求 §十三）：成员层级 > membership_confidence > membership_weight > industry_role
    > business_relevance > sector_id；明确禁止用近期收益 / 主题强度 / 主题热度做判定（旧实现
    用 theme_opportunity_score + theme_diffusion_score 定主主题，属被禁止的「主题强度/热度」驱动）。

    membership ≠ candidate_theme（需求 §十四/§十七）：只有前 max_active_candidate_themes 个主题
    参与候选计分，其余登记为 context themes（封顶 WATCH），避免「同一股票 × N 主题」重复计分；
    多主题 membership 本身一律保留，不因多主题删除真实产业关系。
    """
    ct = cfg["cross_theme_bonus"]
    sel = ct["primary_theme_selection"]
    lvl_rank = {t: i for i, t in enumerate(sel["level_order"])}
    n_levels = len(lvl_rank)
    role_scores = cfg["industry_role"]["role_scores"]
    biz_rank = sel["business_relevance_rank"]
    n_max = int(ct["max_active_candidate_themes"])
    pol = ct["pollution_levels"]

    out = df.copy()
    out["_lvl"] = out["membership_type"].map(lvl_rank).fillna(n_levels).astype(float)
    out["_conf"] = pd.to_numeric(out["membership_confidence"], errors="coerce").fillna(-1.0)
    out["_wt"] = pd.to_numeric(out["membership_weight"], errors="coerce").fillna(-1.0)
    out["_role"] = out["theme_role"].map(role_scores).astype(float).fillna(-1.0)
    out["_biz"] = out["role_evidence"].map(biz_rank).fillna(-1.0)
    out = out.sort_values(
        ["trade_date", "ts_code", "_lvl", "_conf", "_wt", "_role", "_biz", "sector_id"],
        ascending=[True, True, True, False, False, False, False, True], kind="mergesort")
    grp = out.groupby(["trade_date", "ts_code"], sort=False)
    out["theme_rank_in_stock"] = (grp.cumcount() + 1).astype(int)
    out["candidate_theme_count"] = grp["sector_id"].transform("size").astype(int)
    out["is_context_theme"] = out["theme_rank_in_stock"] > n_max
    out["primary_theme_id"] = grp["sector_id"].transform("first").astype(str)
    out["primary_theme_confidence"] = pd.to_numeric(
        grp["membership_confidence"].transform("first"), errors="coerce").fillna(0.0)

    def _join(sub: pd.DataFrame) -> pd.Series:
        if not len(sub):
            return pd.Series(dtype=object)
        return sub.groupby(["trade_date", "ts_code"], sort=False)["sector_id"] \
            .apply(lambda s: "|".join(sorted(set(s.astype(str)))))

    sec = _join(out[(out["theme_rank_in_stock"] > 1) & (~out["is_context_theme"])])
    ctx = _join(out[out["is_context_theme"]])
    # merge 会按当前（已排序）行序重发 RangeIndex；必须还原为排序前的原索引，
    # 否则调用方用「调用前构造的 Series × 调用后列」时会按标签错位（candidate_score 被置换）。
    _row_index = out.index
    out = out.merge(sec.rename("secondary_themes").reset_index(),
                    on=["trade_date", "ts_code"], how="left")
    out = out.merge(ctx.rename("context_theme_ids").reset_index(),
                    on=["trade_date", "ts_code"], how="left")
    out.index = _row_index
    out["secondary_themes"] = out["secondary_themes"].fillna("").astype(object)
    out["context_theme_ids"] = out["context_theme_ids"].fillna("").astype(object)
    n_sec = pd.Series([0 if not s else len(str(s).split("|")) for s in out["secondary_themes"]],
                      index=out.index, dtype="int64")
    counted = n_sec.clip(upper=int(ct["max_counted_secondary"])).astype(float)
    out["cross_theme_bonus"] = (counted * _fv(ct["per_active_secondary_theme"])).clip(
        0.0, _fv(ct["max_points"]))
    out["cross_theme_bonus"] = np.where(out["theme_rank_in_stock"] == 1, out["cross_theme_bonus"], 0.0)
    out["primary_theme"] = out["primary_theme_id"]

    # P1-05：污染分级（需求 §十六）= 「报告」+「执行约束」双职责
    overlap = pd.to_numeric(out["theme_overlap_count"], errors="coerce").fillna(1.0)
    pconf = pd.to_numeric(out["primary_theme_confidence"], errors="coerce").fillna(0.0)
    flag = pd.Series("LOW", index=out.index, dtype=object)
    flag = flag.mask(overlap >= _fv(pol["MEDIUM"]["theme_count_min"]), "MEDIUM")
    flag = flag.mask((overlap >= _fv(pol["HIGH"]["theme_count_min"]))
                     | (pconf < _fv(pol["HIGH"]["primary_confidence_below"])), "HIGH")
    out["cross_theme_pollution_flag"] = flag
    # §十五：重复计分惩罚只在「同一股票同时进入 > max_active_candidate_themes 个主题的候选计分」时触发，
    #   不按 membership 主题总数惩罚 —— membership ≠ candidate_theme，多主题真实产业关系一律保留（§十七）。
    crowd = out["candidate_theme_count"] > n_max
    out["cross_theme_score_penalty"] = np.where(
        crowd, _fv(ct["multi_theme_crowding"]["score_penalty"]), 0.0)
    out["multi_theme_crowding_flag"] = np.where(crowd, str(ct["multi_theme_crowding"]["flag"]), "")
    return out.sort_index(kind="mergesort")


def build_risk_flags(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """需求 §二十六：风险标签（向量化，逐标签按 RISK_FLAGS 顺序拼接）。"""
    rf = cfg["risk_flags_rules"]
    ex = cfg["extension_penalty"]
    hi_lo = ex["flag_ramp"]["HIGH_EXTENSION"]
    lc = rf["leader_climax"]

    def num(c: str) -> pd.Series:
        return pd.to_numeric(df[c], errors="coerce") if c in df.columns \
            else pd.Series(np.nan, index=df.index)

    def flag(c: str) -> pd.Series:
        return df[c].fillna(False).astype(bool) if c in df.columns \
            else pd.Series(False, index=df.index)

    ext = num("extension_score")
    tests = [
        ("HIGH_EXTENSION", ext.notna() & (ext / 100.0 >= _fv(hi_lo[0]))),
        ("HIGH_CROWDING", df["crowding_class"].isin(["HIGH", "EXTREME"])),
        ("VOLUME_SPIKE", flag("extreme_volume")),
        ("LEADER_CLIMAX", (num("ret_5") >= _fv(lc["ret_5_min"]))
                          & (num("near_gap_120") <= _fv(lc["near_gap_120_max"]))),
        ("LOW_BREADTH_SUPPORT", num("breadth") <= _fv(rf["low_breadth_support_max"])),
        ("WEAK_CORE_SUPPORT", num("core_breadth") <= _fv(rf["weak_core_support_max"])),
        ("THEME_COOLING", df["theme_phase"].isin(["COOLING", "DETERIORATING", "EXITING"])),
        ("THEME_DIVERGENCE", num("top5_concentration") >= _fv(rf["theme_divergence_min_top5_concentration"])),
        ("LOW_MEMBERSHIP_CONFIDENCE",
         num("membership_confidence") < _fv(cfg["membership_thresholds"]["confidence_min"])),
        ("FUNDAMENTAL_WEAK", flag("fundamental_weak")),
        ("DATA_INCOMPLETE",
         num("data_quality") < 100.0 * _fv(cfg["data_rules"]["data_quality_invalid_threshold"])),
    ]
    out = pd.Series("", index=df.index, dtype=object)
    for name, mask in tests:
        if name not in RISK_FLAGS:
            continue
        hit = mask.fillna(False).astype(bool)
        if hit.any():
            out.loc[hit] = (out.loc[hit] + "|" + name).str.lstrip("|")
    return out


def build_reason(row, cfg: dict) -> list:
    """需求 §二十五：结构化原因，禁止「看好/有潜力/值得买」等主观措辞。"""
    rs = [f"{row['sector_id']}{row['sector_name']}处于{row['theme_phase']}"]
    rs.append(f"{row['membership_type']}成员（confidence={s4(row['membership_confidence'])}）")
    rl = row["theme_role"]
    if rl not in ("UNKNOWN", "CONCEPT_ONLY"):
        rs.append(ROLE_LABEL.get(rl, rl))
    dtp = row["diffusion_type"]
    if dtp in DIFFUSION_LABEL:
        rs.append(f"主题正在{DIFFUSION_LABEL[dtp]}")
    if _num(row.get("rel_ret_5_improvement"), 0.0) >= cfg["diffusion_thresholds"]["stock_signal"]["stock_rel_ret_5_improvement_min"]:
        rs.append("个股相对主题强度改善")
    if row["crowding_class"] in ("LOW", "MEDIUM"):
        rs.append(f"拥挤度{row['crowding_class']}")
    if row["price_extension"] in ("LOW", "MEDIUM"):
        rs.append("当前价格扩张尚未极端")
    if bool(row.get("_is_pullback", False)):
        rs.append("主题内健康调整、量能收缩且趋势未破")
    if bool(row.get("industry_conflict", False)):
        rs.append("行业分类与主题行业关键词不一致（已降级）")
    return rs


def s4(x) -> str:
    x = _fv(x)
    return "NA" if math.isnan(x) else f"{round(x, 4):g}"


def assign_status(df: pd.DataFrame, cfg: dict, pollution: pd.DataFrame) -> pd.Series:
    """需求 §二十四：CANDIDATE / WATCH / EXCLUDE 三态。"""
    st = cfg["status_thresholds"]
    order = cfg["crowding_thresholds"]["class_order"]
    out = pd.Series("WATCH", index=df.index, dtype=object)
    score = pd.to_numeric(df["candidate_score"], errors="coerce")
    out = out.mask(score < _fv(st["watch_min_score"]), "EXCLUDE")
    out = out.mask(score >= _fv(st["candidate_min_score"]), "CANDIDATE")
    # CANDIDATE 附加条件
    cond_ok = (
        df["membership_type"].isin(st["candidate_membership"])
        & (df["theme_role"].map(cfg["industry_role"]["role_scores"]).astype(float) >= _fv(st["candidate_role_min"]))
        & (pd.to_numeric(df["fundamental_quality"], errors="coerce") >= _fv(st["candidate_fundamental_min"]))
        & (pd.to_numeric(df["structure_quality_precheck"], errors="coerce") >= _fv(st["candidate_structure_min"]))
        & (df["crowding_class"].map(lambda c: order.index(c) if c in order else 99) <= order.index(st["candidate_crowding_max"]))
        & df["is_active_bucket"].fillna(False)
        & (~df["trend_broken"].fillna(True))
    )
    out = out.mask(out.eq("CANDIDATE") & (~cond_ok), "WATCH")
    # P1-05：跨主题污染分级升级为执行约束（需求 §十六），不再只出现在报告里
    plv = cfg["cross_theme_bonus"]["pollution_levels"]
    pconf = pd.to_numeric(df["primary_theme_confidence"], errors="coerce")
    out = out.mask(out.eq("CANDIDATE") & df["cross_theme_pollution_flag"].eq("HIGH"),
                   str(plv["HIGH"]["max_status"]))
    out = out.mask(out.eq("CANDIDATE") & df["cross_theme_pollution_flag"].eq("MEDIUM")
                   & (pconf < _fv(plv["MEDIUM"]["min_primary_confidence"])),
                   str(plv["MEDIUM"]["max_status"]))
    # 需求 §十四/§十七：超出 max_active_candidate_themes 的主题只作 context，不参与候选计分
    out = out.mask(out.eq("CANDIDATE") & df["is_context_theme"].fillna(False),
                   str(cfg["cross_theme_bonus"]["context_theme_policy"]["max_status"]))
    # EXCLUDE 规则
    out = out.mask(df["theme_phase"].eq("EXITING"), "EXCLUDE")
    out = out.mask(pd.to_numeric(df["data_quality"], errors="coerce") < 100.0 * cfg["data_rules"]["data_quality_invalid_threshold"], "EXCLUDE")
    out = out.mask(df["theme_role"].eq(cfg["pollution_rules"]["concept_only_role"]), "EXCLUDE")
    out = out.mask(df["extreme_price"].fillna(False) | df["extreme_volume"].fillna(False), "EXCLUDE")
    out = out.mask(~df["membership_type"].isin(cfg["membership_thresholds"]["watch_types"]), "EXCLUDE")
    # 风险主题阶段默认不建新增候选（仅结构性强可入 WATCH）
    risk_policy = cfg["theme_opportunity"]["risk_phase_policy"]
    if not risk_policy["allow_new_candidates"]:
        risk_keep = df["candidate_type"].eq(risk_policy["exception_candidate_type"])
        out = out.mask(df["is_risk_bucket"].fillna(False) & (~risk_keep), "EXCLUDE")
    # 污染命中（CONCEPT_POLLUTION / LOW_MEMBERSHIP_CONFIDENCE 等）
    if len(pollution):
        hard = pollution[pollution["action"].eq("EXCLUDE")]
        hk = (hard["trade_date"].astype(str) + "|" + hard["ts_code"].astype(str)
              + "|" + hard["sector_id"].astype(str))
        keys = (df["trade_date"].astype(str) + "|" + df["ts_code"].astype(str)
                + "|" + df["sector_id"].astype(str))
        out = out.mask(keys.isin(set(hk)), "EXCLUDE")
    return out


# ────────────────────────────────────────────────────────────────────────────
# 污染登记（需求 §二十一、§二十）
# ────────────────────────────────────────────────────────────────────────────

def build_pollution(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §二十一 / §二十：污染登记表。

    登记范围 = 「硬性排除」+「证据与成员层级冲突」；非核心层级（THEMATIC/OBSERVATION）
    由 §五 的层级规则统一承接，不在此重复登记（否则污染表被 82% 的整体成员关系淹没）。
    每行按 pollution_rules.priority_order 只登记最高优先级的 1 条。
    """
    pr = cfg["pollution_rules"]
    amap = pr["action_map"]
    conf_min = _fv(cfg["membership_thresholds"]["confidence_min"])
    dq_min = 100.0 * _fv(cfg["data_rules"]["data_quality_invalid_threshold"])

    ptype = pd.Series("", index=df.index, dtype=object)
    why = pd.Series("", index=df.index, dtype=object)

    def take(name: str, mask: pd.Series, reason: pd.Series) -> None:
        hit = mask.fillna(False).astype(bool) & ptype.eq("")
        if hit.any():
            ptype.loc[hit] = name
            why.loc[hit] = reason.loc[hit].astype(str)

    conf = pd.to_numeric(df["membership_confidence"], errors="coerce")
    dq = pd.to_numeric(df["data_quality"], errors="coerce")
    r5 = pd.to_numeric(df.get("ret_5"), errors="coerce").round(4).astype(str)
    dm20 = pd.to_numeric(df.get("distance_ma20"), errors="coerce").round(4).astype(str)
    vr5 = pd.to_numeric(df.get("volume_ratio_5"), errors="coerce").round(4).astype(str)
    to5 = pd.to_numeric(df.get("turnover_5"), errors="coerce").round(4).astype(str)
    role = df["theme_role"].astype(str)
    mm = df["mapping_method"].astype(str) if "mapping_method" in df.columns else pd.Series("", index=df.index)

    # 按 priority_order 逐条判定；未命中任何规则的行不进入污染表
    order = [t for t in pr["priority_order"] if t in pr["types"]]
    for name in order:
        if name == "THEME_EXITING":
            take(name, df["theme_phase"].isin(pr["theme_exiting_phases"]),
                 "主题阶段=" + df["theme_phase"].astype(str))
        elif name == "LOW_MEMBERSHIP_CONFIDENCE":
            take(name, conf < conf_min,
                 "membership_confidence=" + conf.round(4).astype(str) + " < " + str(conf_min))
        elif name == "CONCEPT_ONLY":
            take(name, role.eq(pr["concept_only_role"]) & df["membership_type"].isin(
                pr["concept_only_membership_types"]),
                "成员层级=" + df["membership_type"].astype(str)
                + " 但 mapping_method=" + mm + " 无产业/产品级证据")
        elif name == "OVER_EXTENSION":
            take(name, df["extreme_price"].fillna(False),
                 "ret_5=" + r5 + " distance_ma20=" + dm20)
        elif name == "VOLUME_SPIKE":
            take(name, df["extreme_volume"].fillna(False),
                 "volume_ratio_5=" + vr5 + " turnover_5=" + to5)
        elif name == "DATA_INVALID":
            take(name, dq < dq_min, "data_quality=" + dq.round(4).astype(str))
        elif name == "CONCEPT_POLLUTION":
            take(name, mm.isin(pr["concept_pollution_mapping_methods"]),
                 "mapping_method=" + mm + " 仅概念名称/关键词式归属（需求 §六 禁止）")
        elif name == "INDUSTRY_CONFLICT":
            take(name, df["industry_conflict"].fillna(False),
                 "industry=" + df["industry"].astype(str) + " 与主题行业关键词/细分环节无交集（降级 WATCH）")

    hit = ptype.ne("")
    out = pd.DataFrame({
        "trade_date": df.loc[hit, "trade_date"], "ts_code": df.loc[hit, "ts_code"],
        "name": df.loc[hit, "name"], "sector_id": df.loc[hit, "sector_id"],
        "sector_name": df.loc[hit, "sector_name"], "membership_type": df.loc[hit, "membership_type"],
        "pollution_type": ptype.loc[hit], "reason": why.loc[hit],
    })
    out["action"] = out["pollution_type"].map(amap).fillna("EXCLUDE")
    log.info("污染登记：%d 行（评估 %d 行）", len(out), len(df))
    if len(out):
        log.info("污染类型分布：\n%s", out["pollution_type"].value_counts().to_string())
    return out.reindex(columns=POLLUTION_COLS).reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# 主流水线
# ────────────────────────────────────────────────────────────────────────────

def build_pipeline(cfg: dict, meta: dict, membership: pd.DataFrame, panel: pd.DataFrame,
                   stock_long: pd.DataFrame, stock_basic: pd.DataFrame) -> tuple:
    """主题层 → 成员过滤 → 个股评分 → 候选池。"""
    st = cfg["status_thresholds"]
    uni = cfg["universe_rules"]
    # 跨主题股票集合（≥1 个 CORE/PRIMARY 归属）
    cp_ids = set(membership.loc[membership["membership_type"].isin(
        cfg["membership_thresholds"]["candidate_types"]), "ts_code"].unique())

    mb = membership.copy()
    # membership CSV 不含行业字段；产业归属判定（§六 行业分类层）必须补上 stock_basic.industry
    sb_ind = dict(zip(stock_basic["ts_code"], stock_basic["industry"].fillna("")))
    mb["industry"] = mb["ts_code"].map(sb_ind).fillna("")
    mb["membership_quality"] = compute_membership_quality(mb, cfg)
    role_df = detect_theme_role(mb, meta, cfg, cp_ids)
    mb["theme_role"] = role_df["theme_role"]
    mb["role_evidence"] = role_df["role_evidence"]
    mb["industry_conflict"] = detect_industry_conflict(mb, meta, cfg)
    log.info("主题角色分布：\n%s", mb["theme_role"].value_counts().to_string())
    log.info("角色证据层级分布：\n%s", mb["role_evidence"].value_counts().to_string())
    log.info("行业冲突标记：%d / %d 行成员", int(mb["industry_conflict"].sum()), len(mb))

    # 主题层
    theme = compute_theme_layer(panel, stock_long, membership, cfg)
    # 只在有效主题日上生成候选（DORMANT 无主题机会；DATA_INVALID 排除）
    elig = theme[(~theme["theme_phase"].isin(["DORMANT", "DATA_INVALID"]))
                 & theme["warmup_ready"] & (~theme["data_invalid"])].copy()
    elig_dates = set(elig["trade_date"].unique())
    log.info("有效主题日：%d 行 / %d 主题 / %d 日", len(elig), elig["sector_id"].nunique(), len(elig_dates))

    # 成员 × 有效主题日
    pairs = mb.merge(elig[["trade_date", "sector_id"]], on="sector_id", how="inner")
    pairs = pairs[pairs["trade_date"].isin(elig_dates)]
    # point-in-time 成员过滤（需求 §三十）：与 compute_theme_layer 共用唯一 accessor（P0-01）
    pairs = apply_pit_membership(pairs)
    log.info("成员×主题日展开：%d 行", len(pairs))

    # 合并个股结构 + 财务
    df = pairs.merge(stock_long, on=["trade_date", "ts_code"], how="left")
    # 主题层字段
    tcols = ["trade_date", "sector_id", "sector_name", "rotation_group", "theme_phase",
             "seos_score", "theme_health", "breadth", "breadth_delta_5", "core_breadth",
             "core_breadth_delta_5", "primary_breadth_delta_3", "secondary_breadth_delta_5",
             "top5_concentration", "theme_ret_1", "theme_ret_3", "theme_ret_5", "theme_ret_10",
             "theme_ret_20", "theme_opportunity_score", "theme_bucket", "is_active_bucket",
             "is_risk_bucket", "diffusion_type", "diffusion_pattern", "diffusion_stage",
             "diffusion_score_theme", "full_breadth_extra",
             "calibrated_opportunity_base", "calibration_status"]
    df = df.merge(theme[tcols], on=["trade_date", "sector_id"], how="left", suffixes=("", "_th"))
    # 合并后同名列只保留主题侧真值（Step 3/4 面板为准），避免静默取到成员表副本
    for _c in list(df.columns):
        if _c.endswith("_th"):
            _base = _c[:-3]
            df[_base] = df[_c].where(df[_c].notna(), df[_base]) if _base in df.columns else df[_c]
            df = df.drop(columns=[_c])
    df["theme_seos"] = df["seos_score"]
    ind_map = dict(zip(stock_basic["ts_code"], stock_basic["industry"]))
    nm_map = dict(zip(stock_basic["ts_code"], stock_basic["name"]))
    df["industry"] = df["ts_code"].map(ind_map).fillna(df.get("industry", ""))
    df["name"] = df["ts_code"].map(nm_map).fillna(df["stock_name"])
    df["list_date"] = df["ts_code"].map(dict(zip(stock_basic["ts_code"], stock_basic["list_date"])))
    df["list_status"] = df["ts_code"].map(dict(zip(stock_basic["ts_code"], stock_basic["list_status"])))

    df = compute_fundamental_quality(df, cfg)
    df = compute_structure_quality(df, cfg)
    df = compute_relative_strength(df, cfg)
    df = compute_extension(df, cfg)
    df = compute_crowding(df, cfg)
    df = compute_diffusion_signal(df, cfg)

    # universe 过滤（需求 §二十.8）
    listed = pd.to_numeric(df["list_date"], errors="coerce")
    td_int = pd.to_numeric(df["trade_date"], errors="coerce")
    df["listed_days"] = td_int - listed
    name_up = df["name"].astype(str).str.upper()
    st_hit = name_up.str.contains("ST", na=False) | df["name"].astype(str).str.contains("退", na=False)
    price_ok = pd.to_numeric(df["price_hist_days"], errors="coerce") >= _fv(uni["min_price_history_days"])
    ok_st = (~st_hit) if uni["exclude_st"] else pd.Series(True, index=df.index)
    ok_trade = df["traded_today"].fillna(False).astype(bool) if uni["exclude_suspended"] \
        else pd.Series(True, index=df.index)
    ok_list = pd.to_numeric(df["listed_days"], errors="coerce") >= _fv(uni["min_calendar_days_listed"])
    ok_hist = price_ok.fillna(False)
    df["universe_ok"] = (ok_st & ok_trade & ok_list & ok_hist)
    log.info("universe 过滤：通过 %d / %d 行（剔除 ST=%d 停牌=%d 上市不足%d日=%d 价格史不足%d日=%d）",
             int(df["universe_ok"].sum()), len(df), int((~ok_st).sum()), int((~ok_trade).sum()),
             int(uni["min_calendar_days_listed"]), int((~ok_list).sum()),
             int(uni["min_price_history_days"]), int((~ok_hist).sum()))
    # 数据质量（价格充分性 / 财务覆盖 / 成员完整性）
    mq = pd.to_numeric(df["membership_quality"], errors="coerce")
    df["data_quality"] = (50.0 * df["universe_ok"].astype(float)
                          + 0.25 * pd.to_numeric(df["fundamental_data_quality"], errors="coerce").fillna(0.0)
                          + 0.25 * mq.fillna(0.0)).clip(0.0, 100.0)

    # 类型 → 分数 → 状态
    df["candidate_type"] = classify_candidate_type(df, cfg)
    df["_is_pullback"] = df["candidate_type"].eq("THEME_PULLBACK")
    cw = cfg["candidate_weights"]
    raw = (
        cw["theme_opportunity"] * pd.to_numeric(df["theme_opportunity_score"], errors="coerce").fillna(0.0)
        + cw["membership_quality"] * pd.to_numeric(df["membership_quality"], errors="coerce").fillna(0.0)
        + cw["industry_role"] * df["theme_role"].map(cfg["industry_role"]["role_scores"]).astype(float).fillna(0.0)
        + cw["fundamental_quality"] * pd.to_numeric(df["fundamental_quality"], errors="coerce").fillna(0.0)
        + cw["relative_theme_strength"] * pd.to_numeric(df["relative_theme_strength"], errors="coerce").fillna(0.0)
        + cw["structure_quality"] * pd.to_numeric(df["structure_quality_precheck"], errors="coerce").fillna(0.0)
        + cw["diffusion_signal"] * pd.to_numeric(df["theme_diffusion_score"], errors="coerce").fillna(0.0)
        + cw["data_quality"] * pd.to_numeric(df["data_quality"], errors="coerce").fillna(0.0)
    )
    df["raw_candidate_score"] = raw.clip(0.0, 100.0)
    # P1-05：跨主题污染分级口径 = 股票 PIT 成员表主题总数（需求 §十二 口径：1-3 LOW / 4-5 MEDIUM / >=6 HIGH）
    theme_cnt_map = membership.groupby("ts_code")["sector_id"].nunique().to_dict()
    df["theme_overlap_count"] = df["ts_code"].map(theme_cnt_map).fillna(0).astype(int)
    df = compute_cross_theme(df, cfg)
    df["candidate_score"] = (raw
                             - pd.to_numeric(df["extension_penalty"], errors="coerce").fillna(0.0)
                             + pd.to_numeric(df["cross_theme_bonus"], errors="coerce").fillna(0.0)
                             - pd.to_numeric(df["cross_theme_score_penalty"], errors="coerce").fillna(0.0)
                             ).clip(0.0, 100.0)
    # 行业冲突降级（需求 §二十.3）
    cap = _fv(cfg["industry_role"]["conflict_check"]["conflict_score_cap"])
    df.loc[df["industry_conflict"].fillna(False) & (df["candidate_score"] > cap), "candidate_score"] = cap

    pollution = build_pollution(df, cfg)
    df["candidate_status"] = assign_status(df, cfg, pollution)
    df.loc[~df["universe_ok"].fillna(False), "candidate_status"] = "EXCLUDE"
    df["risk_flags"] = build_risk_flags(df, cfg)
    # candidate_reason 只对进入候选池的行生成（需求 §二十五）；被排除行不写主观措辞
    sel = df["candidate_status"].isin(["CANDIDATE", "WATCH"])
    reasons = pd.Series("", index=df.index, dtype=object)
    if sel.any():
        reasons.loc[sel] = ["|".join(build_reason(r, cfg)) for _, r in df.loc[sel].iterrows()]
    df["candidate_reason"] = reasons
    df["industry_role"] = df["theme_role"]
    _log_candidate_diagnostics(df, cfg)
    return df, theme, pollution


def _log_candidate_diagnostics(df: pd.DataFrame, cfg: dict) -> None:
    """排除原因诊断：确认候选池规模由哪一层收紧（非调参，仅观测）。"""
    st = cfg["status_thresholds"]
    order = cfg["crowding_thresholds"]["class_order"]
    tier = df["membership_type"].isin(cfg["membership_thresholds"]["watch_types"])
    sc = pd.to_numeric(df["candidate_score"], errors="coerce")
    sub = sc[tier]
    log.info("候选层级行：%d / %d；candidate_score p50=%.1f p75=%.1f p90=%.1f max=%.1f",
             int(tier.sum()), len(df), sub.quantile(0.50), sub.quantile(0.75),
             sub.quantile(0.90), sub.max())
    log.info("候选状态分布：\n%s", df["candidate_status"].value_counts().to_string())
    ex = df[tier & df["candidate_status"].eq("EXCLUDE")]
    if len(ex):
        crowd_rank = ex["crowding_class"].map(
            lambda c: order.index(c) if c in order else 99)
        checks = {
            "score<watch_min": sc.loc[ex.index] < _fv(st["watch_min_score"]),
            "structure<min": pd.to_numeric(ex["structure_quality_precheck"], errors="coerce")
                             < _fv(st["candidate_structure_min"]),
            "fundamental<min": pd.to_numeric(ex["fundamental_quality"], errors="coerce")
                               < _fv(st["candidate_fundamental_min"]),
            "trend_broken": ex["trend_broken"].fillna(True).astype(bool),
            "crowding>max": crowd_rank > order.index(st["candidate_crowding_max"]),
            "not_active_bucket": ~ex["is_active_bucket"].fillna(False).astype(bool),
            "risk_bucket": ex["is_risk_bucket"].fillna(False).astype(bool),
        }
        log.info("EXCLUDE 命中次数（tier 内 %d 行，可重叠）：%s",
                 len(ex), {k: int(v.sum()) for k, v in checks.items()})


# ────────────────────────────────────────────────────────────────────────────
# 输出（需求 §三十四 ~ §三十七）
# ────────────────────────────────────────────────────────────────────────────

def _fmt_df(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = df.reindex(columns=[c for c in cols if c in df.columns]).copy()
    for c in out.columns:
        if c in ("trade_date", "ts_code", "name", "sector_id", "sector_name", "rotation_group",
                 "theme_phase", "membership_type", "theme_role", "candidate_type",
                 "candidate_status", "risk_flags", "candidate_reason", "price_extension",
                 "volume_extension", "crowding_class", "diffusion_type", "industry",
                 "primary_theme", "secondary_themes", "data_quality_label", "role_evidence",
                 "diffusion_pattern", "diffusion_stage", "calibration_status", "primary_theme_id",
                 "cross_theme_pollution_flag", "multi_theme_crowding_flag"):
            continue
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def select_pool(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """需求 §二十二：单主题限额（每类型 ≤5，单主题 ≤12），报告只展示 TOP N。"""
    lim = cfg["selection_limits"]
    pool = df[df["candidate_status"].isin(["CANDIDATE", "WATCH"])].copy()
    if pool.empty:
        return pool
    pool["_type_rank"] = pool.groupby(["trade_date", "sector_id", "candidate_type"], sort=False)[
        "candidate_score"].rank(method="first", ascending=False)
    pool = pool[pool["_type_rank"] <= int(lim["per_type_per_theme"])]
    fill_rank = {t: i for i, t in enumerate(lim["fill_order"])}
    pool["_fill"] = pool["candidate_type"].map(fill_rank).fillna(99)
    pool["_status_rank"] = (pool["candidate_status"] != "CANDIDATE").astype(int)
    pool = pool.sort_values(["trade_date", "sector_id", "_status_rank", "_fill", "candidate_score"],
                            ascending=[True, True, True, True, False], kind="mergesort")
    pool["_theme_rank"] = pool.groupby(["trade_date", "sector_id"], sort=False).cumcount() + 1
    pool = pool[pool["_theme_rank"] <= int(lim["per_theme_total"])]
    return pool.drop(columns=["_type_rank", "_fill", "_status_rank", "_theme_rank"])


def stock_payload(r, cfg: dict) -> dict:
    return {
        "ts_code": r["ts_code"], "name": r["name"],
        "theme_id": r["sector_id"], "theme_role": r["theme_role"],
        "membership_type": r["membership_type"],
        "membership_confidence": b_round(r["membership_confidence"]),
        "candidate_type": r["candidate_type"],
        "theme_candidate_score": b_round(r["candidate_score"], 2),
        "fundamental_quality": b_round(r["fundamental_quality"], 2),
        "structure_quality_precheck": b_round(r["structure_quality_precheck"], 2),
        "relative_theme_strength": b_round(r["relative_theme_strength"], 2),
        "diffusion_score": b_round(r["theme_diffusion_score"], 2),
        "crowding_score": b_round(r["crowding_score"], 2),
        "crowding_class": r["crowding_class"],
        "extension_penalty": b_round(r["extension_penalty"], 2),
        "price_extension": r["price_extension"],
        "volume_extension": r["volume_extension"],
        "ret_1": b_round(r.get("ret_1")), "ret_5": b_round(r.get("ret_5")),
        "ret_20": b_round(r.get("ret_20")),
        "distance_ma20": b_round(r.get("distance_ma20")),
        "distance_ma60": b_round(r.get("distance_ma60")),
        "volume_ratio_5": b_round(r.get("volume_ratio_5")),
        "turnover_5": b_round(r.get("turnover_5")),
        "candidate_status": r["candidate_status"],
        "candidate_reason": [x for x in str(r["candidate_reason"]).split("|") if x],
        "risk_flags": [x for x in str(r["risk_flags"]).split("|") if x],
        "primary_theme": r.get("primary_theme"),
        "secondary_themes": [x for x in str(r.get("secondary_themes", "")).split("|") if x],
    }


def build_theme_payload(theme_row, stocks: list, cfg: dict) -> dict:
    return {
        "theme_id": theme_row["sector_id"],
        "theme_name": theme_row["sector_name"],
        "rotation_group": theme_row["rotation_group"],
        "phase": theme_row["theme_phase"],
        "seos": b_round(theme_row["seos_score"], 2),
        "health": b_round(theme_row["theme_health"], 2),
        "theme_opportunity_score": b_round(theme_row["theme_opportunity_score"], 2),
        "bucket": theme_row["theme_bucket"],
        "diffusion_type": theme_row["diffusion_type"],
        "diffusion_state": {
            "breadth": b_round(theme_row["breadth"]),
            "breadth_delta_5": b_round(theme_row["breadth_delta_5"]),
            "core_breadth": b_round(theme_row["core_breadth"]),
            "core_breadth_delta_5": b_round(theme_row["core_breadth_delta_5"]),
            "primary_breadth_delta_3": b_round(theme_row["primary_breadth_delta_3"]),
            "secondary_breadth_delta_5": b_round(theme_row["secondary_breadth_delta_5"]),
            "theme_ret_1": b_round(theme_row["theme_ret_1"]),
            "theme_ret_5": b_round(theme_row["theme_ret_5"]),
            "top5_concentration": b_round(theme_row["top5_concentration"]),
        },
        "candidate_count": len(stocks),
        "top_candidates": stocks,
    }


def write_outputs(df: pd.DataFrame, theme: pd.DataFrame, pollution: pd.DataFrame,
                  pool: pd.DataFrame, cfg: dict, trade_date: str, args,
                  requested_date: str = "", date_note: str = "") -> None:
    # --date 指定时，按「生效日」而非「请求日」过滤明细表，避免回退前后口径不一致；
    # --full（未指定 --date）保持全窗口 merge 写出口径。
    date_filter = trade_date if args.date else None
    daily = _fmt_df(df, CANDIDATE_DAILY_COLS)
    for c in ("ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "distance_ma20", "distance_ma60",
              "distance_ma120", "volume_ratio_5", "volume_ratio_20", "turnover_5", "turnover_20",
              "high_20", "high_60", "high_120", "drawdown_20", "drawdown_60", "drawdown_120",
              "candidate_score", "fundamental_quality", "structure_quality_precheck",
              "relative_theme_strength", "theme_diffusion_score", "crowding_score",
              "extension_penalty", "data_quality", "theme_seos", "theme_health",
              "theme_opportunity_score", "cross_theme_bonus", "membership_confidence",
              "membership_weight", "price_extension_score", "volume_extension_score",
              "trend_quality", "industry_conflict"):
        if c in daily.columns:
            daily[c] = pd.to_numeric(daily[c], errors="coerce").round(6)
    daily = daily.sort_values(["trade_date", "sector_id", "ts_code"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(daily, OUT_CANDIDATE_DAILY)

    td = theme.copy()
    cnt = pool.groupby(["trade_date", "sector_id"], sort=False).size() if len(pool) else \
        pd.Series(dtype=int)
    td = td.merge(cnt.rename("candidate_count").reset_index(),
                  on=["trade_date", "sector_id"], how="left")
    td["candidate_count"] = pd.to_numeric(td["candidate_count"], errors="coerce").fillna(0).astype(int)
    tdd = td.reindex(columns=[c for c in DIFFUSION_COLS if c in td.columns]).copy()
    for c in tdd.columns:
        if c not in ("trade_date", "sector_id", "sector_name", "rotation_group", "theme_phase",
                     "theme_bucket", "diffusion_type", "diffusion_pattern", "diffusion_stage"):
            tdd[c] = pd.to_numeric(tdd[c], errors="coerce").round(6)
    tdd = tdd.sort_values(["trade_date", "sector_id"], kind="mergesort").reset_index(drop=True)
    write_merge_csv(tdd, OUT_DIFFUSION, key_cols=("trade_date", "sector_id"))

    pol = pollution.copy()
    if date_filter:
        pol = pol[pol["trade_date"] == date_filter]
    write_merge_csv(pol, OUT_POLLUTION, key_cols=("trade_date", "ts_code", "sector_id", "pollution_type"))

    # §二十八 跨主题重复候选
    multi = pool.groupby(["trade_date", "ts_code"], sort=False)["sector_id"].nunique()
    dup_keys = multi[multi > 1].reset_index()[["trade_date", "ts_code"]]
    if len(dup_keys):
        d = pool.merge(dup_keys, on=["trade_date", "ts_code"], how="inner")
        rows = []
        for (dt_, code), g in d.groupby(["trade_date", "ts_code"], sort=False):
            g = g.sort_values("candidate_score", ascending=False)
            ids = g["sector_id"].tolist()
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    if a > b:
                        a, b = b, a
                    ra = g[g["sector_id"] == a].iloc[0]
                    rb = g[g["sector_id"] == b].iloc[0]
                    rows.append({
                        "trade_date": dt_, "ts_code": code, "name": ra["name"],
                        "theme_a": a, "theme_b": b,
                        "membership_a": ra["membership_type"], "membership_b": rb["membership_type"],
                        "primary_theme": ra["primary_theme"],
                        "overlap_reason": f"score_a={round(ra['candidate_score'], 2)};"
                                          f"score_b={round(rb['candidate_score'], 2)};"
                                          f"type_a={ra['candidate_type']};type_b={rb['candidate_type']}",
                    })
        ov = pd.DataFrame(rows)
    else:
        ov = pd.DataFrame(columns=["trade_date", "ts_code", "name", "theme_a", "theme_b",
                                   "membership_a", "membership_b", "primary_theme", "overlap_reason"])
    if date_filter and len(ov):
        ov = ov[ov["trade_date"] == date_filter]
    write_merge_csv(ov, OUT_OVERLAP, key_cols=("trade_date", "ts_code", "theme_a", "theme_b"))


def resolve_effective_date(pool: pd.DataFrame, trade_date: str) -> tuple:
    """请求日若无任何候选（上游 Step3/4 该日主题全部 DORMANT / RISK），
    显式回退到最近有候选的主题日，并在输出中标注 requested_date 与 date_note，
    绝不用「补造候选」的方式让报告显得非空。
    """
    if len(pool) and (pool["trade_date"] == trade_date).any():
        return trade_date, ""
    if len(pool):
        alt = str(pool["trade_date"].max())
        return alt, (f"请求日 {trade_date} 无主题机会（上游该日主题全部 DORMANT / RISK，"
                     f"需求 §四 规定默认不建新增候选）；已显式回退到最近有候选的主题日 {alt}")
    return trade_date, f"请求日 {trade_date} 在整个评估窗口内无任何候选"


def build_today_json(theme: pd.DataFrame, pool: pd.DataFrame, trade_date: str, cfg: dict,
                     requested_date: str = "", date_note: str = "") -> dict:
    """需求 §三十六：theme-first, stock-second。"""
    lim = int(cfg["selection_limits"]["report_top_per_theme"])
    t = theme[theme["trade_date"] == trade_date].sort_values(
        "theme_opportunity_score", ascending=False, kind="mergesort")
    p = pool[pool["trade_date"] == trade_date] if len(pool) else pool
    out = []
    seen_sector = set()
    for _, tr in t.iterrows():
        if tr["sector_id"] in seen_sector:
            continue
        seen_sector.add(tr["sector_id"])
        st = p[p["sector_id"] == tr["sector_id"]].sort_values("candidate_score", ascending=False) \
            if len(p) else p
        stocks = [stock_payload(r, cfg) for _, r in st.head(lim).iterrows()]
        if not stocks and not tr["is_active_bucket"]:
            continue
        out.append(build_theme_payload(tr, stocks, cfg))
    return {
        "version": cfg["version"],
        "layer": cfg["layer"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "requested_date": requested_date or trade_date,
        "date_note": date_note,
        "canonical_source": cfg["canonical_sources"]["theme_definition"],
        "config_source": os.path.basename(CFG_PATH),
        "not_a_buy_list": True,
        "notice": "本文件为主题 → 个股候选池（CANDIDATE/WATCH/EXCLUDE），不含任何交易决策；"
                  "不构成 BUY / 仓位 / 止损 / 目标价。",
        "theme_opportunities": out,
    }


def build_pool_json(df: pd.DataFrame, theme: pd.DataFrame, trade_date: str, cfg: dict,
                    requested_date: str = "", date_note: str = "") -> dict:
    """需求 §二十三：主题候选池分层（内部完整候选池，含 WATCH）。"""
    lim = int(cfg["selection_limits"]["per_theme_total"])
    t = theme[theme["trade_date"] == trade_date]
    out = []
    for _, tr in t.sort_values("theme_opportunity_score", ascending=False, kind="mergesort").iterrows():
        st = df[(df["trade_date"] == trade_date) & (df["sector_id"] == tr["sector_id"])
                & (df["candidate_status"].isin(["CANDIDATE", "WATCH"]))]
        st = st.sort_values(["candidate_status", "candidate_score"],
                            ascending=[True, False], kind="mergesort").head(lim)
        if st.empty:
            continue
        out.append(build_theme_payload(tr, [stock_payload(r, cfg) for _, r in st.iterrows()], cfg))
    return {
        "version": cfg["version"], "layer": cfg["layer"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "requested_date": requested_date or trade_date,
        "date_note": date_note,
        "canonical_source": cfg["canonical_sources"]["theme_definition"],
        "config_source": os.path.basename(CFG_PATH),
        "legacy_config_used": cfg["legacy_config_used"],
        "not_a_buy_list": True,
        "theme_candidate_pool": out,
    }


def build_top_json(theme: pd.DataFrame, pool: pd.DataFrame, trade_date: str, cfg: dict,
                   requested_date: str = "", date_note: str = "") -> tuple:
    """需求 §三十七：TOP THEMES → TOP CANDIDATE STOCKS（每主题 TOP 3-5）。"""
    lim = int(cfg["selection_limits"]["report_top_per_theme"])
    t = theme[theme["trade_date"] == trade_date]
    p = pool[pool["trade_date"] == trade_date] if len(pool) else pool
    items = []
    for _, tr in t.sort_values("theme_opportunity_score", ascending=False, kind="mergesort").iterrows():
        st = p[p["sector_id"] == tr["sector_id"]] if len(p) else p
        if len(st) == 0:
            continue
        st = st.sort_values(["candidate_status", "candidate_score"],
                            ascending=[True, False], kind="mergesort").head(lim)
        if st.empty:
            continue
        items.append(build_theme_payload(tr, [stock_payload(r, cfg) for _, r in st.iterrows()], cfg))
    obj = {
        "version": cfg["version"], "layer": cfg["layer"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "requested_date": requested_date or trade_date,
        "date_note": date_note,
        "not_a_buy_list": True,
        "top_themes": items,
    }
    lines = [f"# Step 5 — 主题 → 个股候选池 TOP（{trade_date}）", "",
             "> 本报告为候选池（CANDIDATE / WATCH），不含任何交易决策。", ""]
    for it in items:
        lines.append(f"## 【{it['theme_id']} {it['theme_name']}】")
        lines.append(f"- Phase: {it['phase']}  |  SEOS: {it['seos']}  |  Health: {it['health']}")
        lines.append(f"- Theme Opportunity: {it['theme_opportunity_score']}  |  Bucket: {it['bucket']}")
        lines.append(f"- Diffusion: {it['diffusion_type']}")
        for i, s in enumerate(it["top_candidates"], 1):
            lines.append(f"{i}. {s['name']}（{s['ts_code']}）")
            lines.append(f"   Role: {s['theme_role']}  |  Membership: {s['membership_type']} "
                         f"({s['membership_confidence']})")
            lines.append(f"   Candidate: {s['candidate_type']}  |  Status: {s['candidate_status']}  "
                         f"|  Score: {s['theme_candidate_score']}")
            lines.append(f"   Crowding: {s['crowding_class']} ({s['crowding_score']})  |  "
                         f"Extension: {s['price_extension']}/{s['volume_extension']} "
                         f"(penalty={s['extension_penalty']})")
            lines.append("   Reason:")
            for rs in s["candidate_reason"]:
                lines.append(f"   - {rs}")
            if s["risk_flags"]:
                lines.append(f"   Risk: {'|'.join(s['risk_flags'])}")
        lines.append("")
    return obj, "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# 回测（需求 §三十一）：验证「主题机会 → 主题内候选」是否真的带来信息增量
# ────────────────────────────────────────────────────────────────────────────

def _stat_block(vals: np.ndarray) -> dict:
    v = vals[~np.isnan(vals)]
    if v.size == 0:
        return {"n": 0}
    return {
        "n": int(v.size),
        "mean_return": float(np.mean(v)),
        "median_return": float(np.median(v)),
        "win_rate": float(np.mean(v > 0)),
        "top10_return": float(np.mean(np.sort(v)[-max(1, int(round(v.size * 0.10))):])),
        "bottom10_return": float(np.mean(np.sort(v)[:max(1, int(round(v.size * 0.10))):])),
    }


def build_backtest(df: pd.DataFrame, pool: pd.DataFrame, theme: pd.DataFrame,
                   cfg: dict) -> tuple:
    bt = cfg["backtest"]
    horizons = bt["horizons"]
    rng = random.Random(int(bt["random_seed"]))
    rows = []

    def fwd(frame, h):
        return pd.to_numeric(frame[f"_fwd_ret_{h}"], errors="coerce").values

    # 候选池 vs 主题全部成员 vs 随机成员
    for ctype in bt["types"]:
        sel = pool[pool["candidate_type"] == ctype]
        if sel.empty:
            continue
        for h in horizons:
            vals = fwd(sel, h)
            st = _stat_block(vals)
            st.update({"group": ctype, "horizon": h, "compare": "candidate_pool"})
            rows.append(st)

    for h in horizons:
        st = _stat_block(fwd(df, h))
        st.update({"group": "ALL_THEME_MEMBERS", "horizon": h, "compare": "theme_all_members"})
        rows.append(st)

    # 随机成员基线：对每个候选信号，在其主题内随机抽成员
    rand_vals = {h: [] for h in horizons}
    cand = pool[pool["candidate_type"].isin(bt["types"])][["trade_date", "sector_id"]].drop_duplicates()
    if len(cand) > int(bt["random_signals_cap"]):
        cand = cand.sample(n=int(bt["random_signals_cap"]),
                           random_state=int(bt["random_seed"])).reset_index(drop=True)
    by_theme = {k: v for k, v in df.groupby(["trade_date", "sector_id"], sort=False)}
    for _, c in cand.iterrows():
        g = by_theme.get((c["trade_date"], c["sector_id"]))
        if g is None or g.empty:
            continue
        for _ in range(int(bt["random_draws_per_signal"])):
            for h in horizons:
                v = fwd(g.sample(n=1, random_state=rng.randint(0, 10 ** 9)), h)
                if v.size and not np.isnan(v[0]):
                    rand_vals[h].append(float(v[0]))
    for h in horizons:
        st = _stat_block(np.array(rand_vals[h], dtype=float))
        st.update({"group": "RANDOM_MEMBERS", "horizon": h, "compare": "random_members"})
        rows.append(st)

    btdf = pd.DataFrame(rows)
    # 主题层映射有效性：候选池超额（相对主题等权）
    extra = []
    for h in horizons:
        base = _stat_block(fwd(df, h)).get("mean_return", float("nan"))
        for ctype in bt["types"]:
            sel = pool[pool["candidate_type"] == ctype]
            if sel.empty:
                continue
            m = _stat_block(fwd(sel, h)).get("mean_return", float("nan"))
            extra.append({"group": ctype, "horizon": h,
                          "pool_mean": m, "theme_mean": base,
                          "excess": (m - base) if not (math.isnan(m) or math.isnan(base)) else None})
    return btdf, pd.DataFrame(extra), {"all_members": _stat_block(fwd(df, 5))}


def render_backtest_md(btdf: pd.DataFrame, excess: pd.DataFrame, base: dict,
                       cfg: dict, trade_date: str,
                       requested_date: str = "", date_note: str = "") -> str:
    L = []
    A = L.append
    A("# Step 5 — Theme-to-Stock Candidate 回测（信息增量验证）")
    A("")
    A(f"- 数据日期：{trade_date}")
    if requested_date and requested_date != trade_date:
        A(f"- 请求日期：{requested_date}（{date_note}）")
    A(f"- 配置版本：{cfg['version']}（回测结果不用于反向调参，需求 §三十二）")
    A(f"- 前向期：{', '.join('T+' + str(h) for h in cfg['backtest_horizons'])} 交易日")
    A(f"- 复权口径：{cfg['backtest']['price_adjustment']}")
    A("- 对照：Theme Candidate Pool vs Theme All Members vs Random Members")
    A("- 重点不是追求最高收益，而是检验「主题机会 → 主题内候选股票」是否带来信息增量。")
    A("")
    A("## 一、分层统计")
    A("")
    A("| 分组 | 对照 | 前向期 | 样本 | 均值 | 中位数 | 胜率 | Top10% | Bottom10% |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for _, r in btdf.iterrows():
        A(f"| {r['group']} | {r['compare']} | T+{int(r['horizon'])} | {int(r.get('n', 0))} | "
          f"{r.get('mean_return', float('nan')):.4f} | {r.get('median_return', float('nan')):.4f} | "
          f"{r.get('win_rate', float('nan')):.3f} | {r.get('top10_return', float('nan')):.4f} | "
          f"{r.get('bottom10_return', float('nan')):.4f} |")
    A("")
    A("## 二、候选池相对主题等权的超额（信息增量）")
    A("")
    A("| 类型 | 前向期 | 候选池均值 | 主题全部成员均值 | 超额 |")
    A("| --- | --- | --- | --- | --- |")
    for _, r in excess.iterrows():
        ex = r["excess"]
        exs = "NA" if ex is None or (isinstance(ex, float) and math.isnan(ex)) else f"{ex:+.4f}"
        A(f"| {r['group']} | T+{int(r['horizon'])} | {r['pool_mean']:.4f} | "
          f"{r['theme_mean']:.4f} | {exs} |")
    A("")
    if excess["excess"].notna().any():
        pos = int((excess["excess"] > 0).sum())
        A(f"- 正向超额组数：{pos} / {int(excess['excess'].notna().sum())}")
        A("- 结论口径：仅当候选池在多数前向期优于主题等权时，才说明主题→个股映射带来了信息增量；"
          "否则应视为映射无效并复核配置（但不得为提高 T+5 胜率反复调参）。")
    else:
        A("- 样本不足，暂无法给出超额结论（如实标注，不做外推）。")
    A("")
    return "\n".join(L)


# ────────────────────────────────────────────────────────────────────────────
# 验证（需求 §三十九、§四十）
# ────────────────────────────────────────────────────────────────────────────

def run_validation(panel: pd.DataFrame, df: pd.DataFrame, theme: pd.DataFrame, pool: pd.DataFrame,
                   pollution: pd.DataFrame, meta: dict, membership: pd.DataFrame,
                   master_raw: dict, cfg: dict, trade_date: str,
                   stock_basic: pd.DataFrame = None, requested_date: str = "",
                   date_note: str = "", stock_long: pd.DataFrame = None) -> pd.DataFrame:
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "status": "PASS" if bool(ok) else "FAIL", "detail": str(detail)[:400]})

    # 1 配置
    add("CHECK_CONFIG_LOADED",
        cfg.get("version") and cfg.get("candidate_weights") and cfg.get("legacy_config_used") is False,
        f"version={cfg.get('version')}")
    # 2 主题真源
    unknown = sorted(set(theme["sector_id"].unique()) - set(meta.keys()))
    add("CHECK_THEME_IN_MASTER", not unknown, f"未知主题={unknown[:5]}")
    # 3 成员来源
    src_ok = set(df["ts_code"]).issubset(set(membership["ts_code"])) and \
        set(df["sector_id"]).issubset(set(membership["sector_id"]))
    add("CHECK_MEMBERSHIP_SOURCE", src_ok, "所有候选均来自 data/sector_membership.csv")
    # 4 legacy 隔离
    legacy_hit = [f for f in READ_FILES if os.path.basename(f) in LEGACY_NAMES]
    add("CHECK_LEGACY_ISOLATION", (not legacy_hit) and cfg["legacy_config_used"] is False,
        f"legacy 读取={legacy_hit}")
    # 5 无未来价格：对外输出不含任何前向收益列
    leak_cols = [c for c in CANDIDATE_DAILY_COLS + POLLUTION_COLS if c.startswith("_fwd")]
    add("CHECK_NO_FUTURE_PRICE", not leak_cols, f"前向列={leak_cols}")
    # 6 无未来财报
    fwd_fina = df[df["ann_date"].astype(str) > df["trade_date"].astype(str)]
    add("CHECK_NO_FUTURE_FUNDAMENTAL", len(fwd_fina) == 0,
        f"ann_date>trade_date 行数={len(fwd_fina)}")
    # 7 成员 PIT
    pit_bad = df[(~df["is_static"]) & (df["effective_date"] > df["trade_date"])]
    add("CHECK_MEMBERSHIP_PIT", len(pit_bad) == 0, f"effective_date>trade_date 行数={len(pit_bad)}")
    # 7b 主题层 PIT 口径（P0-01 自动测试：test_theme_return_pit）
    if stock_long is not None:
        pit_theme_ok, pit_theme_detail = test_theme_return_pit(panel, stock_long, membership, theme)
        add("CHECK_THEME_RETURN_PIT", pit_theme_ok, pit_theme_detail)
    # 8 状态枚举
    bad_status = sorted(set(df["candidate_status"].unique()) - set(CANDIDATE_STATUSES))
    add("CHECK_STATUS_ENUM", not bad_status, f"非法状态={bad_status}")
    # 9 类型枚举
    bad_type = sorted(set(df["candidate_type"].unique()) - set(CANDIDATE_TYPES))
    add("CHECK_CANDIDATE_TYPE_ENUM", not bad_type, f"非法类型={bad_type}")
    # 10 角色枚举
    bad_role = sorted(set(df["theme_role"].unique()) - set(THEME_ROLES))
    add("CHECK_ROLE_ENUM", not bad_role, f"非法角色={bad_role}")
    # 11 扩散类型枚举
    bad_diff = sorted(set(theme["diffusion_type"].unique()) - set(DIFFUSION_TYPES))
    add("CHECK_DIFFUSION_TYPE_ENUM", not bad_diff, f"非法扩散类型={bad_diff}")
    # 11b P1-03：扩散形态 / 扩散阶段枚举
    bad_pat = sorted(set(theme["diffusion_pattern"].unique()) - set(DIFFUSION_PATTERNS))
    bad_stg = sorted(set(theme["diffusion_stage"].unique()) - set(DIFFUSION_STAGES))
    add("CHECK_DIFFUSION_PATTERN_ENUM", (not bad_pat) and (not bad_stg),
        f"非法形态={bad_pat} 非法阶段={bad_stg}")
    # 11c P1-05：跨主题污染分级枚举 + 「报告→执行约束」落盘校验（需求 §十六）
    plv2 = cfg["cross_theme_bonus"]["pollution_levels"]
    bad_pol = sorted(set(df["cross_theme_pollution_flag"].unique()) - set(POLLUTION_LEVELS))
    _pc = pd.to_numeric(df["primary_theme_confidence"], errors="coerce")
    leak = df["candidate_status"].eq("CANDIDATE") & (
        df["cross_theme_pollution_flag"].eq("HIGH")
        | df["is_context_theme"].fillna(False)
        | (df["cross_theme_pollution_flag"].eq("MEDIUM")
           & (_pc < _fv(plv2["MEDIUM"]["min_primary_confidence"]))))
    add("CHECK_POLLUTION_ENFORCED", (not bad_pol) and int(leak.sum()) == 0,
        f"非法分级={bad_pol} 污染行泄漏进 CANDIDATE={int(leak.sum())}")
    # 12 分数范围
    s = pd.to_numeric(df["candidate_score"], errors="coerce")
    add("CHECK_SCORE_RANGE", bool(s.dropna().between(0, 100).all()),
        f"min={s.min():.3f} max={s.max():.3f}")
    # 13 universe：无 ST / 无停牌
    st_bad = df[df["name"].astype(str).str.upper().str.contains("ST", na=False)
                | df["name"].astype(str).str.contains("退", na=False)]
    add("CHECK_UNIVERSE_FILTER",
        len(st_bad[st_bad["candidate_status"].isin(["CANDIDATE", "WATCH"])]) == 0,
        f"ST 入选={len(st_bad[st_bad['candidate_status'].isin(['CANDIDATE', 'WATCH'])])}")
    # 14 去重
    key = ["trade_date", "ts_code", "sector_id"]
    add("CHECK_NO_DUPLICATE", not df.duplicated(key).any(),
        f"重复行={int(df.duplicated(key).sum())}")
    # 15 单主题限额
    if len(pool):
        mx = pool.groupby(["trade_date", "sector_id"]).size().max()
        tmx = pool.groupby(["trade_date", "sector_id", "candidate_type"]).size().max()
        ok = mx <= int(cfg["selection_limits"]["per_theme_total"]) and \
            tmx <= int(cfg["selection_limits"]["per_type_per_theme"])
    else:
        mx = tmx = 0
        ok = True
    add("CHECK_SELECTION_LIMITS", ok, f"单主题最多={mx} 单类型最多={tmx}")
    # 16 污染已输出
    add("CHECK_POLLUTION_LOGGED", set(pollution["pollution_type"]).issubset(set(POLLUTION_TYPES)),
        f"污染类型={sorted(set(pollution['pollution_type']))}")
    # 17 Extension：涨幅最大者不得自动成为该主题第一
    viol = 0
    if len(pool):
        for (d_, sid_), g in pool.groupby(["trade_date", "sector_id"]):
            if len(g) < 2:
                continue
            top = g.sort_values("candidate_score", ascending=False).iloc[0]
            max_ret = g.sort_values("ret_5", ascending=False).iloc[0]
            if (top["ts_code"] == max_ret["ts_code"]) and top["crowding_class"] in ("HIGH", "EXTREME"):
                viol += 1
    add("CHECK_EXTENSION_NOT_TOP1", viol == 0, f"违规主题日={viol}")
    # 18 扩散：强主题下不应全为 LEADER_ONLY
    strong = theme[(theme["theme_phase"].isin(["EMERGING", "CONFIRMING", "STRONG"]))]
    dt_set = set(strong["diffusion_type"])
    add("CHECK_DIFFUSION_IDENTIFIABLE",
        len(dt_set & {"CORE_EXPANSION", "PRIMARY_EXPANSION", "SECOND_LINE_EXPANSION",
                      "FULL_BREADTH_EXPANSION"}) > 0,
        f"强主题扩散类型={sorted(dt_set)}")
    # 19 输入覆盖
    add("CHECK_INPUT_COVERAGE",
        df["ret_5"].notna().mean() > 0.8 and df["fundamental_quality"].notna().mean() > 0.8,
        f"ret_5 覆盖={df['ret_5'].notna().mean():.3f} 基本面覆盖={df['fundamental_quality'].notna().mean():.3f}")
    # 20 禁止交易词：只扫描候选内容层，元数据里的否定式免责声明（"不构成 BUY / 仓位 / 止损"）不计入
    today = build_today_json(theme, pool, trade_date, cfg, requested_date, date_note)
    blob = json.dumps(today.get("theme_opportunities", []), ensure_ascii=False)
    banned = [w for w in ("买入价", "止损", "止盈", "目标价", "仓位", "BUY", "NO TRADE",
                          "TradeRank", "Execution Score", "HVT", "IGE", "F120") if w in blob]
    add("CHECK_NO_TRADING_TERMS", (not banned) and today.get("not_a_buy_list") is True,
        f"候选内容层命中禁用词={banned}；not_a_buy_list={today.get('not_a_buy_list')}")
    # 21 主题层无 DORMANT 候选
    dormant = df[df["theme_phase"].isin(["DORMANT", "DATA_INVALID"])
                 & df["candidate_status"].isin(["CANDIDATE", "WATCH"])]
    add("CHECK_NO_DORMANT_CANDIDATE", len(dormant) == 0, f"DORMANT/INVALID 入选={len(dormant)}")
    # 22 跨主题不重复加分
    bad_bonus = df[pd.to_numeric(df["cross_theme_bonus"], errors="coerce")
                   > _fv(cfg["cross_theme_bonus"]["max_points"]) + 1e-9]
    add("CHECK_CROSS_THEME_BONUS_CAP", len(bad_bonus) == 0,
        f"越界行={len(bad_bonus)} cap={cfg['cross_theme_bonus']['max_points']}")
    # 23 industry role 不依赖涨幅（涨幅最大者不得被自动判为 CORE_LEADER）
    auto_leader = df[(df["theme_role"].eq("CORE_LEADER"))
                     & (~df["membership_origin"].isin(["CORE_COMPANY"]))
                     & (~df["candidate_type"].eq("THEME_LEADER"))]
    add("CHECK_ROLE_NOT_BY_RETURN", len(auto_leader) <= len(df) * 0.5,
        f"CORE_LEADER 非 CORE_COMPANY 来源={len(auto_leader)}")
    # 24 需求 §四十四：收尾声明必须完整出现在审计报告中
    if stock_basic is not None:
        audit_txt = build_audit(df, theme, pool, pollution, cfg, meta, membership, None,
                                trade_date, stock_basic, requested_date, date_note)
        miss = [s for s in STEP5_STOP_NOTICE if s not in audit_txt]
        add("CHECK_STEP5_STOP_NOTICE", not miss, f"审计报告缺失声明={miss}")
    return pd.DataFrame(checks)


# ────────────────────────────────────────────────────────────────────────────
# 审计报告（需求 §四十一，20 项）
# ────────────────────────────────────────────────────────────────────────────

def build_audit(df: pd.DataFrame, theme: pd.DataFrame, pool: pd.DataFrame, pollution: pd.DataFrame,
                cfg: dict, meta: dict, membership: pd.DataFrame, val: pd.DataFrame,
                trade_date: str, stock_basic: pd.DataFrame,
                requested_date: str = "", date_note: str = "") -> str:
    L = []
    A = L.append
    A("# Step 5 — Theme-to-Stock Candidate Layer 审计报告")
    A("")
    A(f"1. **数据日期**：{trade_date}（主题面板 {theme['trade_date'].min()} → {theme['trade_date'].max()}）")
    if requested_date and requested_date != trade_date:
        A(f"    - **日期回退说明**：请求日 {requested_date}；{date_note}")
    A(f"2. **股票 universe**：成员覆盖股票 {membership['ts_code'].nunique()} 只；"
      f"通过 universe 过滤 {int(df['universe_ok'].sum())} 行；上市状态 L "
      f"{int((stock_basic['list_status'] == 'L').sum())} 只")
    A(f"3. **Theme 数量**：真源定义 {len(meta)} 个；面板出现 {theme['sector_id'].nunique()} 个")
    opp_cnt = theme[theme["is_active_bucket"]]["sector_id"].nunique()
    risk_cnt = theme[theme["is_risk_bucket"]]["sector_id"].nunique()
    A(f"4. **Opportunity Theme 数量**：{opp_cnt}（风险观察 {risk_cnt}）")
    n_cand = int((df["candidate_status"] == "CANDIDATE").sum())
    n_watch = int((df["candidate_status"] == "WATCH").sum())
    n_excl = int((df["candidate_status"] == "EXCLUDE").sum())
    A(f"5. **Candidate 股票数量**：{n_cand} 行（去重 "
      f"{df[df['candidate_status'] == 'CANDIDATE']['ts_code'].nunique()} 只）")
    A(f"6. **WATCH 数量**：{n_watch}")
    A(f"7. **EXCLUDE 数量**：{n_excl}")
    mt = df["membership_type"].value_counts().to_dict()
    A(f"8. **CORE / PRIMARY / SECONDARY 数量**：CORE={mt.get('CORE', 0)} / "
      f"PRIMARY={mt.get('PRIMARY', 0)} / SECONDARY={mt.get('SECONDARY', 0)} / "
      f"THEMATIC={mt.get('THEMATIC', 0)}")
    ct = df["candidate_type"].value_counts().to_dict()
    A(f"9. **THEME_DIFFUSION 数量**：{ct.get('THEME_DIFFUSION', 0)}")
    A(f"10. **THEME_PULLBACK 数量**：{ct.get('THEME_PULLBACK', 0)}")
    A(f"11. **高拥挤股票数量**：{int(df['crowding_class'].isin(['HIGH', 'EXTREME']).sum())}"
      f"（CANDIDATE/WATCH 中 "
      f"{int(df[df['candidate_status'].isin(['CANDIDATE', 'WATCH'])]['crowding_class'].isin(['HIGH', 'EXTREME']).sum())}）")
    A(f"12. **高扩张股票数量**：价格 {int(df['price_extension'].isin(['HIGH', 'EXTREME']).sum())} / "
      f"成交量 {int(df['volume_extension'].isin(['HIGH', 'EXTREME']).sum())}"
      f"（极端价 {int(df['extreme_price'].sum())} / 极端量 {int(df['extreme_volume'].sum())}）")
    A(f"13. **Pollution 数量**：{len(pollution)}；分布 "
      f"{pollution['pollution_type'].value_counts().to_dict() if len(pollution) else '{}'}")
    n_multi = int((df.groupby(['trade_date', 'ts_code'])['sector_id'].nunique() > 1).sum())
    A(f"14. **跨主题股票数量**：{n_multi}（同一交易日同一股票进入多个主题评估）")
    A(f"15. **缺失数据数量**：ret_5 缺失 {int(df['ret_5'].isna().sum())}；"
      f"基本面缺失（coverage<100%）{int((pd.to_numeric(df['fundamental_data_quality'], errors='coerce') < 100).sum())}；"
      f"data_quality<阈值 {int((pd.to_numeric(df['data_quality'], errors='coerce') < 60).sum())}")
    A("16. **历史回测结果**：见 output/sector_stock_candidate_backtest.md")
    A("17. **Future Leakage Check**：")
    A("    - 价格：全部结构指标只用 trade_date ≤ D 的行情；前向收益仅内部回测使用，不写入对外输出")
    A("    - 财报：merge_asof 按 ann_date ≤ trade_date 向后匹配，绝无未来财报")
    A("    - 主题状态：直接消费 Step 3/4 已截断的历史面板，无前视")
    A(f"    - 成员：静态映射视为恒定义；非静态行按 effective_date ≤ trade_date 过滤"
      f"（违规 {len(df[(~df['is_static']) & (df['effective_date'] > df['trade_date'])])} 行）")
    A("18. **Legacy Contamination Check**：")
    legacy_hit = [f for f in READ_FILES if os.path.basename(f) in LEGACY_NAMES]
    A(f"    - legacy_config_used = {cfg['legacy_config_used']}")
    A(f"    - 读取到的 legacy 文件：{legacy_hit if legacy_hit else '无'}")
    A(f"    - 忽略清单：{cfg['legacy_files_ignored']}")
    A("19. **异常样本**：")
    A(f"    - trend_broken 但入选：{int((df['trend_broken'].fillna(False) & df['candidate_status'].isin(['CANDIDATE', 'WATCH'])).sum())}")
    A(f"    - industry_conflict 入选：{int((df['industry_conflict'].fillna(False) & df['candidate_status'].isin(['CANDIDATE', 'WATCH'])).sum())}")
    A(f"    - 角色 UNKNOWN 入选：{int((df['theme_role'].eq('UNKNOWN') & df['candidate_status'].isin(['CANDIDATE', 'WATCH'])).sum())}")
    A("20. **Top Candidate 样本**：")
    top = pool[pool["trade_date"] == trade_date].sort_values("candidate_score", ascending=False).head(8) \
        if len(pool) else pool
    if len(top):
        A("")
        A("| 主题 | 股票 | 角色 | 成员 | 类型 | 分数 | 拥挤 | 扩张 | 状态 |")
        A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for _, r in top.iterrows():
            A(f"| {r['sector_id']} {r['sector_name']} | {r['name']}({r['ts_code']}) | {r['theme_role']} | "
              f"{r['membership_type']} | {r['candidate_type']} | {r['candidate_score']:.2f} | "
              f"{r['crowding_class']} | {r['price_extension']} | {r['candidate_status']} |")
    else:
        A("    - 该日无 CANDIDATE/WATCH")
    A("")
    if val is not None:
        n_pass = int((val["status"] == "PASS").sum())
        A(f"**验证结果：{n_pass}/{len(val)} 通过**")
        A("")
        A("| 检查 | 结果 | 说明 |")
        A("| --- | --- | --- |")
        for _, r in val.iterrows():
            A(f"| {r['check']} | {r['status']} | {r['detail']} |")
    A("")
    A("---")
    A("")
    for line in STEP5_STOP_NOTICE:
        A(line)
    return "\n".join(L)


# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Step 5 — Theme-to-Stock Candidate Layer（主题→个股候选池）")
    ap.add_argument("--full", action="store_true", help="全量重算并写出")
    ap.add_argument("--date", type=str, default=None, help="只重算指定交易日 YYYYMMDD")
    ap.add_argument("--validate", action="store_true", help="执行全项验证")
    ap.add_argument("--no-write", action="store_true", help="只计算不写文件（调试用）")
    args = ap.parse_args()

    if not (args.full or args.date or args.validate):
        ap.print_help()
        return 1

    cfg = load_json(CFG_PATH)
    if cfg.get("legacy_config_used") is not False:
        raise SystemExit("配置 legacy_config_used 必须为 false（需求 §二）")
    master_raw = load_json(MASTER_PATH)
    meta, group_names = load_master_meta()
    membership = load_membership()
    panel = load_theme_panel()
    stock_basic = load_stock_basic()

    horizons = list(cfg["backtest"]["horizons"])
    panel_dates = sorted(panel["trade_date"].unique())
    if args.date:
        if args.date not in panel_dates:
            raise SystemExit(f"指定日期 {args.date} 不在主题面板中")
    max_date = panel_dates[-1]
    codes = sorted(set(membership["ts_code"]))

    # 行情窗口：MA120 / high_120 / T+20 前向都需要足额前置与后置交易日
    all_dates = sorted(set(_read_all_trade_dates()))
    idx0 = all_dates.index(panel_dates[0]) if panel_dates[0] in all_dates else 0
    idx1 = all_dates.index(max_date) if max_date in all_dates else len(all_dates) - 1
    lo = max(0, idx0 - 200)
    hi = min(len(all_dates) - 1, idx1 + max(horizons) + 5)
    calendar = all_dates[lo:hi + 1]
    d_start, d_end = calendar[0], calendar[-1]

    px, dbb, af = load_prices(d_start, d_end, codes)
    fina = load_fundamentals(max_date, d_start)
    stock_long = compute_stock_features(px, dbb, af, calendar, panel_dates, horizons)
    fk = compute_fundamental_pit(stock_long[["trade_date", "ts_code"]], fina)
    stock_long = stock_long.merge(
        fk[["trade_date", "ts_code", "end_date", "ann_date", "roe", "roe_annualized",
            "grossprofit_margin", "or_yoy", "netprofit_yoy", "ocf_to_or", "debt_to_assets",
            "fina_months", "fina_stale_days"]],
        on=["trade_date", "ts_code"], how="left")

    df, theme, pollution = build_pipeline(cfg, meta, membership, panel, stock_long, stock_basic)
    pool = select_pool(df, cfg)
    # 需求 §四：COOLING / RISK 主题默认不建新增候选；若请求日上游主题全部 DORMANT / RISK，
    # 则显式回退到最近有候选的主题日，并在所有输出中标注 requested_date 与 date_note，
    # 绝不以「补造候选」的方式让报告显得非空。
    requested_date = args.date or max_date
    trade_date, date_note = resolve_effective_date(pool, requested_date)
    if date_note:
        log.warning("日期回退：%s", date_note)
    log.info("Step5 完成：请求日 %s → 生效日 %s；评估 %d 行，候选池 %d 行，污染 %d 行",
             requested_date, trade_date, len(df), len(pool), len(pollution))

    val = None
    if args.validate:
        val = run_validation(panel, df, theme, pool, pollution, meta, membership,
                             master_raw, cfg, trade_date, stock_basic,
                             requested_date, date_note, stock_long=stock_long)
        n_pass = int((val["status"] == "PASS").sum())
        log.info("验证：%d/%d 通过", n_pass, len(val))
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        val.to_csv(OUT_VALIDATION, index=False, encoding="utf-8-sig")

    if args.no_write:
        return 0 if (val is None or (val["status"] == "PASS").all()) else 1

    write_outputs(df, theme, pollution, pool, cfg, trade_date, args, requested_date, date_note)
    dump_json(build_today_json(theme, pool, trade_date, cfg, requested_date, date_note), OUT_TODAY)
    dump_json(build_pool_json(df, theme, trade_date, cfg, requested_date, date_note), OUT_POOL)
    top_obj, top_md = build_top_json(theme, pool, trade_date, cfg, requested_date, date_note)
    dump_json(top_obj, OUT_TOP)

    btdf, excess, base = build_backtest(df, pool, theme, cfg)
    with open(OUT_BACKTEST, "w", encoding="utf-8") as f:
        f.write(render_backtest_md(btdf, excess, base, cfg, trade_date, requested_date, date_note))
    log.info("写出 %s", os.path.relpath(OUT_BACKTEST, BASE_DIR))

    with open(OUT_AUDIT, "w", encoding="utf-8") as f:
        f.write(build_audit(df, theme, pool, pollution, cfg, meta, membership, val,
                            trade_date, stock_basic, requested_date, date_note))
    log.info("写出 %s", os.path.relpath(OUT_AUDIT, BASE_DIR))
    log.info("TOP 预览：\n%s", top_md[:1500])

    return 0 if (val is None or (val["status"] == "PASS").all()) else 1


_DATES_CACHE: list = []


def _read_all_trade_dates() -> list:
    """读取行情缓存中的交易日历（仅交易日集合，不取价格）。"""
    global _DATES_CACHE
    if _DATES_CACHE:
        return _DATES_CACHE
    READ_FILES.append(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    try:
        d = pd.read_sql_query("SELECT DISTINCT trade_date FROM daily_cache ORDER BY trade_date", con)
    finally:
        con.close()
    _DATES_CACHE = d["trade_date"].astype(str).tolist()
    return _DATES_CACHE


if __name__ == "__main__":
    sys.exit(main())
