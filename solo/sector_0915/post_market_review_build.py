# -*- coding: utf-8 -*-
"""Step 8：Post-Market Review / 盘后复盘系统

Step 8 是 Step 1-7 之上的「复盘与反馈层」（§一）。它不选股、不产生 BUY、不改上游。

硬约束：
  * READ ONLY —— 不修改 Step 1-7 任何落盘结果（§三 / §三十六）。
  * 不重新选股 / 不重新打分 / 不产生 BUY。
  * 防马后炮：feature 日期 <= 对应 signal / review 日期；未来数据只能作为 outcome（§四十九-1 / §五十四）。
  * WATCH != BUY（§四十二）。
  * Feedback != 自动修改模型；必须满足 sample_size + multiple_dates + multiple_regimes 才可进入
    MODEL_CHANGE_CANDIDATE（§三十七 / §三十八 / §三十九）。
  * 完成后停止：不做自动参数优化、不自动改 Step 1-7（§五十七）。

CLI（§四十七 / §四十八）：
  python post_market_review_build.py --date 20260918
  python post_market_review_build.py --full
  python post_market_review_build.py --validate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(ROOT, "config", "post_market_review_config.json")

with open(CFG_PATH, encoding="utf-8") as fh:
    CFG = json.load(fh)

META = CFG["meta"]
INP = CFG["input"]
LOOK = CFG["lookback"]
MKT = CFG["market"]
EARN = CFG["earning_effect"]
STAG = CFG["structure_tag"]
THEME = CFG["theme"]
CANDC = CFG["candidate"]
STRC = CFG["structure_review"]
HVTC = CFG["hvt_review"]
BKOC = CFG["breakout_review"]
RTC = CFG["retest_review"]
BUYC = CFG["buy_review"]
MAEC = CFG["mae_mfe"]
NTC = CFG["no_trade_review"]
ERRC = CFG["error"]
SCC = CFG["scorecard"]
FUNC = CFG["funnel"]
INCR = CFG["incremental"]
CROS = CFG["cross"]
FBK = CFG["feedback"]
WPC = CFG["watch_pool"]
REP = CFG["report"]
VALC = CFG["validation"]
FSC = CFG["final_status"]
OUT = CFG["output"]

LEVEL_PASS, LEVEL_WARN, LEVEL_FAIL = "PASS", "WARNING", "ISSUE"


# ══════════════════════════════════════════════════════════════════════════
# 0. 通用工具
# ══════════════════════════════════════════════════════════════════════════
def _abs(rel: str) -> str:
    return os.path.join(ROOT, str(rel).replace("/", os.sep).replace("\\", os.sep))


def fnum(x, default=np.nan) -> float:
    """安全转 float。"""
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def jnum(x, nd: int = 6):
    """JSON 安全的数字（NaN -> None）。"""
    v = fnum(x, np.nan)
    return None if not np.isfinite(v) else round(float(v), nd)


def jstr(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and not np.isfinite(x):
        return ""
    return str(x)


def safe_div(a, b, default=np.nan) -> float:
    a, b = fnum(a), fnum(b)
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return default
    return float(a) / float(b)


def pct_ret(cur, base) -> float:
    return safe_div(fnum(cur) - fnum(base), fnum(base), np.nan)


def clip01(v, lo, hi) -> float:
    v = fnum(v, np.nan)
    if not np.isfinite(v):
        return np.nan
    return float(min(max(v, lo), hi))


def norm01(v, lo, hi) -> float:
    v = fnum(v, np.nan)
    if not np.isfinite(v) or hi == lo:
        return np.nan
    return float(min(max((v - lo) / (hi - lo), 0.0), 1.0))


def stat_block(values) -> dict:
    s = pd.Series([fnum(v, np.nan) for v in values], dtype="float64").dropna()
    out = {"n": int(len(s))}
    if len(s):
        out["win_rate"] = round(float((s > 0).mean()), 6)
        out["mean_return"] = round(float(s.mean()), 6)
        out["median_return"] = round(float(s.median()), 6)
        out["positive_ratio"] = round(float((s > 0).mean()), 6)
    else:
        out.update(win_rate=None, mean_return=None, median_return=None, positive_ratio=None)
    return out


def mean_of(values):
    s = pd.Series([fnum(v, np.nan) for v in values], dtype="float64").dropna()
    return None if not len(s) else round(float(s.mean()), 6)


def read_csv_cols(rel: str, cols) -> pd.DataFrame:
    """只读指定列，避免 173k 行文件全量读入。缺列时如实返回空表。"""
    path = _abs(rel)
    if not os.path.exists(path):
        return pd.DataFrame(columns=list(cols))
    head = pd.read_csv(path, nrows=0)
    use = [c for c in cols if c in head.columns]
    df = pd.read_csv(path, usecols=use, low_memory=False)
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").fillna(0).astype("int64")
    return df


def write_csv(df: pd.DataFrame, rel: str) -> None:
    path = _abs(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _json_safe(o):
    """NaN/Inf -> null，numpy 标量 -> python 原生类型（保证严格合法 JSON）。"""
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if np.isfinite(v) else None
    return o


def write_json(obj, rel: str) -> None:
    path = _abs(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_json_safe(obj), fh, ensure_ascii=False, indent=2, default=str, allow_nan=False)


def is_st_name(name) -> bool:
    s = jstr(name)
    if not s:
        return False
    return bool(re.search(MKT["st_keyword_regex"], s))


def board_of(ts_code: str) -> str:
    c = str(ts_code).split(".")[0]
    for board, prefixes in MKT["board_prefixes"].items():
        for p in prefixes:
            if c.startswith(p) and len(p) == 3:
                return board
    for board, prefixes in MKT["board_prefixes"].items():
        for p in prefixes:
            if c.startswith(p) and len(p) == 2:
                return board
    return "MAIN"


def limit_pct_of(ts_code: str, name_map: dict) -> float:
    if is_st_name(name_map.get(ts_code)):
        return float(MKT["limit_pct"]["ST"])
    return float(MKT["limit_pct"].get(board_of(ts_code), MKT["limit_pct"]["MAIN"]))


# ══════════════════════════════════════════════════════════════════════════
# 1. 行情仓库（本地 DB，只读）
# ══════════════════════════════════════════════════════════════════════════
class BarStore:
    """只读行情读取。全窗口一次读入，供 --full 复用。"""

    def __init__(self, db_path: str, date_min: int, date_max: int):
        self.conn = sqlite3.connect(db_path)
        self.date_min = int(date_min)
        self.date_max = int(date_max)
        self.cal = self._load_calendar()
        self.idx = self._load_index()
        self.frame = self._load_bars()
        self._wide_cache: dict = {}

    def _load_calendar(self):
        q = ("select distinct trade_date from index_daily_cache where ts_code = ? "
             "order by trade_date")
        df = pd.read_sql(q, self.conn, params=[MKT["index_codes"][MKT["benchmark_index"]]])
        return [int(x) for x in df.iloc[:, 0].tolist()]

    def _load_index(self) -> pd.DataFrame:
        codes = tuple(MKT["index_codes"].values())
        ph = ",".join("?" * len(codes))
        q = (f"select ts_code, trade_date, open, high, low, close, pre_close, pct_chg, amount "
             f"from index_daily_cache where ts_code in ({ph}) and trade_date between ? and ?")
        df = pd.read_sql(q, self.conn, params=list(codes) + [self.date_min, self.date_max])
        for c in ("trade_date",):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        return df

    def _load_bars(self) -> pd.DataFrame:
        q = ("select ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount "
             "from daily_cache where trade_date between ? and ?")
        df = pd.read_sql(q, self.conn, params=[self.date_min, self.date_max])
        df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").fillna(0).astype("int64")
        qa = ("select ts_code, trade_date, adj_factor from adj_factor_cache "
              "where trade_date between ? and ?")
        adj = pd.read_sql(qa, self.conn, params=[self.date_min, self.date_max])
        adj["trade_date"] = pd.to_numeric(adj["trade_date"], errors="coerce").fillna(0).astype("int64")
        df = df.merge(adj, on=["ts_code", "trade_date"], how="left")
        df["adj_factor"] = df["adj_factor"].fillna(1.0)
        df["adj_close"] = df["close"] * df["adj_factor"]
        df["adj_open"] = df["open"] * df["adj_factor"]
        df["adj_high"] = df["high"] * df["adj_factor"]
        df["adj_low"] = df["low"] * df["adj_factor"]
        return df

    def wide(self, col: str) -> pd.DataFrame:
        if col not in self._wide_cache:
            self._wide_cache[col] = self.frame.pivot_table(
                index="trade_date", columns="ts_code", values=col, aggfunc="last")
        return self._wide_cache[col]

    def bars_at(self, trade_date: int) -> pd.DataFrame:
        return self.frame[self.frame["trade_date"] == trade_date]

    def index_at(self, trade_date: int) -> dict:
        d = self.idx[self.idx["trade_date"] == trade_date]
        out = {}
        for key, code in MKT["index_codes"].items():
            row = d[d["ts_code"] == code]
            out[key] = {
                "close": fnum(row["close"].iloc[0]) if len(row) else np.nan,
                "pct_chg": fnum(row["pct_chg"].iloc[0]) if len(row) else np.nan,
                "high": fnum(row["high"].iloc[0]) if len(row) else np.nan,
                "low": fnum(row["low"].iloc[0]) if len(row) else np.nan,
                "pre_close": fnum(row["pre_close"].iloc[0]) if len(row) else np.nan,
                "amount": fnum(row["amount"].iloc[0]) if len(row) else np.nan,
            }
        return out

    def next_days(self, trade_date: int, n: int):
        later = [d for d in self.cal if d > trade_date]
        return later[:n]

    def prev_day(self, trade_date: int):
        earlier = [d for d in self.cal if d < trade_date]
        return earlier[-1] if earlier else None

    def window_before(self, trade_date: int, n: int):
        earlier = [d for d in self.cal if d <= trade_date]
        return earlier[-n:]

    def ret_panel(self, base_date: int, codes, horizons) -> dict:
        """相对 base_date 后复权收盘的前向收益 {h: {ts_code: ret}}。"""
        out = {}
        w = self.wide("adj_close")
        if base_date not in w.index:
            return {h: {} for h in horizons}
        base = w.loc[base_date]
        fwd = self.next_days(base_date, max(horizons) if horizons else 0)
        for h in horizons:
            if h > len(fwd):
                out[h] = {}
                continue
            row = w.loc[fwd[h - 1]]
            r = (row / base) - 1.0
            out[h] = {c: fnum(r.get(c)) for c in codes}
        return out

    def path_stats(self, base_date: int, codes, windows, ref_adj: dict) -> dict:
        """以 ref_adj 为基准的后复权 MAE / MFE / close_return。"""
        wl, wh, wc = self.wide("adj_low"), self.wide("adj_high"), self.wide("adj_close")
        fwd = self.next_days(base_date, max(windows) if windows else 0)
        res: dict = {c: {} for c in codes}
        for h in windows:
            if h > len(fwd):
                for c in codes:
                    res[c][h] = (np.nan, np.nan, np.nan)
                continue
            dates = fwd[:h]
            lo = wl.loc[dates].min()
            hi = wh.loc[dates].max()
            cl = wc.loc[dates[-1]]
            for c in codes:
                ref = fnum(ref_adj.get(c))
                if not np.isfinite(ref) or ref == 0:
                    res[c][h] = (np.nan, np.nan, np.nan)
                    continue
                res[c][h] = (pct_ret(lo.get(c), ref), pct_ret(hi.get(c), ref),
                             pct_ret(cl.get(c), ref))
        return res


# ══════════════════════════════════════════════════════════════════════════
# 2. 上下文（静态上游数据）
# ══════════════════════════════════════════════════════════════════════════
class Ctx:
    def __init__(self):
        self.sector_master = json.load(open(_abs(INP["sector_master"]), encoding="utf-8"))
        self.theme_ids = {s["sector_id"] for s in self.sector_master["sectors"]}
        self.theme_names = {s["sector_id"]: s["sector_name"] for s in self.sector_master["sectors"]}
        self.name_map = self._load_names()

        self.exec = read_csv_cols(INP["stock_execution_daily"], [
            "trade_date", "ts_code", "name", "primary_theme_id", "candidate_type",
            "candidate_status", "structure_state", "structure_class", "structure_qualification",
            "structure_quality", "hvt_state", "hvt_quality_score", "breakout_state",
            "retest_state", "volume_state", "extension_risk", "data_quality", "market_regime",
            "execution_mode", "entry_state", "entry_type", "trigger_price", "entry_low",
            "entry_high", "entry_ref", "stop_price", "support_1", "target_1", "risk", "reward",
            "risk_reward", "action", "primary_no_trade_reason", "gate_failures", "price_basis",
            "adj_factor", "raw_close", "execution_grade", "execution_reason", "risk_note",
            "invalid_condition", "historical_execution_quality"])
        self.seos = read_csv_cols(INP["sector_seos_daily"], [
            "trade_date", "sector_id", "sector_name", "rotation_group", "theme_health",
            "theme_health_delta_1", "theme_health_delta_3", "theme_health_delta_5", "breadth",
            "breadth_delta_1", "breadth_delta_3", "breadth_delta_5", "core_breadth",
            "core_breadth_delta_1", "core_breadth_delta_3", "core_breadth_delta_5",
            "primary_breadth", "primary_breadth_delta_3", "secondary_breadth",
            "secondary_breadth_delta_3", "relative_strength", "relative_strength_delta_3",
            "volume_ratio_1", "volume_ratio_3", "theme_amount_share", "amount_share_delta_3",
            "seos_score", "core_pattern", "theme_phase", "prev_phase", "phase_transition",
            "phase_duration", "startup_quality", "rotation_signal", "signal_reason",
            "data_quality_score"])
        self.sds = read_csv_cols(INP["sector_daily_stats"], [
            "trade_date", "sector_id", "breadth", "core_breadth", "primary_breadth",
            "sector_amount_share", "data_quality_score"])
        self.rot = read_csv_cols(INP["sector_rotation"], [
            "trade_date", "sector_id", "sector_name", "rotation_signal", "rotation_reason",
            "ref_breadth_delta", "breadth_delta_5", "core_breadth_delta_5", "rs_turn_5",
            "amount_share_delta_5"])
        self.ph = read_csv_cols(INP["sector_phase_history"], [
            "trade_date", "sector_id", "prev_phase", "current_phase", "phase_transition",
            "phase_changed"])
        self.dif = read_csv_cols(INP["sector_stock_diffusion"], [
            "trade_date", "sector_id", "sector_name", "theme_phase", "theme_bucket",
            "diffusion_type", "diffusion_pattern", "diffusion_stage", "core_breadth_delta_3",
            "primary_breadth_delta_3", "secondary_breadth_delta_3", "theme_opportunity_score",
            "candidate_count"])
        self.cand = read_csv_cols(INP["sector_stock_candidate_daily"], [
            "trade_date", "ts_code", "name", "sector_id", "sector_name", "rotation_group",
            "theme_phase", "theme_seos", "theme_health", "membership_type", "theme_role",
            "candidate_type", "candidate_status", "candidate_score", "risk_flags",
            "data_quality"])
        self.hvth = read_csv_cols(INP["stock_hvt_history"], [
            "trade_date", "ts_code", "hvt_state", "hvt_stage", "hvt_days_after",
            "adjustment_status", "locking_status", "breakout_state", "retest_state"])
        self.breakout = read_csv_cols(INP["stock_breakout"], [
            "trade_date", "ts_code", "breakout_level", "breakout_days_ago",
            "breakout_volume_ratio", "breakout_state", "retest_status", "retest_depth",
            "retest_volume_ratio"])
        self.retest = read_csv_cols(INP["stock_retest"], [
            "trade_date", "ts_code", "retest_state", "retest_level", "retest_depth",
            "retest_volume_ratio", "retest_hold", "retest_quality", "breakout_level"])
        self.structure = read_csv_cols(INP["stock_structure_daily"], [
            "trade_date", "ts_code", "name", "close", "pct_chg", "ma20",
            "structure_state", "structure_qualification", "structure_quality", "extension_risk",
            "data_quality"])
        self.exitsig = read_csv_cols(INP["stock_exit_signal"], [
            "trade_date", "ts_code", "exit_state", "exit_reason"])

    def _load_names(self) -> dict:
        src = INP.get("name_source") or {}
        out: dict = {}
        if src.get("file"):
            p = _abs(src["file"])
            if os.path.exists(p):
                head = pd.read_csv(p, nrows=0)
                cc, nc = src["code_col"], src["name_col"]
                if cc in head.columns and nc in head.columns:
                    d = pd.read_csv(p, usecols=[cc, nc], low_memory=False).dropna()
                    out.update(dict(zip(d[cc].astype(str), d[nc].astype(str))))
        return out


# ══════════════════════════════════════════════════════════════════════════
# 3. 市场层（§五 / §六 / §七 / §八）
# ══════════════════════════════════════════════════════════════════════════
def limit_flags(bars: pd.DataFrame, name_map: dict) -> pd.DataFrame:
    """逐行标记 涨停 / 跌停 / 炸板（按板块与 ST 口径）。"""
    if not len(bars):
        return pd.DataFrame(columns=["ts_code", "limit_up", "limit_down", "failed_limit_up",
                                     "limit_price", "limit_down_price"])
    d = bars[["ts_code", "close", "high", "pre_close"]].copy()
    lp = np.array([limit_pct_of(c, name_map) for c in d["ts_code"]], dtype="float64")
    pc = d["pre_close"].to_numpy(dtype="float64")
    up_px = np.round(pc * (1.0 + lp) + 1e-9, MKT["price_round_digits"])
    dn_px = np.round(pc * (1.0 - lp) + 1e-9, MKT["price_round_digits"])
    cl = d["close"].to_numpy(dtype="float64")
    hi = d["high"].to_numpy(dtype="float64")
    band = 0.005
    d["limit_price"] = up_px
    d["limit_down_price"] = dn_px
    d["limit_up"] = cl >= up_px - band
    d["limit_down"] = cl <= dn_px + band
    d["failed_limit_up"] = (hi >= up_px - band) & (cl < up_px - band)
    return d[["ts_code", "limit_up", "limit_down", "failed_limit_up",
              "limit_price", "limit_down_price"]]


def max_consecutive_board(store: BarStore, review_date: int, name_map: dict) -> int:
    days = store.window_before(review_date, 30)
    mats = {}
    for d in days:
        f = limit_flags(store.bars_at(d), name_map)
        if len(f):
            mats[d] = dict(zip(f["ts_code"], f["limit_up"].to_numpy()))
    if not mats:
        return 0
    codes = sorted({c for m in mats.values() for c in m})
    idx = {c: i for i, c in enumerate(codes)}
    M = np.zeros((len(days), len(codes)), dtype=bool)
    for i, d in enumerate(days):
        for c, v in mats[d].items():
            M[i, idx[c]] = bool(v)
    best = 0
    last = M[-1]
    for j in np.nonzero(last)[0]:
        k, i = 0, M.shape[0] - 1
        while i >= 0 and M[i, j]:
            k += 1
            i -= 1
        best = max(best, k)
    return int(best)


def regime_by_date(ctx: Ctx) -> dict:
    e = ctx.exec
    if not len(e):
        return {}
    out = {}
    for d, g in e.groupby("trade_date"):
        r = g["market_regime"].mode()
        m = g["execution_mode"].mode()
        out[int(d)] = {
            "market_regime": jstr(r.iloc[0]) if len(r) else "",
            "execution_mode": jstr(m.iloc[0]) if len(m) else "",
            "rows": int(len(g)),
            "buy": int((g["action"] == "BUY").sum()),
            "no_trade": int((g["action"] == "NO TRADE").sum()),
        }
    return out


def build_market(store: BarStore, ctx: Ctx, review_date: int, signal_date: int,
                 regime_map: dict, theme_summary: dict, val: list) -> dict:
    bars = store.bars_at(review_date)
    excl = tuple(MKT.get("exclude_prefixes", []))
    if excl:
        bars = bars[~bars["ts_code"].str.startswith(excl)]
    n_all = len(bars)
    if not n_all:
        val.append({"level": LEVEL_FAIL, "code": "MARKET_DATA",
                    "message": f"{review_date} 无全市场行情"})
        return {"trade_date": review_date, "rows": 0, "data_status": LEVEL_FAIL}

    prev_date = store.prev_day(review_date)
    prev_bars = store.bars_at(prev_date) if prev_date else bars.iloc[0:0]
    pct = bars["pct_chg"].to_numpy(dtype="float64") / 100.0
    amt = bars["amount"].to_numpy(dtype="float64")
    lf = limit_flags(bars, ctx.name_map)
    up = int((pct > 0).sum())
    dn = int((pct < 0).sum())
    flat = int((pct == 0).sum())
    breadth = safe_div(up, up + dn + flat, 0.0)
    wb = safe_div(amt[pct > 0].sum(), amt.sum(), 0.0)
    lu = int(lf["limit_up"].sum())
    ld = int(lf["limit_down"].sum())
    flu = int(lf["failed_limit_up"].sum())
    amount_sum = float(amt.sum())
    amount_yi = amount_sum / 1e5
    ma_days = store.window_before(review_date, LOOK["amount_ma_window"])
    amt_hist = [float(store.bars_at(d)["amount"].sum()) for d in ma_days]
    amount_ma20 = float(np.mean(amt_hist)) if amt_hist else np.nan
    amount_ratio20 = safe_div(amount_sum, amount_ma20, np.nan)
    prev_amount = float(prev_bars["amount"].sum()) if len(prev_bars) else np.nan
    amount_change = pct_ret(amount_sum, prev_amount)
    prev_pct = prev_bars["pct_chg"].to_numpy(dtype="float64") / 100.0
    prev_up = int((prev_pct > 0).sum())
    prev_dn = int((prev_pct < 0).sum())
    prev_flat = int((prev_pct == 0).sum())
    prev_breadth = safe_div(prev_up, prev_up + prev_dn + prev_flat, np.nan)
    breadth_change = (breadth - prev_breadth) if np.isfinite(fnum(prev_breadth)) else np.nan

    idx = store.index_at(review_date)
    idx_ret = {k: fnum(v["pct_chg"]) / 100.0 for k, v in idx.items()}
    sse = idx.get(MKT["benchmark_index"], {})
    sse_amp = safe_div(fnum(sse.get("high")) - fnum(sse.get("low")), fnum(sse.get("pre_close")), np.nan)

    reg = regime_map.get(review_date) or {}
    reg_dates = sorted(regime_map.keys())
    reg_date = max([d for d in reg_dates if d <= review_date], default=None)
    reg = regime_map.get(reg_date, {}) if reg_date else {}
    prev_reg_date = max([d for d in reg_dates if reg_date and d < reg_date], default=None)
    prev_reg = regime_map.get(prev_reg_date, {}) if prev_reg_date else {}
    regime_now = jstr(reg.get("market_regime"))
    regime_before = jstr(prev_reg.get("market_regime"))
    regime_change = (regime_before, regime_now) if (regime_before and regime_now) else ("", "")
    regime_reason = ""
    if regime_before and regime_now and regime_before != regime_now:
        regime_reason = (
            f"breadth {jnum(prev_breadth,4)}→{jnum(breadth,4)}；"
            f"成交额变化 {jnum(amount_change*100 if np.isfinite(fnum(amount_change)) else np.nan,2)}%；"
            f"涨停 {lu} / 跌停 {ld}；指数结构 SSE {jnum(idx_ret.get('SSE',np.nan)*100 if np.isfinite(fnum(idx_ret.get('SSE'))) else np.nan,2)}%"
            f"（依据 breadth / volume / amount / limit-up-down / index structure，§六）")
    elif regime_now:
        regime_reason = (
            f"regime 未变化（{regime_before or '-'}→{regime_now}）；"
            f"breadth {jnum(breadth,4)}，成交额变化 "
            f"{jnum(amount_change*100 if np.isfinite(fnum(amount_change)) else np.nan,2)}%")

    # 赚钱效应（§七）
    med = float(np.median(pct)) if n_all else np.nan
    srt = np.sort(pct)
    k10 = max(1, int(round(n_all * 0.10)))
    top10 = float(np.mean(srt[-10:])) if n_all >= 10 else np.nan
    bot10 = float(np.mean(srt[:10])) if n_all >= 10 else np.nan
    top_dec = float(np.mean(srt[-k10:]))
    bot_dec = float(np.mean(srt[:k10]))
    op = bars["open"].to_numpy(dtype="float64")
    cl = bars["close"].to_numpy(dtype="float64")
    pc = bars["pre_close"].to_numpy(dtype="float64")
    holc = int(((op > pc) & (cl < op)).sum())
    lohc = int(((op < pc) & (cl > op)).sum())
    lu_strength = safe_div(lu, n_all, 0.0)
    ld_pressure = safe_div(ld, n_all, 0.0)

    hvt_date = ctx.hvth["trade_date"].max() if len(ctx.hvth) else 0
    hvt_date = int(min(hvt_date, review_date))
    failed_breakout = 0
    if len(ctx.hvth):
        hh = ctx.hvth[ctx.hvth["trade_date"] == hvt_date]
        failed_breakout = int((hh["breakout_state"] == "BREAKOUT_FAILED").sum())

    if lu_strength >= EARN["limit_up_strength_full"] and np.isfinite(med) and med > 0:
        earn_effect = "STRONG_EARNING"
    elif ld_pressure >= EARN["limit_down_pressure_heavy"]:
        earn_effect = "WEAK_EARNING"
    else:
        earn_effect = "NEUTRAL_EARNING"

    div_flags = []
    if (np.isfinite(fnum(idx_ret.get(MKT["benchmark_index"], np.nan)))
            and fnum(idx_ret.get(MKT["benchmark_index"])) > EARN["index_up_breadth_down_min_index_ret"]
            and np.isfinite(fnum(breadth_change))
            and breadth_change <= EARN["index_up_breadth_down_max_breadth_delta"]):
        div_flags.append("INDEX_UP_BREADTH_DOWN")
    strong_theme_count = int(theme_summary.get("strong_or_rotation_in", 0))
    bm_ret = fnum(idx_ret.get(MKT["benchmark_index"], np.nan))
    if (np.isfinite(bm_ret) and bm_ret <= EARN["index_weak_max_index_ret"]
            and strong_theme_count >= EARN["index_weak_min_strong_theme_count"]):
        div_flags.append("INDEX_WEAK_THEME_STRONG")

    # 市场结构标签（§八）
    tags = []
    if np.isfinite(fnum(amount_ratio20)) and amount_ratio20 <= STAG["low_participation_amount_ratio"]:
        tags.append("LOW_PARTICIPATION")
    if (abs(fnum(idx_ret.get(MKT["benchmark_index"], np.nan))) >= STAG["high_volatility_abs_index_ret"]
            or fnum(sse_amp) >= STAG["high_volatility_index_amplitude"]
            or lu >= STAG["high_volatility_min_limit_up"]):
        tags.append("HIGH_VOLATILITY")
    if div_flags:
        tags.append("DIVERGENCE")
    if bm_ret > 0 and breadth >= STAG["broad_ratio_min"]:
        tags.append("BROAD_RALLY")
    if bm_ret < 0 and breadth <= STAG["narrow_ratio_max"]:
        tags.append("BROAD_DECLINE")
    if bm_ret > 0 and breadth <= STAG["narrow_ratio_max"]:
        tags.append("NARROW_RALLY")
    if bm_ret < 0 and breadth >= STAG["broad_ratio_min"]:
        tags.append("NARROW_DECLINE")
    if int(theme_summary.get("rotation_in", 0)) >= STAG["rotation_in_min_count"]:
        tags.append("STRUCTURAL_ROTATION")
    primary_tag = ""
    for t in STAG["priority"]:
        if t in tags:
            primary_tag = t
            break
    if not primary_tag:
        primary_tag = "NO_TAG"

    max_board = max_consecutive_board(store, review_date, ctx.name_map)

    rec = {
        "trade_date": review_date,
        "signal_date": signal_date,
        "market_regime_date": reg_date,
        "index_ret_SSE": idx_ret.get("SSE"),
        "index_ret_CSI300": idx_ret.get("CSI300"),
        "index_ret_CSI1000": idx_ret.get("CSI1000"),
        "index_ret_CSI2000": idx_ret.get("CSI2000"),
        "index_amplitude_SSE": sse_amp,
        "market_amount_yi": amount_yi,
        "market_amount_change": amount_change,
        "market_amount_ma20_yi": jnum(amount_ma20 / 1e5 if np.isfinite(fnum(amount_ma20)) else np.nan, 2),
        "market_amount_ratio_20": amount_ratio20,
        "up_count": up, "down_count": dn, "flat_count": flat,
        "breadth": breadth, "weighted_breadth": wb,
        "breadth_before": prev_breadth, "breadth_change": breadth_change,
        "limit_up": lu, "limit_down": ld, "failed_limit_up": flu,
        "max_consecutive_board": max_board,
        "median_stock_return": med, "top10_return": top10, "bottom10_return": bot10,
        "top_decile_return": top_dec, "bottom_decile_return": bot_dec,
        "limit_up_strength": lu_strength, "limit_down_pressure": ld_pressure,
        "high_open_low_close": holc, "low_open_high_close": lohc,
        "failed_breakout": failed_breakout, "failed_breakout_date": hvt_date,
        "market_earning_effect": earn_effect,
        "market_structure_tag": primary_tag,
        "market_structure_tags": "|".join(tags) if tags else "NO_TAG",
        "market_structure_tags_secondary": "|".join([t for t in tags if t != primary_tag]),
        "divergence_flag": "|".join(div_flags),
        "market_regime": regime_now, "market_regime_before": regime_before,
        "market_regime_change": f"{regime_before}->{regime_now}" if regime_before else regime_now,
        "regime_change_reason": regime_reason,
        "execution_mode": jstr(reg.get("execution_mode")),
        "rows": n_all,
    }
    # 覆盖统计只取 <= review_date 的 Step 7 日期（§五十三 防马后炮）
    rm_upto = {d: v for d, v in regime_map.items() if int(d) <= int(review_date)}
    observed_rm = {jstr(v.get("market_regime")) for v in rm_upto.values() if jstr(v.get("market_regime"))}
    rec["regime_values_observed"] = "|".join(sorted(observed_rm))
    rec["regime_missing_values"] = "|".join([r for r in MKT["regime_values"] if r not in observed_rm])
    status = LEVEL_PASS if n_all > 0 and up + dn + flat > 0 else LEVEL_WARN
    if reg_date != review_date:
        val.append({"level": LEVEL_WARN, "code": "MARKET_REGIME_DATE_LAG",
                    "message": f"market_regime 取最近可得日 {reg_date}（Step 7 未覆盖 {review_date}），"
                               f"已在报告标注（§五：直接读取不重复计算）"})
    if len(idx_ret) < len(MKT["index_codes"]):
        miss = [k for k in MKT["index_codes"] if not np.isfinite(fnum(idx_ret.get(k, np.nan)))]
        val.append({"level": LEVEL_WARN, "code": "MARKET_INDEX_MISSING",
                    "message": f"指数缺失 {miss}"})
    rec["data_status"] = status
    return rec


# ══════════════════════════════════════════════════════════════════════════
# 4. 主题层（§九 ~ §十四）
# ══════════════════════════════════════════════════════════════════════════
def theme_strength(row) -> float:
    w = THEME["strength_weights"]
    sc = THEME["strength_scale"]
    parts, wsum = 0.0, 0.0
    for col, wt in w.items():
        lo, hi = sc[col]
        v = norm01(row.get(col), lo, hi)
        if np.isfinite(fnum(v)):
            parts += wt * float(v)
            wsum += wt
    return round(parts / wsum, 6) if wsum > 0 else np.nan


def build_theme(ctx: Ctx, theme_date: int, signal_date: int, val: list) -> dict:
    if not len(ctx.seos):
        val.append({"level": LEVEL_FAIL, "code": "THEME_DATA", "message": "sector_seos_daily 为空"})
        return {"theme_date": theme_date, "rows": [], "summary": {}, "data_status": LEVEL_FAIL}
    d = ctx.seos[ctx.seos["trade_date"] == theme_date].copy()
    if not len(d):
        val.append({"level": LEVEL_FAIL, "code": "THEME_DATA",
                    "message": f"{theme_date} 无主题层数据"})
        return {"theme_date": theme_date, "rows": [], "summary": {}, "data_status": LEVEL_FAIL}
    ph = ctx.ph[ctx.ph["trade_date"] == theme_date] if len(ctx.ph) else pd.DataFrame()
    rot = ctx.rot[ctx.rot["trade_date"] == theme_date] if len(ctx.rot) else pd.DataFrame()
    dif = ctx.dif[ctx.dif["trade_date"] == theme_date] if len(ctx.dif) else pd.DataFrame()
    d = d.merge(ph[["sector_id", "phase_transition", "phase_changed"]]
                if len(ph) else pd.DataFrame(columns=["sector_id", "phase_transition", "phase_changed"]),
                on="sector_id", how="left", suffixes=("", "_ph"))
    d = d.merge(rot[["sector_id", "rotation_signal", "rotation_reason"]]
                if len(rot) else pd.DataFrame(columns=["sector_id", "rotation_signal", "rotation_reason"]),
                on="sector_id", how="left", suffixes=("", "_rot"))
    if len(dif):
        d = d.merge(dif[["sector_id", "theme_bucket", "diffusion_type", "diffusion_pattern",
                         "diffusion_stage", "candidate_count"]],
                    on="sector_id", how="left")
    else:
        for c in ("theme_bucket", "diffusion_type", "diffusion_pattern", "diffusion_stage",
                  "candidate_count"):
            d[c] = ""
    rows = []
    unknown = []
    for _, r in d.iterrows():
        sid = jstr(r.get("sector_id"))
        if sid not in ctx.theme_ids:
            unknown.append(sid)
        prev_phase = jstr(r.get("prev_phase"))
        cur_phase = jstr(r.get("theme_phase"))
        startup_outcome = ""
        if prev_phase in THEME["startup_phases"]:
            startup_outcome = THEME["startup_outcome_map"].get(f"{prev_phase}>{cur_phase}", "unchanged")
        groups = []
        if cur_phase in THEME["strong_continuing_phases"]:
            groups.append("STRONG_CONTINUING")
        if (fnum(r.get("theme_health_delta_1"), -1) > THEME["improving_min_health_delta"]
                and fnum(r.get("breadth_delta_1"), -1) > THEME["improving_min_breadth_delta"]):
            groups.append("IMPROVING")
        if jstr(r.get("rotation_signal")) == "ROTATION_IN":
            groups.append("ROTATION_IN")
        if cur_phase == "COOLING" or (cur_phase in THEME["strong_continuing_phases"]
                                      and fnum(r.get("theme_health_delta_3"), 1) <= THEME["cooling_max_health_delta_3"]):
            groups.append("COOLING")
        if cur_phase in THEME["deteriorating_phases"]:
            groups.append("DETERIORATING")
        if jstr(r.get("startup_quality")) in THEME["failed_startup_qualities"]:
            groups.append("FAILED_STARTUP")
        dif_type = jstr(r.get("diffusion_type"))
        rows.append({
            "trade_date": theme_date,
            "signal_date": signal_date,
            "sector_id": sid,
            "sector_name": jstr(r.get("sector_name")),
            "rotation_group": jstr(r.get("rotation_group")),
            "theme_phase_before": prev_phase,
            "theme_phase_after": cur_phase,
            "phase_transition": jstr(r.get("phase_transition")),
            "phase_duration": int(fnum(r.get("phase_duration"), 0)),
            "theme_health": fnum(r.get("theme_health")),
            "health_change": fnum(r.get("theme_health_delta_1")),
            "seos_score": fnum(r.get("seos_score")),
            "seos_change": fnum(r.get("theme_health_delta_1")),
            "breadth": fnum(r.get("breadth")),
            "breadth_change": fnum(r.get("breadth_delta_1")),
            "core_breadth": fnum(r.get("core_breadth")),
            "core_breadth_change": fnum(r.get("core_breadth_delta_1")),
            "primary_breadth": fnum(r.get("primary_breadth")),
            "primary_breadth_change": fnum(r.get("primary_breadth_delta_3")),
            "secondary_breadth": fnum(r.get("secondary_breadth")),
            "secondary_breadth_change": fnum(r.get("secondary_breadth_delta_3")),
            "relative_strength": fnum(r.get("relative_strength")),
            "relative_strength_change": fnum(r.get("relative_strength_delta_3")),
            "amount_share": fnum(r.get("theme_amount_share")),
            "amount_share_change": fnum(r.get("amount_share_delta_3")),
            "volume_ratio_1": fnum(r.get("volume_ratio_1")),
            "volume_participation_change": fnum(r.get("volume_ratio_3")),
            "core_pattern": jstr(r.get("core_pattern")),
            "startup_quality": jstr(r.get("startup_quality")),
            "startup_outcome": startup_outcome,
            "rotation_signal": jstr(r.get("rotation_signal")),
            "rotation_reason": jstr(r.get("rotation_reason")),
            "theme_bucket": jstr(r.get("theme_bucket")),
            "diffusion_type": dif_type,
            "diffusion_pattern": jstr(r.get("diffusion_pattern")),
            "diffusion_stage": jstr(r.get("diffusion_stage")),
            "diffusion_review": THEME["diffusion_review_map"].get(dif_type, ""),
            "candidate_count": int(fnum(r.get("candidate_count"), 0)),
            "theme_group": "|".join(groups),
            "known_at": "KNOWN_AT_SIGNAL",
            "data_quality": fnum(r.get("data_quality_score")),
        })
    for r in rows:
        r["theme_strength_score"] = theme_strength(r)
    rows.sort(key=lambda x: fnum(x.get("theme_strength_score"), -1), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    summary = {
        "n_themes": len(rows),
        "strong_continuing": sum(1 for r in rows if "STRONG_CONTINUING" in r["theme_group"]),
        "improving": sum(1 for r in rows if "IMPROVING" in r["theme_group"]),
        "rotation_in": sum(1 for r in rows if "ROTATION_IN" in r["theme_group"]),
        "cooling": sum(1 for r in rows if "COOLING" in r["theme_group"]),
        "deteriorating": sum(1 for r in rows if "DETERIORATING" in r["theme_group"]),
        "failed_startup": sum(1 for r in rows if "FAILED_STARTUP" in r["theme_group"]),
        "strong_or_rotation_in": sum(1 for r in rows if ("STRONG_CONTINUING" in r["theme_group"]
                                                         or "ROTATION_IN" in r["theme_group"])),
    }
    status = LEVEL_PASS
    if unknown:
        status = LEVEL_FAIL
        val.append({"level": LEVEL_FAIL, "code": "THEME_UNKNOWN",
                    "message": f"出现未知主题（不在 theme_master）：{sorted(set(unknown))[:10]}"})
    if theme_date != signal_date:
        val.append({"level": LEVEL_WARN, "code": "THEME_DATE_LAG",
                    "message": f"主题层取最近可得日 {theme_date}（signal_date={signal_date}）"})
    return {"theme_date": theme_date, "rows": rows, "summary": summary, "data_status": status}


# ══════════════════════════════════════════════════════════════════════════
# 5. 个股层：Candidate / Structure / HVT（§十五 ~ §二十）
# ══════════════════════════════════════════════════════════════════════════
def mark_hvt_outcome(row, ret_1, ma20_break) -> str:
    r = fnum(ret_1, np.nan)
    if jstr(row.get("hvt_state")) in HVTC["failed_states"]:
        return "failed"
    if not np.isfinite(r):
        return ""
    if r <= HVTC["failed_ret_1_max"] or ma20_break:
        return "failed"
    if r >= HVTC["extended_ret_1_min"]:
        return "extended"
    if HVTC["confirmed_ret_1_min"] < r < HVTC["confirmed_ret_1_max"]:
        return "confirmed"
    return "continued"


def classify_breakout(gap, close_ret, vol_chg) -> str:
    r = fnum(close_ret, np.nan)
    if not np.isfinite(r):
        return ""
    if r >= BKOC["extension_min_close_return"]:
        return "BREAKOUT_EXTENSION"
    if r >= BKOC["success_min_close_return"]:
        return "BREAKOUT_SUCCESS"
    if r > BKOC["continuation_min_close_return"]:
        return "BREAKOUT_CONTINUATION"
    if r <= BKOC["failed_max_close_return"]:
        return "BREAKOUT_FAILED"
    return "BREAKOUT_CHOP"


def build_structure_layer(store: BarStore, ctx: Ctx, signal_date: int, horizons,
                          val: list) -> dict:
    src = ctx.exec[ctx.exec["trade_date"] == signal_date].copy()
    if not len(src):
        val.append({"level": LEVEL_FAIL, "code": "STRUCTURE_DATA",
                    "message": f"{signal_date} 无 Step 6/7 结构记录"})
        return {"rows": [], "summary": {}, "data_status": LEVEL_FAIL}
    hv = ctx.hvth[ctx.hvth["trade_date"] == signal_date] if len(ctx.hvth) else pd.DataFrame()
    if len(hv):
        src = src.merge(hv[["ts_code", "hvt_stage", "hvt_days_after", "adjustment_status",
                            "locking_status"]], on="ts_code", how="left")
    else:
        for c in ("hvt_stage", "hvt_days_after", "adjustment_status", "locking_status"):
            src[c] = ""
    rk = ctx.breakout[ctx.breakout["trade_date"] == signal_date] if len(ctx.breakout) else pd.DataFrame()
    if len(rk):
        src = src.merge(rk[["ts_code", "breakout_level", "breakout_days_ago",
                            "breakout_volume_ratio", "retest_depth", "retest_volume_ratio"]],
                        on="ts_code", how="left", suffixes=("", "_bko"))
    else:
        for c in ("breakout_level", "breakout_days_ago", "breakout_volume_ratio",
                  "retest_depth", "retest_volume_ratio"):
            src[c] = np.nan
    rt = ctx.retest[ctx.retest["trade_date"] == signal_date] if len(ctx.retest) else pd.DataFrame()
    if len(rt):
        src = src.merge(rt[["ts_code", "retest_level", "retest_hold", "retest_quality",
                            "retest_volume_ratio"]], on="ts_code", how="left",
                        suffixes=("", "_rt"))
    else:
        for c in ("retest_level", "retest_hold", "retest_quality"):
            src[c] = ""
    st = ctx.structure[ctx.structure["trade_date"] == signal_date] if len(ctx.structure) else pd.DataFrame()
    if len(st):
        src = src.merge(st[["ts_code", "ma20", "pct_chg"]], on="ts_code", how="left",
                        suffixes=("", "_st"))

    codes = src["ts_code"].tolist()
    rets = store.ret_panel(signal_date, codes, horizons)
    w_close = store.wide("close")
    w_ma = store.wide("adj_close")
    ma20_map = {}
    if len(st):
        ma20_map = dict(zip(st["ts_code"], st["ma20"]))
    ret1 = rets.get(1, {})
    w_hi, w_lo = store.wide("adj_high"), store.wide("adj_low")
    fwd1 = store.next_days(signal_date, 1)
    hi1 = w_hi.loc[fwd1[0]] if fwd1 else None
    lo1 = w_lo.loc[fwd1[0]] if fwd1 else None
    adj1 = store.wide("adj_close").loc[fwd1[0]] if fwd1 else None

    rows = []
    for _, r in src.iterrows():
        code = str(r["ts_code"])
        adj_sig = fnum(r.get("adj_factor"), 1.0)
        ma20_raw = fnum(ma20_map.get(code), np.nan)
        adj_close_1 = fnum(adj1.get(code)) if adj1 is not None else np.nan
        ma20_break = bool(np.isfinite(adj_close_1) and np.isfinite(ma20_raw)
                          and adj_close_1 < ma20_raw * adj_sig)
        r1 = fnum(ret1.get(code))
        hi_r = lo_r = np.nan
        if hi1 is not None:
            sig_close = fnum(store.wide("adj_close").loc[signal_date].get(code))
            hi_r = pct_ret(hi1.get(code), sig_close)
            lo_r = pct_ret(lo1.get(code), sig_close)
        gap = np.nan
        if fwd1:
            sig_close = fnum(store.wide("adj_close").loc[signal_date].get(code))
            gap = pct_ret(store.wide("adj_open").loc[fwd1[0]].get(code), sig_close)
        v_sig = fnum(store.wide("vol").loc[signal_date].get(code))
        v_1 = fnum(store.wide("vol").loc[fwd1[0]].get(code)) if fwd1 else np.nan
        vol_chg = safe_div(v_1, v_sig, np.nan)
        ret_1_sig = fnum(r.get("pct_chg"), np.nan) / 100.0
        ret_1_sig = fnum(ret_1_sig)
        row = {
            "trade_date": store.date_max if fwd1 is None else fwd1[0],
            "signal_date": signal_date,
            "ts_code": code,
            "name": jstr(r.get("name")) or jstr(ctx.name_map.get(code)),
            "primary_theme_id": jstr(r.get("primary_theme_id")),
            "candidate_type": jstr(r.get("candidate_type")),
            "structure_state": jstr(r.get("structure_state")),
            "structure_class": jstr(r.get("structure_class")),
            "structure_qualification": jstr(r.get("structure_qualification")),
            "structure_quality": fnum(r.get("structure_quality")),
            "extension_risk": jstr(r.get("extension_risk")),
            "hvt_state": jstr(r.get("hvt_state")),
            "hvt_stage": jstr(r.get("hvt_stage")),
            "breakout_state": jstr(r.get("breakout_state")),
            "retest_state": jstr(r.get("retest_state")),
            "volume_state": jstr(r.get("volume_state")),
            "ret_1": r1,
            "ret_3": rets.get(3, {}).get(code),
            "ret_5": rets.get(5, {}).get(code),
            "ret_10": rets.get(10, {}).get(code),
            "ret_20": rets.get(20, {}).get(code),
            "high_return_1": hi_r, "low_return_1": lo_r, "gap_1": gap,
            "volume_change_1": vol_chg,
            "signal_day_return": ret_1_sig,
            "ma20_break": ma20_break,
            "breakout_result": "",
            "retest_hold": "",
            "close_position": np.nan,
            "data_quality": jstr(r.get("data_quality")),
        }
        row["hvt_outcome"] = mark_hvt_outcome(r, r1, ma20_break)
        if jstr(r.get("breakout_state")) in BKOC["confirmed_states"]:
            row["breakout_result"] = classify_breakout(gap, r1, vol_chg)
        rl = fnum(r.get("retest_level"), np.nan)
        if jstr(r.get("retest_state")) in RTC["states"] and np.isfinite(rl):
            row["retest_hold"] = "HOLD" if (np.isfinite(fnum(ret1.get(code)))
                                            and 1 + fnum(ret1.get(code)) >= 1 - RTC["hold_depth_max"]) else "BREAK"
        if fwd1 is not None:
            h, l, c = fnum(hi1.get(code)), fnum(lo1.get(code)), fnum(adj1.get(code))
            row["close_position"] = safe_div(c - l, h - l, np.nan) if h != l else np.nan
        rows.append(row)
    if not rows:
        val.append({"level": LEVEL_FAIL, "code": "STRUCTURE_DATA",
                    "message": f"{signal_date} 结构复盘 0 行"})
        return {"rows": [], "summary": {}, "data_status": LEVEL_FAIL}
    summary = {}
    for f in STRC["group_fields"]:
        g = {}
        for k, sub in pd.DataFrame(rows).groupby(f, dropna=False):
            g[jstr(k)] = stat_block(sub["ret_1"].tolist())
        summary[f] = g
    status = LEVEL_PASS
    if any(not jstr(r["hvt_state"]) for r in rows):
        val.append({"level": LEVEL_WARN, "code": "HVT_STATE_MISSING",
                    "message": "部分结构行 hvt_state 为空（§四十九-5）"})
    return {"rows": rows, "summary": summary, "data_status": status}


# ══════════════════════════════════════════════════════════════════════════
# 6. Candidate 层（§十五 / §十六）
# ══════════════════════════════════════════════════════════════════════════
def benchmark_ret(store: BarStore, base_date: int, horizons) -> dict:
    w = store.wide("adj_close")
    if base_date not in w.index:
        return {h: np.nan for h in horizons}
    base = w.loc[base_date]
    fwd = store.next_days(base_date, max(horizons) if horizons else 0)
    out = {}
    for h in horizons:
        if h > len(fwd):
            out[h] = np.nan
            continue
        r = (w.loc[fwd[h - 1]] / base) - 1.0
        out[h] = None if not len(r.dropna()) else round(float(r.dropna().mean()), 6)
    return out


def build_candidate_layer(store: BarStore, ctx: Ctx, cand_date: int, signal_date: int,
                          horizons, bench: dict, val: list) -> dict:
    c = ctx.cand[ctx.cand["trade_date"] == cand_date]
    c = c[c["candidate_status"].isin(CANDC["accepted_status"])]
    if not len(c):
        val.append({"level": LEVEL_WARN, "code": "CANDIDATE_EMPTY",
                    "message": f"{cand_date} 无 Candidate（允许为 0，不为凑数量降低门槛）"})
        return {"rows": [], "by_type": {}, "data_status": LEVEL_PASS}
    codes = c["ts_code"].tolist()
    rets = store.ret_panel(cand_date, codes, horizons)
    rows = []
    for _, r in c.iterrows():
        code = str(r["ts_code"])
        rec = {
            "trade_date": store.date_max if not store.next_days(cand_date, 1) else store.next_days(cand_date, 1)[0],
            "signal_date": signal_date,
            "candidate_date": cand_date,
            "ts_code": code,
            "name": jstr(r.get("name")) or jstr(ctx.name_map.get(code)),
            "sector_id": jstr(r.get("sector_id")),
            "sector_name": jstr(r.get("sector_name")),
            "rotation_group": jstr(r.get("rotation_group")),
            "candidate_type": jstr(r.get("candidate_type")),
            "candidate_status": jstr(r.get("candidate_status")),
            "candidate_score": fnum(r.get("candidate_score")),
            "membership_type": jstr(r.get("membership_type")),
            "theme_role": jstr(r.get("theme_role")),
            "theme_phase": jstr(r.get("theme_phase")),
            "theme_seos": fnum(r.get("theme_seos")),
            "risk_flags": jstr(r.get("risk_flags")),
            "data_quality": jstr(r.get("data_quality")),
        }
        for h in horizons:
            rec[f"ret_{h}"] = rets.get(h, {}).get(code)
            rec[f"benchmark_ret_{h}"] = bench.get(h)
            rec[f"excess_ret_{h}"] = (fnum(rets.get(h, {}).get(code)) - fnum(bench.get(h), np.nan)
                                      if np.isfinite(fnum(rets.get(h, {}).get(code)))
                                      and np.isfinite(fnum(bench.get(h), np.nan)) else np.nan)
        r1 = fnum(rec["ret_1"], np.nan)
        rec["outcome_class"] = ("UP" if r1 > 0 else "DOWN") if np.isfinite(r1) else ""
        rows.append(rec)
    df = pd.DataFrame(rows)
    by_type = {}
    for t, sub in df.groupby("candidate_type"):
        blk = stat_block(sub["ret_1"].tolist()) if "ret_1" in sub else {}
        blk["mean_ret_5"] = mean_of(sub["ret_5"].tolist()) if "ret_5" in sub else None
        blk["mean_ret_20"] = mean_of(sub["ret_20"].tolist()) if "ret_20" in sub else None
        by_type[jstr(t)] = blk
    return {"rows": rows, "by_type": by_type, "data_status": LEVEL_PASS}


# ══════════════════════════════════════════════════════════════════════════
# 7. 交易层：BUY 复盘 + NO TRADE 复盘（§二十一 ~ §二十六）
# ══════════════════════════════════════════════════════════════════════════
def buy_result_class(touched, chase, close_ret_entry, close_px, stop_px) -> str:
    if not touched:
        return "BUY_NO_CHASE" if chase else "BUY_NO_ENTRY"
    if np.isfinite(fnum(close_px)) and np.isfinite(fnum(stop_px)) and fnum(close_px) <= fnum(stop_px):
        return "BUY_FAILED"
    if np.isfinite(fnum(close_ret_entry)) and fnum(close_ret_entry) > BUYC["success_min_close_return"]:
        return "BUY_SUCCESS"
    return "BUY_TRIGGERED"


def no_trade_class(reason) -> str:
    return NTC["reason_map"].get(jstr(reason), "")


def no_trade_outcome(cls, r1, entry_state) -> str:
    """§二十五 / §二十六：只按「如果不买、今天表现如何」分类，不把 Missed 直接当错误。"""
    r = fnum(r1, np.nan)
    if not np.isfinite(r):
        return NTC["unresolved_outcome"]
    if cls == "NO_TRADE_DUE_TO_EXTENSION" and r <= NTC["correct_no_trade_max_return"]:
        return "CORRECT_NO_TRADE"
    if r >= NTC["missed_opportunity_min_return"]:
        return "MISSED_OPPORTUNITY"
    if jstr(entry_state) in NTC["waiting_states"]:
        return "WAITING_CORRECTLY"
    if r <= NTC["correct_no_trade_max_return"]:
        return "CORRECT_NO_TRADE"
    return NTC["neutral_outcome"]


def attribute_error(row, ctx: Ctx) -> tuple:
    """错误归因（§二十七 / §二十八 / §二十九）：只按规则违反判定，不凭结果倒推。"""
    act = jstr(row.get("action"))
    if act == "BUY":
        missing = [f for f in VALC["buy_required_fields"] if not np.isfinite(fnum(row.get(f), np.nan))]
        if missing:
            return "DATA_ERROR", "P0", f"BUY 缺失必需字段 {missing}", True
        if jstr(row.get("structure_qualification")) not in SCC["accepted_qualifications"]:
            return ("STRUCTURE_ERROR", "P1",
                    f"BUY 时 structure_qualification={jstr(row.get('structure_qualification'))} 不在接受集合", True)
        if jstr(row.get("hvt_state")) in HVTC["failed_states"]:
            return ("HVT_ERROR", "P1", f"BUY 时 hvt_state={jstr(row.get('hvt_state'))}（失效结构未恢复）", True)
        if not np.isfinite(fnum(row.get("risk_reward"), np.nan)):
            return "RISK_REWARD_ERROR", "P1", "BUY 缺少 risk_reward", True
        if jstr(row.get("extension_risk")) == "EXTREME":
            return "EXTENSION_ERROR", "P1", "BUY 时 extension_risk=EXTREME（巨量不追原则冲突）", True
        if jstr(row.get("market_regime")) == "RISK_OFF" and jstr(row.get("execution_mode")) == "DEFENSIVE":
            return "MARKET_REGIME_ERROR", "P1", "BUY 发生在 RISK_OFF / DEFENSIVE 环境下", True
        return "NO_ERROR", "", "", False
    if act == "NO TRADE":
        if not jstr(row.get("primary_no_trade_reason")):
            return "DATA_ERROR", "P0", "NO TRADE 缺少 primary_no_trade_reason", True
        return "NO_ERROR", "", "", False
    return "NO_ERROR", "", "", False


def build_execution_layer(store: BarStore, ctx: Ctx, signal_date: int, review_date: int,
                          horizons, val: list) -> dict:
    src = ctx.exec[ctx.exec["trade_date"] == signal_date].copy()
    if not len(src):
        val.append({"level": LEVEL_FAIL, "code": "EXECUTION_DATA",
                    "message": f"{signal_date} 无 Step 7 执行记录"})
        return {"rows": [], "buy": [], "no_trade_summary": {}, "data_status": LEVEL_FAIL}
    fwd = store.next_days(signal_date, max(horizons) if horizons else 1)
    t1 = fwd[0] if fwd else None
    t1_bars = store.bars_at(t1) if t1 else pd.DataFrame()
    b1 = t1_bars.set_index("ts_code") if len(t1_bars) else pd.DataFrame()
    w_adj = store.wide("adj_close")
    sig_adj = w_adj.loc[signal_date] if signal_date in w_adj.index else None

    ref_adj = {}
    codes = src["ts_code"].tolist()
    for _, r in src.iterrows():
        if jstr(r.get("action")) == "BUY":
            ref = fnum(r.get(BUYC["fill_reference"]), np.nan)
            af = fnum(r.get("adj_factor"), 1.0)
            ref_adj[str(r["ts_code"])] = ref * af if np.isfinite(ref) else np.nan
    paths = store.path_stats(signal_date, list(ref_adj.keys()), MAEC["windows"], ref_adj) if ref_adj else {}
    rets = store.ret_panel(signal_date, codes, horizons)

    rows, buy_rows = [], []
    nt_cls_count, nt_out_count = {}, {}
    for _, r in src.iterrows():
        code = str(r["ts_code"])
        act = jstr(r.get("action"))
        ent_low, ent_high = fnum(r.get("entry_low"), np.nan), fnum(r.get("entry_high"), np.nan)
        o = fnum(b1.loc[code, "open"]) if len(b1) and code in b1.index else np.nan
        h = fnum(b1.loc[code, "high"]) if len(b1) and code in b1.index else np.nan
        lo = fnum(b1.loc[code, "low"]) if len(b1) and code in b1.index else np.nan
        c = fnum(b1.loc[code, "close"]) if len(b1) and code in b1.index else np.nan
        sig_close_raw = fnum(r.get("raw_close"), np.nan)
        today_ret = pct_ret(c, sig_close_raw)
        gap = pct_ret(o, sig_close_raw)
        touched = bool(np.isfinite(h) and np.isfinite(lo) and np.isfinite(ent_low)
                       and np.isfinite(ent_high) and lo <= ent_high and h >= ent_low)
        chase = bool(np.isfinite(lo) and np.isfinite(ent_high) and lo > ent_high)
        if (np.isfinite(o) and np.isfinite(ent_high) and BUYC["chase_gap_over_entry_high"]
                and o >= ent_high * (1 + BUYC["no_chase_gap_pct"])):
            chase = True
        entry_ref = fnum(r.get(BUYC["fill_reference"]), np.nan)
        close_ret_entry = pct_ret(c, entry_ref) if (touched and np.isfinite(entry_ref)) else np.nan
        result = buy_result_class(touched, chase, close_ret_entry, c, r.get("stop_price")) if act == "BUY" else ""
        e_cls, e_sev, e_ev, e_model = attribute_error(r, ctx)
        rec = {
            "trade_date": t1 if t1 else review_date,
            "signal_date": signal_date,
            "ts_code": code,
            "name": jstr(r.get("name")) or jstr(ctx.name_map.get(code)),
            "action": act,
            "entry_state": jstr(r.get("entry_state")),
            "entry_type": jstr(r.get("entry_type")),
            "structure_state": jstr(r.get("structure_state")),
            "structure_class": jstr(r.get("structure_class")),
            "structure_qualification": jstr(r.get("structure_qualification")),
            "hvt_state": jstr(r.get("hvt_state")),
            "breakout_state": jstr(r.get("breakout_state")),
            "retest_state": jstr(r.get("retest_state")),
            "extension_risk": jstr(r.get("extension_risk")),
            "market_regime": jstr(r.get("market_regime")),
            "execution_mode": jstr(r.get("execution_mode")),
            "risk_reward": fnum(r.get("risk_reward")),
            "trigger_price": fnum(r.get("trigger_price")),
            "entry_low": ent_low, "entry_high": ent_high,
            "entry_ref": entry_ref,
            "stop_price": fnum(r.get("stop_price")),
            "target_1": fnum(r.get("target_1")),
            "price_basis": jstr(r.get("price_basis")),
            "today_open": o, "today_high": h, "today_low": lo, "today_close": c,
            "today_return": today_ret, "gap_open": gap,
            "entry_zone_touch": touched, "chase_flag": chase,
            "result_class": result, "return_basis": "ADJ(review) / RAW(bar)",
            "close_ret_vs_entry": close_ret_entry,
            "no_trade_class": no_trade_class(r.get("primary_no_trade_reason")) if act == "NO TRADE" else "",
            "no_trade_reason_raw": jstr(r.get("primary_no_trade_reason")),
            "error_class": e_cls, "error_severity": e_sev, "rule_violated": e_ev,
            "is_model_error": e_model, "known_at": "KNOWN_AT_SIGNAL",
            "gate_failures": jstr(r.get("gate_failures")),
            "execution_grade": jstr(r.get("execution_grade")),
            "signal_return_1": rets.get(1, {}).get(code),
            "signal_return_5": rets.get(5, {}).get(code),
            "signal_return_20": rets.get(20, {}).get(code),
        }
        if act == "NO TRADE":
            rec["no_trade_outcome"] = no_trade_outcome(rec["no_trade_class"],
                                                       rec["signal_return_1"], r.get("entry_state"))
            nt_cls_count[rec["no_trade_class"] or "UNMAPPED"] = nt_cls_count.get(rec["no_trade_class"] or "UNMAPPED", 0) + 1
            nt_out_count[rec["no_trade_outcome"] or "UNMAPPED"] = nt_out_count.get(rec["no_trade_outcome"] or "UNMAPPED", 0) + 1
        else:
            rec["no_trade_outcome"] = ""
        if act == "BUY":
            for w in MAEC["windows"]:
                mae, mfe, cret = paths.get(code, {}).get(w, (np.nan, np.nan, np.nan))
                rec[f"mae_{w}"], rec[f"mfe_{w}"], rec[f"close_ret_{w}"] = mae, mfe, cret
            rec["stop_hit"] = bool(np.isfinite(fnum(rec.get(f"mae_1"), np.nan))
                                   and np.isfinite(fnum(r.get("stop_price"), np.nan))
                                   and np.isfinite(entry_ref) and entry_ref > 0
                                   and abs(fnum(rec.get("mae_1"))) >= abs(safe_div(
                                       entry_ref - fnum(r.get("stop_price")), entry_ref, 0.0)))
            rec["target_hit"] = bool(np.isfinite(fnum(rec.get("mfe_1"), np.nan))
                                     and np.isfinite(fnum(r.get("target_1"), np.nan))
                                     and np.isfinite(entry_ref) and entry_ref > 0
                                     and fnum(rec.get("mfe_1")) >= safe_div(
                                         fnum(r.get("target_1")) - entry_ref, entry_ref, 0.0))
            rec["result_reason"] = (
                f"entry_zone {jnum(ent_low,2)}~{jnum(ent_high,2)}；today low {jnum(lo,2)} / high {jnum(h,2)}；"
                f"touch={touched}；close {jnum(c,2)} vs entry_ref {jnum(entry_ref,2)}；"
                f"stop {jnum(fnum(r.get('stop_price')),2)}")
            buy_rows.append(rec)
        rows.append(rec)
    df = pd.DataFrame(rows)
    for w in MAEC["windows"]:
        for col in (f"mae_{w}", f"mfe_{w}", f"close_ret_{w}"):
            if col not in df.columns:
                df[col] = np.nan
    if not np.isfinite(fnum(len(df))):
        pass
    status = LEVEL_PASS
    if len(df) and not np.isfinite(fnum(df["today_close"].notna().sum(), np.nan)):
        status = LEVEL_WARN
    if len(df):
        miss_bar = int(df["today_close"].isna().sum())
        if miss_bar:
            val.append({"level": LEVEL_WARN, "code": "EXECUTION_BAR_MISSING",
                        "message": f"{miss_bar} 只股票在 {t1} 无行情（停牌/退市），已如实留空"})
    bad_buy = [r["ts_code"] for r in buy_rows if r["error_class"] == "DATA_ERROR"]
    if bad_buy:
        val.append({"level": LEVEL_FAIL, "code": "BUY_FIELD_MISSING",
                    "message": f"BUY 缺 trigger/entry/stop/target/RR（§四十九-3）：{bad_buy[:10]}"})
        status = LEVEL_FAIL
    bad_nt = int(((df["action"] == "NO TRADE") & (df["no_trade_reason_raw"] == "")).sum()) if len(df) else 0
    if bad_nt:
        val.append({"level": LEVEL_FAIL, "code": "NO_TRADE_REASON_MISSING",
                    "message": f"{bad_nt} 条 NO TRADE 缺少 no_trade_reason（§四十九-4）"})
        status = LEVEL_FAIL
    nt_summary = {
        "total": int((df["action"] == "NO TRADE").sum()) if len(df) else 0,
        "by_class": nt_cls_count,
        "by_outcome": nt_out_count,
        "missed_ratio": (safe_div(nt_out_count.get("MISSED_OPPORTUNITY", 0), len(df[df["action"] == "NO TRADE"]), 0.0)
                         if len(df) else 0.0),
    }
    return {"rows": rows, "buy": buy_rows, "no_trade_summary": nt_summary,
            "data_status": status, "t1": t1, "execution_qualified_entry_states":
            SCC["execution_qualified_entry_states"]}


# ══════════════════════════════════════════════════════════════════════════
# 8. 诊断与反馈层（§二十七 ~ §三十九）
# ══════════════════════════════════════════════════════════════════════════
ERROR_LAYER = {
    "THEME_ERROR": "STEP3_4",
    "CANDIDATE_ERROR": "STEP5",
    "STRUCTURE_ERROR": "STEP6",
    "HVT_ERROR": "STEP6",
    "ENTRY_ERROR": "STEP7",
    "EXTENSION_ERROR": "STEP7",
    "MARKET_REGIME_ERROR": "STEP7",
    "RISK_REWARD_ERROR": "STEP7",
    "DATA_ERROR": "STEP7",
    "NO_ERROR": "-",
}


def _layer_of_error(cls: str) -> str:
    return ERROR_LAYER.get(jstr(cls), "-")


def layer_stat_block(store: BarStore, base_date: int, codes, horizons) -> dict:
    """某一层信号相对 base_date 的前向收益分布（§三十 / §三十一）。"""
    codes = [c for c in codes if jstr(c)]
    rets = store.ret_panel(base_date, codes, horizons) if codes else {h: {} for h in horizons}
    blk = {"n": len(codes)}
    for h in horizons:
        blk[f"ret_{h}"] = stat_block([rets.get(h, {}).get(c) for c in codes])
    return blk


def _blk_metric(blk: dict, h: int, key: str):
    return (blk.get(f"ret_{h}") or {}).get(key)


def multiframe_stat_block(store: BarStore, pairs, horizons) -> dict:
    """跨多个 signal_date 聚合的前向收益分布（§三十三：同一信号在不同 regime 下表现可能完全不同）。"""
    by_date: dict = {}
    for d, c in pairs:
        if jstr(c):
            by_date.setdefault(int(d), []).append(c)
    blk = {"n": sum(len(v) for v in by_date.values())}
    for h in horizons:
        vals = []
        for d, codes in by_date.items():
            rets = store.ret_panel(d, codes, horizons).get(h, {})
            vals.extend(rets.get(c) for c in codes)
        blk[f"ret_{h}"] = stat_block(vals)
    return blk


def signal_scorecard(store: BarStore, ctx: Ctx, cand_date: int, signal_date: int,
                     horizons, val: list) -> dict:
    """§三十 昨日信号 T+1 Scorecard：层层过滤是否提高收益分布质量。"""
    e = ctx.exec[ctx.exec["trade_date"] == signal_date] if len(ctx.exec) else pd.DataFrame()
    c = ctx.cand[ctx.cand["trade_date"] == cand_date] if len(ctx.cand) else pd.DataFrame()
    if len(c):
        c = c[c["candidate_status"].isin(CANDC["accepted_status"])]
    if not len(e):
        val.append({"level": LEVEL_WARN, "code": "SCORECARD_EMPTY",
                    "message": f"{signal_date} 无 Step 7 执行记录，Scorecard 仅 Candidate 层有效"})

    layers = {
        "CANDIDATE": layer_stat_block(store, cand_date, c["ts_code"].tolist() if len(c) else [], horizons),
        "STRUCTURE_QUALIFIED": layer_stat_block(
            store, signal_date,
            e[e["structure_qualification"].isin(SCC["accepted_qualifications"])]["ts_code"].tolist()
            if len(e) else [], horizons),
        "EXECUTION_QUALIFIED": layer_stat_block(
            store, signal_date,
            e[e["entry_state"].isin(SCC["execution_qualified_entry_states"])]["ts_code"].tolist()
            if len(e) else [], horizons),
        "BUY": layer_stat_block(store, signal_date,
                                e[e["action"] == "BUY"]["ts_code"].tolist() if len(e) else [], horizons),
        "NO_TRADE": layer_stat_block(store, signal_date,
                                     e[e["action"] == "NO TRADE"]["ts_code"].tolist() if len(e) else [],
                                     horizons),
    }
    rows = []
    for name in SCC["layers"]:
        b = layers.get(name, {})
        row = {"trade_date": signal_date, "signal_date": signal_date, "layer": name,
               "n": b.get("n", 0)}
        for h in horizons:
            s = b.get(f"ret_{h}") or {}
            row[f"win_rate_{h}"] = s.get("win_rate")
            row[f"mean_return_{h}"] = s.get("mean_return")
            row[f"median_return_{h}"] = s.get("median_return")
        rows.append(row)
    return {"rows": rows, "layers": layers, "cand_date": cand_date,
            "signal_date": signal_date, "data_status": LEVEL_PASS}


def build_funnel(sc: dict, val: list) -> dict:
    """§三十一 Funnel：Candidate → Structure Qualified → Execution Qualified → BUY。"""
    rows = []
    prev_mean = None
    for st in FUNC["stages"]:
        b = sc["layers"].get(st, {})
        m1, w1 = _blk_metric(b, 1, "mean_return"), _blk_metric(b, 1, "win_rate")
        imp = (fnum(m1) - fnum(prev_mean)) if (np.isfinite(fnum(m1))
                                              and np.isfinite(fnum(prev_mean))) else np.nan
        rows.append({"trade_date": sc["signal_date"], "signal_date": sc["signal_date"],
                     "stage": st, "n": b.get("n", 0), "win_rate": w1,
                     "mean_return": m1, "median_return": _blk_metric(b, 1, "median_return"),
                     "improvement_vs_prev": imp})
        prev_mean = m1
    base = _blk_metric(sc["layers"].get("STRUCTURE_QUALIFIED", {}), 1, "mean_return")
    buy = _blk_metric(sc["layers"].get("BUY", {}), 1, "mean_return")
    delta = (fnum(buy) - fnum(base)) if (np.isfinite(fnum(buy))
                                         and np.isfinite(fnum(base))) else np.nan
    if not np.isfinite(fnum(delta)):
        value_add = "INSUFFICIENT_SAMPLE"
    elif fnum(delta) >= FUNC["min_improvement"]:
        value_add = "POSITIVE"
    elif fnum(delta) > 0:
        value_add = "NEUTRAL"
    else:
        value_add = "NEGATIVE"
    if value_add == "NEGATIVE":
        val.append({"level": LEVEL_WARN, "code": "EXECUTION_LAYER_VALUE_ADD",
                    "message": "EXECUTION_LAYER_VALUE_ADD = NEGATIVE（BUY 不优于 Structure Qualified，"
                               "§三十一，不作强行解释）"})
    return {"rows": rows, "execution_layer_value_add": value_add,
            "buy_minus_structure_t1": jnum(delta),
            "note": "每增加一层过滤，信号质量是否真的改善；NEGATIVE 必须标记而非解释。"}


def build_incremental(sc: dict, val: list) -> dict:
    """§三十二 Step 7 的增量价值。"""
    base_l, tgt_l = INCR["baseline_layer"], INCR["target_layer"]
    out = {"trade_date": sc["signal_date"], "signal_date": sc["signal_date"],
           "baseline_layer": base_l, "target_layer": tgt_l, "horizons": {}}
    best = None
    for h in INCR["horizons"]:
        bm = _blk_metric(sc["layers"].get(base_l, {}), h, "mean_return")
        tm = _blk_metric(sc["layers"].get(tgt_l, {}), h, "mean_return")
        imp = (fnum(tm) - fnum(bm)) if (np.isfinite(fnum(tm))
                                        and np.isfinite(fnum(bm))) else np.nan
        out["horizons"][str(h)] = {"baseline_mean": bm, "target_mean": tm, "improvement": jnum(imp)}
        if np.isfinite(fnum(imp)) and (best is None or fnum(imp) > fnum(best)):
            best = imp
    if not np.isfinite(fnum(best)):
        fv = "INSUFFICIENT_SAMPLE"
    elif fnum(best) >= INCR["positive_min_improvement"]:
        fv = "POSITIVE"
    else:
        fv = "LOW"
    out["filtering_value"] = fv
    out["note"] = ("Step 7 主要减少数量但未改善质量时为 LOW；明显改善为 POSITIVE（§三十二）。")
    return out


def build_regime_strat(store: BarStore, ctx: Ctx, signal_date: int, horizons, val: list) -> dict:
    """§三十三 所有信号按 5 档 market regime 分别统计。

    单日的 market_regime 是市场级唯一标签，按单日切分只会命中 1 档、统计无意义；
    故按 <= signal_date 的全部 Step 7 日期累计聚合（全部特征日期 <= review，无前视）。
    """
    e = ctx.exec[ctx.exec["trade_date"] <= int(signal_date)] if len(ctx.exec) else pd.DataFrame()
    rows = []
    for rv in MKT["regime_values"]:
        sub = e[e["market_regime"] == rv] if len(e) else e
        codes = sub["ts_code"].tolist() if len(sub) else []
        blk = multiframe_stat_block(store, list(zip(sub["trade_date"].tolist(), codes)), horizons)
        row = {"trade_date": signal_date, "signal_date": signal_date, "regime": rv,
               "n": blk.get("n", 0),
               "buy": int((sub["action"] == "BUY").sum()) if len(sub) else 0}
        for h in horizons:
            s = blk.get(f"ret_{h}") or {}
            row[f"win_rate_{h}"] = s.get("win_rate")
            row[f"mean_return_{h}"] = s.get("mean_return")
            row[f"median_return_{h}"] = s.get("median_return")
        rows.append(row)
    observed = sorted({jstr(x) for x in e["market_regime"].dropna().unique()}) if len(e) else []
    missing = [r for r in MKT["regime_values"] if r not in observed]
    if missing:
        val.append({"level": LEVEL_WARN, "code": "REGIME_STRAT_MISSING",
                    "message": f"Step 7 历史未产出的 regime 档位如实为 0：{missing}（§三十三 / §五十六）"})
    dates = sorted({int(x) for x in e["trade_date"].unique()}) if len(e) else []
    return {"trade_date": signal_date, "signal_date": signal_date, "rows": rows,
            "window_start": dates[0] if dates else None, "window_end": dates[-1] if dates else None,
            "n_dates": len(dates),
            "observed_regimes": "|".join(observed), "missing_regimes": "|".join(missing)}


def _cross_rows(store: BarStore, e: pd.DataFrame, signal_date: int, key_a: str, key_b: str,
                horizons, min_sample: int) -> list:
    if not len(e):
        return []
    rows = []
    for (a, b), sub in e.groupby([key_a, key_b], dropna=False):
        blk = multiframe_stat_block(store, list(zip(sub["trade_date"].tolist(),
                                                    sub["ts_code"].tolist())), horizons)
        rec = {"trade_date": signal_date, "signal_date": signal_date,
               "dim_a": key_a, "dim_b": key_b,
               "value_a": jstr(a), "value_b": jstr(b), "n": blk.get("n", 0),
               "sample_sufficient": bool(blk.get("n", 0) >= min_sample),
               "buy": int((sub["action"] == "BUY").sum())}
        for h in horizons:
            s = blk.get(f"ret_{h}") or {}
            rec[f"win_rate_{h}"] = s.get("win_rate")
            rec[f"mean_return_{h}"] = s.get("mean_return")
            rec[f"median_return_{h}"] = s.get("median_return")
        rows.append(rec)
    rows.sort(key=lambda x: (x["value_a"], x["value_b"]))
    return rows


def build_cross(store: BarStore, ctx: Ctx, signal_date: int, theme_rows, val: list) -> dict:
    """§三十四 主题 Phase × Structure State；§三十五 主题 Phase × Structure Class。

    单日交叉格子样本必然过薄、且 T+5/T+20 尚未发生，故按 <= signal_date 的全部 Step 7 日期累计
    （每格按当日已知的 phase / structure 归类，无前视）。
    """
    e = ctx.exec[ctx.exec["trade_date"] <= int(signal_date)].copy() if len(ctx.exec) else pd.DataFrame()
    if not len(e):
        return {"theme_structure": [], "seos_structure": [], "n_dates": 0}
    pmap = {}
    if len(ctx.seos):
        s = ctx.seos[ctx.seos["trade_date"] <= int(signal_date)]
        pmap = {(int(a), jstr(b)): jstr(c)
                for a, b, c in zip(s["trade_date"], s["sector_id"], s["theme_phase"])}
    for r in theme_rows or []:
        pmap[(int(signal_date), jstr(r.get("sector_id")))] = jstr(r.get("theme_phase_after"))
    e["theme_phase_at_signal"] = [pmap.get((int(d), jstr(t)), "")
                                  for d, t in zip(e["trade_date"], e["primary_theme_id"])]
    ts_rows = _cross_rows(store, e, signal_date, "theme_phase_at_signal", "structure_state",
                          CROS["theme_structure_horizons"], CROS["min_cell_sample"])
    ss_rows = _cross_rows(store, e, signal_date, "theme_phase_at_signal", "structure_class",
                          CROS["seos_structure_horizons"], CROS["min_cell_sample"])
    thin = sum(1 for r in ts_rows + ss_rows if not r["sample_sufficient"])
    if thin:
        val.append({"level": LEVEL_WARN, "code": "CROSS_SAMPLE_THIN",
                    "message": f"{thin} 个交叉格子样本 < {CROS['min_cell_sample']}，已如实标注"
                               f"（§三十四 / §三十五）"})
    dates = sorted({int(x) for x in e["trade_date"].unique()})
    return {"theme_structure": ts_rows, "seos_structure": ss_rows, "n_dates": len(dates),
            "window_start": dates[0], "window_end": dates[-1]}


def build_errors(exec_layer: dict, review_date: int, signal_date: int, val: list) -> dict:
    """§二十七 / §二十八 错误诊断：只按规则违反聚合，不凭结果倒推；最多 5 条 P0/P1。"""
    df = pd.DataFrame(exec_layer.get("rows") or [])
    rows = []
    if len(df) and "error_class" in df.columns:
        bad = df[(df["error_class"] != "NO_ERROR") & (df["error_severity"].isin(ERRC["severity"][:2]))]
        for (cls, sev), sub in bad.groupby(["error_class", "error_severity"]):
            ev = sorted({jstr(x) for x in sub["rule_violated"] if jstr(x)})
            rows.append({
                "trade_date": review_date, "signal_date": signal_date,
                "layer": _layer_of_error(cls), "error_class": cls, "severity": sev,
                "n": int(len(sub)),
                "stocks": "|".join(sub["ts_code"].astype(str).tolist()[:12]),
                "evidence": "；".join(ev[:3]),
                "is_model_error": bool(sub["is_model_error"].any()),
                "known_at": "KNOWN_AT_SIGNAL",
            })
    order = {s: i for i, s in enumerate(ERRC["severity"])}
    rows.sort(key=lambda r: (order.get(r["severity"], 9), -r["n"]))
    rows = rows[:REP["max_errors"]]
    summary = "NO MATERIAL MODEL ERROR" if not rows else f"{len(rows)} 类 P0/P1 规则问题"
    if not rows:
        val.append({"level": LEVEL_PASS, "code": "MODEL_ERROR_NONE",
                    "message": "NO MATERIAL MODEL ERROR（不强行找问题，§四十-9）"})
    return {"rows": rows, "summary": summary, "review_date": review_date, "signal_date": signal_date}


def error_history(store: BarStore, ctx: Ctx, upto_date: int) -> pd.DataFrame:
    """按信号日规则重放全部历史执行记录，得到跨日期/跨 regime 的错误样本（§三十八）。"""
    recs = []
    if not len(ctx.exec):
        return pd.DataFrame(columns=["trade_date", "ts_code", "error_class", "severity",
                                     "is_model_error", "market_regime", "ret_1"])
    e = ctx.exec[ctx.exec["trade_date"] <= upto_date]
    for _, r in e.iterrows():
        cls, sev, ev, is_model = attribute_error(r, ctx)
        if cls == "NO_ERROR":
            continue
        recs.append({
            "trade_date": int(r["trade_date"]), "ts_code": jstr(r["ts_code"]),
            "error_class": cls, "severity": sev, "is_model_error": bool(is_model),
            "market_regime": jstr(r.get("market_regime")),
        })
    df = pd.DataFrame(recs)
    if not len(df):
        df["ret_1"] = []
        return df
    rmap = {}
    for d, sub in df.groupby("trade_date"):
        rp = store.ret_panel(int(d), sub["ts_code"].tolist(), [1])
        for c in sub["ts_code"].tolist():
            rmap[(int(d), c)] = rp.get(1, {}).get(c)
    df["ret_1"] = [rmap.get((int(r["trade_date"]), r["ts_code"])) for _, r in df.iterrows()]
    return df


def _cohort_pair(store: BarStore, e: pd.DataFrame, mask_a, mask_b, horizons) -> tuple:
    sub_a = e[mask_a] if len(e) else e
    sub_b = e[mask_b] if len(e) else e
    blk_a = multiframe_stat_block(store, list(zip(sub_a["trade_date"].tolist(),
                                                  sub_a["ts_code"].tolist())), horizons)
    blk_b = multiframe_stat_block(store, list(zip(sub_b["trade_date"].tolist(),
                                                  sub_b["ts_code"].tolist())), horizons)
    return blk_a, sub_a, blk_b, sub_b


def long_term_indicators(store: BarStore, ctx: Ctx, signal_date: int, horizons, val: list) -> dict:
    """§五十二 最重要的长期反馈指标：每一层过滤是否真的增加了信息。

    按 <= signal_date 的全部 Step 7 日期累计（每个 (日期, 股票) 用当日已知字段归类，无前视）；
    样本不足一律 INSUFFICIENT_SAMPLE，不给结论（§三十八 / §三十九）。
    """
    e = ctx.exec[ctx.exec["trade_date"] <= int(signal_date)] if len(ctx.exec) else pd.DataFrame()
    # Step5 的对照口径（EXCLUDE）不在 Step 7 落盘中，改用 Step 5 候选全集度量；其余指标用 Step 7 全量。
    cand = ctx.cand[ctx.cand["trade_date"] <= int(signal_date)] if len(ctx.cand) else pd.DataFrame()
    if not len(e) and not len(cand):
        return {"window_start": None, "window_end": None, "n_dates": 0, "rows": []}
    # market_regime 是市场级唯一标签；Step 5 候选集无该列，按同交易日映射（当日已知，无前视）
    rmap = ({int(d): jstr(v) for d, v in zip(e["trade_date"], e["market_regime"]) if jstr(v)}
            if len(e) else {})
    qual = SCC["accepted_qualifications"]
    defs = [
        ("STEP5", "CANDIDATE_FILTER", "候选过滤增量价值", "接受(WATCH/CANDIDATE)", "排除(EXCLUDE)", "cand",
         lambda f: f["candidate_status"].isin(CANDC["accepted_status"]),
         lambda f: f["candidate_status"] == "EXCLUDE"),
        ("STEP6", "STRUCTURE_FILTER", "结构过滤增量价值", "结构合格", "REJECTED", "exec",
         lambda f: f["structure_qualification"].isin(qual),
         lambda f: f["structure_qualification"] == "REJECTED"),
        ("STEP7", "EXECUTION_FILTER", "执行层增量价值（Step7 是否优于 Step6）", "BUY",
         "结构合格但未 BUY", "exec",
         lambda f: f["action"] == "BUY",
         lambda f: f["structure_qualification"].isin(qual) & (f["action"] != "BUY")),
        ("STEP6", "HVT_INCREMENTAL", "HVT 增量价值", "HVT 活跃", "HVT_FAILED/HVT_NONE", "exec",
         lambda f: f["hvt_state"].isin(HVTC["states"]),
         lambda f: f["hvt_state"].isin(HVTC["failed_states"]) | (f["hvt_state"] == "HVT_NONE")),
        ("STEP6", "RETEST_INCREMENTAL", "Retest 增量价值", "RETEST_SUCCESS", "NO_RETEST", "exec",
         lambda f: f["retest_state"] == RTC["states"][-1],
         lambda f: f["retest_state"] == "NO_RETEST"),
        ("STEP7", "EXTENSION_FILTER", "Extension 过滤增量价值（过滤掉的是否更差）",
         "extension_risk=LOW", "extension_risk=MEDIUM/EXTREME", "exec",
         lambda f: f["extension_risk"] == STRC["extension_risk_low_state"],
         lambda f: f["extension_risk"].isin(STRC["extension_risk_high_states"])),
        ("STEP7", "MARKET_REGIME_FILTER", "市场 Regime 过滤增量价值", "execution_mode=NORMAL",
         "execution_mode=WAIT/DEFENSIVE", "exec",
         lambda f: f["execution_mode"] == MKT["execution_mode_normal"],
         lambda f: f["execution_mode"].isin(MKT["execution_mode_cautious"])),
    ]
    thr = INCR["positive_min_improvement"]
    rows, seen_dates = [], set()
    for layer, ind, desc, ga, gb, fkey, maf, mbf in defs:
        f = cand if fkey == "cand" else e
        if len(f):
            ba, sa, bb, sb = _cohort_pair(store, f, maf(f), mbf(f), horizons)
        else:
            ba, sa, bb, sb = {"n": 0}, f, {"n": 0}, f
        for sub in (sa, sb):
            if len(sub):
                seen_dates.update(int(x) for x in sub["trade_date"].unique())
        dates = int(sa["trade_date"].nunique()) if len(sa) else 0
        if fkey == "cand":
            regimes = sorted({r for r in (rmap.get(int(d), "") for d in sa["trade_date"].unique()) if r}) \
                if len(sa) else []
        else:
            regimes = sorted({jstr(x) for x in sa["market_regime"].unique() if jstr(x)}) if len(sa) else []
        n_a, n_b = int(ba.get("n", 0)), int(bb.get("n", 0))
        if n_b == 0:
            val.append({"level": LEVEL_WARN, "code": "LONG_TERM_NO_COHORT",
                        "message": f"{ind} 无对照样本（n_b=0），如实标 INSUFFICIENT_SAMPLE，不给结论（§五十二）"})
        # 必须有可对照的 B 组、且样本/日期/regime 门槛同时满足，才允许给结论（§三十八 / §三十九）
        sample_ok = bool(n_a >= FBK["min_sample_size"] and n_b > 0
                         and dates >= FBK["min_dates"] and len(regimes) >= FBK["min_regimes"])
        d1 = (fnum(_blk_metric(ba, 1, "mean_return"), np.nan)
              - fnum(_blk_metric(bb, 1, "mean_return"), np.nan))
        d5 = (fnum(_blk_metric(ba, 5, "mean_return"), np.nan)
              - fnum(_blk_metric(bb, 5, "mean_return"), np.nan))
        d1 = d1 if np.isfinite(d1) else np.nan
        d5 = d5 if np.isfinite(d5) else np.nan
        key = d5 if np.isfinite(d5) else d1
        if not sample_ok:
            verdict = "INSUFFICIENT_SAMPLE"
        elif np.isfinite(key) and key >= thr:
            verdict = "POSITIVE"
        elif np.isfinite(key) and key <= -thr:
            verdict = "NEGATIVE"
        else:
            verdict = "LOW"
        rows.append({
            "layer": layer, "indicator": ind, "description": desc,
            "group_a": ga, "group_b": gb,
            "n_a": n_a, "n_b": n_b,
            "dates": dates, "n_regimes": len(regimes), "regimes": "|".join(regimes),
            "sample_ok": sample_ok,
            "a_mean_1": _blk_metric(ba, 1, "mean_return"), "b_mean_1": _blk_metric(bb, 1, "mean_return"),
            "a_mean_5": _blk_metric(ba, 5, "mean_return"), "b_mean_5": _blk_metric(bb, 5, "mean_return"),
            "a_win_1": _blk_metric(ba, 1, "win_rate"), "b_win_1": _blk_metric(bb, 1, "win_rate"),
            "diff_1": jnum(d1), "diff_5": jnum(d5),
            "verdict": verdict, "known_at": "KNOWN_AT_REVIEW",
        })
    dates_all = sorted(seen_dates)
    return {"window_start": dates_all[0] if dates_all else None,
            "window_end": dates_all[-1] if dates_all else None,
            "n_dates": len(dates_all), "rows": rows}


def build_feedback(err_hist: pd.DataFrame, review_date: int, val: list,
                   lti: dict = None) -> dict:
    """§三十七 Feedback Queue + §三十八/§三十九 Model Change Candidate（防过拟合门槛）。"""
    issues, cands = [], []
    if len(err_hist):
        for cls, sub in err_hist.groupby("error_class"):
            n = int(len(sub))
            dates = int(sub["trade_date"].nunique())
            regimes = sorted({jstr(x) for x in sub["market_regime"].unique() if jstr(x)})
            sev_p0 = bool((sub["severity"] == "P0").any())
            if sev_p0 and n >= FBK["priority_p0_sample"]:
                prio = "P0"
            elif n >= FBK["priority_p1_sample"]:
                prio = "P1"
            else:
                prio = "P2"
            mean_ret = mean_of(sub["ret_1"].tolist())
            issues.append({
                "layer": _layer_of_error(cls),
                "type": cls,
                "observation": f"{cls} 在 {dates} 个交易日出现 {n} 次，平均 T+1 收益 {mean_ret}；"
                               f"涉及 regime {regimes}",
                "sample_size": n,
                "dates": dates,
                "regimes": "|".join(regimes),
                "priority": prio,
                "requires_validation": True,
                "known_at": "KNOWN_AT_SIGNAL",
            })
            if (n >= FBK["min_sample_size"] and dates >= FBK["min_dates"]
                    and len(regimes) >= FBK["min_regimes"]):
                cands.append({
                    "layer": _layer_of_error(cls), "issue": cls,
                    "evidence": f"n={n}, dates={dates}, regimes={len(regimes)}",
                    "sample_size": n, "regimes": "|".join(regimes),
                    "before_metric": f"mean_ret_1={mean_ret}",
                    "after_expected_metric": "待独立 Validation / Research 流程验证（Step 8 不预设结论）",
                    "priority": prio, "validation_required": True,
                })
    # §五十二 长期过滤增量价值 -> §三十七 观察型 issue（不是规则违反，也不是自动改模型）
    for r in ((lti or {}).get("rows") or []):
        if r["verdict"] not in ("NEGATIVE", "LOW"):
            continue
        prio = "P1" if r["verdict"] == "NEGATIVE" and r["sample_ok"] else "P2"
        issues.append({
            "layer": r["layer"],
            "type": r["indicator"],
            "observation": f"{r['description']}：{r['group_a']} T+1 {_pct(r.get('a_mean_1'))} / T+5 "
                           f"{_pct(r.get('a_mean_5'))} vs {r['group_b']} T+1 {_pct(r.get('b_mean_1'))} / "
                           f"T+5 {_pct(r.get('b_mean_5'))}；差 {_pct(r.get('diff_1'))} / {_pct(r.get('diff_5'))}"
                           f"（样本 {r['n_a']} vs {r['n_b']}，{r['dates']} 个交易日，regime {r['n_regimes']} 档）",
            "sample_size": r["n_a"],
            "dates": r["dates"],
            "regimes": r["regimes"],
            "priority": prio,
            "requires_validation": True,
            "known_at": "KNOWN_AT_REVIEW",
        })
        if r["verdict"] == "NEGATIVE" and r["sample_ok"]:
            cands.append({
                "layer": r["layer"], "issue": r["indicator"],
                "evidence": f"{r['description']}；n={r['n_a']}, dates={r['dates']}, "
                            f"regimes={r['n_regimes']}；diff_1={r.get('diff_1')}, diff_5={r.get('diff_5')}",
                "sample_size": r["n_a"], "regimes": r["regimes"],
                "before_metric": f"{r['group_b']} mean_ret_1={r.get('b_mean_1')} / mean_ret_5={r.get('b_mean_5')}",
                "after_expected_metric": "待独立 Validation / Research 流程验证（Step 8 不预设结论）",
                "priority": prio, "validation_required": True,
            })
    queue = {
        "trade_date": review_date,
        "read_only": True,
        "note": "Feedback != 自动修改模型。进入 MODEL_CHANGE_CANDIDATE 必须同时满足 "
                f"sample_size >= {FBK['min_sample_size']}、dates >= {FBK['min_dates']}、"
                f"regimes >= {FBK['min_regimes']}（§三十七 / §三十八 / §三十九）。",
        "issues": issues,
    }
    return {"queue": queue, "change_candidates": cands}

# ══════════════════════════════════════════════════════════════════════════
# 9. 明日 Watch Pool（§四十一 / §四十二：WATCH != BUY）
# ══════════════════════════════════════════════════════════════════════════
def _fmt_px(v) -> str:
    return "-" if not np.isfinite(fnum(v, np.nan)) else f"{fnum(v):.2f}"


def _watch_item(row, watch_type: str, phase_map: dict, basis_date: int) -> dict:
    code = jstr(row.get("ts_code"))
    tid = jstr(row.get("primary_theme_id"))
    el, eh = fnum(row.get("entry_low")), fnum(row.get("entry_high"))
    trig, stop = fnum(row.get("trigger_price")), fnum(row.get("stop_price"))
    es = jstr(row.get("entry_state"))
    return {
        "ts_code": code,
        "name": jstr(row.get("name")),
        "theme": tid,
        "theme_phase": phase_map.get(tid, ""),
        "structure_state": jstr(row.get("structure_state")),
        "hvt_state": jstr(row.get("hvt_state")),
        "watch_type": watch_type,
        "trigger": (f"触发参考 {_fmt_px(trig)}（须由 Step 7 下一交易日重新确认，Step 8 不产生 BUY）"
                    if np.isfinite(fnum(trig)) else "须由 Step 7 下一交易日重新确认"),
        "entry_condition": (f"Entry 区间 {_fmt_px(el)}~{_fmt_px(eh)}；"
                            f"entry_state={es or '-'} 需重新确认"),
        "invalid_condition": (f"收盘跌破 {_fmt_px(stop)}" if np.isfinite(fnum(stop))
                              else "结构失效（跌破结构低点 / MA20）"),
        "extension_risk": jstr(row.get("extension_risk")),
        "reason": f"{watch_type} 结构在 {basis_date} 成立；Step 8 仅记录观察条件，不给出 BUY",
        "basis_date": basis_date,
        "price_basis": jstr(row.get("price_basis")),
        "known_at": "KNOWN_AT_SIGNAL",
    }


def build_watch_pool(ctx: Ctx, review_date: int, signal_date: int, theme_rows, val: list) -> dict:
    """§四十一 明日 Watch Pool；basis 取 review_date 前最近可得的 Step 7 快照。"""
    basis = latest_date(ctx.exec, review_date)
    e = ctx.exec[ctx.exec["trade_date"] == basis] if (basis and len(ctx.exec)) else pd.DataFrame()
    phase_map = {}
    if basis and len(ctx.seos):
        s = ctx.seos[ctx.seos["trade_date"] == basis]
        phase_map = {jstr(a): jstr(b) for a, b in zip(s["sector_id"], s["theme_phase"])}
    items = []
    if len(e):
        for wt in WPC["watch_types"]:
            states = WPC["source_states"].get(wt, [])
            states = [states] if isinstance(states, str) else states
            fields = WPC["source_fields"].get(wt, [])
            for f in fields:
                if f not in e.columns:
                    continue
                for _, r in e[e[f].isin(states)].iterrows():
                    items.append(_watch_item(r, wt, phase_map, basis))
    seen, uniq = set(), []
    for it in items:
        if it["ts_code"] in seen:
            continue
        seen.add(it["ts_code"])
        uniq.append(it)
    uniq = uniq[:WPC["max_items"]]
    note = ("watch basis = Step 7 快照日；所有观察对象必须在下一交易日由 Step 7 重新确认后"
            "才可能产生 BUY（WATCH != BUY，§四十二）。")
    if basis != review_date:
        note += f"（{review_date} 无 Step 7 快照，basis 回退至 {basis}）"
        val.append({"level": LEVEL_WARN, "code": "WATCH_BASIS_LAG",
                    "message": f"Watch Pool basis 回退至 {basis}（{review_date} 无 Step 7 快照）"})
    return {"trade_date": review_date, "signal_date": signal_date, "basis_date": basis,
            "generated_at": "KNOWN_AT_REVIEW", "disclaimer": WPC["disclaimer"],
            "watch_basis_note": note, "count": len(uniq), "items": uniq}


# ══════════════════════════════════════════════════════════════════════════
# 10. 长期统计库（§五十一）
# ══════════════════════════════════════════════════════════════════════════
def history_record(art: dict, review_date: int, signal_date: int, final_status: str) -> dict:
    m, th, ca = art["market"], art["theme"], art["candidate"]
    st, ex, sc = art["structure"], art["execution"], art["scorecard"]
    fn, er = art["funnel"], art["errors"]
    buys = ex.get("buy") or []
    nts = ex.get("no_trade_summary") or {}
    buy_l = sc["layers"].get("BUY", {}).get("ret_1", {})
    str_l = sc["layers"].get("STRUCTURE_QUALIFIED", {}).get("ret_1", {})
    return {
        "trade_date": review_date, "signal_date": signal_date,
        "market_regime": jstr(m.get("market_regime")),
        "market_regime_before": jstr(m.get("market_regime_before")),
        "market_earning_effect": jstr(m.get("market_earning_effect")),
        "market_structure_tag": jstr(m.get("market_structure_tag")),
        "market_structure_tags_secondary": jstr(m.get("market_structure_tags_secondary")),
        "divergence_flag": jstr(m.get("divergence_flag")),
        "index_ret_sse": fnum(m.get("index_ret_SSE")),
        "market_amount_yi": fnum(m.get("market_amount_yi")),
        "market_amount_ratio_20": fnum(m.get("market_amount_ratio_20")),
        "breadth": fnum(m.get("breadth")), "breadth_change": fnum(m.get("breadth_change")),
        "limit_up": fnum(m.get("limit_up")), "limit_down": fnum(m.get("limit_down")),
        "failed_limit_up": fnum(m.get("failed_limit_up")),
        "median_stock_return": fnum(m.get("median_stock_return")),
        "max_consecutive_board": fnum(m.get("max_consecutive_board")),
        "n_themes": (th.get("summary") or {}).get("n_themes", 0),
        "strong_continuing": (th.get("summary") or {}).get("strong_continuing", 0),
        "improving": (th.get("summary") or {}).get("improving", 0),
        "rotation_in": (th.get("summary") or {}).get("rotation_in", 0),
        "cooling": (th.get("summary") or {}).get("cooling", 0),
        "deteriorating": (th.get("summary") or {}).get("deteriorating", 0),
        "failed_startup": (th.get("summary") or {}).get("failed_startup", 0),
        "n_candidate": len(ca.get("rows") or []),
        "n_structure": len(st.get("rows") or []),
        "n_structure_qualified": sc["layers"].get("STRUCTURE_QUALIFIED", {}).get("n", 0),
        "n_execution_qualified": sc["layers"].get("EXECUTION_QUALIFIED", {}).get("n", 0),
        "n_buy": len(buys), "n_no_trade": nts.get("total", 0),
        "buy_n": len(buys),
        "buy_success": sum(1 for r in buys if r.get("result_class") == "BUY_SUCCESS"),
        "buy_failed": sum(1 for r in buys if r.get("result_class") == "BUY_FAILED"),
        "buy_no_entry": sum(1 for r in buys if r.get("result_class") == "BUY_NO_ENTRY"),
        "buy_no_chase": sum(1 for r in buys if r.get("result_class") == "BUY_NO_CHASE"),
        "no_trade_correct": (nts.get("by_outcome") or {}).get("CORRECT_NO_TRADE", 0),
        "no_trade_missed": (nts.get("by_outcome") or {}).get("MISSED_OPPORTUNITY", 0),
        "no_trade_waiting": (nts.get("by_outcome") or {}).get("WAITING_CORRECTLY", 0),
        "candidate_mean_ret_1": _blk_metric(sc["layers"].get("CANDIDATE", {}), 1, "mean_return"),
        "structure_qualified_mean_ret_1": str_l.get("mean_return"),
        "buy_mean_ret_1": buy_l.get("mean_return"),
        "buy_win_rate_1": buy_l.get("win_rate"),
        "candidate_mean_ret_5": _blk_metric(sc["layers"].get("CANDIDATE", {}), 5, "mean_return"),
        "structure_qualified_mean_ret_5": _blk_metric(
            sc["layers"].get("STRUCTURE_QUALIFIED", {}), 5, "mean_return"),
        "buy_mean_ret_5": _blk_metric(sc["layers"].get("BUY", {}), 5, "mean_return"),
        "buy_mean_ret_20": _blk_metric(sc["layers"].get("BUY", {}), 20, "mean_return"),
        "execution_layer_value_add": fn.get("execution_layer_value_add"),
        "filtering_value": art["incremental"].get("filtering_value"),
        "n_error_p0": sum(r["n"] for r in (er.get("rows") or []) if r.get("severity") == "P0"),
        "n_error_p1": sum(r["n"] for r in (er.get("rows") or []) if r.get("severity") == "P1"),
        "error_summary": er.get("summary"),
        "watch_count": art["watch"].get("count", 0),
        "data_status_market": m.get("data_status"),
        "data_status_theme": th.get("data_status"),
        "data_status_structure": st.get("data_status"),
        "data_status_execution": ex.get("data_status"),
        "final_status": final_status,
    }


def append_history(rec: dict, rel: str) -> int:
    path = _abs(rel)
    row = pd.DataFrame([rec])
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, low_memory=False)
        except Exception:
            old = pd.DataFrame()
        if len(old) and "trade_date" in old.columns:
            old = old[old["trade_date"].astype(str) != str(rec["trade_date"])]
        row = pd.concat([old, row], ignore_index=True, sort=False)
    if "trade_date" in row.columns:
        row = row.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row.to_csv(path, index=False, encoding="utf-8-sig")
    return int(len(row))


# ══════════════════════════════════════════════════════════════════════════
# 11. 报告生成（§四十 / §四十三 / §四十四 / §四十五）
# ══════════════════════════════════════════════════════════════════════════
TAG_CN = {
    "BROAD_RALLY": "全面普涨",
    "NARROW_RALLY": "指数偏强、个股分化",
    "BROAD_DECLINE": "全面回落",
    "NARROW_DECLINE": "指数走弱、个股相对抗跌",
    "STRUCTURAL_ROTATION": "结构性轮动",
    "HIGH_VOLATILITY": "高波动",
    "LOW_PARTICIPATION": "成交参与度偏低",
    "DIVERGENCE": "指数与个股背离",
    "NO_TAG": "结构中性",
}
REGIME_CN = {"STRONG": "强势", "HEALTHY": "健康", "NEUTRAL": "中性",
             "WEAK": "偏弱", "RISK_OFF": "风险规避"}
EARN_CN = {"STRONG_EARNING": "赚钱效应强", "NEUTRAL_EARNING": "赚钱效应中性",
           "WEAK_EARNING": "赚钱效应弱"}
RESULT_CN = {"BUY_SUCCESS": "触发后走强", "BUY_TRIGGERED": "已触发、表现平淡",
             "BUY_FAILED": "触发后跌破 Stop", "BUY_NO_ENTRY": "未进入 Entry 区间",
             "BUY_NO_CHASE": "跳空过高、未追", "BUY_PENDING": "待确认"}
VALUE_CN = {"POSITIVE": "正向", "NEGATIVE": "负向", "NEUTRAL": "中性",
            "HIGH": "高", "MEDIUM": "中", "LOW": "低", "INSUFFICIENT_SAMPLE": "样本不足"}
ERROR_SUMMARY_CN = {"NO MATERIAL MODEL ERROR": "无实质性模型错误"}
LT_CN = {"CANDIDATE_FILTER": "候选过滤", "STRUCTURE_FILTER": "结构过滤",
         "EXECUTION_FILTER": "执行过滤", "HVT_INCREMENTAL": "HVT 增量",
         "RETEST_INCREMENTAL": "Retest 增量", "EXTENSION_FILTER": "Extension 过滤",
         "MARKET_REGIME_FILTER": "市场 Regime 过滤"}


def _n(v, nd: int = 2) -> str:
    return "-" if not np.isfinite(fnum(v, np.nan)) else f"{fnum(v):.{nd}f}"


def _pct(v, nd: int = 2) -> str:
    return "-" if not np.isfinite(fnum(v, np.nan)) else f"{fnum(v) * 100:.{nd}f}%"


def market_one_liner(m: dict) -> str:
    tag = TAG_CN.get(jstr(m.get("market_structure_tag")), jstr(m.get("market_structure_tag")))
    reg = REGIME_CN.get(jstr(m.get("market_regime")), jstr(m.get("market_regime")))
    parts = [f"{tag}（{reg}环境）"]
    bc = fnum(m.get("breadth_change"))
    if np.isfinite(bc):
        parts.append(f"breadth {_n(fnum(m.get('breadth')), 2)}，较前一日{'改善' if bc > 0 else ('回落' if bc < 0 else '持平')}"
                     f" {_pct(abs(bc))}")
    parts.append(f"涨停 {_n(m.get('limit_up'), 0)} / 跌停 {_n(m.get('limit_down'), 0)}")
    parts.append(f"成交额 {_n(m.get('market_amount_yi'), 0)} 亿")
    if jstr(m.get("divergence_flag")):
        parts.append("存在指数与个股背离")
    return "；".join(parts) + "。"


def _summary_blocks(art: dict, review_date: int, signal_date: int) -> dict:
    m, th = art["market"], art["theme"]
    ex, w = art["execution"], art["watch"]
    buys = ex.get("buy") or []
    top = [r for r in (th.get("rows") or [])][:REP["max_themes"]]
    strongest = [f"{r['sector_id']} {r['sector_name']}" for r in top[:3]]
    improving = [f"{r['sector_id']} {r['sector_name']}（breadth/health 改善）"
                 for r in top if "IMPROVING" in jstr(r.get("theme_group"))][:3]
    cooling = [f"{r['sector_id']} {r['sector_name']}"
               for r in (th.get("rows") or []) if "COOLING" in jstr(r.get("theme_group"))
               or "DETERIORATING" in jstr(r.get("theme_group"))][:3]
    nt = ex.get("no_trade_summary") or {}
    return {
        "MARKET": "市场：" + market_one_liner(m),
        "THEME": ("主线：" + ("、".join(strongest) if strongest else "无") +
                  "；强化：" + ("、".join(improving) if improving else "无") +
                  "；退潮：" + ("、".join(cooling) if cooling else "无")),
        "SIGNAL": (f"昨日信号：侯选 {len(art['candidate'].get('rows') or [])} / "
                   f"结构合格 {art['scorecard']['layers'].get('STRUCTURE_QUALIFIED', {}).get('n', 0)} / "
                   f"执行合格 {art['scorecard']['layers'].get('EXECUTION_QUALIFIED', {}).get('n', 0)}；"
                   f"Step7：BUY {len(buys)} / 未交易 {nt.get('total', 0)}"),
        "TRADE": (f"昨日 BUY：{sum(1 for r in buys if r.get('result_class') == 'BUY_TRIGGERED')} 触发、"
                  f"{sum(1 for r in buys if r.get('result_class') in ('BUY_SUCCESS',))} 走强、"
                  f"{sum(1 for r in buys if r.get('result_class') == 'BUY_FAILED')} 跌破 Stop、"
                  f"{sum(1 for r in buys if r.get('result_class') in ('BUY_NO_ENTRY', 'BUY_NO_CHASE'))} 未成交；"
                  f"未交易中潜在漏掉 {(nt.get('by_outcome') or {}).get('MISSED_OPPORTUNITY', 0)}"),
        "FEEDBACK": (f"复盘：执行层增量价值 "
                     f"{VALUE_CN.get(jstr(art['funnel'].get('execution_layer_value_add')), jstr(art['funnel'].get('execution_layer_value_add')))}"
                     f"（BUY 减结构合格 T+1 = {_pct(art['funnel'].get('buy_minus_structure_t1'))}）；"
                     f"过滤增量价值 "
                     f"{VALUE_CN.get(jstr(art['incremental'].get('filtering_value')), jstr(art['incremental'].get('filtering_value')))}；"
                     f"{ERROR_SUMMARY_CN.get(jstr(art['errors'].get('summary')), jstr(art['errors'].get('summary')))}；"
                     f"待独立验证的模型改动候选 {len(art['feedback'].get('change_candidates') or [])} 项"
                     f"（长期指标 §五十二）；"
                     f"明日保留 {w.get('count', 0)} 个条件观察对象（WATCH ≠ BUY）"),
    }


def render_report_md(art: dict, review_date: int, signal_date: int,
                     validation: dict, final_status: str) -> str:
    m, th, st = art["market"], art["theme"], art["structure"]
    ex, sc, fn = art["execution"], art["scorecard"], art["funnel"]
    inc, er, w = art["incremental"], art["errors"], art["watch"]
    blocks = _summary_blocks(art, review_date, signal_date)
    L = []
    L.append(f"# Step 8 盘后复盘 · {review_date}")
    L.append("")
    L.append(f"- signal_date（被复盘信号日）：**{signal_date}**；review_date（复盘日）：**{review_date}**")
    L.append(f"- 模式：**READ ONLY**（不修改 Step 1-7 的任何落盘结果；不重新选股；不产生 BUY）")
    L.append(f"- 最终状态：**{final_status}**")
    L.append("")
    L.append("## 核心摘要")
    for k in REP["summary_blocks"]:
        L.append(f"- **{k}** — {blocks[k]}")
    L.append("")

    # 1 市场
    L.append("## 1. 市场")
    L.append("")
    L.append(f"{market_one_liner(m)}")
    L.append("")
    L.append("| 项目 | 数值 | 项目 | 数值 |")
    L.append("| --- | --- | --- | --- |")
    L.append(f"| 上证 | {_pct(m.get('index_ret_SSE'))} | 沪深300 | {_pct(m.get('index_ret_CSI300'))} |")
    L.append(f"| 中证1000 | {_pct(m.get('index_ret_CSI1000'))} | 中证2000 | {_pct(m.get('index_ret_CSI2000'))} |")
    L.append(f"| 成交额 | {_n(m.get('market_amount_yi'), 0)} 亿 | 成交额/20日均 | {_n(m.get('market_amount_ratio_20'))} |")
    L.append(f"| 上涨/下跌/平盘 | {_n(m.get('up_count'),0)}/{_n(m.get('down_count'),0)}/{_n(m.get('flat_count'),0)} "
             f"| Breadth | {_n(m.get('breadth'), 4)} |")
    L.append(f"| 涨停/跌停 | {_n(m.get('limit_up'),0)}/{_n(m.get('limit_down'),0)} | 炸板 | {_n(m.get('failed_limit_up'),0)} |")
    L.append(f"| 最高连板 | {_n(m.get('max_consecutive_board'),0)} | 赚钱效应 | {EARN_CN.get(jstr(m.get('market_earning_effect')), jstr(m.get('market_earning_effect')))} |")
    L.append(f"| 结构标签 | {jstr(m.get('market_structure_tag'))} | 次级标签 | {jstr(m.get('market_structure_tags_secondary')) or '-'} |")
    L.append(f"| 背离标记 | {jstr(m.get('divergence_flag')) or '-'} | Regime | {jstr(m.get('market_regime'))}"
             f"（{jstr(m.get('market_regime_before')) or '-'}→{jstr(m.get('market_regime'))}） |")
    L.append("")
    L.append(f"Regime 变化依据：{jstr(m.get('regime_change_reason')) or '-'}")
    L.append(f"Regime 覆盖：已观测 {jstr(m.get('regime_values_observed')) or '-'}；"
             f"缺档（如实为 0）{jstr(m.get('regime_missing_values')) or '无'}")
    L.append("")

    # 2 主题
    L.append(f"## 2. 主题（最多 {REP['max_themes']} 个）")
    L.append("")
    L.append("| 主题 | Phase（前→后） | SEOS | Health | Breadth | Core Breadth | Amount Share | 今日变化 | 分组 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in (th.get("rows") or [])[:REP["max_themes"]]:
        L.append(f"| {r['sector_id']} {r['sector_name']} | {r['theme_phase_before']}→{r['theme_phase_after']} "
                 f"| {_n(r['seos_score'])} | {_n(r['theme_health'])} | {_n(r['breadth'], 3)} "
                 f"| {_n(r['core_breadth'], 3)} | {_n(r['amount_share'], 4)} "
                 f"| health {_n(r['health_change'], 3)} / breadth {_n(r['breadth_change'], 3)} "
                 f"| {r['theme_group'] or '-'} |")
    if not (th.get("rows") or []):
        L.append("| - | - | - | - | - | - | - | - | - |")
    L.append("")
    rot_rows = [r for r in (th.get("rows") or [])
                if jstr(r.get("rotation_signal")) in ("ROTATION_IN", "ROTATION_OUT")]
    L.append(f"### 主题轮动（最多展示 {REP['max_rotation_items']} 个）")
    L.append("")
    if rot_rows:
        for r in rot_rows[:REP["max_rotation_items"]]:
            L.append(f"- {r['sector_id']} {r['sector_name']}：{jstr(r.get('rotation_signal'))}"
                     f"（{jstr(r.get('rotation_reason')) or '-'}）")
    else:
        L.append("- 当日无 ROTATION_IN / ROTATION_OUT 记录。")
    dif = [r for r in (th.get("rows") or []) if jstr(r.get("diffusion_stage"))]
    if dif:
        L.append("")
        L.append("扩散复盘：" + "；".join(
            f"{r['sector_id']} {r['diffusion_stage']}→{jstr(r.get('diffusion_review')) or '-'}"
            for r in dif[:REP["max_themes"]]))
    L.append("")

    # 3 结构池
    focus = [f for f in STRC["focus_values"]]
    pool = [r for r in (st.get("rows") or [])
            if jstr(r.get("structure_class")) in focus or jstr(r.get("hvt_state")) in focus
            or jstr(r.get("retest_state")) in focus or jstr(r.get("breakout_state")) in focus]
    pool.sort(key=lambda r: (focus.index(r["structure_class"]) if r["structure_class"] in focus else 99))
    cap = min(REP["max_structure_pool"], REP["max_stocks"])
    L.append(f"## 3. 今日结构池（最多 {cap} 只）")
    L.append("")
    L.append("| 股票 | 主题 | structure_class | hvt_state | T+1 | 备注 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for r in pool[:cap]:
        L.append(f"| {r['ts_code']} {r['name']} | {r['primary_theme_id']} | {r['structure_class']} "
                 f"| {r['hvt_state']} | {_pct(r['ret_1'])} | {jstr(r.get('hvt_outcome')) or '-'} |")
    if not pool:
        L.append("| - | - | - | - | - | - |")
    L.append("")

    # 4 交易复盘
    L.append("## 4. 昨日交易复盘")
    L.append("")
    buys = ex.get("buy") or []
    L.append(f"### BUY 复盘（{len(buys)} 只）")
    L.append("")
    L.append("| 股票 | Entry | Trigger | 今日 O/H/L/C | MAE(T+1) | MFE(T+1) | 结果 | 原因 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in buys[:REP["max_stocks"]]:
        ohlc = f"{_n(r.get('today_open'))}/{_n(r.get('today_high'))}/{_n(r.get('today_low'))}/{_n(r.get('today_close'))}"
        L.append(f"| {r['ts_code']} {r['name']} | {_n(r.get('entry_low'))}~{_n(r.get('entry_high'))} "
                 f"| {_n(r.get('trigger_price'))} | {ohlc} | {_pct(r.get('mae_1'))} | {_pct(r.get('mfe_1'))} "
                 f"| {RESULT_CN.get(jstr(r.get('result_class')), jstr(r.get('result_class')))} "
                 f"| {jstr(r.get('result_reason'))[:72]} |")
    if not buys:
        L.append("| - | - | - | - | - | - | - | - |")
    L.append("")
    nt = ex.get("no_trade_summary") or {}
    L.append(f"### NO TRADE 复盘（{nt.get('total', 0)} 条）")
    L.append("")
    L.append(f"- 分类：{_kv_cn(nt.get('by_class') or {})}")
    L.append(f"- 结果：{_kv_cn(nt.get('by_outcome') or {})}")
    rep_rows = _no_trade_representative(ex.get("rows") or [])
    for label, rs in rep_rows:
        L.append(f"- {label}：" + ("；".join(
            f"{r['ts_code']} {r['name']}（T+1 {_pct(r.get('signal_return_1'))}）" for r in rs[:5]) or "无"))
    L.append("")

    # 5 模型表现
    L.append("## 5. 模型表现（层层过滤）")
    L.append("")
    L.append("| Layer | N | T+1 Win | T+1 Mean | T+1 Median | T+5 Mean | T+20 Mean |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in sc.get("rows") or []:
        L.append(f"| {r['layer']} | {r['n']} | {_n(r.get('win_rate_1'), 4)} | {_n(r.get('mean_return_1'), 4)} "
                 f"| {_n(r.get('median_return_1'), 4)} | {_n(r.get('mean_return_5'), 4)} "
                 f"| {_n(r.get('mean_return_20'), 4)} |")
    L.append("")
    L.append(f"- 执行层增量价值：**"
             f"{VALUE_CN.get(jstr(fn.get('execution_layer_value_add')), jstr(fn.get('execution_layer_value_add')))}**"
             f"（BUY 减结构合格 T+1 = {_pct(fn.get('buy_minus_structure_t1'))}）")
    L.append(f"- 过滤增量价值：**"
             f"{VALUE_CN.get(jstr(inc.get('filtering_value')), jstr(inc.get('filtering_value')))}**"
             f"（baseline {inc.get('baseline_layer')} vs target {inc.get('target_layer')}）")
    L.append("")
    rs = art["regime_strat"]
    L.append(f"### Regime 分层（§三十三，累计 {rs.get('n_dates')} 个交易日 "
             f"{jstr(rs.get('window_start'))}~{jstr(rs.get('window_end'))}）")
    L.append("")
    L.append("| Regime | N | BUY | T+1 Win | T+1 Mean | T+5 Mean | T+20 Mean |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in (art["regime_strat"].get("rows") or []):
        L.append(f"| {r['regime']} | {r['n']} | {r['buy']} | {_n(r.get('win_rate_1'), 4)} "
                 f"| {_n(r.get('mean_return_1'), 4)} | {_n(r.get('mean_return_5'), 4)} "
                 f"| {_n(r.get('mean_return_20'), 4)} |")
    L.append("")
    lt = art.get("long_term") or {}
    L.append(f"### 长期过滤增量价值（§五十二，累计 {lt.get('n_dates')} 个交易日 "
             f"{jstr(lt.get('window_start'))}~{jstr(lt.get('window_end'))}）")
    L.append("")
    L.append("| Layer | 指标 | 对照 | N(A/B) | Dates | Regimes | T+1 差 | T+5 差 | 判定 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in (lt.get("rows") or []):
        L.append(f"| {r['layer']} | {LT_CN.get(r['indicator'], r['indicator'])} | {r['group_a']} vs {r['group_b']} | "
                 f"{r['n_a']}/{r['n_b']} | {r['dates']} | {r['n_regimes']} "
                 f"| {_pct(r.get('diff_1'))} | {_pct(r.get('diff_5'))} "
                 f"| {VALUE_CN.get(r['verdict'], r['verdict'])} |")
    if not (lt.get("rows") or []):
        L.append("| - | - | - | - | - | - | - | - | - |")
    L.append("")

    cs = art["cross"]
    cs_win = f"累计 {cs.get('n_dates')} 个交易日 {jstr(cs.get('window_start'))}~{jstr(cs.get('window_end'))}"
    for title, key, hz in (("主题 Phase × Structure State", "theme_structure", CROS["theme_structure_horizons"]),
                           ("主题 Phase × Structure Class", "seos_structure", CROS["seos_structure_horizons"])):
        rows = [r for r in (cs.get(key) or []) if r["sample_sufficient"]]
        L.append(f"### {title}（§{'三十四' if key == 'theme_structure' else '三十五'}，{cs_win}）")
        L.append("")
        if rows:
            L.append("| " + " | ".join(["Phase", "Structure", "N", "BUY"] +
                                       [f"T+{h} Mean" for h in hz]) + " |")
            L.append("|" + " --- |" * (4 + len(hz)))
            for r in rows[:12]:
                L.append("| " + " | ".join([r["value_a"] or "-", r["value_b"] or "-", str(r["n"]), str(r["buy"])] +
                                           [_pct(r.get(f"mean_return_{h}")) for h in hz]) + " |")
        else:
            L.append(f"- 无样本量 ≥ {CROS['min_cell_sample']} 的交叉格子（样本不足已如实标注，不作解读）。")
        L.append("")

    # 6 模型错误
    L.append(f"## 6. 模型错误（最多 {REP['max_errors']} 条 P0/P1）")
    L.append("")
    if er.get("rows"):
        L.append("| 级别 | 层 | 类型 | N | 证据 | MODEL_ERROR |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        for r in er["rows"]:
            L.append(f"| {r['severity']} | {r['layer']} | {r['error_class']} | {r['n']} "
                     f"| {r['evidence']} | {r['is_model_error']} |")
    else:
        L.append(f"**NO MATERIAL MODEL ERROR**（不强行找问题，§四十-9）")
    L.append("")

    # 7 明日观察
    L.append("## 7. 明日执行观察（WATCH，非 BUY 清单）")
    L.append("")
    L.append(f"> {w.get('disclaimer')}")
    L.append(f"> {w.get('watch_basis_note')}")
    L.append("")
    L.append("| 股票 | 主题/Phase | watch_type | Watch（触发参考） | Condition | Invalid | extension_risk |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for it in (w.get("items") or [])[:REP["max_stocks"]]:
        L.append(f"| {it['ts_code']} {it['name']} | {it['theme']}/{it['theme_phase']} | {it['watch_type']} "
                 f"| {it['trigger']} | {it['entry_condition']} | {it['invalid_condition']} "
                 f"| {it['extension_risk']} |")
    if not (w.get("items") or []):
        L.append("| - | - | - | - | - | - | - |")
    L.append("")

    # 附录：验证
    L.append("## 附录：Validation（§四十九）")
    L.append("")
    L.append(f"- 最终状态：**{final_status}**")
    for c in validation.get("checks") or []:
        L.append(f"- [{c['level']}] {c['check']}：{c['message']}")
    if validation.get("limitations"):
        L.append("- LIMITATION：" + "；".join(validation["limitations"]))
    L.append("")
    L.append("---")
    L.append("")
    L.append("Step 8 只反馈、不修改模型；模型修改必须经过独立 Validation / Research 流程（§五十七）。")
    L.append("")
    return "\n".join(L)


def _kv_cn(d: dict) -> str:
    if not d:
        return "无"
    return "；".join(f"{k or 'UNMAPPED'} {v}" for k, v in sorted(d.items(), key=lambda x: -x[1]))


def _no_trade_representative(rows) -> list:
    """代表性样本：正确规避看「跌得最多」（最该规避的），漏掉/继续观察看「涨得最多」。"""
    out = []
    for label, key, asc in (("正确规避", "CORRECT_NO_TRADE", True),
                            ("值得继续观察", "WAITING_CORRECTLY", False),
                            ("潜在漏掉", "MISSED_OPPORTUNITY", False)):
        rs = [r for r in rows if jstr(r.get("no_trade_outcome")) == key]
        rs.sort(key=lambda r: fnum(r.get("signal_return_1"), 0.0), reverse=not asc)
        out.append((label, rs))
    return out


def check_forbidden(text: str) -> list:
    return [w for w in REP["forbidden_words"] if w in (text or "")]


def build_report_json(art: dict, review_date: int, signal_date: int,
                      validation: dict, final_status: str) -> dict:
    return {
        "meta": {"step": META["step"], "version": META["version"],
                 "review_date": review_date, "signal_date": signal_date,
                 "read_only": True, "price_basis": META["price_basis"],
                 "generated_from": "Step 3/4/5/6/7 落盘结果 + 本地 DB 行情（不跨目录调用）"},
        "final_status": final_status,
        "summary": _summary_blocks(art, review_date, signal_date),
        "market": art["market"],
        "theme": {"theme_date": art["theme"]["theme_date"], "summary": art["theme"]["summary"],
                  "rows": art["theme"]["rows"], "data_status": art["theme"]["data_status"]},
        "candidate": {"cand_date": art["candidate"].get("cand_date"),
                      "by_type": art["candidate"].get("by_type"),
                      "rows": art["candidate"].get("rows"),
                      "data_status": art["candidate"].get("data_status")},
        "structure": {"summary": art["structure"].get("summary"),
                      "rows": art["structure"].get("rows"),
                      "data_status": art["structure"].get("data_status")},
        "execution": {"rows": art["execution"].get("rows"),
                      "buy": art["execution"].get("buy"),
                      "no_trade_summary": art["execution"].get("no_trade_summary"),
                      "data_status": art["execution"].get("data_status")},
        "scorecard": art["scorecard"]["rows"],
        "funnel": art["funnel"],
        "incremental": art["incremental"],
        "regime_strat": art["regime_strat"],
        "cross": art["cross"],
        "errors": art["errors"],
        "feedback": {"queue": art["feedback"]["queue"],
                     "change_candidates": art["feedback"].get("change_candidates") or []},
        "long_term": art.get("long_term"),
        "watch_pool": art["watch"],
        "validation": validation,
    }


# ══════════════════════════════════════════════════════════════════════════
# 12. 验证层（§四十九）+ 最终状态（§五十六）
# ══════════════════════════════════════════════════════════════════════════
def validate(art: dict, store: BarStore, ctx: Ctx, review_date: int, signal_date: int) -> dict:
    """§四十九 六项检查 + 上游状态一致性 / Regime 档位覆盖等附加检查，只读；不修改任何上游结果。"""
    checks, limitations = [], []
    horizon_check = {}

    def add(name: str, level: str, message: str):
        checks.append({"check": name, "level": level, "message": message})

    horizons = LOOK["horizons"]
    theme_date = int(art["theme"].get("theme_date") or 0)
    cand_date = int(art["candidate"].get("cand_date") or 0)

    # 1. FUTURE LEAKAGE：feature date <= 对应 base date；未来数据只作 outcome
    leak = []
    if int(signal_date) >= int(review_date):
        leak.append(f"signal_date {signal_date} 不早于 review_date {review_date}")
    if theme_date and theme_date > int(signal_date):
        leak.append(f"theme feature date {theme_date} > signal_date {signal_date}")
    if cand_date and cand_date > int(signal_date):
        leak.append(f"candidate feature date {cand_date} > signal_date {signal_date}")
    if int(art["market"].get("trade_date") or 0) != int(review_date):
        leak.append(f"market feature date {art['market'].get('trade_date')} != review_date {review_date}")
    t1 = art["execution"].get("t1")
    if t1 is not None and int(t1) <= int(signal_date):
        leak.append(f"outcome date {t1} 未晚于 signal_date {signal_date}")
    add("FUTURE_LEAKAGE", LEVEL_FAIL if leak else LEVEL_PASS,
        "；".join(leak) if leak else
        f"feature 日期均 <= 对应 base date（theme {theme_date} / candidate {cand_date} "
        f"<= signal {signal_date} < review {review_date}）；未来收益仅作 outcome")

    # 2. STEP DEPENDENCY
    miss = [rel for rel in VALC["required_step_files"] if not os.path.exists(_abs(rel))]
    if not len(ctx.cand):
        miss.append("Step5 rows(sector_stock_candidate_daily)")
    if not len(ctx.structure):
        miss.append("Step6 rows(stock_structure_daily)")
    if not len(ctx.exec):
        miss.append("Step7 rows(stock_execution_daily)")
    add("STEP_DEPENDENCY", LEVEL_FAIL if miss else LEVEL_PASS,
        f"缺失：{miss}" if miss else "Step5 / Step6 / Step7 落盘文件与当日子集均存在，未绕过")

    # 3. BUY REVIEW
    need = VALC["buy_required_fields"]
    bad_buy = []
    for r in art["execution"].get("buy") or []:
        lack = [f for f in need if not np.isfinite(fnum(r.get(f), np.nan))]
        if lack:
            bad_buy.append(f"{r['ts_code']}({','.join(lack)})")
    n_buy = len(art["execution"].get("buy") or [])
    if bad_buy:
        add("BUY_REVIEW", LEVEL_FAIL, f"{len(bad_buy)} 只 BUY 缺少 trigger/entry/stop/target/RR：{bad_buy[:8]}")
    else:
        add("BUY_REVIEW", LEVEL_PASS,
            f"{n_buy} 只 BUY 均具备 trigger / entry / stop / target / RR"
            + ("（当日 BUY=0，允许，不为凑数量降低门槛）" if n_buy == 0 else ""))

    # 4. NO TRADE REVIEW
    nt_rows = [r for r in (art["execution"].get("rows") or []) if jstr(r.get("action")) == "NO TRADE"]
    bad_nt = [r["ts_code"] for r in nt_rows if not jstr(r.get("no_trade_reason_raw"))]
    add("NO_TRADE_REVIEW", LEVEL_FAIL if bad_nt else LEVEL_PASS,
        f"{len(bad_nt)} 条 NO TRADE 缺少 no_trade_reason：{bad_nt[:8]}" if bad_nt
        else f"{len(nt_rows)} 条 NO TRADE 均带 no_trade_reason")

    # 5. HVT REVIEW
    srows = art["structure"].get("rows") or []
    miss_hvt = [r["ts_code"] for r in srows if not jstr(r.get("hvt_state"))]
    hvt_domain = set(HVTC["states"]) | set(HVTC["failed_states"]) | {"HVT_NONE"}
    odd = sorted({jstr(r.get("hvt_state")) for r in srows
                  if jstr(r.get("hvt_state")) and jstr(r.get("hvt_state")) not in hvt_domain})
    if miss_hvt:
        add("HVT_REVIEW", LEVEL_FAIL, f"{len(miss_hvt)} 行 HVT 结构缺失 hvt_state：{miss_hvt[:8]}")
    elif odd:
        add("HVT_REVIEW", LEVEL_WARN, f"hvt_state 出现声明域外取值：{odd}（如实标注，不臆断）")
        limitations.append(f"HVT 取值域外：{odd}")
    else:
        add("HVT_REVIEW", LEVEL_PASS, f"{len(srows)} 行结构记录的 hvt_state 全部存在且落在声明域内")

    # 5b. UPSTREAM STATE CONFLICT（只读：Step 6 落盘状态不得自相矛盾）
    conf = [f"{r['ts_code']}(HVT_FAILED×{jstr(r.get('retest_state'))})" for r in srows
            if jstr(r.get("hvt_state")) == "HVT_FAILED"
            and jstr(r.get("retest_state")) in ("RETEST_SUCCESS", "RETEST_PENDING")]
    if conf:
        add("UPSTREAM_STATE_CONFLICT", LEVEL_WARN,
            f"{len(conf)} 行 Step 6 状态自相矛盾（hvt_state=HVT_FAILED 却报回踩成功 / 待确认）："
            f"{conf[:8]}（§二十七：失败结构不得自动恢复）")
        limitations.append(f"Step6 状态冲突 {len(conf)} 行：hvt_state 与 retest_state 不自洽")
    else:
        add("UPSTREAM_STATE_CONFLICT", LEVEL_PASS,
            f"{len(srows)} 行结构记录中 hvt_state 与 retest_state 无自相矛盾（§二十七）")

    # 6. THEME REVIEW
    unknown = []
    if len(ctx.seos):
        d = ctx.seos[ctx.seos["trade_date"] == theme_date]
        unknown = sorted({jstr(x) for x in d["sector_id"] if jstr(x) not in ctx.theme_ids})
    if unknown:
        add("THEME_REVIEW", LEVEL_FAIL, f"出现 theme_master 之外的主题：{unknown[:10]}")
    elif not theme_date:
        add("THEME_REVIEW", LEVEL_WARN, "无主题层数据，主题复盘不可用")
        limitations.append("缺失主题层数据")
    else:
        add("THEME_REVIEW", LEVEL_PASS,
            f"{theme_date} 全部主题均来自 theme_master（{len(ctx.theme_ids)} 个），无未知主题")

    # 附：Regime 档位覆盖（§三十三：声明 5 档，实际未产出的档位如实标注）
    rstrat = art.get("regime_strat") or {}
    declared = list(MKT["regime_values"])
    observed = [x for x in jstr(rstrat.get("observed_regimes")).split("|") if x]
    missing = [x for x in declared if x not in observed]
    window = f"{rstrat.get('window_start')}~{rstrat.get('window_end')}"
    if not observed:
        add("REGIME_TIER_COVERAGE", LEVEL_WARN, "Step 7 历史无 market_regime 标签，档位覆盖不可判定")
        limitations.append("regime 档位覆盖不可判定")
    elif missing:
        add("REGIME_TIER_COVERAGE", LEVEL_WARN,
            f"声明 {len(declared)} 档，Step 7 历史（{window}）实际产出 {len(observed)} 档 "
            f"{'、'.join(REGIME_CN.get(x, x) for x in observed)}；"
            f"未覆盖 {'、'.join(REGIME_CN.get(x, x) for x in missing)}"
            f"（对应代码 {'、'.join(missing)}）—— 如实缺档，不臆断、不为凑覆盖率下调阈值"
            f"（阈值与成因见 config/execution_config.json::market_regime）")
        limitations.append(f"regime 档位未覆盖：{'、'.join(REGIME_CN.get(x, x) for x in missing)}"
                           f"（如实缺档，§三十三）")
    else:
        add("REGIME_TIER_COVERAGE", LEVEL_PASS,
            f"声明 {len(declared)} 档在 Step 7 历史（{window}）全部出现")

    # 附：报告语言检查（§四十四）
    md = art.get("_report_md") or ""
    fw = check_forbidden(md)
    if fw:
        checks.append({"check": "REPORT_LANGUAGE", "level": LEVEL_WARN,
                       "message": f"报告出现禁用夸张词：{fw}"})
        limitations.append(f"报告措辞：{fw}")
    else:
        checks.append({"check": "REPORT_LANGUAGE", "level": LEVEL_PASS,
                       "message": "报告未使用禁用夸张词（§四十四）"})

    # 附：报告章节数量（§五十五 5-8 节）
    n_sec = md.count("\n## ") + (1 if md.startswith("## ") else 0)
    n_core = max(0, n_sec - 1)  # 去掉「核心摘要」一节
    if md:
        lvl = LEVEL_PASS if REP["min_sections"] <= n_core <= REP["max_sections"] else LEVEL_WARN
        checks.append({"check": "REPORT_SECTIONS", "level": lvl,
                       "message": f"正文核心章节 {n_core} 节（要求 {REP['min_sections']}-{REP['max_sections']}）"})
        if lvl == LEVEL_WARN:
            limitations.append(f"报告章节数 {n_core} 超出建议区间")

    for h in horizons:
        fwd = store.next_days(signal_date, h)
        horizon_check[str(h)] = {"outcome_available": len(fwd) >= h,
                                 "outcome_date": fwd[h - 1] if len(fwd) >= h else None}
    na = [h for h, v in horizon_check.items() if not v["outcome_available"]]
    if na:
        limitations.append(f"T+{', T+'.join(na)} 结果尚未发生，如实留空（不以前视数据填充）")

    results = {
        "Market data": art["market"].get("data_status", LEVEL_FAIL),
        "Theme data": art["theme"].get("data_status", LEVEL_FAIL),
        "Candidate data": art["candidate"].get("data_status", LEVEL_FAIL),
        "Structure data": art["structure"].get("data_status", LEVEL_FAIL),
        "Execution data": art["execution"].get("data_status", LEVEL_FAIL),
        "Outcome PIT": LEVEL_PASS if not leak else LEVEL_FAIL,
        "Future leakage": LEVEL_PASS if not leak else LEVEL_FAIL,
    }
    for k, v in results.items():
        if v == LEVEL_FAIL:
            limitations.append(f"{k} NOT PASS")
    return {"checks": checks, "results": results, "horizons": horizon_check,
            "limitations": sorted(set(limitations)),
            "n_pass": sum(1 for c in checks if c["level"] == LEVEL_PASS),
            "n_warning": sum(1 for c in checks if c["level"] == LEVEL_WARN),
            "n_issue": sum(1 for c in checks if c["level"] == LEVEL_FAIL)}


def final_status(validation: dict) -> str:
    """§五十六：任一 required 项 FAIL -> REVIEW_INVALID；有 WARNING -> CONDITIONAL_REVIEW。"""
    levels = list((validation.get("results") or {}).values())
    if any(l == LEVEL_FAIL for l in levels):
        return FSC["invalid"]
    if any(l == LEVEL_WARN for l in levels):
        return FSC["conditional"]
    return FSC["ready"]


# ══════════════════════════════════════════════════════════════════════════
# 13. 编排 / 输出 / CLI（§四十六 ~ §四十八）
# ══════════════════════════════════════════════════════════════════════════
def latest_date(df: pd.DataFrame, upto: int) -> int:
    if not len(df) or "trade_date" not in df.columns:
        return 0
    d = df.loc[df["trade_date"] <= int(upto), "trade_date"]
    return int(d.max()) if len(d) else 0


def next_trading_day(cal, d: int):
    later = [x for x in cal if x > int(d)]
    return int(later[0]) if later else None


def load_calendar() -> list:
    conn = sqlite3.connect(INP["db_path"])
    try:
        q = ("select distinct trade_date from index_daily_cache where ts_code = ? "
             "order by trade_date")
        df = pd.read_sql(q, conn, params=[MKT["index_codes"][MKT["benchmark_index"]]])
    finally:
        conn.close()
    return [int(x) for x in df.iloc[:, 0].tolist()]


def build_all(store: BarStore, ctx: Ctx, review_date: int, signal_date: int, val: list) -> dict:
    horizons = LOOK["horizons"]
    regime_map = regime_by_date(ctx)
    theme_date = latest_date(ctx.seos, signal_date)
    cand_date = latest_date(ctx.cand, signal_date)
    theme = build_theme(ctx, theme_date, signal_date, val)
    market = build_market(store, ctx, review_date, signal_date, regime_map,
                          theme.get("summary") or {}, val)
    structure = build_structure_layer(store, ctx, signal_date, horizons, val)
    bench = benchmark_ret(store, cand_date, horizons)
    candidate = build_candidate_layer(store, ctx, cand_date, signal_date, horizons, bench, val)
    candidate["cand_date"] = cand_date
    execution = build_execution_layer(store, ctx, signal_date, review_date, horizons, val)
    scorecard = signal_scorecard(store, ctx, cand_date, signal_date, horizons, val)
    funnel = build_funnel(scorecard, val)
    incremental = build_incremental(scorecard, val)
    regime_strat = build_regime_strat(store, ctx, signal_date, horizons, val)
    cross = build_cross(store, ctx, signal_date, theme.get("rows") or [], val)
    errors = build_errors(execution, review_date, signal_date, val)
    err_hist = error_history(store, ctx, signal_date)
    lti = long_term_indicators(store, ctx, signal_date, INCR["horizons"], val)
    feedback = build_feedback(err_hist, review_date, val, lti)
    watch = build_watch_pool(ctx, review_date, signal_date, theme.get("rows") or [], val)
    return {"market": market, "theme": theme, "candidate": candidate, "structure": structure,
            "execution": execution, "scorecard": scorecard, "funnel": funnel,
            "incremental": incremental, "regime_strat": regime_strat, "cross": cross,
            "errors": errors, "feedback": feedback, "watch": watch, "long_term": lti,
            "theme_date": theme_date, "cand_date": cand_date,
            "signal_date": signal_date, "review_date": review_date}


def write_outputs(art: dict, review_date: int, signal_date: int,
                  validation: dict, status: str, md: str = None) -> dict:
    if md is None:
        md = render_report_md(art, review_date, signal_date, validation, status)
    art["_report_md"] = md
    write_csv(pd.DataFrame([art["market"]]), OUT["data_market"])
    write_csv(pd.DataFrame(art["theme"].get("rows") or []), OUT["data_theme"])
    write_csv(pd.DataFrame(art["candidate"].get("rows") or []), OUT["data_candidate"])
    write_csv(pd.DataFrame(art["structure"].get("rows") or []), OUT["data_structure"])
    write_csv(pd.DataFrame(art["execution"].get("rows") or []), OUT["data_execution"])
    write_csv(pd.DataFrame(art["scorecard"].get("rows") or []), OUT["data_scorecard"])
    write_csv(pd.DataFrame(art["errors"].get("rows") or []), OUT["data_error"])
    write_csv(pd.DataFrame(art["feedback"]["queue"].get("issues") or []), OUT["data_feedback"])
    write_csv(pd.DataFrame(art["feedback"].get("change_candidates") or []), OUT["model_change_candidate"])
    hist_n = append_history(history_record(art, review_date, signal_date, status), OUT["data_history"])
    write_json(art["watch"], OUT["watch_pool"])
    write_json(art["feedback"]["queue"], OUT["feedback_queue"])
    write_json(build_report_json(art, review_date, signal_date, validation, status),
               OUT["report_json"].replace("{date}", str(review_date)))
    md_path = _abs(OUT["report_md"].replace("{date}", str(review_date)))
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return {"md": OUT["report_md"].replace("{date}", str(review_date)),
            "json": OUT["report_json"].replace("{date}", str(review_date)),
            "history_rows": hist_n}


def run_review(store: BarStore, ctx: Ctx, review_date: int, signal_date: int,
               write: bool = True) -> tuple:
    val: list = []
    art = build_all(store, ctx, review_date, signal_date, val)
    # 预渲染报告正文（纯函数），使 validation 可执行语言/章节检查（§四十四 / §五十五）
    art["_report_md"] = render_report_md(art, review_date, signal_date,
                                         {"checks": [], "limitations": []}, "")
    validation = validate(art, store, ctx, review_date, signal_date)
    status = final_status(validation)
    if write:
        # 用最终 validation 重渲染一次（附录回填），随后一次性落盘全部产物
        md = render_report_md(art, review_date, signal_date, validation, status)
        art["_paths"] = write_outputs(art, review_date, signal_date, validation, status, md=md)
    art["_validation"] = validation
    art["_final_status"] = status
    art["_issues"] = val
    return art, validation, status


def print_console(art: dict, validation: dict, status: str, review_date: int, signal_date: int) -> None:
    m = art["market"]
    ex = art["execution"]
    print(f"[Step 8] review_date={review_date} signal_date={signal_date} READ ONLY")
    print(f"  市场：regime={jstr(m.get('market_regime'))} tag={jstr(m.get('market_structure_tag'))} "
          f"breadth={jnum(m.get('breadth'))} 涨停={m.get('limit_up')} 跌停={m.get('limit_down')}")
    print(f"  主题：{len(art['theme'].get('rows') or [])} 个；"
          f"strong/rotation_in={art['theme'].get('summary', {}).get('strong_or_rotation_in')}")
    print(f"  信号：candidate={len(art['candidate'].get('rows') or [])} "
          f"structure_qualified={art['scorecard']['layers'].get('STRUCTURE_QUALIFIED', {}).get('n')} "
          f"execution_qualified={art['scorecard']['layers'].get('EXECUTION_QUALIFIED', {}).get('n')}")
    print(f"  交易：BUY={len(ex.get('buy') or [])} NO TRADE={(ex.get('no_trade_summary') or {}).get('total')} "
          f"missed={(ex.get('no_trade_summary') or {}).get('by_outcome', {}).get('MISSED_OPPORTUNITY', 0)}")
    print(f"  Funnel：EXECUTION_LAYER_VALUE_ADD={art['funnel'].get('execution_layer_value_add')} "
          f"FILTERING_VALUE={art['incremental'].get('filtering_value')}")
    print(f"  错误：{art['errors'].get('summary')}；Watch Pool={art['watch'].get('count')}（WATCH != BUY）")
    lt = art.get("long_term") or {}
    if lt.get("rows"):
        print("  长期过滤增量价值（§五十二）：" + "；".join(
            f"{r['indicator']}={r['verdict']}(n={r['n_a']}/{r['n_b']}, {r['dates']}d)" for r in lt["rows"]))
        for r in lt["rows"]:
            if r["verdict"] == "NEGATIVE" and r["sample_ok"]:
                print(f"    -> MODEL_CHANGE_CANDIDATE：{r['indicator']}（样本/日期/regime 均达标，"
                      f"仍需独立 Validation，Step 8 不改模型）")
    for c in validation["checks"]:
        print(f"  [{c['level']}] {c['check']}: {c['message']}")
    print(f"  最终状态：{status}")
    for iss in art.get("_issues") or []:
        print(f"  提示 [{iss['level']}] {iss['code']}: {iss['message']}")
    if validation.get("limitations"):
        print("  LIMITATION: " + "；".join(validation["limitations"]))
    if art.get("_paths"):
        print(f"  输出：{art['_paths']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 8 盘后复盘系统（READ ONLY，不修改 Step 1-7）")
    ap.add_argument("--date", type=str, default=None,
                    help="复盘日 YYYYMMDD；非交易日自动取最近有效交易日（§四十七）")
    ap.add_argument("--full", action="store_true", help="回补长期历史复盘（§五十 一）")
    ap.add_argument("--validate", action="store_true", help="只运行六项验证（§四十九）")
    args = ap.parse_args()

    cal = load_calendar()
    if not cal:
        raise SystemExit("无法读取交易日历（index_daily_cache）")
    data_max = int(cal[-1])
    ctx = Ctx()
    exec_dates = sorted({int(x) for x in ctx.exec["trade_date"].unique()}) if len(ctx.exec) else []
    if not exec_dates:
        raise SystemExit("Step 7 执行层为空，无法复盘（§四十九-2 STEP_DEPENDENCY）")

    # ── 日期解析（§四十八：signal_date / review_date 严格对应）
    if args.full:
        targets = []
        for d in exec_dates:
            nd = next_trading_day(cal, d)
            if nd and nd <= data_max:
                targets.append(nd)
        targets = sorted(set(targets))[-int(LOOK["max_history_dates_full"]):]
    else:
        want = None
        if args.date:
            want = int(re.sub(r"\D", "", str(args.date)) or 0) or None
            if want and want not in cal:
                prev = [d for d in cal if d <= want]
                print(f"[Step 8] {want} 非交易日，取最近有效交易日 {prev[-1] if prev else '-'}")
        review_date = (max([d for d in cal if want is None or d <= want], default=None)
                       if want is not None else data_max)
        if review_date is None:
            raise SystemExit(f"无法解析 --date {args.date}")
        targets = [int(review_date)]

    # 行情窗口必须与运行模式无关：跨日期累计统计（§三十三 / §三十四 / §五十二）覆盖到最早的
    # Step 7 日期，否则同一 review_date 在 --date / --full 下会得到不同的累计样本与结论（§四十八）。
    earliest = min([targets[0]] + exec_dates)
    idx = cal.index(earliest) if earliest in cal else 0
    store = BarStore(INP["db_path"], int(cal[max(0, idx - 45)]), data_max)
    print(f"[Step 8] 行情窗口 {store.date_min}~{store.date_max}（DB 最新 {data_max}）")

    results = []
    for i, review_date in enumerate(targets):
        prev = [d for d in cal if d < review_date]
        signal_date = int(prev[-1]) if prev else None
        if signal_date is None:
            print(f"[Step 8] {review_date} 无前一交易日，跳过")
            continue
        lag = ""
        if signal_date not in set(exec_dates):
            alt = [d for d in exec_dates if d < review_date]
            if alt:
                lag = f"signal_date 回退至 {alt[-1]}（{signal_date} 无 Step 7 记录）"
                signal_date = int(alt[-1])
        last = (i == len(targets) - 1)
        write = (not args.validate) and (last or not args.full)
        art, validation, status = run_review(store, ctx, review_date, signal_date, write=write)
        if lag:
            print(f"[Step 8] {review_date}：{lag}")
        if args.full and not last:
            if not args.validate:
                append_history(history_record(art, review_date, signal_date, status),
                               OUT["data_history"])
            results.append((review_date, signal_date, status, validation["n_issue"]))
            continue
        print_console(art, validation, status, review_date, signal_date)
        results.append((review_date, signal_date, status, validation["n_issue"]))

    if args.full:
        print(f"[Step 8] --full 完成，共处理 {len(results)} 个交易日")
        for d, s, st, ni in results[-5:]:
            print(f"  {d} (signal {s}) -> {st} issue={ni}")
    bad = [r for r in results if r[3] > 0]
    if bad:
        print(f"[Step 8] 存在 ISSUE 的复盘日：{[r[0] for r in bad]}")


if __name__ == "__main__":
    main()

