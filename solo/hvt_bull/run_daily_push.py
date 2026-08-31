# -*- coding: utf-8 -*-
"""HVT-BULL 每日盘后定时任务入口（每工作日 17:00）

数据补全 → 全市场扫描 → AI 自然语言复盘 → 微信推送。
由 Windows 任务计划程序调用，也可手动运行：
    python hvt_bull/run_daily_push.py [--date 20260830]
"""
import os
import sys
import sqlite3
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.push import _load_env, push_daily_report
from hvt_bull.data_loader import DB_PATH


def _ensure_data(trade_date: str):
    """stk_factor_pro 当日数据不足时自动补全（Tushare 批量+个股并发）"""
    import stock_cache as sc
    try:
        with sqlite3.connect(DB_PATH, timeout=60.0) as conn:
            cnt = conn.execute(
                'SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date=?',
                (trade_date,)).fetchone()[0]
    except Exception:
        cnt = 0
    if cnt >= 4000:
        print(f'[RUN-PUSH] {trade_date} 数据已就绪（{cnt}条），无需补全')
        return
    print(f'[RUN-PUSH] {trade_date} 数据不足（{cnt}条），开始补全...')
    try:
        sc.supplement_missing_stocks(trade_date)
    except Exception as e:
        print(f'[RUN-PUSH] 数据补全异常: {e}')


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
