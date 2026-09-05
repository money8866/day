# -*- coding: utf-8 -*-
"""
天量后长线翻倍画像 · 每日盘后触发扫描
=================================
画像来自 report_daily/double_analysis.md（D250 完整观察 14797 事件的显著特征）：
  TIER1（核心，翻倍率≈38%）：
    流通市值≤30亿 + 事件日换手≥8% + 距250日高<-10%（回踩/中继）+ MA60乖离<10% + MA20乖离<15%
  TIER2（基础基因，≈28-33%）：
    流通市值≤50亿 + 事件日换手≥8%
用法：python _daily_double_scan.py [--asof 20260904] [--days 5] [--workers 8]
  - 事件口径：V5 天量 = 前120日分位量能(vol)+换手(turnover_rate_f)双≥P99
  - 入口：事件日收盘；扫描在收盘后跑，命中即列入观察名单
  - 池：SLI top5 细分龙头池（359 赛道×5）
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

try:
    from market_regime_v3.wechat_push import send_pushplus
except Exception:
    send_pushplus = None

POOL_P99 = 99.0
CTX = 250  # 事件前上下文（保证 250 日高低点/连板等特征）

_SUB = {}
_NAME = {}


def _limit_pct(code):
    if code[:3] in ("300", "301", "688"):
        return 19.5
    if code.startswith("8") or code.startswith("4"):
        return 29.5
    return 9.5


def _last_event_rec(code, df, asof):
    """只看该股最后一日（asof 收盘）：若当日触发 V5 天量，返回特征记录；否则 None"""
    dates = df.trade_date.astype(str).to_numpy()
    if not len(dates) or dates[-1] > asof:
        return None
    if dates[-1] < asof:            # 停牌/未更新：不构成当日触发
        return None
    closes = pd.to_numeric(df.close, errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(df.high, errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(df.low, errors="coerce").to_numpy(dtype=float)
    opens = pd.to_numeric(df.open, errors="coerce").to_numpy(dtype=float)
    vols = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)
    trf = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0).to_numpy(dtype=float)
    mv = pd.to_numeric(df.circ_mv, errors="coerce").fillna(np.nan).to_numpy(dtype=float)
    pct = pd.to_numeric(df.pct_chg, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(df)
    i = n - 1
    if i < CTX:
        return None
    lim = _limit_pct(code)
    c0 = closes[i]
    if not np.isfinite(c0) or c0 <= 0:
        return None
    s = i - 120
    p_t = float(np.mean(trf[s:i] <= trf[i]) * 100.0)
    p_v = float(np.mean(vols[s:i] <= vols[i]) * 100.0)
    if min(p_t, p_v) < POOL_P99:    # 当日非天量 → 不触发
        return None
    # 是否簇首：向前 20 日之内是否还有天量日（简化回溯）
    first_of_cluster = True
    for j in range(i - 1, max(s, i - 20) - 1, -1):
        if np.mean(trf[j - 120:j] <= trf[j]) * 100 >= POOL_P99 and np.mean(vols[j - 120:j] <= vols[j]) * 100 >= POOL_P99:
            first_of_cluster = False
            break
    streak = 0
    j = i
    while j >= 0 and pct[j] >= lim:
        streak += 1
        j -= 1
    def pre_ret(k):
        return closes[i] / closes[i - k] - 1.0 if i >= k and closes[i - k] > 0 else np.nan
    ma20 = float(np.mean(closes[i - 19:i + 1]))
    ma60 = float(np.mean(closes[i - 59:i + 1]))
    hi250 = float(np.max(highs[i - 249:i + 1]))
    tr_prev = np.maximum(highs[1:i + 1] - lows[1:i + 1],
                         np.maximum(np.abs(highs[1:i + 1] - closes[:i]),
                                    np.abs(lows[1:i + 1] - closes[:i])))
    atr20 = float(np.mean(tr_prev[-20:]))
    vol20m = float(np.mean(vols[i - 20:i]))
    trf20m = float(np.mean(trf[i - 20:i]))
    mv_yi = float(mv[i]) / 1e4 if np.isfinite(mv[i]) else np.nan     # circ_mv(万元)→亿元
    return {
        "code": code, "name": _NAME.get(code, ""), "subsector": _SUB.get(code, ""),
        "date": dates[i], "close": round(c0, 2), "pct": round(pct[i], 2),
        "p_vol": round(p_v, 1), "p_turn": round(p_t, 1),
        "is_limitup": bool(pct[i] >= lim), "streak": streak, "is_yizi": bool(pct[i] >= lim and opens[i] > 0 and abs(highs[i] - lows[i]) < 1e-8),
        "first_of_cluster": bool(first_of_cluster),
        "mv_yi": round(mv_yi, 1) if np.isfinite(mv_yi) else np.nan,
        "turn_today": round(float(trf[i]), 2), "turn_20m": round(trf20m, 2) if np.isfinite(trf20m) else np.nan,
        "vol_ratio": round(float(vols[i] / vol20m), 2) if vol20m > 0 else np.nan,
        "ret20": round(pre_ret(20) * 100, 1), "ret60": round(pre_ret(60) * 100, 1),
        "rel_hi250": round(c0 / hi250 - 1, 3), "ma20_x": round(c0 / ma20 - 1, 3), "ma60_x": round(c0 / ma60 - 1, 3),
        "atr20pct": round(atr20 / c0 * 100, 2) if atr20 > 0 else np.nan,
    }


def _init_worker(sub, name):
    global _SUB, _NAME
    _SUB, _NAME = sub, name


def _worker(job):
    chunk, asof = job
    reader = CacheReader()
    reader.load_all(asof, codes=chunk, min_date="20230101")
    out = []
    for code in chunk:
        df = reader.frames.get(code)
        if df is None or df.empty:
            continue
        rec = _last_event_rec(code, df, asof)
        if rec:
            out.append(rec)
    reader.close()
    return out


def tier(rec):
    mv = rec["mv_yi"]
    t = rec["turn_today"]
    if mv is None or not np.isfinite(mv) or not np.isfinite(t):
        return None
    g = [mv <= 30 and t >= 8]
    if g[0] and rec["rel_hi250"] < -0.10 and rec["ma60_x"] < 0.10 and rec["ma20_x"] < 0.15:
        return "TIER1_核心"
    if mv <= 50 and t >= 8:
        return "TIER2_基础"
    return None


def build_push_md(df, asof):
    """构建微信推送 markdown（仅命中画像的行）"""
    hit = df[df.tier.notna()].copy().sort_values("tier", ascending=False)
    if not len(hit):
        return None
    L = [f"**天量翻倍画像 · 盘后触发名单**", "",
         f"交易日 {asof}｜命中 {len(hit)} 只（TIER1={int((hit.tier=='TIER1_核心').sum())} / TIER2={int((hit.tier=='TIER2_基础').sum())}）",
         "> 口径：V5天量(量+换手双≥P99)；历史D250翻倍率 整体19% / 核心画像≈38%；仅供研究观察。", ""]
    for tier in ("TIER1_核心", "TIER2_基础"):
        sub = hit[hit.tier == tier]
        if not len(sub):
            continue
        L.append(f"**{tier}**" if tier == "TIER1_核心" else "**TIER2_基础**")
        for r in sub.itertuples():
            mv = f"{r.mv_yi:.0f}亿" if pd.notna(r.mv_yi) else "-"
            L.append(f"- {r.name} {r.code[:6]}｜{r.subsector}｜收{r.close:.2f}({r.pct:+.1f}%)")
            L.append(f"  流通{mv}｜换手{r.turn_today:.1f}%(常态{r.turn_20m:.1f}%)｜距250日高{r.rel_hi250*100:.0f}%｜"
                     f"MA20{r.ma20_x*100:+.1f}%｜MA60{r.ma60_x*100:+.1f}%｜"
                     f"{'涨停' if r.is_limitup else '非涨停'}｜{'簇首' if r.first_of_cluster else '非簇首'}")
        L.append("")
    L.append("---")
    L.append("执行：SLI细分龙头池每日盘后天量翻倍画像扫描")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="", help="留空=自动取库内最新交易日")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--push", action="store_true", help="命中画像时推送到微信")
    ap.add_argument("--out", default="report_daily/double_trigger.csv")
    args = ap.parse_args()

    probe = CacheReader()
    if args.asof:
        asof = args.asof
    else:
        mx = probe.conn.execute("SELECT MAX(trade_date) FROM stk_factor_pro").fetchone()
        asof = str(mx[0])
        print(f"自动检测最新交易日 asof={asof}", flush=True)
    probe.close()

    panel = get_subsector_top5()
    sub_df = panel[["ts_code", "subsector", "name"]].drop_duplicates("ts_code")
    sub_df["ts_code"] = sub_df["ts_code"].astype(str).str.strip()
    submap = dict(zip(sub_df.ts_code, sub_df.subsector))
    namemap = dict(zip(sub_df.ts_code, sub_df.name))
    codes = sorted(submap.keys())
    print(f"股票池={len(codes)} asof={asof} 扫描中...", flush=True)

    t0 = time.time()
    nw = min(args.workers, len(codes))
    chunks = [list(c) for c in np.array_split(np.asarray(codes, dtype=object), nw) if len(c)]
    with Pool(nw, initializer=_init_worker, initargs=(submap, namemap)) as pool:
        batches = pool.map(_worker, [(c, asof) for c in chunks])
    recs = [r for b in batches for r in b]
    print(f"天量事件={len(recs)} 耗时={time.time()-t0:.0f}s", flush=True)

    if not recs:
        print("当日无天量触发。")
        return
    df = pd.DataFrame(recs)
    df["tier"] = df.apply(tier, axis=1)
    hit = df[df.tier.notna()].copy()
    miss = df[df.tier.isna()].copy()
    df = pd.concat([hit, miss])
    df = df.sort_values(["tier", "mv_yi"], ascending=[False, True], na_position="last")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df.to_csv(args.out, index=False)
    print("写出:", args.out, "| 命中画像=%d (TIER1=%d TIER2=%d) | 未命中=%d" % (
        len(hit), int((df.tier == "TIER1_核心").sum()), int((df.tier == "TIER2_基础").sum()), len(miss)))
    cols = ["code", "name", "subsector", "date", "tier", "close", "pct", "mv_yi", "turn_today",
            "turn_20m", "vol_ratio", "p_vol", "p_turn", "rel_hi250", "ma20_x", "ma60_x", "ret60",
            "streak", "is_limitup", "first_of_cluster"]
    show = df[cols]
    print("\n" + show.to_string(index=False))

    if args.push and len(hit):
        md = build_push_md(df, asof)
        if md and send_pushplus is not None:
            send_pushplus(md, title=f"天量翻倍画像 {asof} 触发{len(hit)}只")


if __name__ == "__main__":
    main()
