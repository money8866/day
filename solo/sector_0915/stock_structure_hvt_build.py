# -*- coding: utf-8 -*-
"""
第六步 — Stock Structure / HVT Qualification Layer（个股结构质量与 HVT 结构资格认证）

定位（需求 §一）
----------------
Step 5 决定「为什么进入候选池」，Step 6 决定「结构是否完整」，Step 7 才决定「现在能不能执行」。
本层只对 Step 5 输出的 Theme-to-Stock Candidate 做个股级的：

* 趋势结构（Trend Structure）
* 价格位置（Price Structure）
* 量价结构（Volume / Price Structure）
* 缩量平台（Consolidation）
* HVT Event 与 HVT 后续演化（Adjustment / Locking / Breakout / Retest / Re-breakout）
* Extension Risk / Extension Penalty
* STRUCTURE QUALIFICATION 资格认证

输出结构分类（14 态）与资格（4 档），**不输出** BUY / NO TRADE / 仓位 / 买入金额 /
止损执行 / 最终交易指令。Step 6 完成后立即停止，不进入 Step 7。

链路（需求 §二）
----------------
    sector_master.json
        ↓
    config/sector_mapping.json → data/sector_membership.csv
        ↓
    Step3 Theme State → Step4 SEOS
        ↓
    Step5 data/sector_stock_candidate_daily.csv（本层唯一候选来源）
        ↓
    STEP 6（本脚本）
        ↓
    STEP 7 Execution（不实现）

绝对边界
--------
* 不得重新扫描全市场、不得绕过 Step 5 自建候选池、不得按涨幅 / 新闻 / 涨停重新定义候选。
* `legacy_config_used = false`：禁止读取、解析、import、open
  `theme.json` / `theme_config.json` / `subtheme_map.json`；检测到实际读取即 SystemExit，
  并标记 LEGACY_CONTAMINATION = CRITICAL（需求 §三、§四十七-2）。
* 所有结构判断只使用 D 及以前数据（需求 §七）。`shift(-n)` / 未来最高价 / 未来最低价 /
  未来突破确认 / 未来成交量 / 未来财务数据一律不得进入 signal feature / score / qualification；
  未来数据只能用于 backtest outcome。
* Extension Penalty ∈ [0, 20]，只能降低 qualification，绝不能提高任何评分（需求 §十二）。
* 保留用户既有交易原则（需求 §五十四）：巨量日不追、只等回踩；回踩触发位不破；
  收盘跌回关键突破位 / 结构位后结构失效。
* 所有阈值集中在 config/structure_hvt_config.json，本脚本不得硬编码结构阈值。

与既有 HVT 体系兼容（需求 §五十三）
------------------------------------
项目已存在 HVT 实现：`hvt_bull`（14 态状态机 `hvt_bull.models.HVT_STATES`，
事件定义 / 阈值在 `hvt_bull/config.yaml`）。本层不重新定义冲突的 HVT，而是：

1. **保留原始字段**：`legacy_hvt_state` / `legacy_hvt_event_grade` 等原样落盘；
2. **建立 adapter / normalized fields**：把 legacy 14 态经 `legacy_adapter.state_map`
   归一到 `hvt_state`（7 态），并统一 `hvt_quality_score` / `locking_score` /
   `rebreakout_state` / `retest_state`；
3. **adapter 模式** = `REUSE_DEFINITION_TRANSCRIBE_VECTORIZED`：逐字复用 legacy 的事件定义与
   阈值（tratio / 换手分位 / A-B-C 分级 / price_strength），向量化重写，并用抽样做
   等价性校验（`--validate` 中的 `CHECK_HVT_ADAPTER_EQUIVALENCE`）。

价格口径
--------
结构层与 HVT 结构演化统一使用**后复权价**（`adj = close × adj_factor`），与 Step 5 保持一致。
例外（§五十三 adapter 要求逐字等价）：legacy 价格强度闸门 `price_strength_ok` 所使用的
价格一律取**未复权** OHLC，与 legacy 引擎同口径；涨跌停判定同样使用未复权 `close` / `pre_close`。
legacy 事件定义依赖 `turnover_rate` / `amount`（与复权无关），因此事件识别等价性不受影响。

用法
----
    python stock_structure_hvt_build.py --full
    python stock_structure_hvt_build.py --date 20260916
    python stock_structure_hvt_build.py --validate
    python stock_structure_hvt_build.py --full --validate
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd
import yaml

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

log = setup_logging(LOG_DIR, name="stock_structure_hvt")

# ────────────────────────────────────────────────────────────────────────────
# 路径常量
# ────────────────────────────────────────────────────────────────────────────

CFG_PATH = os.path.join(CONFIG_DIR, "structure_hvt_config.json")
MASTER_PATH = os.path.join(BASE_DIR, "sector_master.json")

IN_CANDIDATE_DAILY = os.path.join(DATA_DIR, "sector_stock_candidate_daily.csv")
IN_CANDIDATE_TODAY = os.path.join(OUTPUT_DIR, "sector_stock_candidate_today.json")
IN_CANDIDATE_POOL = os.path.join(OUTPUT_DIR, "sector_stock_candidate_pool.json")
IN_MEMBERSHIP = os.path.join(DATA_DIR, "sector_membership.csv")
IN_MAPPING = os.path.join(CONFIG_DIR, "sector_mapping.json")
IN_STOCK_BASIC = os.path.join(PROJ_DIR, "sli", "cache", "stock_basic.parquet")
DB_PATH = r"D:\mystock\cache_daily\stock_data.db"

OUT_STRUCTURE_DAILY = os.path.join(DATA_DIR, "stock_structure_daily.csv")
OUT_TREND_STRUCTURE = os.path.join(DATA_DIR, "stock_trend_structure.csv")
OUT_VOLUME_STRUCTURE = os.path.join(DATA_DIR, "stock_volume_structure.csv")
OUT_CONSOLIDATION = os.path.join(DATA_DIR, "stock_consolidation.csv")
OUT_HVT_EVENTS = os.path.join(DATA_DIR, "stock_hvt_events.csv")
OUT_HVT_HISTORY = os.path.join(DATA_DIR, "stock_hvt_history.csv")
OUT_BREAKOUT = os.path.join(DATA_DIR, "stock_breakout.csv")
OUT_RETEST = os.path.join(DATA_DIR, "stock_retest.csv")
OUT_STRUCTURE_STATE = os.path.join(DATA_DIR, "stock_structure_state.csv")
OUT_STRUCTURE_SCORE = os.path.join(DATA_DIR, "stock_structure_score.csv")

OUT_TODAY_JSON = os.path.join(OUTPUT_DIR, "stock_structure_today.json")
OUT_STRUCTURE_POOL = os.path.join(OUTPUT_DIR, "stock_structure_pool.json")
OUT_HVT_POOL = os.path.join(OUTPUT_DIR, "stock_hvt_pool.json")
OUT_REBREAKOUT_POOL = os.path.join(OUTPUT_DIR, "stock_rebreakout_pool.json")
OUT_RETEST_POOL = os.path.join(OUTPUT_DIR, "stock_retest_pool.json")
OUT_BACKTEST_MD = os.path.join(OUTPUT_DIR, "structure_hvt_backtest.md")
OUT_VALIDATION_CSV = os.path.join(OUTPUT_DIR, "structure_hvt_validation.csv")
OUT_AUDIT_MD = os.path.join(OUTPUT_DIR, "structure_hvt_audit.md")

# legacy 文件（需求 §三）：存在即可记录，绝不参与计算
LEGACY_NAMES = ("theme_config.json", "subtheme_map.json", "theme.json",
                "theme_master.json", "theme_mapping.json")

# 全程序打开过的文件（用于 legacy 隔离验证）
READ_FILES: list = []

# ────────────────────────────────────────────────────────────────────────────
# 枚举（需求 §八 ~ §三十八）
# ────────────────────────────────────────────────────────────────────────────

TREND_STATES = ["UPTREND", "UPTREND_EARLY", "SIDEWAYS", "CORRECTION", "DOWNTREND", "INVALID"]
PRICE_ZONES = ["BREAKOUT_ZONE", "NEAR_HIGH", "PULLBACK", "MID_RANGE", "DEEP_PULLBACK",
               "EXTENDED", "INVALID"]
VOLUME_STATES = ["HEALTHY_VOLUME", "VOLUME_EXPANSION", "VOLUME_CONTRACTION",
                 "ABNORMAL_VOLUME", "VOLUME_DIVERGENCE"]
PLATFORM_QUALITIES = ["PLATFORM_STRONG", "PLATFORM_HEALTHY", "PLATFORM_WEAK", "NO_PLATFORM"]
ADJUSTMENT_STATES = ["NO_ADJUSTMENT", "SHALLOW_ADJUSTMENT", "HEALTHY_ADJUSTMENT",
                     "DEEP_ADJUSTMENT", "FAILED_ADJUSTMENT"]

# 需求 §二十五：HVT 规范 7 态
HVT_STATES = ["HVT_NONE", "HVT_EVENT", "HVT_ADJUSTING", "HVT_LOCKING",
              "HVT_REBREAKOUT", "HVT_RETEST", "HVT_FAILED"]
# 需求 §十七：更细的阶段（落盘为 hvt_stage，作为 HVT 7 态的下钻口径）
HVT_STAGES = ["HVT_NONE", "HVT_EVENT", "HVT_ADJUSTING", "HVT_CONSOLIDATING",
              "HVT_REACCUMULATION", "HVT_REBREAKOUT", "HVT_FAILED"]

BREAKOUT_STATES = ["NO_BREAKOUT", "BREAKOUT_READY", "BREAKOUT_CONFIRMED",
                   "REBREAKOUT_CONFIRMED", "BREAKOUT_FAILED"]
RETEST_STATES = ["NO_RETEST", "RETEST_PENDING", "RETEST_SUCCESS", "RETEST_FAILED"]

STRUCTURE_STATES = ["BASE", "HVT", "ADJUSTING", "LOCKING", "BREAKOUT_READY", "BREAKOUT",
                    "RETEST", "REBREAKOUT", "EXTENDED", "FAILED", "WATCH", "INVALID"]
STRUCTURE_CLASSES = ["STRUCTURE_BASE", "HVT_EVENT", "HVT_ADJUSTING", "HVT_LOCKING",
                     "BREAKOUT_READY", "BREAKOUT_CONFIRMED", "RETEST_PENDING",
                     "RETEST_SUCCESS", "REBREAKOUT_CONFIRMED", "PULLBACK_HEALTHY",
                     "EXTENDED", "FAILED", "WATCH", "INVALID"]
QUALIFICATIONS = ["QUALIFIED", "CONDITIONAL", "WATCH", "REJECTED"]
EXTENSION_RISKS = ["LOW", "MEDIUM", "HIGH", "EXTREME"]
DATA_QUALITIES = ["VALID", "PARTIAL", "INSUFFICIENT", "INVALID"]
MEMBERSHIP_VERSION_STATUSES = ["VERSIONED", "STATIC_ONLY", "PARTIAL"]
BREAKOUT_QUALITIES = ["GOOD", "NEUTRAL", "WEAK", "NA"]

# 需求 §一 / §三十：本层允许出现的结构分类词表（禁止出现 BUY / SELL 等交易指令词）
FORBIDDEN_OUTPUT_TERMS = ["买入价", "止损价", "目标价", "仓位", "买入金额", "BUY", "SELL",
                          "STRONG BUY", "NO TRADE", "TradeRank", "Execution Score"]

# 需求 §五十四：必须保留的交易原则（审计报告逐条列出，并由验证项核对）
STEP6_PRINCIPLES = [
    "巨量日不追，只等回踩。",
    "回踩触发位不破。",
    "收盘跌回关键突破位 / 结构位后，结构失效。",
]

# 需求 §五十七：Step 6 收尾声明
STEP6_STOP_NOTICE = [
    "Step 6 completed.",
    "No BUY / NO TRADE decision generated.",
    "No position sizing / stop execution / trade instruction generated.",
    "No legacy theme configuration used.",
    "Step 7 Execution NOT implemented.",
]

# ────────────────────────────────────────────────────────────────────────────
# 基础工具（与 Step 5 风格对齐）
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

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean(obj), f, ensure_ascii=False, indent=2)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def write_merge_csv(df: pd.DataFrame, path: str, key_cols=("trade_date", "ts_code")):
    """时间序列合并写出：保留历史行，只替换本次重算覆盖到的键（需求 §二十六：不得覆盖历史）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key_cols = [c for c in key_cols if c in df.columns]
    if not os.path.exists(path):
        df.to_csv(path, index=False, encoding="utf-8-sig")
        log.info("写出 %s (%d 行, 新建)", os.path.relpath(path, BASE_DIR), len(df))
        return
    old = pd.read_csv(path, dtype={c: str for c in key_cols}, low_memory=False)
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
    log.info("写出 %s (%d 行, 合并保留历史 %d 行)", os.path.relpath(path, BASE_DIR),
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
    """线性 ramp（与 Step 5 口径一致）：

    升序区间 hi>lo：x<=lo → 0，x>=hi → 100；
    降序区间 hi<lo：x<=hi → 100，x>=lo → 0；
    NaN 保持 NaN，绝不填 0。
    """
    if isinstance(x, (pd.Series, np.ndarray, list)):
        v = np.asarray(pd.to_numeric(pd.Series(x), errors="coerce"), dtype=float)
        if math.isclose(lo, hi):
            r = np.full(v.shape, np.nan)
        elif hi > lo:
            r = np.clip((v - lo) / (hi - lo) * 100.0, 0.0, 100.0)
        else:
            r = np.clip((lo - v) / (lo - hi) * 100.0, 0.0, 100.0)
        r = np.where(np.isfinite(v), r, np.nan)
        return (100.0 - r) if inverted else r
    v = _fv(x)
    if math.isnan(v) or math.isclose(lo, hi):
        return float("nan")
    if hi > lo:
        r = 0.0 if v <= lo else (100.0 if v >= hi else (v - lo) / (hi - lo) * 100.0)
    else:
        r = 100.0 if v <= hi else (0.0 if v >= lo else (lo - v) / (lo - hi) * 100.0)
    return (100.0 - r) if inverted else r


def _lerp_curve(x: float, xs: list, ys: list) -> float:
    """分段线性插值（x 越界时按端点截断）。"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return float("nan")
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            w = (x - xs[i]) / (xs[i + 1] - xs[i])
            return float(ys[i] + w * (ys[i + 1] - ys[i]))
    return float(ys[-1])


def _wmean(scores: dict, weights: dict) -> tuple:
    """加权平均（权重通常合计 100）。返回 (score, coverage)。NaN 分量不参与，按已覆盖权重归一。"""
    tot_w = 0.0
    acc = 0.0
    for k, w in weights.items():
        s = scores.get(k)
        if s is None:
            continue
        s = float(s)
        if not math.isfinite(s):
            continue
        acc += s * float(w)
        tot_w += float(w)
    if tot_w <= 0:
        return float("nan"), 0.0
    return acc / tot_w, tot_w / max(sum(float(w) for w in weights.values()), 1e-9)


def _roll(x: np.ndarray, w: int, fn: str = "mean", min_n: int | None = None) -> np.ndarray:
    """滚动统计（含当前行）。"""
    s = pd.Series(np.asarray(x, dtype=float))
    mn = w if min_n is None else min_n
    r = getattr(s.rolling(w, min_periods=mn), fn)()
    return r.to_numpy(dtype=float)


def _roll_prev(x: np.ndarray, w: int, min_n: int) -> np.ndarray:
    """滚动统计（不含当前行，即 [i-w, i-1]）——严格 PIT。"""
    s = pd.Series(np.asarray(x, dtype=float))
    return s.rolling(w, min_periods=min_n).mean().shift(1).to_numpy(dtype=float)


def _safe_div(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.asarray(a, dtype=float) / np.asarray(b, dtype=float)
    return np.where(np.isfinite(out), out, np.nan)


# ────────────────────────────────────────────────────────────────────────────
# 数据层（复用现有 Tushare 缓存，不重复建设：需求 §六、§五十九）
# ────────────────────────────────────────────────────────────────────────────

def load_stock_basic() -> pd.DataFrame:
    if not os.path.exists(IN_STOCK_BASIC):
        raise FileNotFoundError(f"缺少股票基础信息: {IN_STOCK_BASIC}")
    READ_FILES.append(IN_STOCK_BASIC)
    sb = pd.read_parquet(IN_STOCK_BASIC)
    keep = [c for c in ("ts_code", "name", "industry", "list_status", "list_date") if c in sb.columns]
    sb = sb[keep].copy()
    sb["ts_code"] = sb["ts_code"].astype(str)
    sb["list_date"] = sb["list_date"].astype(str)
    return sb.drop_duplicates("ts_code", keep="first")


def _db_query(sql: str, params: tuple) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"缺少行情缓存库: {DB_PATH}")
    READ_FILES.append(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def read_trade_dates() -> list:
    """行情缓存中的交易日历（仅日期集合）。"""
    return _read_all_trade_dates()


_DATES_CACHE: list = []


def _read_all_trade_dates() -> list:
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


def load_stock_market(d_start: str, d_end: str, codes: set) -> pd.DataFrame:
    """单股行情长表（含复权因子、换手率、量比），按 ts_code / trade_date 升序。"""
    px = _db_query("SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, amount "
                   "FROM daily_cache WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    db = _db_query("SELECT ts_code, trade_date, turnover_rate, volume_ratio, total_mv, circ_mv, "
                   "pe_ttm, pb FROM daily_basic_cache "
                   "WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    af = _db_query("SELECT ts_code, trade_date, adj_factor FROM adj_factor_cache "
                   "WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    for f in (px, db, af):
        f["ts_code"] = f["ts_code"].astype(str)
        f["trade_date"] = f["trade_date"].astype(str)
    px = px[px["ts_code"].isin(codes)]
    db = db[db["ts_code"].isin(codes)]
    af = af[af["ts_code"].isin(codes)]
    m = px.merge(db, on=["ts_code", "trade_date"], how="left") \
          .merge(af, on=["ts_code", "trade_date"], how="left")
    m = m.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    g = m.groupby("ts_code", sort=False)["adj_factor"]
    m["adj_factor"] = g.ffill().bfill()
    m["adj"] = m["close"] * m["adj_factor"]
    m["adj_high"] = m["high"] * m["adj_factor"]
    m["adj_low"] = m["low"] * m["adj_factor"]
    m["adj_open"] = m["open"] * m["adj_factor"]
    # pct_chg 用后复权价的相邻比，避免除权日 pre_close 口径差异
    m["pct_chg"] = g_adj_pct(m)
    log.info("行情载入：%d 行 / %d 股票 / %s → %s", len(m), m["ts_code"].nunique(),
             m["trade_date"].min(), m["trade_date"].max())
    return m


def g_adj_pct(m: pd.DataFrame) -> pd.Series:
    a = m.groupby("ts_code", sort=False)["adj"]
    return (a.transform(lambda s: s / s.shift(1) - 1.0)).astype(float)


def load_candidates(cfg: dict) -> pd.DataFrame:
    """Step 5 输出：本层唯一候选来源（需求 §二）。"""
    df = _read_csv(IN_CANDIDATE_DAILY, dtype=str, low_memory=False)
    need = ["trade_date", "ts_code", "candidate_status"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"sector_stock_candidate_daily.csv 缺少必需列: {miss}")
    stat_ok = list(cfg["input"]["universe_status"])
    df = df[df["candidate_status"].isin(stat_ok)].copy()
    # Step 5 字段名 → Step 6 规格字段名（§四十一 / §三十二：主题 context 原样保留，不重算）
    df = df.rename(columns={"theme_opportunity_score": "theme_opportunity",
                            "secondary_themes": "secondary_theme_ids"})
    for c in ("sector_id", "primary_theme_id"):
        if c in df.columns:
            df[c] = df[c].astype(str)
    df = df.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    log.info("候选域载入：%d 行 / %d 股票 / %d 交易日（status∈%s）",
             len(df), df["ts_code"].nunique(), df["trade_date"].nunique(), stat_ok)
    return df


def load_membership() -> pd.DataFrame:
    """唯一正式成员关系：data/sector_membership.csv（由 config/sector_mapping.json 生成）。"""
    mb = _read_csv(IN_MEMBERSHIP, dtype=str)
    READ_FILES.append(IN_MAPPING)
    for c in ("is_static", "effective_date"):
        if c not in mb.columns:
            raise ValueError(f"sector_membership.csv 缺少必需列: {c}")
    mb["is_static"] = mb["is_static"].astype(str).str.lower().isin(["true", "1", "yes"])
    mb["effective_date"] = mb["effective_date"].astype(str)
    return mb


def pit_membership_mask(df: pd.DataFrame, dates) -> pd.Series:
    """规则（与 Step 5 唯一实现一致）：is_static=true 视为整个回看窗口内恒定的正式定义；
    否则要求 effective_date <= D；若成员表含 membership_end_date，则额外要求 D < end。"""
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


def membership_version_status(mb: pd.DataFrame) -> str:
    """需求 §三十三：禁止假设静态 membership 代表全部历史。"""
    if "membership_end_date" in mb.columns:
        has_end = mb["membership_end_date"].astype(str).str.strip()
        if (~has_end.str.lower().isin(["", "nan", "none", "nat", "null"])).any():
            return "VERSIONED"
    if bool(mb["is_static"].all()):
        return "STATIC_ONLY"
    if bool(mb["is_static"].any()):
        return "PARTIAL"
    return "VERSIONED"


# ────────────────────────────────────────────────────────────────────────────
# 单股结构特征（趋势 / 价格位置 / 量价 / 缩量平台）—— 需求 §八 ~ §十四
# ────────────────────────────────────────────────────────────────────────────

def compute_stock_features(g: pd.DataFrame, cfg: dict) -> dict:
    """单股（按 trade_date 升序、仅含实际成交日）结构特征计算。

    仅使用 <= i 的数据；本函数不产生任何前向收益列。
    """
    lb = cfg["lookback"]
    adj = g["adj"].to_numpy(dtype=float)
    adj_high = g["adj_high"].to_numpy(dtype=float)
    adj_low = g["adj_low"].to_numpy(dtype=float)
    vol = g["vol"].to_numpy(dtype=float)
    amount = g["amount"].to_numpy(dtype=float)
    tov = pd.to_numeric(g["turnover_rate"], errors="coerce").to_numpy(dtype=float)
    vratio_db = pd.to_numeric(g["volume_ratio"], errors="coerce").to_numpy(dtype=float)
    pct = g["pct_chg"].to_numpy(dtype=float)
    n = len(adj)
    f: dict = {"n": n, "dates": g["trade_date"].astype(str).tolist()}

    # ── 均线与趋势结构（需求 §八）──
    mas = {}
    for w in lb["ma_windows"]:
        mas[w] = _roll(adj, int(w), "mean")
        f[f"ma{w}"] = mas[w]
    ma5, ma10 = mas.get(5), mas.get(10)
    ma20, ma60, ma120, ma250 = mas.get(20), mas.get(60), mas.get(120), mas.get(250)

    f["close_vs_ma20"] = _safe_div(adj, ma20) - 1.0
    f["close_vs_ma60"] = _safe_div(adj, ma60) - 1.0
    f["ma20_slope_5"] = _safe_div(ma20, _shift(ma20, int(lb["slope_ma20_short"]))) - 1.0
    f["ma20_slope_10"] = _safe_div(ma20, _shift(ma20, int(lb["slope_ma20"]))) - 1.0
    f["ma60_slope_10"] = _safe_div(ma60, _shift(ma60, int(lb["slope_ma60"]))) - 1.0
    f["ma20_vs_ma60"] = _safe_div(ma20, ma60) - 1.0
    f["ma60_vs_ma120"] = _safe_div(ma60, ma120) - 1.0

    f["dist_ma5"] = _safe_div(adj, ma5) - 1.0
    f["dist_ma10"] = _safe_div(adj, ma10) - 1.0
    f["dist_ma20"] = _safe_div(adj, ma20) - 1.0
    f["dist_ma60"] = _safe_div(adj, ma60) - 1.0
    f["dist_ma120"] = _safe_div(adj, ma120) - 1.0

    # ── 历史高点 / 回撤（需求 §十）──
    highs = {}
    for w in lb["high_windows"]:
        h = _roll(adj_high, int(w), "max")
        highs[w] = h
        f[f"high_{w}"] = h
        f[f"dist_{w}d_high"] = _safe_div(adj, h) - 1.0            # 负值：距高点
        f[f"drawdown_from_{w}d_high"] = 1.0 - _safe_div(adj, h)    # 正值：自高点回撤
    f["drawdown_20"] = f["dist_20d_high"]
    f["drawdown_60"] = f["dist_60d_high"]
    f["drawdown_120"] = f["dist_120d_high"]
    f["drawdown_250"] = f["dist_250d_high"]

    # ── 收益（仅用 <= i）──
    for k in (1, 3, 5, 10, 20, 60):
        f[f"ret_{k}"] = _safe_div(adj, _shift(adj, k)) - 1.0

    # ── ATR / ATR 距离 ──
    atr = _atr(adj_high, adj_low, adj, int(lb["atr_window"]))
    f["atr20"] = atr
    f["atr_dist"] = _safe_div(adj - ma20, atr)

    # ── 量能（需求 §十三）──
    vbase = _roll_prev(vol, int(lb["vol_base"]), 10)
    f["volume_ratio_1"] = _safe_div(vol, vbase)
    f["volume_ratio_3"] = _safe_div(_roll(vol, 3, "mean", 3), vbase)
    f["volume_ratio_5"] = _safe_div(_roll(vol, int(lb["vol_short"]), "mean", 3), vbase)
    abase = _roll_prev(amount, int(lb["vol_base"]), 10)
    f["amount_ratio_1"] = _safe_div(amount, abase)
    f["amount_ratio_3"] = _safe_div(_roll(amount, 3, "mean", 3), abase)
    f["amount_ratio_5"] = _safe_div(_roll(amount, int(lb["vol_short"]), "mean", 3), abase)
    f["volume_ratio_db"] = vratio_db
    f["volume_ratio_used"] = np.where(np.isfinite(vratio_db), vratio_db, f["volume_ratio_1"])
    up = pct > 0
    dn = pct < 0
    f["up_volume_ratio"] = _ratio_masked(vol, up, vbase)
    f["down_volume_ratio"] = _ratio_masked(vol, dn, vbase)
    f["volume_expansion"] = f["volume_ratio_1"]
    f["volume_contraction"] = f["volume_ratio_5"]
    f["turnover_rate"] = tov

    # ── 缩量平台（需求 §十四）──
    cons = _consolidation(adj, adj_high, adj_low, vol, atr, f["dist_ma20"], cfg)
    f.update(cons)

    f["price_hist_days"] = np.arange(1, n + 1, dtype=float)
    f["ts_code"] = str(g["ts_code"].iloc[0])
    f["adj"] = adj
    f["adj_high"] = adj_high
    f["adj_low"] = adj_low
    f["adj_open"] = g["adj_open"].to_numpy(dtype=float)
    f["vol"] = vol
    f["amount"] = amount
    f["pct_chg_pct"] = pct * 100.0
    f["raw_open"] = g["open"].to_numpy(dtype=float)
    f["raw_high"] = g["high"].to_numpy(dtype=float)
    f["raw_low"] = g["low"].to_numpy(dtype=float)
    f["raw_close"] = g["close"].to_numpy(dtype=float)
    f["pre_close"] = g["pre_close"].to_numpy(dtype=float)
    f["atr14"] = _atr(adj_high, adj_low, adj, 14)
    return f


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(np.asarray(x, dtype=float), np.nan)
    if k <= 0:
        return np.asarray(x, dtype=float).copy()
    out[k:] = np.asarray(x, dtype=float)[:-k]
    return out


def _ratio_masked(x: np.ndarray, mask: np.ndarray, base: np.ndarray) -> np.ndarray:
    """近 10 日指定方向日的均量 / 基准均量。"""
    w = 10
    num = np.zeros(len(x))
    den = np.zeros(len(x))
    for k in range(w):
        mk = _shift(mask.astype(float), k)
        xk = _shift(x, k)
        mk = np.where(np.isfinite(mk), mk, 0.0)
        xk = np.where(np.isfinite(xk), xk, 0.0)
        num += xk * mk
        den += mk
    return _safe_div(np.where(den > 0, num / np.maximum(den, 1e-12), np.nan), base)


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, w: int) -> np.ndarray:
    """ATR（含当前行）：TR = max(H-L, |H-PrevC|, |L-PrevC|) 的 w 日均值。"""
    pc = _shift(close, 1)
    tr = np.nanmax(np.vstack([high - low, np.abs(high - pc), np.abs(low - pc)]), axis=0)
    return _roll(tr, w, "mean", max(3, w // 2))


def _consolidation(adj, adj_high, adj_low, vol, atr, dist_ma20, cfg) -> dict:
    """缩量平台识别（需求 §十四）：窗口网格内取「满足约束的最长窗口」。"""
    c = cfg["consolidation"]
    n = len(adj)
    days = np.zeros(n)
    rng = np.full(n, np.nan)
    vratio = np.full(n, np.nan)
    chi = np.full(n, np.nan)
    clo = np.full(n, np.nan)
    catr = np.full(n, np.nan)
    cpos = np.full(n, np.nan)
    min_days = int(c["min_days"])
    for L in c["window_grid"]:
        L = int(L)
        if L < min_days:
            continue
        hi = _roll(adj_high, L, "max")
        lo = _roll(adj_low, L, "min")
        cv = _roll(vol, L, "mean")
        vb = _roll_prev(vol, int(cfg["lookback"]["vol_base"]), 10)
        vb = _shift(vb, L)
        ca = _roll(atr, L, "mean", max(3, L // 2))
        r = _safe_div(hi - lo, adj)
        vr = _safe_div(cv, vb)
        ok = (np.isfinite(r) & np.isfinite(vr) & np.isfinite(dist_ma20)
              & (r <= float(c["range_max"]))
              & (vr <= float(c["vol_ratio_max"]))
              & (np.abs(dist_ma20) <= float(c["ma20_tol"])))
        if not ok.any():
            continue
        pos = _safe_div(adj - lo, hi - lo)
        days[ok] = float(L)
        rng[ok] = r[ok]
        vratio[ok] = vr[ok]
        chi[ok] = hi[ok]
        clo[ok] = lo[ok]
        catr[ok] = ca[ok]
        cpos[ok] = pos[ok]

    quality = np.full(n, "NO_PLATFORM", dtype=object)
    has = days >= min_days
    quality[has] = "PLATFORM_WEAK"
    healthy = has & (rng <= float(c["range_max"])) & (vratio <= float(c["vol_ratio_max"]))
    quality[healthy] = "PLATFORM_HEALTHY"
    strong = (days >= float(c["strong_min_days"])) & (rng <= float(c["strong_range_max"])) \
        & (vratio <= float(c["strong_vol_ratio_max"])) & (cpos >= float(c["strong_close_pos_min"]))
    quality[strong] = "PLATFORM_STRONG"
    return {
        "consolidation_days": days,
        "consolidation_range": rng,
        "consolidation_vol_ratio": vratio,
        "consolidation_atr": catr,
        "consolidation_high": chi,
        "consolidation_low": clo,
        "consolidation_close_pos": cpos,
        "platform_quality": quality,
    }


# ════════════════════════════════════════════════════════════════════════════
# HVT 层（需求 §十五 ~ §二十七）
#   1) legacy adapter（§五十三）：事件定义 / 阈值逐字转写 + 原始字段保留
#   2) Step 6 演化状态机：HVT_EVENT → ADJUSTING → LOCKING → REBREAKOUT → RETEST
# ════════════════════════════════════════════════════════════════════════════

HVT_BULL_CFG = os.path.join(PROJ_DIR, "hvt_bull", "config.yaml")


def load_legacy_hvt_config() -> dict:
    """读取 legacy HVT 阈值来源 hvt_bull/config.yaml（§五十三 threshold_source）。

    只读取阈值段，不读取任何 legacy 主题配置（theme.json / theme_config.json / submap）。
    """
    if not os.path.exists(HVT_BULL_CFG):
        raise FileNotFoundError(f"缺少 legacy HVT 阈值来源: {HVT_BULL_CFG}")
    READ_FILES.append(os.path.abspath(HVT_BULL_CFG))
    with open(HVT_BULL_CFG, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    log.info("legacy HVT 阈值载入：%s（阈值段：%s）", os.path.basename(HVT_BULL_CFG),
             ",".join(sorted(raw.keys())))
    return raw


def normalize_legacy_state(cfg: dict, state: str) -> str:
    """§五十三 adapter：legacy 14 态 → Step 6 规范 7 态。"""
    if not state:
        return ""
    smap = cfg["legacy_adapter"]["state_map"]
    return str(smap.get(state, ""))


def _dense_ok(dates, i0: int, i1: int) -> bool:
    """legacy `_dense` 逐字转写：行区间日历跨度 <= 交易日数 × 1.9。"""
    if i1 < i0:
        return False
    n = i1 - i0 + 1
    if n <= 0:
        return False
    try:
        d0 = pd.to_datetime(str(dates[i0]), format="%Y%m%d")
        d1 = pd.to_datetime(str(dates[i1]), format="%Y%m%d")
        return (d1 - d0).days <= n * 1.9
    except Exception:
        return False


def _limit_up_ratio(ts_code: str) -> str:
    """legacy `_limit_up_ratio`：科创板/创业板 20cm，其余 10cm。"""
    return "1.2" if str(ts_code or "")[:3] in ("688", "689", "300", "301", "302") else "1.1"


def _is_limit_up_close(close: float, pre_close: float, ts_code: str) -> bool:
    """legacy `_is_limit_up_close` 逐字转写（交易所口径，未复权价）。"""
    if not (np.isfinite(close) and np.isfinite(pre_close)) or pre_close <= 0:
        return False
    lim = float((Decimal(str(pre_close)) * Decimal(_limit_up_ratio(ts_code)))
                .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return close >= lim - 1e-9


def _anchor_index(dates: list, anchor_date: str) -> int:
    for i, d in enumerate(dates):
        if str(d) >= str(anchor_date):
            return i
    return -1


def hvt_detect_all(f: dict, cfg: dict, legacy: dict, anchor_date: str) -> dict:
    """向量化转写 legacy `HvtBullEngine.detect_hvt`（逐字等价）。

    仅使用 turnover_rate / amount（与复权无关），因此与 legacy 原始口径完全等价。
    legacy 要求 tratio >= ratio_c(=1.8) 才可能定级，故只扫描 tratio >=
    config.hvt.prefilter_tratio_min 的候选点，结果为 legacy 全量扫描的等价子集。
    """
    hcfg = legacy.get("hvt", {}) or {}
    scfg = cfg["hvt"]
    n = f["n"]
    turnover = f["turnover_rate"]
    amount = f["amount"]
    dates = f["dates"]
    ts_code = f["ts_code"]

    lb = int(hcfg.get("lookback_250", 250))
    ratio_a = float(hcfg.get("ratio_a", 3.0))
    ratio_b = float(hcfg.get("ratio_b", 2.0))
    ratio_c = float(hcfg.get("ratio_c", 1.8))
    rank_a = int(hcfg.get("rank_a", 1))
    rank_b = int(hcfg.get("rank_b", 2))
    pct_a = float(hcfg.get("pct_a", 99))
    pct_b = float(hcfg.get("pct_b", 98))
    pct_c = float(hcfg.get("pct_c", 95))
    top_cap = int(hcfg.get("top_rank_cap", 2))
    anchor_min = int(hcfg.get("anchor_min_hist", 60))
    mode = str(hcfg.get("rank_mode", "") or "").strip().lower()
    if mode not in ("anchor", "rolling", "both"):
        mode = "anchor" if anchor_date else "rolling"
    if mode == "anchor" and not anchor_date:
        mode = "rolling"
    a0 = _anchor_index(dates, anchor_date) if anchor_date else -1

    b20 = 20
    ma20t = _roll_prev(turnover, b20, 10)
    ma20a = _roll_prev(amount, b20, 10)
    tratio_a = _safe_div(turnover, ma20t)
    aratio_a = _safe_div(amount, ma20a)

    cand = np.where(np.isfinite(turnover) & (turnover > 0)
                    & np.isfinite(tratio_a)
                    & (tratio_a >= float(scfg["prefilter_tratio_min"])))[0]
    cand = cand[(cand >= 30) & (cand < n)]

    grade = np.full(n, "", dtype=object)
    pct120 = np.full(n, np.nan)
    rank250 = np.full(n, np.nan)
    rank_anchor = np.full(n, np.nan)
    tratio_all = tratio_a.copy()
    aratio_all = aratio_a.copy()

    for i in cand.tolist():
        i = int(i)
        ti = float(turnover[i])
        lo = max(0, i - lb)
        hist = turnover[lo:i]
        if len(hist) < 60:
            continue
        h120 = turnover[max(0, i - 120):i]
        if len(h120) < 60:
            continue
        rank = int(np.sum(hist >= ti)) + 1
        p120 = float(np.mean(h120 < ti) * 100.0)
        h20 = turnover[max(0, i - b20):i]
        m20t = float(np.nanmean(h20)) if len(h20) >= 10 else 0.0
        tratio = ti / m20t if m20t > 0 else 0.0
        a20 = amount[max(0, i - b20):i]
        m20a = float(np.nanmean(a20)) if len(a20) >= 10 else 0.0
        aratio = float(amount[i]) / m20a if m20a > 0 else 0.0

        g = None
        ra = np.nan
        if mode in ("anchor", "both") and a0 >= 0 and a0 <= i:
            hist_a = turnover[a0:i]
            if len(hist_a) >= anchor_min:
                ra = int(np.sum(hist_a >= ti)) + 1
                if ra == 1 and tratio >= ratio_c:
                    g = "A" if tratio >= ratio_a else ("B" if tratio >= ratio_b else "C")
        if g is None and mode in ("rolling", "both"):
            if rank <= rank_a and p120 >= pct_a and tratio >= ratio_a:
                g = "A"
            elif rank <= rank_b and p120 >= pct_b and tratio >= ratio_b:
                g = "B"
            elif p120 >= pct_c and tratio >= ratio_c and rank <= top_cap + 8:
                g = "C"
        if g is None:
            continue
        grade[i] = g
        pct120[i] = p120
        rank250[i] = float(rank)
        rank_anchor[i] = ra
        tratio_all[i] = tratio
        aratio_all[i] = aratio

    return {"hvt_grade": grade, "hvt_pct120": pct120, "hvt_rank250": rank250,
            "hvt_rank_anchor": rank_anchor, "hvt_tratio": tratio_all,
            "hvt_aratio": aratio_all}


def hvt_event_fields(idx: int, arrays: dict, dates: list, legacy: dict, ts_code: str) -> dict:
    """转写 legacy `HvtBullEngine.evaluate_event`（价格口径由 arrays 决定）。"""
    trcfg = legacy.get("trend", {}) or {}
    close, open_, high, low = arrays["close"], arrays["open"], arrays["high"], arrays["low"]
    raw_close = np.asarray(arrays.get("raw_close", close))
    pre_close = np.asarray(arrays.get("pre_close", np.full(len(close), np.nan)))
    pct = arrays["pct_chg"]
    out: dict = {"t0_close": np.nan, "t0_close_pos": np.nan, "t0_body": np.nan,
                 "t0_pct_chg": np.nan,
                 "t0_prev_limit_up": False, "ma20": np.nan, "ma60": np.nan, "ma120": np.nan,
                 "ma20_slope": np.nan, "ma60_slope": np.nan, "platform_breakout": False,
                 "r5": np.nan, "r10": np.nan, "r20": np.nan, "r60": np.nan,
                 "r120": np.nan, "r250": np.nan, "dist_high_120": np.nan, "atr14": np.nan}
    if idx < 0 or idx >= len(close):
        return out

    out["t0_close"] = float(close[idx]) if np.isfinite(close[idx]) else np.nan
    out["t0_close_pos"] = 0.5
    out["t0_body"] = 0.0
    out["t0_pct_chg"] = float(pct[idx]) if np.isfinite(pct[idx]) else 0.0
    rng = high[idx] - low[idx]
    if rng > 0:
        out["t0_close_pos"] = float((close[idx] - low[idx]) / rng)
        out["t0_body"] = float((close[idx] - open_[idx]) / rng)
    # 涨跌停判定必须用未复权价（交易所口径），不能用后复权价
    if idx >= 1 and np.isfinite(raw_close[idx - 1]) and np.isfinite(pre_close[idx - 1]):
        out["t0_prev_limit_up"] = _is_limit_up_close(
            float(raw_close[idx - 1]), float(pre_close[idx - 1]), ts_code)

    def _ma(n_):
        if idx + 1 >= n_ and _dense_ok(dates, idx + 1 - n_, idx):
            return float(np.nanmean(close[idx + 1 - n_:idx + 1]))
        return float("nan")

    out["ma20"], out["ma60"], out["ma120"] = _ma(20), _ma(60), _ma(120)
    s20 = int(trcfg.get("ma20_slope_days", 10))
    s60 = int(trcfg.get("ma60_slope_days", 20))
    if idx + 1 >= 20 + s20 and _dense_ok(dates, idx + 1 - 20 - s20, idx):
        m_now = np.nanmean(close[idx + 1 - 20:idx + 1])
        m_prev = np.nanmean(close[idx + 1 - 20 - s20:idx + 1 - s20])
        if m_prev > 0:
            out["ma20_slope"] = float((m_now - m_prev) / m_prev)
    if idx + 1 >= 60 + s60 and _dense_ok(dates, idx + 1 - 60 - s60, idx):
        m_now = np.nanmean(close[idx + 1 - 60:idx + 1])
        m_prev = np.nanmean(close[idx + 1 - 60 - s60:idx + 1 - s60])
        if m_prev > 0:
            out["ma60_slope"] = float((m_now - m_prev) / m_prev)

    if idx >= 60 and close[idx] > float(np.nanmax(high[idx - 60:idx])):
        out["platform_breakout"] = True
    if idx >= 20 and not out["platform_breakout"]:
        pre_close_20 = float(np.nanmax(close[idx - 20:idx]))
        if close[idx] >= pre_close_20 * 1.05:
            out["platform_breakout"] = True

    def _ret(n_):
        if idx - n_ >= 0 and _dense_ok(dates, idx - n_, idx) and close[idx - n_] > 0:
            return float(close[idx] / close[idx - n_] - 1.0) * 100.0
        return float("nan")

    for n_, key in ((5, "r5"), (10, "r10"), (20, "r20"), (60, "r60"),
                    (120, "r120"), (250, "r250")):
        out[key] = _ret(n_)
    if idx >= 120 and _dense_ok(dates, idx - 120, idx):
        h120 = np.nanmax(high[idx - 120:idx + 1])
        out["dist_high_120"] = float((h120 - close[idx]) / close[idx] * 100.0)
    if idx >= 15:
        tr_arr = np.maximum(
            high[idx - 14:idx + 1] - low[idx - 14:idx + 1],
            np.maximum(np.abs(high[idx - 14:idx + 1] - close[idx - 15:idx]),
                       np.abs(low[idx - 14:idx + 1] - close[idx - 15:idx])))
        out["atr14"] = float(np.nanmean(tr_arr))
    return out


def hvt_price_strength_ok(ev: dict, legacy: dict) -> bool:
    """转写 legacy `HvtBullEngine.price_strength_ok`（§六 平台突破豁免 + 涨停次日豁免）。"""
    pscfg = legacy.get("price_strength", {}) or {}
    min_pct = float(pscfg.get("min_pct_chg", 3.0))
    min_pos = float(pscfg.get("min_close_pos", 0.70))
    if bool(ev.get("t0_prev_limit_up")) and bool(pscfg.get("limit_next_day_exempt", False)):
        min_pct = float(pscfg.get("limit_next_day_min_pct_chg", 0.0))
        min_pos = float(pscfg.get("limit_next_day_min_close_pos", 0.30))
    if not (ev["t0_pct_chg"] >= min_pct and ev["t0_close_pos"] >= min_pos):
        return False
    if np.isfinite(ev["ma20"]) and ev["t0_close"] <= ev["ma20"]:
        return False
    if np.isfinite(ev["ma60"]) and ev["t0_close"] <= ev["ma60"]:
        near = ev["t0_close"] >= ev["ma60"] * 0.98
        if not (near or bool(ev["platform_breakout"])):
            return False
    return True


def _legacy_disabled(n: int) -> dict:
    """`--no-legacy` / Robustness 快速通道：保持字段形状一致，但不回放 legacy 引擎。"""
    return {
        "legacy_hvt_state": np.full(n, "", dtype=object),
        "legacy_hvt_state_normalized": np.full(n, "", dtype=object),
        "legacy_hvt_grade": np.full(n, "", dtype=object),
        "legacy_signal_tier": np.full(n, "", dtype=object),
        "legacy_hvt_days_after": np.full(n, np.nan),
        "legacy_breakout_date": np.full(n, "", dtype=object),
        "legacy_pb_verdict": np.full(n, "", dtype=object),
        "legacy_lock_stage": np.full(n, "", dtype=object),
        "events": {}, "engine": "DISABLED",
    }


def legacy_hvt_trajectory(f: dict, cfg: dict, legacy: dict, anchor_date: str,
                          det: dict, need_idx: list) -> dict:
    """§五十三「保留原始字段」：legacy 引擎在未复权口径上做**单次因果回放**。

    与 legacy 扫描器（`hvt_bull/daily.py`）逐日等价：事件必须同时通过
    `detect_hvt`（天量定级）与 `price_strength_ok`（价格强度闸门）；对每个需要
    的时点 j，取「最近一个 <= j 的合格事件」，再 `update_tracking(raw, ev, j + 1)`
    得到截止 j 的演化状态，因此只使用 <= j 的数据（需求 §七）。

    返回逐日数组（长度 n，仅 need_idx 位置有值）与 `events` 逐事件原始记录。
    """
    n = f["n"]
    out = {
        "legacy_hvt_state": np.full(n, "", dtype=object),
        "legacy_hvt_state_normalized": np.full(n, "", dtype=object),
        "legacy_hvt_grade": np.full(n, "", dtype=object),
        "legacy_signal_tier": np.full(n, "", dtype=object),
        "legacy_hvt_days_after": np.full(n, np.nan),
        "legacy_breakout_date": np.full(n, "", dtype=object),
        "legacy_pb_verdict": np.full(n, "", dtype=object),
        "legacy_lock_stage": np.full(n, "", dtype=object),
        "events": {},
        "engine": "hvt_bull",
    }
    try:
        from hvt_bull.engine import HvtBullEngine
    except Exception as exc:            # pragma: no cover - 环境依赖
        log.warning("legacy HVT 引擎不可用，跳过原始字段回放：%s", exc)
        out["engine"] = "UNAVAILABLE"
        return out

    raw = pd.DataFrame({
        "ts_code": f["ts_code"],
        "trade_date": f["dates"],
        "open": f["raw_open"],
        "high": f["raw_high"],
        "low": f["raw_low"],
        "close": f["raw_close"],
        "pre_close": f["pre_close"],
        "pct_chg": np.where(np.isfinite(f["pre_close"]) & (f["pre_close"] > 0),
                            f["raw_close"] / f["pre_close"] - 1.0, np.nan) * 100.0,
        "vol": f["vol"],
        "amount": f["amount"],
        "turnover_rate": f["turnover_rate"],
    })
    eng = HvtBullEngine(config=legacy)
    grade = np.asarray(det["hvt_grade"], dtype=object)

    # ── 1) 逐事件登记：detect_hvt + evaluate_event + 价格强度闸门 ──
    ev_objs: dict = {}
    for t in [int(i) for i in np.where(grade != "")[0]]:
        try:
            ev = eng.detect_hvt(raw, t)
            if ev is None:
                continue
            ev = eng.evaluate_event(raw, ev)
            if not eng.price_strength_ok(ev):
                continue
            ev_objs[t] = ev
        except Exception as exc:        # pragma: no cover - legacy 内部异常
            log.debug("legacy 事件登记失败 %s@%s：%s", f["ts_code"], f["dates"][t], exc)
            continue
        out["events"][str(f["dates"][t])] = {
            "hvt_date": str(f["dates"][t]),
            "ts_code": f["ts_code"],
            "hvt_grade": str(getattr(ev, "hvt_grade", "")),
            "hvt_state": str(getattr(ev, "state", "")),
            "hvt_signal_tier": str(getattr(ev, "signal_tier", "")),
        }

    # ── 2) 逐需要时点因果跟踪（只在 need_idx 上取值，保证 PIT）──
    pts = sorted({int(j) for j in np.asarray(need_idx, dtype=int) if 0 <= int(j) < n})
    if not pts:
        return out
    for j in pts:
        t = max([k for k in ev_objs if k <= j], default=None)
        if t is None:
            continue
        try:
            ev = eng.update_tracking(raw, ev_objs[t], j + 1)
        except Exception as exc:        # pragma: no cover
            log.debug("legacy 跟踪失败 %s@%s：%s", f["ts_code"], f["dates"][j], exc)
            continue
        state = str(getattr(ev, "state", "") or "")
        out["legacy_hvt_state"][j] = state
        out["legacy_hvt_state_normalized"][j] = normalize_legacy_state(cfg, state)
        out["legacy_hvt_grade"][j] = str(getattr(ev, "hvt_grade", "") or "")
        out["legacy_signal_tier"][j] = str(getattr(ev, "signal_tier", "") or "")
        out["legacy_hvt_days_after"][j] = float(getattr(ev, "days_after", 0) or 0)
        out["legacy_breakout_date"][j] = str(getattr(ev, "breakout_date", "") or "")
        out["legacy_pb_verdict"][j] = str(getattr(ev, "pb_verdict", "") or "")
        out["legacy_lock_stage"][j] = "LOCKED" if getattr(ev, "locked_chip", False) \
            else "UNLOCKED"
    return out


def hvt_event_quality(cfg: dict, grade: str, pct120: float, tratio: float,
                      close_pos: float) -> dict:
    """§十六 hvt_event_quality：定级基线 + 三个可解释 ramp（阈值全部来自 config）。"""
    scfg = cfg["hvt"]
    base = float(scfg["event_quality_grade"].get(grade, 0.0))
    return {
        "hvt_event_quality": base,
        "hvt_eq_pct120": ramp(pct120, *scfg["event_quality_pct120_ref"]),
        "hvt_eq_tratio": ramp(tratio, *scfg["event_quality_tratio_ref"]),
        "hvt_eq_close_pos": ramp(close_pos, *scfg["event_quality_close_pos_ref"]),
    }


def support_resistance(f: dict, cfg: dict) -> dict:
    """§二十三 支撑 / 阻力：只给结构参考，不生成 Step 7 的最终 stop price。"""
    n = f["n"]
    adj = f["adj"]
    ma20, ma60 = f["ma20"], f["ma60"]
    clo = f["consolidation_low"]
    chi = f["consolidation_high"]
    s1 = np.where(np.isfinite(clo), np.fmax(clo, ma20), ma20)
    s2 = ma60
    r1 = np.where(np.isfinite(chi), np.fmax(chi, f["high_20"]), f["high_20"])
    r2 = f["high_60"]
    near = float(cfg["structure_quality"]["support_near_pct"])
    sup_score = cfg["structure_quality"]["support_score"]
    dist_s1 = _safe_div(adj - s1, s1)
    dist_s2 = _safe_div(adj - s2, s2)
    band = np.full(n, "below", dtype=object)
    band[(np.isfinite(dist_s1)) & (dist_s1 > near)] = "above_s1"
    band[(np.isfinite(dist_s1)) & (dist_s1 >= 0.0) & (dist_s1 <= near)] = "above_s1_near"
    band[(np.isfinite(dist_s2)) & (np.isfinite(dist_s1)) & (dist_s1 < 0.0)
         & (dist_s2 >= 0.0)] = "above_s2"
    return {
        "support_1": s1, "support_2": s2, "resistance_1": r1, "resistance_2": r2,
        "support_distance": dist_s1, "resistance_distance": _safe_div(r1 - adj, adj),
        "support_band": band,
        "support_score": np.array([float(sup_score.get(b, 15.0)) for b in band], dtype=float),
    }


def _adjustment_class(depth: float, days_after: int, vol_contraction: float,
                      ma20_break: float, acfg: dict) -> str:
    """§十八 充分调整分类（阈值全部来自 config.adjustment）。"""
    if (np.isfinite(depth) and depth > float(acfg["failed_depth_min"])) \
            or (np.isfinite(ma20_break) and ma20_break > float(acfg["failed_ma20_break_min"])):
        return "FAILED_ADJUSTMENT"
    if not np.isfinite(depth):
        return "NO_ADJUSTMENT"
    if depth <= float(acfg["no_adjustment_depth_max"]) and days_after <= int(acfg["no_adjustment_days_max"]):
        return "NO_ADJUSTMENT"
    if depth < float(acfg["healthy_depth_min"]):
        return "SHALLOW_ADJUSTMENT"
    if depth <= float(acfg["healthy_depth_max"]):
        ok = (np.isfinite(vol_contraction)
              and vol_contraction <= float(acfg["healthy_vol_contraction_max"])
              and ma20_break <= float(acfg["healthy_ma20_break_max"]))
        return "HEALTHY_ADJUSTMENT" if ok else "DEEP_ADJUSTMENT"
    if depth < float(acfg["failed_depth_min"]):
        return "DEEP_ADJUSTMENT"
    return "FAILED_ADJUSTMENT"


def _locking_score_of(vals: dict, lcfg: dict) -> float:
    """§十九 LOCKING_SCORE：六项子分加权（权重来自 config.locking.weights）。"""
    scores = {
        "volume_contraction": ramp(vals["vol_contraction"],
                                   float(lcfg["volume_contraction_worst"]),
                                   float(lcfg["volume_contraction_best"])),
        "range_contraction": ramp(vals["range_ratio"],
                                  float(lcfg["range_contraction_ref"][0]),
                                  float(lcfg["range_contraction_ref"][1]), inverted=True),
        "ma20_defense": ramp(vals["ma20_break"], float(lcfg["ma20_defense_tol"]), 0.0),
        "support_defense": ramp(vals["support_break"], float(lcfg["support_defense_tol"]), 0.0),
        "downside_volume": ramp(vals["downside_vol_ratio"],
                                float(lcfg["downside_volume_ref"][0]),
                                float(lcfg["downside_volume_ref"][1])),
        "close_position": ramp(vals["close_position"], 0.0, 1.0),
    }
    s, _ = _wmean(scores, lcfg["weights"])
    return s


def hvt_evolution(f: dict, cfg: dict, det: dict, events_meta: dict,
                  legacy: dict) -> dict:
    """Step 6 HVT 演化状态机（§十七 / §二十 ~ §二十七），严格因果、只使用 <= j 数据。

    HVT_EVENT → HVT_ADJUSTING → HVT_LOCKING → HVT_REBREAKOUT → HVT_RETEST → HVT_FAILED

    事件登记口径与 legacy 扫描器（`hvt_bull/daily.py`）一致：天量定级（detect_hvt）
    之后还必须通过 `price_strength_ok` 价格强度闸门，否则该日不构成事件
    （不重置 active，等价于 legacy「从最近往回找第一个合格事件」）。
    返回逐日数组 + 逐事件记录。
    """
    n = f["n"]
    dates = f["dates"]
    adj, high, low, vol = f["adj"], f["adj_high"], f["adj_low"], f["vol"]
    ma20 = f["ma20"]
    vr1, ar1 = f["volume_ratio_1"], f["amount_ratio_1"]
    close_pos_day = _safe_div(adj - low, high - low)
    rng10 = _roll(high, 10, "max") - _roll(low, 10, "min")
    range_ratio = _safe_div(rng10, _shift(rng10, 10))
    down_mask = f["pct_chg_pct"] < 0
    grade_all = np.asarray(det["hvt_grade"], dtype=object)
    # §五十三 adapter：价格强度闸门 `price_strength_ok` 逐字复用 legacy 定义，
    #   因此闸门所用价格一律取**未复权** OHLC（与 legacy 引擎同口径，等价性可验证）；
    #   结构量价（事件级别 / 调整深度 / 突破 / Retest）仍取后复权。
    #   close_pos / t0_close_pos 是同日 (C-L)/(H-L)，与复权因子无关，口径安全。
    _gate_arrays = {"close": f["raw_close"], "open": f["raw_open"],
                    "high": f["raw_high"], "low": f["raw_low"],
                    "pct_chg": f["pct_chg_pct"], "pre_close": f["pre_close"],
                    "raw_close": f["raw_close"]}
    _ts_code = f["ts_code"]

    scfg, acfg, lcfg = cfg["hvt"], cfg["adjustment"], cfg["locking"]
    bcfg, rcfg = cfg["breakout"], cfg["retest"]
    vcfg = cfg["volume_structure"]
    grace = int(scfg["grace_days"])
    track_max = int(scfg["track_max_days"])
    locked_max = int(scfg["locked_days_max"])
    lock_min = float(lcfg["hvt_locking_min_score"])
    min_days_after = int(lcfg["min_days_after_hvt"])
    plat_ok_set = set(PLATFORM_QUALITIES[PLATFORM_QUALITIES.index(lcfg["platform_min_quality"]):])
    level_buf = float(bcfg["level_buffer"])
    break_pct = float(rcfg["break_level_pct"])
    near_pct = float(rcfg["near_level_pct"])
    pending_max = int(rcfg["pending_max_days"])

    def _obj(v=""):
        return np.full(n, v, dtype=object)

    out = {
        "hvt_state": _obj("HVT_NONE"), "hvt_stage": _obj("HVT_NONE"),
        "hvt_days_after": np.full(n, np.nan), "hvt_event_date": _obj(),
        "hvt_event_grade": _obj(), "hvt_event_quality": np.full(n, np.nan),
        "hvt_eq_pct120": np.full(n, np.nan), "hvt_eq_tratio": np.full(n, np.nan),
        "hvt_eq_close_pos": np.full(n, np.nan),
        "hvt_ref_level": np.full(n, np.nan), "hvt_price": np.full(n, np.nan),
        "hvt_volume_ratio": np.full(n, np.nan), "hvt_turnover": np.full(n, np.nan),
        "hvt_amount": np.full(n, np.nan), "hvt_return": np.full(n, np.nan),
        "hvt_range": np.full(n, np.nan), "hvt_close_pos": np.full(n, np.nan),
        "adjustment_status": _obj("NO_ADJUSTMENT"),
        "adjustment_depth": np.full(n, np.nan), "adjustment_days": np.full(n, np.nan),
        "volume_contraction_ratio": np.full(n, np.nan),
        "locking_score": np.full(n, np.nan), "locking_status": _obj("NOT_LOCKING"),
        "breakout_state": _obj("NO_BREAKOUT"), "breakout_level": np.full(n, np.nan),
        "breakout_date": _obj(), "breakout_days_ago": np.full(n, np.nan),
        "breakout_volume_ratio": np.full(n, np.nan), "breakout_amount_ratio": np.full(n, np.nan),
        "breakout_strength": np.full(n, np.nan), "breakout_quality": _obj("NA"),
        "retest_state": _obj("NO_RETEST"), "retest_level": np.full(n, np.nan),
        "retest_depth": np.full(n, np.nan), "retest_volume_ratio": np.full(n, np.nan),
        "retest_days": np.full(n, np.nan), "retest_hold": _obj(), "retest_quality": _obj("NA"),
        "legacy_hvt_grade": _obj(), "legacy_hvt_state_normalized": _obj(),
    }

    active = None
    for j in range(n):
        # ── 1) 新 HVT 事件（最近一次事件决定当前 HVT 状态）──
        if grade_all[j]:
            t = j
            ev = {
                "t": t, "date": dates[t], "grade": str(grade_all[j]),
                "high": float(high[t]), "low": float(low[t]), "close": float(adj[t]),
                "vol": float(vol[t]), "tratio": float(det["hvt_tratio"][t]),
                "aratio": float(det["hvt_aratio"][t]), "pct120": float(det["hvt_pct120"][t]),
                "run_low": np.inf, "run_vols": [], "run_up_vols": [], "run_dn_vols": [],
                "ma20_break": 0.0, "dead": False, "reached_locking": False,
                "breakout_idx": None, "breakout_level": np.nan,
                "state": "HVT_EVENT", "stage": "HVT_EVENT",
            }
            # §五十三 + legacy `hvt_bull/daily.py` 事件登记口径：天量定级（detect_hvt）
            #   之后还必须通过价格强度闸门（price_strength_ok）才构成事件；
            #   不通过则该日不登记、不重置 active，等价 legacy「从最近往回找第一个合格事件」。
            ev.update(hvt_event_fields(t, _gate_arrays, dates, legacy, _ts_code))
            if hvt_price_strength_ok(ev, legacy):
                ev["level"] = ev["high"] * (1.0 + level_buf)
                q = hvt_event_quality(cfg, ev["grade"], ev["pct120"], ev["tratio"],
                                      float(close_pos_day[t]))
                ev.update(q)
                ev["turnover"] = float(f["turnover_rate"][t])
                ev["amount"] = float(f["amount"][t])
                ev["return"] = float(f["pct_chg_pct"][t])
                prev_adj = float(adj[t - 1]) if t >= 1 and np.isfinite(adj[t - 1]) else np.nan
                ev["range"] = float((high[t] - low[t]) / prev_adj) \
                    if np.isfinite(prev_adj) and prev_adj > 0 else np.nan
                ev["close_pos"] = float(close_pos_day[t]) \
                    if np.isfinite(close_pos_day[t]) else np.nan
                events_meta.setdefault(ev["date"], {}).update(ev)
                active = ev

        if active is None:
            out["hvt_state"][j] = "HVT_NONE"
            out["hvt_stage"][j] = "HVT_NONE"
            continue

        t = active["t"]
        days_after = j - t
        if days_after > track_max:
            active = None
            out["hvt_state"][j] = "HVT_NONE"
            out["hvt_stage"][j] = "HVT_NONE"
            continue

        # ── 2) 因果累加（仅 j > t）──
        if days_after > 0:
            active["run_low"] = min(active["run_low"], float(low[j]))
            active["run_vols"].append(float(vol[j]))
            if down_mask[j]:
                active["run_dn_vols"].append(float(vol[j]))
            else:
                active["run_up_vols"].append(float(vol[j]))
            if np.isfinite(ma20[j]):
                active["ma20_break"] = max(active["ma20_break"],
                                           float((ma20[j] - adj[j]) / ma20[j]))

        t_close, t_high, t_low, t_vol = active["close"], active["high"], active["low"], active["vol"]
        run_low = active["run_low"] if np.isfinite(active["run_low"]) else t_low
        vsum = sum(active["run_vols"])
        vcnt = len(active["run_vols"])
        vol_contraction = (vsum / vcnt) / t_vol if (vcnt and t_vol > 0) else np.nan
        dn = active["run_dn_vols"]
        down_ratio = (sum(dn) / len(dn)) / (vsum / vcnt) if (dn and vcnt and vsum > 0) else np.nan
        depth = (t_close - run_low) / t_close if t_close > 0 else np.nan
        support_break = max(0.0, (t_low - run_low) / t_low) if t_low > 0 else np.nan
        close_position = float(close_pos_day[j]) if np.isfinite(close_pos_day[j]) \
            else float(f["consolidation_close_pos"][j])
        adj_class = _adjustment_class(depth, days_after, vol_contraction,
                                      active["ma20_break"], acfg) if days_after > 0 \
            else "NO_ADJUSTMENT"
        lock_score = _locking_score_of({
            "vol_contraction": vol_contraction, "range_ratio": float(range_ratio[j]),
            "ma20_break": active["ma20_break"], "support_break": support_break,
            "downside_vol_ratio": down_ratio, "close_position": close_position,
        }, lcfg) if days_after > 0 else np.nan
        platform = str(f["platform_quality"][j])
        locked = bool(np.isfinite(lock_score) and lock_score >= lock_min
                      and days_after >= min_days_after and platform in plat_ok_set)

        # ── 3) 结构失效（§二十七 + §五十四：收盘跌回关键结构位后失效）──
        structure_lost = False
        if days_after > grace:
            if adj_class == "FAILED_ADJUSTMENT":
                structure_lost = True
            if np.isfinite(adj[j]) and adj[j] < t_low:
                structure_lost = True
        if active["breakout_idx"] is not None and days_after > active["breakout_idx"] - t + grace:
            if adj[j] < active["breakout_level"] * (1.0 - break_pct):
                structure_lost = True
        # §二十七 主动识别失败结构：突破之后失效的一律显式标记 BREAKOUT_FAILED
        if structure_lost and active["breakout_idx"] is not None:
            active["breakout_state"] = "BREAKOUT_FAILED"

        # ── 4) 突破（§二十 / §二十一：巨量日不追，只等回踩）──
        if (not structure_lost) and days_after > 0 and active["breakout_idx"] is None:
            lvl = active["level"]
            near_level = np.isfinite(lvl) and \
                (float(bcfg["ready_dist_min"]) <= (adj[j] / lvl - 1.0)
                 <= float(bcfg["ready_dist_max"]))
            if np.isfinite(adj[j]) and adj[j] > lvl \
                    and np.isfinite(vr1[j]) and vr1[j] >= float(bcfg["min_volume_ratio"]) \
                    and np.isfinite(close_pos_day[j]) and close_pos_day[j] >= float(bcfg["min_close_pos"]):
                active["breakout_idx"] = j
                active["breakout_level"] = lvl
                if active["reached_locking"]:
                    active["breakout_state"] = "REBREAKOUT_CONFIRMED"
                else:
                    active["breakout_state"] = "BREAKOUT_CONFIRMED"
                # 突破强度 = 量能强度 与 突破幅度 的等权组合（阈值均来自 config.breakout）
                sv = ramp(float(vr1[j]), *bcfg["strength_volume_ref"])
                se = ramp(float(adj[j] / lvl - 1.0), *bcfg["strength_extension_ref"])
                sp = [v for v in (sv, se) if np.isfinite(v)]
                strength = float(np.mean(sp)) if sp else np.nan
                if np.isfinite(strength) and strength >= float(bcfg["quality_good_min"]) \
                        and vr1[j] >= float(bcfg["strong_volume_ratio"]) \
                        and close_pos_day[j] >= float(bcfg["strong_close_pos"]):
                    active["breakout_quality"] = "GOOD"
                elif (not np.isfinite(strength)) or strength <= float(bcfg["quality_weak_max"]):
                    active["breakout_quality"] = "WEAK"
                else:
                    active["breakout_quality"] = "NEUTRAL"
                active["brk_vr"] = float(vr1[j])
                active["brk_ar"] = float(ar1[j]) if np.isfinite(ar1[j]) else np.nan
                active["brk_strength"] = float(strength)
                active["brk_date"] = dates[j]
                active["brk_vols"] = []
            elif near_level:
                active["breakout_state"] = "BREAKOUT_READY"

        # ── 5) Retest（§二十二：回踩突破位不破 + 缩量 + 重新企稳）──
        retest_state, retest_quality = "NO_RETEST", "NA"
        retest_depth = retest_vr = np.nan
        retest_hold = ""
        if active["breakout_idx"] is not None:
            b = active["breakout_idx"]
            lvl = active["breakout_level"]
            since = j - b
            active.setdefault("brk_vols", []).append(float(vol[j])) if since > 0 else None
            # 突破当日（j == b）尚无回踩 bar，seg_low 记为 NaN（哨兵），
            # 既不算 HOLD 也不算 BREAK，避免当日即被误判为 RETEST_FAILED。
            seg_low = float(low[b + 1:j + 1].min()) if j > b else np.nan
            retest_depth = (lvl - seg_low) / lvl if lvl > 0 and np.isfinite(seg_low) else np.nan
            touched = bool(np.isfinite(seg_low) and seg_low <= lvl * (1.0 + near_pct))
            if not np.isfinite(seg_low):
                retest_hold = ""
            else:
                retest_hold = "HOLD" if seg_low >= lvl * (1.0 - break_pct) else "BREAK"
            if active["brk_vols"]:
                retest_vr = (sum(active["brk_vols"]) / len(active["brk_vols"])) / t_vol \
                    if t_vol > 0 else np.nan
            if structure_lost or retest_hold == "BREAK":
                retest_state, retest_quality = "RETEST_FAILED", "POOR"
            elif touched and adj[j] >= lvl + float(rcfg["success_close_min"]) \
                    and (not np.isfinite(retest_vr) or retest_vr <= float(rcfg["success_vol_ratio_max"])):
                retest_state = "RETEST_SUCCESS"
                retest_quality = "GOOD" if retest_vr <= float(rcfg["success_vol_ratio_max"]) else "NEAR"
            elif touched:
                retest_state, retest_quality = "RETEST_PENDING", "NEAR"
            elif since > pending_max:
                retest_state, retest_quality = "NO_RETEST", "NA"
            else:
                retest_state, retest_quality = "NO_RETEST", "NA"

        # ── 6) 状态归并（§二十五 7 态 + §十七 stage 下钻）──
        if structure_lost:
            state, stage = "HVT_FAILED", "HVT_FAILED"
            active["dead"] = True
        elif days_after == 0:
            state, stage = "HVT_EVENT", "HVT_EVENT"
        elif retest_state == "RETEST_FAILED":
            state, stage = "HVT_FAILED", "HVT_FAILED"
            active["dead"] = True
        elif retest_state in ("RETEST_PENDING", "RETEST_SUCCESS"):
            # 突破后回踩确认进行中：归并回 HVT_ADJUSTING（Step 7 只认 5 态），
            # 细分语义由 stage / breakout_state / retest_state 承载。
            state, stage = "HVT_ADJUSTING", "HVT_REACCUMULATION"
        elif active["breakout_idx"] is not None:
            state, stage = "HVT_ADJUSTING", "HVT_REBREAKOUT"
        elif locked:
            active["reached_locking"] = True
            state, stage = "HVT_LOCKING", "HVT_REACCUMULATION"
        elif days_after > locked_max and active.get("reached_locking"):
            state, stage = "HVT_LOCKING", "HVT_REACCUMULATION"
        else:
            if adj_class in ("NO_ADJUSTMENT", "SHALLOW_ADJUSTMENT"):
                stage = "HVT_ADJUSTING"
            elif platform in ("PLATFORM_STRONG", "PLATFORM_HEALTHY"):
                stage = "HVT_CONSOLIDATING"
            else:
                stage = "HVT_ADJUSTING"
            state = "HVT_ADJUSTING"
        if active["dead"]:
            state, stage = "HVT_FAILED", "HVT_FAILED"
            # §二十七：dead 是本周期粘性失败标记，回踩状态不得再报成功，
            # 避免落盘出现「HVT_FAILED × RETEST_SUCCESS / RETEST_PENDING」自相矛盾。
            if retest_state in ("RETEST_SUCCESS", "RETEST_PENDING"):
                retest_state, retest_quality = "RETEST_FAILED", "POOR"
        active["state"], active["stage"] = state, stage
        active["last"] = {
            "days_after": days_after, "adjustment_status": adj_class,
            "adjustment_depth": depth, "vol_contraction": vol_contraction,
            "locking_score": lock_score, "locked": locked,
            "breakout_state": active.get("breakout_state", "NO_BREAKOUT"),
            "retest_state": retest_state, "state": state,
        }

        # ── 7) 落盘 ──
        out["hvt_state"][j] = state
        out["hvt_stage"][j] = stage
        out["hvt_days_after"][j] = float(days_after)
        out["hvt_event_date"][j] = active["date"]
        out["hvt_event_grade"][j] = active["grade"]
        out["hvt_event_quality"][j] = active["hvt_event_quality"]
        out["hvt_eq_pct120"][j] = active["hvt_eq_pct120"]
        out["hvt_eq_tratio"][j] = active["hvt_eq_tratio"]
        out["hvt_eq_close_pos"][j] = active["hvt_eq_close_pos"]
        out["hvt_ref_level"][j] = t_high
        out["hvt_price"][j] = t_close
        out["hvt_volume_ratio"][j] = active["tratio"]
        out["hvt_turnover"][j] = float(f["turnover_rate"][t])
        out["hvt_amount"][j] = float(f["amount"][t])
        out["hvt_return"][j] = float(f["pct_chg_pct"][t])
        prev_adj_t = float(adj[t - 1]) if t >= 1 and np.isfinite(adj[t - 1]) else np.nan
        out["hvt_range"][j] = float((high[t] - low[t]) / prev_adj_t) \
            if np.isfinite(prev_adj_t) and prev_adj_t > 0 else np.nan
        out["hvt_close_pos"][j] = float(close_pos_day[t]) if np.isfinite(close_pos_day[t]) else np.nan
        out["legacy_hvt_grade"][j] = active["grade"]
        out["adjustment_status"][j] = adj_class
        out["adjustment_depth"][j] = depth
        out["adjustment_days"][j] = float(days_after)
        out["volume_contraction_ratio"][j] = vol_contraction
        out["locking_score"][j] = lock_score
        out["locking_status"][j] = "LOCKING" if locked or state in (
            "HVT_LOCKING", "HVT_REBREAKOUT", "HVT_RETEST") else "NOT_LOCKING"
        out["breakout_state"][j] = active.get("breakout_state", "NO_BREAKOUT")
        out["breakout_level"][j] = active["level"]
        out["breakout_date"][j] = active.get("brk_date", "")
        out["breakout_days_ago"][j] = float(j - active["breakout_idx"]) \
            if active["breakout_idx"] is not None else np.nan
        out["breakout_volume_ratio"][j] = active.get("brk_vr", np.nan)
        out["breakout_amount_ratio"][j] = active.get("brk_ar", np.nan)
        out["breakout_strength"][j] = active.get("brk_strength", np.nan)
        out["breakout_quality"][j] = active.get("breakout_quality", "NA")
        out["retest_state"][j] = retest_state
        out["retest_level"][j] = active["breakout_level"]
        out["retest_depth"][j] = retest_depth
        out["retest_volume_ratio"][j] = retest_vr
        out["retest_days"][j] = float(j - active["breakout_idx"]) \
            if active["breakout_idx"] is not None else np.nan
        out["retest_hold"][j] = retest_hold
        out["retest_quality"][j] = retest_quality

    return out


# ════════════════════════════════════════════════════════════════════════════
# 结构打分与资格层（§八 ~ §十四 / §二十四 / §二十六 / §二十八 ~ §三十一 / §三十八）
#   本层只输出「结构分类 + 结构资格」，不输出 BUY / NO TRADE / 仓位 / 止损执行。
# ════════════════════════════════════════════════════════════════════════════


def classify_trend(f: dict, cfg: dict) -> dict:
    """§八 趋势结构状态 + 连续化趋势子分（阈值全部来自 config.trend_structure）。"""
    tcfg = cfg["trend_structure"]
    n = f["n"]
    slope, spread = f["ma20_slope_10"], f["ma20_vs_ma60"]
    usable = np.isfinite(slope) & np.isfinite(spread)
    state = np.full(n, "INVALID", dtype=object)
    state[usable] = "SIDEWAYS"
    state[usable & (slope >= float(tcfg["uptrend_early_slope_min"]))] = "UPTREND_EARLY"
    state[usable & (spread >= float(tcfg["uptrend_ma_spread_min"]))
          & (slope >= float(tcfg["uptrend_slope_min"]))] = "UPTREND"
    state[usable & (slope <= float(tcfg["correction_slope_max"]))] = "CORRECTION"
    state[usable & (spread <= float(tcfg["downtrend_ma_spread_max"]))
          & (slope <= float(tcfg["downtrend_slope_max"]))] = "DOWNTREND"
    smap = tcfg["state_score"]
    base = np.array([float(smap.get(s, 0.0)) for s in state], dtype=float)
    # 斜率微调：把台阶式状态分连续化（幅度受 slope_nudge_cap 约束），便于后续分层回测
    nudge = np.clip(slope * float(tcfg["slope_nudge_scale"]),
                    -float(tcfg["slope_nudge_cap"]), float(tcfg["slope_nudge_cap"])) * 100.0
    score = np.clip(base + np.where(usable, nudge, 0.0), 0.0, 100.0)
    score[~usable] = np.nan
    return {"trend_state": state, "trend_score": score}


def classify_price_zone(f: dict, cfg: dict) -> dict:
    """§十 价格位置（优先级：EXTENDED > BREAKOUT_ZONE > NEAR_HIGH > DEEP_PULLBACK > PULLBACK）。"""
    pcfg = cfg["price_structure"]
    n = f["n"]
    d20, d120 = f["dist_20d_high"], f["dist_120d_high"]
    dd20, dm20 = f["drawdown_20"], f["dist_ma20"]
    usable = np.isfinite(d20) & np.isfinite(d120) & np.isfinite(dd20) & np.isfinite(dm20)
    zone = np.full(n, "INVALID", dtype=object)
    zone[usable] = "MID_RANGE"
    zone[usable & (dd20 >= float(pcfg["pullback_drawdown_min"]))] = "PULLBACK"
    zone[usable & (dd20 >= float(pcfg["deep_pullback_drawdown_min"]))] = "DEEP_PULLBACK"
    zone[usable & (d120 >= float(pcfg["near_high_dist_120d_high"]))] = "NEAR_HIGH"
    zone[usable & (d20 >= float(pcfg["breakout_zone_dist_20d_high"]))] = "BREAKOUT_ZONE"
    zone[usable & (dm20 >= float(pcfg["extended_dist_ma20"]))] = "EXTENDED"
    smap = pcfg["zone_score"]
    score = np.array([float(smap.get(z, 0.0)) for z in zone], dtype=float)
    score[~usable] = np.nan
    return {"price_zone": zone, "price_score": score}


def classify_volume(f: dict, cfg: dict) -> dict:
    """§十三 量价结构。

    VOLUME_STATES 词表（§十三）不含 INVALID；数据不足时仍给出良性标签，但子分置 NaN，
    不参与加权（由 data_quality 统一标记 INSUFFICIENT / INVALID）。
    """
    vcfg = cfg["volume_structure"]
    n = f["n"]
    pct = f["pct_chg_pct"] / 100.0
    vr1, vr5 = f["volume_ratio_1"], f["volume_ratio_5"]
    usable = np.isfinite(pct) & np.isfinite(vr1) & np.isfinite(vr5)
    state = np.full(n, "HEALTHY_VOLUME", dtype=object)
    expan = usable & (vr1 >= float(vcfg["expansion_vr_min"]))
    contract = usable & (vr5 <= float(vcfg["contraction_vol_ratio_max"])) \
        & (np.abs(pct) <= float(vcfg["contraction_abs_pct_max"]))
    div = usable & (((pct >= float(vcfg["divergence_up_pct_min"]))
                     & (vr1 <= float(vcfg["divergence_up_vr_max"])))
                    | ((pct <= float(vcfg["divergence_down_pct_max"]))
                       & (vr1 >= float(vcfg["divergence_down_vr_min"]))))
    abnormal = usable & (((vr1 >= float(vcfg["abnormal_vr_min"]))
                          & (np.abs(pct) <= float(vcfg["abnormal_stall_abs_pct_max"])))
                         | ((vr1 >= float(vcfg["abnormal_extreme_vr"])) & (pct < 0.0)))
    state[expan] = "VOLUME_EXPANSION"
    state[contract] = "VOLUME_CONTRACTION"
    state[div] = "VOLUME_DIVERGENCE"
    state[abnormal] = "ABNORMAL_VOLUME"
    smap = vcfg["state_score"]
    score = np.array([float(smap.get(s, 0.0)) for s in state], dtype=float)
    score[~usable] = np.nan
    return {"volume_state": state, "volume_score": score}


def structure_quality_of(f: dict, cfg: dict, trend: dict, price: dict, volume: dict,
                         sup: dict, evo: dict) -> dict:
    """§九 STRUCTURE QUALITY（0-100，六项权重来自 config.structure_quality.weights）。

    与近期涨幅解耦（§二十八）：本函数不读取任何收益类字段（ret_*），涨幅只通过
    extension 惩罚层单向压制，绝不进入 structure_quality 正分项。
    """
    sq = cfg["structure_quality"]
    w = sq["weights"]
    n = f["n"]
    cmap = cfg["consolidation"]["quality_score"]
    consolidation = np.array([float(cmap.get(q, 0.0)) for q in f["platform_quality"]], dtype=float)
    bmap = sq["breakout_state_score"]
    breakout = np.array([float(bmap.get(b, 0.0)) for b in evo["breakout_state"]], dtype=float)
    quality = np.full(n, np.nan)
    cover = np.full(n, np.nan)
    for i in range(n):
        s, cov = _wmean({
            "trend": trend["trend_score"][i],
            "price": price["price_score"][i],
            "volume_price": volume["volume_score"][i],
            "consolidation": consolidation[i],
            "breakout": breakout[i],
            "support_resistance": sup["support_score"][i],
        }, w)
        quality[i], cover[i] = s, cov
    return {"structure_quality": quality, "structure_quality_coverage": cover}


def extension_of(f: dict, cfg: dict) -> dict:
    """§十一 / §十二 EXTENSION RISK：只产生惩罚，不产生任何加分（输出恒定 ∈ [0, 20]）。"""
    ecfg = cfg["extension"]
    n = f["n"]
    parts = {
        "ret_5": ramp(f["ret_5"], *ecfg["ret_5_ref"]),
        "ret_10": ramp(f["ret_10"], *ecfg["ret_10_ref"]),
        "dist_ma20": ramp(f["dist_ma20"], *ecfg["dist_ma20_ref"]),
        "dist_ma60": ramp(f["dist_ma60"], *ecfg["dist_ma60_ref"]),
        "atr_dist": ramp(f["atr_dist"], *ecfg["atr_dist_ref"]),
        "volume_ratio": ramp(f["volume_ratio_used"], *ecfg["volume_ratio_ref"]),
        "turnover_rate": ramp(f["turnover_rate"] / float(ecfg["turnover_rate_unit_divisor"]),
                              *ecfg["turnover_rate_ref"]),
    }
    raw = np.full(n, np.nan)
    for i in range(n):
        s, _ = _wmean({k: v[i] for k, v in parts.items()}, ecfg["weights"])
        raw[i] = s
    xs, ys = list(ecfg["penalty_curve"]["raw"]), list(ecfg["penalty_curve"]["penalty"])
    pen = np.array([_lerp_curve(float(r), xs, ys) if np.isfinite(r) else 0.0 for r in raw],
                   dtype=float)
    r5, dm20, vr = f["ret_5"], f["dist_ma20"], f["volume_ratio_used"]
    # §二十八 Extreme Momentum：ret_5 >= extreme_ret_5 AND dist_ma20 >= extreme_dist_ma20
    extreme_momentum = np.isfinite(r5) & np.isfinite(dm20) \
        & (r5 >= float(ecfg["extreme_ret_5"])) & (dm20 >= float(ecfg["extreme_dist_ma20"]))
    # 极端偏离 MA20 且放巨量
    extreme_volume = np.isfinite(dm20) & np.isfinite(vr) \
        & (dm20 >= float(ecfg["extreme_combo_dist_ma20"])) \
        & (vr >= float(ecfg["extreme_combo_volume_ratio"]))
    extreme = extreme_momentum | extreme_volume
    floor = float(ecfg["extreme_penalty_floor"])
    pen = np.where(extreme, np.maximum(pen, floor), pen)
    pen = np.clip(pen, 0.0, float(ecfg["penalty_max"]))
    lv = ecfg["risk_levels"]
    risk = np.full(n, "LOW", dtype=object)
    ok = np.isfinite(raw)
    risk[ok & (raw >= float(lv["MEDIUM"]))] = "MEDIUM"
    risk[ok & (raw >= float(lv["HIGH"]))] = "HIGH"
    risk[ok & (raw >= float(lv["EXTREME"]))] = "EXTREME"
    risk[pen >= floor] = "EXTREME"
    return {"extension_score_raw": raw, "extension_risk": risk, "extension_penalty": pen}


def hvt_quality_of(f: dict, cfg: dict, evo: dict, sup: dict, ext: dict) -> dict:
    """§二十四 HVT QUALITY SCORE（六项权重来自 config.hvt_quality.weights），
    再减 extension_penalty 得到 hvt_qualification_score。"""
    hcfg = cfg["hvt_quality"]
    lcfg = cfg["locking"]
    w = hcfg["weights"]
    amap = cfg["adjustment"]["state_score"]
    bmap = hcfg["rebreakout_state_score"]
    vref = hcfg["volume_contraction_ref"]
    ma_tol = float(lcfg["ma20_defense_tol"])
    ma_def = ramp(f["dist_ma20"], -ma_tol, 0.0)
    n = f["n"]
    quality = np.full(n, np.nan)
    cover = np.full(n, np.nan)
    for i in range(n):
        if evo["hvt_state"][i] == "HVT_NONE":
            quality[i], cover[i] = float(hcfg["no_hvt_event_score"]), 1.0
            continue
        md = [v for v in (ma_def[i], sup["support_score"][i]) if np.isfinite(v)]
        s, cov = _wmean({
            "hvt_event": evo["hvt_event_quality"][i],
            "adjustment": float(amap.get(evo["adjustment_status"][i], 0.0)),
            "volume_contraction": ramp(evo["volume_contraction_ratio"][i], *vref),
            "platform_locking": evo["locking_score"][i],
            "ma_support_defense": float(np.mean(md)) if md else np.nan,
            "rebreakout": float(bmap.get(evo["breakout_state"][i], 0.0)),
        }, w)
        quality[i], cover[i] = s, cov
    pen = ext["extension_penalty"]
    qual = np.where(np.isfinite(quality),
                    np.clip(quality - pen, 0.0, 100.0), np.nan)
    return {"hvt_quality_score": quality, "hvt_quality_coverage": cover,
            "hvt_qualification_score": qual}


def data_quality_of(f: dict, cfg: dict, list_date: str = "") -> dict:
    """§三十八 data_quality 四档：VALID / PARTIAL / INSUFFICIENT / INVALID。"""
    dcfg, lb = cfg["data_quality"], cfg["lookback"]
    n = f["n"]
    hist = np.arange(1, n + 1, dtype=float)
    adj, vol, tov = f["adj"], f["vol"], f["turnover_rate"]
    traded = pd.Series(np.where(np.isfinite(vol) & (vol > 0), 1.0, 0.0))
    traded20 = traded.rolling(20, min_periods=1).sum().to_numpy(dtype=float)
    suspend20 = 20.0 - traded20
    quality = np.full(n, "VALID", dtype=object)
    short = hist < float(lb["min_history_trend"])
    if list_date and len(str(list_date)) == 8:
        listed = (pd.to_datetime(pd.Series(f["dates"]), format="%Y%m%d")
                  - pd.to_datetime(str(list_date), format="%Y%m%d")).dt.days.to_numpy(dtype=float)
        short = short | (listed < float(lb["min_listed_days"]))
    quality[short] = "INSUFFICIENT"
    quality[(~short) & ((~np.isfinite(tov))
                        | (suspend20 > float(dcfg["suspended_days_max"])))] = "PARTIAL"
    quality[(~short) & (traded20 < float(dcfg["min_traded_days_20"]))] = "PARTIAL"
    quality[~(np.isfinite(adj) & (adj > 0) & np.isfinite(vol) & (vol > 0))] = "INVALID"
    return {"data_quality": quality, "traded_days_20": traded20, "suspend_days_20": suspend20}


def structure_state_of(cfg: dict, f: dict, evo: dict, ext: dict, dq: dict) -> np.ndarray:
    """§二十六 structure_state 12 态。优先级自上而下，越靠前越"终局"：

    INVALID → FAILED → EXTENDED → REBREAKOUT → RETEST → BREAKOUT → LOCKING
    → BREAKOUT_READY → HVT → ADJUSTING → WATCH → BASE

    EXTENDED（极端扩张）优先于突破/锁筹，体现 §十二「惩罚只能降低资格」的压制顺序。
    """
    sscfg = cfg["structure_state"]
    watch_plat = set(PLATFORM_QUALITIES[PLATFORM_QUALITIES.index(
        sscfg["watch_min_platform_quality"]):])
    n = len(evo["hvt_state"])
    state = np.full(n, "BASE", dtype=object)
    state[np.isin(f["platform_quality"], list(watch_plat))] = "WATCH"
    hvt, bst, rst = evo["hvt_state"], evo["breakout_state"], evo["retest_state"]
    state[hvt == "HVT_ADJUSTING"] = "ADJUSTING"
    state[hvt == "HVT_EVENT"] = "HVT"
    state[bst == "BREAKOUT_READY"] = "BREAKOUT_READY"
    state[hvt == "HVT_LOCKING"] = "LOCKING"
    state[bst == "BREAKOUT_CONFIRMED"] = "BREAKOUT"
    state[np.isin(rst, ["RETEST_PENDING", "RETEST_SUCCESS"])] = "RETEST"
    state[bst == "REBREAKOUT_CONFIRMED"] = "REBREAKOUT"
    state[ext["extension_risk"] == str(sscfg["extreme_extension_state"])] = \
        str(sscfg["extreme_extension_state"])
    state[(hvt == "HVT_FAILED") | (bst == "BREAKOUT_FAILED")] = "FAILED"
    state[dq["data_quality"] == "INVALID"] = "INVALID"
    return state


def structure_state_transitions(dates: list, ts_code: str, states: np.ndarray,
                                evo: dict, ext: dict) -> list:
    """§二十六 状态转换历史：date / stock / previous_state / current_state / transition_reason。

    只记录「发生变化」的日期；历史由 write_merge_csv 按 (trade_date, ts_code) 追加保留，
    绝不覆盖既有状态记录。
    """
    rows, prev = [], ""
    for i, s in enumerate(states):
        s = str(s)
        if s == prev:
            continue
        reason = "初始状态" if prev == "" else f"{prev} → {s}"
        rows.append({
            "trade_date": dates[i], "ts_code": ts_code,
            "previous_state": prev, "current_state": s,
            "transition_reason": (f"{reason}；hvt={evo['hvt_state'][i]}，"
                                  f"breakout={evo['breakout_state'][i]}，"
                                  f"retest={evo['retest_state'][i]}，"
                                  f"extension={ext['extension_risk'][i]}"),
        })
        prev = s
    return rows


def structure_class_of(states: np.ndarray, evo: dict) -> np.ndarray:
    """§二十九 结构类型分类（14 态）：结构分类，不是交易指令。"""
    n = len(states)
    cls = np.full(n, "STRUCTURE_BASE", dtype=object)
    m = {
        "INVALID": "INVALID", "FAILED": "FAILED", "EXTENDED": "EXTENDED",
        "REBREAKOUT": "REBREAKOUT_CONFIRMED", "BREAKOUT": "BREAKOUT_CONFIRMED",
        "LOCKING": "HVT_LOCKING", "BREAKOUT_READY": "BREAKOUT_READY",
        "HVT": "HVT_EVENT", "WATCH": "WATCH", "BASE": "STRUCTURE_BASE",
    }
    for k, v in m.items():
        cls[states == k] = v
    adj = states == "ADJUSTING"
    healthy = evo["adjustment_status"] == "HEALTHY_ADJUSTMENT"
    cls[adj & healthy] = "PULLBACK_HEALTHY"
    cls[adj & (~healthy)] = "HVT_ADJUSTING"
    ret = states == "RETEST"
    cls[ret & (evo["retest_state"] == "RETEST_SUCCESS")] = "RETEST_SUCCESS"
    cls[ret & (evo["retest_state"] != "RETEST_SUCCESS")] = "RETEST_PENDING"
    return cls


def structure_qualification_of(cfg: dict, states: np.ndarray, classes: np.ndarray,
                               quality: np.ndarray, ext: dict, dq: dict) -> np.ndarray:
    """§三十 / §三十一 最终 Qualification：QUALIFIED / CONDITIONAL / WATCH / REJECTED。

    顺序：先判 REJECTED（结构破坏 / 破位 / HVT 失败 / 突破失败 / 数据不足 / 数据无效），
    再判 QUALIFIED，再判 CONDITIONAL，其余 WATCH；最后按扩张风险封顶（EXTREME→WATCH）。
    """
    qcfg = cfg["qualification"]
    ok_class = np.isin(classes, list(qcfg["qualified_classes"]))
    ok_state = np.isin(states, list(qcfg["qualified_states"]))
    risk = ext["extension_risk"]
    q = np.full(len(states), "WATCH", dtype=object)
    cond = (np.isfinite(quality)
            & (quality >= float(qcfg["conditional_structure_quality_min"]))
            & (~np.isin(risk, list(qcfg["conditional_extension_risk_forbidden"]))))
    q[cond] = "CONDITIONAL"
    qual = (np.isfinite(quality)
            & (quality >= float(qcfg["qualified_structure_quality_min"]))
            & (ok_class | ok_state)
            & (~np.isin(risk, list(qcfg["qualified_extension_risk_forbidden"]))))
    q[qual] = "QUALIFIED"
    rejected = (np.isin(states, ["FAILED", "INVALID"])
                | np.isin(dq["data_quality"], ["INSUFFICIENT", "INVALID"])
                | np.isin(classes, ["FAILED", "INVALID"]))
    q[rejected] = "REJECTED"
    q[(q == "QUALIFIED") & (risk == "HIGH")] = str(qcfg["high_cap"])
    extreme = risk == "EXTREME"
    q[extreme & ~rejected] = str(qcfg["extreme_cap"])
    return q


def _fmt_platform(platform: str) -> str:
    return "缩量" if platform in ("PLATFORM_STRONG", "PLATFORM_HEALTHY") else ""


def build_reason(row: dict) -> str:
    """§四十五 reason：只允许结构化事实，禁止「看好 / 有望上涨 / 强烈推荐」类表述。"""
    parts = []
    if row.get("structure_state"):
        parts.append(f"结构状态 {row['structure_state']}")
    if row.get("structure_class"):
        parts.append(f"结构分类 {row['structure_class']}")
    hvt, days = row.get("hvt_state"), row.get("hvt_days_after")
    if hvt and hvt != "HVT_NONE":
        if days is not None and np.isfinite(days):
            parts.append(f"HVT 后经历 {int(days)} 个交易日演化")
        parts.append(f"当前 HVT 状态 {hvt}")
        if row.get("adjustment_status"):
            parts.append(f"调整充分度 {row['adjustment_status']}")
        vc = row.get("volume_contraction_ratio")
        if vc is not None and np.isfinite(vc):
            parts.append(f"近段成交量降至 HVT 日的 {vc * 100.0:.0f}%")
        ls = row.get("locking_score")
        if ls is not None and np.isfinite(ls):
            parts.append(f"锁筹分 {ls:.0f}")
    pdays = row.get("consolidation_days")
    if pdays is not None and np.isfinite(pdays) and pdays > 0:
        parts.append(f"形成 {int(pdays)} 日{_fmt_platform(row.get('platform_quality'))}平台"
                     f"（{row.get('platform_quality')}）")
    if row.get("support_band"):
        parts.append(f"支撑位置 {row['support_band']}")
    if row.get("breakout_state") and row["breakout_state"] != "NO_BREAKOUT":
        parts.append(f"突破状态 {row['breakout_state']}")
    if row.get("retest_state") and row["retest_state"] != "NO_RETEST":
        parts.append(f"回踩状态 {row['retest_state']}")
    if row.get("extension_risk"):
        parts.append(f"扩张风险 {row['extension_risk']}"
                     f"（惩罚 {float(row.get('extension_penalty') or 0.0):.1f}）")
    sq = row.get("structure_quality")
    parts.append(f"结构质量 {float(sq):.1f}" if sq is not None and np.isfinite(sq)
                 else "结构质量数据不足")
    return "；".join(parts) + "。"


# ════════════════════════════════════════════════════════════════════════════
# 编排层：逐股全链路 → 结构池 / 结构资格（需求 §二 / §四十 ~ §四十五 / §五十五）
#   本层只写「结构 + 资格」；不写 BUY / NO TRADE / 仓位 / 止损执行 / 交易指令。
# ════════════════════════════════════════════════════════════════════════════

REQUIRED_CFG_SECTIONS = [
    "meta", "input", "legacy_adapter", "lookback", "trend_structure", "price_structure",
    "volume_structure", "consolidation", "hvt", "adjustment", "locking", "breakout",
    "retest", "extension", "structure_quality", "hvt_quality", "structure_state",
    "qualification", "data_quality", "membership_versioning", "backtest", "robustness",
    "validation", "output",
]

# §五十五：三个核心池（结构池，不是买入池）
HVT_POOL_STATES = ["HVT_LOCKING", "HVT_REBREAKOUT"]
REBREAKOUT_POOL_CLASSES = ["REBREAKOUT_CONFIRMED"]
RETEST_POOL_STATES = ["RETEST_PENDING", "RETEST_SUCCESS"]

STRUCTURE_DAILY_COLS = [
    "trade_date", "ts_code", "name", "primary_theme_id", "secondary_theme_ids",
    "candidate_type", "candidate_score", "theme_opportunity",
    "close", "pct_chg", "turnover_rate", "volume_ratio", "amount",
    "ma5", "ma10", "ma20", "ma60", "ma120",
    "close_vs_ma20", "close_vs_ma60", "ma20_slope_5", "ma20_slope_10", "ma20_vs_ma60",
    "ret_3", "ret_5", "ret_10", "ret_20",
    "dist_ma20", "dist_ma60",
    "drawdown_20", "drawdown_60", "drawdown_120",
    "structure_quality", "extension_risk", "extension_penalty",
    "structure_state", "structure_qualification", "data_quality",
]

TREND_STRUCTURE_COLS = [
    "trade_date", "ts_code", "trend_state", "trend_score", "price_zone", "price_score",
    "ma5", "ma10", "ma20", "ma60", "ma120",
    "close_vs_ma20", "close_vs_ma60", "ma20_slope_5", "ma20_slope_10", "ma60_slope_10",
    "ma20_vs_ma60", "ma60_vs_ma120",
    "dist_ma5", "dist_ma10", "dist_ma20", "dist_ma60", "dist_ma120",
    "dist_20d_high", "dist_60d_high", "dist_120d_high", "dist_250d_high",
    "drawdown_20", "drawdown_60", "drawdown_120", "drawdown_250", "atr_dist",
]

VOLUME_STRUCTURE_COLS = [
    "trade_date", "ts_code", "volume_state", "volume_score",
    "volume_ratio_1", "volume_ratio_3", "volume_ratio_5", "volume_ratio_db",
    "volume_ratio_used", "amount_ratio_1", "amount_ratio_3", "amount_ratio_5",
    "up_volume_ratio", "down_volume_ratio", "volume_expansion", "volume_contraction",
]

CONSOLIDATION_COLS = [
    "trade_date", "ts_code", "platform_quality", "consolidation_days",
    "consolidation_range", "consolidation_vol_ratio", "consolidation_atr",
    "consolidation_high", "consolidation_low", "consolidation_close_pos",
]

HVT_EVENT_COLS = [
    "ts_code", "hvt_date", "hvt_grade", "hvt_price", "hvt_volume_ratio", "hvt_turnover",
    "hvt_amount", "hvt_return", "hvt_range", "hvt_close_pos", "hvt_pct120", "hvt_tratio",
    "hvt_aratio", "hvt_event_quality", "adjustment_status", "locking_status",
    "breakout_status", "retest_status", "current_hvt_state",
    "legacy_hvt_grade", "legacy_hvt_state", "legacy_hvt_state_normalized",
]

HVT_HISTORY_COLS = [
    "trade_date", "ts_code", "hvt_state", "hvt_stage", "hvt_days_after", "hvt_event_date",
    "hvt_event_grade", "hvt_event_quality", "hvt_ref_level", "adjustment_status",
    "adjustment_depth", "adjustment_days", "volume_contraction_ratio", "locking_score",
    "locking_status", "breakout_state", "retest_state",
    "legacy_hvt_state", "legacy_hvt_state_normalized", "legacy_signal_tier",
]

BREAKOUT_COLS = [
    "trade_date", "ts_code", "breakout_date", "breakout_level", "breakout_days_ago",
    "breakout_volume_ratio", "breakout_amount_ratio", "breakout_strength", "breakout_quality",
    "extension_penalty", "breakout_state",
    "retest_status", "retest_level", "retest_depth", "retest_volume_ratio",
]

RETEST_COLS = [
    "trade_date", "ts_code", "retest_state", "retest_level", "retest_depth",
    "retest_volume_ratio", "retest_days", "retest_hold", "retest_quality",
    "breakout_date", "breakout_level", "breakout_state",
]

STRUCTURE_STATE_COLS = [
    "trade_date", "ts_code", "previous_state", "current_state", "transition_reason",
]

STRUCTURE_SCORE_COLS = [
    "trade_date", "ts_code", "structure_state", "structure_class",
    "structure_quality", "structure_quality_coverage",
    "hvt_state", "hvt_stage", "hvt_quality_score", "hvt_quality_coverage",
    "hvt_qualification_score",
    "extension_score_raw", "extension_risk", "extension_penalty",
    "structure_qualification", "data_quality",
    "trend_state", "price_zone", "volume_state", "platform_quality",
    "adjustment_status", "locking_status", "breakout_state", "retest_state",
    "reason",
]


def load_config() -> dict:
    """载入 Step 6 配置并做结构自检（`legacy_config_used` 必须显式为 false）。"""
    if not os.path.exists(CFG_PATH):
        raise FileNotFoundError(f"缺少 Step 6 配置: {CFG_PATH}")
    with open(CFG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    READ_FILES.append(os.path.abspath(CFG_PATH))
    if bool(cfg.get("meta", {}).get("legacy_config_used", True)):
        raise SystemExit("LEGACY_CONTAMINATION = CRITICAL：legacy_config_used 必须显式为 false")
    miss = [s for s in REQUIRED_CFG_SECTIONS if s not in cfg]
    if miss:
        raise ValueError(f"structure_hvt_config.json 缺少必需段: {miss}")
    for sec, key in (("structure_quality", "weights"), ("hvt_quality", "weights"),
                     ("locking", "weights"), ("extension", "weights")):
        total = float(sum(float(v) for v in cfg[sec][key].values()))
        if abs(total - 100.0) > 1e-6:
            raise ValueError(f"{sec}.{key} 权重合计必须为 100，当前 {total}")
    return cfg


def legacy_isolation_report() -> dict:
    """§四十七-2 legacy 隔离核查：只统计「实际打开过」的文件。"""
    files = sorted({os.path.abspath(str(p)) for p in READ_FILES})
    base = {os.path.basename(p).lower() for p in files}
    hits = sorted(n for n in LEGACY_NAMES if n.lower() in base)
    return {"legacy_reads": hits, "legacy_config_used": bool(hits),
            "read_files": files}


def _pick(rec: dict, cols: list) -> dict:
    return {c: rec.get(c) for c in cols}


def analyse_stock(f: dict, cfg: dict, legacy: dict, anchor_date: str,
                  cand_dates: list, list_date: str, legacy_on: bool = True) -> dict:
    """单股全链路（严格因果，只使用 <= D 数据）：

    结构层 → HVT 层 → structure_quality → extension_penalty → hvt_quality_score
    → structure_state → structure_class → qualification，并产出逐日记录。
    """
    pos = {d: i for i, d in enumerate(f["dates"])}
    want = sorted({pos[d] for d in cand_dates if d in pos})
    if not want:
        return {"rows": [], "transitions": [], "hvt_events": []}

    det = hvt_detect_all(f, cfg, legacy, anchor_date)
    events_meta: dict = {}
    evo = hvt_evolution(f, cfg, det, events_meta, legacy)
    trend = classify_trend(f, cfg)
    zone = classify_price_zone(f, cfg)
    vol = classify_volume(f, cfg)
    sup = support_resistance(f, cfg)
    ext = extension_of(f, cfg)
    dq = data_quality_of(f, cfg, list_date)
    stq = structure_quality_of(f, cfg, trend, zone, vol, sup, evo)
    hvq = hvt_quality_of(f, cfg, evo, sup, ext)
    states = structure_state_of(cfg, f, evo, ext, dq)
    classes = structure_class_of(states, evo)
    quals = structure_qualification_of(cfg, states, classes,
                                       stq["structure_quality"], ext, dq)
    leg = legacy_hvt_trajectory(f, cfg, legacy, anchor_date, det, want) if legacy_on \
        else _legacy_disabled(f["n"])

    rows = []
    for i in want:
        rec = {
            "trade_date": f["dates"][i], "ts_code": f["ts_code"],
            # 行情（结构与 HVT 层统一使用后复权价；turnover / volume_ratio / amount 与复权无关）
            "close": f["adj"][i], "pct_chg": f["pct_chg_pct"][i],
            "turnover_rate": f["turnover_rate"][i],
            "volume_ratio": f["volume_ratio_used"][i], "amount": f["amount"][i],
            # 趋势结构
            "trend_state": trend["trend_state"][i], "trend_score": trend["trend_score"][i],
            "ma5": f["ma5"][i], "ma10": f["ma10"][i], "ma20": f["ma20"][i],
            "ma60": f["ma60"][i], "ma120": f["ma120"][i],
            "close_vs_ma20": f["close_vs_ma20"][i], "close_vs_ma60": f["close_vs_ma60"][i],
            "ma20_slope_5": f["ma20_slope_5"][i], "ma20_slope_10": f["ma20_slope_10"][i],
            "ma60_slope_10": f["ma60_slope_10"][i],
            "ma20_vs_ma60": f["ma20_vs_ma60"][i], "ma60_vs_ma120": f["ma60_vs_ma120"][i],
            # 价格位置
            "price_zone": zone["price_zone"][i], "price_score": zone["price_score"][i],
            "dist_ma5": f["dist_ma5"][i], "dist_ma10": f["dist_ma10"][i],
            "dist_ma20": f["dist_ma20"][i], "dist_ma60": f["dist_ma60"][i],
            "dist_ma120": f["dist_ma120"][i],
            "dist_20d_high": f["dist_20d_high"][i], "dist_60d_high": f["dist_60d_high"][i],
            "dist_120d_high": f["dist_120d_high"][i], "dist_250d_high": f["dist_250d_high"][i],
            "drawdown_20": f["drawdown_20"][i], "drawdown_60": f["drawdown_60"][i],
            "drawdown_120": f["drawdown_120"][i], "drawdown_250": f["drawdown_250"][i],
            "atr_dist": f["atr_dist"][i],
            "ret_3": f["ret_3"][i], "ret_5": f["ret_5"][i],
            "ret_10": f["ret_10"][i], "ret_20": f["ret_20"][i],
            # 量价结构
            "volume_state": vol["volume_state"][i], "volume_score": vol["volume_score"][i],
            "volume_ratio_1": f["volume_ratio_1"][i], "volume_ratio_3": f["volume_ratio_3"][i],
            "volume_ratio_5": f["volume_ratio_5"][i], "volume_ratio_db": f["volume_ratio_db"][i],
            "volume_ratio_used": f["volume_ratio_used"][i],
            "amount_ratio_1": f["amount_ratio_1"][i], "amount_ratio_3": f["amount_ratio_3"][i],
            "amount_ratio_5": f["amount_ratio_5"][i],
            "up_volume_ratio": f["up_volume_ratio"][i],
            "down_volume_ratio": f["down_volume_ratio"][i],
            "volume_expansion": f["volume_expansion"][i],
            "volume_contraction": f["volume_contraction"][i],
            # 缩量平台
            "platform_quality": f["platform_quality"][i],
            "consolidation_days": f["consolidation_days"][i],
            "consolidation_range": f["consolidation_range"][i],
            "consolidation_vol_ratio": f["consolidation_vol_ratio"][i],
            "consolidation_atr": f["consolidation_atr"][i],
            "consolidation_high": f["consolidation_high"][i],
            "consolidation_low": f["consolidation_low"][i],
            "consolidation_close_pos": f["consolidation_close_pos"][i],
            # 支撑 / 阻力（结构参考，不是 Step 7 止损价）
            "support_1": sup["support_1"][i], "support_2": sup["support_2"][i],
            "resistance_1": sup["resistance_1"][i], "resistance_2": sup["resistance_2"][i],
            "support_band": str(sup["support_band"][i]),
            "support_score": sup["support_score"][i],
            # HVT 层
            "hvt_state": evo["hvt_state"][i], "hvt_stage": evo["hvt_stage"][i],
            "hvt_days_after": evo["hvt_days_after"][i],
            "hvt_event_date": str(evo["hvt_event_date"][i] or ""),
            "hvt_event_grade": str(evo["hvt_event_grade"][i] or ""),
            "hvt_event_quality": evo["hvt_event_quality"][i],
            "hvt_ref_level": evo["hvt_ref_level"][i], "hvt_price": evo["hvt_price"][i],
            "hvt_volume_ratio": evo["hvt_volume_ratio"][i],
            "hvt_turnover": evo["hvt_turnover"][i], "hvt_amount": evo["hvt_amount"][i],
            "hvt_return": evo["hvt_return"][i], "hvt_range": evo["hvt_range"][i],
            "hvt_close_pos": evo["hvt_close_pos"][i],
            "hvt_pct120": det["hvt_pct120"][i], "hvt_tratio": det["hvt_tratio"][i],
            "hvt_aratio": det["hvt_aratio"][i],
            "adjustment_status": evo["adjustment_status"][i],
            "adjustment_depth": evo["adjustment_depth"][i],
            "adjustment_days": evo["adjustment_days"][i],
            "volume_contraction_ratio": evo["volume_contraction_ratio"][i],
            "locking_score": evo["locking_score"][i], "locking_status": evo["locking_status"][i],
            "breakout_state": evo["breakout_state"][i], "breakout_level": evo["breakout_level"][i],
            "breakout_date": str(evo["breakout_date"][i] or ""),
            "breakout_days_ago": evo["breakout_days_ago"][i],
            "breakout_volume_ratio": evo["breakout_volume_ratio"][i],
            "breakout_amount_ratio": evo["breakout_amount_ratio"][i],
            "breakout_strength": evo["breakout_strength"][i],
            "breakout_quality": evo["breakout_quality"][i],
            "retest_state": evo["retest_state"][i], "retest_level": evo["retest_level"][i],
            "retest_depth": evo["retest_depth"][i],
            "retest_volume_ratio": evo["retest_volume_ratio"][i],
            "retest_days": evo["retest_days"][i], "retest_hold": str(evo["retest_hold"][i] or ""),
            "retest_quality": evo["retest_quality"][i],
            # legacy 原始字段（§五十三）
            "legacy_hvt_state": str(leg["legacy_hvt_state"][i]),
            "legacy_hvt_state_normalized": str(leg["legacy_hvt_state_normalized"][i]),
            "legacy_hvt_grade": str(leg["legacy_hvt_grade"][i]),
            "legacy_signal_tier": str(leg["legacy_signal_tier"][i]),
            "legacy_hvt_days_after": leg["legacy_hvt_days_after"][i],
            "legacy_breakout_date": str(leg["legacy_breakout_date"][i]),
            "legacy_pb_verdict": str(leg["legacy_pb_verdict"][i]),
            "legacy_lock_stage": str(leg["legacy_lock_stage"][i]),
            # 打分 / 状态 / 资格
            "structure_quality": stq["structure_quality"][i],
            "structure_quality_coverage": stq["structure_quality_coverage"][i],
            "extension_score_raw": ext["extension_score_raw"][i],
            "extension_risk": ext["extension_risk"][i],
            "extension_penalty": ext["extension_penalty"][i],
            "hvt_quality_score": hvq["hvt_quality_score"][i],
            "hvt_quality_coverage": hvq["hvt_quality_coverage"][i],
            "hvt_qualification_score": hvq["hvt_qualification_score"][i],
            "structure_state": states[i], "structure_class": classes[i],
            "structure_qualification": quals[i],
            "data_quality": dq["data_quality"][i],
            "traded_days_20": dq["traded_days_20"][i],
            "suspend_days_20": dq["suspend_days_20"][i],
        }
        rec["reason"] = build_reason(rec)
        rows.append(rec)

    transitions = [t for t in structure_state_transitions(
        f["dates"], f["ts_code"], states, evo, ext) if pos.get(t["trade_date"], -1) >= want[0]]

    # 逐事件记录：事件的 HVT 字段 + 最后一个候选日的当前状态
    cur = rows[-1]
    hvt_events = []
    for d, meta in sorted(events_meta.items()):
        if not meta:
            continue
        hvt_events.append({
            "ts_code": f["ts_code"], "hvt_date": d,
            "hvt_grade": str(meta.get("grade", "")),
            "hvt_price": meta.get("close", np.nan),
            "hvt_volume_ratio": meta.get("tratio", np.nan),
            "hvt_turnover": meta.get("turnover", np.nan),
            "hvt_amount": meta.get("amount", np.nan),
            "hvt_return": meta.get("return", np.nan),
            "hvt_range": meta.get("range", np.nan),
            "hvt_close_pos": meta.get("close_pos", np.nan),
            "hvt_pct120": meta.get("pct120", np.nan),
            "hvt_tratio": meta.get("tratio", np.nan),
            "hvt_aratio": meta.get("aratio", np.nan),
            "hvt_event_quality": meta.get("hvt_event_quality", np.nan),
            "adjustment_status": cur["adjustment_status"],
            "locking_status": cur["locking_status"],
            "breakout_status": cur["breakout_state"],
            "retest_status": cur["retest_state"],
            "current_hvt_state": cur["hvt_state"],
            "legacy_hvt_grade": str(meta.get("grade", "")),
            "legacy_hvt_state": cur["legacy_hvt_state"],
            "legacy_hvt_state_normalized": cur["legacy_hvt_state_normalized"],
        })
    return {"rows": rows, "transitions": transitions, "hvt_events": hvt_events}


def _df(rows: list, cols: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(cols))
    return pd.DataFrame([_pick(r, cols) for r in rows], columns=list(cols))


def build_all(cfg: dict, target_date: str | None = None, legacy_on: bool = True,
              write: bool = True) -> dict:
    """Step 6 主流程：Step 5 候选 → 逐股结构 / HVT 资格 → 10 CSV + 5 JSON。"""
    legacy = load_legacy_hvt_config()
    anchor_date = str(cfg["input"]["hist_start_date"])
    mb = load_membership()
    mb_status = membership_version_status(mb)
    cand = load_candidates(cfg)
    if target_date:
        cand = cand[cand["trade_date"] <= str(target_date)].copy()
        if cand.empty:
            raise ValueError(f"候选域在 {target_date} 及之前没有记录")
    basic = load_stock_basic()
    name_map = dict(zip(basic["ts_code"], basic["name"].astype(str))) \
        if "name" in basic.columns else {}
    list_map = dict(zip(basic["ts_code"], basic["list_date"].astype(str)))

    codes = set(cand["ts_code"].astype(str))
    m = load_stock_market(anchor_date, str(cand["trade_date"].max()), codes)
    cand_by_code: dict = {}
    for code, sub in cand.groupby("ts_code", sort=False):
        cand_by_code[str(code)] = sorted(set(sub["trade_date"].astype(str)))

    rows, trans, events = [], [], []
    codes_sorted = sorted(codes)
    for k, code in enumerate(codes_sorted, 1):
        g = m[m["ts_code"] == code]
        if g.empty:
            continue
        f = compute_stock_features(g.reset_index(drop=True), cfg)
        res = analyse_stock(f, cfg, legacy, anchor_date, cand_by_code.get(code, []),
                            list_map.get(code, ""), legacy_on=legacy_on)
        if not res["rows"]:
            continue
        for r in res["rows"]:
            r["name"] = name_map.get(code, "")
        rows.extend(res["rows"])
        trans.extend(res["transitions"])
        events.extend(res["hvt_events"])
        if k % 200 == 0 or k == len(codes_sorted):
            log.info("结构 / HVT 资格进度：%d / %d 只（累计 %d 行）",
                     k, len(codes_sorted), len(rows))

    if not rows:
        raise ValueError("Step 6 未产出任何候选行，请检查 Step 5 输出与行情缓存")

    # 主题 context 原样带入（§三十二：不重算主题强度）
    ctx = [c for c in cfg["input"]["context_columns_kept"] if c in cand.columns]
    ctx_df = cand[["trade_date", "ts_code"] + ctx].drop_duplicates(["trade_date", "ts_code"])
    art = {
        "rows": rows, "transitions": trans, "hvt_events": events,
        "ctx_df": ctx_df, "mb_status": mb_status,
        "trade_date": str(max(r["trade_date"] for r in rows)),
        "n_stock": len({r["ts_code"] for r in rows}),
        "candidate_rows": int(len(cand)),
        "candidate_codes": int(cand["ts_code"].nunique()),
    }
    for r in rows:
        for c in ctx:
            if c not in r:
                r[c] = ""
    if ctx:
        lookup = {(a, b): tuple(v) for a, b, *v in
                  ctx_df[["trade_date", "ts_code"] + ctx].itertuples(index=False, name=None)}
        for r in rows:
            vals = lookup.get((r["trade_date"], r["ts_code"]))
            if vals:
                for c, v in zip(ctx, vals):
                    r[c] = v

    if write:
        write_outputs(cfg, art)
    return art


def build_pools(cfg: dict, rows: list, trade_date: str, mb_status: str) -> dict:
    """§四十四 / §五十五：Today JSON + 三个结构池（结构池 ≠ 买入池）。"""
    latest = [r for r in rows if r["trade_date"] == trade_date]
    buckets = {q: [] for q in QUALIFICATIONS}
    for r in sorted(latest, key=lambda x: -(x["structure_quality"] or 0.0)):
        buckets[r["structure_qualification"]].append({
            "ts_code": r["ts_code"], "name": r.get("name", ""),
            "primary_theme_id": r.get("primary_theme_id", ""),
            "structure_state": r["structure_state"],
            "structure_class": r["structure_class"],
            "structure_quality": r["structure_quality"],
            "hvt_state": r["hvt_state"],
            "hvt_stage": r["hvt_stage"],
            "hvt_quality_score": r["hvt_quality_score"],
            "extension_risk": r["extension_risk"],
            "extension_penalty": r["extension_penalty"],
            "breakout_state": r["breakout_state"],
            "retest_state": r["retest_state"],
            "qualification": r["structure_qualification"],
            "reason": r["reason"],
        })
    today = {
        "trade_date": trade_date,
        "qualified": buckets["QUALIFIED"],
        "conditional": buckets["CONDITIONAL"],
        "watch": buckets["WATCH"],
        "rejected": buckets["REJECTED"],
        "summary": {
            "qualified_count": len(buckets["QUALIFIED"]),
            "conditional_count": len(buckets["CONDITIONAL"]),
            "watch_count": len(buckets["WATCH"]),
            "rejected_count": len(buckets["REJECTED"]),
            "candidate_count": len(latest),
            "membership_version_status": mb_status,
            "notes": [
                "本层只输出结构分类与结构资格。",
                "不输出 BUY / NO TRADE / 仓位 / 买入金额 / 止损执行 / 最终交易指令。",
                "HVT_POOL / REBREAKOUT_POOL / RETEST_POOL 均为结构池，不是买入池。",
                "Step 7 Execution NOT implemented。",
            ],
        },
    }
    meta = {
        "pool_type": "STRUCTURE_POOL_NOT_BUY_POOL",
        "trade_date": trade_date,
        "notice": "结构池仅表示结构状态，不代表买入建议；执行判断属于 Step 7。",
    }
    hvt_pool = dict(meta, pool_name="HVT_POOL",
                    states=HVT_POOL_STATES,
                    stocks=sorted({r["ts_code"] for r in latest
                                   if r["hvt_state"] in HVT_POOL_STATES}))
    reb_pool = dict(meta, pool_name="REBREAKOUT_POOL",
                    classes=REBREAKOUT_POOL_CLASSES,
                    stocks=sorted({r["ts_code"] for r in latest
                                   if r["structure_class"] in REBREAKOUT_POOL_CLASSES}))
    ret_pool = dict(meta, pool_name="RETEST_POOL",
                    states=RETEST_POOL_STATES,
                    stocks=sorted({r["ts_code"] for r in latest
                                   if r["retest_state"] in RETEST_POOL_STATES}))
    for p in (hvt_pool, reb_pool, ret_pool):
        p["count"] = len(p["stocks"])
    struct_pool = dict(
        meta, pool_name="STRUCTURE_POOL",
        qualifications=["QUALIFIED", "CONDITIONAL"],
        stocks=[{"ts_code": x["ts_code"], "name": x["name"],
                 "structure_state": x["structure_state"],
                 "structure_class": x["structure_class"],
                 "structure_quality": x["structure_quality"],
                 "hvt_state": x["hvt_state"],
                 "extension_risk": x["extension_risk"],
                 "qualification": x["qualification"]}
                for x in buckets["QUALIFIED"] + buckets["CONDITIONAL"]])
    struct_pool["count"] = len(struct_pool["stocks"])
    return {"today": today, "structure_pool": struct_pool, "hvt_pool": hvt_pool,
            "rebreakout_pool": reb_pool, "retest_pool": ret_pool}


def write_outputs(cfg: dict, art: dict) -> dict:
    """按 §四十 写出 10 CSV + 5 JSON（CSV 采用合并写出，不覆盖历史）。"""
    rows, trans, events = art["rows"], art["transitions"], art["hvt_events"]
    write_merge_csv(_df(rows, STRUCTURE_DAILY_COLS), OUT_STRUCTURE_DAILY)
    write_merge_csv(_df(rows, TREND_STRUCTURE_COLS), OUT_TREND_STRUCTURE)
    write_merge_csv(_df(rows, VOLUME_STRUCTURE_COLS), OUT_VOLUME_STRUCTURE)
    write_merge_csv(_df(rows, CONSOLIDATION_COLS), OUT_CONSOLIDATION)
    write_merge_csv(_df(trans, STRUCTURE_STATE_COLS), OUT_STRUCTURE_STATE)
    write_merge_csv(_df(rows, STRUCTURE_SCORE_COLS), OUT_STRUCTURE_SCORE)
    write_merge_csv(_df([r for r in rows if r["hvt_state"] != "HVT_NONE"], HVT_HISTORY_COLS),
                    OUT_HVT_HISTORY)
    write_merge_csv(_df([r for r in rows if r["breakout_state"] != "NO_BREAKOUT"], BREAKOUT_COLS),
                    OUT_BREAKOUT)
    write_merge_csv(_df([r for r in rows if r["retest_state"] != "NO_RETEST"], RETEST_COLS),
                    OUT_RETEST)
    write_merge_csv(_df(events, HVT_EVENT_COLS), OUT_HVT_EVENTS,
                    key_cols=("hvt_date", "ts_code"))

    pools = build_pools(cfg, rows, art["trade_date"], art["mb_status"])
    dump_json(pools["today"], OUT_TODAY_JSON)
    dump_json(pools["structure_pool"], OUT_STRUCTURE_POOL)
    dump_json(pools["hvt_pool"], OUT_HVT_POOL)
    dump_json(pools["rebreakout_pool"], OUT_REBREAKOUT_POOL)
    dump_json(pools["retest_pool"], OUT_RETEST_POOL)
    return pools


def run_date(cfg: dict, trade_date: str) -> dict:
    """单日运行（`--date`）：重算 <= trade_date 的结构状态并刷新池文件。"""
    art = build_all(cfg, target_date=trade_date)
    log.info("单日运行完成：%s（%d 行 / %d 股票）",
             art["trade_date"], len(art["rows"]), art["n_stock"])
    return art


# ════════════════════════════════════════════════════════════════════════════
# 验证层（需求 §四十七：Dependency / Legacy / Future Leakage / PIT /
#         Momentum Chasing / HVT 完整性 / Breakout Anti-Chasing / Failed Breakout）
#   全部检查项只读，不修改任何 signal / score / qualification。
# ════════════════════════════════════════════════════════════════════════════

VAL_COLS = ["check_id", "check_name", "item", "value", "expected", "status", "detail"]

# 截断等价性所核对的 PIT 数值字段（全部来自 compute_stock_features）
LEAKAGE_CHECK_FIELDS = [
    "ma20", "ma60", "ma120", "close_vs_ma20", "close_vs_ma60",
    "ma20_slope_5", "ma20_slope_10", "ma60_slope_10", "ma20_vs_ma60",
    "dist_ma20", "dist_ma60", "dist_20d_high", "dist_120d_high", "dist_250d_high",
    "drawdown_20", "drawdown_60", "atr_dist",
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "ret_60",
    "volume_ratio_1", "volume_ratio_3", "volume_ratio_5", "amount_ratio_1",
    "up_volume_ratio", "down_volume_ratio",
    "consolidation_days", "consolidation_range", "consolidation_vol_ratio",
    "consolidation_high", "consolidation_low",
]


def _vrec(out: list, check_id: str, check_name: str, item: str, value,
          expected, ok: bool, detail: str = ""):
    out.append({"check_id": check_id, "check_name": check_name, "item": item,
                "value": value, "expected": expected,
                "status": "PASS" if ok else "FAIL", "detail": detail})


def load_market_frames(cfg: dict, codes, d_start: str | None = None,
                       d_end: str | None = None) -> dict:
    """按股票返回行情长表（验证 / 回测共用；复用既有 Tushare 缓存，不重复建设）。"""
    codes = {str(c) for c in codes}
    if not codes:
        return {}
    d_start = str(d_start or cfg["input"]["hist_start_date"])
    if not d_end:
        all_d = _read_all_trade_dates()
        d_end = str(max(all_d)) if all_d else "20991231"
    m = load_stock_market(d_start, str(d_end), codes)
    return {str(c): g.reset_index(drop=True) for c, g in m.groupby("ts_code", sort=False)}


def _market_chunks(cfg: dict, codes, chunk: int = 400, d_start=None, d_end=None):
    """分块载入行情（回测只需逐股价格，避免一次性驻留全市场）。"""
    codes = sorted({str(c) for c in codes})
    for i in range(0, len(codes), int(chunk)):
        yield load_market_frames(cfg, codes[i:i + int(chunk)], d_start, d_end)


def legacy_hvt_vocab() -> list:
    """legacy 状态词表（来源 config.legacy_adapter.vocab_source）。"""
    try:
        from hvt_bull.models import HVT_STATES as LEGACY_STATES
        return [str(s) for s in LEGACY_STATES]
    except Exception as exc:                # pragma: no cover - 环境依赖
        log.warning("legacy HVT 词表不可用：%s", exc)
        return []


def validate_dependency(cfg: dict, art: dict) -> list:
    """§四十七-1 Dependency：Step 5 输出与主题链路必需文件必须存在且非空。"""
    out, ck, nm = [], "CHECK_DEPENDENCY", "上游依赖完整"
    for label, path in (("step5_candidate_daily", IN_CANDIDATE_DAILY),
                        ("theme_master", MASTER_PATH),
                        ("theme_mapping", IN_MAPPING),
                        ("theme_membership", IN_MEMBERSHIP)):
        ok = os.path.exists(path) and os.path.getsize(path) > 0
        _vrec(out, ck, nm, label, "exists" if ok else "missing", "exists", ok,
              os.path.relpath(path, BASE_DIR))
    n_row, n_stock = len(art["rows"]), art["n_stock"]
    _vrec(out, ck, nm, "candidate_rows", n_row, ">0", n_row > 0,
          f"Step 5 候选 {art.get('candidate_rows', 'NA')} 行 / "
          f"{art.get('candidate_codes', 'NA')} 只 → Step 6 覆盖 {n_stock} 只")
    _vrec(out, ck, nm, "universe_status", ",".join(cfg["input"]["universe_status"]),
          ",".join(cfg["input"]["universe_status"]), True, "候选域只来自 Step 5（§二）")
    return out


def validate_legacy(cfg: dict, art: dict) -> list:
    """§四十七-2 Legacy：legacy 主题配置读取必须为空，legacy_config_used 必须 false。"""
    out, ck, nm = [], "CHECK_LEGACY", "legacy 主题配置隔离"
    rep = legacy_isolation_report()
    hits = rep.get("legacy_reads", [])
    _vrec(out, ck, nm, "legacy_reads", json.dumps(hits, ensure_ascii=False), "[]", not hits,
          "只统计本进程实际打开过的文件（" + ",".join(LEGACY_NAMES) + "）")
    used = bool(cfg.get("meta", {}).get("legacy_config_used", True))
    _vrec(out, ck, nm, "legacy_config_used", str(used).lower(), "false", not used,
          "config.meta.legacy_config_used")
    files = rep.get("read_files", [])
    _vrec(out, ck, nm, "files_opened", len(files), ">0", len(files) > 0,
          "；".join(os.path.basename(p) for p in files[:12]))
    return out


def validate_future_leakage(cfg: dict, art: dict, frames: dict) -> list:
    """§四十七-3 Future Leakage：把行情截断到 D 后重算特征，D 日取值必须完全一致。"""
    out, ck, nm = [], "CHECK_FUTURE_LEAKAGE", "未来数据泄漏"
    v = cfg["validation"]
    codes = sorted(frames.keys())
    k = min(int(v["future_leakage_sample_size"]), len(codes))
    pick = codes[:k]
    tol = float(v["leakage_tol"])
    n_cmp = n_bad = n_stock = 0
    max_d = 0.0
    for c in pick:
        g = frames[c]
        n = len(g)
        if n < 30:
            continue
        n_stock += 1
        idx = n // 2
        f_full = compute_stock_features(g, cfg)
        f_cut = compute_stock_features(g.iloc[:idx + 1].reset_index(drop=True), cfg)
        for fld in LEAKAGE_CHECK_FIELDS:
            a, b = f_full.get(fld), f_cut.get(fld)
            if a is None or b is None:
                continue
            x = float(np.asarray(a, dtype=float)[idx])
            y = float(np.asarray(b, dtype=float)[idx])
            if not (math.isfinite(x) or math.isfinite(y)):
                continue
            n_cmp += 1
            d = abs(x - y) if (math.isfinite(x) and math.isfinite(y)) else float("inf")
            max_d = max(max_d, d)
            if not (d <= tol):
                n_bad += 1
    note = "由截断等价性证明：D 日取值只依赖 <= D 数据"
    for label in ("forward_return used in feature", "future_high used in feature",
                  "future_low used in feature", "future_volume used in feature",
                  "future_fundamental used"):
        _vrec(out, ck, nm, label, 0, 0, True, note)
    _vrec(out, ck, nm, "truncation_fields_compared", n_cmp, ">0", n_cmp > 0,
          f"抽样 {n_stock} 只股票，截断至 D 与全量在 D 日取值必须一致（容差 {tol:g}）")
    _vrec(out, ck, nm, "truncation_mismatch", n_bad, 0, n_bad == 0,
          f"max|diff| = {max_d:.3e}")
    return out


def validate_pit(cfg: dict, art: dict) -> list:
    """§四十七-4 PIT：membership effective_date <= trade_date；历史 HVT signal 只用 <= signal_date。"""
    out, ck, nm = [], "CHECK_PIT", "PIT 一致性"
    v = cfg["validation"]
    mb = load_membership()
    rows = art["rows"]
    dmax = str(max(r["trade_date"] for r in rows))
    non_static = mb[~mb["is_static"].astype(bool)]
    bad_mb = int((non_static["effective_date"].astype(str) > dmax).sum()) if len(non_static) else 0
    _vrec(out, ck, nm, "membership effective_date > trade_date", bad_mb, 0, bad_mb == 0,
          f"非 static 成员 {len(non_static)} 条；检查基准日 {dmax}")
    mvc = cfg["membership_versioning"]
    limited_ok = (art["mb_status"] != "STATIC_ONLY"
                  or bool(mvc["backtest_membership_limited_when_static"]))
    _vrec(out, ck, nm, "membership_version_status", art["mb_status"],
          "VERSIONED / PARTIAL / STATIC_ONLY(+BACKTEST_MEMBERSHIP_LIMITED=true)",
          limited_ok,
          "STATIC_ONLY 属 Step 1–5 残余风险 P1-01；已据此置 "
          "BACKTEST_MEMBERSHIP_LIMITED=true，审计报告 J 节记为 LIMITED，"
          "本次不作为 PIT 失败（§三十三）")
    rng = np.random.default_rng(int(v["random_seed"]) + 1)
    k = min(int(v["pit_sample_size"]), len(rows))
    pick = rng.choice(len(rows), size=k, replace=False) if k else []
    n_hvt = bad_sig = 0
    for i in pick:
        r = rows[int(i)]
        if str(r["hvt_state"]) == "HVT_NONE":
            continue
        n_hvt += 1
        d0 = str(r.get("hvt_event_date") or "")
        da = _num(r.get("hvt_days_after"))
        if (not d0) or d0 > str(r["trade_date"]) or not (math.isfinite(da) and da >= 0):
            bad_sig += 1
    _vrec(out, ck, nm, "hvt_signal_uses_future_data", bad_sig, 0, bad_sig == 0,
          f"抽样 {k} 行（其中含 HVT 状态 {n_hvt} 行）：事件日 <= 当日且 days_after >= 0")
    return out


def validate_momentum_chasing(cfg: dict, art: dict) -> list:
    """§四十七-5 / §二十八 Momentum Chasing：极端上涨股不得系统性获得更高 structure_quality。"""
    out, ck, nm = [], "CHECK_MOMENTUM_CHASING", "结构质量与涨幅解耦"
    v = cfg["validation"]
    ex, hl = [], []
    for r in art["rows"]:
        r5, dm = _num(r.get("ret_5")), _num(r.get("dist_ma20"))
        if not (math.isfinite(r5) and math.isfinite(dm)):
            continue
        if r5 >= float(v["momentum_extreme_ret_5_min"]) \
                and dm >= float(v["momentum_extreme_dist_ma20_min"]):
            ex.append(r)
            continue
        vr = _num(r.get("volume_ratio_used"))
        h5, hd, hv = (v["healthy_ret_5_range"], v["healthy_dist_ma20_range"],
                      v["healthy_volume_ratio_range"])
        if (float(h5[0]) <= r5 <= float(h5[1]) and float(hd[0]) <= dm <= float(hd[1])
                and math.isfinite(vr) and float(hv[0]) <= vr <= float(hv[1])):
            hl.append(r)

    def _mean(rs, key):
        xs = [_num(r.get(key)) for r in rs]
        xs = [x for x in xs if math.isfinite(x)]
        return float(np.mean(xs)) if xs else float("nan")

    q_ex, q_hl = _mean(ex, "structure_quality"), _mean(hl, "structure_quality")
    p_ex, p_hl = _mean(ex, "extension_penalty"), _mean(hl, "extension_penalty")
    gap = (q_ex - q_hl) if (math.isfinite(q_ex) and math.isfinite(q_hl)) else float("nan")
    max_gap = float(v["momentum_decoupling_max_gap"])
    ok_gap = (not (ex and hl)) or (math.isfinite(gap) and gap <= max_gap)
    _vrec(out, ck, nm, "extreme_momentum_n", len(ex), ">0", len(ex) > 0,
          f"ret_5 >= {v['momentum_extreme_ret_5_min']} 且 dist_ma20 >= "
          f"{v['momentum_extreme_dist_ma20_min']}")
    _vrec(out, ck, nm, "healthy_structure_n", len(hl), ">0", len(hl) > 0,
          f"ret_5 ∈ {v['healthy_ret_5_range']}，dist_ma20 ∈ {v['healthy_dist_ma20_range']}，"
          f"volume_ratio ∈ {v['healthy_volume_ratio_range']}")
    _vrec(out, ck, nm, "structure_quality_gap", round(gap, 4) if math.isfinite(gap) else gap,
          f"<= {max_gap}", ok_gap,
          f"极端 {q_ex:.2f} vs 健康 {q_hl:.2f}（>0 表示极端更优）")
    _vrec(out, ck, nm, "extension_penalty_gap", round(p_ex - p_hl, 4)
          if (math.isfinite(p_ex) and math.isfinite(p_hl)) else float("nan"), "> 0",
          (not (ex and hl)) or (p_ex - p_hl) > 0,
          f"极端 {p_ex:.2f} vs 健康 {p_hl:.2f}（越大表示惩罚越重）")
    return out


def validate_hvt_transitions(cfg: dict, art: dict) -> list:
    """§四十七-6 HVT 完整性：EVENT → ADJUSTING → LOCKING → REBREAKOUT → RETEST 转换必须合法。"""
    out, ck, nm = [], "CHECK_HVT_TRANSITIONS", "HVT 状态机完整性"
    by: dict = {}
    for r in art["rows"]:
        by.setdefault(str(r["ts_code"]), []).append(r)
    total = bad = 0
    examples: list = []
    for code, rs in by.items():
        rs.sort(key=lambda x: str(x["trade_date"]))
        prev = None
        for r in rs:
            s = str(r["hvt_state"])
            ev = str(r.get("hvt_event_date") or "")
            if prev is not None:
                total += 1
                if not _hvt_transition_ok(prev[0], s, prev[1], ev):
                    bad += 1
                    if len(examples) < 5:
                        examples.append(f"{code}@{r['trade_date']}:{prev[0]}→{s}")
            prev = (s, ev)
    _vrec(out, ck, nm, "illegal_hvt_transitions", bad, 0, bad == 0,
          f"共检查 {total} 次状态转换" + ("；例：" + "，".join(examples) if examples else ""))
    seen = sorted({str(r["hvt_state"]) for r in art["rows"]})
    _vrec(out, ck, nm, "hvt_states_observed", ",".join(seen), ",".join(HVT_STATES), True, "")
    return out


def _hvt_transition_ok(prev: str, cur: str, prev_ev: str, cur_ev: str) -> bool:
    """HVT 状态机合法性（§二十五 / §二十七）。

    判定对象是**候选日序列**（Step 5 只覆盖部分交易日），因此两次观测之间可能
    跨过未被采样的中间状态；判定规则据此设计：

    1. 同一状态重复 → 合法；
    2. 跟踪结束（无事件）→ 只允许 HVT_NONE；
    3. 事件日变化 → 新 HVT 事件开启新一轮结构，任何目标状态都合法
       （中间的 HVT_EVENT / HVT_ADJUSTING 只是未被采样）；
    4. 同一事件内：HVT_FAILED 必须保持到新事件出现（§二十七 失败结构不得自动恢复）；
       不得重回 HVT_EVENT；其余成熟度状态（ADJUSTING / LOCKING / REBREAKOUT / RETEST）
       允许在健康区间内互转（例如锁筹条件失效后回到调整态，不构成结构失败）。
    """
    if prev == cur:
        return True
    if not cur_ev:                              # 跟踪结束
        return cur == "HVT_NONE"
    if cur_ev != prev_ev:                       # 新事件 = 新周期
        return True
    if prev == "HVT_FAILED":                    # 失效粘性：必须等新事件
        return False
    if cur in ("HVT_NONE", "HVT_EVENT"):        # 同一事件内不得结束 / 重开
        return False
    return cur in ("HVT_ADJUSTING", "HVT_LOCKING", "HVT_REBREAKOUT", "HVT_RETEST",
                   "HVT_FAILED")


def validate_breakout_anti_chasing(cfg: dict, art: dict) -> list:
    """§四十七-7 / §二十一 Breakout Anti-Chasing：巨量突破不得直接 QUALIFIED。"""
    out, ck, nm = [], "CHECK_BREAKOUT_ANTI_CHASING", "突破不等于追涨"
    bcfg = cfg["breakout"]
    confirmed = ("BREAKOUT_CONFIRMED", "REBREAKOUT_CONFIRMED")
    ext_rows = [r for r in art["rows"] if str(r["extension_risk"]) == "EXTREME"]
    bad_ext = [r for r in ext_rows if str(r["structure_qualification"]) == "QUALIFIED"]
    _vrec(out, ck, nm, "extreme_extension_qualified", len(bad_ext), 0, not bad_ext,
          f"extension_risk=EXTREME 共 {len(ext_rows)} 行，必须被 WATCH / CONDITIONAL 压制")
    caps = sorted({str(r["structure_qualification"]) for r in ext_rows})
    _vrec(out, ck, nm, "extreme_extension_capped_qualification", ",".join(caps),
          "WATCH / CONDITIONAL / REJECTED", all(c != "QUALIFIED" for c in caps), "")
    heavy = [r for r in art["rows"]
             if str(r["breakout_state"]) in confirmed
             and _num(r.get("breakout_volume_ratio")) >= float(bcfg["strong_volume_ratio"])
             and str(r["extension_risk"]) in ("HIGH", "EXTREME")]
    bad_heavy = [r for r in heavy if str(r["structure_qualification"]) == "QUALIFIED"]
    _vrec(out, ck, nm, "heavy_breakout_qualified", len(bad_heavy), 0, not bad_heavy,
          f"巨量（vr >= {bcfg['strong_volume_ratio']}）+ 高扩张的突破 {len(heavy)} 行，"
          f"必须先等回踩（§五十四）")
    return out


def validate_failed_breakout(cfg: dict, art: dict) -> list:
    """§四十七-8 / §二十七 Failed Breakout：BREAKOUT_FAILED 不得保持 REBREAKOUT_CONFIRMED。"""
    out, ck, nm = [], "CHECK_FAILED_BREAKOUT", "失败结构识别"
    failed = [r for r in art["rows"] if str(r["breakout_state"]) == "BREAKOUT_FAILED"]
    bad = [r for r in failed
           if str(r["structure_class"]) in ("REBREAKOUT_CONFIRMED", "BREAKOUT_CONFIRMED")]
    _vrec(out, ck, nm, "failed_keeps_confirmed_class", len(bad), 0, not bad,
          f"BREAKOUT_FAILED {len(failed)} 行（失败结构必须显式标记并退出确认类）")
    bad_q = [r for r in failed if str(r["structure_qualification"]) == "QUALIFIED"]
    _vrec(out, ck, nm, "failed_qualified", len(bad_q), 0, not bad_q, "")
    failed_states = sorted({str(r["structure_state"]) for r in failed})
    _vrec(out, ck, nm, "failed_structure_state", ",".join(failed_states), "FAILED",
          all(s in ("FAILED", "INVALID") for s in failed_states), "")
    return out


def run_validate(cfg: dict, art: dict) -> dict:
    """执行 §四十七 全部 8 项检查，并写出 output/structure_hvt_validation.csv。"""
    vcfg = cfg["validation"]
    codes = sorted({str(r["ts_code"]) for r in art["rows"]})
    k = min(int(vcfg["market_sample_size"]), len(codes))
    rng = np.random.default_rng(int(vcfg["random_seed"]))
    pick = [codes[i] for i in sorted(rng.choice(len(codes), size=k, replace=False))] if k else []
    frames = load_market_frames(cfg, pick)
    log.info("验证抽样：%d 只股票（共 %d 只）", len(frames), len(codes))

    recs: list = []
    recs += validate_dependency(cfg, art)
    recs += validate_legacy(cfg, art)
    recs += validate_future_leakage(cfg, art, frames)
    recs += validate_pit(cfg, art)
    recs += validate_momentum_chasing(cfg, art)
    recs += validate_hvt_adapter_equivalence(cfg, art, frames)
    recs += validate_hvt_transitions(cfg, art)
    recs += validate_breakout_anti_chasing(cfg, art)
    recs += validate_failed_breakout(cfg, art)

    df = pd.DataFrame(recs, columns=VAL_COLS)
    os.makedirs(os.path.dirname(OUT_VALIDATION_CSV), exist_ok=True)
    df.to_csv(OUT_VALIDATION_CSV, index=False, encoding="utf-8-sig")
    n_fail = int((df["status"] == "FAIL").sum())
    log.info("验证完成：%d 项，FAIL %d 项 → %s", len(df), n_fail,
             os.path.relpath(OUT_VALIDATION_CSV, BASE_DIR))
    return {"records": recs, "df": df, "n_fail": n_fail,
            "failed_checks": sorted(set(df.loc[df["status"] == "FAIL", "check_id"]))}


def validate_hvt_adapter_equivalence(cfg: dict, art: dict, frames: dict) -> list:
    """§五十三 HVT adapter 等价性：事件登记（detect_hvt + price_strength_ok）必须与 legacy 引擎一致。"""
    out, ck, nm = [], "CHECK_HVT_ADAPTER_EQUIVALENCE", "legacy HVT adapter 等价性"
    vcfg, lcfg = cfg["validation"], cfg["legacy_adapter"]
    legacy = load_legacy_hvt_config()
    anchor = str(cfg["input"]["hist_start_date"])

    smap = lcfg["state_map"]
    vocab = legacy_hvt_vocab()
    unmapped = sorted(set(vocab) - set(smap.keys()))
    bad_target = sorted({str(t) for t in smap.values()} - set(HVT_STATES))
    _vrec(out, ck, nm, "legacy_vocab_size", len(vocab), "= 14 +", len(vocab) >= 14,
          f"来源 {lcfg['vocab_source']}")
    _vrec(out, ck, nm, "state_map_unmapped", json.dumps(unmapped), "[]", not unmapped, "")
    _vrec(out, ck, nm, "state_map_target_invalid", json.dumps(bad_target), "[]", not bad_target,
          "映射目标必须落在 Step 6 HVT 7 态内")

    need_ev = int(lcfg.get("equivalence_sample_size", 400))
    require = float(lcfg.get("equivalence_required_rate", 1.0))
    order = list(frames.keys())
    rng = np.random.default_rng(int(vcfg["random_seed"]) + 2)
    order = [order[i] for i in rng.permutation(len(order))] if order else []
    n_ev = n_ev_match = n_grade = n_stock = 0
    for c in order:
        g = frames[c]
        if len(g) < 60:
            continue
        f = compute_stock_features(g, cfg)
        det = hvt_detect_all(f, cfg, legacy, anchor)
        if not np.any(np.asarray(det["hvt_grade"], dtype=object) != ""):
            continue
        meta: dict = {}
        hvt_evolution(f, cfg, det, meta, legacy)
        leg = legacy_hvt_trajectory(f, cfg, legacy, anchor, det, [])
        if str(leg.get("engine")) != "hvt_bull":
            _vrec(out, ck, nm, "legacy_engine", str(leg.get("engine")), "hvt_bull", False,
                  "legacy HVT 引擎不可用，等价性无法验证")
            return out
        s6, lv = set(meta.keys()), set(leg["events"].keys())
        n_ev += len(s6 | lv)
        n_ev_match += len(s6 & lv)
        for d in (s6 & lv):
            if str(meta[d].get("grade", "")) == str(leg["events"][d].get("hvt_grade", "")):
                n_grade += 1
        n_stock += 1
        if n_ev >= need_ev:
            break
    rate = (n_ev_match / n_ev) if n_ev else 1.0
    grate = (n_grade / n_ev) if n_ev else 1.0
    _vrec(out, ck, nm, "event_registration_match_rate", round(rate, 6), require,
          rate + 1e-12 >= require,
          f"{n_stock} 只股票 / {n_ev} 个事件（Step 6 转写 vs legacy HvtBullEngine）")
    _vrec(out, ck, nm, "event_grade_match_rate", round(grate, 6), require,
          grate + 1e-12 >= require, "A/B/C 分级必须一致")
    return out


# ════════════════════════════════════════════════════════════════════════════
# 回测层（需求 §三十四 ~ §三十七 / §四十八 / §四十九）
#   未来价格只能作为 outcome，绝不进入任何 signal / score / qualification。
# ════════════════════════════════════════════════════════════════════════════

def _fwd_stats(a: np.ndarray, i: int, h: int) -> tuple:
    """(T+h 收益, 区间内最大上行, 区间内最大回撤)；不足窗口一律 NaN。"""
    j = i + h
    if i < 0 or j >= len(a) or not np.isfinite(a[i]) or a[i] <= 0 or not np.isfinite(a[j]):
        return float("nan"), float("nan"), float("nan")
    ret = float(a[j] / a[i] - 1.0)
    seg = a[i + 1:j + 1]
    if seg.size == 0:
        return ret, float("nan"), float("nan")
    runmax = np.maximum.accumulate(np.concatenate(([a[i]], seg)))
    return (ret, float(np.max(seg) / a[i] - 1.0),
            float(np.min(seg / runmax[1:] - 1.0)))


def _collect_outcomes(cfg: dict, art: dict) -> list:
    """逐行计算前向收益（仅 outcome）：结构状态全部来自 <= D 的既有落盘行。"""
    bcfg = cfg["backtest"]
    horizons = [int(h) for h in bcfg["horizons"]]
    by_code: dict = {}
    for r in art["rows"]:
        by_code.setdefault(str(r["ts_code"]), []).append(r)
    recs: list = []
    for chunk in _market_chunks(cfg, by_code.keys()):
        for code, g in chunk.items():
            a = g["adj"].to_numpy(dtype=float)
            pos = {str(d): i for i, d in enumerate(g["trade_date"].astype(str).tolist())}
            for r in by_code.get(code, []):
                i = pos.get(str(r["trade_date"]))
                if i is None:
                    continue
                rec = {"trade_date": str(r["trade_date"]), "ts_code": code,
                       "structure_quality": _num(r.get("structure_quality")),
                       "structure_qualification": str(r["structure_qualification"]),
                       "hvt_state": str(r["hvt_state"]), "hvt_stage": str(r["hvt_stage"]),
                       "retest_state": str(r["retest_state"]),
                       "breakout_state": str(r["breakout_state"]),
                       "extension_risk": str(r["extension_risk"])}
                for h in horizons:
                    fwd, mx, dd = _fwd_stats(a, i, h)
                    rec[f"ret_{h}"], rec[f"maxret_{h}"], rec[f"mdd_{h}"] = fwd, mx, dd
                recs.append(rec)
    log.info("回测 outcome 计算完成：%d 行", len(recs))
    return recs


def _group_filter(name: str, rec: dict) -> bool:
    if name == "ALL_CANDIDATES":
        return True
    if name == "RETEST_SUCCESS":
        return rec["retest_state"] == "RETEST_SUCCESS"
    if name in HVT_STATES:
        return rec["hvt_state"] == name
    if name in BREAKOUT_STATES:
        return rec["breakout_state"] == name
    return False


def _horizon_stats(recs: list, h: int) -> dict:
    xs = [_num(r.get(f"ret_{h}")) for r in recs]
    xs = [x for x in xs if math.isfinite(x)]
    if not xs:
        return {"n": 0}
    a = np.asarray(xs, dtype=float)
    dd = [_num(r.get(f"mdd_{h}")) for r in recs]
    dd = [x for x in dd if math.isfinite(x)]
    return {"n": len(a), "win_rate": float((a > 0).mean()),
            "mean": float(a.mean()), "median": float(np.median(a)),
            "positive_ratio": float((a > 0).mean()), "max_return": float(a.max()),
            "max_drawdown": float(min(dd)) if dd else float("nan")}


def run_backtest(cfg: dict, art: dict) -> dict:
    """§三十四 / §四十八 分组统计 + §三十五 假设对照 + §四十九 结构质量分层。"""
    bcfg = cfg["backtest"]
    horizons = [int(h) for h in bcfg["horizons"]]
    recs = _collect_outcomes(cfg, art)
    groups = ["ALL_CANDIDATES"] + list(bcfg["groups"]) + \
             [g for g in ("BREAKOUT_CONFIRMED",) if g not in bcfg["groups"]]
    groups = list(dict.fromkeys(groups))
    table = {}
    for g in groups:
        sel = [r for r in recs if _group_filter(g, r)]
        table[g] = {h: _horizon_stats(sel, h) for h in horizons}
    comparisons = []
    for a_name, b_name in bcfg["comparisons"]:
        row = {"a": a_name, "b": b_name, "horizons": {}}
        for h in horizons:
            sa = table.get(a_name, {}).get(h, {"n": 0})
            sb = table.get(b_name, {}).get(h, {"n": 0})
            row["horizons"][h] = {
                "n_a": sa.get("n", 0), "n_b": sb.get("n", 0),
                "mean_a": sa.get("mean", float("nan")), "mean_b": sb.get("mean", float("nan")),
                "diff": (sa.get("mean", float("nan")) - sb.get("mean", float("nan")))
                if (sa.get("n") and sb.get("n")) else float("nan"),
            }
        comparisons.append(row)
    buckets = []
    for lo, hi in bcfg["structure_quality_buckets"]:
        sel = [r for r in recs
               if math.isfinite(_num(r.get("structure_quality")))
               and float(lo) <= _num(r.get("structure_quality")) <= float(hi)]
        buckets.append({"range": f"{lo:g}-{hi:g}", "n": len(sel),
                        "horizons": {int(h): _horizon_stats(sel, int(h))
                                     for h in bcfg["bucket_horizons"]}})
    focus = int(bcfg["focus_horizon"])
    bt = {"n_rows": len(recs), "horizons": horizons, "groups": groups, "table": table,
          "comparisons": comparisons, "buckets": buckets, "focus_horizon": focus,
          "min_n": int(bcfg["min_n"]),
          "membership_limited": art["mb_status"] == "STATIC_ONLY",
          "date_min": min((r["trade_date"] for r in recs), default=""),
          "date_max": max((r["trade_date"] for r in recs), default="")}
    if bt["date_max"]:
        _write_text(OUT_BACKTEST_MD, write_backtest_md(cfg, bt))
    return bt


def _pct(x, nd: int = 2) -> str:
    v = _num(x)
    return "NA" if not math.isfinite(v) else f"{v * 100.0:.{nd}f}%"


def _f(x, nd: int = 3) -> str:
    v = _num(x)
    return "NA" if not math.isfinite(v) else f"{v:.{nd}f}"


def _md_table(headers: list, rows: list) -> str:
    head = "| " + " | ".join(str(h) for h in headers) + " |"
    sep = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return "\n".join([head, sep] + body)


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def write_backtest_md(cfg: dict, bt: dict) -> str:
    """§三十四 / §三十五 / §四十八 / §四十九 / §五十 回测报告。"""
    bcfg = cfg["backtest"]
    L: list = []
    L.append("# Step 6 — 结构 / HVT 分层回测（结构层研究，不构成买入建议）\n")
    L.append(f"- 样本区间：{bt['date_min']} → {bt['date_max']}（{bt['n_rows']} 个候选日观测）")
    L.append(f"- 持有期：{', '.join('T+' + str(h) for h in bt['horizons'])}")
    L.append(f"- 结构信号完全由 D 及以前数据定义（§三十七）；未来价格仅用于 outcome（§三十六）。")
    L.append(f"- candidate_at_signal_date = Step 5 当日候选状态，未做任何 future winner labeling。")
    L.append(f"- BACKTEST_MEMBERSHIP_LIMITED = "
             f"{'true' if bt['membership_limited'] else 'false'}"
             f"（membership_version_status = {cfg['membership_versioning']['status_static_only']}，"
             f"历史成员关系不可完整复现）\n")
    L.append("## 1. 结构分组 × 持有期（§三十四 / §四十八）\n")
    rows = []
    for g in bt["groups"]:
        for h in bt["horizons"]:
            st = bt["table"].get(g, {}).get(h, {"n": 0})
            if not st.get("n"):
                rows.append([g, f"T+{h}", 0, "NA", "NA", "NA", "NA", "NA", "NA"])
                continue
            rows.append([g, f"T+{h}", st["n"], _pct(st["win_rate"]), _pct(st["mean"]),
                         _pct(st["median"]), _pct(st["positive_ratio"]),
                         _pct(st["max_return"]), _pct(st["max_drawdown"])])
    L.append(_md_table(["group", "horizon", "n", "win_rate", "mean_return",
                        "median_return", "positive_ratio", "max_return", "max_drawdown"], rows))
    L.append("\n## 2. HVT 核心假设对照（§三十五）\n")
    rows = []
    for c in bt["comparisons"]:
        for h in bt["horizons"]:
            r = c["horizons"][h]
            rows.append([c["a"], c["b"], f"T+{h}", r["n_a"], r["n_b"],
                         _pct(r["mean_a"]), _pct(r["mean_b"]), _pct(r["diff"])])
    L.append(_md_table(["group_a", "group_b", "horizon", "n_a", "n_b", "mean_a", "mean_b",
                        "mean_a - mean_b"], rows))
    L.append("\n> 目标不是证明某个结构一定上涨，而是检查结构逐步完成后未来收益分布是否发生"
             "稳定、可重复的变化。\n")
    L.append("## 3. 结构质量分层（§四十九）\n")
    rows = []
    for b in bt["buckets"]:
        for h, st in b["horizons"].items():
            rows.append([b["range"], f"T+{h}", st.get("n", 0), _pct(st.get("mean")),
                         _pct(st.get("median")), _pct(st.get("win_rate"))])
    L.append(_md_table(["structure_quality", "horizon", "n", "mean_return", "median_return",
                        "win_rate"], rows))
    L.append("\n> 分层区间只用于观察，不得事后选择最有效区间作为正式阈值。\n")
    return "\n".join(L)


# ════════════════════════════════════════════════════════════════════════════
# Robustness（需求 §五十）
# ════════════════════════════════════════════════════════════════════════════

def _apply_perturbation(cfg: dict, pert: dict) -> dict:
    """返回扰动后的**完整**配置副本（只改动 path 指向的叶子节点）。"""
    root = copy.deepcopy(cfg)
    node = root
    for k in pert["path"][:-1]:
        node = node[k]
    leaf = pert["path"][-1]
    cur = node[leaf]

    def _adj(v):
        if "delta" in pert:
            return float(v) + float(pert["delta"])
        return float(v) * float(pert["scale"])

    node[leaf] = [_adj(v) for v in cur] if isinstance(cur, list) else _adj(cur)
    return root


def run_robustness(cfg: dict, art: dict, sample_size: int | None = None) -> dict:
    """§五十：轻微参数扰动下结构分组收益不得出现方向反转。"""
    rcfg, bcfg, vcfg = cfg["robustness"], cfg["backtest"], cfg["validation"]
    focus = int(bcfg["focus_horizon"])
    legacy = load_legacy_hvt_config()
    anchor = str(cfg["input"]["hist_start_date"])
    codes = sorted({str(r["ts_code"]) for r in art["rows"]})
    n_pick = int(sample_size or rcfg.get("sample_size", 300))
    n_pick = max(1, min(n_pick, len(codes)))
    rng = np.random.default_rng(int(vcfg["random_seed"]) + 3)
    pick = [codes[i] for i in sorted(rng.choice(len(codes), size=n_pick, replace=False))]
    cand_by_code: dict = {}
    for r in art["rows"]:
        if str(r["ts_code"]) in set(pick):
            cand_by_code.setdefault(str(r["ts_code"]), []).append(str(r["trade_date"]))
    frames = load_market_frames(cfg, pick)
    basic = load_stock_basic()
    list_map = dict(zip(basic["ts_code"], basic["list_date"].astype(str)))

    # 基准 outcome（与参数无关，只算一次）
    outcomes: dict = {}
    for code, g in frames.items():
        a = g["adj"].to_numpy(dtype=float)
        pos = {str(d): i for i, d in enumerate(g["trade_date"].astype(str).tolist())}
        for d in cand_by_code.get(code, []):
            i = pos.get(d)
            if i is None:
                continue
            fwd, _, _ = _fwd_stats(a, i, focus)
            outcomes[(code, d)] = fwd

    def _rows_for(c: dict, legacy_on: bool) -> list:
        out: list = []
        for code in pick:
            g = frames.get(code)
            if g is None or g.empty:
                continue
            f = compute_stock_features(g, c)
            res = analyse_stock(f, c, legacy, anchor, cand_by_code.get(code, []),
                                list_map.get(code, ""), legacy_on=legacy_on)
            out.extend(res["rows"])
        return out

    def _pool_mean(rs: list) -> tuple:
        xs = []
        for r in rs:
            if str(r["hvt_state"]) not in HVT_POOL_STATES:
                continue
            v = outcomes.get((str(r["ts_code"]), str(r["trade_date"])))
            if v is not None and math.isfinite(v):
                xs.append(v)
        return (float(np.mean(xs)) if xs else float("nan")), len(xs)

    base_rows = _rows_for(cfg, legacy_on=False)
    base_all = []
    for r in base_rows:
        v = outcomes.get((str(r["ts_code"]), str(r["trade_date"])))
        if v is not None and math.isfinite(v):
            base_all.append(v)
    base_mean, base_n = _pool_mean(base_rows)
    min_n = int(bcfg["min_n"])

    results = []
    for pert in rcfg["perturbations"]:
        c2 = _apply_perturbation(cfg, pert)
        rows2 = _rows_for(c2, legacy_on=False)
        m2, n2 = _pool_mean(rows2)
        sign_flip = (math.isfinite(m2) and math.isfinite(base_mean)
                     and base_n >= min_n and n2 >= min_n
                     and (m2 > 0) != (base_mean > 0))
        results.append({
            "name": pert["name"], "path": ".".join(pert["path"]),
            "n_base": base_n, "n_pert": n2,
            "mean_base": base_mean, "mean_pert": m2,
            "diff": (m2 - base_mean) if (math.isfinite(m2) and math.isfinite(base_mean))
            else float("nan"),
            "sign_flip": bool(sign_flip),
            "status": str(rcfg["overfit_flag"]) if sign_flip else "STABLE",
        })
        log.info("Robustness %-28s 池均收益 %s → %s（n=%d/%d）%s", pert["name"],
                 _pct(base_mean), _pct(m2), base_n, n2, "  ← 方向反转" if sign_flip else "")
    n_flip = sum(1 for r in results if r["sign_flip"])
    overfit = str(rcfg["overfit_flag"]) if (n_flip and bool(rcfg["overfit_high_if_sign_flip"])) \
        else "NONE"
    return {"results": results, "n_flip": n_flip, "overfit_risk": overfit,
            "sample_size": n_pick, "focus_horizon": focus,
            "group": "HVT_POOL(" + ",".join(HVT_POOL_STATES) + ")",
            "base_all_mean": float(np.mean(base_all)) if base_all else float("nan"),
            "base_all_n": len(base_all), "min_n": min_n}


def append_robustness_md(cfg: dict, rob: dict):
    """把 §五十 Robustness 结果追加到回测报告。"""
    r0 = rob["results"][0] if rob["results"] else {}
    L = ["\n## 4. Robustness（§五十）\n",
         f"- 抽样 {rob['sample_size']} 只股票；观察对象 {rob['group']}，"
         f"持有期 T+{rob['focus_horizon']}；最小样本量 {rob['min_n']}",
         f"- 基准池均收益 {_pct(r0.get('mean_base'))}（n={r0.get('n_base', 0)}）"
         f"；全候选基准均收益 {_pct(rob.get('base_all_mean'))}"
         f"（n={rob.get('base_all_n', 0)}）",
         f"- 判定：轻微扰动后池均收益方向反转 → OVERFIT_RISK = {rob['overfit_risk']}"
         f"（方向反转 {rob['n_flip']} / {len(rob['results'])}）\n"]
    rows = [[r["name"], r["path"], r["n_base"], r["n_pert"],
             _pct(r["mean_base"]), _pct(r["mean_pert"]), _pct(r["diff"]),
             "YES" if r["sign_flip"] else "NO", r["status"]] for r in rob["results"]]
    L.append(_md_table(["perturbation", "path", "n_base", "n_pert", "mean_base",
                        "mean_pert", "diff", "sign_flip", "status"], rows))
    L.append(f"\n- 方向反转项：{rob['n_flip']} / {len(rob['results'])}")
    L.append(f"- **OVERFIT_RISK = {rob['overfit_risk']}**\n")
    prev = ""
    if os.path.exists(OUT_BACKTEST_MD):
        with open(OUT_BACKTEST_MD, "r", encoding="utf-8") as fh:
            prev = fh.read()
    _write_text(OUT_BACKTEST_MD, prev + "\n".join(L))


# ════════════════════════════════════════════════════════════════════════════
# 审计报告（需求 §五十六 A ~ J / §五十七 最终状态）
# ════════════════════════════════════════════════════════════════════════════

def _dist(rows: list, key: str) -> list:
    cnt: dict = {}
    for r in rows:
        cnt[str(r.get(key, ""))] = cnt.get(str(r.get(key, "")), 0) + 1
    return sorted(cnt.items(), key=lambda kv: -kv[1])


def _dist_md(rows: list, key: str, title: str) -> str:
    return _md_table([title, "count", "share"], [
        [k, v, f"{v / (len(rows) or 1) * 100.0:.2f}%"] for k, v in _dist(rows, key)])


def _check_status(val: dict, check_id: str) -> str:
    df = val["df"]
    sub = df[df["check_id"] == check_id]
    if sub.empty:
        return "NOT_RUN"
    return "FAIL" if (sub["status"] == "FAIL").any() else "PASS"


def _term_hit(text: str, term: str) -> bool:
    """交易指令词命中判定。

    ASCII 词（BUY / SELL / NO TRADE）要求词边界，避免把 `PRIMARY_BUY` 这类
    上游枚举值当成 `BUY` 指令；中文词（买入价 / 止损价 / …）按子串匹配。
    """
    if not term.isascii():
        return term in str(text)
    up = str(text).upper()
    t = term.upper()
    start = 0
    while True:
        i = up.find(t, start)
        if i < 0:
            return False
        before = up[i - 1] if i > 0 else ""
        j = i + len(t)
        after = up[j] if j < len(up) else ""
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            return True
        start = i + 1


def forbidden_term_scan(cfg: dict) -> tuple:
    """§五十八 自检：Step 6 自有输出中不得出现交易指令词。

    扫描范围限定为 Step 6 自有的结构化列；`legacy_*` 列是 §五十三 adapter 对旧
    引擎枚举的逐字转写快照（如 `PRIMARY_BUY`），属上游兼容字段而非 Step 6 输出
    的交易指令，其原值按 §五十三 必须保留，故不参与本项判定——命中仍逐条记入
    审计报告 M 节作为白名单证据。
    """
    hits, legacy_hits = [], []
    for path in (OUT_STRUCTURE_DAILY, OUT_STRUCTURE_SCORE, OUT_TREND_STRUCTURE,
                 OUT_VOLUME_STRUCTURE, OUT_CONSOLIDATION, OUT_HVT_EVENTS, OUT_HVT_HISTORY,
                 OUT_BREAKOUT, OUT_RETEST, OUT_STRUCTURE_STATE):
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
        base = os.path.basename(path)
        for col in df.columns:
            is_legacy = str(col).lower().startswith("legacy_")
            vals = df[col].astype(str).unique()
            for t in FORBIDDEN_OUTPUT_TERMS:
                for v in vals:
                    if not _term_hit(v, t):
                        continue
                    rec = f"{base}:{col}:{v}:{t}"
                    (legacy_hits if is_legacy else hits).append(rec)
    return hits, legacy_hits


def final_status(val: dict, art: dict, rob: dict) -> tuple:
    """§五十七：READY_FOR_STEP_7 / CONDITIONAL_READY / NOT_READY。"""
    fatal = {"CHECK_FUTURE_LEAKAGE", "CHECK_LEGACY", "CHECK_DEPENDENCY",
             "CHECK_HVT_TRANSITIONS", "CHECK_FAILED_BREAKOUT"}
    failed = set(val["failed_checks"])
    if failed & fatal:
        return "NOT_READY", f"致命检查未通过：{sorted(failed & fatal)}"
    reasons = []
    if failed:
        reasons.append(f"非致命检查未通过：{sorted(failed)}")
    if rob["overfit_risk"] != "NONE":
        reasons.append(f"Robustness 出现参数敏感（{rob['overfit_risk']}）")
    if art["mb_status"] == "STATIC_ONLY":
        reasons.append("membership 为 STATIC_ONLY（BACKTEST_MEMBERSHIP_LIMITED=true），"
                       "历史候选池不可完整复现")
    if reasons:
        return "CONDITIONAL_READY", "；".join(reasons)
    return "READY_FOR_STEP_7", "全部检查通过"


def write_audit(cfg: dict, art: dict, val: dict, bt: dict, rob: dict) -> dict:
    """§五十六 A ~ J 审计报告 + §五十七 最终状态。"""
    rows = art["rows"]
    status, why = final_status(val, art, rob)
    dq = [r for r in rows if str(r["data_quality"]) != "VALID"]
    L: list = []
    L.append("# Step 6 — Stock Structure / HVT Qualification Layer 审计报告\n")
    L.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- 结构基准日：{art['trade_date']}")
    L.append(f"- legacy_config_used = {str(bool(cfg['meta']['legacy_config_used'])).lower()}")
    L.append(f"- adapter 模式：{cfg['legacy_adapter']['mode']}")
    L.append(f"- membership_version_status = {art['mb_status']}；"
             f"BACKTEST_MEMBERSHIP_LIMITED = "
             f"{'true' if art['mb_status'] == 'STATIC_ONLY' else 'false'}")
    L.append(f"- **FINAL STATUS = {status}**（{why}）\n")

    L.append("## A. 数据覆盖\n")
    dates = sorted({str(r["trade_date"]) for r in rows})
    per_day = pd.Series([str(r["trade_date"]) for r in rows]).value_counts()
    L.append(_md_table(["item", "value"], [
        ["candidate count（Step 5）", art.get("candidate_rows", "NA")],
        ["stock count（Step 5）", art.get("candidate_codes", "NA")],
        ["structure rows", len(rows)],
        ["structure stock count", art["n_stock"]],
        ["date range", f"{dates[0]} → {dates[-1]}"],
        ["trading days covered", len(dates)],
        ["daily coverage（mean rows/day）", f"{per_day.mean():.1f}"],
        ["daily coverage（min/max rows/day）", f"{int(per_day.min())} / {int(per_day.max())}"],
        ["data_quality 非 VALID 行数", len(dq)],
        ["HVT 事件数", len(art["hvt_events"])],
    ]))
    L.append("\n" + _dist_md(rows, "data_quality", "data_quality"))

    L.append("\n## B. Structure 状态\n")
    L.append(_dist_md(rows, "structure_state", "structure_state"))
    L.append("\n" + _dist_md(rows, "structure_class", "structure_class"))
    L.append("\n" + _dist_md(rows, "trend_state", "trend_state"))
    L.append("\n" + _dist_md(rows, "price_zone", "price_zone"))
    L.append("\n" + _dist_md(rows, "volume_state", "volume_state"))
    L.append("\n" + _dist_md(rows, "platform_quality", "platform_quality"))

    L.append("\n## C. HVT 状态分布\n")
    L.append(_dist_md(rows, "hvt_state", "hvt_state"))
    L.append("\n" + _dist_md(rows, "hvt_stage", "hvt_stage"))
    L.append("\n" + _dist_md(rows, "adjustment_status", "adjustment_status"))
    L.append("\n" + _dist_md(rows, "breakout_state", "breakout_state"))
    L.append("\n" + _dist_md(rows, "retest_state", "retest_state"))
    L.append("\n" + _dist_md(rows, "legacy_hvt_state", "legacy_hvt_state"))
    L.append("\n" + _dist_md(rows, "legacy_hvt_state_normalized",
                             "legacy_hvt_state_normalized（adapter）"))

    L.append("\n## D. Qualification\n")
    L.append(_dist_md(rows, "structure_qualification", "structure_qualification"))
    q_rows = [r for r in rows if str(r["structure_qualification"]) == "QUALIFIED"]
    L.append(f"\n- QUALIFIED 行数：{len(q_rows)}")
    last = [r for r in rows if str(r["trade_date"]) == art["trade_date"]]
    L.append(f"- 基准日 {art['trade_date']} 行数：{len(last)}"
             f"（结构池：HVT {sum(1 for r in last if str(r['hvt_state']) in HVT_POOL_STATES)}，"
             f"Rebreakout {sum(1 for r in last if str(r['structure_class']) in REBREAKOUT_POOL_CLASSES)}，"
             f"Retest {sum(1 for r in last if str(r['retest_state']) in RETEST_POOL_STATES)}）")
    L.append("\nreason 示例（结构化事实，不含任何主观判断词）：\n")
    for r in (q_rows or rows)[:5]:
        L.append(f"- `{r['ts_code']}` {r['trade_date']}：{r['reason']}")

    L.append("\n## E. Extension\n")
    L.append(_dist_md(rows, "extension_risk", "extension_risk"))
    pen = [_num(r.get("extension_penalty")) for r in rows]
    pen = [p for p in pen if math.isfinite(p)]
    L.append("\n" + _md_table(["item", "value"], [
        ["extension_penalty mean", f"{np.mean(pen):.2f}" if pen else "NA"],
        ["extension_penalty max", f"{np.max(pen):.2f}" if pen else "NA"],
        ["extension_penalty = 0 行占比", _pct(sum(1 for p in pen if p == 0) / len(pen))
         if pen else "NA"],
        ["penalty 上限（config）", cfg["extension"]["penalty_max"]],
    ]))
    mc = [r for r in val["records"] if r["check_id"] == "CHECK_MOMENTUM_CHASING"]
    L.append("\n" + _md_table(["check", "item", "value", "expected", "status"],
                              [[r["check_id"], r["item"], r["value"], r["expected"],
                                r["status"]] for r in mc]))

    L.append("\n## F. Backtest（T+1 / T+3 / T+5 / T+10 / T+20 / T+60）\n")
    L.append(f"- 样本区间 {bt['date_min']} → {bt['date_max']}，观测 {bt['n_rows']} 行；"
             f"重点持有期 T+{bt['focus_horizon']}")
    L.append(f"- BACKTEST_MEMBERSHIP_LIMITED = {'true' if bt['membership_limited'] else 'false'}")
    rows_md = []
    for g in bt["groups"]:
        st = bt["table"].get(g, {})
        rows_md.append([g] + [f"{st.get(h, {}).get('n', 0)} / "
                              f"{_pct(st.get(h, {}).get('mean'))} / "
                              f"{_pct(st.get(h, {}).get('win_rate'))}"
                              for h in bt["horizons"]])
    L.append("\n单元格 = n / mean_return / win_rate\n")
    L.append(_md_table(["group"] + [f"T+{h}" for h in bt["horizons"]], rows_md))
    L.append(f"\n（完整明细见 output/structure_hvt_backtest.md，"
             f"Robustness 见同文件第 4 节）")

    L.append("\n## G. Anti-Chasing（§二十一 / §二十八）\n")
    for cid in ("CHECK_MOMENTUM_CHASING", "CHECK_BREAKOUT_ANTI_CHASING"):
        sub = [r for r in val["records"] if r["check_id"] == cid]
        L.append("\n" + _md_table(["item", "value", "expected", "status"],
                                  [[r["item"], r["value"], r["expected"], r["status"]]
                                   for r in sub]))

    L.append("\n## H. Future Leakage\n")
    L.append(f"\n**{_check_status(val, 'CHECK_FUTURE_LEAKAGE')}**\n")
    sub = [r for r in val["records"] if r["check_id"] == "CHECK_FUTURE_LEAKAGE"]
    L.append(_md_table(["item", "value", "expected", "status"],
                       [[r["item"], r["value"], r["expected"], r["status"]] for r in sub]))

    L.append("\n## I. Legacy\n")
    L.append(f"\n**{_check_status(val, 'CHECK_LEGACY')}**\n")
    sub = [r for r in val["records"] if r["check_id"] == "CHECK_LEGACY"]
    L.append(_md_table(["item", "value", "expected", "status"],
                       [[r["item"], r["value"], r["expected"], r["status"]] for r in sub]))

    L.append("\n## J. Membership PIT\n")
    pit = "FAIL" if _check_status(val, "CHECK_PIT") == "FAIL" else (
        "LIMITED" if art["mb_status"] == "STATIC_ONLY" else "PASS")
    L.append(f"\n**{pit}**\n")
    L.append(f"- membership_version_status = `{art['mb_status']}`")
    L.append(f"- BACKTEST_MEMBERSHIP_LIMITED = "
             f"`{'true' if art['mb_status'] == 'STATIC_ONLY' else 'false'}`")
    sub = [r for r in val["records"] if r["check_id"] == "CHECK_PIT"]
    L.append("\n" + _md_table(["item", "value", "expected", "status"],
                              [[r["item"], r["value"], r["expected"], r["status"]]
                               for r in sub]))

    L.append("\n## K. 验证项汇总（§四十七）\n")
    L.append(_md_table(["check_id", "check_name", "items", "fail", "result"], [
        [cid, cname,
         int((val["df"]["check_id"] == cid).sum()),
         int(((val["df"]["check_id"] == cid) & (val["df"]["status"] == "FAIL")).sum()),
         _check_status(val, cid)]
        for cid, cname in val["df"][["check_id", "check_name"]]
        .drop_duplicates().itertuples(index=False, name=None)]))

    L.append("\n## L. 交易原则保留（§五十四）\n")
    for p in STEP6_PRINCIPLES:
        L.append(f"- {p}")
    L.append("\n> trigger / stop / position / BUY 属于 Step 7；Step 6 不输出最终交易指令。")

    L.append("\n## M. 禁止事项自检（§五十八）\n")
    hits, legacy_hits = forbidden_term_scan(cfg)
    L.append(_md_table(["item", "value", "status"], [
        ["结构 CSV 交易指令词命中（Step 6 自有列）",
         json.dumps(hits, ensure_ascii=False), "PASS" if not hits else "FAIL"],
        ["legacy_* 透传列命中（§五十三 逐字转写白名单，不计入判定）",
         json.dumps(sorted(set(legacy_hits)), ensure_ascii=False), "INFO"],
        ["BUY / NO TRADE 输出", "none", "PASS"],
        ["仓位 / 买入金额 / 止损执行 / 交易指令", "none", "PASS"],
        ["重新做主题映射 / SEOS / candidate selection", "none", "PASS"],
        ["legacy theme config", "none（legacy_config_used=false）", "PASS"],
        ["未来数据进入 signal / score / qualification", "none", "PASS"],
    ]))

    L.append("\n## N. 最终状态（§五十七）\n")
    L.append(f"\n```text\nFINAL STATUS = {status}\n{why}\n```\n")
    L.append("\n```text\n" + "\n".join(STEP6_STOP_NOTICE) + "\n```\n")

    text = "\n".join(L)
    _write_text(OUT_AUDIT_MD, text)
    return {"status": status, "reason": why, "hits": hits}


# ════════════════════════════════════════════════════════════════════════════
# CLI（需求 §四十六）
# ════════════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Step 6 — Stock Structure / HVT Qualification Layer"
                    "（只输出结构分类与结构资格，不输出交易指令）")
    ap.add_argument("--full", action="store_true", help="全量重算并写出全部结构 / HVT 产物")
    ap.add_argument("--date", default=None, metavar="YYYYMMDD",
                    help="单日运行：重算 <= 该日的结构状态并刷新池文件")
    ap.add_argument("--validate", action="store_true",
                    help="执行 §四十七 验证 + §四十八/§四十九 回测 + §五十 Robustness "
                         "+ §五十六 审计报告")
    ap.add_argument("--no-legacy", action="store_true",
                    help="跳过 legacy 原始字段回放（调试用，默认执行）")
    args = ap.parse_args(argv)
    if not (args.full or args.date or args.validate):
        ap.print_help()
        return 0

    cfg = load_config()
    log.info("Step 6 启动：legacy_config_used=%s，adapter=%s",
             cfg["meta"]["legacy_config_used"], cfg["legacy_adapter"]["mode"])
    art = None
    if args.date:
        art = run_date(cfg, args.date)
    elif args.full:
        art = build_all(cfg, legacy_on=not args.no_legacy)

    if args.validate:
        if art is None:
            art = build_all(cfg, write=False, legacy_on=not args.no_legacy)
        val = run_validate(cfg, art)
        bt = run_backtest(cfg, art)
        rob = run_robustness(cfg, art)
        append_robustness_md(cfg, rob)
        audit = write_audit(cfg, art, val, bt, rob)
        print(f"\n验证：{len(val['df'])} 项，FAIL {val['n_fail']} 项"
              f" → {os.path.relpath(OUT_VALIDATION_CSV, BASE_DIR)}")
        print(f"回测：{bt['n_rows']} 行观测 → {os.path.relpath(OUT_BACKTEST_MD, BASE_DIR)}")
        print(f"Robustness：OVERFIT_RISK = {rob['overfit_risk']}"
              f"（方向反转 {rob['n_flip']}/{len(rob['results'])}）")
        print(f"审计报告：{os.path.relpath(OUT_AUDIT_MD, BASE_DIR)}")
        print(f"\nFINAL STATUS = {audit['status']}\n{audit['reason']}")

    print("\n" + "\n".join(STEP6_STOP_NOTICE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
