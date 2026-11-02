# -*- coding: utf-8 -*-
"""仓位/状态机年度诊断（V10.2 议题支撑）

目的：用 2026 全年真实数据回答三个问题
  Q1  建议仓位为什么全年上不到 60% 以上？
  Q2  S4/S5 为什么全年 0 天？是阈值太严还是代码链路断了？
  Q3  S6 强势主升的四个模块分别卡在哪？

输出：_bt_out/diag_*.csv + 控制台摘要
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

import market_regime_v10 as v10
import market_regime_v101 as v101

BASE = os.path.dirname(os.path.abspath(__file__))
DB = r"D:\mystock\cache_daily\stock_data.db"
OUT = os.path.join(BASE, "_bt_out")
os.makedirs(OUT, exist_ok=True)


def load_dates():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache "
        "WHERE trade_date>='20260101' ORDER BY trade_date")]
    conn.close()
    return dates


def precompute(dates):
    pack = {"breadth": {}, "amount": {}, "dt": {}}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for d in dates:
        df = pd.read_sql_query(
            "SELECT ts_code,pct_chg,amount FROM daily_cache WHERE trade_date=?",
            conn, params=(d,))
        if df.empty:
            continue
        up = int((df["pct_chg"] > 0).sum())
        dn = int((df["pct_chg"] < 0).sum())
        pack["breadth"][d] = up / (up + dn) * 100.0 if (up + dn) else 0.0
        pack["amount"][d] = float(df["amount"].sum()) / 100000.0
        thr = df["ts_code"].map(
            lambda c: 20.0 if str(c)[:3] in ("300", "301", "688") else 10.0)
        pack["dt"][d] = int((df["pct_chg"] <= -thr * 0.99).sum())
    conn.close()
    return pack


def index_trend_table(dates):
    """五大指数全年真实走势。"""
    print("=" * 78)
    print("一、2026 主要指数真实走势")
    print("=" * 78)
    rows = []
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for nm, code in v10.INDEX_LIST:
        h = v10._query_index_hist(conn, code, dates[-1], 420)
        if h is None or len(h) == 0:
            print(f"  {nm}: 无数据")
            continue
        h = h.sort_index()
        s0 = float(h["close"].iloc[0])
        s1 = float(h["close"].iloc[-1])
        ma20 = float(h["close"].tail(20).mean())
        ma60 = float(h["close"].tail(60).mean())
        r5 = (s1 / float(h["close"].iloc[-6]) - 1) * 100 if len(h) > 6 else np.nan
        r20 = (s1 / float(h["close"].iloc[-21]) - 1) * 100 if len(h) > 21 else np.nan
        hi = float(h["close"].max())
        hid = str(h["close"].idxmax())
        rows.append(dict(name=nm, code=code, start=s0, end=s1,
                         ytd=(s1 / s0 - 1) * 100,
                         vs_ma20=(s1 / ma20 - 1) * 100,
                         vs_ma60=(s1 / ma60 - 1) * 100,
                         ret5=r5, ret20=r20, high=hi, high_date=hid))
        print(f"  {nm:8s} 年初{s0:9.2f} → 年末{s1:9.2f}  年内{(s1/s0-1)*100:+7.2f}%  "
              f"vsMA20 {(s1/ma20-1)*100:+6.2f}%  vsMA60 {(s1/ma60-1)*100:+6.2f}%  "
              f"ret5 {r5:+6.2f}%  ret20 {r20:+7.2f}%  年内高 {hi:.2f}@{hid}")
    conn.close()
    return pd.DataFrame(rows)


def node_diag(dates, pack, cfg):
    """逐日节点诊断：显式串行传入 prev_snapshot，检验 delta_pp 是否恢复。"""
    print()
    print("=" * 78)
    print("二、节点命中诊断（显式串行 prev_snapshot）")
    print("=" * 78)
    prev_snap = None
    rows = []
    for d in dates:
        snap = v10.collect_market_snapshot(d, cfg, prev_snap, pack)
        node = v10.detect_market_nodes(snap, cfg)
        hd = node.hit_detail
        rows.append({
            "d": d,
            "node": node.node_type,
            "panic": len(hd[v10.NODE_PANIC]),
            "extreme": len(hd[v10.NODE_EXTREME]),
            "easing": len(hd[v10.NODE_EASING]),
            "repair": len(hd[v10.NODE_REPAIR]),
            "br": round((snap.breadth.breadth_ratio or 0) * 100, 1),
            "dpp": (round(snap.breadth.delta_pp, 2)
                    if snap.breadth.delta_pp is not None else None),
            "dt": snap.limit.dt_count,
            "pdt": snap.limit.prev_dt_count,
            "brk": snap.limit.broken_rate,
            "ssm": snap.limit.strong_stock_mean_pct,
            "promo": snap.limit.promotion_rate,
        })
        prev_snap = snap
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "diag_nodes_fixed.csv"), index=False)

    print(f"  delta_pp 非空天数: {df['dpp'].notna().sum()} / {len(df)}")
    if df["dpp"].notna().any():
        print(f"  delta_pp 统计: 均值 {df['dpp'].mean():.2f}  "
              f"max {df['dpp'].max():.2f}  "
              f"p90 {df['dpp'].quantile(0.9):.2f}  "
              f"p75 {df['dpp'].quantile(0.75):.2f}")
    print()
    for k in ["panic", "extreme", "easing", "repair"]:
        print(f"  {k:8s} 命中数分布: {df[k].value_counts().sort_index().to_dict()}")
    print()
    print(f"  节点裁决结果: {df['node'].value_counts().to_dict()}")
    print()
    print("  ── 广度回升条件敏感度 ──")
    for thr in (10, 8, 6, 5, 4, 3):
        m = ((df["br"] >= 55) & (df["dpp"] >= thr)).sum()
        print(f"    br>=55 且 dpp>={thr:>2}: {m:>3} 天")
    print()
    print("  ── 单条件满足天数 ──")
    print(f"    br>=55          : {(df['br'] >= 55).sum():>3}")
    print(f"    dpp>=10         : {(df['dpp'] >= 10).sum():>3}")
    print(f"    dt<=pdt*0.8     : {((df['dt'] <= df['pdt']*0.8) & (df['pdt'] > 0)).sum():>3}")
    print(f"    炸板率<=30       : {(df['brk'] <= 30).sum():>3}")
    print(f"    强股均值>=0      : {(df['ssm'] >= 0).sum():>3}")
    print(f"    晋级率>=25       : {(df['promo'] >= 25).sum():>3}")
    print()
    print("  ── 修复确认三条件联合（trend+breadth+sentiment）──")
    m = (df["repair"] >= 3) & ((df["br"] >= 55) & (df["dpp"] >= 10)) \
        & (df["dt"] <= df["pdt"] * 0.8) & (df["pdt"] > 0)
    print(f"    全满足天数: {m.sum()}")
    return df


def confirm_days_test(mask, need=2):
    """连续 N 日成立的第 2..N 日计数。"""
    run = 0
    days = 0
    for v in mask:
        run = run + 1 if v else 0
        if run >= need:
            days += 1
    return days


def strong_module_diag(dates, cfg, v10cfg):
    """S6 四模块逐日细节。"""
    print()
    print("=" * 78)
    print("三、S6 强势主升四模块诊断")
    print("=" * 78)
    rows = []
    for d in dates:
        try:
            r = v101.detect_strong_uptrend(d, cfg)
        except Exception as e:
            print(f"  {d} 异常: {e}")
            continue
        m = r.modules
        rows.append({
            "d": d,
            "passed": r.passed_count,
            "m_index": int(m["指数趋势"].passed),
            "m_breadth": int(m["市场广度"].passed),
            "m_money": int(m["短线赚钱效应"].passed),
            "m_mainline": int(m["主线持续性"].passed),
            "mainline_missing": int(bool(m["主线持续性"].missing)),
        })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "diag_strong_modules.csv"), index=False)
    print(f"  四模块全部通过（strong_passed=4）: {(df['passed'] == 4).sum()} 天")
    print(f"  passed 分布: {df['passed'].value_counts().sort_index().to_dict()}")
    print()
    for k in ["m_index", "m_breadth", "m_money", "m_mainline"]:
        print(f"  {k:12s} 通过: {df[k].value_counts().to_dict()}")
    print(f"  主线数据缺失(UNKNOWN): {df['mainline_missing'].sum()} / {len(df)} 天")
    return df


def main():
    cfg = v10.load_config()
    c101 = v101.load_config()
    dates = load_dates()
    print(f"回放区间 {dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日\n")
    pack = precompute(dates)

    index_trend_table(dates)
    ndf = node_diag(dates, pack, cfg)

    # S4/S5 触发可行性
    print()
    print("  ── S4 / S5 可触发性（不同阈值组合）──")
    print("    S4 = easing>=2，连续2日确认")
    for thr in (2, 3):
        print(f"      easing>={thr}: 命中 {(ndf['easing'] >= thr).sum():>3} 天，"
              f"连续2日 {confirm_days_test(ndf['easing'] >= thr)} 天")
    print("    S5 = repair>=3 且 trend+breadth+sentiment 三条件，连续2日确认")
    for bthr in (55, 50, 45):
        for dthr in (10, 8, 6, 5):
            m = ((ndf["repair"] >= 3) & (ndf["br"] >= bthr) & (ndf["dpp"] >= dthr)
                 & (ndf["dt"] <= ndf["pdt"] * 0.8) & (ndf["pdt"] > 0))
            print(f"      br>={bthr} dpp>={dthr}: 单日 {m.sum():>3} 天，"
                  f"连续2日 {confirm_days_test(m):>3} 天")

    sdf = strong_module_diag(dates, c101, cfg)
    print()
    print("  ── S6 组合可行（若改为 min_modules_passed=3）──")
    for need in (4, 3, 2):
        m = sdf["passed"] >= need
        print(f"    需通过>={need}: 单日 {m.sum():>3} 天，连续2日 {confirm_days_test(m)} 天")

    print()
    print(f"✓ 诊断结果已写入 {OUT}")


if __name__ == "__main__":
    main()
