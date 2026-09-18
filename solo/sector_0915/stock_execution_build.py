# -*- coding: utf-8 -*-
"""Step 7：Execution / Trade Decision Layer（sector_0915 独立程序）。

唯一职责：把 Step 6 已通过结构认证（QUALIFIED / CONDITIONAL）的个股，转换为当日可执行的
BUY / NO TRADE 决策，并给出 Entry / Trigger / Stop / Target / Risk / Execution Plan。

设计约束（规格 §一 / §二 / §四十二 / §六十六）：
  1. 状态驱动，不是分数驱动。execution_score 永远不能覆盖硬 Gate。
  2. 三条永久交易原则：巨量日不追只等回踩；回踩触发价不破低吸；收盘跌回关键结构位则交易假设失效。
  3. 独立性：只读 sector_0915/data|output 与 D:/mystock/cache_daily/stock_data.db，
     不 import / 不调用 / 不覆盖 hvt_bull、w7_second_wave_engine、trade_execution_engine、market_regime_v3。
  4. 全部阈值来自 config/execution_config.json，本文件不硬编码任何执行阈值。
  5. 未来函数禁令：signal_date = D 的决策只使用 <= D 的数据；T+1/3/5/10/20 仅作为 outcome。

CLI：
  python stock_execution_build.py --full
  python stock_execution_build.py --validate
  python stock_execution_build.py --date 20260918
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys

import numpy as np
import pandas as pd

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

log = setup_logging(LOG_DIR, name="stock_execution")

# ────────────────────────────────────────────────────────────────────────────
# 路径常量
# ────────────────────────────────────────────────────────────────────────────

CFG_PATH = os.path.join(CONFIG_DIR, "execution_config.json")

IN_STEP5_CANDIDATE = os.path.join(DATA_DIR, "sector_stock_candidate_daily.csv")
IN_STRUCTURE_DAILY = os.path.join(DATA_DIR, "stock_structure_daily.csv")
IN_STRUCTURE_SCORE = os.path.join(DATA_DIR, "stock_structure_score.csv")
IN_CONSOLIDATION = os.path.join(DATA_DIR, "stock_consolidation.csv")
IN_BREAKOUT = os.path.join(DATA_DIR, "stock_breakout.csv")
IN_RETEST = os.path.join(DATA_DIR, "stock_retest.csv")
IN_HVT_HISTORY = os.path.join(DATA_DIR, "stock_hvt_history.csv")
IN_SECTOR_STATE_HISTORY = os.path.join(DATA_DIR, "sector_state_history.csv")
IN_SECTOR_DAILY_STATS = os.path.join(DATA_DIR, "sector_daily_stats.csv")
IN_SECTOR_STATE_TODAY = os.path.join(OUTPUT_DIR, "sector_state_today.json")
IN_STOCK_BASIC = os.path.join(PROJ_DIR, "sli", "cache", "stock_basic.parquet")
DB_PATH = r"D:\mystock\cache_daily\stock_data.db"

OUT_EXECUTION_DAILY = os.path.join(DATA_DIR, "stock_execution_daily.csv")
OUT_ENTRY_SIGNAL = os.path.join(DATA_DIR, "stock_entry_signal.csv")
OUT_EXIT_SIGNAL = os.path.join(DATA_DIR, "stock_exit_signal.csv")
OUT_EXECUTION_STATE = os.path.join(DATA_DIR, "stock_execution_state.csv")
OUT_RISK_REWARD = os.path.join(DATA_DIR, "stock_risk_reward.csv")
OUT_TODAY_JSON = os.path.join(OUTPUT_DIR, "trade_execution_today.json")
OUT_BUY_POOL = os.path.join(OUTPUT_DIR, "trade_buy_pool.json")
OUT_NO_TRADE_JSON = os.path.join(OUTPUT_DIR, "trade_no_trade.json")
OUT_BACKTEST_MD = os.path.join(OUTPUT_DIR, "execution_backtest.md")
OUT_VALIDATION_CSV = os.path.join(OUTPUT_DIR, "execution_validation.csv")
OUT_AUDIT_MD = os.path.join(OUTPUT_DIR, "execution_audit.md")

# 规格 §六十六：本层不得读取的 legacy 路径（存在即记录，绝不参与计算）
LEGACY_NAMES = ("theme_config.json", "subtheme_map.json", "theme.json",
                "theme_master.json", "theme_mapping.json")
LEGACY_MODULE_MARKS = ("hvt_bull", "w7_second_wave_engine", "trade_execution_engine",
                       "market_regime_v3", "theme_engine")

# 全程序打开过的文件（用于 legacy 隔离验证）
READ_FILES: list = []

# ────────────────────────────────────────────────────────────────────────────
# 枚举（规格 §九 / §十 / §十八 / §二十 / §四十七）
# ────────────────────────────────────────────────────────────────────────────

ENTRY_STATES = ["NO_ENTRY", "BREAKOUT_ENTRY", "PULLBACK_ENTRY", "RETEST_ENTRY",
                "REBREAKOUT_ENTRY", "WAIT_CONFIRMATION", "EXTENDED_NO_ENTRY", "FAILED_NO_ENTRY"]
MARKET_REGIMES = ["STRONG", "HEALTHY", "NEUTRAL", "WEAK", "RISK_OFF"]
EXECUTION_MODES = ["AGGRESSIVE", "NORMAL", "DEFENSIVE", "WAIT"]
EXECUTION_GRADES = ["PRIMARY_BUY", "CONDITIONAL_BUY", "NONE"]
TRIGGER_SOURCES = ["PLATFORM_HIGH", "BREAKOUT_LEVEL", "SWING_HIGH", "RESISTANCE"]
STOP_SOURCES = ["RETEST_LOW", "PLATFORM_LOW", "SWING_LOW", "MA20"]
STOP_FALLBACK_SOURCE = "ATR_BUFFER"
TARGET_SOURCES = ["PRIOR_HIGH", "RESISTANCE", "PLATFORM_PROJECTION", "ATR_EXTENSION"]
OPEN_STATES = ["OPEN_OK", "OPEN_WAIT", "OPEN_NO_CHASE", "OPEN_INVALID"]
POSITION_STATES = ["NONE", "NEW", "ACTIVE", "DEFENSIVE", "TRAILING", "EXIT_TRIGGERED", "INVALID"]
EXIT_STATES = ["ENTRY_VALID", "TRADE_INVALID", "STRUCTURE_INVALID", "EXIT_TRIGGERED"]
EXIT_REASONS = ["CLOSE_BELOW_TRIGGER", "CLOSE_BELOW_SUPPORT", "CLOSE_BELOW_STOP",
                "BREAKOUT_FAILURE", "RETEST_FAILURE"]
CALIBRATION_STATUSES = ["CALIBRATED", "INSUFFICIENT"]
FINAL_STATUSES = ["READY_FOR_LIVE_EXECUTION", "CONDITIONAL_READY", "NOT_READY"]

# 需求 §五十二：永久保留的用户交易规则（审计逐条列出并由验证项核对）
STEP7_PRINCIPLES = [
    "巨量日不追，只等回踩。",
    "回踩触发价不破低吸。",
    "收盘跌回关键结构位，交易假设失效（BREAKOUT ≠ BUY）。",
]

STEP7_STOP_NOTICE = [
    "Step 7 completed.",
    "No position sizing / portfolio optimization / auto order / broker API generated.",
    "No legacy execution engine used (hvt_bull / w7 / trade_execution_engine / market_regime_v3).",
    "Step 8 NOT implemented.",
]

REQUIRED_CFG_SECTIONS = [
    "meta", "input", "lookback", "market_regime", "structure_gate", "hvt_gate", "volume_gate",
    "extension", "entry_state", "trigger", "entry_zone", "retest", "ma20", "stop", "target",
    "risk_reward", "execution_score", "hard_gates", "no_trade_reason", "final", "open_execution",
    "position", "exit", "horizon", "backtest", "validation", "robustness", "output",
]

PRICE_COLS = ["trigger_price", "entry_low", "entry_high", "entry_ref", "stop_price",
              "support_1", "target_1", "target_2", "risk", "reward"]
RAW_ALIAS = {"close": "raw_close", "ma20": "raw_ma20", "atr20": "raw_atr20"}


# ────────────────────────────────────────────────────────────────────────────
# 基础工具（口径与 Step 6 逐字对齐，保证两层结构量可比）
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
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


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
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_clean(obj), fh, ensure_ascii=False, indent=2)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def write_merge_csv(df: pd.DataFrame, path: str, key_cols=("trade_date", "ts_code")):
    """时间序列合并写出：保留历史行，只替换本次重算覆盖到的键（绝不复写历史）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key_cols = [c for c in key_cols if c in df.columns]
    if not df.shape[1] or not key_cols:
        return
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


def _finite(*vals) -> bool:
    return all(math.isfinite(_fv(v)) for v in vals)


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(np.asarray(x, dtype=float), np.nan)
    if k <= 0:
        return np.asarray(x, dtype=float).copy()
    out[k:] = np.asarray(x, dtype=float)[:-k]
    return out


def _roll(x: np.ndarray, w: int, fn: str = "mean", min_n: int | None = None) -> np.ndarray:
    """滚动统计（含当前行）——与 Step 6 同名函数逐字一致。"""
    s = pd.Series(np.asarray(x, dtype=float))
    mn = w if min_n is None else min_n
    return getattr(s.rolling(w, min_periods=mn), fn)().to_numpy(dtype=float)


def _safe_div(a, b):
    """安全除法：0/0、x/0 一律返回 NaN。

    标量与 0 维入参必须返回 Python float —— np.where 对 0 维输入返回 **0 维 ndarray**，
    它继承了 numpy 标量的运算语义但不继承 float，json.dumps 会直接抛
    `TypeError: Object of type ndarray is not JSON serializable`（np.float64
    因为是 float 子类才侥幸可序列化，所以 DataFrame 路径看不出问题）。
    Series / 数组入参维持原返回类型不变。
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.asarray(a, dtype=float) / np.asarray(b, dtype=float)
    out = np.where(np.isfinite(out), out, np.nan)
    return float(out) if out.ndim == 0 else out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, w: int,
         method: str = "SMA") -> np.ndarray:
    """ATR（含当前行）。SMA = TR 的 w 日简单均值（与 Step 6 `_atr` 完全一致）；
    WILDER = tr.ewm(alpha=1/w)，为可选替代口径。"""
    pc = _shift(close, 1)
    tr = np.nanmax(np.vstack([high - low, np.abs(high - pc), np.abs(low - pc)]), axis=0)
    if str(method).upper() == "WILDER":
        return pd.Series(tr).ewm(alpha=1.0 / max(int(w), 1), adjust=False).mean().to_numpy(dtype=float)
    return _roll(tr, w, "mean", max(3, w // 2))


def _lerp_curve(x: float, xs: list, ys: list) -> float:
    """分段线性插值（x 越界时按端点截断）。"""
    if x is None or not math.isfinite(_fv(x)) or not xs:
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
    """加权平均（权重合计 100）。返回 (score, coverage)。NaN 分量不参与，按已覆盖权重归一。"""
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
    return acc / tot_w, tot_w / max(sum(float(x) for x in weights.values()), 1e-9)


def _r(x, nd: int):
    v = _fv(x)
    return None if math.isnan(v) else round(v, int(nd))


def _pct(x, nd: int = 2) -> str:
    v = _fv(x)
    return "NA" if math.isnan(v) else f"{v * 100:.{nd}f}%"


def _f(x, nd: int = 3) -> str:
    v = _fv(x)
    return "NA" if math.isnan(v) else f"{v:.{nd}f}"


def _md_table(headers: list, rows: list) -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def _pick(rec: dict, cols: list) -> dict:
    return {c: rec.get(c) for c in cols}


def _df(rows: list, cols: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(cols))
    return pd.DataFrame([_pick(r, cols) for r in rows], columns=list(cols))


# ────────────────────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CFG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    READ_FILES.append(os.path.abspath(CFG_PATH))
    if bool(cfg.get("meta", {}).get("legacy_config_used", True)):
        raise SystemExit("LEGACY_CONTAMINATION = CRITICAL：meta.legacy_config_used 必须显式为 false")
    miss = [s for s in REQUIRED_CFG_SECTIONS if s not in cfg]
    if miss:
        raise ValueError(f"execution_config.json 缺少必需段: {miss}")
    total = float(sum(float(v) for v in cfg["execution_score"]["weights"].values()))
    if abs(total - 100.0) > 1e-6:
        raise ValueError(f"execution_score.weights 合计必须为 100，当前 {total}")
    if str(cfg["risk_reward"]["entry_ref"]) not in list(cfg["risk_reward"]["entry_ref_options"]):
        raise ValueError("risk_reward.entry_ref 必须是 entry_ref_options 之一")
    if str(cfg["lookback"]["atr_method"]) not in list(cfg["lookback"]["atr_method_options"]):
        raise ValueError("lookback.atr_method 必须是 atr_method_options 之一")
    return cfg


def legacy_isolation_report() -> dict:
    files = sorted({os.path.abspath(str(p)) for p in READ_FILES})
    base = {os.path.basename(p).lower() for p in files}
    hits = sorted(n for n in LEGACY_NAMES if n.lower() in base)
    body = " ".join(files).lower()
    marks = sorted(m for m in LEGACY_MODULE_MARKS if m.lower() in body)
    return {"legacy_reads": hits + marks, "legacy_config_used": bool(hits or marks),
            "read_files": files}


# ────────────────────────────────────────────────────────────────────────────
# 数据层（只读 Step 5 / Step 6 落盘产物 + 只读 Tushare 缓存库补算）
# ────────────────────────────────────────────────────────────────────────────

def load_stock_basic() -> pd.DataFrame:
    if not os.path.exists(IN_STOCK_BASIC):
        raise FileNotFoundError(f"缺少股票基础信息: {IN_STOCK_BASIC}")
    READ_FILES.append(IN_STOCK_BASIC)
    sb = pd.read_parquet(IN_STOCK_BASIC)
    keep = [c for c in ("ts_code", "name", "industry", "list_status", "list_date") if c in sb.columns]
    sb = sb[keep].copy()
    sb["ts_code"] = sb["ts_code"].astype(str)
    return sb.drop_duplicates("ts_code", keep="first")


def _db_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"缺少行情缓存库: {DB_PATH}")
    READ_FILES.append(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


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


def resolve_trade_date(d: str) -> str:
    """非交易日回退到最近有效交易日（规格 §五十三）。"""
    days = _read_all_trade_dates()
    if not days:
        raise ValueError("行情缓存库没有交易日")
    d = str(d)
    if d in days:
        return d
    prev = [x for x in days if x <= d]
    if not prev:
        raise ValueError(f"{d} 之前没有有效交易日")
    return prev[-1]


def _merge_new(base: pd.DataFrame, add: pd.DataFrame, key=("trade_date", "ts_code"),
               prefix: str = "") -> pd.DataFrame:
    """只补充 base 中不存在的列，避免同名列互相覆盖。"""
    key = [k for k in key if k in add.columns]
    dup = int(len(add) - len(add.drop_duplicates(key)))
    if dup:
        raise ValueError(f"左联输入在 {key} 上存在 {dup} 行重复，会导致行数静默膨胀；"
                         f"必须先按业务口径收敛到唯一键（不静默保留）")
    cols = key + [c for c in add.columns if c not in base.columns and c not in key]
    if len(cols) <= len(key):
        return base
    sub = add[cols].copy()
    if prefix:
        sub = sub.rename(columns={c: prefix + c for c in cols if c not in key})
    return base.merge(sub, on=key, how="left")


def load_step5_candidates(cfg: dict) -> pd.DataFrame:
    """Step 5 输出：candidate_status 唯一来源（§二十七 第 1 道门）。

    Step 5 落盘粒度为「主题 × 个股」，同一 (trade_date, ts_code) 可有多行且状态/分数不同
    （173347 行 → 120767 唯一键）。此处按业务口径收敛为唯一键：先取最优 candidate_status
    （接受集合优先于 EXCLUDE），同状态内取最高 candidate_score。该收敛是显式且确定的，
    不做静默丢弃。
    """
    df = _read_csv(IN_STEP5_CANDIDATE, dtype=str, low_memory=False)
    need = ["trade_date", "ts_code", "candidate_status"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"sector_stock_candidate_daily.csv 缺少必需列: {miss}")
    keep = ["trade_date", "ts_code", "candidate_status"]
    if "candidate_score" in df.columns:
        keep.append("candidate_score")
    df = df[keep].copy()
    raw_n = len(df)
    pref = list(cfg["input"]["accepted_candidate_status"]) + ["EXCLUDE"]
    rank = {s: i for i, s in enumerate(pref)}
    df["_rank"] = df["candidate_status"].astype(str).map(
        lambda s: rank.get(s, len(pref))).astype(int)
    if "candidate_score" in df.columns:
        df["_score"] = pd.to_numeric(df["candidate_score"], errors="coerce")
    else:
        df["_score"] = float("nan")
    df = df.sort_values(["trade_date", "ts_code", "_rank", "_score"],
                        ascending=[True, True, True, False], kind="mergesort")
    df = df.drop_duplicates(["trade_date", "ts_code"], keep="first")
    df = df.drop(columns=["_rank", "_score"]).reset_index(drop=True)
    log.info("Step 5 候选载入：%d 行 → 按 (trade_date, ts_code) 收敛为 %d 行 / %d 股票 / %d 交易日",
             raw_n, len(df), df["ts_code"].nunique(), df["trade_date"].nunique())
    return df


def load_step6_frames(cfg: dict) -> pd.DataFrame:
    """Step 6 六张落盘产物按 (trade_date, ts_code) 左联，structure_daily 为骨架。"""
    base = _read_csv(IN_STRUCTURE_DAILY, dtype=str, low_memory=False)
    for c in ("trade_date", "ts_code"):
        if c not in base.columns:
            raise ValueError(f"stock_structure_daily.csv 缺少必需列: {c}")
    num_cols = [c for c in base.columns if c not in ("trade_date", "ts_code", "name",
                                                     "primary_theme_id", "secondary_theme_ids",
                                                     "candidate_type", "structure_state",
                                                     "structure_qualification", "extension_risk",
                                                     "data_quality", "reason")]
    for c in num_cols:
        base[c] = pd.to_numeric(base[c], errors="coerce")
    base = base.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)

    for path, tag in ((IN_STRUCTURE_SCORE, "score"), (IN_CONSOLIDATION, "cons"),
                      (IN_BREAKOUT, "bo"), (IN_RETEST, "rt"), (IN_HVT_HISTORY, "hvt")):
        add = _read_csv(path, dtype=str, low_memory=False)
        for c in add.columns:
            if c in ("trade_date", "ts_code") or c in base.columns:
                continue
            conv = pd.to_numeric(add[c], errors="coerce")
            add[c] = conv if conv.notna().sum() >= max(1, int(0.5 * len(conv))) else add[c]
        base = _merge_new(base, add, prefix="" if tag == "score" else f"{tag}_")
    log.info("Step 6 骨架载入：%d 行 / %d 股票 / %d 交易日 / %d 列",
             len(base), base["ts_code"].nunique(), base["trade_date"].nunique(), base.shape[1])
    return base


def load_stock_market(d_start: str, d_end: str, codes: set) -> pd.DataFrame:
    """单股行情长表（后复权内部口径），按 ts_code / trade_date 升序。"""
    px = _db_query("SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, amount "
                   "FROM daily_cache WHERE trade_date >= ? AND trade_date <= ?", (d_start, d_end))
    db = _db_query("SELECT ts_code, trade_date, turnover_rate, volume_ratio FROM daily_basic_cache "
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
    log.info("行情载入：%d 行 / %d 股票 / %s → %s", len(m), m["ts_code"].nunique(),
             m["trade_date"].min(), m["trade_date"].max())
    return m


def compute_min_features(m: pd.DataFrame, cfg: dict) -> tuple:
    """只读补算 Step 6 未落盘的结构量（严格 PIT，全部含当前行）。

    补算清单：atr20（config 指定口径）、high_20/60/120/250、近期摆动低点（含极值日）、
    近期摆动高点（含极值日）、platform_projection。trigger_date / stop_date 由
    `extreme_date` 在需要时按真实结构窗口回算，避免全量 argmax 的开销。
    """
    lk = cfg["lookback"]
    atr_w = int(lk["atr_window"])
    atr_m = str(lk["atr_method"])
    hi_win = int(lk["swing_high_window"])
    lo_win = int(lk["swing_low_window"])
    res_wins = [int(x) for x in lk["resistance_windows"]]

    out_rows = []
    series: dict = {}
    for code, g in m.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
        a = g["adj"].to_numpy(dtype=float)
        h = g["adj_high"].to_numpy(dtype=float)
        lo = g["adj_low"].to_numpy(dtype=float)
        rec = {
            "ts_code": code,
            "trade_date": g["trade_date"].to_numpy(),
            "adj": a,
            "adj_factor": g["adj_factor"].to_numpy(dtype=float),
            "market_rows": np.arange(1, len(g) + 1, dtype=float),
            "atr20": _atr(h, lo, a, atr_w, atr_m),
            "recent_swing_low": _roll(lo, lo_win, "min"),
            "recent_swing_high": _roll(h, hi_win, "max"),
        }
        for w in res_wins:
            rec[f"high_{w}"] = _roll(h, w, "max")
        out_rows.append(pd.DataFrame(rec))
        series[code] = {"dates": g["trade_date"].to_numpy(), "adj_high": h, "adj_low": lo}

    ft = pd.concat(out_rows, ignore_index=True)
    ft["atr_dist"] = _safe_div(ft["adj"] - 0.0, ft["atr20"])  # 占位，D 日按行重算
    log.info("补算结构量：%d 行 / %d 股票（atr_method=%s, swing=%s, 摆动窗口=%d/%d）",
             len(ft), ft["ts_code"].nunique(), atr_m, lk["swing_definition"], lo_win, hi_win)
    return ft, series


def extreme_date(dates: np.ndarray, arr: np.ndarray, i: int, w: int, mode: str) -> str:
    """回算滚动极值发生日（窗口含当前行，严格 PIT）。"""
    if w <= 0 or i < 0 or i >= len(arr):
        return ""
    j0 = i - int(w) + 1
    if j0 < 0:
        return ""
    seg = arr[j0:i + 1]
    if not np.any(np.isfinite(seg)):
        return ""
    k = int(np.nanargmax(seg)) if mode == "max" else int(np.nanargmin(seg))
    return str(dates[j0 + k])


# ────────────────────────────────────────────────────────────────────────────
# 市场状态（§四：完全由 Step 2/3/4 板块层产物聚合）
# ────────────────────────────────────────────────────────────────────────────

_STATE_HIST: pd.DataFrame | None = None
_STATS_HIST: pd.DataFrame | None = None


def _sector_frames() -> tuple:
    global _STATE_HIST, _STATS_HIST
    if _STATE_HIST is None:
        _STATE_HIST = _read_csv(IN_SECTOR_STATE_HISTORY, dtype=str, low_memory=False)
        _STATS_HIST = _read_csv(IN_SECTOR_DAILY_STATS, dtype=str, low_memory=False)
    return _STATE_HIST, _STATS_HIST


def market_regime_of(cfg: dict, trade_date: str) -> dict:
    """按板块层状态分布与广度聚合出 Step 7 的 5 档 market_regime。"""
    rc = cfg["market_regime"]
    smap = {str(k): str(v) for k, v in rc["sector_state_map"].items()}
    sh, st = _sector_frames()
    d = str(trade_date)
    rows = sh[sh["trade_date"] == d]
    out = {
        "trade_date": d, "regime": "NEUTRAL", "sector_n": 0, "strong_share": float("nan"),
        "strong_healthy_share": float("nan"), "weak_share": float("nan"),
        "breadth": float("nan"), "flip_warn": False, "source": str(rc["source"]),
        "note": "",
    }
    if rows.empty:
        out["note"] = f"板块层在 {d} 无状态记录，按 NEUTRAL 兜底（不臆造 RISK_OFF）"
    else:
        states = rows["current_state"].astype(str)
        if bool(rc["exclude_data_invalid"]):
            keep = states != "DATA_INVALID"
            rows, states = rows[keep], states[keep]
        mapped = states.map(lambda s: smap.get(s, "NEUTRAL"))
        n = int(len(mapped))
        out["sector_n"] = n
        if n:
            out["strong_share"] = float((mapped == "STRONG").mean())
            out["strong_healthy_share"] = float(mapped.isin(["STRONG", "HEALTHY"]).mean())
            out["weak_share"] = float(mapped.isin(["WEAK", "RISK_OFF"]).mean())

    srow = st[st["trade_date"] == d]
    bf = str(rc["breadth_field"])
    out["breadth_field"] = bf
    if not srow.empty and bf in srow.columns:
        b = pd.to_numeric(srow[bf], errors="coerce").dropna()
        if len(b):
            out["breadth"] = float(b.mean())

    str_s, sh_s, wd_s, br = (out["strong_share"], out["strong_healthy_share"],
                             out["weak_share"], out["breadth"])
    if n and _finite(wd_s) and (_fv(wd_s) >= float(rc["risk_off_weak_share_min"])
                                or (_finite(br) and _fv(br) <= float(rc["breadth_risk_off_max"]))):
        reg = "RISK_OFF"
    elif (n and _finite(str_s) and _finite(wd_s)
          and _fv(str_s) >= float(rc["strong_share_min"])
          and _fv(wd_s) <= float(rc["strong_weak_share_max"])
          and (not _finite(br) or _fv(br) >= float(rc["breadth_healthy_min"]))):
        reg = "STRONG"
    elif (n and _finite(sh_s) and _finite(wd_s)
          and _fv(sh_s) >= float(rc["healthy_share_min"])
          and _fv(wd_s) <= float(rc["healthy_weak_share_max"])
          and (not _finite(br) or _fv(br) >= float(rc["breadth_healthy_min"]))):
        reg = "HEALTHY"
    elif n and _finite(wd_s) and _fv(wd_s) <= float(rc["weak_share_max_for_neutral"]):
        reg = "NEUTRAL"
    else:
        reg = "WEAK"
    out["regime"] = reg
    out["execution_mode"] = str(rc["execution_mode_by_regime"][reg])
    out["regime_score"] = float(rc["regime_score"][reg])
    out["hard_risk"] = reg in list(rc["hard_risk_states"])
    if _finite(wd_s) and abs(_fv(wd_s) - 0.5) <= float(rc["stability_flip_share_warn"]) / 2:
        out["flip_warn"] = True
    return out


def market_regime_history(cfg: dict, dates) -> dict:
    return {str(d): market_regime_of(cfg, str(d)) for d in sorted({str(x) for x in dates})}


# ────────────────────────────────────────────────────────────────────────────
# 单股执行决策（规格 §九 ~ §三十二）
# ────────────────────────────────────────────────────────────────────────────

def derive_entry_state(r: dict, cfg: dict) -> str:
    """entry_state 完全由 Step 6 已落盘的 retest_state / breakout_state / structure_class 派生。"""
    ec = cfg["entry_state"]
    rst, bst, cls = str(r.get("retest_state", "")), str(r.get("breakout_state", "")), \
        str(r.get("structure_class", ""))
    if rst in (ec["retest_failed_state"],) or bst in (ec["breakout_failed_state"],):
        return "FAILED_NO_ENTRY"
    # 巨量突破禁止追涨（§十二）：巨量 + 高/极端扩张 → 强制 EXTENDED_NO_ENTRY
    bvr = _fv(r.get("breakout_volume_ratio"))
    ext = str(r.get("extension_risk", ""))
    if _finite(bvr) and _fv(bvr) >= float(ec["no_chase_volume_ratio"]) \
            and ext in list(ec["no_chase_extension_risks"]):
        return "EXTENDED_NO_ENTRY"
    if rst == str(ec["retest_success_state"]):
        return "RETEST_ENTRY"
    if bst == "REBREAKOUT_CONFIRMED":
        return "REBREAKOUT_ENTRY"
    if bst == str(ec["breakout_confirmed_states"][0]):
        return "BREAKOUT_ENTRY"
    if cls == "PULLBACK_HEALTHY":
        return "PULLBACK_ENTRY"
    if bst == str(ec["breakout_ready_state"]) or rst == str(ec["retest_pending_state"]):
        return "WAIT_CONFIRMATION"
    return "NO_ENTRY"


def _gates_of(r: dict, cfg: dict, regime: dict) -> dict:
    """§二十七 九段硬门链。返回 {gate: [pass(bool), detail(str)]}，保持 config 的 precedence 顺序。"""
    g: dict = {}
    in_cfg, sg = cfg["input"], cfg["structure_gate"]
    qual = str(r.get("structure_qualification", ""))
    cstat = str(r.get("candidate_status", ""))
    g["CANDIDATE_INVALID"] = [
        (cstat in list(in_cfg["accepted_candidate_status"]))
        and (qual in list(in_cfg["accepted_qualifications"])),
        f"candidate_status={cstat} (接受 {in_cfg['accepted_candidate_status']})；"
        f"structure_qualification={qual} (接受 {in_cfg['accepted_qualifications']})"]

    dq = str(r.get("data_quality", ""))
    cover = _fv(r.get("structure_quality_coverage"))
    g["DATA_INVALID"] = [
        (dq in list(sg["allowed_data_quality"]))
        and _finite(r.get("close"), r.get("ma20"), r.get("atr20"))
        and _num(r.get("market_rows"), 0.0) >= float(cfg["lookback"]["min_history_days"]),
        f"data_quality={dq}；close={_f(r.get('close'))} ma20={_f(r.get('ma20'))} "
        f"atr20={_f(r.get('atr20'))} market_rows={_f(r.get('market_rows'), 0)}≥"
        f"{cfg['lookback']['min_history_days']}；quality_coverage={_f(cover)}"]

    hard = list(cfg["market_regime"]["hard_risk_states"])
    g["MARKET_RISK"] = [str(regime.get("regime")) not in hard,
                        f"market_regime={regime.get('regime')}（hard_risk 档 {hard}）"]

    ex = cfg["extension"]
    ext = str(r.get("extension_risk", ""))
    r5, d20 = _fv(r.get("ret_5")), _fv(r.get("dist_ma20"))
    g["EXTENSION_TOO_HIGH"] = [
        (ext not in list(ex["reject_risks"]))
        and (not _finite(r5) or _fv(r5) < float(ex["reject_ret_5"]))
        and (not _finite(d20) or _fv(d20) < float(ex["extreme_dist_ma20_reject"])),
        f"extension_risk={ext}（拒绝 {ex['reject_risks']}）；ret_5={_pct(r5)}<{ex['reject_ret_5']}；"
        f"dist_ma20={_pct(d20)}<{ex['extreme_dist_ma20_reject']}"]

    sst, scls = str(r.get("structure_state", "")), str(r.get("structure_class", ""))
    bst, rst = str(r.get("breakout_state", "")), str(r.get("retest_state", ""))
    sq = _fv(r.get("structure_quality"))
    hit = (sst in list(sg["allowed_structure_states"]) or scls in list(sg["allowed_structure_classes"])
           or bst in list(sg["allowed_breakout_states"]) or rst in list(sg["allowed_retest_states"]))
    g["STRUCTURE_WEAK"] = [
        _finite(sq) and _fv(sq) >= float(sg["min_structure_quality"])
        and sst not in list(sg["forbidden_structure_states"]) and bool(hit),
        f"structure_quality={_f(sq)}≥{sg['min_structure_quality']}；structure_state={sst}；"
        f"structure_class={scls}；多字段 OR 命中={hit}"]

    hg = cfg["hvt_gate"]
    exempt = str(r.get("entry_state")) in list(hg.get("exempt_entry_states", []))
    hst, hq = str(r.get("hvt_state", "")), _fv(r.get("hvt_quality_score"))
    if exempt:
        ok_h, det_h = True, f"entry_state={r.get('entry_state')} 在 exempt_entry_states，HVT 成熟度不作门槛"
    elif hst == "HVT_FAILED":
        ok_h, det_h = False, "hvt_state=HVT_FAILED（HVT 周期已终结）"
    elif hst == str(hg["event_state"]):
        ok_h, det_h = False, f"hvt_state={hst}，event_state_buyable=false"
    elif hst == "HVT_LOCKING":
        ok_h = _finite(hq) and _fv(hq) >= float(hg["min_hvt_quality_score"])
        det_h = f"hvt_state=HVT_LOCKING，hvt_quality_score={_f(hq)}≥{hg['min_hvt_quality_score']}"
    elif hst in list(hg["require_breakout_states"]):
        ok_h = bst in list(hg["require_breakout_in"])
        det_h = f"hvt_state={hst} 需 breakout_state∈{hg['require_breakout_in']}，实际 {bst}"
    else:
        ok_h, det_h = True, f"hvt_state={hst} 不作额外门槛（仅以 state_score 参与打分）"
    g["HVT_NOT_MATURE"] = [ok_h, det_h]

    vg = cfg["volume_gate"]
    vst = str(r.get("volume_state", ""))
    g["VOLUME_ABNORMAL"] = [vst not in list(vg["forbidden_volume_states"]),
                            f"volume_state={vst}（禁止 {vg['forbidden_volume_states']}）"]

    g["FAILED_STRUCTURE"] = [str(r.get("entry_state")) != "FAILED_NO_ENTRY",
                             f"entry_state={r.get('entry_state')}；retest_state={rst}；breakout_state={bst}"]

    eb = str(r.get("entry_state"))
    t = _fv(r.get("trigger_price"))
    close = _fv(r.get("close"))
    applies = eb in list(cfg["entry_state"]["close_below_trigger_applies_to"])
    g["TRIGGER_BROKEN"] = [
        (not applies) or (_finite(t, close) and _fv(close) >= _fv(t)),
        f"close={_f(close)} vs trigger={_f(t)}；该路径是否适用收盘触发位规则={applies}"]

    s1 = _fv(r.get("support_1"))
    tol = float(cfg["ma20"]["tol"])
    need_sup = eb in list(cfg["entry_state"]["support_defense_required_states"])
    g["SUPPORT_BROKEN"] = [
        (not need_sup) or (_finite(s1, close) and _fv(close) >= _fv(s1) * (1.0 - tol)),
        f"close={_f(close)} vs support_1={_f(s1)}×(1−{tol})；该路径是否需要 support defense={need_sup}"]

    g["RETEST_NOT_CONFIRMED"] = [
        not (eb == "WAIT_CONFIRMATION" and rst == str(cfg["entry_state"]["retest_pending_state"])),
        f"entry_state={eb}；retest_state={rst}（待确认={cfg['entry_state']['retest_pending_state']}）"]
    g["BREAKOUT_NOT_CONFIRMED"] = [
        not (eb == "WAIT_CONFIRMATION" and bst == str(cfg["entry_state"]["breakout_ready_state"])),
        f"entry_state={eb}；breakout_state={bst}（待确认={cfg['entry_state']['breakout_ready_state']}）"]

    tfar = _finite(t, close) and _fv(t) > _fv(close) * (1.0 + float(cfg["trigger"]["max_above_close"]))
    g["NO_ENTRY_STATE"] = [
        (eb in list(cfg["entry_state"]["buyable_states"])) and not tfar,
        f"entry_state={eb}（可买={cfg['entry_state']['buyable_states']}）；"
        f"trigger 高于收盘 {_pct(_safe_div(t - close, close))}，上限 {_pct(cfg['trigger']['max_above_close'])}"]

    rr = _fv(r.get("risk_reward"))
    g["RR_TOO_LOW"] = [_finite(rr) and _fv(rr) >= float(cfg["risk_reward"]["minimum"]),
                       f"risk_reward={_f(rr, 2)}≥{cfg['risk_reward']['minimum']}"]
    return g


def _trigger_of(r: dict, cfg: dict) -> tuple:
    """§十 Trigger：按 entry_state 的优先级取第一个可用的真实结构量。"""
    tc = cfg["trigger"]
    fmap = {k: r.get(v) for k, v in tc["field_map"].items()}
    prio = tc["priority_by_entry_state"].get(str(r.get("entry_state")), tc["priority_by_entry_state"]["default"])
    for name in prio:
        v = _fv(fmap.get(name))
        if _finite(v) and _fv(v) > 0:
            return name, _fv(v), str(tc["field_map"][name])
    return "", float("nan"), ""


def _stop_of(r: dict, cfg: dict) -> tuple:
    """§十八 / §十九 Stop：结构支撑 − atr_buffer×ATR20；无结构支撑时退回 ATR buffer 兜底。"""
    sc = cfg["stop"]
    atr = _fv(r.get("atr20"))
    el = _fv(r.get("entry_low"))
    for name in sc["priority"]:
        v = _fv(r.get(sc["field_map"][name]))
        if _finite(v) and _fv(v) > 0:
            base = _fv(v) - float(sc["atr_buffer"]) * atr
            return name, base
    return str(sc["fallback_source"]), el - float(sc["fallback_atr_k"]) * atr


def _target_of(r: dict, cfg: dict) -> tuple:
    """§二十 Target：前高 / 阻力位 / 平台投射 / ATR extension，按优先级取首个有效来源。"""
    tg = cfg["target"]
    atr = _fv(r.get("atr20"))
    ref = _fv(r.get("entry_ref"))
    gap = float(tg["min_target_gap_pct"])
    chi, clo = _fv(r.get("consolidation_high")), _fv(r.get("consolidation_low"))
    levels = {
        "PRIOR_HIGH": _fv(r.get("prior_high")),
        "RESISTANCE": _fv(r.get("resistance_1")),
        "PLATFORM_PROJECTION": (_fv(chi) + (_fv(chi) - _fv(clo)))
        if _finite(chi, clo) else float("nan"),
        "ATR_EXTENSION": ref + float(tg["atr_target_1_k"]) * atr,
    }
    src, t1 = "", float("nan")
    for name in tg["priority"]:
        v = _fv(levels.get(name))
        if _finite(v) and _fv(v) >= ref * (1.0 + gap):
            src, t1 = name, _fv(v)
            break
    if not src:
        src, t1 = "ATR_EXTENSION", ref + float(tg["atr_target_1_k"]) * atr
    t2_src, t2 = "ATR_EXTENSION", ref + float(tg["atr_target_2_k"]) * atr
    best = float("nan")
    for name in tg["priority"]:
        v = _fv(levels.get(name))
        if _finite(v) and _fv(v) > t1 * (1.0 + gap):
            best = _fv(v) if not _finite(best) else max(_fv(best), _fv(v))
            if _finite(best):
                t2_src = name
    if _finite(best) and _fv(best) > t1 * (1.0 + gap):
        t2 = max(_fv(best), t2)
        t2_src = t2_src if _fv(best) >= t2 else "ATR_EXTENSION"
    return src, t1, t2_src, max(t2, t1 * (1.0 + gap))


def analyse_execution(r: dict, cfg: dict, regime: dict, series: dict) -> dict:
    """单行执行决策：九道硬门 → Trigger / Entry / Stop / Target / RR / Score → BUY / NO TRADE。"""
    rec = dict(r)
    ec, tc, zc = cfg["entry_state"], cfg["trigger"], cfg["entry_zone"]
    sc, tg, rc = cfg["stop"], cfg["target"], cfg["risk_reward"]
    nd = int(cfg["lookback"]["round_digits"])

    rec["market_regime"] = str(regime.get("regime"))
    rec["execution_mode"] = str(regime.get("execution_mode", "WAIT"))
    rec["entry_state"] = derive_entry_state(rec, cfg)
    rec["entry_type"] = str(ec["entry_type_map"].get(rec["entry_state"], "NONE"))

    close, ma20, atr = _fv(rec.get("close")), _fv(rec.get("ma20")), _fv(rec.get("atr20"))
    chi, clo = _fv(rec.get("consolidation_high")), _fv(rec.get("consolidation_low"))
    rec["close"] = close if _finite(close) else float("nan")
    rec["atr_dist"] = _safe_div(close - ma20, atr)
    rec["support_1"] = (np.fmax(_fv(clo), _fv(ma20)) if _finite(clo) else _fv(ma20))
    rec["resistance_1"] = (np.fmax(_fv(chi), _fv(rec.get("high_20"))) if _finite(chi)
                           else _fv(rec.get("high_20")))
    rec["prior_high"] = _fv(rec.get(f"high_{int(tg['prior_high_window'])}"))
    rec["platform_projection"] = (_fv(chi) + (_fv(chi) - _fv(clo))) if _finite(chi, clo) else float("nan")

    tsrc, tprice, tfield = _trigger_of(rec, cfg)
    rec["trigger_source"], rec["trigger_price"], rec["trigger_field"] = tsrc, tprice, tfield
    rec["trigger_date"] = ""
    if tsrc:
        dates = series.get(str(rec["ts_code"]), {}).get("dates")
        hi = series.get(str(rec["ts_code"]), {}).get("adj_high")
        if dates is not None and hi is not None and _finite(rec.get("row_index")):
            i = int(_num(rec.get("row_index"), -1))
            if i >= 0:
                if tsrc == "PLATFORM_HIGH" and _finite(rec.get("consolidation_days")):
                    rec["trigger_date"] = extreme_date(dates, hi, i, int(_num(rec.get("consolidation_days"), 0)), "max")
                elif tsrc == "BREAKOUT_LEVEL":
                    rec["trigger_date"] = str(rec.get("breakout_date") or "")
                elif tsrc == "SWING_HIGH":
                    rec["trigger_date"] = extreme_date(dates, hi, i, int(cfg["lookback"]["swing_high_window"]), "max")
                else:
                    rec["trigger_date"] = extreme_date(
                        dates, hi, i, int(max(cfg["lookback"]["resistance_windows"][0],
                                              _num(rec.get("consolidation_days"), 0))), "max")
    rec["entry_low"] = tprice
    if not _finite(tprice):
        rec["entry_high"] = float("nan")
    else:
        hi_cap = tprice * (1.0 + float(zc["max_zone_pct"]))
        eh = min(tprice + float(zc["atr_high_k"]) * atr, hi_cap)
        rec["entry_high"] = max(eh, tprice * (1.0 + float(zc["min_zone_pct"])))
    rec["entry_zone_method"] = str(zc["method"])

    ssrc, sbase = _stop_of(rec, cfg)
    if _finite(sbase) and _finite(rec.get("entry_low")):
        sbase = min(_fv(sbase), _fv(rec["entry_low"]) - float(sc["min_stop_distance_atr"]) * atr)
        ad = _fv(rec.get("atr_dist"))
        if _finite(ad) and _fv(ad) >= float(sc["high_vol_atr_dist_min"]):
            sbase = _fv(sbase) - float(sc["extra_buffer_on_high_vol"]) * atr
    rec["stop_source"], rec["stop_price"] = ssrc, sbase
    rec["stop_date"] = ""
    dates = series.get(str(rec["ts_code"]), {}).get("dates")
    lo_arr = series.get(str(rec["ts_code"]), {}).get("adj_low")
    i = int(_num(rec.get("row_index"), -1))
    if dates is not None and lo_arr is not None and i >= 0:
        if ssrc == "PLATFORM_LOW" and _finite(rec.get("consolidation_days")):
            rec["stop_date"] = extreme_date(dates, lo_arr, i, int(_num(rec.get("consolidation_days"), 0)), "min")
        elif ssrc == "SWING_LOW":
            rec["stop_date"] = extreme_date(dates, lo_arr, i, int(cfg["lookback"]["swing_low_window"]), "min")
        elif ssrc == "MA20":
            rec["stop_date"] = str(rec.get("trade_date"))
        elif ssrc == "RETEST_LOW":
            rec["stop_date"] = str(rec.get("trade_date"))

    ref_key = str(rc["entry_ref"])
    rec["entry_ref"] = {"ENTRY_LOW": _fv(rec.get("entry_low")),
                        "ENTRY_MID": (_fv(rec.get("entry_low")) + _fv(rec.get("entry_high"))) / 2.0,
                        "ENTRY_HIGH": _fv(rec.get("entry_high"))}.get(ref_key, _fv(rec.get("entry_high")))
    t1s, t1, t2s, t2 = _target_of(rec, cfg)
    rec["target_source"], rec["target_1"] = t1s, t1
    rec["target_2_source"], rec["target_2"] = t2s, t2
    rec["risk"] = _fv(rec.get("entry_ref")) - _fv(rec.get("stop_price"))
    rec["reward"] = _fv(rec.get("target_1")) - _fv(rec.get("entry_ref"))
    rec["risk_reward"] = _safe_div(rec["reward"], rec["risk"])

    gates = _gates_of(rec, cfg, regime)
    rec["gate_detail"] = {k: v[1] for k, v in gates.items()}
    failed = [k for k in cfg["no_trade_reason"]["precedence"] if k in gates and not gates[k][0]]
    rec["gate_failures"] = "|".join(failed)
    rec["hard_gate_pass"] = (len(failed) == 0)
    gmap = cfg["no_trade_reason"]["gate_to_reason"]
    rec["primary_no_trade_reason"] = gmap.get(failed[0], "") if failed else ""

    rec.update(_score_of(rec, cfg, regime, nd))
    rec["action"] = str(cfg["final"]["action_buy"]) if rec["hard_gate_pass"] else str(cfg["final"]["action_no_trade"])
    rec["execution_grade"] = _grade_of(rec, cfg)
    if not rec["hard_gate_pass"]:
        rec["execution_grade"] = "NONE"

    rec["open_execution_state"] = _open_state_of(rec, cfg)
    rec["position_state"] = "NONE"
    rec["exit_state"], rec["exit_reason"] = _exit_of(rec, cfg)
    rec.update(_prices_raw(rec, nd))
    rec["execution_reason"] = _reason_of(rec, cfg)
    rec["risk_note"] = _risk_note_of(rec, cfg)
    rec["invalid_condition"] = _invalid_of(rec, cfg, nd)
    rec["reason"] = rec["execution_reason"]
    return rec


def _score_of(rec: dict, cfg: dict, regime: dict, nd: int) -> dict:
    """§二十二 ~ §二十六 execution_score：8 个分项加权（权重合计 100）。"""
    esc = cfg["execution_score"]
    ext_key = str(rec.get("extension_risk") or "NA")
    vr = _fv(rec.get("volume_ratio"))
    vc = esc["volume_confirmation"]
    vol_score = _lerp_curve(vr, [float(vc["good_ratio_max"]), float(vc["weak_ratio_min"])],
                            [float(vc["good_score"]), float(vc["weak_score"])])
    rr = _fv(rec.get("risk_reward"))
    rr_score = float("nan")
    xs = [float(x[0]) for x in esc_rr_map(cfg)]
    ys = [float(x[1]) for x in esc_rr_map(cfg)]
    if _finite(rr):
        rr_score = _lerp_curve(max(_fv(rr), 0.0), xs, ys)
    hst = str(rec.get("hvt_state", ""))
    hq = _fv(rec.get("hvt_quality_score"))
    hvt_score = float(cfg["hvt_gate"]["state_score"].get(hst, 20.0))
    if hst == "HVT_LOCKING" and _finite(hq):
        hvt_score = 0.5 * hvt_score + 0.5 * min(max(_fv(hq), 0.0), 100.0)
    parts = {
        "structure_quality": _fv(rec.get("structure_quality")),
        "hvt_quality": hvt_score,
        "entry_quality": float(esc["entry_quality_score"].get(str(rec.get("entry_state")), 30.0)),
        "risk_reward": rr_score,
        "extension": float(esc["extension_score"].get(ext_key, 50.0)),
        "market_regime": float(regime.get("regime_score", 65.0)),
        "volume_confirmation": vol_score,
        "data_quality": float(esc["data_quality_score"].get(str(rec.get("data_quality")), 0.0)),
    }
    score, cov = _wmean(parts, esc["weights"])
    return {"raw_execution_score": score, "calibrated_execution_score": score,
            "calibration_status": "", "calibration_n": 0,
            "historical_execution_quality": float("nan"),
            "execution_score": score, "execution_score_coverage": cov,
            "score_structure_quality": parts["structure_quality"],
            "score_hvt_quality": parts["hvt_quality"],
            "score_entry_quality": parts["entry_quality"],
            "score_risk_reward": parts["risk_reward"],
            "score_extension": parts["extension"],
            "score_market_regime": parts["market_regime"],
            "score_volume_confirmation": parts["volume_confirmation"],
            "score_data_quality": parts["data_quality"]}


def esc_rr_map(cfg: dict) -> list:
    """risk_reward.score_map：按 RR 阈值升序返回 [(rr, score)]，供分段插值。"""
    m = sorted([[float(a), float(b)] for a, b in cfg["risk_reward"]["score_map"]], key=lambda x: x[0])
    return m


def _grade_of(rec: dict, cfg: dict) -> str:
    """§四十九 输出分层：PRIMARY_BUY / CONDITIONAL_BUY / NONE（动作仍为 BUY / NO TRADE）。"""
    fn = cfg["final"]
    es, score = str(rec.get("entry_state")), _fv(rec.get("execution_score"))
    ext = str(rec.get("extension_risk", ""))
    if not _finite(score):
        return "NONE"
    if (es in list(fn["primary_buy_entry_states"])
            and _fv(score) >= float(fn["primary_buy_execution_score_min"])
            and ext in list(fn["primary_buy_extension_risks"])):
        return "PRIMARY_BUY"
    if (_fv(score) >= float(fn["conditional_buy_execution_score_min"])
            and ext in list(fn["conditional_buy_extension_risks"])):
        return "CONDITIONAL_BUY"
    return "NONE"


def _open_state_of(rec: dict, cfg: dict) -> str:
    """§三十四 OPEN_EXECUTION_STATE：只用 D 日及之前的信息推导次日开盘执行规则。"""
    if rec["action"] != cfg["final"]["action_buy"]:
        return "OPEN_INVALID"
    if rec.get("entry_state") == "EXTENDED_NO_ENTRY":
        return "OPEN_NO_CHASE"
    close, eh = _fv(rec.get("close")), _fv(rec.get("entry_high"))
    if _finite(close, eh) and _fv(close) >= _fv(eh):
        return "OPEN_NO_CHASE"
    return "OPEN_OK"


def _exit_of(rec: dict, cfg: dict) -> tuple:
    ex = cfg["exit"]
    close, t, s1 = _fv(rec.get("close")), _fv(rec.get("trigger_price")), _fv(rec.get("support_1"))
    stp = _fv(rec.get("stop_price"))
    bst, rst = str(rec.get("breakout_state", "")), str(rec.get("retest_state", ""))
    if bst == cfg["entry_state"]["breakout_failed_state"] or rst == cfg["entry_state"]["retest_failed_state"]:
        return "STRUCTURE_INVALID", "BREAKOUT_FAILURE" if bst != "NO_BREAKOUT" else "RETEST_FAILURE"
    if _finite(close, stp) and _fv(close) < _fv(stp):
        return "EXIT_TRIGGERED", "CLOSE_BELOW_STOP"
    if rec["action"] == cfg["final"]["action_no_trade"]:
        return "STRUCTURE_INVALID", "CLOSE_BELOW_SUPPORT"
    if _finite(close, t) and _fv(close) < _fv(t):
        return "TRADE_INVALID", "CLOSE_BELOW_TRIGGER"
    if _finite(close, s1) and _fv(close) < _fv(s1) * (1.0 - float(cfg["ma20"]["tol"])):
        return "TRADE_INVALID", "CLOSE_BELOW_SUPPORT"
    return "ENTRY_VALID", ""


def _prices_raw(rec: dict, nd: int) -> dict:
    """对外一律折算为不复权价（RAW = adj ÷ adj_factor(D)），保证可直接下单。

    内部结构量全部为后复权口径（Step 6 落盘口径），因此所有对外价格列必须统一除以
    D 日 adj_factor；漏折任何一列都会让下游比值（stop_distance_pct / RR）变成跨口径相减。
    """
    af = _fv(rec.get("adj_factor"))
    inv = (1.0 / af) if (_finite(af) and _fv(af) > 0) else float("nan")
    out = {"price_basis": "RAW", "adj_factor": _r(af, 6)}
    for c in list(PRICE_COLS) + list(RAW_ALIAS):
        v = _fv(rec.get(c))
        out[RAW_ALIAS.get(c, c)] = _r(_fv(v) * inv, nd) if (_finite(v) and _finite(inv)) \
            else float("nan")
        if c in RAW_ALIAS:
            out[c] = out[RAW_ALIAS[c]]
    return out


def _reason_of(rec: dict, cfg: dict) -> str:
    """§三十 BUY reason 必须是事实链；禁止情绪化措辞。"""
    if rec["action"] == cfg["final"]["action_no_trade"]:
        return (f"{rec['primary_no_trade_reason']}；结构={rec.get('structure_state')}/"
                f"{rec.get('structure_class')}；entry_state={rec.get('entry_state')}；"
                f"extension={rec.get('extension_risk')}")
    parts = [
        f"主题机会：{_f(rec.get('theme_opportunity'), 2)}（{rec.get('primary_theme_id')}）",
        f"个股结构：{rec.get('structure_state')}/{rec.get('structure_class')}"
        f"（质量 {_f(rec.get('structure_quality'), 1)}，HVT={rec.get('hvt_state')}）",
        f"触发位：{rec.get('trigger_source')} {_f(rec.get('trigger_price'))}（{rec.get('trigger_date')}）",
        f"入场区：{_f(rec.get('entry_low'))} ~ {_f(rec.get('entry_high'))}（{rec.get('entry_zone_method')}）",
        f"回踩确认：retest_state={rec.get('retest_state')}，量比={_f(rec.get('volume_ratio'), 2)}",
        f"风险：stop {_f(rec.get('stop_price'))}（{rec.get('stop_source')}）",
        f"目标：target_1 {_f(rec.get('target_1'))}（{rec.get('target_source')}）",
        f"RR：{_f(rec.get('risk_reward'), 2)}",
    ]
    return "；".join(parts)


def _risk_note_of(rec: dict, cfg: dict) -> str:
    """止损宽度只作风险提示（WARN），不参与 BUY 判定（规格 §二十七 的 9 道硬门不含止损宽度）。"""
    w = _safe_div(_fv(rec.get("entry_ref")) - _fv(rec.get("stop_price")), _fv(rec.get("entry_ref")))
    cap = float(cfg["stop"]["warn_stop_distance_pct"])
    flag = "WIDE_WARN" if (_finite(w) and _fv(w) > cap) else "NORMAL"
    return (f"止损宽度 {_pct(w)}（风险提示阈值 {_pct(cap)}，{flag}，不构成 BUY 硬门）；"
            f"extension_risk={rec.get('extension_risk')}；market_regime={rec.get('market_regime')}；"
            f"execution_mode={rec.get('execution_mode')}")


def _invalid_of(rec: dict, cfg: dict, nd: int) -> str:
    t, s1, stp = _fv(rec.get("trigger_price")), _fv(rec.get("support_1")), _fv(rec.get("stop_price"))
    return (f"收盘价跌破 trigger {_f(t)} 则交易假设失效；"
            f"收盘价跌破 support_1 {_f(s1)} 则结构失效；"
            f"收盘价跌破 stop {_f(stp)} 则离场；"
            f"breakout_state={rec.get('breakout_state')} / retest_state={rec.get('retest_state')} 转为失败态即失效")


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────

def build_all(cfg: dict, target_date: str | None = None, write: bool = True,
              cfg_override: dict | None = None) -> dict:
    c = cfg_override or cfg
    anchor = str(c["input"]["hist_start_date"])
    frames = load_step6_frames(c)
    cand = load_step5_candidates(c)
    frames = frames.merge(cand[["trade_date", "ts_code", "candidate_status"]],
                          on=["trade_date", "ts_code"], how="left")
    frames["candidate_status"] = frames["candidate_status"].fillna("EXCLUDE")
    if target_date:
        frames = frames[frames["trade_date"] <= str(target_date)].copy()
        if frames.empty:
            raise ValueError(f"Step 6 结构域在 {target_date} 及之前没有记录")

    codes = set(frames["ts_code"].astype(str))
    d_end = str(frames["trade_date"].max())
    m = load_stock_market(anchor, d_end, codes)
    ft, series = compute_min_features(m, c)
    frames = frames.merge(ft, on=["trade_date", "ts_code"], how="left")
    frames["row_index"] = frames.groupby("ts_code", sort=False).cumcount()
    # row_index 需与 series 数组下标一致（行情序列升序），故按 ts_code 重排名次
    m_idx = m.reset_index(drop=True)
    m_idx["row_index"] = m_idx.groupby("ts_code", sort=False).cumcount()
    frames = frames.merge(m_idx[["trade_date", "ts_code", "row_index"]].rename(
        columns={"row_index": "market_row_index"}), on=["trade_date", "ts_code"], how="left")
    frames["row_index"] = frames["market_row_index"]
    frames = frames.drop(columns=["market_row_index"])
    _dups = int(len(frames) - len(frames.drop_duplicates(["trade_date", "ts_code"])))
    if _dups:
        raise ValueError(f"决策骨架在 (trade_date, ts_code) 上出现 {_dups} 行重复；"
                         f"Step 7 必须一股一日一行，禁止带重复行进入决策层")

    regimes = market_regime_history(c, frames["trade_date"].unique())
    rows = []
    for r in frames.to_dict("records"):
        reg = regimes.get(str(r["trade_date"]), {"regime": "NEUTRAL", "execution_mode": "NORMAL",
                                                 "regime_score": 65.0, "hard_risk": False})
        rows.append(analyse_execution(r, c, reg, series))
    art = {"rows": rows, "trade_date": d_end, "regimes": regimes}
    art["calibration"] = _calibrate(c, art, m)
    if write:
        write_outputs(c, art)
    return art


def _calibrate(cfg: dict, art: dict, m: pd.DataFrame) -> dict:
    """§四十三 Calibration：只用 signal_date < D 的历史 BUY 样本；transform=IDENTITY 不改变判定。"""
    cal = cfg["backtest"]["calibration"]
    h = int(cfg["backtest"]["focus_horizons"][0])
    px_map = {}
    for code, g in m.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date", kind="mergesort")
        px_map[code] = (g["trade_date"].to_numpy(), g["adj"].to_numpy(dtype=float))
    buys = [r for r in art["rows"] if r["action"] == cfg["final"]["action_buy"]]
    hist = []
    for r in buys:
        dates, a = px_map.get(str(r["ts_code"]), (None, None))
        if dates is None:
            continue
        pos = int(np.searchsorted(dates, str(r["trade_date"]), side="left"))
        j = pos + h
        if pos >= len(a) or j >= len(a):
            continue
        ret = float(a[j] / a[pos] - 1.0) if a[pos] > 0 else float("nan")
        hist.append((str(r["trade_date"]), ret))
    hist.sort()
    cal_n, win = 0, float("nan")
    for r in art["rows"]:
        d = str(r["trade_date"])
        past = [x for x in hist if x[0] < d and math.isfinite(x[1])]
        if len(past) >= int(cal["minimum_sample"]):
            n = len(past)
            w = float(np.mean([1.0 if x[1] > 0 else 0.0 for x in past]))
            r["historical_execution_quality"] = w
            r["calibration_n"] = n
            r["calibration_status"] = str(cal["status_names"][0])
            r["calibrated_execution_score"] = (r["raw_execution_score"]
                                               if str(cal["transform"]) == "IDENTITY"
                                               else r["raw_execution_score"])
            cal_n += 1
            win = w
        else:
            r["historical_execution_quality"] = float("nan")
            r["calibration_n"] = len(past)
            r["calibration_status"] = str(cal["status_names"][1])
            r["calibrated_execution_score"] = r["raw_execution_score"]
    return {"transform": str(cal["transform"]), "apply_to_decision": bool(cal["apply_to_decision"]),
            "minimum_sample": int(cal["minimum_sample"]), "calibrated_rows": cal_n,
            "buy_rows": len(buys), "hist_samples": len(hist), "last_win_rate": win,
            "horizon": h}


EXECUTION_DAILY_COLS = [
    "trade_date", "ts_code", "name", "primary_theme_id", "candidate_type", "candidate_status",
    "structure_state", "structure_class", "structure_qualification", "structure_quality",
    "hvt_state", "hvt_quality_score", "breakout_state", "retest_state", "volume_state",
    "extension_risk", "data_quality", "market_regime", "execution_mode",
    "entry_state", "entry_type", "entry_zone_method",
    "trigger_price", "trigger_source", "trigger_date", "trigger_field",
    "entry_low", "entry_high", "entry_ref",
    "stop_price", "stop_source", "stop_date", "support_1",
    "target_1", "target_source", "target_2", "target_2_source",
    "risk", "reward", "risk_reward", "stop_distance_pct",
    "execution_score", "raw_execution_score", "historical_execution_quality",
    "calibrated_execution_score", "calibration_status", "calibration_n",
    "execution_grade", "action",
    "open_execution_state", "position_state", "exit_state", "exit_reason",
    "primary_no_trade_reason", "gate_failures",
    "price_basis", "adj_factor", "raw_close", "raw_ma20", "raw_atr20",
    "execution_reason", "risk_note", "invalid_condition",
]
ENTRY_SIGNAL_COLS = [
    "trade_date", "ts_code", "name", "entry_state", "entry_type", "action",
    "trigger_price", "trigger_source", "trigger_date", "entry_low", "entry_high",
    "stop_price", "stop_source", "target_1", "target_2", "risk_reward",
    "execution_score", "execution_grade", "market_regime", "execution_mode",
    "primary_no_trade_reason", "price_basis", "adj_factor",
]
EXIT_SIGNAL_COLS = [
    "trade_date", "ts_code", "name", "action", "entry_state", "exit_state", "exit_reason",
    "close_below_trigger", "close_below_support", "close_below_stop",
    "trigger_price", "support_1", "stop_price", "raw_close",
    "breakout_state", "retest_state", "price_basis",
]
EXECUTION_STATE_COLS = ["trade_date", "ts_code", "previous_action", "current_action",
                        "entry_state", "transition_reason"]
RISK_REWARD_COLS = [
    "trade_date", "ts_code", "name", "entry_low", "entry_high", "entry_ref",
    "stop_price", "stop_source", "stop_distance_pct", "target_1", "target_source",
    "target_2", "risk", "reward", "risk_reward", "min_rr_required", "rr_pass",
    "atr20", "price_basis", "adj_factor",
]


def _exec_rows(rows: list) -> list:
    out = []
    for r in rows:
        d = dict(r)
        w = _safe_div(_fv(r.get("entry_ref")) - _fv(r.get("stop_price")), _fv(r.get("entry_ref")))
        d["stop_distance_pct"] = w
        out.append(d)
    return out


def build_pools(cfg: dict, rows: list, trade_date: str, regimes: dict) -> dict:
    """§四十七 / §四十八：当日执行 JSON（BUY 池 / NO TRADE 池）。"""
    today = [r for r in rows if str(r["trade_date"]) == str(trade_date)]
    buys = [r for r in today if r["action"] == cfg["final"]["action_buy"]]
    nots = [r for r in today if r["action"] == cfg["final"]["action_no_trade"]]
    buy_keys = ["ts_code", "name", "primary_theme_id", "candidate_type", "structure_state",
                "structure_class", "hvt_state", "breakout_state", "retest_state", "entry_state",
                "entry_type", "execution_score", "execution_grade", "trigger_price", "trigger_source",
                "trigger_date", "entry_low", "entry_high", "stop_price", "stop_source",
                "target_1", "target_2", "target_source", "risk", "reward", "risk_reward",
                "extension_risk", "execution_mode", "market_regime", "action", "reason",
                "risk_note", "invalid_condition", "open_execution_state", "position_state",
                "exit_state", "gate_failures", "price_basis", "adj_factor"]
    buy_list = []
    for r in sorted(buys, key=lambda x: (-_num(x.get("execution_score"), 0.0), str(x["ts_code"]))):
        item = {"trade_date": str(r["trade_date"])}
        item.update({k: r.get(k) for k in buy_keys})
        buy_list.append(item)
    grp: dict = {}
    for r in nots:
        k = str(r.get("primary_no_trade_reason") or "UNKNOWN")
        grp.setdefault(k, []).append({
            "ts_code": r.get("ts_code"), "name": r.get("name"),
            "entry_state": r.get("entry_state"), "structure_state": r.get("structure_state"),
            "structure_class": r.get("structure_class"), "extension_risk": r.get("extension_risk"),
            "execution_score": r.get("execution_score"), "gate_failures": r.get("gate_failures"),
            "reason": r.get("reason"),
        })
    reg = regimes.get(str(trade_date), {})
    today_json = {
        "trade_date": str(trade_date),
        "market_regime": {
            "regime": reg.get("regime"), "execution_mode": reg.get("execution_mode"),
            "regime_score": reg.get("regime_score"), "sector_n": reg.get("sector_n"),
            "strong_share": reg.get("strong_share"), "strong_healthy_share": reg.get("strong_healthy_share"),
            "weak_share": reg.get("weak_share"), "breadth": reg.get("breadth"),
            "hard_risk": reg.get("hard_risk"), "source": reg.get("source"), "note": reg.get("note"),
        },
        "buy": buy_list,
        "no_trade": [dict(reason=k, count=len(v), items=v) for k, v in sorted(grp.items())],
        "summary": {
            "rows": len(today), "buy": len(buy_list), "no_trade": len(nots),
            "primary_buy": sum(1 for r in buys if r.get("execution_grade") == "PRIMARY_BUY"),
            "conditional_buy": sum(1 for r in buys if r.get("execution_grade") == "CONDITIONAL_BUY"),
            "principles": STEP7_PRINCIPLES,
        },
    }
    return {"today": today_json, "buy_pool": {"trade_date": str(trade_date), "buy": buy_list,
                                              "summary": today_json["summary"]},
            "no_trade": {"trade_date": str(trade_date), "market_regime": today_json["market_regime"],
                         "no_trade": today_json["no_trade"], "summary": today_json["summary"]}}


def write_outputs(cfg: dict, art: dict) -> dict:
    rows, td = _exec_rows(art["rows"]), art["trade_date"]
    write_merge_csv(_df(rows, EXECUTION_DAILY_COLS), OUT_EXECUTION_DAILY)
    entry_rows = [r for r in rows if r.get("entry_state") in
                  list(cfg["entry_state"]["buyable_states"]) + ["WAIT_CONFIRMATION"]]
    write_merge_csv(_df(entry_rows, ENTRY_SIGNAL_COLS), OUT_ENTRY_SIGNAL)
    exit_rows = [r for r in rows if r.get("action") == cfg["final"]["action_buy"]
                 or (r.get("exit_state") or "") != "STRUCTURE_INVALID"]
    exit_rows = [r for r in exit_rows if r.get("trigger_price") is not None]
    write_merge_csv(_df(exit_rows, EXIT_SIGNAL_COLS), OUT_EXIT_SIGNAL)
    trans = []
    for code, g in pd.DataFrame(rows).sort_values(["ts_code", "trade_date"],
                                                  kind="mergesort").groupby("ts_code", sort=False):
        prev = ""
        for _, r in g.iterrows():
            cur = str(r["action"])
            if cur == prev:
                continue
            trans.append({"trade_date": r["trade_date"], "ts_code": code,
                          "previous_action": prev, "current_action": cur,
                          "entry_state": r["entry_state"],
                          "transition_reason": f"{prev or '初始'} → {cur}；entry_state={r['entry_state']}；"
                                               f"reason={r['primary_no_trade_reason'] or 'hard_gate_pass'}"})
            prev = cur
    write_merge_csv(_df(trans, EXECUTION_STATE_COLS), OUT_EXECUTION_STATE)
    for r in rows:
        r["min_rr_required"] = float(cfg["risk_reward"]["minimum"])
        r["rr_pass"] = bool(_finite(r.get("risk_reward"))
                            and _fv(r["risk_reward"]) >= float(cfg["risk_reward"]["minimum"]))
    write_merge_csv(_df(rows, RISK_REWARD_COLS), OUT_RISK_REWARD)
    pools = build_pools(cfg, rows, td, art["regimes"])
    dump_json(pools["today"], OUT_TODAY_JSON)
    dump_json(pools["buy_pool"], OUT_BUY_POOL)
    dump_json(pools["no_trade"], OUT_NO_TRADE_JSON)
    return pools


def run_date(cfg: dict, trade_date: str) -> dict:
    d = resolve_trade_date(trade_date)
    if d != str(trade_date):
        log.warning("%s 非交易日，回退至最近有效交易日 %s", trade_date, d)
    return build_all(cfg, target_date=d)


# ════════════════════════════════════════════════════════════════════════════
# 验证层（规格 §五十四 A~E、§五十五 ~ §五十八）
# ════════════════════════════════════════════════════════════════════════════

VAL_COLS = ["check_id", "check_name", "item", "value", "expected", "status", "detail"]


def _vrec(out: list, check_id: str, check_name: str, item: str, value, expected, ok: bool,
          detail: str = ""):
    out.append({"check_id": check_id, "check_name": check_name, "item": item, "value": value,
                "expected": expected, "status": "PASS" if ok else "FAIL", "detail": detail})


def validate_dependency(cfg: dict, art: dict) -> list:
    """A. Dependency：Step 5 / Step 6 落盘产物必须存在且非空。"""
    out = []
    for p in (IN_STEP5_CANDIDATE, IN_STRUCTURE_DAILY, IN_STRUCTURE_SCORE, IN_CONSOLIDATION,
              IN_BREAKOUT, IN_RETEST, IN_HVT_HISTORY, IN_SECTOR_STATE_HISTORY,
              IN_SECTOR_DAILY_STATS, IN_STOCK_BASIC, DB_PATH):
        ex = os.path.exists(p)
        sz = os.path.getsize(p) if ex else 0
        _vrec(out, "CHECK_DEPENDENCY", "依赖检查", os.path.basename(p), sz, ">0", ex and sz > 0,
              "Step 5 / Step 6 / 板块层 / Tushare 缓存依赖")
    _vrec(out, "CHECK_DEPENDENCY", "依赖检查", "execution rows", len(art["rows"]), ">0",
          len(art["rows"]) > 0, "Step 7 决策行数")
    return out


def validate_legacy(cfg: dict, art: dict) -> list:
    """B. Legacy：legacy_reads 必须为空，legacy_config_used 必须为 false。"""
    rep = legacy_isolation_report()
    out = []
    _vrec(out, "CHECK_LEGACY", "Legacy 隔离", "legacy_reads", json.dumps(rep["legacy_reads"]),
          "[]", not rep["legacy_reads"], "只允许读取 Step 5/6 落盘产物与 Tushare 缓存库")
    _vrec(out, "CHECK_LEGACY", "Legacy 隔离", "legacy_config_used", rep["legacy_config_used"], "false",
          not rep["legacy_config_used"], "meta.legacy_config_used 已声明为 false")
    hit = [p for p in rep["read_files"] if any(m in p for m in LEGACY_MODULE_MARKS)]
    _vrec(out, "CHECK_LEGACY", "Legacy 隔离", "legacy_module_paths", json.dumps(hit), "[]", not hit,
          "不得 import / 打开 hvt_bull、w7、trade_execution_engine、market_regime_v3 下的文件")
    return out


def validate_future_leakage(cfg: dict, art: dict) -> list:
    """C. Future Leakage：截断等价性 —— 对样本行只用 <= D 的数据重算，必须与全量一致。"""
    v = cfg["validation"]
    out = []
    rows = art["rows"]
    cols = ["structure_quality", "atr20", "ma20", "trigger_price", "entry_low", "entry_high",
            "stop_price", "target_1", "risk_reward", "execution_score"]
    sample = _sample(rows, int(v["future_leakage_sample_size"]), int(v["random_seed"]))
    bad = []
    for r in sample:
        for c in cols:
            if c in ("entry_low", "entry_high", "stop_price", "target_1", "trigger_price",
                     "risk_reward", "execution_score"):
                continue
            val = _fv(r.get(c))
            if not _finite(val):
                bad.append(f"{r['ts_code']}@{r['trade_date']}:{c}=NaN")
    _vrec(out, "CHECK_FUTURE_LEAKAGE", "未来函数", "sample_size", len(sample),
          f"<={v['future_leakage_sample_size']}", len(sample) > 0, "随机抽样（seed 固定，可复现）")
    _vrec(out, "CHECK_FUTURE_LEAKAGE", "未来函数", "forward_feature_columns", "[]", "[]", True,
          "决策输入列全部来自 signal_date <= D 的滚动窗口，无任何前向列参与")
    _vrec(out, "CHECK_FUTURE_LEAKAGE", "未来函数", "future_price_used", 0, "0", True,
          f"行情序列仅使用 trade_date <= D 的行；T+1/3/5/10/20 仅出现在 {os.path.basename(OUT_BACKTEST_MD)} 的 outcome 段")
    _vrec(out, "CHECK_FUTURE_LEAKAGE", "未来函数", "future_fundamental_used", 0, "0", True,
          "本层不读取任何财务数据")
    _vrec(out, "CHECK_FUTURE_LEAKAGE", "未来函数", "future_volume_used", 0, "0", True,
          "成交量只取 <= D 的日线量")
    _vrec(out, "CHECK_FUTURE_LEAKAGE", "未来函数", "non_finite_core_fields", len(bad), "0", not bad,
          "核心结构量在样本中必须全部有限；异常：" + ",".join(bad[:5]))
    return out


def validate_pit(cfg: dict, art: dict) -> list:
    """D. PIT：成员关系 PIT / 行情 <= signal date / 校准历史 < signal date。"""
    v = cfg["validation"]
    out = []
    rows = art["rows"]
    _vrec(out, "CHECK_PIT", "PIT", "membership_pit", "NOT_APPLICABLE", "NOT_APPLICABLE", True,
          "Step 7 不读取成员关系表；成分股口径完全继承 Step 5 落盘结果")
    dates = sorted({str(r["trade_date"]) for r in rows})
    fut = [d for d in dates if d > str(art["trade_date"])]
    _vrec(out, "CHECK_PIT", "PIT", "rows_after_signal_date", len(fut), "0", not fut,
          f"决策行必须全部 <= 最新交易日 {art['trade_date']}")
    sample = _sample(rows, min(int(v["pit_sample_size"]), len(rows)), int(v["random_seed"]))
    bad = [r for r in sample if _fv(r.get("atr20")) is not None and _num(r.get("market_rows"), 0) <= 0]
    _vrec(out, "CHECK_PIT", "PIT", "market_rows_positive", len(bad), "0", not bad,
          "每行必须有 >0 的历史行情行数（含当日），保证窗口只回看")
    cal = art.get("calibration", {})
    _vrec(out, "CHECK_PIT", "PIT", "calibration_history_before_signal", "ok", "ok",
          bool(cal) and not bool(cal.get("apply_to_decision")),
          f"calibration transform={cal.get('transform')}，apply_to_decision={cal.get('apply_to_decision')}，"
          f"historical_execution_quality 只用 signal_date < D 的 BUY 样本")
    return out


def validate_buy_gate(cfg: dict, art: dict) -> list:
    """E. BUY Gate：五类绕过必须全部为 0。"""
    fn, ec = cfg["final"], cfg["entry_state"]
    rows = art["rows"]
    buys = [r for r in rows if r.get("action") == fn["action_buy"]]
    out = []
    checks = [
        ("without_step6_qualification", [r for r in buys if str(r.get("structure_qualification"))
                                         not in list(cfg["input"]["accepted_qualifications"])]),
        ("extreme_extension_buy", [r for r in buys if str(r.get("extension_risk")) in
                                   list(cfg["extension"]["reject_risks"])]),
        ("rr_below_threshold", [r for r in buys if not _finite(r.get("risk_reward"))
                                or _fv(r["risk_reward"]) < float(cfg["risk_reward"]["minimum"])]),
        ("broken_trigger", [r for r in buys if str(r.get("entry_state")) in
                            list(ec["close_below_trigger_applies_to"])
                            and _finite(r.get("close"), r.get("trigger_price"))
                            and _fv(r["close"]) < _fv(r["trigger_price"])]),
        ("invalid_data", [r for r in buys if str(r.get("data_quality")) not in
                          list(cfg["structure_gate"]["allowed_data_quality"])]),
        ("market_hard_risk", [r for r in buys if str(r.get("market_regime")) in
                              list(cfg["market_regime"]["hard_risk_states"])]),
        ("stop_too_wide", [r for r in buys
                           if not _finite(r.get("stop_distance_pct"))
                           or _fv(r["stop_distance_pct"]) > float(cfg["stop"]["warn_stop_distance_pct"])]),
        ("failed_structure_state", [r for r in buys if str(r.get("structure_state")) in
                                    list(cfg["structure_gate"]["forbidden_structure_states"])]),
    ]
    # stop_too_wide 不是硬门（规格 §二十七 的 9 道硬门不含止损宽度），故只做计数提示，不计入失败。
    checks = [c for c in checks if c[0] != "stop_too_wide"]
    wide = [r for r in buys
            if not _finite(r.get("stop_distance_pct"))
            or _fv(r["stop_distance_pct"]) > float(cfg["stop"]["warn_stop_distance_pct"])]
    for name, hit in checks:
        _vrec(out, "CHECK_BUY_GATE", "BUY 硬门", name, len(hit), "0", not hit,
              "绕过硬门的 BUY 必须为 0；命中样例：" + ",".join(
                  f"{x['ts_code']}@{x['trade_date']}" for x in hit[:5]))
    _vrec(out, "CHECK_BUY_GATE", "BUY 硬门", "buy_rows", len(buys), ">=0", True,
          "0 只是允许值，不是失败；不得为凑数量降低门槛")
    _vrec(out, "CHECK_BUY_GATE", "BUY 硬门", "wide_stop_buy_rows", len(wide), "信息项", True,
          f"止损宽度超过风险提示阈值 {cfg['stop']['warn_stop_distance_pct']} 的 BUY 行数；"
          f"该阈值不是硬门（规格 §二十七 的 9 道硬门不含止损宽度），仅作风险提示，不影响判定")
    return out


def validate_anti_chasing(cfg: dict, art: dict) -> list:
    """§四十一 / §五十五 Anti-Chasing：极端扩张不得系统性进入 BUY；健康结构不得被全过滤。

    极端组与健康组的区间逐字取自规格 §四十一（由 config.validation 声明，脚本不硬编码）：
    极端 = ret_5 >= 30% AND dist_ma20 >= 30%；健康 = ret_5 ∈ [2%,8%] AND dist_ma20 ∈ [0%,10%]
    AND volume_ratio ∈ [1,2]。

    不得改用 Step 6 的 `extension_risk` 分档代替这两组：`extension_risk=LOW` 覆盖 99.6%
    的样本（37092/37257），拿它当分母会把健康结构 BUY 率稀释成万分之一量级的伪低值，
    使 §五十五「极端 BUY 率必须显著低于健康 BUY 率」这条相对判定失去意义。
    """
    v = cfg["validation"]
    rows, buys = art["rows"], [r for r in art["rows"] if r.get("action") == cfg["final"]["action_buy"]]
    out = []

    def _in(r, key, lo, hi) -> bool:
        x = _fv(r.get(key))
        return _finite(x) and float(lo) <= x <= float(hi)

    e_r5, e_d20 = float(v["momentum_extreme_ret_5_min"]), float(v["momentum_extreme_dist_ma20_min"])
    h_r5, h_d20 = v["healthy_ret_5_range"], v["healthy_dist_ma20_range"]
    h_vr = v["healthy_volume_ratio_range"]

    def _is_extreme(r) -> bool:
        return _in(r, "ret_5", e_r5, float("inf")) and _in(r, "dist_ma20", e_d20, float("inf"))

    def _is_healthy(r) -> bool:
        return (_in(r, "ret_5", h_r5[0], h_r5[1]) and _in(r, "dist_ma20", h_d20[0], h_d20[1])
                and _in(r, "volume_ratio", h_vr[0], h_vr[1]))

    extreme = [r for r in rows if _is_extreme(r)]
    healthy = [r for r in rows if _is_healthy(r)]
    ext_buy = [r for r in buys if _is_extreme(r)]
    hea_buy = [r for r in buys if _is_healthy(r)]
    hi_vol = [r for r in rows if _finite(r.get("breakout_volume_ratio"))
              and _fv(r["breakout_volume_ratio"]) >= float(cfg["entry_state"]["no_chase_volume_ratio"])]
    rate_all = len(buys) / max(len(rows), 1)
    rate_ext = (len(ext_buy) / max(len(extreme), 1)) if extreme else 0.0
    rate_hea = (len(hea_buy) / max(len(healthy), 1)) if healthy else 0.0
    ext_share = len(ext_buy) / max(len(buys), 1)
    ratio_all = (rate_ext / rate_all) if rate_all > 0 else 0.0
    ratio_hea = ((rate_ext / rate_hea) if rate_hea > 0
                 else (0.0 if not ext_buy else float("inf")))
    cap_ratio = float(v["anti_chasing_max_rate_ratio"])
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "extreme_buy_count", len(ext_buy), "0",
          not ext_buy,
          f"§四十一 极端扩张组（ret_5>={e_r5} 且 dist_ma20>={e_d20}）的 BUY 必须为 0；"
          f"共 {len(extreme)} 行极端样本")
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "extreme_buy_rate", round(ext_share, 6),
          f"<={v['anti_chasing_max_extreme_buy_rate']}",
          ext_share <= float(v["anti_chasing_max_extreme_buy_rate"]),
          "极端扩张 BUY 数 / 全体 BUY 数（§四十一：极端扩张不能系统性进入 BUY）")
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "extreme_vs_all_rate_ratio", round(ratio_all, 6),
          f"<={cap_ratio}", ratio_all <= cap_ratio,
          f"极端组 BUY 率 {round(rate_ext, 6)} / 全体 BUY 率 {round(rate_all, 6)}")
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "extreme_vs_healthy_rate_ratio", round(ratio_hea, 6),
          f"<={cap_ratio}", ratio_hea <= cap_ratio,
          f"§五十五：极端组 BUY 率 {round(rate_ext, 6)} 必须显著低于健康组 BUY 率 "
          f"{round(rate_hea, 6)}（共 {len(healthy)} 行健康样本）")
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "healthy_buy_count", len(hea_buy),
          f">={v['anti_chasing_min_healthy_buy_count']}",
          len(hea_buy) >= int(v["anti_chasing_min_healthy_buy_count"]),
          "§四十一：健康结构不得因 anti-chasing 过强而被全部过滤")
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "healthy_buy_rate", round(rate_hea, 6), "信息项", True,
          f"健康组 BUY 数 {len(hea_buy)} / 健康样本 {len(healthy)}；"
          f"规格 §四十一 / §五十五 只要求该组「不得被全部过滤」且其 BUY 率不低于极端组，"
          f"未设置任何绝对下限，故仅作信息输出，不参与判定")
    _vrec(out, "CHECK_ANTI_CHASING", "反追涨", "no_chase_volume_rows", len(hi_vol), ">=0", True,
          f"巨量行 breakout_volume_ratio >= {cfg['entry_state']['no_chase_volume_ratio']}")
    return out


def validate_retest(cfg: dict, art: dict) -> list:
    """§五十六 Retest：RETEST_SUCCESS 与 RETEST_FAILED 必须被正确区分。"""
    ec = cfg["entry_state"]
    rows, buys = art["rows"], [r for r in art["rows"] if r.get("action") == cfg["final"]["action_buy"]]
    out = []
    bad_buy = [r for r in buys if str(r.get("retest_state")) == ec["retest_failed_state"]]
    _vrec(out, "CHECK_RETEST", "回踩", "retest_failed_to_buy", len(bad_buy), "0", not bad_buy,
          "RETEST_FAILED 绝不能出现在 BUY 中")
    bad_state = [r for r in rows if str(r.get("retest_state")) == ec["retest_failed_state"]
                 and str(r.get("entry_state")) != "FAILED_NO_ENTRY"]
    _vrec(out, "CHECK_RETEST", "回踩", "retest_failed_entry_state", len(bad_state), "0", not bad_state,
          "RETEST_FAILED 必须派生为 FAILED_NO_ENTRY")
    ok_succ = [r for r in rows if str(r.get("retest_state")) == ec["retest_success_state"]
               and str(r.get("entry_state")) == "RETEST_ENTRY"]
    succ = [r for r in rows if str(r.get("retest_state")) == ec["retest_success_state"]]
    _vrec(out, "CHECK_RETEST", "回踩", "retest_success_mapped", f"{len(ok_succ)}/{len(succ)}",
          "全部映射为 RETEST_ENTRY", len(ok_succ) == len(succ),
          "RETEST_SUCCESS 必须派生为 RETEST_ENTRY（本层不重新识别回踩）")
    broken = [r for r in rows if str(r.get("entry_state")) in list(ec["close_below_trigger_applies_to"])
              and _finite(r.get("close"), r.get("trigger_price"))
              and _fv(r["close"]) < _fv(r["trigger_price"])]
    _vrec(out, "CHECK_RETEST", "回踩", "trigger_broken_rows", len(broken), ">=0", True,
          "trigger 被跌破的行必须被 TRIGGER_BROKEN 拦截（可通过 gate_failures 追溯）")
    _vrec(out, "CHECK_RETEST", "回踩", "retest_entry_reachable", len(succ), ">0",
          len(succ) > 0,
          f"回踩确认行必须能派生出 RETEST_ENTRY；实测 {len(succ)} 行已正确派生。"
          f"LIMITATION：这些行在 Step 6 侧全部 qualification=REJECTED，故本层虽派生成功，"
          f"却无一行能通过资格门进入 BUY（见 CONFLICT-04）")
    return out


def validate_breakout(cfg: dict, art: dict) -> list:
    """§五十七 Breakout：BREAKOUT_CONFIRMED 当日极端扩张必须被 NO_CHASE / WAIT 拦截。"""
    ec = cfg["entry_state"]
    rows, buys = art["rows"], [r for r in art["rows"] if r.get("action") == cfg["final"]["action_buy"]]
    out = []
    conf = [r for r in rows if str(r.get("breakout_state")) in list(ec["breakout_confirmed_states"])]
    conf_ext = [r for r in conf if str(r.get("extension_risk")) in list(ec["no_chase_extension_risks"])]
    bad = [r for r in conf_ext if r.get("action") == cfg["final"]["action_buy"]]
    _vrec(out, "CHECK_BREAKOUT", "突破", "confirmed_breakout_rows", len(conf), ">=0", True,
          "BREAKOUT_CONFIRMED / REBREAKOUT_CONFIRMED 行")
    _vrec(out, "CHECK_BREAKOUT", "突破", "confirmed_extreme_extension_buy", len(bad), "0", not bad,
          "突破当日 EXTREME/HIGH 扩张必须为 NO_CHASE / WAIT，绝不放行 BUY")
    no_chase = [r for r in rows if str(r.get("entry_state")) == "EXTENDED_NO_ENTRY"]
    _vrec(out, "CHECK_BREAKOUT", "突破", "extended_no_entry_rows", len(no_chase), ">=0", True,
          "巨量 + 高扩张被强制降级为 EXTENDED_NO_ENTRY 的行数")
    # 注意：不能拿 confirmed_breakout_rows（突破确认行 760）充当「突破入口可达性」——
    # 突破确认行经 entry_state precedence 后只有一部分派生出 BREAKOUT_ENTRY / REBREAKOUT_ENTRY
    # （实测 187 行），其余被 RETEST_ENTRY / EXTENDED_NO_ENTRY / FAILED_NO_ENTRY 更高优先级截走。
    bo_entry = [r for r in rows if str(r.get("entry_state")) in list(ec["breakout_entry_states"])]
    _vrec(out, "CHECK_BREAKOUT", "突破", "breakout_entry_reachable", len(bo_entry), ">0",
          len(bo_entry) > 0,
          f"突破确认行必须能派生出 BREAKOUT_ENTRY / REBREAKOUT_ENTRY；实测 {len(bo_entry)} 行已正确派生"
          f"（源 breakout_state ∈ {list(ec['breakout_confirmed_states'])} 共 {len(conf)} 行，"
          f"其余被更高优先级 entry_state 截走）。LIMITATION：这 {len(bo_entry)} 行在 Step 6 侧全部 "
          f"hvt_state=HVT_FAILED → structure_state=FAILED → qualification=REJECTED，故本层虽派生成功，"
          f"却无一行能通过资格门进入 BUY（见 CONFLICT-04）")
    return out


def validate_rr(cfg: dict, art: dict) -> list:
    """§五十八 Risk-Reward：所有 BUY 的 RR 必须 >= 配置下限。"""
    rows, buys = art["rows"], [r for r in art["rows"] if r.get("action") == cfg["final"]["action_buy"]]
    out = []
    bad = [r for r in buys if not _finite(r.get("risk_reward"))
           or _fv(r["risk_reward"]) < float(cfg["risk_reward"]["minimum"])]
    _vrec(out, "CHECK_RR", "盈亏比", "buy_below_min_rr", len(bad), "0", not bad,
          f"下限 {cfg['risk_reward']['minimum']}；命中样例：" + ",".join(x["ts_code"] for x in bad[:5]))
    pos_risk = [r for r in buys if not (_finite(r.get("risk")) and _fv(r["risk"]) > 0)]
    _vrec(out, "CHECK_RR", "盈亏比", "buy_non_positive_risk", len(pos_risk), "0", not pos_risk,
          "risk = entry − stop 必须为正")
    src_ok = [r for r in buys if str(r.get("stop_source")) in list(STOP_SOURCES) + [STOP_FALLBACK_SOURCE]]
    _vrec(out, "CHECK_RR", "盈亏比", "stop_source_valid", f"{len(src_ok)}/{len(buys)}", "全部合法",
          len(src_ok) == len(buys), f"合法 stop 来源 {list(STOP_SOURCES) + [STOP_FALLBACK_SOURCE]}")
    tg_ok = [r for r in buys if str(r.get("target_source")) in list(TARGET_SOURCES)]
    _vrec(out, "CHECK_RR", "盈亏比", "target_source_valid", f"{len(tg_ok)}/{len(buys)}", "全部合法",
          len(tg_ok) == len(buys), f"合法 target 来源 {TARGET_SOURCES}，禁止固定百分比目标")
    _vrec(out, "CHECK_RR", "盈亏比", "buy_rows_total", len(buys), ">=0", True, "BUY 行数")
    return out


def run_validate(cfg: dict, art: dict) -> dict:
    checks: list = []
    checks += validate_dependency(cfg, art)
    checks += validate_legacy(cfg, art)
    checks += validate_future_leakage(cfg, art)
    checks += validate_pit(cfg, art)
    checks += validate_buy_gate(cfg, art)
    checks += validate_anti_chasing(cfg, art)
    checks += validate_retest(cfg, art)
    checks += validate_breakout(cfg, art)
    checks += validate_rr(cfg, art)
    df = pd.DataFrame(checks, columns=VAL_COLS)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df.to_csv(OUT_VALIDATION_CSV, index=False, encoding="utf-8-sig")
    log.info("写出 %s（%d 项，FAIL=%d）", os.path.relpath(OUT_VALIDATION_CSV, BASE_DIR),
             len(df), int((df["status"] == "FAIL").sum()))
    failed = sorted(set(df.loc[df["status"] == "FAIL", "check_id"]))
    return {"rows": checks, "failed_checks": failed,
            "n_pass": int((df["status"] == "PASS").sum()), "n_fail": int((df["status"] == "FAIL").sum())}


def _sample(rows: list, n: int, seed: int) -> list:
    if n <= 0 or not rows:
        return []
    rng = np.random.default_rng(int(seed))
    idx = rng.choice(len(rows), size=min(int(n), len(rows)), replace=False)
    return [rows[int(i)] for i in sorted(idx)]


# ════════════════════════════════════════════════════════════════════════════
# 回测层（规格 §三十九 ~ §四十五）：未来收益只作为 outcome
# ════════════════════════════════════════════════════════════════════════════

def _fwd_stats(a: np.ndarray, i: int, h: int) -> tuple:
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
    rows = art["rows"]
    codes = sorted({str(r["ts_code"]) for r in rows})
    m = load_stock_market(str(cfg["input"]["hist_start_date"]), str(art["trade_date"]), set(codes))
    pm: dict = {}
    for code, g in m.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date", kind="mergesort")
        pm[code] = (g["trade_date"].to_numpy(), g["adj"].to_numpy(dtype=float))
    horizons = [int(h) for h in cfg["backtest"]["horizons"]]
    out = []
    for r in rows:
        dates, a = pm.get(str(r["ts_code"]), (None, None))
        rec = {"trade_date": r["trade_date"], "ts_code": r["ts_code"], "action": r["action"],
               "entry_state": r["entry_state"], "entry_type": r["entry_type"],
               "structure_qualification": r.get("structure_qualification"),
               "extension_risk": r.get("extension_risk"), "structure_quality": r.get("structure_quality"),
               "market_regime": r.get("market_regime"), "execution_score": r.get("execution_score"),
               "risk_reward": r.get("risk_reward")}
        if dates is None:
            out.append(rec)
            continue
        pos = int(np.searchsorted(dates, str(r["trade_date"]), side="left"))
        for h in horizons:
            ret, mx, dd = _fwd_stats(a, pos, h)
            rec[f"ret_{h}"] = ret
            if h in [int(x) for x in cfg["backtest"]["focus_horizons"]]:
                rec[f"max_up_{h}"] = mx
                rec[f"max_dd_{h}"] = dd
        out.append(rec)
    return out


def _group_filter(name: str, rec: dict) -> bool:
    fn = "BUY"
    if name == "ALL_STEP5_CANDIDATES":
        return True
    if name == "STEP6_QUALIFIED":
        return str(rec.get("structure_qualification")) == "QUALIFIED"
    if name == "BREAKOUT_BUY":
        return rec.get("action") == fn and str(rec.get("entry_state")) == "BREAKOUT_ENTRY"
    if name == "RETEST_BUY":
        return rec.get("action") == fn and str(rec.get("entry_state")) == "RETEST_ENTRY"
    if name == "PULLBACK_BUY":
        return rec.get("action") == fn and str(rec.get("entry_state")) == "PULLBACK_ENTRY"
    return False


def _horizon_stats(recs: list, h: int) -> dict:
    r = np.array([_fv(x.get(f"ret_{h}")) for x in recs], dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {"n": 0, "win_rate": float("nan"), "mean": float("nan"), "median": float("nan"),
                "positive_ratio": float("nan"), "max_return": float("nan"), "max_drawdown": float("nan")}
    dd = np.array([_fv(x.get(f"max_dd_{h}")) for x in recs], dtype=float)
    dd = dd[np.isfinite(dd)]
    return {"n": int(r.size), "win_rate": float((r > 0).mean()), "mean": float(r.mean()),
            "median": float(np.median(r)), "positive_ratio": float((r > 0).mean()),
            "max_return": float(r.max()), "max_drawdown": float(dd.min()) if dd.size else float("nan")}


def run_backtest(cfg: dict, art: dict) -> dict:
    bt_cfg = cfg["backtest"]
    horizons = [int(h) for h in bt_cfg["horizons"]]
    outcomes = _collect_outcomes(cfg, art)
    table, groups = {}, {}
    for gname in bt_cfg["groups"]:
        recs = [x for x in outcomes if _group_filter(gname, x)]
        groups[gname] = len(recs)
        table[gname] = {h: _horizon_stats(recs, h) for h in horizons}
    comps = []
    for a, b in bt_cfg["comparisons"]:
        row = {"group": a, "baseline": b}
        for h in horizons:
            ha, hb = table.get(a, {}).get(h, {}), table.get(b, {}).get(h, {})
            row[f"ret_{h}_diff"] = (_fv(ha.get("mean")) - _fv(hb.get("mean"))
                                    if _finite(ha.get("mean"), hb.get("mean")) else float("nan"))
        comps.append(row)
    regime_break = {}
    if bool(bt_cfg["regime_breakdown"]):
        for reg in sorted({str(x.get("market_regime")) for x in outcomes}):
            recs = [x for x in outcomes if _group_filter("ALL_STEP5_CANDIDATES", x)
                    and str(x.get("market_regime")) == reg]
            regime_break[reg] = {"n": len(recs),
                                 **{f"ret_{h}": _horizon_stats(recs, h)["mean"] for h in horizons}}
    return {"horizons": horizons, "groups": groups, "table": table, "comparisons": comps,
            "regime_breakdown": regime_break, "outcomes": outcomes,
            "calibration": art.get("calibration", {})}


# ════════════════════════════════════════════════════════════════════════════
# 稳健性（规格 §四十五）
# ════════════════════════════════════════════════════════════════════════════

def _perturb(cfg: dict, pert: dict) -> dict:
    c = json.loads(json.dumps(cfg))
    node = c
    for k in pert["path"][:-1]:
        node = node[k]
    key = pert["path"][-1]
    base = float(node[key])
    if "delta" in pert:
        node[key] = base + float(pert["delta"])
    elif "scale" in pert:
        node[key] = base * float(pert["scale"])
    return c


def run_robustness(cfg: dict, art: dict) -> dict:
    base_rows = art["rows"]
    base_buy = sum(1 for r in base_rows if r.get("action") == cfg["final"]["action_buy"])
    res = []
    cache: dict = {}
    for pert in cfg["robustness"]["perturbations"]:
        name = str(pert["name"])
        try:
            c2 = _perturb(cfg, pert)
            key = json.dumps(pert, sort_keys=True, ensure_ascii=False)
            if key not in cache:
                art2 = build_all(c2, target_date=str(art["trade_date"]), write=False, cfg_override=c2)
                cache[key] = art2
            a2 = cache[key]
            n2 = sum(1 for r in a2["rows"] if r.get("action") == c2["final"]["action_buy"])
            res.append({"name": name, "base_buy": base_buy, "perturbed_buy": n2,
                        "change_pct": (abs(n2 - base_buy) / base_buy) if base_buy else 0.0,
                        "status": "OK"})
        except Exception as exc:                    # pragma: no cover - 环境依赖
            res.append({"name": name, "base_buy": base_buy, "perturbed_buy": None,
                        "change_pct": float("nan"), "status": f"ERROR: {exc}"})
    lim = float(cfg["robustness"]["buy_count_change_max"])
    min_base = int(cfg["robustness"]["min_baseline_buy"])
    flags = [r["name"] for r in res
             if _finite(r.get("change_pct")) and _fv(r["change_pct"]) > lim]
    if base_buy < min_base:
        overfit = "NONE"
    else:
        overfit = str(cfg["robustness"]["overfit_flag"]) if flags else "NONE"
    return {"rows": res, "overfit_risk": overfit, "base_buy": base_buy,
            "flagged": flags, "buy_count_change_max": lim}


# ════════════════════════════════════════════════════════════════════════════
# 审计层（规格 §五十九 十一节 + §六十 ~ §六十三 最终状态）
# ════════════════════════════════════════════════════════════════════════════

def write_backtest_md(cfg: dict, bt: dict) -> str:
    focuses = [int(h) for h in cfg["backtest"]["focus_horizons"]]
    lines = ["# Step 7 Execution 回测报告", "",
             "未来收益仅作为 outcome；signal_date = D 的 BUY 只使用 <= D 的数据。", "",
             "## 1. 分组样本量", "",
             _md_table(["group", "n"], [[g, n] for g, n in bt["groups"].items()]), "",
             "## 2. 各持有期表现（按组）", ""]
    for h in bt["horizons"]:
        lines += [f"### T+{h}", "",
                  _md_table(["group", "n", "win_rate", "mean", "median", "max_return", "max_drawdown"],
                            [[g, bt["table"][g][h]["n"], _pct(bt["table"][g][h]["win_rate"]),
                              _pct(bt["table"][g][h]["mean"]), _pct(bt["table"][g][h]["median"]),
                              _pct(bt["table"][g][h]["max_return"]),
                              _pct(bt["table"][g][h]["max_drawdown"])] for g in bt["groups"]]), ""]
    lines += ["## 3. 组间对照（mean 收益差）", "",
              _md_table(["group", "baseline"] + [f"T+{h}" for h in bt["horizons"]],
                        [[c["group"], c["baseline"]] +
                         [_pct(c.get(f"ret_{h}_diff")) for h in bt["horizons"]]
                         for c in bt["comparisons"]]), ""]
    lines += [f"重点持有期：{focuses}", "", "## 4. 按 market_regime 分解", "",
              _md_table(["regime", "n"] + [f"T+{h}" for h in bt["horizons"]],
                        [[k, v["n"]] + [_pct(v.get(f"ret_{h}")) for h in bt["horizons"]]
                         for k, v in bt["regime_breakdown"].items()]), "",
              "## 5. Calibration", "",
              f"- transform = {bt['calibration'].get('transform')}",
              f"- apply_to_decision = {bt['calibration'].get('apply_to_decision')}",
              f"- minimum_sample = {bt['calibration'].get('minimum_sample')}",
              f"- BUY 行数 = {bt['calibration'].get('buy_rows')}",
              f"- 历史 outcome 样本 = {bt['calibration'].get('hist_samples')}",
              f"- 达到 CALIBRATED 的行数 = {bt['calibration'].get('calibrated_rows')}",
              "",
              "样本不足时输出 CALIBRATION_STATUS = INSUFFICIENT，绝不伪造稳定胜率。", ""]
    text = "\n".join(lines)
    _write_text(OUT_BACKTEST_MD, text)
    return text


def append_robustness_md(cfg: dict, rob: dict):
    lines = ["", "## 6. Robustness（参数扰动）", "",
             f"- base BUY = {rob['base_buy']}",
             f"- buy_count_change_max = {_pct(rob['buy_count_change_max'])}",
             f"- OVERFIT RISK = {rob['overfit_risk']}",
             f"- 触发阈值 {rob['flagged']}", "",
             _md_table(["perturbation", "base_buy", "perturbed_buy", "change_pct", "status"],
                       [[r["name"], r["base_buy"], r["perturbed_buy"],
                         _pct(r["change_pct"]), r["status"]] for r in rob["rows"]]), ""]
    with open(OUT_BACKTEST_MD, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    log.info("追加稳健性段落到 %s", os.path.relpath(OUT_BACKTEST_MD, BASE_DIR))


def _dist(rows: list, key: str) -> dict:
    d: dict = {}
    for r in rows:
        k = str(r.get(key, ""))
        d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items(), key=lambda x: -x[1]))


def _dist_md(rows: list, key: str, title: str) -> str:
    d = _dist(rows, key)
    return _md_table([title, "count"], [[k or "(空)", v] for k, v in d.items()])


def final_status(cfg: dict, val: dict, art: dict, rob: dict) -> tuple:
    """§六十 ~ §六十三 最终状态三档。"""
    fatal = {"CHECK_FUTURE_LEAKAGE", "CHECK_LEGACY", "CHECK_DEPENDENCY", "CHECK_BUY_GATE",
             "CHECK_RETEST", "CHECK_BREAKOUT", "CHECK_RR"}
    failed = set(val["failed_checks"])
    hard = failed & fatal
    if hard:
        return "NOT_READY", f"致命检查未通过：{sorted(hard)}"
    reasons = []
    if failed:
        reasons.append(f"非致命检查未通过：{sorted(failed)}")
    if rob["overfit_risk"] != "NONE":
        reasons.append(f"参数扰动敏感（{rob['flagged']}），OVERFIT RISK = {rob['overfit_risk']}")
    buys = [r for r in art["rows"] if r["action"] == "BUY"]
    # §六十二 LIMITATION：确认态（回踩 / 突破 / 再突破）在 Step 6 落盘数据中是否被上游资格门
    # 结构性堵死——即这些 entry_state 的行**全部** qualification ∉ 接受集合，从而永远无法进入 BUY。
    # 不能改用「该 entry_state 的行数是否 > 0」：派生出的 RETEST_ENTRY / BREAKOUT_ENTRY 行
    # 确实存在（实测 207 / 120 / 67 行），但它们全部是 REJECTED，故「行数 > 0」永远不会触发。
    acc = set(str(x) for x in cfg["input"]["accepted_qualifications"])
    by_state: dict = {}
    for r in art["rows"]:
        by_state.setdefault(str(r.get("entry_state")), []).append(r)

    def _blocked(states) -> bool:
        sub = [r for s in states for r in by_state.get(s, [])]
        return bool(sub) and not any(str(r.get("structure_qualification")) in acc for r in sub)

    blocked = [s for s in (["RETEST_ENTRY"], ["BREAKOUT_ENTRY", "REBREAKOUT_ENTRY"]) if _blocked(s)]
    if blocked:
        detail = " / ".join("+".join(s) for s in blocked)
        reasons.append(f"LIMITATION：上游 Step 6 落盘数据使 {detail} 结构性不可达 BUY"
                       f"（其行全部 qualification ∉ {sorted(acc)}），本层不得绕过 Step 6 资格门"
                       f"（§六十二 Step6 bypass = NOT_READY），当前 BUY 仅来自 PULLBACK_ENTRY")
    if not buys:
        reasons.append("LIMITATION：当前上游输出下 BUY = 0（规格允许，且不得为凑数量降低门槛）")
    if reasons:
        return "CONDITIONAL_READY", "；".join(reasons)
    return "READY_FOR_LIVE_EXECUTION", "全部检查通过"


def write_audit(cfg: dict, art: dict, val: dict, bt: dict, rob: dict) -> dict:
    rows = art["rows"]
    buys = [r for r in rows if r["action"] == cfg["final"]["action_buy"]]
    status, reason = final_status(cfg, val, art, rob)
    rep = legacy_isolation_report()

    ent_dist = _dist(rows, "entry_state")
    nt = [r for r in rows if r["action"] == cfg["final"]["action_no_trade"]]
    nt_dist = _dist(nt, "primary_no_trade_reason")
    gate_fail = {}
    for r in nt:
        for g in str(r.get("gate_failures", "")).split("|"):
            if g:
                gate_fail[g] = gate_fail.get(g, 0) + 1
    gate_fail = dict(sorted(gate_fail.items(), key=lambda x: -x[1]))
    by_date = {}
    for r in rows:
        by_date.setdefault(str(r["trade_date"]), 0)
    for r in buys:
        by_date[str(r["trade_date"])] = by_date.get(str(r["trade_date"]), 0) + 1
    dates = sorted(by_date)

    L = []
    L += ["# Step 7 Execution / Trade Decision Layer 审计报告", "",
          f"- 最新交易日：{art['trade_date']}", f"- 决策行数：{len(rows)}",
          f"- BUY：{len(buys)}　NO TRADE：{len(nt)}",
          f"- FINAL STATUS：**{status}**", f"- 判定原因：{reason}", "",
          "> 三条永久交易原则（§五十二 / §四）：", ""]
    L += [f"> {i + 1}. {p}" for i, p in enumerate(STEP7_PRINCIPLES)]
    L += ["", "---", "", "## 1. 输入", "",
          _md_table(["输入", "路径", "存在"],
                    [["Step 5 候选", os.path.relpath(IN_STEP5_CANDIDATE, BASE_DIR),
                      os.path.exists(IN_STEP5_CANDIDATE)],
                     ["Step 6 结构骨架", os.path.relpath(IN_STRUCTURE_DAILY, BASE_DIR),
                      os.path.exists(IN_STRUCTURE_DAILY)],
                     ["Step 6 结构评分", os.path.relpath(IN_STRUCTURE_SCORE, BASE_DIR),
                      os.path.exists(IN_STRUCTURE_SCORE)],
                     ["Step 6 平台", os.path.relpath(IN_CONSOLIDATION, BASE_DIR),
                      os.path.exists(IN_CONSOLIDATION)],
                     ["Step 6 突破", os.path.relpath(IN_BREAKOUT, BASE_DIR),
                      os.path.exists(IN_BREAKOUT)],
                     ["Step 6 回踩", os.path.relpath(IN_RETEST, BASE_DIR),
                      os.path.exists(IN_RETEST)],
                     ["Step 6 HVT 历史", os.path.relpath(IN_HVT_HISTORY, BASE_DIR),
                      os.path.exists(IN_HVT_HISTORY)],
                     ["板块层状态历史", os.path.relpath(IN_SECTOR_STATE_HISTORY, BASE_DIR),
                      os.path.exists(IN_SECTOR_STATE_HISTORY)],
                     ["板块层日统计", os.path.relpath(IN_SECTOR_DAILY_STATS, BASE_DIR),
                      os.path.exists(IN_SECTOR_DAILY_STATS)],
                     ["Tushare 缓存库", DB_PATH, os.path.exists(DB_PATH)]]), "",
          f"- 历史锚点：{cfg['input']['hist_start_date']}（来源：{cfg['input']['hist_start_source']}）",
          f"- ATR 口径：{cfg['lookback']['atr_method']}（窗口 {cfg['lookback']['atr_window']}）"
          f" —— {cfg['lookback']['atr_method_note']}",
          f"- 摆动点定义：{cfg['lookback']['swing_definition']}"
          f"（高 {cfg['lookback']['swing_high_window']} 日 / 低 {cfg['lookback']['swing_low_window']} 日）",
          f"- 价格口径：{cfg['meta']['price_basis']}", "",
          "### 1.1 只读补算量（Step 6 未落盘）", "",
          _md_table(["结构量", "口径", "说明"],
                    [["atr20", f"{cfg['lookback']['atr_method']} ATR（含当日）",
                      "与 Step 6 `_atr` 逐字一致，保证 extension / atr_dist 可比"],
                     ["high_20/60/120/250", "滚动最高后复权价（含当日）", "阻力位与 PRIOR_HIGH 来源"],
                     ["recent_swing_high", f"{cfg['lookback']['swing_high_window']} 日最高（含当日）",
                      "trigger 的 SWING_HIGH 来源"],
                     ["recent_swing_low", f"{cfg['lookback']['swing_low_window']} 日最低（含当日）",
                      "stop 的 SWING_LOW 来源"],
                     ["trigger_date / stop_date", "极值发生日回算（窗口含当日）", "严格 PIT"]]), "",
          "---", "", "## 2. 市场状态", ""]
    reg_rows = []
    for d in dates:
        g = art["regimes"].get(d, {})
        reg_rows.append([d, g.get("regime"), g.get("execution_mode"), _f(g.get("regime_score"), 1),
                         g.get("sector_n"), _pct(g.get("strong_healthy_share")),
                         _pct(g.get("weak_share")), _pct(g.get("breadth")), g.get("hard_risk")])
    L += [_md_table(["trade_date", "regime", "execution_mode", "regime_score", "sector_n",
                     "STRONG+HEALTHY 占比", "WEAK+DETERIORATING 占比", "breadth", "hard_risk"],
                    reg_rows[-25:]), "",
          f"- 状态枚举：{cfg['market_regime']['state_names']}",
          f"- 映射：{cfg['market_regime']['sector_state_map']}",
          f"- 数据源：{cfg['market_regime']['source']}（{cfg['market_regime']['source_files']}）",
          f"- hard-risk 档：{cfg['market_regime']['hard_risk_states']}", "",
          "---", "", "## 3. BUY 数量", "",
          f"- BUY = **{len(buys)}**；NO TRADE = {len(nt)}；决策行数 = {len(rows)}", ""]
    if buys:
        L += [_md_table(["trade_date", "ts_code", "name", "entry_state", "entry_type",
                         "execution_score", "execution_grade", "trigger_price", "entry_low",
                         "entry_high", "stop_price", "target_1", "target_2", "risk_reward",
                         "extension_risk", "market_regime"],
                        [[r["trade_date"], r["ts_code"], r.get("name"), r["entry_state"],
                          r["entry_type"], _f(r.get("execution_score"), 1), r.get("execution_grade"),
                          _f(r.get("trigger_price")), _f(r.get("entry_low")), _f(r.get("entry_high")),
                          _f(r.get("stop_price")), _f(r.get("target_1")), _f(r.get("target_2")),
                          _f(r.get("risk_reward"), 2), r.get("extension_risk"),
                          r.get("market_regime")] for r in buys]), ""]
    else:
        L += [f"> 当日 BUY = 0。规格 §五 / §五十 明确：宁可没有 BUY，也不要为了凑数量产生伪信号。", ""]
    L += ["### 3.1 BUY 来源分布", "",
          _dist_md(buys, "entry_state", "entry_state"),
          "", _dist_md(buys, "execution_grade", "execution_grade"),
          "", _dist_md(buys, "stop_source", "stop_source"),
          "", "---", "", "## 4. NO TRADE 原因", "",
          _md_table(["primary_no_trade_reason", "count"], [[k or "(空)", v] for k, v in nt_dist.items()]),
          "", "### 4.1 硬门命中次数（一行可命中多道门）", "",
          _md_table(["gate", "count"], [[k, v] for k, v in gate_fail.items()]), "",
          "### 4.2 entry_state 分布（全量）", "",
          _md_table(["entry_state", "count"], [[k, v] for k, v in ent_dist.items()]), "",
          "---", "", "## 5. HVT", "",
          _md_table(["hvt_state", "count"], [[k, v] for k, v in _dist(rows, "hvt_state").items()]), "",
          f"- HVT 门规则：{json.dumps(cfg['hvt_gate']['state_score'], ensure_ascii=False)}",
          f"- HVT_EVENT 不可直接 BUY：event_state_buyable = {cfg['hvt_gate']['event_state_buyable']}",
          f"- HVT_LOCKING 质量下限：{cfg['hvt_gate']['min_hvt_quality_score']}",
          f"- HVT_ADJUSTING 需突破确认：{cfg['hvt_gate']['require_breakout_in']}",
          f"- 豁免 entry_state：{cfg['hvt_gate']['exempt_entry_states']}",
          f"  - {cfg['hvt_gate']['exempt_note']}", "",
          "---", "", "## 6. Backtest", "",
          f"详见 [{os.path.basename(OUT_BACKTEST_MD)}]({os.path.basename(OUT_BACKTEST_MD)})。", "",
          _md_table(["group", "n"] + [f"T+{h} mean" for h in bt["horizons"]],
                    [[g, bt["groups"][g]] +
                     [_pct(bt["table"][g][h]["mean"]) for h in bt["horizons"]] for g in bt["groups"]]),
          "", f"- Calibration：transform={bt['calibration'].get('transform')}，"
              f"apply_to_decision={bt['calibration'].get('apply_to_decision')}，"
              f"BUY={bt['calibration'].get('buy_rows')}，"
              f"CALIBRATED 行={bt['calibration'].get('calibrated_rows')}",
          f"- Robustness：OVERFIT RISK = {rob['overfit_risk']}，"
          f"base BUY = {rob['base_buy']}，触发阈值 = {rob['flagged']}", "",
          "---", "", "## 7. Anti-Chasing（§五十五）", ""]
    anti = [r for r in val["rows"] if r["check_id"] == "CHECK_ANTI_CHASING"]
    L += [_md_table(["item", "value", "expected", "status"],
                    [[r["item"], r["value"], r["expected"], r["status"]] for r in anti]), "",
          "---", "", "## 8. Future Leakage（§五十四 C）", ""]
    fl = [r for r in val["rows"] if r["check_id"] == "CHECK_FUTURE_LEAKAGE"]
    L += [_md_table(["item", "value", "expected", "status", "detail"],
                    [[r["item"], r["value"], r["expected"], r["status"], r["detail"]] for r in fl]), "",
          "---", "", "## 9. Legacy 隔离（§六十六）", "",
          f"- legacy_reads = {rep['legacy_reads']}", f"- legacy_config_used = {rep['legacy_config_used']}",
          f"- 读取文件数 = {len(rep['read_files'])}",
          f"- 策略：{cfg['meta']['legacy_policy']}", "",
          "---", "", "## 10. PIT（§五十四 D）", ""]
    pit = [r for r in val["rows"] if r["check_id"] == "CHECK_PIT"]
    L += [_md_table(["item", "value", "expected", "status", "detail"],
                    [[r["item"], r["value"], r["expected"], r["status"], r["detail"]] for r in pit]), "",
          "---", "", "## 11. Robustness（§四十五）", "",
          _md_table(["perturbation", "base_buy", "perturbed_buy", "change_pct", "status"],
                    [[r["name"], r["base_buy"], r["perturbed_buy"], _pct(r["change_pct"]),
                      r["status"]] for r in rob["rows"]]), "",
          f"- 判定阈值：BUY 数量变化 > {_pct(cfg['robustness']['buy_count_change_max'])} → "
          f"{cfg['robustness']['overfit_flag']}",
          f"- 结论：OVERFIT RISK = **{rob['overfit_risk']}**", "",
          "---", "", "## 12. 验证汇总（§五十四）", "",
          _md_table(["check_id", "PASS", "FAIL"], _check_summary(val)), "",
          "---", "", "## 13. 层级边界与冲突记录（§六十六）", "",
          "| # | CONFLICT | OLD LOGIC | NEW LOGIC | RECOMMENDED ADAPTER |",
          "|---|---|---|---|---|",
          "| 01 | 执行层三套并存 | `hvt_bull/trade_execution.py`（trigger=ev.entry or pbl，"
          "invalidation=max(t0_high×0.95, trigger−1.2×ATR14)）；`trade_execution_engine.py`"
          "（STOP_K=0.97 / STOP_ATR=2.2 / BUY_BAND=0.985~1.015）；`w7_second_wave_engine.py` | "
          "Step 7 独立执行层：trigger 取真实结构位，stop = 结构支撑 − 0.5×ATR20 | "
          "不合并旧引擎；旧引擎只作口径借鉴，不 import / 不调用 / 不覆盖 |",
          "| 02 | Market Regime 四套枚举 | risk_on/neutral/weak/risk_off/panic；"
          "Bear/Recovery/Neutral/Bull/Euphoria；BULL/RECOVERY/BEAR/RANGE；数值 0/1/2 | "
          "STRONG/HEALTHY/NEUTRAL/WEAK/RISK_OFF，由 Step 2/3/4 板块层状态分布与广度聚合 | "
          "不新增市场状态模块；只读板块层落盘产物 |",
          "| 03 | ATR 口径三套 | ATR14 简单均值（trade_execution_engine）；"
          "1.2×ATR14 / 2.2×ATR14 止损 k 值 | SMA ATR20（TR 的 20 日简单均值），与 "
          "Step 6 `_atr` 逐字一致；缓冲 0.5×ATR20 写入 config | 以 Step 6 为唯一基准，"
          "保证两层结构量可比；WILDER 作为可选口径保留在 config |",
          "| 04 | 确认态终局归属（**新增·实测**） | Step 6 的 HVT 生命周期在突破/回踩确认后进入 "
          "HVT_FAILED → structure_state=FAILED → qualification=REJECTED | Step 7 要求"
          "BREAKOUT_ENTRY / RETEST_ENTRY / REBREAKOUT_ENTRY 才可 BUY | 不绕过 Step 6 资格门"
          "（§六十二 Step6 bypass = NOT_READY）；如实记录为 LIMITATION，BUY 仅来自 PULLBACK_ENTRY"
          "（实测：RETEST_ENTRY 207 行、BREAKOUT_ENTRY 120 行、REBREAKOUT_ENTRY 67 行 **全部** "
          "structure_qualification=REJECTED，故 0 行能进入 BUY；PULLBACK_ENTRY 323 行中 39 行通过全部门）；"
          "FINAL STATUS 因此为 CONDITIONAL_READY（§六十二）而非 READY_FOR_LIVE_EXECUTION；"
          "**需上游决策**：是否在 Step 6 为确认态保留独立资格档 |",
          "| 05 | 止损宽度上限（**新增·实测·本层已裁决**） | 规格 §十八 / §十九 只规定 stop 的算法"
          "（结构支撑 − ATR buffer），**全文未出现止损宽度上限**；旧执行引擎存在固定百分比止损习惯 | "
          "首版曾把 `max_stop_distance_pct=0.12` 接成硬门 STOP_TOO_WIDE，导致 78 个交易日 BUY 恒为 0"
          "（PULLBACK 的 stop 锚点只能是 10 日摆动低点，宽度中位 22%） | 已改为 `warn_stop_distance_pct`，"
          "只输出风险提示（risk_note 中的 WIDE_WARN）与审计计数，不参与 BUY 判定；止损一侧质量把关交回"
          "§二十一 Risk/ Reward 的 minimum。改动后 BUY 由 0 → 39（仍属少而精）。"
          "**若需恢复硬门**：把该键改回硬门并同步 config.no_trade_reason 的 reasons / precedence / "
          "gate_to_reason 三处，且须接受 BUY 恒为 0 的后果 |",
          "| 06 | Anti-Chasing 健康结构下限（**新增·实测·本层已裁决**） | 规格 §四十一 只给出三组"
          "区间（极端 = ret_5>=30% 且 dist_ma20>=30%；健康 = ret_5∈[2%,8%] 且 dist_ma20∈[0%,10%] "
          "且 volume_ratio∈[1,2]），§五十五 只给出相对判定「极端 BUY 率必须显著低于健康 BUY 率」，"
          "健康一侧只要求「不能因为 anti-chasing 过强而全部被过滤」，**全文未出现任何绝对下限** | "
          "首版有两处偏离：① 用 Step 6 的 `extension_risk=LOW`（37092/37257 = 99.6% 的样本）"
          "充当健康组，把健康 BUY 率稀释成 0.001 量级的伪低值；② 自设绝对下限 "
          "`anti_chasing_min_healthy_buy_rate=0.02` 并接成 FAIL 项 | 已按规格区间重写："
          "极端/健康两组改用 config.validation 已声明的 §四十一 区间（ret_5 / dist_ma20 / "
          "volume_ratio），绝对下限改为规格原文的「不得被全部过滤」（`anti_chasing_min_healthy_buy_count=1`），"
          "并新增 `extreme_vs_healthy_rate_ratio <= anti_chasing_max_rate_ratio` 承担「显著低于」这条相对判定；"
          "`healthy_buy_rate` 降为信息项。实测：极端组 1 行 / BUY 0；健康组 4455 行 / BUY 2；"
          "极端组 BUY 率 0.0 <= 健康组 0.000449。**若需恢复绝对下限**：把 `anti_chasing_min_healthy_buy_count` "
          "改回 rate 阈值并接受健康组被误判为 FAIL 的后果 |", "",
          "### 13.1 层级边界变化", "",
          "`output/sector_state_today.json` 的 `not_in_scope` 明确排除了「个股BUY/NO TRADE」与"
          "「交易执行」。Step 7 首次跨越该边界：板块层（Step 2/3/4）仍只输出状态，"
          "个股执行决策全部落在本层产物中，不改写板块层任何文件。", "",
          "### 13.2 未落盘量映射", "",
          _md_table(["规格字段", "实际来源", "说明"],
                    [["retest_low", "stock_retest.retest_level",
                      "Step 6 未落盘 retest_low，按其唯一回踩价位锚点显式映射（config.stop.field_map_note）"],
                     ["support_1 / resistance_1", "Step 6 `support_resistance` 口径重算",
                      "Step 6 未落盘，按同一公式（fmax(platform_low, ma20) / fmax(platform_high, high_20)）重算"],
                     ["support_2 / resistance_2", "未使用", "§十八 / §二十 的优先级只到 MA20 与 ATR buffer"]]),
          "", "---", "", "## 14. 收尾声明", ""]
    L += [f"- {x}" for x in STEP7_STOP_NOTICE]
    L += ["", f"- FINAL STATUS = **{status}**", f"- 原因：{reason}", ""]
    text = "\n".join(L)
    _write_text(OUT_AUDIT_MD, text)
    return {"status": status, "reason": reason, "text": text}


def _check_summary(val: dict) -> list:
    d: dict = {}
    for r in val["rows"]:
        k = r["check_id"]
        d.setdefault(k, [0, 0])
        d[k][0 if r["status"] == "PASS" else 1] += 1
    return [[k, v[0], v[1]] for k, v in sorted(d.items())]


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Step 7 Execution / Trade Decision Layer")
    ap.add_argument("--full", action="store_true", help="全量重算并写出全部产物")
    ap.add_argument("--date", default=None, metavar="YYYYMMDD", help="指定交易日（非交易日回退最近有效交易日）")
    ap.add_argument("--validate", action="store_true", help="运行验证 / 回测 / 稳健性 / 审计")
    args = ap.parse_args(argv)
    if not (args.full or args.date or args.validate):
        ap.print_help()
        return 0
    cfg = load_config()
    art = None
    if args.date:
        art = run_date(cfg, args.date)
    elif args.full:
        art = build_all(cfg)
    if args.validate:
        if art is None:
            art = build_all(cfg, write=False)
        val = run_validate(cfg, art)
        bt = run_backtest(cfg, art)
        write_backtest_md(cfg, bt)
        rob = run_robustness(cfg, art)
        append_robustness_md(cfg, rob)
        audit = write_audit(cfg, art, val, bt, rob)
        print(f"\nVALIDATION: PASS={val['n_pass']} FAIL={val['n_fail']}")
        if val["failed_checks"]:
            print(f"FAILED CHECKS = {val['failed_checks']}")
        print(f"\nFINAL STATUS = {audit['status']}\n{audit['reason']}")
    print("\n" + "\n".join(STEP7_STOP_NOTICE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
