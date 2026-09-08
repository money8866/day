# -*- coding: utf-8 -*-
"""HVT-BULL 每日盘后定时任务入口（每工作日 17:00）

数据补全 → 全市场扫描 → 选股落库(stock_pick_db) → AI 自然语言复盘 → 微信推送。
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

# 选股跟踪库（幂等落库；导入失败不影响主流程）
try:
    from stock_pick_db import DB_PATH as PICK_DB_PATH, record_picks
except Exception:
    PICK_DB_PATH = None
    record_picks = None


def _ensure_data(trade_date: str):
    """三张普通日线缓存表（daily / daily_basic / adj_factor）当日数据不足时自动补全

    与 data_loader 一致，改用窄表口径；各自缺失时调 stock_cache 的
    daily_market / daily_basic_market / adj_factor_market（缓存优先+API 兜底回写）。
    """
    import stock_cache as sc
    min_cnt = sc.UDC_MARKET_MIN_COUNT
    checks = {
        'daily_cache':       (sc.get_daily_by_date_count,       sc.daily_market),
        'daily_basic_cache': (sc.get_daily_basic_valuation_count, sc.daily_basic_market),
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


def _record_picks_to_db(trade_date: str, report: dict):
    """te_buy_pool 接入 stock_pick_db 选股跟踪（幂等，失败不阻塞推送）

    按记录表标准列映射: close=current_close(跟踪收益基准), stop_price/target1
    供 STOP_HIT/TARGET_HIT 命中判定; 其余策略私有字段（reentry_streak/
    entry_trigger/buy_zone/invalidation…）自动进 indicators JSON 列。
    """
    if record_picks is None or not report:
        return
    try:
        pool = report.get('te_buy_pool') or []
        if not pool:
            print('[RUN-PUSH] te_buy_pool 为空，跳过选股落库')
            return
        if PICK_DB_PATH:
            os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
        rows = [dict(it, rank_no=i + 1) for i, it in enumerate(pool)]
        n = record_picks('hvt_bull_te', 'HVT-BULL TE 执行池', rows, pick_date=trade_date,
                         field_map={'next_day_action': 'action',
                                    'te_decision_point': 'signal',
                                    'execution_score': 'score',
                                    'current_close': 'close',
                                    'stop_loss': 'stop_price',
                                    'target1': 'target_price',
                                    'sector_name': 'industry',
                                    'execution_reason': 'reason'})
        print(f'[RUN-PUSH] stock_pick_db 写入 {n}/{len(rows)} 条 '
              f'(strategy=hvt_bull_te pick_date={trade_date})')
    except Exception as e:
        print(f'[RUN-PUSH] stock_pick_db 写入失败(不影响推送): {e}')


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
    report = run_daily(trade_date=trade_date)
    _record_picks_to_db(trade_date, report)
    push_daily_report(trade_date=trade_date)


if __name__ == '__main__':
    main()
