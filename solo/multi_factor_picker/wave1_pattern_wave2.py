# -*- coding: utf-8 -*-
"""
一波拉升形态对二波力度的影响
验证：不同一波形态（涨幅/时长/量能/RSI）下，二波成功率与涨幅差异
"""
import os, sys, time, datetime
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import numpy as np
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 参数
SURGE_DAYS_MIN = 10
SURGE_DAYS_MAX = 30
SURGE_MIN = 0.20
ADJUST_MAX = 90
WAVE2_DAYS = 20
WAVE2_MIN = 0.10
START_DATE = '20240101'
END_DATE = '20260620'

# 获取股票池（沪深300 + 双创板各100只）
print('获取股票池...')
try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    hs = sb[sb['ts_code'].str.startswith('60')]['ts_code'].tolist()[:100]
    cy = sb[sb['ts_code'].str.startswith(('300', '688'))]['ts_code'].tolist()[:100]
    pool = hs + cy
    print(f'股票池: 沪深主板{len(hs)}只 + 双创板{len(cy)}只 = {len(pool)}只')
except Exception as e:
    print(f'获取股票池失败: {e}')
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────
all_waves = []
t0 = time.time()

print(f'\n开始扫描一波拉升形态...')
for idx, code in enumerate(pool):
    if (idx + 1) % 30 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (idx + 1) * (len(pool) - idx - 1)
        print(f'进度 {idx+1}/{len(pool)}  耗时{elapsed:.0f}s  ETA{eta:.0f}s')

    try:
        df = pro.stk_factor_pro(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 150:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values
        n = len(df)

        # 找一波拉升
        for i in range(30, n - 50):
            # 向后扫描一波拉升
            for wave1_len in range(SURGE_DAYS_MIN, min(SURGE_DAYS_MAX + 1, n - i)):
                window = closes[i:i+wave1_len]
                low_idx = np.argmin(window[:wave1_len//2])  # 前半段找低点
                high_idx = np.argmax(window[low_idx:]) + low_idx  # 后半段找高点

                if high_idx <= low_idx:
                    continue

                wave1_gain = (window[high_idx] - window[low_idx]) / window[low_idx]
                if wave1_gain < SURGE_MIN:
                    continue

                wave1_high_idx = i + high_idx
                wave1_low_idx = i + low_idx
                wave1_high = closes[wave1_high_idx]
                wave1_low = closes[wave1_low_idx]

                # 一波拉升形态指标
                wave1_row = df.iloc[wave1_high_idx]
                wave1_rsi = float(wave1_row.get('rsi_qfq_6', 50))
                wave1_vol_ratio = float(wave1_row.get('volume_ratio', 1.0))
                wave1_macd_dif = float(wave1_row.get('macd_dif_qfq', 0))
                wave1_macd_dea = float(wave1_row.get('macd_dea_qfq', 0))
                wave1_adx = float(wave1_row.get('dmi_adx_qfq', 0))

                # 一波拉升期间量能变化
                wave1_vols = []
                for k in range(wave1_low_idx, wave1_high_idx + 1):
                    if k < len(df):
                        wave1_vols.append(float(df.iloc[k].get('vol', 0)))
                wave1_vol_avg = np.mean(wave1_vols) if wave1_vols else 0
                wave1_vol_trend = (wave1_vols[-1] / wave1_vols[0]) if len(wave1_vols) > 1 and wave1_vols[0] > 0 else 1.0

                # 找调整低点
                adjust_low_idx = wave1_high_idx + 1
                adjust_low = closes[wave1_high_idx]
                for j in range(wave1_high_idx + 1, min(wave1_high_idx + ADJUST_MAX + 1, n)):
                    if closes[j] < adjust_low:
                        adjust_low = closes[j]
                        adjust_low_idx = j

                adjust_days = adjust_low_idx - wave1_high_idx
                if adjust_days < 3 or adjust_days > ADJUST_MAX:
                    continue

                pullback_pct = (wave1_high - adjust_low) / wave1_high
                if pullback_pct < 0.05:  # 调整至少5%
                    continue

                # 调整低点指标
                adjust_row = df.iloc[adjust_low_idx]
                adjust_rsi = float(adjust_row.get('rsi_qfq_6', 50))
                adjust_kdj_j = float(adjust_row.get('kdj_qfq', 50))
                adjust_vol_ratio = float(adjust_row.get('volume_ratio', 1.0))

                # 找二波
                wave2_high = adjust_low
                wave2_high_idx = adjust_low_idx
                for j in range(adjust_low_idx + 1, min(adjust_low_idx + WAVE2_DAYS + 1, n)):
                    if closes[j] > wave2_high:
                        wave2_high = closes[j]
                        wave2_high_idx = j

                wave2_gain = (wave2_high - adjust_low) / adjust_low
                wave2_success = wave2_gain >= WAVE2_MIN

                all_waves.append({
                    'ts_code': code,
                    'wave1_date': df.iloc[wave1_high_idx]['trade_date'],
                    'adjust_date': df.iloc[adjust_low_idx]['trade_date'],
                    'wave1_gain': round(wave1_gain * 100, 1),
                    'wave1_days': wave1_high_idx - wave1_low_idx,
                    'wave1_rsi_peak': round(wave1_rsi, 1),
                    'wave1_vol_ratio': round(wave1_vol_ratio, 2),
                    'wave1_vol_trend': round(wave1_vol_trend, 2),
                    'wave1_adx': round(wave1_adx, 1),
                    'wave1_macd_golden': wave1_macd_dif > wave1_macd_dea,
                    'pullback_pct': round(pullback_pct * 100, 1),
                    'adjust_days': adjust_days,
                    'adjust_rsi': round(adjust_rsi, 1),
                    'adjust_kdj_j': round(adjust_kdj_j, 1),
                    'wave2_gain': round(wave2_gain * 100, 1),
                    'wave2_success': wave2_success,
                })
                break

            # 每只股票最多记录10个案例
            if len([w for w in all_waves if w['ts_code'] == code]) >= 10:
                break

        time.sleep(0.12)
    except Exception:
        continue

# ────────────────────────────────────────────────────────────────────
print(f'\n扫描完成！找到 {len(all_waves)} 个案例')

if not all_waves:
    print('未找到案例')
else:
    df = pd.DataFrame(all_waves)

    print(f'\n{'='*70}')
    print(f'  一波拉升形态对二波力度的影响')
    print(f'{'='*70}')

    print(f'\n总体统计 ({len(df)}个案例):')
    print(f'  二波成功率: {df["wave2_success"].mean()*100:.1f}%')
    print(f'  二波均涨: {df["wave2_gain"].mean():.2f}%')
    print(f'  一波均涨: {df["wave1_gain"].mean():.2f}%')

    # ── 一波涨幅分层 ──
    print('\n--- 一波涨幅分层 ---')
    df['wave1_gain_tier'] = pd.cut(df['wave1_gain'], bins=[0, 25, 35, 50, 1000],
                                     labels=['20-25%', '25-35%', '35-50%', '>50%'])
    tier_stats = df.groupby('wave1_gain_tier', observed=True).agg(
        n=('ts_code', 'count'),
        wave2_success_rate=('wave2_success', 'mean'),
        avg_wave2_gain=('wave2_gain', 'mean'),
        avg_wave1_gain=('wave1_gain', 'mean'),
    ).reset_index()
    tier_stats['wave2_success_rate'] = (tier_stats['wave2_success_rate'] * 100).round(1)
    tier_stats['avg_wave2_gain'] = tier_stats['avg_wave2_gain'].round(2)
    tier_stats['avg_wave1_gain'] = tier_stats['avg_wave1_gain'].round(1)
    print(tier_stats.to_string(index=False))

    # ── 一波时长分层 ──
    print('\n--- 一波拉升时长分层 ---')
    df['wave1_days_tier'] = pd.cut(df['wave1_days'], bins=[0, 7, 14, 21, 100],
                                     labels=['<7天', '7-14天', '14-21天', '>21天'])
    days_stats = df.groupby('wave1_days_tier', observed=True).agg(
        n=('ts_code', 'count'),
        wave2_success_rate=('wave2_success', 'mean'),
        avg_wave2_gain=('wave2_gain', 'mean'),
    ).reset_index()
    days_stats['wave2_success_rate'] = (days_stats['wave2_success_rate'] * 100).round(1)
    days_stats['avg_wave2_gain'] = days_stats['avg_wave2_gain'].round(2)
    print(days_stats.to_string(index=False))

    # ── 一波RSI峰值分层 ──
    print('\n--- 一波拉升RSI峰值分层 ---')
    df['wave1_rsi_tier'] = pd.cut(df['wave1_rsi_peak'], bins=[0, 60, 70, 80, 100],
                                    labels=['<60', '60-70', '70-80', '>80'])
    rsi_stats = df.groupby('wave1_rsi_tier', observed=True).agg(
        n=('ts_code', 'count'),
        wave2_success_rate=('wave2_success', 'mean'),
        avg_wave2_gain=('wave2_gain', 'mean'),
        avg_pullback=('pullback_pct', 'mean'),
    ).reset_index()
    rsi_stats['wave2_success_rate'] = (rsi_stats['wave2_success_rate'] * 100).round(1)
    rsi_stats['avg_wave2_gain'] = rsi_stats['avg_wave2_gain'].round(2)
    rsi_stats['avg_pullback'] = rsi_stats['avg_pullback'].round(1)
    print(rsi_stats.to_string(index=False))

    # ── 一波量能趋势分层 ──
    print('\n--- 一波拉升量能趋势分层 ---')
    df['wave1_vol_trend_tier'] = pd.cut(df['wave1_vol_trend'], bins=[0, 1.0, 1.5, 2.0, 100],
                                          labels=['缩量(<1)', '温和(1-1.5)', '放量(1.5-2)', '巨量(>2)'])
    vol_stats = df.groupby('wave1_vol_trend_tier', observed=True).agg(
        n=('ts_code', 'count'),
        wave2_success_rate=('wave2_success', 'mean'),
        avg_wave2_gain=('wave2_gain', 'mean'),
    ).reset_index()
    vol_stats['wave2_success_rate'] = (vol_stats['wave2_success_rate'] * 100).round(1)
    vol_stats['avg_wave2_gain'] = vol_stats['avg_wave2_gain'].round(2)
    print(vol_stats.to_string(index=False))

    # ── 一波ADX分层 ──
    print('\n--- 一波拉升ADX分层 ---')
    df['wave1_adx_tier'] = pd.cut(df['wave1_adx'], bins=[0, 25, 40, 100],
                                    labels=['<25弱趋势', '25-40强趋势', '>40极强'])
    adx_stats = df.groupby('wave1_adx_tier', observed=True).agg(
        n=('ts_code', 'count'),
        wave2_success_rate=('wave2_success', 'mean'),
        avg_wave2_gain=('wave2_gain', 'mean'),
    ).reset_index()
    adx_stats['wave2_success_rate'] = (adx_stats['wave2_success_rate'] * 100).round(1)
    adx_stats['avg_wave2_gain'] = adx_stats['avg_wave2_gain'].round(2)
    print(adx_stats.to_string(index=False))

    # ── 组合分析：一波形态×调整RSI ──
    print('\n--- 一波形态×调整RSI交叉分析 ---')

    # 一波涨幅大 + 调整RSI低
    high_wave1_low_rsi = df[(df['wave1_gain'] >= 35) & (df['adjust_rsi'] < 50)]
    if len(high_wave1_low_rsi) > 0:
        print(f'\n【一波涨幅>=35% + 调整RSI<50】({len(high_wave1_low_rsi)}个)')
        print(f'  二波成功率: {high_wave1_low_rsi["wave2_success"].mean()*100:.1f}%')
        print(f'  二波均涨: {high_wave1_low_rsi["wave2_gain"].mean():.2f}%')

    # 一波RSI峰值高 + 调整RSI低
    high_peak_low_adjust = df[(df['wave1_rsi_peak'] >= 75) & (df['adjust_rsi'] < 50)]
    if len(high_peak_low_adjust) > 0:
        print(f'\n【一波RSI峰值>=75 + 调整RSI<50】({len(high_peak_low_adjust)}个)')
        print(f'  二波成功率: {high_peak_low_adjust["wave2_success"].mean()*100:.1f}%')
        print(f'  二波均涨: {high_peak_low_adjust["wave2_gain"].mean():.2f}%')

    # 一波量能放大 + 调整缩量
    high_vol_low_adjust = df[(df['wave1_vol_trend'] >= 1.5) & (df['adjust_kdj_j'] < 20)]
    if len(high_vol_low_adjust) > 0:
        print(f'\n【一波放量拉升 + 调整KDJ-J<20】({len(high_vol_low_adjust)}个)')
        print(f'  二波成功率: {high_vol_low_adjust["wave2_success"].mean()*100:.1f}%')
        print(f'  二波均涨: {high_vol_low_adjust["wave2_gain"].mean():.2f}%')

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = f'{out_dir}\\wave1_pattern_wave2_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')

    # TOP案例
    print('\n--- 二波最成功的TOP20案例 ---')
    top20 = df.nlargest(20, 'wave2_gain')
    for _, r in top20.iterrows():
        print(f"{r['ts_code']:<12} 一波{r['wave1_gain']:>5.1f}%/{r['wave1_days']:>2}天 RSI{r['wave1_rsi_peak']:>3.0f} "
              f"调整{r['adjust_days']:>2}天/{r['pullback_pct']:>4.1f}% RSI{r['adjust_rsi']:>3.0f} "
              f"二波+{r['wave2_gain']:>5.1f}%")
