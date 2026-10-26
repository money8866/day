# -*- coding: utf-8 -*-
"""
EOD-RB 事件驱动回测引擎。

两种模式：
  - daily 模式（消融 A/B，仅 L1+L2）：按「信号日T收盘评估 → T+1 开盘入场」的日线基准，可覆盖 2021+ 长历史。
  - full  模式（消融 C/D，含 L3 尾盘 + L4 板块）：按「T日14:30~14:55信号 → 当下一根可成交分钟价入场」，
    仅能覆盖分钟数据实际存在的 MINUTE_START~MINUTE_END。

两种模式都严格无前视：T 日信号只用 <=T 数据；成交价均为信号后真实可执行价。
"""
import os
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from . import engine as E
from .engine import add_indicators, board_of, limit_up_price, amount_to_yi
from . import minute as M
from .config import EOD_RB_CONFIG, CACHE_DB_PATH
import bts.data as bd
import stock_cache as sc

# 某交易日全市场日线（含 ts_code）。bts.db_daily_by_date 不返回 ts_code，故本地补充。
_SNAP_COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]


def snapshot_by_date(trade_date):
    """返回某交易日全市场日线快照（含 ts_code），用于板块/候选截面。"""
    try:
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=10.0)
        df = pd.read_sql_query(
            f"SELECT {','.join(_SNAP_COLS)} FROM daily_cache WHERE trade_date=?",
            conn, params=(str(trade_date),),
        )
        conn.close()
    except Exception:
        return None
    if df.empty:
        return None
    df["trade_date"] = df["trade_date"].astype(str)
    for c in _SNAP_COLS[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ═══════════════════════════════════════════════════════════════
# 股票池
# ═══════════════════════════════════════════════════════════════
class Universe:
    def __init__(self, ref_date: str, exclude_below_mv: float = 0.0):
        self.ref_date = ref_date
        self.pool = bd.get_stock_pool(exclude_st=True)
        self.codes = []
        self.name = {}
        self.industry = {}
        self.mv = {}           # ts_code -> 亿元（参考日）
        mv_map = self._bulk_mv(ref_date) if exclude_below_mv > 0 else {}
        if exclude_below_mv > 0 and not mv_map:
            # 参考日无 daily_basic 时，回退逐股（仅极端边界，正常不会触发）
            mv_map = self._fallback_mv(ref_date)
        for _, r in self.pool.iterrows():
            code = r["ts_code"]
            ld = str(r.get("list_date", "19900101"))
            if ld > ref_date:
                continue
            self.name[code] = r.get("name", code)
            self.industry[code] = r.get("industry", "")
            if exclude_below_mv > 0:
                mv = mv_map.get(code)
                if mv is None or mv < exclude_below_mv:
                    continue
                self.mv[code] = mv
            self.codes.append(code)
        self.code_set = set(self.codes)

    @staticmethod
    def _bulk_mv(ref_date):
        """单次查询参考日全市场 total_mv（万元 → 亿元），避免逐股 fetch_hist_range 慢查询。"""
        try:
            dbb = sc.get_daily_basic_by_date(str(ref_date))
        except Exception:
            return {}
        if dbb is None or dbb.empty:
            return {}
        return {
            str(r["ts_code"]): float(r["total_mv"]) / 10000.0
            for _, r in dbb.iterrows() if pd.notna(r.get("total_mv"))
        }

    @staticmethod
    def _fallback_mv(ref_date):
        """逐股回退（慢），仅在 bulk 缺失时使用。"""
        out = {}
        for code in bd.get_stock_pool(exclude_st=True)["ts_code"]:
            v = bd.load_total_mv(code, ref_date)
            if v is not None:
                out[code] = v
        return out


def build_universe(cfg, ref_date):
    u = Universe(ref_date, exclude_below_mv=cfg["universe"]["min_total_mv_yi"])
    return u


# ═══════════════════════════════════════════════════════════════
# 板块/行业上下文（每交易日全市场截面）
# ═══════════════════════════════════════════════════════════════
def build_sector_ctx(trade_date, universe):
    """返回 dict: {industry: {"mean_pct": float, "up_ratio": float}}, "_mkt_pct": float"""
    snap = snapshot_by_date(trade_date)
    ctx = {"_mkt_pct": 0.0}
    if snap is None or snap.empty:
        return ctx
    snap = snap[snap["ts_code"].isin(universe.code_set)].copy()
    if snap.empty:
        return ctx
    snap["industry"] = snap["ts_code"].map(universe.industry)
    ctx["_mkt_pct"] = float(snap["pct_chg"].mean())
    g = snap.groupby("industry")["pct_chg"]
    mean = g.mean()
    up = g.apply(lambda s: float((s > 0).mean()))
    for ind in mean.index:
        ctx[str(ind)] = {"mean_pct": float(mean[ind]), "up_ratio": float(up[ind])}
    return ctx


# ═══════════════════════════════════════════════════════════════
# 交易成本 / 成交
# ═══════════════════════════════════════════════════════════════
def cost_factor(cfg, intraday=True):
    one = cfg["cost"]["one_side_bp"]
    slip = cfg["buy"]["slippage_bp"] if intraday else 0.0
    return (one + slip) / 10000.0


def net_return(entry, exit_, cfg, intraday=True):
    f = cost_factor(cfg, intraday)
    if entry <= 0:
        return None
    return (exit_ / entry) * ((1 - f) / (1 + f)) - 1


# ═══════════════════════════════════════════════════════════════
# 指标汇总（规范 9.2 必算项）
# ═══════════════════════════════════════════════════════════════
def compute_metrics(trades, cfg):
    if not trades:
        return {"signals": 0}
    df = pd.DataFrame(trades)
    net = df["net_ret"].dropna().astype(float)
    gross = df["gross_ret"].dropna().astype(float)

    def _sub(sub):
        if sub.empty:
            return {"n": 0}
        return {
            "n": int(len(sub)),
            "avg": float(sub.mean()),
            "win": float((sub > 0).mean()),
        }

    # 最大回撤：按 exit_date 排序的等权累计净值
    eq = pd.DataFrame({"exit_date": df["exit_date"], "net_ret": df["net_ret"]})
    eq = eq.dropna().sort_values("exit_date")
    curve = (1 + eq["net_ret"]).cumprod()
    roll_max = curve.cummax()
    dd = (curve / roll_max - 1).min() if len(curve) else 0.0

    win = net[net > 0]
    loss = net[net <= 0]
    avg_win = win.mean() if len(win) else 0.0
    avg_loss = loss.mean() if len(loss) else 0.0
    pnl_ratio = (avg_win / abs(avg_loss)) if abs(avg_loss) > 1e-9 else np.nan

    by_year = df.groupby("year")["net_ret"].apply(lambda s: _sub(s.dropna())).to_dict() if "year" in df else {}
    by_regime = df.groupby("regime")["net_ret"].apply(lambda s: _sub(s.dropna())).to_dict() if "regime" in df else {}

    return {
        "signals": int(len(df)),
        "filled": int(df["fillable"].sum()) if "fillable" in df else int(len(df)),
        "fill_rate": float(df["fillable"].mean()) if ("fillable" in df and len(df)) else 1.0,
        "avg_next_open": float(df["r1_open"].mean()) if "r1_open" in df else None,
        "avg_next_close": float(df["r1_close"].mean()) if "r1_close" in df else None,
        "avg_t2_close": float(df["r2_close"].mean()) if "r2_close" in df else None,
        "avg_t3_close": float(df["r3_close"].mean()) if "r3_close" in df else None,
        "win_rate": float((net > 0).mean()) if len(net) else None,
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "pnl_ratio": None if pd.isna(pnl_ratio) else float(pnl_ratio),
        "mean_net": float(net.mean()) if len(net) else None,
        "median_net": float(net.median()) if len(net) else None,
        "std_net": float(net.std()) if len(net) else None,
        "max_drawdown": float(dd),
        "mean_gross": float(gross.mean()) if len(gross) else None,
        "by_year": by_year,
        "by_regime": by_regime,
        "unfillable": int((df["fillable"] == False).sum()) if "fillable" in df else 0,
    }


# ═══════════════════════════════════════════════════════════════
# 退出价定位
# ═══════════════════════════════════════════════════════════════
_OFFSET = {"NEXT_OPEN": 1, "NEXT_CLOSE": 1, "T2_CLOSE": 2, "T3_CLOSE": 3}


def _is_one_word_limit(df, pos, board):
    row = df.iloc[pos]
    lu = limit_up_price(row.get("pre_close"), board)
    if lu is None:
        return False
    return (abs(row["open"] - lu) < 1e-6 and abs(row["high"] - lu) < 1e-6
            and abs(row["low"] - lu) < 1e-6)


# ═══════════════════════════════════════════════════════════════
# daily 模式（消融 A/B）
# ═══════════════════════════════════════════════════════════════
def run_daily(start, end, cfg, require_pullback: bool, exit_mode=None, dedup_days=3):
    """逐股外循环，覆盖长历史；信号日T评估，T+1开盘入场。"""
    exit_mode = exit_mode or cfg["exit"]["mode"]
    ref = end if end < "20260430" else "20260430"
    uni = build_universe(cfg, ref)
    dates = bd.get_trade_dates(start, end)
    date_set = set(dates)
    trades = []
    n_signals = 0
    n_skip_limit = 0

    for ts_code in uni.codes:
        df = bd.db_daily(ts_code, "20250101", end)  # DB 单源前复权，避复权接缝
        if df is None or len(df) < 40:
            continue
        df = add_indicators(df)
        df = df[df["trade_date"] <= end].reset_index(drop=True)
        if len(df) < 40:
            continue
        pos = {d: i for i, d in enumerate(df["trade_date"])}
        board = board_of(ts_code)
        last = -999
        for T in dates:
            if T not in pos:
                continue
            i = pos[T]
            if i - last < dedup_days:
                continue
            sub = df.iloc[:i + 1]
            base = E.evaluate_daily(ts_code, sub, cfg)
            if not base["trend_ok"]:
                continue
            if require_pullback and not base["pullback_ok"]:
                continue
            # 入场：T+1 开盘
            entry_pos = i + 1
            if entry_pos >= len(df):
                continue
            if _is_one_word_limit(df, entry_pos, board):
                n_skip_limit += 1
                continue
            n_signals += 1
            last = i
            entry = float(df.iloc[entry_pos]["open"])
            exit_pos = entry_pos + _OFFSET[exit_mode]
            if exit_pos >= len(df):
                continue
            exit_row = df.iloc[exit_pos]
            exit_price = float(exit_row["open"] if exit_mode == "NEXT_OPEN" else exit_row["close"])
            trades.append(_make_trade(
                T, ts_code, uni, entry, exit_price, entry_pos, df, exit_pos, exit_mode, "daily", cfg,
            ))
    return trades, {"signals": n_signals, "skip_limit": n_skip_limit}


def _make_trade(T, ts_code, uni, entry, exit_price, entry_pos, df, exit_pos, exit_mode, mode_tag, cfg):
    gross = exit_price / entry - 1 if entry > 0 else None
    net = net_return(entry, exit_price, cfg, intraday=(mode_tag == "full"))

    def _r(offp, is_open=False):
        p = entry_pos + offp
        if p >= len(df):
            return None
        px = float(df.iloc[p]["open"] if is_open else df.iloc[p]["close"])
        return (px / entry - 1) if entry > 0 else None

    regime = bd.market_regime(T)
    return {
        "trade_date": T,
        "ts_code": ts_code,
        "name": uni.name.get(ts_code, ts_code),
        "entry_price": entry,
        "exit_price": exit_price,
        "exit_date": str(df.iloc[exit_pos]["trade_date"]),
        "gross_ret": gross,
        "net_ret": net,
        "r1_open": _r(1, True),
        "r1_close": _r(1, False),
        "r2_close": _r(2, False),
        "r3_close": _r(3, False),
        "regime": regime,
        "year": T[:4],
        "fillable": True,
        "exit_mode": exit_mode,
        "mode": mode_tag,
    }


# ═══════════════════════════════════════════════════════════════
# full 模式（消融 C/D，含尾盘 + 可选板块）
# ═══════════════════════════════════════════════════════════════
class _Caches:
    def __init__(self, end):
        self.end = end
        self.hist = {}
        self.minute = {}
        self._bench = {}

    def daily(self, ts_code):
        if ts_code not in self.hist:
            df = bd.db_daily(ts_code, "20250101", self.end)
            self.hist[ts_code] = add_indicators(df) if (df is not None and len(df) >= 40) else None
        return self.hist[ts_code]

    def anchor(self, ts_code, T):
        key = (ts_code, T)
        if key not in self.minute:
            self.minute[key] = M.build_tail_anchor(ts_code, T)
        return self.minute[key]

    def bench(self, ts_code, T, lookback):
        key = (ts_code, T)
        if key not in self._bench:
            self._bench[key] = M.tail_volume_benchmark(ts_code, T, lookback)
        return self._bench[key]


def run_full(start, end, cfg, require_sector: bool, exit_mode=None):
    exit_mode = exit_mode or cfg["exit"]["mode"]
    if require_sector:
        cfg = dict(cfg)
        cfg["_require_sector"] = True
    caches = _Caches(end)
    ref = end if end < "20260430" else "20260430"
    uni = build_universe(cfg, ref)
    dates = bd.get_trade_dates(start, end)
    min_amount_qian = cfg["universe"]["min_amount_yi"] * 1e5

    trades = []
    n_signals = 0
    n_amount_filter = 0

    for T in dates:
        snap = snapshot_by_date(T)
        if snap is None or snap.empty:
            continue
        regime = bd.market_regime(T)
        sector_ctx = _build_sector(snap, uni)
        cand = snap[snap["ts_code"].isin(uni.code_set)].copy()
        cand = cand[cand["amount"] >= min_amount_qian]
        for _, r in cand.iterrows():
            ts_code = r["ts_code"]
            df = caches.daily(ts_code)
            if df is None:
                continue
            sub = df[df["trade_date"] <= T]
            if len(sub) < 40:
                continue
            base = E.evaluate_daily(ts_code, sub, cfg)
            if not (base["trend_ok"] and base["pullback_ok"]):
                continue
            anchor = caches.anchor(ts_code, T)
            bench = caches.bench(ts_code, T, cfg["tail"]["vol_ratio_lookback"])
            sector = _sector(ts_code, r.get("pct_chg"), uni, sector_ctx)
            sig = E.evaluate_full(
                ts_code, sub, anchor, bench, regime, uni.name.get(ts_code, ts_code),
                uni.mv.get(ts_code), sector, cfg,
            )
            if sig["decision"] in ("BUY", "WATCH"):
                n_signals += 1
            if sig["decision"] != "BUY":
                continue
            # 入场：当下一根可成交分钟价（exec_next），滑点计入
            entry = sig.get("exec_price")
            if not entry or entry <= 0:
                continue
            # 定位信号日 T 在日线中的位置
            i = int(np.flatnonzero((df["trade_date"].values == T))[0]) if T in df["trade_date"].values else None
            if i is None:
                continue
            board = board_of(ts_code)
            exit_pos = i + _OFFSET[exit_mode]
            if exit_pos >= len(df):
                continue
            exit_row = df.iloc[exit_pos]
            exit_price = float(exit_row["open"] if exit_mode == "NEXT_OPEN" else exit_row["close"])
            gross = exit_price / entry - 1
            net = net_return(entry, exit_price, cfg, intraday=True)

            def _r(offp, is_open=False):
                p = i + offp
                if p >= len(df):
                    return None
                px = float(df.iloc[p]["open"] if is_open else df.iloc[p]["close"])
                return (px / entry - 1) if entry > 0 else None

            trades.append({
                "trade_date": T,
                "ts_code": ts_code,
                "name": uni.name.get(ts_code, ts_code),
                "entry_price": entry,
                "exit_price": exit_price,
                "exit_date": str(exit_row["trade_date"]),
                "gross_ret": gross,
                "net_ret": net,
                "r1_open": _r(1, True),
                "r1_close": _r(1, False),
                "r2_close": _r(2, False),
                "r3_close": _r(3, False),
                "regime": regime,
                "year": T[:4],
                "fillable": sig["risk_ok"],
                "tag": sig.get("tag"),
                "exit_mode": exit_mode,
                "mode": "full",
            })
    return trades, {"signals": n_signals, "amount_filter": n_amount_filter}


def _build_sector(snap, uni):
    snap = snap[snap["ts_code"].isin(uni.code_set)].copy()
    if snap.empty:
        return {"_mkt_pct": 0.0, "_ind": {}}
    snap["industry"] = snap["ts_code"].map(uni.industry)
    ctx = {"_mkt_pct": float(snap["pct_chg"].mean()), "_ind": {}}
    for ind, sub in snap.groupby("industry")["pct_chg"]:
        ctx["_ind"][ind] = {"mean_pct": float(sub.mean()), "up_ratio": float((sub > 0).mean())}
    return ctx


def _sector(ts_code, pct_chg, uni, ctx):
    ind = uni.industry.get(ts_code, "")
    m = ctx.get("_ind", {}).get(ind)
    if m is None:
        return None
    rel = (float(pct_chg) - m["mean_pct"]) if pd.notna(pct_chg) else None
    ind_vs_mkt = m["mean_pct"] - ctx.get("_mkt_pct", 0.0)
    return {
        "rel_strength": None if rel is None else round(rel, 2),
        "industry_vs_market": round(ind_vs_mkt, 2),
        "breadth": round(m["up_ratio"], 3),
        "ok": bool(rel is not None and rel > 0 and ind_vs_mkt > 0 and m["up_ratio"] > 0.5),
    }