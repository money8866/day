# -*- coding: utf-8 -*-
"""HVT-BULL 每日盘后定时任务入口（每工作日 17:00）

数据补全 → 全市场扫描 → AI 自然语言复盘 → 微信推送。
由 Windows 任务计划程序调用，也可手动运行：
    python hvt_bull/run_daily_push.py [--date 20260830]
"""
import os
import sys
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.push import _load_env, push_daily_report


def _ensure_data(trade_date: str):
    """三张普通日线缓存表（daily / daily_basic / adj_factor）当日数据不足时自动补全

    与 data_loader 一致，改用窄表口径；各自缺失时调 stock_cache 的
    daily_market / daily_basic_market / adj_factor_market（缓存优先+API 兜底回写）。
    """
    import stock_cache as sc
    min_cnt = sc.UDC_MARKET_MIN_COUNT
    checks = {
        'daily_cache':       (sc.get_daily_by_date_count,       sc.daily_market),
        'daily_basic_cache': (sc.get_daily_basic_by_date_count, sc.daily_basic_market),
        'adj_factor_cache':  (sc.get_adj_factor_by_date_count,  sc.adj_factor_market),
    }
    for name, (count_fn, fill_fn) in checks.items():
        try:
            cnt = count_fn(trade_date)
        except Exception:
            cnt = 0
        if cnt >= min_cnt:
            print(f'[RUN-PUSH] {name} {trade_date} 数据已就绪（{cnt}条），无需补全')
            continue
        print(f'[RUN-PUSH] {name} {trade_date} 数据不足（{cnt}条），开始补全...')
        try:
            fill_fn(trade_date)
        except Exception as e:
            print(f'[RUN-PUSH] {name} 数据补全异常: {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    _load_env()
    import stock_cache as sc
    trade_date = args.date or sc.get_effective_date()
    print(f'[RUN-PUSH] 目标交易日: {trade_date}')
    _ensure_data(trade_date)
    from hvt_bull.daily import run_daily
    run_daily(trade_date=trade_date)
    push_daily_report(trade_date=trade_date)


if __name__ == '__main__':
    main()
