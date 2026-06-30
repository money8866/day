# -*- coding: utf-8 -*-
"""
二波行情策略优化：放宽条件扩大样本
目标：在保持胜率>=70%的前提下，扩大有效信号数量
"""
import os, sys, time, datetime
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import pandas as pd
import numpy as np
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 参数
START_DATE = '20240101'
END_DATE = '20260620'
WAVE2_DAYS = 20
WAVE2_MIN = 0.10

# 获取股票池
print('获取股票池...')
try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    hs = sb[sb['ts_code'].str.startswith('60')]['ts_code'].tolist()[:100]
    cy = sb[sb['ts_code'].str.startswith(('300', '688'))]['ts_code'].tolist()[:100]
    pool = hs + cy
    print(f'股票池: {len(pool)}只')
except:
    pool = []
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────
all_waves = []
t0 = time.time()

print('\n开始扫描...')
for idx, code in enumerate(pool):
    if (idx + 1) % 30 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (idx + 1) * (len(pool) - idx - 1)
        print(f'进度 {idx+1}/{len(pool)}  ETA{eta:.0f}s')

    try:
        df = pro.stk_factor_pro(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 150:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values
        n = len(df)

        # 找一波拉升（放宽到>=20%）
        for i in range(30, n - 50):
            for wave1_len in range(10, 31):
                if i + wave1_len >= n:
                    continue
                window = closes[i:i+wave1_len]
                low_idx = np.argmin(window[:wave1_len//2])
                high_idx = np.argmax(window[low_idx:]) + low_idx

                if high_idx <= low_idx:
                    continue

                wave1_gain = (window[high_idx] - window[low_idx]) / window[low_idx]
                if wave1_gain < 0.20:  # 放宽到20%
                    continue

                wave1_high_idx = i + high_idx
                wave1_low_idx = i + low_idx
                wave1_high = closes[wave1_high_idx]

                # 一波形态指标
                wave1_row = df.iloc[wave1_high_idx]
                wave1_rsi = float(wave1_row.get('rsi_qfq_6', 50))
                wave1_vol_ratio = float(wave1_row.get('volume_ratio', 1.0))
                wave1_adx = float(wave1_row.get('dmi_adx_qfq', 0))

                # 找调整低点
                adjust_low_idx = wave1_high_idx + 1
                adjust_low = wave1_high
                for j in range(wave1_high_idx + 1, min(wave1_high_idx + 91, n)):
                    if closes[j] < adjust_low:
                        adjust_low = closes[j]
                        adjust_low_idx = j

                if adjust_low_idx == wave1_high_idx:
                    continue

                adjust_days = adjust_low_idx - wave1_high_idx
                if adjust_days < 3:
                    continue

                pullback_pct = (wave1_high - adjust_low) / wave1_high
                if pullback_pct < 0.05:
                    continue

                # 调整低点指标
                adjust_row = df.iloc[adjust_low_idx]
                adjust_rsi = float(adjust_row.get('rsi_qfq_6', 50))
                adjust_kdj_j = float(adjust_row.get('kdj_qfq', 50))
                adjust_vol_ratio = float(adjust_row.get('volume_ratio', 1.0))

                # 找二波
                wave2_high = adjust_low
                for j in range(adjust_low_idx + 1, min(adjust_low_idx + WAVE2_DAYS + 1, n)):
                    if closes[j] > wave2_high:
                        wave2_high = closes[j]

                wave2_gain = (wave2_high - adjust_low) / adjust_low
                wave2_success = wave2_gain >= WAVE2_MIN

                all_waves.append({
                    'ts_code': code,
                    'wave1_gain': round(wave1_gain * 100, 1),
                    'wave1_days': wave1_high_idx - wave1_low_idx,
                    'wave1_rsi': round(wave1_rsi, 1),
                    'wave1_vol_ratio': round(wave1_vol_ratio, 2),
                    'wave1_adx': round(wave1_adx, 1),
                    'adjust_days': adjust_days,
                    'pullback_pct': round(pullback_pct * 100, 1),
                    'adjust_rsi': round(adjust_rsi, 1),
                    'adjust_kdj_j': round(adjust_kdj_j, 1),
                    'wave2_gain': round(wave2_gain * 100, 1),
                    'wave2_success': wave2_success,
                })
                break

            if len([w for w in all_waves if w['ts_code'] == code]) >= 10:
                break

        time.sleep(0.12)
    except:
        continue

# ────────────────────────────────────────────────────────────────────
print(f'\n扫描完成！找到{len(all_waves)}个案例')

if not all_waves:
    sys.exit(1)

df = pd.DataFrame(all_waves)

print(f'\n{'='*70}')
print(f'  二波行情策略优化分析')
print(f'{'='*70}')

print(f'\n总体统计:')
print(f'  二波成功率: {df["wave2_success"].mean()*100:.1f}%')
print(f'  二波均涨: {df["wave2_gain"].mean():.2f}%')

# ── 策略1：原策略（一波>=35% + RSI<50）──
print('\n=== 策略1（原策略）：一波>=35% + 调整RSI<50 ===')
s1 = df[(df['wave1_gain'] >= 35) & (df['adjust_rsi'] < 50)]
if len(s1) > 0:
    print(f'样本数: {len(s1)}')
    print(f'二波成功率: {s1["wave2_success"].mean()*100:.1f}%')
    print(f'二波均涨: {s1["wave2_gain"].mean():.2f}%')
    print(f'占总样本: {len(s1)/len(df)*100:.1f}%')

# ── 策略2：放宽一波涨幅到25%──
print('\n=== 策略2：一波>=25% + 调整RSI<50 ===')
s2 = df[(df['wave1_gain'] >= 25) & (df['adjust_rsi'] < 50)]
if len(s2) > 0:
    print(f'样本数: {len(s2)}')
    print(f'二波成功率: {s2["wave2_success"].mean()*100:.1f}%')
    print(f'二波均涨: {s2["wave2_gain"].mean():.2f}%')
    print(f'占总样本: {len(s2)/len(df)*100:.1f}%')

# ── 策略3：放宽调整RSI到<60──
print('\n=== 策略3：一波>=35% + 调整RSI<60 ===')
s3 = df[(df['wave1_gain'] >= 35) & (df['adjust_rsi'] < 60)]
if len(s3) > 0:
    print(f'样本数: {len(s3)}')
    print(f'二波成功率: {s3["wave2_success"].mean()*100:.1f}%')
    print(f'二波均涨: {s3["wave2_gain"].mean():.2f}%')
    print(f'占总样本: {len(s3)/len(df)*100:.1f}%')

# ── 策略4：双放宽 ──
print('\n=== 策略4：一波>=25% + 调整RSI<60 ===')
s4 = df[(df['wave1_gain'] >= 25) & (df['adjust_rsi'] < 60)]
if len(s4) > 0:
    print(f'样本数: {len(s4)}')
    print(f'二波成功率: {s4["wave2_success"].mean()*100:.1f}%')
    print(f'二波均涨: {s4["wave2_gain"].mean():.2f}%')
    print(f'占总样本: {len(s4)/len(df)*100:.1f}%')

# ── 策略5：一波涨幅分层（无RSI限制）──
print('\n=== 策略5：仅一波涨幅分层（无RSI限制）===')
for threshold in [20, 25, 30, 35, 40, 50]:
    subset = df[df['wave1_gain'] >= threshold]
    if len(subset) > 0:
        print(f'一波>={threshold}%: 样本{len(subset)} 胜率{subset["wave2_success"].mean()*100:.1f}% 均涨{subset["wave2_gain"].mean():.1f}%')

# ── 策略6：调整RSI分层（无一波限制）──
print('\n=== 策略6：仅调整RSI分层（无一波限制）===')
for rsi_max in [40, 50, 60, 70]:
    subset = df[df['adjust_rsi'] < rsi_max]
    if len(subset) > 0:
        print(f'RSI<{rsi_max}: 样本{len(subset)} 胜率{subset["wave2_success"].mean()*100:.1f}% 均涨{subset["wave2_gain"].mean():.1f}%')

# ── 策略7：一波涨幅 + 调整幅度组合 ──
print('\n=== 策略7：一波涨幅 + 调整幅度组合 ===')
for wave1_min in [25, 30, 35]:
    for pullback_min in [10, 15, 20]:
        subset = df[(df['wave1_gain'] >= wave1_min) & (df['pullback_pct'] >= pullback_min)]
        if len(subset) > 10:
            print(f'一波>={wave1_min}% + 调整>={pullback_min}%: 样本{len(subset)} 胜率{subset["wave2_success"].mean()*100:.1f}%')

# ── 策略8：一波涨幅 + 调整天数组合 ──
print('\n=== 策略8：一波涨幅 + 调整天数组合 ===')
for wave1_min in [25, 30, 35]:
    for adjust_min in [5, 10, 15, 20]:
        subset = df[(df['wave1_gain'] >= wave1_min) & (df['adjust_days'] >= adjust_min)]
        if len(subset) > 10:
            print(f'一波>={wave1_min}% + 调整>={adjust_min}天: 样本{len(subset)} 胜率{subset["wave2_success"].mean()*100:.1f}%')

# ── 策略9：多因子组合评分 ──
print('\n=== 策略9：多因子评分系统 ===')
df['score'] = 0
df.loc[df['wave1_gain'] >= 35, 'score'] += 3
df.loc[(df['wave1_gain'] >= 25) & (df['wave1_gain'] < 35), 'score'] += 2
df.loc[df['adjust_rsi'] < 50, 'score'] += 3
df.loc[(df['adjust_rsi'] >= 50) & (df['adjust_rsi'] < 60), 'score'] += 2
df.loc[df['wave1_adx'] > 30, 'score'] += 2
df.loc[df['pullback_pct'] >= 15, 'score'] += 1

for score_min in [5, 6, 7, 8]:
    subset = df[df['score'] >= score_min]
    if len(subset) > 10:
        print(f'评分>={score_min}: 样本{len(subset)} 胜率{subset["wave2_success"].mean()*100:.1f}% 均涨{subset["wave2_gain"].mean():.1f}%')

# ── 最优策略推荐 ──
print('\n=== 最优策略推荐 ===')
print('基于胜率>=70%且样本数最大原则：')

strategies = [
    ('策略1', s1, 35, 50),
    ('策略2', s2, 25, 50),
    ('策略3', s3, 35, 60),
    ('策略4', s4, 25, 60),
]

best = None
for name, subset, wave1_min, rsi_max in strategies:
    if len(subset) > 0 and subset['wave2_success'].mean() >= 0.70:
        if best is None or len(subset) > len(best[1]):
            best = (name, subset, wave1_min, rsi_max)

if best:
    print(f'\n推荐：{best[0]}')
    print(f'条件：一波涨幅>={best[2]}% + 调整RSI<{best[3]}')
    print(f'样本数：{len(best[1])}')
    print(f'胜率：{best[1]["wave2_success"].mean()*100:.1f}%')
    print(f'均涨：{best[1]["wave2_gain"].mean():.2f}%')
else:
    print('未找到胜率>=70%的策略，推荐放宽胜率要求至60%')
    for name, subset, wave1_min, rsi_max in strategies:
        if len(subset) > 0 and subset['wave2_success'].mean() >= 0.60:
            print(f'{name}: 样本{len(subset)} 胜率{subset["wave2_success"].mean()*100:.1f}%')

# 保存
out_dir = r'D:\mystock\solo\multi_factor_picker\output'
ts_str = datetime.datetime.now().strftime('%H%M%S')
csv_path = f'{out_dir}\\wave2_strategy_optimize_{ts_str}.csv'
df.to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f'\n已保存: {csv_path}')
