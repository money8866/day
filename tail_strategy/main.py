#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
尾盘猎手 - 最高胜率尾盘战法系统

用法:
  python -m tail_strategy.main live        # 实时尾盘扫描(14:30-15:00)
  python -m tail_strategy.main backtest    # 历史回测
  python -m tail_strategy.main report      # 胜率报告
  python -m tail_strategy.main backfill    # 回填信号收益
  python -m tail_strategy.main scan        # 立即扫描一次(不限时段)
  python -m tail_strategy.main scan-daily --date 20260731  # 盘后扫描指定日期(daily_cache)

核心策略逻辑:
  尾盘(14:50后)捕捉"放量拉升收在高位"的个股, 利用次日惯性高开获利
  六维评分: 攻击力(35) + 结构(25) + 位置(20) + 技术(20) + 主题(10) + 资金(5)
  诱多识别: 四大红旗扣分(最高-30), 过滤"全天弱势尾盘偷袭"陷阱
  硬过滤: 涨停/跌停/振幅>9%/连板/高位/换手异常/小市值

胜率关键:
  1. 尾盘放量(1.3-2.5倍) + 收在日内最高位 = 主力次日做多概率极高
  2. MACD零上多头 + KDJ金叉 + RSI健康区 = 技术共振叠加
  3. 主题内有涨停配合 + 非龙头补涨 = 溢价空间大
  4. 前几日缩量 + 当日温和放量 = 洗盘结束启动信号
  5. 诱多扣分 = 排除80%的"尾盘拉高次日低开"陷阱
"""
import os
import sys
import argparse
from datetime import datetime

# Windows GBK 修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def cmd_live(args):
    """实时尾盘扫描"""
    from .live_scanner import LiveScanner
    scanner = LiveScanner(min_score=args.min_score, top_n=args.top_n)
    scanner.run()


def cmd_scan(args):
    """立即扫描一次"""
    from .live_scanner import LiveScanner
    scanner = LiveScanner(min_score=args.min_score, top_n=args.top_n)
    if not scanner.init():
        return
    n = scanner.fetch_quotes()
    print(f"获取行情: {n}只")
    if n > 0:
        signals = scanner.scan()
        scanner._print_signals(signals, 1)
        scanner._save_signals(signals)


def cmd_scan_daily(args):
    """盘后扫描指定日期 (daily_cache 统一缓存)"""
    from .daily_scan import scan_date, print_signals
    date = args.date or datetime.now().strftime('%Y%m%d')
    signals = scan_date(date, min_score=args.min_score)
    print(f"\n盘后扫描 {date}: {len(signals)}个信号 (按总分排序)")
    print_signals(signals, args.top_n)


def cmd_backtest(args):
    """历史回测"""
    from .backtest import BacktestEngine
    bt = BacktestEngine(min_score=args.min_score, top_n=args.top_n)
    stats = bt.run(
        start_date=args.start,
        end_date=args.end,
        verbose=True,
    )
    if stats.total_signals > 0:
        bt.save_results()


def cmd_report(args):
    """胜率报告"""
    from .analyzer import WinRateAnalyzer
    analyzer = WinRateAnalyzer()
    analyzer.report()
    analyzer.factor_contribution()


def cmd_backfill(args):
    """回填信号收益"""
    from .analyzer import WinRateAnalyzer
    analyzer = WinRateAnalyzer()
    analyzer.backfill_signals(days_back=args.days)


def main():
    parser = argparse.ArgumentParser(
        description='尾盘猎手 - 最高胜率尾盘战法系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # live
    p_live = subparsers.add_parser('live', help='实时尾盘扫描(14:30-15:00)')
    p_live.add_argument('--min-score', type=float, default=65, help='最低信号分数')
    p_live.add_argument('--top-n', type=int, default=15, help='每轮最多信号数')

    # scan
    p_scan = subparsers.add_parser('scan', help='立即扫描一次')
    p_scan.add_argument('--min-score', type=float, default=65, help='最低信号分数')
    p_scan.add_argument('--top-n', type=int, default=15, help='最多信号数')

    # scan-daily (盘后, daily_cache数据源)
    p_sd = subparsers.add_parser('scan-daily', help='盘后扫描指定日期(daily_cache)')
    p_sd.add_argument('--date', type=str, default=None, help='扫描日期YYYYMMDD(默认今天)')
    p_sd.add_argument('--min-score', type=float, default=50, help='最低信号分数')
    p_sd.add_argument('--top-n', type=int, default=40, help='最多信号数')

    # backtest
    p_bt = subparsers.add_parser('backtest', help='历史回测')
    p_bt.add_argument('--start', type=str, default='20250101', help='起始日期')
    p_bt.add_argument('--end', type=str, default=None, help='结束日期')
    p_bt.add_argument('--min-score', type=float, default=65, help='最低信号分数')
    p_bt.add_argument('--top-n', type=int, default=10, help='每日最多信号数')

    # report
    p_report = subparsers.add_parser('report', help='胜率报告')

    # backfill
    p_bf = subparsers.add_parser('backfill', help='回填信号收益')
    p_bf.add_argument('--days', type=int, default=30, help='回填天数')

    args = parser.parse_args()

    if args.command == 'live':
        cmd_live(args)
    elif args.command == 'scan':
        cmd_scan(args)
    elif args.command == 'scan-daily':
        cmd_scan_daily(args)
    elif args.command == 'backtest':
        cmd_backtest(args)
    elif args.command == 'report':
        cmd_report(args)
    elif args.command == 'backfill':
        cmd_backfill(args)
    else:
        parser.print_help()
        print("\n💡 快速开始:")
        print("  python -m tail_strategy.main scan       # 立即扫描")
        print("  python -m tail_strategy.main live       # 尾盘实时")
        print("  python -m tail_strategy.main backtest   # 历史回测")
        print("  python -m tail_strategy.main report     # 胜率报告")


if __name__ == '__main__':
    main()
