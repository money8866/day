# -*- coding: utf-8 -*-
"""
天量后长线翻倍研究（SLI 细分龙头池版）
=====================================
口径：
  - 股票池：SLI V2 细分赛道 Top5（359 赛道，最新快照 20260901，1577 只）。注：池为当前快照，
    回看 2024-2025 事件存在前视（这些股现在仍是/已成为龙头），翻倍率偏乐观，特征方向仍有效。
  - 天量事件：V5 口径——事件日前 120 日分位，量能(vol)+换手(turnover_rate_f) 双≥P99。
  - 买入成本：事件日收盘价。
  - 翻倍：事件日后 W=min(250, 可观察交易日) 内，盘中高点≥2× 或 收盘价≥2× 事件收盘。
  - 主结论池 D250：事件日距数据末 ≥250 根（事件日≈20240101~20250815，保证整段 250 日观察）。
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from w7_second_wave_engine import CacheReader, DATA_START
from sli.reader import get_subsector_top5

EV_MIN_DATE = "20240101"     # 事件最早日
CTX_BARS = 250               # 事件前上下文根数（保证 250 日高低点/连板等特征）
FWD_CAP = 250                # 最长前视交易日
POOL_P99 = 99.0

_MKT = None  # {date: 等权累计净额}
_SUB = {}    # code -> subsector


def _limit_pct(code):
    if code[:3] in ("300", "301", "688"):
        return 19.5
    if code.startswith("8") or code.startswith("4"):
        return 29.5
    return 9.5


def load_market(reader):
    """全市场等权累计净额序列（日级），用于超额收益基准；走引擎增量 CSV 缓存，避免每次全表聚合"""
    dates, cum = reader.market_curve("20260904", DATA_START)
    return dict(zip(dates, cum))


def process_stock(code, df):
    """返回该股的事件记录列表（含事件特征与前视结果）"""
    dates = df.trade_date.astype(str).to_numpy()
    closes = pd.to_numeric(df.close, errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(df.high, errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(df.low, errors="coerce").to_numpy(dtype=float)
    opens = pd.to_numeric(df.open, errors="coerce").to_numpy(dtype=float)
    vols = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)
    trf = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0).to_numpy(dtype=float)
    mv = pd.to_numeric(df.circ_mv, errors="coerce").fillna(np.nan).to_numpy(dtype=float)
    pct = pd.to_numeric(df.pct_chg, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(df)
    if n < CTX_BARS + 80:
        return []
    lim = _limit_pct(code)
    # 事件索引：前120日分位 双≥P99
    ev_idx = []
    for i in range(CTX_BARS, n - 1):
        if dates[i] < EV_MIN_DATE:
            continue
        s, e = i - 120, i
        p_t = float(np.mean(trf[s:e] <= trf[i]) * 100.0)
        p_v = float(np.mean(vols[s:e] <= vols[i]) * 100.0)
        if min(p_t, p_v) >= POOL_P99:
            ev_idx.append(i)
    out = []
    last_ev = -10**9
    for i in ev_idx:
        W = n - 1 - i
        if W < 60:
            continue
        c0 = closes[i]
        if not np.isfinite(c0) or c0 <= 0:
            last_ev = i
            continue
        # ── 前视窗口（W 截断到 FWD_CAP，主结论仅用 W>=250 的事件）──
        w = min(W, FWD_CAP)
        fut_h = highs[i + 1:i + w + 1]
        fut_l = lows[i + 1:i + w + 1]
        fut_c = closes[i + 1:i + w + 1]
        mfe_w = float(np.max(fut_h)) / c0 - 1.0
        mae_w = float(np.min(fut_l)) / c0 - 1.0
        mae60 = float(np.min(fut_l[:60])) / c0 - 1.0 if w >= 60 else np.nan
        mae20 = float(np.min(fut_l[:20])) / c0 - 1.0 if w >= 20 else np.nan
        # 翻倍判定（收盘/盘中），W>=250 才算完整
        rel_c = fut_c / c0
        rel_h = fut_h / c0
        pos_c = np.flatnonzero(rel_c >= 2.0)
        pos_h = np.flatnonzero(rel_h >= 2.0)
        dbl_c = bool(len(pos_c)) if W >= 250 else None
        dbl_h = bool(len(pos_h)) if W >= 250 else None
        dbl_c_days = int(pos_c[0] + 1) if len(pos_c) else None
        dbl_h_days = int(pos_h[0] + 1) if len(pos_h) else None
        # 阶段收益
        def ret_h(h):
            if W >= h:
                return closes[i + h] / c0 - 1.0
            return np.nan
        # ── 事件特征（仅用 ≤i 数据）──
        s = i - 120
        p_t = float(np.mean(trf[s:i] <= trf[i]) * 100.0)
        p_v = float(np.mean(vols[s:i] <= vols[i]) * 100.0)
        # 连板（含当日涨停）
        streak = 0
        j = i
        while j >= 0 and pct[j] >= lim:
            streak += 1
            j -= 1
        is_limitup = pct[i] >= lim
        is_yizi = bool(is_limitup and opens[i] > 0 and abs(highs[i] - lows[i]) < 1e-8)
        # 前期涨幅 / 距前高
        def pre_ret(k):
            return closes[i] / closes[i - k] - 1.0 if i >= k and closes[i - k] > 0 else np.nan
        ma20 = float(np.mean(closes[i - 19:i + 1])) if i >= 19 else c0
        ma60 = float(np.mean(closes[i - 59:i + 1])) if i >= 59 else c0
        hi250 = float(np.max(highs[max(0, i - 249):i + 1]))
        hi120 = float(np.max(highs[max(0, i - 119):i + 1]))
        tr_prev = np.maximum(highs[1:i + 1] - lows[1:i + 1],
                             np.maximum(np.abs(highs[1:i + 1] - closes[:i]),
                                        np.abs(lows[1:i + 1] - closes[:i])))
        atr20 = float(np.mean(tr_prev[-20:])) if i >= 20 else np.nan
        vol20m = float(np.mean(vols[i - 20:i])) if i >= 20 else 0.0
        trf20m = float(np.mean(trf[i - 20:i])) if i >= 20 else np.nan
        # 超额收益（vs 全市场等权）
        mkt_c, mkt_p = _MKT.get(dates[i], np.nan), _MKT.get(dates[i - 60] if i >= 60 else dates[0], np.nan)
        rs60 = (1 + pre_ret(60)) / (mkt_c / mkt_p) - 1.0 if (np.isfinite(mkt_c) and np.isfinite(mkt_p) and mkt_p > 0) else np.nan
        rec = {
            "code": code, "subsector": _SUB.get(code, ""), "date": dates[i],
            "W": W,
            "close": round(c0, 2), "pct": round(pct[i], 2),
            "is_limitup": is_limitup, "is_yizi": is_yizi, "streak": streak,
            "p_vol": round(p_v, 1), "p_turn": round(p_t, 1),
            "ret20": round(pre_ret(20) * 100, 2), "ret60": round(pre_ret(60) * 100, 2),
            "ret120": round(pre_ret(120) * 100, 2),
            "rel_hi250": round(c0 / hi250 - 1, 3), "rel_hi120": round(c0 / hi120 - 1, 3),
            "ma20_x": round(c0 / ma20 - 1, 3), "ma60_x": round(c0 / ma60 - 1, 3),
            "atr20pct": round(atr20 / c0 * 100, 2) if np.isfinite(atr20) and atr20 > 0 else np.nan,
            "vol_ratio": round(vols[i] / vol20m, 2) if vol20m > 0 else np.nan,
            "turn_today": round(trf[i], 2), "turn_20m": round(trf20m, 2) if np.isfinite(trf20m) else np.nan,
            "log_mv": round(np.log10(mv[i]), 2) if np.isfinite(mv[i]) else np.nan,
            "rs60": round(rs60 * 100, 1) if np.isfinite(rs60) else np.nan,
            "gap_last_ev": i - last_ev,
            "first_of_cluster": bool(i - last_ev >= 20),
            # 前视
            "r20": round(ret_h(20) * 100, 2), "r60": round(ret_h(60) * 100, 2),
            "r120": round(ret_h(120) * 100, 2), "r250": round(ret_h(250) * 100, 2),
            "mfe_w": round(mfe_w * 100, 1), "mae_w": round(mae_w * 100, 1),
            "mae60": round(mae60 * 100, 1) if np.isfinite(mae60) else np.nan,
            "mae20": round(mae20 * 100, 1) if np.isfinite(mae20) else np.nan,
            "dbl_c": dbl_c, "dbl_h": dbl_h,
            "dbl_c_days": dbl_c_days, "dbl_h_days": dbl_h_days,
        }
        out.append(rec)
        last_ev = i
    return out


def _init_worker(mkt, sub):
    global _MKT, _SUB
    _MKT = mkt
    _SUB = sub


def _worker(chunk_codes):
    reader = CacheReader()
    reader.load_all("20260904", codes=chunk_codes, min_date="20230101")
    rows = []
    for code in chunk_codes:
        df = reader.frames.get(code)
        if df is None or df.empty:
            continue
        rows.extend(process_stock(code, df))
    reader.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=str, default="report_daily/double_sli_pool_events.csv")
    args = ap.parse_args()

    panel = get_subsector_top5()
    sub_df = panel[["ts_code", "subsector"]].drop_duplicates("ts_code")
    sub_df["ts_code"] = sub_df["ts_code"].astype(str).str.strip()
    submap = dict(zip(sub_df.ts_code, sub_df.subsector))
    codes = sorted(submap.keys())
    if args.limit:
        codes = codes[:args.limit]
    print(f"股票池={len(codes)} 赛道={panel['subsector'].nunique()} 快照=20260901", flush=True)

    # 市场基准由主进程预取后注入
    probe = CacheReader()
    mkt = load_market(probe)
    probe.close()
    print(f"市场基准交易日={len(mkt)}", flush=True)

    t0 = time.time()
    nw = min(args.workers, len(codes)) if codes else 1
    chunks = np.array_split(np.asarray(codes, dtype=object), nw)
    chunks = [list(c) for c in chunks if len(c)]
    if args.workers > 1 and nw > 1:
        with Pool(nw, initializer=_init_worker, initargs=(mkt, submap)) as pool:
            batches = pool.map(_worker, chunks)
    else:
        _init_worker(mkt, submap)
        batches = [_worker(c) for c in chunks]
    rows = [r for b in batches for r in b]
    print(f"完成 {len(codes)} 只 事件={len(rows)} 耗时={time.time()-t0:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df.to_csv(args.out, index=False)
    d250 = df[df.W >= 250]
    print("写出:", args.out, "| D250 完整观察事件=%d" % len(d250))


if __name__ == "__main__":
    main()
