"""
B浪策略真正历史回测脚本 v2
======================
模拟每个历史交易日发出信号，跟踪未来N天收益。

用法:
  python bwave_backtest_v2.py --start 2025-01-01 --end 2026-06-30
  python bwave_backtest_v2.py --pool qualified --start 2025-06-01
"""

import os, sys, argparse, sqlite3
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = r'D:\mystock\cache_daily\stock_data.db'
OUTPUT_DIR = r'D:\mystock\solo\trend_feature_output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 导入原策略的检测函数
from bwave_strategy import (
    get_data, detect_awave, detect_bwave, detect_bwave_relaxed,
    check_launch_signal, detect_bwave_divergence,
    calc_bwave_score, calc_divergence_score,
    normalize_ts_code, load_qualified_pool
)


def run_backtest_for_stock(ts_code: str, start_date: str, end_date: str) -> list:
    """对单只股票运行历史回测"""
    df = get_data(ts_code)
    if df is None or len(df) < 250:
        return []
    
    # 找到起始和结束日期的索引
    date_list = df['trade_date'].tolist()
    if start_date not in date_list or end_date not in date_list:
        return []
    
    start_idx = date_list.index(start_date)
    end_idx = date_list.index(end_date)
    
    signals = []
    
    # 遍历每个交易日（从start_idx+250开始，确保有足够历史数据）
    for idx in range(start_idx + 250, end_idx + 1):
        # 限制数据范围（避免使用未来数据）
        df_limited = df.iloc[:idx+1].copy()
        
        # 检测A浪
        awave = detect_awave(df_limited)
        if awave is None:
            continue
        
        # 检测B浪
        bwave = detect_bwave(df_limited, awave)
        if bwave is None:
            bwave = detect_bwave_relaxed(df_limited, awave)
        if bwave is None:
            continue
        
        # 检测启动信号
        launch = check_launch_signal(df_limited, awave, bwave)
        if launch:
            # 计算未来收益
            entry_price = launch['launch_price']
            future_rets = {}
            for w in [1, 5, 10, 20]:
                fi = min(launch['launch_idx'] + w, len(df) - 1)
                future_rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0
            
            signals.append({
                'ts_code': ts_code,
                'signal_date': df.iloc[launch['launch_idx']]['trade_date'],
                'signal_type': 'launch',
                'entry_price': entry_price,
                'a_gain': awave['gain'],
                'b_drop': bwave['drop'],
                'return_1d': future_rets[1],
                'return_5d': future_rets[5],
                'return_10d': future_rets[10],
                'return_20d': future_rets[20],
            })
        
        # 检测底背离信号
        div = detect_bwave_divergence(df_limited, awave, bwave)
        if div:
            # 计算未来收益
            entry_price = div['launch_price']
            future_rets = {}
            for w in [1, 5, 10, 20]:
                fi = min(div['launch_idx'] + w, len(df) - 1)
                future_rets[w] = round((df.iloc[fi]['close'] / entry_price - 1) * 100, 2) if entry_price > 0 else 0
            
            signals.append({
                'ts_code': ts_code,
                'signal_date': df.iloc[div['launch_idx']]['trade_date'],
                'signal_type': 'divergence',
                'entry_price': entry_price,
                'a_gain': awave['gain'],
                'b_drop': bwave['drop'],
                'return_1d': future_rets[1],
                'return_5d': future_rets[5],
                'return_10d': future_rets[10],
                'return_20d': future_rets[20],
            })
    
    return signals


def main():
    parser = argparse.ArgumentParser(description='B浪策略历史回测 v2')
    parser.add_argument('--pool', choices=['qualified', 'all'], default='qualified')
    parser.add_argument('--start', type=str, default='2025-06-01')
    parser.add_argument('--end', type=str, default='2026-06-30')
    args = parser.parse_args()
    
    # 加载股票池
    if args.pool == 'qualified':
        stock_codes = load_qualified_pool('qualified')
    else:
        stock_codes = load_qualified_pool('all')
    
    print(f'历史回测 v2 — {args.start} 至 {args.end}')
    print(f'股票池: {args.pool} ({len(stock_codes)}只)')
    print()
    
    all_signals = []
    total = len(stock_codes)
    
    for i, ts_code in enumerate(stock_codes):
        if (i + 1) % 50 == 0:
            print(f'[{i+1}/{total}] 回测中...已发现{len(all_signals)}个信号')
        
        signals = run_backtest_for_stock(ts_code, args.start, args.end)
        all_signals.extend(signals)
    
    print(f'\n回测完成！共 {len(all_signals)} 个信号')
    
    if not all_signals:
        print('无信号')
        return
    
    # 保存CSV
    df_out = pd.DataFrame(all_signals)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(OUTPUT_DIR, f'bwave_backtest_v2_{timestamp}.csv')
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'CSV: {csv_path}')
    
    # 统计
    print(f'\n统计:')
    for w in [1, 5, 10, 20]:
        r = df_out[f'return_{w}d'].dropna()
        if len(r) == 0:
            continue
        wins = r[r > 0]
        loss = r[r < 0]
        win_rate = len(wins) / len(r) * 100
        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = abs(loss.mean()) if len(loss) > 0 else 0
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        
        print(f'  +{w}d: 均={r.mean():.2f}%  胜率={win_rate:.0f}%  盈亏比={pl_ratio:.2f}')
        print(f'        盈利{len(wins)}个 亏损{len(loss)}个')
    
    # 计算期望收益
    r10 = df_out['return_10d'].dropna()
    if len(r10) > 0:
        win_rate_10 = len(r10[r10 > 0]) / len(r10)
        avg_win_10 = r10[r10 > 0].mean()
        avg_loss_10 = abs(r10[r10 < 0].mean())
        expected_return = win_rate_10 * avg_win_10 - (1 - win_rate_10) * avg_loss_10
        print(f'\n  期望收益(+10d): {expected_return:.2f}%')


if __name__ == '__main__':
    main()
