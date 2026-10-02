# -*- coding: utf-8 -*-
"""H-AOS-01 数据可用性探针（只读）
目的：核实 ETF 动量 与 Theme Alpha 能否在长区间重建历史信号。
"""
import os
import sqlite3
import glob
import pandas as pd

CACHE = r"D:\mystock\cache_daily"
REPORT = r"d:\mystock\solo\report_daily"


def sec(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def sqlite_probe(path):
    print(f"--- {path}  exists={os.path.exists(path)}")
    if not os.path.exists(path):
        return
    c = sqlite3.connect(path)
    cur = c.cursor()
    tabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
    print(f"    tables={tabs}")
    for t in tabs:
        try:
            n = cur.execute(f'select count(*) from "{t}"').fetchone()[0]
            cols = [d[1] for d in cur.execute(f'pragma table_info("{t}")')]
            print(f"    [{t}] rows={n} cols={cols[:14]}")
        except Exception as e:
            print(f"    [{t}] err={e}")
    c.close()


def main():
    sec("1. Theme Alpha 数据链")
    p = os.path.join(REPORT, "theme_heat_series.csv")
    if os.path.exists(p):
        d = pd.read_csv(p, dtype={"date": str})
        print(f"theme_heat_series.csv rows={len(d)} cols={list(d.columns)}")
        print(f"  date: {d['date'].min()} ~ {d['date'].max()}  n_dates={d['date'].nunique()}  n_themes={d['theme'].nunique()}")
    print(f"theme_heat_v24_*.json 数量 = {len(glob.glob(os.path.join(REPORT, 'theme_heat_v2*_*.json')))}")
    print(f"theme_stock_map_v2_*.json 数量 = {len(glob.glob(os.path.join(CACHE, 'theme_stock_map_v2_*.json')))}")
    print(f"theme_stock_map_2*.json 数量 = {len(glob.glob(os.path.join(CACHE, 'theme_stock_map_2*.json')))}")
    print(f"theme_stock_map_latest.json exists={os.path.exists(os.path.join(CACHE, 'theme_stock_map_latest.json'))}")
    print(f"theme_map/ dir exists={os.path.isdir(os.path.join(CACHE, 'theme_map'))}")
    sqlite_probe(os.path.join(CACHE, "theme_scores_v2.db"))

    sec("2. ETF 动量数据链")
    hist = sorted(glob.glob(os.path.join(CACHE, "etf_backtest_hist", "*.csv")))
    print(f"etf_backtest_hist/*.csv 数量 = {len(hist)}")
    if hist:
        f = hist[0]
        d = pd.read_csv(f, dtype={"trade_date": str})
        print(f"  样例 {os.path.basename(f)} rows={len(d)} cols={list(d.columns)}")
        print(f"  trade_date: {d['trade_date'].min()} ~ {d['trade_date'].max()}")
    for sub in ("etf_fund", "etf_cons", "etf_share", "etf_moneyflow", "etf_alpha_ranking"):
        dd = os.path.join(CACHE, sub)
        fs = glob.glob(os.path.join(dd, "*")) if os.path.isdir(dd) else []
        dates = sorted({os.path.basename(x).split("_")[-1].replace(".csv", "").replace(".json", "") for x in fs if len(os.path.basename(x).split("_")) > 1})
        print(f"  {sub}: files={len(fs)} date_range={dates[0] if dates else '-'} ~ {dates[-1] if dates else '-'} n_dates={len(dates)}")
    print(f"etf_constituents_all.json exists={os.path.exists(os.path.join(CACHE, 'etf_constituents_all.json'))}")

    sec("3. 共享数据层 hve_common.build_grid 依赖")
    sqlite_probe(os.path.join(CACHE, "stock_data.db"))

    sec("4. 已有回测结果台账（可复用 OOS）")
    for p in [
        os.path.join(REPORT, "te_backtest_20250101_20260828_summary.json"),
        os.path.join(REPORT, "hvt_bull_backtest_20250101_20260828.json"),
        os.path.join(REPORT, "hve_backtest_20260930.json"),
        os.path.join(R"d:\mystock\solo\research\h_rbp", "H_RBP_01_SUMMARY.json"),
    ]:
        print(f"  exists={os.path.exists(p)}  {p}")


if __name__ == "__main__":
    main()
