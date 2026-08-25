# -*- coding: utf-8 -*-
"""Bull中报股池 全量103只：RIB V2.1 报告（NOW/NEXT/WATCH/FUNNEL/FAILED/结论+Forward Test）。
数据源: 项目本地日K线缓存 stock_data.db (daily_cache 表, Tushare, 不复权)，不联网。

用法:
  python scan_bull103.py [END_DATE] [--regime REGIME] [--aug] [--ft START END]
    END_DATE  默认 20260824
    --regime  市场环境 bull/normal/recovery/weak/bear，默认 normal
    --aug     追加 8 月逐日 PRIMARY_BUY 扫描
    --ft      NEXT Forward Test 回测区间（如 --ft 20260801 20260824）
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rib.engine import RIBEngine
from rib.filters import MarketSnapshot
from rib.v2_report import generate_v2_report, v2_report_to_file
from rib.v2_forward_test import run_forward_test, format_forward_report

DB_PATH = r"D:\mystock\cache_daily\stock_data.db"


def get_df(code6: str, end_date: str = "") -> pd.DataFrame:
    """从本地 daily_cache 读取单股日线（ts_code, 升序）"""
    ts = code6 + (".SH" if code6.startswith("6") else ".SZ")
    conn = sqlite3.connect(DB_PATH)
    sql = "SELECT trade_date, open, high, low, close, vol, amount FROM daily_cache WHERE ts_code=? "
    params: list = [ts]
    if end_date:
        sql += "AND trade_date <= ? "
        params.append(str(end_date))
    sql += "ORDER BY trade_date"
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df.reset_index(drop=True)


def to_tscode(code6: str) -> str:
    return code6 + (".SH" if code6.startswith("6") else ".SZ")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("end_date", nargs="?", default="20260824")
    parser.add_argument("--regime", default="normal",
                        choices=["bull", "normal", "recovery", "weak", "bear"])
    parser.add_argument("--aug", action="store_true", help="追加8月逐日PRIMARY_BUY扫描")
    parser.add_argument("--ft", nargs=2, metavar=("START", "END"), default=None,
                        help="NEXT Forward Test 回测区间")
    args = parser.parse_args()

    pool = pd.read_csv(r"d:\mystock\solo\bull103.csv", dtype={"代码6": str})
    engine = RIBEngine()
    snap = MarketSnapshot(regime=args.regime)

    print("═" * 76)
    print(f"RIB V2.1 扫描 - Bull中报股池 {len(pool)}只 - 本地缓存 stock_data.db 截止 {args.end_date}")
    print(f"市场环境: {args.regime}")
    print("═" * 76)

    results, fail = [], []
    for i, (_, s) in enumerate(pool.iterrows(), 1):
        code6, name = s["代码6"], s["名称"]
        df = get_df(code6, args.end_date)
        if len(df) < 130:
            fail.append(name)
            print(f"[{i:>3}] {name:<6} 数据不足({len(df)}根) 跳过")
            continue
        try:
            r = engine.analyze(df, ts_code=to_tscode(code6), name=name,
                               market_snapshot=snap)
        except Exception as e:
            fail.append(name)
            print(f"[{i:>3}] {name:<6} 异常: {e}")
            continue
        results.append(r)
        tag = f"  R={r.buy_readiness:.0f}  NEXT={r.next_state}({r.next_state_score:.0f})"
        tier = r.pool_tier if r.pool_tier != "IGNORE" else ""
        print(f"[{i:>3}] {name:<6} {r.close:>8.2f}  {r.state:<24} {tag}  {tier}")

    # ── NEXT Forward Test（可选，V2.1 §31/§32）──
    forward_text = ""
    if args.ft:
        ft_start, ft_end = args.ft
        print("\n" + "═" * 76)
        print(f"NEXT Forward Test 回测: {ft_start} ~ {ft_end}")
        print("═" * 76)

        def _loader(code6: str, end_date: str = "") -> pd.DataFrame:
            return get_df(code6, "")

        def _analyze(sub: pd.DataFrame):
            return engine.analyze(sub, market_snapshot=snap)

        signals, stats, chain = run_forward_test(
            _loader, pool, _analyze, ft_start, ft_end)
        forward_text = format_forward_report(signals, stats, chain)
        print(forward_text)

    # ── V2.1 报告 ──
    report = generate_v2_report(results, args.end_date, args.regime,
                                forward_text=forward_text)
    print("\n" + report)
    path = v2_report_to_file(report, args.end_date)
    print(f"\n报告已保存: {path}")

    if fail:
        print(f"数据缺失 {len(fail)} 只: {', '.join(fail)}")

    # ── 8月逐日 PRIMARY_BUY 扫描（可选）──
    if args.aug:
        print("\n" + "═" * 76)
        print("8月逐日 PRIMARY_BUY 扫描")
        print("═" * 76)
        buys_all = []
        for i, (_, s) in enumerate(pool.iterrows(), 1):
            code6, name = s["代码6"], s["名称"]
            df = get_df(code6, args.end_date)
            if len(df) < 130:
                continue
            aug = df[df["trade_date"].str.startswith("202608")]["trade_date"].tolist()
            buys = []
            for d in aug:
                sub = df[df["trade_date"] <= d].reset_index(drop=True)
                if len(sub) < 130:
                    continue
                try:
                    r2 = engine.analyze(sub, ts_code=to_tscode(code6), name=name,
                                        market_snapshot=snap)
                except Exception:
                    continue
                if r2.state == "PRIMARY_BUY":
                    buys.append(d)
            if buys:
                buys_all.append((name, buys))
                print(f"  {name:<6} 买入信号 {buys}")
        if buys_all:
            print(f"\n8月 PRIMARY_BUY 信号: {len(buys_all)} 只")
        else:
            print("\n8月全池无 PRIMARY_BUY 买入信号")

    print(f"\n结论: Bull103 全池 V2.1 扫描完成（数据源: 本地 stock_data.db，截止 {args.end_date}）")


if __name__ == "__main__":
    main()
