# -*- coding: utf-8 -*-
"""PEA-Absorption 20260827 盘后扫描 runner
数据补齐三步: ① force 重拉 tushare 增量(旧缓存止于 0826) ② 防未来截断 <= 20260827
③ monkeypatch 日历(_bts_data/_pea_mod 两处) + 引擎 bench(TDX-only → TDX+ts增量)
然后调用引擎 scan_pea('2026H1', '20260827', save=True) 落盘出报告
"""
import os
import sys

import pea_absorption_backtest_tdx as bt
import pea_absorption as pea

SCAN_DATE = '20260827'


def main():
    # 1) 重拉增量(旧 delta 缓存为 0827 凌晨拉取, 止于 0826, 必须 force)
    ok = bt.ensure_ts_delta(force=True)
    if not ok:
        print('[runner] tushare 增量拉取失败, 退出')
        return 1

    # 2) 防未来截断: 今天 20260828, 增量可能含 0828, 只保留 <= 20260827
    for attr in ('_ts_delta', '_ts_idx_delta'):
        df = getattr(bt, attr)
        if df is not None and len(df):
            keep = df[df['trade_date'].astype(str) <= SCAN_DATE].reset_index(drop=True)
            setattr(bt, attr, keep)
            days = keep['trade_date'].astype(str).unique().tolist()
            print(f'[runner] {attr} 截断至 {SCAN_DATE}: {len(keep)} 行, 日期 {days}')
    if bt._ts_delta is None or bt._ts_delta.empty:
        print('[runner] ⚠ 0827 个股增量缺失')
        return 1
    d27 = bt._ts_delta[bt._ts_delta['trade_date'].astype(str) == SCAN_DATE]
    print(f'[runner] 0827 个股增量: {len(d27)} 只')

    # 3) patch 日历 + 引擎 bench(全量, 尾端=0827)
    bt.install_ts_patches()
    bench_full = bt.load_bench_full()
    pea.load_bench = lambda *a, **k: bench_full.copy()
    tail = str(bench_full['trade_date'].iloc[-1])
    print(f'[runner] bench patched: {len(bench_full)} 根, 尾端={tail}')
    if tail != SCAN_DATE:
        print(f'[runner] ⚠ bench 尾端 {tail} != {SCAN_DATE}, 指数增量可能缺失')

    # 4) 盘后扫描(个股日线走 daily_cache, 已有 0827 共 5547 只)
    cands, full, report = pea.scan_pea('2026H1', SCAN_DATE, top_n=30, save=True)
    print(report)
    print(f'[runner] 候选 {len(cands)} / 全池 {len(full)} → {pea.REPORT_DIR}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
