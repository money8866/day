# -*- coding: utf-8 -*-
"""盘后预计算技术因子快照(供次日 monitor 启动零预热直读)

背景: monitor/回测读取 MACD/KDJ/RSI/BOLL 等因子时,旧逻辑对全市场每天
做约 250 交易日预热现算(分钟级)。技术指标是前一交易日收盘定稿日线上的
确定值,因此每天盘后(三窄表已定稿)把最新交易日的全市场因子算好并落库到
factor_snapshot_cache,次日 monitor 启动直接 SELECT 当日行即可(秒级)。

用法:
  python _build_factor_snapshot.py                # 补齐最近3个交易日缺失的快照
  python _build_factor_snapshot.py --days 1       # 只补最近1日
  python _build_factor_snapshot.py --force        # 强制重建最近3日(口径变化/数据修正时)
"""
import argparse
import sys
import time
from datetime import datetime

sys.path.insert(0, r'd:\mystock\solo')
import stock_cache as sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=3, help='检查最近 N 个交易日(默认3)')
    ap.add_argument('--force', action='store_true', help='强制重建(默认只补缺失)')
    args = ap.parse_args()

    dates = sc.get_recent_trade_dates(n=args.days + 1)
    if not dates:
        print('⚠ SQLite 三窄表缓存为空,无交易日可构建')
        return 1
    dates = dates[-args.days:]  # 取最近 N 个
    have = sc.factor_snapshot_counts(start_date=dates[0], end_date=dates[-1])

    t0 = time.time()
    done, skipped = 0, 0
    for d in dates:
        if not args.force and have.get(d, 0) >= sc.FACTOR_SNAPSHOT_MIN_ROWS:
            print(f'[factor_snapshot] 已有 {d} ({have[d]} 只),跳过')
            skipped += 1
            continue
        n = sc.build_factor_snapshot(d, silent=False)
        done += 1 if n > 0 else 0
        if n == 0:
            skipped += 1
    print(f"[factor_snapshot] 完成: 新构建 {done} 日 / 跳过 {skipped} 日 | "
          f"总耗时 {time.time()-t0:.0f}s | {datetime.now():%H:%M:%S}")
    print('  建议加入每日盘后任务链(收盘后三窄表定稿后执行,如 19:45 调度)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
