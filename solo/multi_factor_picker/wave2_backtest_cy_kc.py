#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
双创板深度回调细分回测
对比：创新低 vs 不创新低 的二波胜率
"""
import tushare as ts
import os, sys
import json
from datetime import datetime, timedelta
import time

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        TOKEN = _l.strip().split('=', 1)[1].strip().strip('"')
        break
pro = ts.pro_api(TOKEN)

OUT_DIR = r'D:\mystock\solo\multi_factor_picker\output'
os.makedirs(OUT_DIR, exist_ok=True)

def get_cy_kc_pool():
    df = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    cy = df[df['ts_code'].str.startswith(('300', '688', '301'))]
    return cy['ts_code'].tolist()

def get_daily_data(ts_code, start, end):
    try:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and not df.empty:
            df = df.sort_values('trade_date')
            df['close_qfq'] = df['close']  # 简化：用close代替qfq
            return df
    except:
        pass
    return None

def backtest_cy_kc_deepcallback():
    """双创板深度回调细分回测"""
    print("=" * 60)
    print("双创板深度回调 — 创新低 vs 不创新低 胜率对比")
    print("=" * 60)
    
    pool = get_cy_kc_pool()
    print(f"双创板股票池: {len(pool)} 只")
    
    start_date = '20240101'
    end_date = '20260620'
    
    # 收集所有信号
    signals = []  # (ts_code, wave1_start, wave1_end, wave2_start, is_higher_low)
    
    for i, ts_code in enumerate(pool):
        if i % 50 == 0:
            print(f"  进度: {i}/{len(pool)}")
        
        df = get_daily_data(ts_code, start_date, end_date)
        if df is None or len(df) < 40:
            continue
        
        # 找一波拉升
        for j in range(20, len(df) - 20):
            price_20d_before = df.iloc[j-20]['close']
            price_now = df.iloc[j]['close']
            gain = (price_now - price_20d_before) / price_20d_before * 100
            
            if gain < 20:
                continue
            
            # 找到一波终点（最大涨幅）
            wave1_start_idx = j - 20
            wave1_end_idx = j
            wave1_max = price_now
            
            for k in range(j, min(j + 20, len(df))):
                if df.iloc[k]['close'] > wave1_max:
                    wave1_max = df.iloc[k]['close']
                    wave1_end_idx = k
            
            # 检查是否真的一波（回调 < 10% 时还在涨）
            wave1_high = wave1_max
            
            # 找调整期（一波结束后 5~40 天）
            for m in range(wave1_end_idx + 5, min(wave1_end_idx + 45, len(df))):
                price_m = df.iloc[m]['close']
                pullback = (wave1_high - price_m) / wave1_high * 100
                
                if pullback < 10:  # 不是深度回调
                    continue
                
                # 深度回调：回调 >= 15%
                if pullback >= 15:
                    # 判断创新低 vs 不创新低
                    # 一波启动前最低价
                    pre_wave_low = df.iloc[max(0, wave1_start_idx-10):wave1_start_idx+1]['close'].min()
                    # 调整期最低价
                    adjust_low = df.iloc[wave1_end_idx:m+1]['close'].min()
                    
                    is_higher_low = adjust_low > pre_wave_low  # 不创新低
                    
                    # 记录二波结果
                    # 入场价 = 调整低点价
                    entry_price = price_m
                    entry_idx = m
                    
                    # 检查未来20天是否有二波（涨幅>10%）
                    has_wave2 = 0
                    wave2_max_gain = 0
                    for n in range(entry_idx + 1, min(entry_idx + 21, len(df))):
                        future_gain = (df.iloc[n]['close'] - entry_price) / entry_price * 100
                        if future_gain > wave2_max_gain:
                            wave2_max_gain = future_gain
                        if future_gain > 10:
                            has_wave2 = 1
                            break
                    
                    signals.append({
                        'ts_code': ts_code,
                        'is_higher_low': is_higher_low,  # 1=不创新低, 0=创新低
                        'has_wave2': has_wave2,
                        'wave2_max_gain': wave2_max_gain,
                        'pullback_pct': pullback,
                        'wave1_gain': (wave1_high - price_20d_before) / price_20d_before * 100,
                    })
                    
                    break  # 每个一波只取第一个深度回调信号
            
            # 跳出内层循环（已处理该一波）
            break
        
        time.sleep(0.06)  # Tushare限速
    
    # 统计
    df_sig = pd.DataFrame(signals)
    
    print(f"\n总信号数: {len(df_sig)}")
    
    # 分层统计
    higher_low = df_sig[df_sig['is_higher_low'] == True]
    lower_low = df_sig[df_sig['is_higher_low'] == False]
    
    print(f"\n【不创新低深度回调】（调整低点 > 一波前低点）:")
    print(f"  信号数: {len(higher_low)}")
    if len(higher_low) > 0:
        win_rate = higher_low['has_wave2'].mean() * 100
        avg_gain = higher_low['wave2_max_gain'].mean()
        print(f"  二波胜率: {win_rate:.1f}%")
        print(f"  平均最大涨幅: {avg_gain:.1f}%")
    
    print(f"\n【创新低深度回调】（调整低点 ≤ 一波前低点）:")
    print(f"  信号数: {len(lower_low)}")
    if len(lower_low) > 0:
        win_rate = lower_low['has_wave2'].mean() * 100
        avg_gain = lower_low['wave2_max_gain'].mean()
        print(f"  二波胜率: {win_rate:.1f}%")
        print(f"  平均最大涨幅: {avg_gain:.1f}%")
    
    # 按回调幅度分层
    print(f"\n【按回调幅度分层】")
    df_sig['pullback_tier'] = pd.cut(df_sig['pullback_pct'], 
                                       bins=[15, 25, 35, 50, 100],
                                       labels=['15-25%', '25-35%', '35-50%', '50%+'])
    for tier in ['15-25%', '25-35%', '35-50%', '50%+']:
        subset = df_sig[df_sig['pullback_tier'] == tier]
        if len(subset) > 0:
            wr = subset['has_wave2'].mean() * 100
            print(f"  {tier}: {len(subset)}只, 胜率{wr:.1f}%, 均涨{subset['wave2_max_gain'].mean():.1f}%")
    
    # 保存结果
    result = {
        'total_signals': len(df_sig),
        'higher_low': {
            'count': len(higher_low),
            'win_rate': float(higher_low['has_wave2'].mean() * 100) if len(higher_low) > 0 else 0,
            'avg_gain': float(higher_low['wave2_max_gain'].mean()) if len(higher_low) > 0 else 0,
        },
        'lower_low': {
            'count': len(lower_low),
            'win_rate': float(lower_low['has_wave2'].mean() * 100) if len(lower_low) > 0 else 0,
            'avg_gain': float(lower_low['wave2_max_gain'].mean()) if len(lower_low) > 0 else 0,
        },
    }
    
    out_path = os.path.join(OUT_DIR, f'wave2_cy_kc_backtest_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存: {out_path}")
    return result

if __name__ == '__main__':
    import pandas as pd
    result = backtest_cy_kc_deepcallback()
    print("\n" + "=" * 60)
    print("回测完成")
    print("=" * 60)
