# -*- coding: utf-8 -*-
"""
快速验证：用前200只合格股测试最近10天的信号数
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
from datetime import datetime

# 读取合格股池
pool_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
all_codes = pool_df['code'].tolist()
# 格式化代码
codes = []
for c in all_codes[:200]:
    c = str(c).zfill(6)
    if c.startswith(('60', '688')):
        codes.append(c + '.SH')
    else:
        codes.append(c + '.SZ')

print(f"测试股票数: {len(codes)}只（前200只合格股）")

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

# 遍历每只股票，收集所有历史信号
all_signals = []

for i, code in enumerate(codes):
    if i % 50 == 0:
        print(f"  进度: {i}/{len(codes)}")
    try:
        df = detector.load_data(code, lookback=500)
        if df is None or len(df) < 60:
            continue
        
        closes = df['close'].values
        volumes = df['vol'].values
        n = len(df)
        
        wave1_candidates = detector._find_recent_wave1(closes, n)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
            wave1_high_price = closes[wave1_high_idx]
            post_high = closes[wave1_high_idx:]
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
            
            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue
            
            # v3.3硬过滤
            surge_pct = round(surge_gain * 100, 1)
            if not (0.03 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
                continue
            
            # 不创新低
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
            
            # 评分
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
            score_result = detector.scorer.score(
                df.iloc[entry_idx], prev_row,
                wave1_gain_pct=surge_pct,
                new_high_confirmed=new_high_confirmed,
                new_high_pullback=new_high_pullback,
                is_higher_low=is_higher_low,
                pattern_type='强势横盘',
                gap_to_peak_pct=gap_to_peak,
                pullback_pct=pullback_pct,
                is_deep_long_consolidation=False,
                limitup_score=0,
                volume_recovery_score=0
            )
            
            divs = detector.scorer.check_divergence(df, entry_idx)
            for key, div in divs.items():
                if div.get('found'):
                    score_result['total'] += div['pts']
            
            dmi_cross = detector.scorer.check_dmi_crossover(df, entry_idx)
            if dmi_cross.get('found'):
                score_result['total'] += dmi_cross['pts']
            
            bonus_pts, _ = detector._board_bonus(code, '强势横盘')
            score_result['total'] += bonus_pts
            
            if score_result['total'] >= 25:
                entry_date = df.index[entry_idx]
                if hasattr(entry_date, 'strftime'):
                    date_str = entry_date.strftime('%Y%m%d')
                else:
                    date_str = str(entry_date)[:10].replace('-', '')
                
                all_signals.append({
                    'date': date_str,
                    'code': code,
                    'score': score_result['total'],
                    'pullback': round(pullback_pct * 100, 1),
                    'surge': surge_pct
                })
    except Exception as e:
        continue

print(f"\n扫描完成，共找到 {len(all_signals)} 个信号")

# 统计最近10个交易日
import tushare as ts
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
os.environ['TS_TOKEN'] = os.environ['TUSHARE_TOKEN']
pro = ts.pro_api()

cal = pro.trade_cal(exchange='SSE', start_date='20260601', end_date='20260630')
trade_days = sorted(cal[cal['is_open'] == 1]['cal_date'].tolist())
last_10 = trade_days[-10:]

print(f"\n最近10个交易日每日信号数（前200只合格股）:")
daily_counts = {}
for d in last_10:
    cnt = sum(1 for s in all_signals if s['date'] == d)
    daily_counts[d] = cnt
    print(f"  {d}: {cnt}只")

print(f"\n最近10天统计:")
counts = list(daily_counts.values())
print(f"  有信号天数: {sum(1 for c in counts if c > 0)}/10")
print(f"  ≥1只天数: {sum(1 for c in counts if c >= 1)}/10")
print(f"  日均: {np.mean(counts):.2f}只")
print(f"  最高: {max(counts)}只")

# 推算全量946只的情况
print(f"\n推算全量946只合格股的情况（×{946/200:.1f}）:")
print(f"  日均: {np.mean(counts) * 946/200:.2f}只")
print(f"  10天总信号约: {len(all_signals) * 946/200:.0f}只")

# 显示最近5天的信号详情
print(f"\n最近5天信号详情（前200只）:")
for d in last_10[-5:]:
    day_signals = [s for s in all_signals if s['date'] == d]
    if day_signals:
        print(f"  {d}:")
        for s in sorted(day_signals, key=lambda x: -x['score']):
            print(f"    {s['code']} 评分={s['score']} 回调={s['pullback']}% 一波={s['surge']}%")
