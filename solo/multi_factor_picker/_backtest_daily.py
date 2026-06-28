# -*- coding: utf-8 -*-
"""
回测5-6月每天的信号数量
测试不同条件组合下的每日信号分布
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import tushare as ts

os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
os.environ['TS_TOKEN'] = os.environ['TUSHARE_TOKEN']
pro = ts.pro_api()

# 读取合格股池
pool_path = r'D:\mystock\report_daily\bull_stocks_qualified.csv'
if os.path.exists(pool_path):
    pool_df = pd.read_csv(pool_path)
    codes = pool_df['ts_code'].tolist()
    print(f"合格股池: {len(codes)} 只")
else:
    print("合格股池文件不存在，使用全部A股")
    codes = []

# 交易日历 - 2026年5-6月
cal = pro.trade_cal(exchange='SSE', start_date='20260501', end_date='20260630')
trade_days = cal[cal['is_open'] == 1]['cal_date'].sort_values().tolist()
print(f"5-6月交易日: {len(trade_days)}天")
print(f"交易日: {trade_days[:5]}...{trade_days[-5:]}")
print()

# 导入扫描器
import wave2_pattern_scanner as scanner

# 定义测试方案 - 对应硬过滤条件
plans = [
    ("基准: 回调3-8%+一波30-50%+评分≥30",
     {'pullback_min': 0.03, 'pullback_max': 0.08,
      'surge_min': 30, 'surge_max': 50, 'score_min': 30}),
    
    ("方案A: 回调3-10%+一波25-60%+评分≥30",
     {'pullback_min': 0.03, 'pullback_max': 0.10,
      'surge_min': 25, 'surge_max': 60, 'score_min': 30}),
    
    ("方案B: 回调2-10%+一波20-60%+评分≥30",
     {'pullback_min': 0.02, 'pullback_max': 0.10,
      'surge_min': 20, 'surge_max': 60, 'score_min': 30}),
    
    ("方案C: 回调2-10%+一波20-60%+评分≥28",
     {'pullback_min': 0.02, 'pullback_max': 0.10,
      'surge_min': 20, 'surge_max': 60, 'score_min': 28}),
    
    ("方案D: 回调2-10%+一波20-80%+评分≥25",
     {'pullback_min': 0.02, 'pullback_max': 0.10,
      'surge_min': 20, 'surge_max': 80, 'score_min': 25}),
    
    ("方案E: 回调3-10%+一波20-60%+评分≥25",
     {'pullback_min': 0.03, 'pullback_max': 0.10,
      'surge_min': 20, 'surge_max': 60, 'score_min': 25}),
]

# 测试代码（用前30只股票快速验证）
test_codes = codes[:100] if len(codes) > 100 else codes
print(f"测试股票数: {len(test_codes)} 只（前100只快速验证）")
print()

# 为每个方案统计每日信号
results = {}
for plan_name, params in plans:
    daily_counts = {d: 0 for d in trade_days}
    signals_detail = []
    
    # 修改全局参数
    scanner.SCORE_SIDWAYS_MIN = params['score_min']
    
    detector = scanner.Wave2Detector()
    
    for code in test_codes:
        try:
            # 直接用detect_sideways_pattern扫描所有历史信号
            df = detector.load_data(code, lookback=500)
            if df is None or len(df) < 60:
                continue
            
            closes  = df['close'].values
            volumes = df['vol'].values
            n = len(df)
            
            wave1_candidates = detector._find_recent_wave1(closes, n)
            for wave1_high_idx, _, surge_gain in wave1_candidates:
                wave1_high_price = closes[wave1_high_idx]
                post_high = closes[wave1_high_idx:]
                if len(post_high) < 5:
                    continue
                
                low_after_high = post_high.min()
                pullback_pct   = (wave1_high_price - low_after_high) / wave1_high_price
                low_pos        = int(np.argmin(post_high))
                adjust_days    = low_pos
                
                if not (pullback_pct < 0.10 and adjust_days <= 15):
                    continue
                
                vol_base_start = max(0, wave1_high_idx - 60)
                base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
                vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
                
                if vol_ratio >= 0.80:
                    continue
                
                entry_idx = wave1_high_idx + low_pos
                if entry_idx >= n:
                    continue
                
                # 硬过滤
                surge_pct = round(surge_gain * 100, 1)
                if not (params['pullback_min'] <= pullback_pct < params['pullback_max'] and
                        params['surge_min'] <= surge_pct < params['surge_max']):
                    continue
                
                # 不创新低检测
                wave1_start_idx = max(0, wave1_high_idx - 20)
                pre_low_start  = max(0, wave1_start_idx - 20)
                if wave1_high_idx >= 40:
                    pre_low = closes[pre_low_start:wave1_start_idx+1].min()
                else:
                    pre_low = closes[0:wave1_high_idx+1].min()
                adj_low = closes[wave1_high_idx:entry_idx+1].min()
                is_higher_low = adj_low > pre_low
                if not is_higher_low:
                    continue
                
                # 计算评分（简化版：只算必要的）
                entry_date = df.index[entry_idx] if hasattr(df.index, 'strftime') else str(df.index[entry_idx])
                if isinstance(entry_date, str):
                    date_str = entry_date[:10].replace('-', '')
                else:
                    date_str = entry_date.strftime('%Y%m%d')
                
                # 只统计5-6月的信号
                if date_str not in trade_days:
                    continue
                
                # 评分检测
                prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
                new_high_confirmed = False
                new_high_pullback = False
                post_high_all = closes[wave1_high_idx:entry_idx + 1]
                if len(post_high_all) > 1:
                    max_post = post_high_all.max()
                    if max_post > wave1_high_price:
                        new_high_confirmed = True
                        new_high_idx_local = np.argmax(post_high_all)
                        if new_high_idx_local < len(post_high_all) - 1:
                            new_high_pullback = True
                
                gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
                is_long_consolidation = False
                
                score_result = detector.scorer.score(
                    df.iloc[entry_idx], prev_row,
                    wave1_gain_pct=surge_pct,
                    new_high_confirmed=new_high_confirmed,
                    new_high_pullback=new_high_pullback,
                    is_higher_low=is_higher_low,
                    pattern_type='强势横盘',
                    gap_to_peak_pct=gap_to_peak,
                    pullback_pct=pullback_pct,
                    is_deep_long_consolidation=is_long_consolidation,
                    limitup_score=0,
                    volume_recovery_score=0
                )
                
                # 底背离
                divs = detector.scorer.check_divergence(df, entry_idx)
                for key, div in divs.items():
                    if div.get('found'):
                        score_result['total'] += div['pts']
                
                # DMI交叉
                dmi_cross = detector.scorer.check_dmi_crossover(df, entry_idx)
                if dmi_cross.get('found'):
                    score_result['total'] += dmi_cross['pts']
                
                # 板块加分
                bonus_pts, _ = detector._board_bonus(code, '强势横盘')
                score_result['total'] += bonus_pts
                
                if score_result['total'] >= params['score_min']:
                    daily_counts[date_str] += 1
                    signals_detail.append({
                        'date': date_str,
                        'code': code,
                        'score': score_result['total'],
                        'pullback': round(pullback_pct * 100, 1),
                        'surge': surge_pct
                    })
        except Exception as e:
            continue
    
    # 统计
    counts = [v for v in daily_counts.values()]
    days_with_signal = sum(1 for c in counts if c > 0)
    days_with_1plus = sum(1 for c in counts if c >= 1)
    days_with_2plus = sum(1 for c in counts if c >= 2)
    avg_count = np.mean(counts) if counts else 0
    max_count = max(counts) if counts else 0
    
    results[plan_name] = {
        'daily_counts': daily_counts,
        'signals': signals_detail,
        'total_signals': len(signals_detail),
        'days_with_signal': days_with_signal,
        'days_ge1': days_with_1plus,
        'days_ge2': days_with_2plus,
        'avg_count': avg_count,
        'max_count': max_count
    }
    
    print(f"=== {plan_name} ===")
    print(f"  总信号数: {len(signals_detail)}")
    print(f"  有信号的天数: {days_with_signal}/{len(trade_days)} ({days_with_signal/len(trade_days)*100:.1f}%)")
    print(f"  ≥1只的天数: {days_with_1plus}天")
    print(f"  ≥2只的天数: {days_with_2plus}天")
    print(f"  日均: {avg_count:.2f}只 | 最高: {max_count}只")
    
    # 打印每日分布
    zero_days = [d for d, c in daily_counts.items() if c == 0]
    if zero_days:
        print(f"  0信号日期: {', '.join(zero_days[:10])}{'...' if len(zero_days)>10 else ''}")
    print()

print("=" * 60)
print("总结：每日≥1只信号的覆盖率")
for plan_name, r in results.items():
    pct = r['days_ge1'] / len(trade_days) * 100
    print(f"  {plan_name.split(':')[0]}: {r['days_ge1']}/{len(trade_days)}天 ({pct:.1f}%) | 总信号{r['total_signals']} | 日均{r['avg_count']:.2f}")
