# -*- coding: utf-8 -*-
"""W7 第三类信号：BREAKOUT_RETEST 优化版（逆势缩量回踩低吸）。

定位：独立于新口径两类（w7_ab）的**第三类**机会，挂独立 strategy_id='w7_retest'，
不改动 w7_ab 主链。目的是用 run_all.bat 每日自动积累样本，攒够 30 条后做正式复检。

================================================================================
【重要】标定结论已被 20261028 前瞻回测修正，本文档以「实测」为准
================================================================================

原标定（20261028 早，基于 w7_hvt 下 BREAKOUT_RETEST 98 条既有信号事后筛选）声称：
    组合条件（volr<0.60 且 大盘20日<0，n=17）：T+3 +4.89% 胜64.7% | T+5 +5.47%
该结论**已被证伪**，原因有二：
  ① 样本池太小（n=17），且阈值与筛选条件均从同一批数据挖出 → 典型过拟合；
  ② 标定池是「引擎状态机已产出的 BREAKOUT_RETEST」子集，而从零扫描全市场得到的
     样本池宽松 5 倍（284 vs 57），两者根本不是同一个东西。

20261028 前瞻回测（20260601~20261027 全部 66 个逆势交易日，独立 strategy_id
'w7_retest_bt'，257 条带完整 T+N 的样本）实测结果：
    T+1 +0.18%/胜49.8% | T+3 +0.34%/胜53.7%/超+0.30% | T+5 +0.24% | T+10 −1.46%/超−0.50%
→ 远不及标定声称的 +4.89%。且 volr 分档的单调区分度完全消失。

【关键发现】age（突破日距信号日的 K 线数）才是真正的筛选因子：
    age 0-3   n=30  T+3 −2.09%/胜43%  T+10 −7.83%/胜17%   ← 灾难
    age 4-6   n=64  T+3 +0.12%/胜61%
    age 7-10  n=76  T+3 +0.37%/胜43%
    age 11-15 n=87  T+3 +1.31%/胜61%
引擎的 RETEST_MAX_AGE=15 只约束上限、不约束下限 → 突破后第 1 天就买 = 没回踩完
= 接在突破当天的高位刀口上。这是原形态最致命的漏洞，也是标定没抓到的原因
（标定池来自引擎，引擎当时恰好没在 age 很小的日子发出信号）。

加入 RETEST_MIN_AGE=8 后（样本外验证：训练段 <20260901 / 测试段 ≥20260901）：
    age>=8  n=145  T+3 +1.02%/超+1.01%  T+5 +1.35%/超+1.43%  T+10 −0.01%/超+1.10%
    训练段 n=56   T+3 −0.36%/超−0.17%   T+10 −1.87%/超−1.90%
    测试段 n=89   T+3 +1.89%/超+1.75%   T+5 +2.79%/超+2.90%  T+10 +1.29%/超+3.25%
→ 测试段稳定为正（训练段为负说明阈值来自测试段，仍有过拟合风险，需继续积累样本）。

当前有效门槛（四条缺一不可）：
    ① 量比硬门槛 volr < 0.60          —— 选股日量 / 前20日均量
    ② 逆势环境门 上证 20 日涨跌幅 < 0  —— 该形态是「强势股回踩」，市场越弱越有效
    ③ 回踩新鲜度 age >= 8 根            —— 必须真回踩完，不能买在突破当天/次日
    ④ 结构止损 max(结构位, close×0.95)  —— 优于固定 −5%(−3.31%)/−8%(−4.07%)
另加流动性下限 5000 万元（与新口径 W7_T0_MIN_AMOUNT 一致）。

局限：模块当前样本 145 条（age>=8），但「age>=8」这一条本身是从这批样本里扫出来的，
      严格说仍属样本内调参 → 已用它做了时间切分（训练段为负、测试段为正，说明
      阈值位置仍偏乐观）。结论：可以小仓位观察，不可当成熟策略实盘。

用法：
  python w7_retest_signal.py --date 20261028     # 产出当日信号并落库
  python w7_retest_signal.py --date 20261028 --dry-run   # 只打印不落库
  python w7_retest_signal.py --date 20261001,20261002 --dry-run   # 多日批量（共享一次行情加载）
"""
import argparse
import json
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import w7_second_wave_engine as w7

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
PICK_DB = os.path.join(HERE, "picks_db", "stock_picks.db")

STRATEGY_ID = "w7_retest"
STRATEGY_NAME = "W7 逆势缩量回踩低吸(BREAKOUT_RETEST优化版)"

# ── 标定阈值（勿随意改，改前须重跑 _tmp_w7_retest_eval.py 复检）──
RETEST_VOLR_MAX = 0.60       # 量比硬门槛：选股日量 / 前20日均量 < 0.60
RETEST_MKT_R20_MAX = 0.0     # 逆势门：上证 20 日涨跌幅 < 0
RETEST_STOP_FLOOR = 0.95     # 结构止损下限：max(结构位, close×0.95)
RETEST_MIN_AMOUNT = 50000.0  # 流动性下限：当日成交额 ≥ 50000 千元（amount 单位＝千元 → 5000 万元），与新口径 W7_T0_MIN_AMOUNT 一致
RETEST_MIN_BARS = 120        # 最少 K 线（比引擎 MIN_BARS 宽，因本形态不依赖天量锚）
RETEST_MIN_AGE = 8           # 回踩新鲜度下限：突破日距信号日 ≥8 根
#   （20261028 前瞻回测 257 条实测：age 0-3 → T+3 −2.09%/胜43%/T+10 −7.83%；
#     age 4-6 → +0.12%；age 7-10 → +0.37%；age 11-15 → +1.31%/胜61%。
#     引擎 RETEST_MAX_AGE=15 只管上限不管下限，是原形态最致命的漏洞：
#     突破后第 1 天就买 = 没回踩完 = 接在突破当天的高位刀口上。）


def market_r20(conn, trade_date):
    """上证 20 日涨跌幅（相对选股日）。用于逆势门。"""
    q = ("SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? "
         "AND trade_date<=? ORDER BY trade_date")
    try:
        df = pd.read_sql_query(q, conn, params=[w7.MARKET_INDEX_CODE, str(trade_date)])
    except Exception:
        return None
    if len(df) < 21:
        return None
    c = df.close.to_numpy(dtype=float)
    return float(c[-1] / c[-21] - 1.0)


def load_frames(conn, trade_date, lookback=420):
    """读取截止 trade_date 的行情 + 均线。

    只取最近 lookback 根 K 线（结构判定最多用到 MA120 + T0 锚 P99 分位窗口 + 突破日
    15 根新鲜度，420 根留足余量）。全历史有 719 万行，读取+均线计算是主要耗时来源，
    截断后加载量降为 1/4。
    """
    # 先定位日期下界
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (str(trade_date), int(lookback))).fetchall()]
    if not dates:
        return pd.DataFrame()
    lo = dates[-1]
    sql = ("SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pct_chg, "
           "       d.vol, d.amount, a.adj_factor "
           "FROM daily_cache d LEFT JOIN adj_factor_cache a "
           "  ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
           "WHERE d.trade_date>=? AND d.trade_date<=? ORDER BY d.ts_code, d.trade_date")
    df = pd.read_sql_query(sql, conn, params=[lo, str(trade_date)])
    df = df.dropna(subset=["close"])
    return w7._fill_ma_columns(df)


def _to_arrays(g):
    """把单只个股 DataFrame 转成 numpy 数组，供向量化预筛使用。"""
    return {
        "open": g.open.to_numpy(dtype=float),
        "high": g.high.to_numpy(dtype=float),
        "low": g.low.to_numpy(dtype=float),
        "close": g.close.to_numpy(dtype=float),
        "vol": g.vol.to_numpy(dtype=float),
        "amount": g.amount.to_numpy(dtype=float),
        "ma20": (g.ma_bfq_20.to_numpy(dtype=float)
                 if "ma_bfq_20" in g.columns else np.full(len(g), np.nan)),
        "trade_date": g.trade_date.astype(str).to_numpy(),
    }


def prescreen(arr, min_bars):
    """向量化预筛：只做 numpy 运算，返回是否可能满足优化版 BREAKOUT_RETEST。

    预筛条件（都是必要条件，不改变判定语义，只是提前淘汰）：
      ① 量比 < 0.60            —— 硬门槛
      ② 成交额 ≥ 5000 万        —— 流动性
      ③ 现价 ≥ MA20 × 0.97     —— breakout_retest 条件⑦的必要条件
      ④ 现价距 60 日高 ≤ 1.10   —— 条件③「不追高」的必要条件

    实测可淘汰 95%+ 的股票，把昂贵的 find_event_anchor / breakout_retest
    调用量从 5774 只降到百只量级。
    """
    n = len(arr["close"])
    if n < min_bars:
        return False
    last = n - 1
    close = arr["close"]
    vol = arr["vol"]
    if not np.isfinite(close[last]) or close[last] <= 0:
        return False

    # ① 量比：选股日量 / 前20日均量（不含当日）
    v20 = arr["vol"][max(0, last - 19):last]
    v20 = v20[np.isfinite(v20)]
    if v20.size == 0:
        return False
    base = v20.mean()
    if base <= 0 or not np.isfinite(vol[last]):
        return False
    if vol[last] / base >= RETEST_VOLR_MAX:
        return False

    # ② 流动性
    amt = arr["amount"][last]
    if np.isfinite(amt) and 0 < amt < RETEST_MIN_AMOUNT:
        return False

    # ③ 现价 ≥ MA20 × RETEST_MA20_K
    ma20 = arr["ma20"][last]
    if np.isfinite(ma20) and ma20 > 0:
        if close[last] < ma20 * w7.RETEST_MA20_K:
            return False

    # ④ 不追高：现价 ≤ 近 60 日最高收盘 × RETEST_MAX_EXT
    #    突破日 B 必然是 60 日内的新高，故用 60 日高点作上界是必要条件
    hi60 = arr["close"][max(0, last - 59):last + 1]
    hi60 = hi60[np.isfinite(hi60)]
    if hi60.size:
        if close[last] > hi60.max() * w7.RETEST_MAX_EXT:
            return False

    return True


def detect_one(g, r20):
    """对单只个股判定当日是否满足优化版 BREAKOUT_RETEST。返回信号 dict 或 None。"""
    n = len(g)
    if n < RETEST_MIN_BARS:
        return None
    last = n - 1
    latest = g.iloc[last]
    close = float(latest.close)
    if close <= 0:
        return None
    amount = float(latest.get("amount", 0.0) or 0.0)
    if 0 < amount < RETEST_MIN_AMOUNT:
        return None

    # ① 量比硬门槛：选股日量 / 前20日均量（不含当日）
    vol20 = w7.safe_mean(g.vol.iloc[max(0, last - 19):last])
    if vol20 <= 0:
        return None
    volr = float(latest.vol) / float(vol20)
    if volr >= RETEST_VOLR_MAX:
        return None

    # ② 逆势门
    if r20 is None or r20 >= RETEST_MKT_R20_MAX:
        return None

    # ③ 结构判定：T0 锚 → 放量突破日 B → 缩量回踩（沿用引擎 breakout_retest 结构条件）
    anchor = w7.find_event_anchor(g)
    if anchor:
        ev = w7.preshift_start_bar(g, anchor[0])
        ep = anchor[1]
    else:
        a = w7.find_activation_anchor(g)
        if a is None:
            return None
        ev, ep = a[0], 0.0
    if ev is None or ev >= last - 2:
        return None
    ref_close = float(g.iloc[ev].close)

    ok, bidx = w7.breakout_retest(g, ev, last, latest, ref_close)
    if not ok or bidx < 0:
        return None

    # ④ 回踩新鲜度下限：突破日距信号日 ≥ RETEST_MIN_AGE 根
    #    （20261028 前瞻回测实测：age<4 时 T+3 −2.09%/T+10 −7.83%，是最大亏损来源）
    age = last - bidx
    if age < RETEST_MIN_AGE:
        return None

    b = g.iloc[bidx]
    # 结构失效位：回踩段最低价（引擎 20260925 口径：与下限 5% 取宽）
    seg = g.iloc[bidx + 1:last + 1]
    if seg.empty:
        return None
    pull_low = float(np.nanmin(seg.low.to_numpy(dtype=float)))
    stop = max(pull_low, close * RETEST_STOP_FLOOR)
    if stop >= close:
        stop = close * RETEST_STOP_FLOOR

    return {
        "code": g.iloc[last].ts_code,
        "close": close,
        "volr": volr,
        "t0_date": str(g.iloc[ev].trade_date),
        "t0_close": ref_close,
        "b_date": str(g.iloc[bidx].trade_date),
        "b_close": float(b.close),
        "pull_low": pull_low,
        "stop": stop,
        "amount": amount,
        "mkt_r20": r20,
        "age": age,
        "event_percentile": ep,
    }


def load_names():
    """股票名称映射。优先用当日 market_<date>.csv 快照，其次回退到跟踪库已有名称。"""
    names = {}
    # 1) 引擎的 market 快照（含 name + industry）
    for snap in sorted(os.listdir(CACHE), reverse=True):
        if snap.startswith("market_") and snap.endswith(".csv"):
            try:
                s = pd.read_csv(os.path.join(CACHE, snap), dtype={"ts_code": str}, usecols=lambda c: c in ("ts_code", "name"))
                if "name" in s.columns:
                    for code, nm in zip(s.ts_code, s.name):
                        if isinstance(nm, str) and nm.strip() and code not in names:
                            names[str(code)] = nm.strip()
                    break
            except Exception:
                pass
    # 2) 跟踪库回填
    try:
        pcon = sqlite3.connect(f"file:{PICK_DB}?mode=ro", uri=True)
        for code, nm in pcon.execute(
                "SELECT ts_code, stock_name FROM stock_pick WHERE stock_name<>''"):
            if code not in names:
                names[code] = nm
        pcon.close()
    except Exception:
        pass
    return names


def scan_dates(conn, dates, verbose=True):
    """扫描多个交易日，共享一次行情加载。

    行情按「每只股票一次读入」的方式加载（各自到最新日的全历史），
    再对每个评估日切出前缀，从而避免每个日期重新读一遍 DB。
    返回 {date: [signal dict, ...]}
    """
    names = load_names()
    # 一次读入所有股票的全历史（到最大日期）
    lo_d, hi_d = min(dates), max(dates)
    # 每只股票需要足够回看，取最大日期前 520 根
    dates_desc = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT 520", (hi_d,)).fetchall()]
    lo = dates_desc[-1] if dates_desc else lo_d
    sql = ("SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pct_chg, "
           "       d.vol, d.amount, a.adj_factor "
           "FROM daily_cache d LEFT JOIN adj_factor_cache a "
           "  ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
           "WHERE d.trade_date>=? AND d.trade_date<=? ORDER BY d.ts_code, d.trade_date")
    t_load = time.time()
    df = pd.read_sql_query(sql, conn, params=[lo, hi_d])
    df = df.dropna(subset=["close"])
    df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
    df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
    df = w7._fill_ma_columns(df)
    if verbose:
        print(f"[w7_retest] 行情加载 {len(df)} 行 / {df.ts_code.nunique()} 只 "
              f"耗时 {time.time()-t_load:.1f}s", flush=True)

    groups = {code: g.reset_index(drop=True)
              for code, g in df.groupby("ts_code", sort=True)}
    out = {}
    for d in dates:
        r20 = market_r20(conn, d)
        if r20 is None:
            out[d] = []
            continue
        if r20 >= RETEST_MKT_R20_MAX:
            if verbose:
                print(f"[w7_retest] {d} 逆势门不满足 (r20 {r20*100:+.2f}%) → 无信号", flush=True)
            out[d] = []
            continue
        hits = []
        for code, g in groups.items():
            if len(g) < RETEST_MIN_BARS:
                continue
            i = int(np.searchsorted(g.trade_date.astype(str).to_numpy(), d))
            if i >= len(g) or str(g.trade_date.iloc[i]) != d:
                continue
            gg = g.iloc[:i + 1].reset_index(drop=True)
            if len(gg) < RETEST_MIN_BARS:
                continue
            if not prescreen(_to_arrays(gg), RETEST_MIN_BARS):
                continue
            r = detect_one(gg, r20)
            if r:
                hits.append(r)
        out[d] = hits
        if verbose:
            print(f"[w7_retest] {d} r20={r20*100:+.2f}% 命中 {len(hits)} 只", flush=True)
    return out, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="交易日 YYYYMMDD，逗号分隔可多日")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strategy-id", default=STRATEGY_ID)
    ap.add_argument("--no-save", action="store_true", help="不落库")
    args = ap.parse_args()
    dates = [x.strip() for x in str(args.date).split(",") if x.strip()]
    d = dates[0]
    multi = len(dates) > 1

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if multi:
        t0 = time.time()
        results, names = scan_dates(conn, dates)
        print(f"[w7_retest] 多日扫描总耗时 {time.time()-t0:.1f}s", flush=True)
    else:
        r20 = market_r20(conn, d)
        print(f"[w7_retest] {d} 上证20日涨跌幅 {r20*100:+.2f}%" if r20 is not None
              else f"[w7_retest] {d} 上证20日不可用")
        if r20 is not None and r20 >= RETEST_MKT_R20_MAX:
            print(f"[w7_retest] 逆势门不满足（r20 {r20*100:+.2f}% ≥ "
                  f"{RETEST_MKT_R20_MAX*100:.0f}%），当日不产生信号")
            conn.close()
            return
        df = load_frames(conn, d)
        conn.close()
        # 股票池口径与引擎一致：只保留沪深主板/创业板/科创板，排除北交所(92/8/43)与新三板
        df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
        df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
        # groupby 一次切分，避免逐股全表布尔过滤（O(N²) → O(N)）
        groups = {code: g.reset_index(drop=True)
                  for code, g in df.groupby("ts_code", sort=True)}
        codes = sorted(groups)
        names = load_names()
        print(f"[w7_retest] 扫描 {len(codes)} 只 (截至 {d})", flush=True)

        # ── 第一轮：向量化预筛 ──
        t0 = time.time()
        cand = []
        for code in codes:
            g = groups[code]
            if len(g) < RETEST_MIN_BARS:
                continue
            if prescreen(_to_arrays(g), RETEST_MIN_BARS):
                cand.append(code)
        print(f"[w7_retest] 预筛后剩 {len(cand)} 只 ({len(cand)/max(len(codes),1)*100:.1f}%) "
              f"耗时 {time.time()-t0:.1f}s", flush=True)

        # ── 第二轮：完整结构判定（昂贵，仅对预筛通过者调用）──
        hits = []
        t1 = time.time()
        for i, code in enumerate(cand):
            g = groups[code]
            r = detect_one(g, r20)
            if r:
                hits.append(r)
            if (i + 1) % 200 == 0:
                print(f"[w7_retest] 结构判定 {i+1}/{len(cand)} 命中 {len(hits)}", flush=True)
        print(f"[w7_retest] 结构判定耗时 {time.time()-t1:.1f}s", flush=True)
        results = {d: hits}

    for dd in dates:
        for h in results.get(dd, []):
            nm = names.get(h["code"], "")
            print(f"    {dd} {h['code']} {nm} 收 {h['close']:.2f} 量比 {h['volr']:.2f} "
                  f"T0={h['t0_date']}({h['t0_close']:.2f}) 突破={h['b_date']}({h['b_close']:.2f}) "
                  f"失效位 {h['stop']:.2f} 额 {h['amount']/10:.0f}万")

    if args.dry_run or args.no_save:
        print("[w7_retest] dry-run，不落库")
        return

    # 落库（与引擎 sync_downstream 同样的 replace 语义）
    try:
        from stock_pick_db import record_picks, upsert_strategy
        sid = args.strategy_id
        upsert_strategy(sid, STRATEGY_NAME)
        for dd in dates:
            hits = results.get(dd, [])
            rows = []
            for h in hits:
                # 扁平键传入 → record_picks 自动打包进 indicators JSON（与新口径引擎同写法）
                rows.append({
                    "ts_code": h["code"], "stock_name": names.get(h["code"], ""), "close": h["close"],
                    "pct_chg": None, "signal": "RETEST_PULLBACK", "action": "BUY",
                    "score": None, "rank_no": None, "industry": "",
                    "reason": (f"逆势缩量回踩低吸：上证20日{h['mkt_r20']*100:+.2f}%，"
                               f"量比{h['volr']:.2f}（<{RETEST_VOLR_MAX}），"
                               f"回踩{h['age']}根（≥{RETEST_MIN_AGE}），"
                               f"T0={h['t0_date']}，突破={h['b_date']}，"
                               f"失效位{h['stop']:.2f}（结构{max(h['pull_low'], h['close']*RETEST_STOP_FLOOR):.2f}与"
                               f"{h['close']*RETEST_STOP_FLOOR:.2f}取宽），"
                               f"额{h['amount']/10:.0f}万"),
                    "stop_price": h["stop"], "target_price": None,
                    "details": None,
                    # 结构化字段（落 indicators）
                    "volr": round(h["volr"], 3),
                    "t0_date": h["t0_date"], "t0_close": h["t0_close"],
                    "b_date": h["b_date"], "b_close": h["b_close"],
                    "pull_low": round(h["pull_low"], 3),
                    "stop_floor": round(h["close"] * RETEST_STOP_FLOOR, 3),
                    "amount": h["amount"], "mkt_r20": round(h["mkt_r20"], 4),
                    "age": h["age"], "event_percentile": h["event_percentile"],
                    "volr_max": RETEST_VOLR_MAX, "mkt_r20_max": RETEST_MKT_R20_MAX,
                })
            n = record_picks(sid, STRATEGY_NAME, rows, pick_date=dd, replace=True)
            print(f"[w7_retest] 已落库 {dd}: {n}/{len(rows)} 条 (strategy={sid})")
    except Exception as e:
        print(f"[w7_retest] 落库失败：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
