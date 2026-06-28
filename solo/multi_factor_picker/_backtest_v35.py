# -*- coding: utf-8 -*-
"""
v3.5回测：5月~6月每天强势横盘信号数量
修复：一波取最高点，同一波不拆分
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
import wave2_pattern_scanner as scanner
from datetime import datetime, timedelta

detector = scanner.WavePatternDetector()

pool_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
all_codes = pool_df['code'].tolist()

# 生成5月1日到6月26日的交易日
start_date = '20260501'
end_date = '20260626'

# 预加载所有股票数据
stock_data = {}
for i, c in enumerate(all_codes):
    c = str(c).zfill(6)
    if c.startswith(('60', '688')):
        code = c + '.SH'
    else:
        code = c + '.SZ'
    
    if code.startswith(('8', '4')) or (code.startswith('9') and code.endswith('.SZ')):
        continue
    
    try:
        df = detector.load_data(code, lookback=500)
        if df is not None and len(df) >= 60:
            stock_data[code] = df
    except:
        pass
    
    if i % 200 == 0:
        print(f"  加载进度: {i}/{len(all_codes)}, 成功 {len(stock_data)} 只")

print(f"\n共加载 {len(stock_data)} 只股票数据")

# 从第一只股票拿交易日历
first_code = list(stock_data.keys())[0]
all_trade_dates = stock_data[first_code]['trade_date'].astype(str).tolist()

target_dates = [d for d in all_trade_dates if start_date <= d <= end_date]
print(f"交易日范围: {start_date} ~ {end_date}, 共 {len(target_dates)} 个交易日")
print()

# 逐日扫描
daily_counts = []
total_signals = 0

for target_date in target_dates:
    count = 0
    for code, df in stock_data.items():
        try:
            closes = df['close'].values
            volumes = df['vol'].values
            n = len(df)
            
            # 找target_date对应的索引
            date_list = df['trade_date'].astype(str).tolist()
            if target_date not in date_list:
                continue
            entry_idx = date_list.index(target_date)
            
            # 到entry_idx为止的数据
            closes_to_date = closes[:entry_idx+1]
            n_to_date = entry_idx + 1
            
            wave1_candidates = detector._find_recent_wave1(closes_to_date, n_to_date)
            
            for wave1_high_idx, _, surge_gain in wave1_candidates[:3]:
                wave1_high_price = closes[wave1_high_idx]
                post_high = closes[wave1_high_idx:entry_idx+1]
                if len(post_high) < 5:
                    continue
                
                low_after_high = post_high.min()
                pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price
                low_pos = int(np.argmin(post_high))
                adjust_days = low_pos
                
                if not (pullback_pct < 0.10 and adjust_days <= 15):
                    continue
                
                vol_base_start = max(0, wave1_high_idx - 60)
                base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
                vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
                
                if vol_ratio >= 0.80:
                    continue
                
                surge_pct = round(surge_gain * 100, 1)
                if not (0.02 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
                    continue
                
                # 更高低点检测
                wave1_start_idx = max(0, wave1_high_idx - 20)
                pre_low_start = max(0, wave1_start_idx - 20)
                if wave1_high_idx >= 40:
                    pre_low = closes[pre_low_start:wave1_start_idx+1].min()
                else:
                    pre_low = closes[0:wave1_high_idx+1].min()
                adj_low = closes[wave1_high_idx:entry_idx+1].min()
                is_higher_low = adj_low > pre_low
                if not is_higher_low:
                    continue
                
                # 评分简化判断：只要基础条件满足就算一个信号（粗略估计）
                count += 1
                break  # 同一只股票同一天只算一次
        except:
            continue
    
    daily_counts.append((target_date, count))
    total_signals += count
    status = "✅" if count >= 1 else "❌"
    print(f"{target_date}: {count} 只 {status}")

print()
print("="*50)
days_with_signal = sum(1 for _, c in daily_counts if c >= 1)
print(f"总信号数: {total_signals}")
print(f"有信号天数: {days_with_signal}/{len(target_dates)} = {days_with_signal/len(target_dates)*100:.1f}%")
print(f"日均信号: {total_signals/len(target_dates):.2f} 只")
