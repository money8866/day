# -*- coding: utf-8 -*-
"""
天量后长线翻倍 — TDX 框架日线回测 + 今日候选精选
================================================
用途：用 TDX 框架日线（本地 C:\\new_tdx 通达信 .day，未复权真实价，与 DB daily_cache 逐价一致）
重算历史全部天量事件(D250)的前视结果，交叉验证"画像"，再据此重排今日候选，输出精选名单。

数据：事件清单取自 _longdouble_bt 扫描结果 report_daily/double_sli_pool_events.csv
      （事件日收盘为成本；前视窗口 250 交易日；翻倍=盘中最高或收盘≥2×事件收盘）
价格：bts.tdx_daily(ts_code) 全历史 .day；缺失/过短则退用 bts.load_daily 合并源。
用法：python -X utf8 double_backtest_tdx.py [--workers 8]
输出：report_daily/double_backtest_tdx.csv（逐事件 tdx 前视重算）
      report_daily/double_backtest_tdx_report.md（交叉验证 + 今日精选）
"""
import os, sys, time, argparse
from multiprocessing import Pool
import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
import bts.data as D

FWD = 250
EVENTS_CSV = os.path.join(SOLO_DIR, "report_daily/double_sli_pool_events.csv")
TRIGGER_CSV = os.path.join(SOLO_DIR, "report_daily/double_trigger.csv")
OUT_CSV = os.path.join(SOLO_DIR, "report_daily/double_backtest_tdx.csv")
OUT_MD = os.path.join(SOLO_DIR, "report_daily/double_backtest_tdx_report.md")

KEEP = ["code", "date", "close", "pct", "W", "is_limitup", "is_yizi", "streak", "p_vol", "p_turn",
        "ret20", "ret60", "ret120", "rel_hi250", "ma20_x", "ma60_x", "atr20pct", "vol_ratio",
        "turn_today", "turn_20m", "log_mv", "rs60", "first_of_cluster"]


def _forward(code, ev):
    """在 TDX 日线上重算单事件前视（成本用事件日 TDX 收盘）"""
    df = D.tdx_daily(code)
    if df is None or df.empty:
        df = D.load_daily(code, "20260904", lookback_bars=2000)
    if df is None or df.empty:
        return None
    dates = df["trade_date"].astype(str).to_numpy()
    i = int(np.searchsorted(dates, ev["date"]))
    if i >= len(dates) or dates[i] != ev["date"]:
        return None
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    c0 = float(closes[i])
    if not np.isfinite(c0) or c0 <= 0:
        return None
    W = len(df) - 1 - i
    if W < 20:
        return None
    w = min(W, FWD)
    fut_c = closes[i + 1:i + 1 + w]
    fut_h = highs[i + 1:i + 1 + w]
    fut_l = lows[i + 1:i + 1 + w]
    rel_c, rel_h = fut_c / c0, fut_h / c0
    pos_c = np.flatnonzero(rel_c >= 2.0)
    pos_h = np.flatnonzero(rel_h >= 2.0)
    return {
        "code": code, "date": ev["date"], "tdx_close": round(c0, 2),
        "mfe_w": round((float(np.max(fut_h)) / c0 - 1) * 100, 1),
        "mae_w": round((float(np.min(fut_l)) / c0 - 1) * 100, 1),
        "mae60": round((float(np.min(fut_l[:60])) / c0 - 1) * 100, 1) if w >= 60 else np.nan,
        "dbl_c": bool(len(pos_c)), "dbl_h": bool(len(pos_h)),
        "dbl_c_days": int(pos_c[0] + 1) if len(pos_c) else None,
        "dbl_h_days": int(pos_h[0] + 1) if len(pos_h) else None,
        "r20": round((closes[i + 20] / c0 - 1) * 100, 2) if W >= 20 else np.nan,
        "r60": round((closes[i + 60] / c0 - 1) * 100, 2) if W >= 60 else np.nan,
        "r120": round((closes[i + 120] / c0 - 1) * 100, 2) if W >= 120 else np.nan,
        "r250": round((closes[i + 250] / c0 - 1) * 100, 2) if W >= 250 else np.nan,
    }


def _worker(job):
    code, events = job
    recs = []
    for ev in events:
        r = _forward(code, ev)
        if r:
            recs.append(r)
    return recs


def _load_index():
    ev = pd.read_csv(EVENTS_CSV)
    ev = ev[KEEP].copy()
    ev["date"] = ev["date"].astype(str)
    idx = {}
    for code, g in ev.groupby("code"):
        idx[code] = g.to_dict("records")
    return ev, idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    ev, idx = _load_index()
    codes = sorted(idx)
    t0 = time.time()
    nw = min(args.workers, len(codes))
    chunks = [list(c) for c in np.array_split(np.asarray(codes, dtype=object), nw) if len(c)]
    jobs = [(c, idx[c]) for chunk in chunks for c in chunk]
    with Pool(nw) as pool:
        batches = pool.map(_worker, jobs)
    rows = [r for b in batches for r in b]
    print(f"TDX 重算完成 {len(codes)} 只 / {len(rows)} 事件 耗时={time.time()-t0:.0f}s", flush=True)
    if not rows:
        print("无有效事件")
        return
    fw = pd.DataFrame(rows)
    m = ev.merge(fw, on=["code", "date"], how="inner")
    m.to_csv(OUT_CSV, index=False)

    # ── 交叉验证（D250，与 stk_factor 版对比）──
    d250 = m[m.W >= 250].copy()
    n = len(d250)
    base = d250.dbl_h.mean()
    mv = 10 ** (d250.log_mv - 4)  # 亿元
    t1 = (mv <= 30) & (d250.turn_today >= 8) & (d250.rel_hi250 < -0.10) & (d250.ma60_x < 0.10) & (d250.ma20_x < 0.15)
    t2 = (mv <= 50) & (d250.turn_today >= 8)
    g_small = mv <= 30
    g_turn = d250.turn_today >= 8
    L = ["# 天量后长线翻倍 — TDX 框架日线交叉验证", "",
         f"事件：{len(m)} 个（TDX 可回放）｜D250 完整：{n} 个",
         f"整体翻倍率(TDX日线)：{base*100:.1f}%｜收盘口径翻倍：{d250.dbl_c.mean()*100:.1f}%",
         "> 旧(stk_factor)版：整体 19.3% / 收盘 17.2%｜TDX 独立日线重算与其一致即画像方向可信。", ""]
    L += ["## 一、画像在 TDX 日线上的翻倍率", "",
          "| 条件组 | n | TDX翻倍率 | 备注 |", "|---|---|---|---|",
          f"| 全体 D250 | {n} | {base*100:.1f}% | 基准 |",
          f"| 流通≤30亿 | {int(g_small.sum())} | {d250[g_small].dbl_h.mean()*100:.1f}% | 旧32.8% |",
          f"| 当日换手≥8% | {int(g_turn.sum())} | {d250[g_turn].dbl_h.mean()*100:.1f}% | 旧24.6% |",
          f"| TIER1核心画像 | {int(t1.sum())} | {d250[t1].dbl_h.mean()*100:.1f}% | 旧≈38% |",
          f"| TIER2基础画像 | {int(t2.sum())} | {d250[t2].dbl_h.mean()*100:.1f}% | 旧≈28-33% |", ""]

    # 单调性抽查：市值分位
    q = pd.qcut(mv.rank(method="first"), 4, labels=["Q1小", "Q2", "Q3", "Q4大"])
    L += ["**流通市值分桶(TDX)**："] + [f"- {lab}: n={int((q==lab).sum())}, 翻倍 {d250[q==lab].dbl_h.mean()*100:.0f}%" for lab in ["Q1小", "Q2", "Q3", "Q4大"]]
    q2 = pd.qcut(d250.turn_today.rank(method="first"), 4, labels=["T1低", "T2", "T3", "T4高"])
    L += ["", "**事件日换手分桶(TDX)**："] + [f"- {lab}: n={int((q2==lab).sum())}, 翻倍 {d250[q2==lab].dbl_h.mean()*100:.0f}%" for lab in ["T1低", "T2", "T3", "T4高"]]

    # ── 今日候选精选 ──
    tr = pd.read_csv(TRIGGER_CSV)
    tr["code"] = tr["code"].astype(str).str.strip()
    tr["date"] = tr["date"].astype(str).str.strip()
    tr = tr[tr.tier.notna()]
    L += ["", "## 二、今日候选 × 个股自身历史画像事件回测（TDX 前视）", "",
          "| 排名 | 名称 | 今日 | 历史天量(n) | 其中画像(n) | 画像翻倍率 | 画像均T+120 | 画像均T+250 | 精选 |", "|---|---|---|---|---|---|---|---|---|"]
    hist = m[m["date"] < tr["date"].min()] if len(tr) else m
    rank_rows = []
    for r in tr.itertuples():
        code = r.code
        sub = hist[hist.code == code]
        if len(sub):
            smv = 10 ** (sub.log_mv - 4)
            sub = sub.copy()
            sub["_t1"] = (smv <= 30) & (sub.turn_today >= 8) & (sub.rel_hi250 < -0.10) & (sub.ma60_x < 0.10) & (sub.ma20_x < 0.15)
            sub["_t2"] = (smv <= 50) & (sub.turn_today >= 8)
            pic = sub[(sub._t1) | (sub._t2)]
        else:
            pic = sub.copy()
        nh = len(sub)
        npic = len(pic)
        dblrate = pic.dbl_h.mean() if npic else np.nan
        r120 = pic.r120.mean() if npic else np.nan
        r250 = pic.r250.dropna().mean() if npic and pic.r250.notna().any() else np.nan
        # 精选规则：画像样本≥2 且翻倍率≥25%；或 画像≥5 且翻倍率≥15%；无样本看簇首与结构
        if npic >= 2 and dblrate >= 0.25:
            tag = "保留★"
        elif npic >= 5 and dblrate >= 0.15:
            tag = "保留★"
        elif npic == 0 and bool(r.first_of_cluster) and r.ma60_x < 0.10:
            tag = "观察"
        else:
            tag = "待验证"
        rank_rows.append((dblrate if np.isfinite(dblrate) else -1, r.code, r.name, r.tier, nh, npic, dblrate, r120, r250, tag))
    rank_rows.sort(key=lambda x: (x[0], x[5]), reverse=True)
    for i, (_, code, name, tier, nh, npic, dblrate, r120, r250, tag) in enumerate(rank_rows, 1):
        ds = f"{dblrate*100:.0f}%" if np.isfinite(dblrate) else "-"
        r1 = f"{r120:.0f}%" if np.isfinite(r120) else "-"
        r2 = f"{r250:.0f}%" if np.isfinite(r250) else "-"
        L.append(f"| {i} | {name} {code[:6]} | {tier} | {nh} | {npic} | {ds} | {r1} | {r2} | {tag} |")
    L += ["", "> 精选规则：个股自身历史≥2次同类画像事件且翻倍率≥25%（或≥5次且≥15%）→保留；无历史样本但今日为簇首+结构未破位→观察；其余→待验证。",
         "> 口径：翻倍=事件日后250日内盘中最高≥2×事件收盘(TDX)。历史画像事件含W<250的已结束事件。"]
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("写出:", OUT_MD)
    print("\n".join(L[:40]))


if __name__ == "__main__":
    main()
