# -*- coding: utf-8 -*-
"""
双创板60日新高突破回测 - 简化版
条件放宽：一波拉升>15% → 调整 → 突破60日新高
"""
import os, sys, time, datetime, json
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import numpy as np
import tushare as ts
import pickle

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 参数（放宽）
SURGE_MIN = 0.15  # 一波拉升>15%
ADJUST_MAX = 90   # 调整期最长90天
LOOKBACK_DAYS = 20  # wave1回看20天
START_DATE = '20240101'
END_DATE = '20260620'

# 获取双创板前100只测试
try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    pool = sb[sb['ts_code'].str.startswith(('300', '688'))]['ts_code'].tolist()[:100]
except:
    pool = []

print(f'双创板样本: {len(pool)} 只')

# ────────────────────────────────────────────────────────────────────
all_signals = []
t0 = time.time()

for idx, code in enumerate(pool):
    if (idx + 1) % 20 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (idx + 1) * (len(pool) - idx - 1)
        print(f'进度 {idx+1}/{len(pool)}  耗时{elapsed:.0f}s  ETA{eta:.0f}s')

    try:
        df = pro.stk_factor_pro(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 100:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values
        n = len(df)

        # 遍历每个60日新高
        for i in range(60, n - 10):
            high_60 = closes[i-60:i].max()
            if closes[i] <= high_60:
                continue

            # 向前找一波拉升（更宽松）
            for lookback in range(3, min(ADJUST_MAX, i)):
                end_idx = i - lookback
                if end_idx < LOOKBACK_DAYS:
                    continue

                window = closes[end_idx - LOOKBACK_DAYS:end_idx + 1]
                low_idx = np.argmin(window)
                high_idx = np.argmax(window)

                if high_idx <= low_idx:
                    continue
                if high_idx - low_idx < 5:  # 至少5天的拉升
                    continue

                surge = (window[high_idx] - window[low_idx]) / window[low_idx]
                if surge < SURGE_MIN:
                    continue

                wave1_high_idx = end_idx - LOOKBACK_DAYS + high_idx
                wave1_high = closes[wave1_high_idx]

                # 调整期
                adjust_days = i - wave1_high_idx
                if adjust_days > ADJUST_MAX or adjust_days < 2:
                    continue

                # 突破验证：当前接近或超过wave1高点（-5%内）
                if closes[i] < wave1_high * 0.95:
                    continue

                # ── 计算评分 ──
                row = df.iloc[i]
                rsi = float(row.get('rsi_qfq_6', 50))
                kdj_j = float(row.get('kdj_qfq', 50))
                cci = float(row.get('cci_qfq', 0))
                wr = float(row.get('wr_qfq', 50))
                mfi = float(row.get('mfi_qfq', 50))
                macd_dif = float(row.get('macd_dif_qfq', 0))
                macd_dea = float(row.get('macd_dea_qfq', 0))
                adx = float(row.get('dmi_adx_qfq', 0))
                vol_ratio = float(row.get('volume_ratio', 1.0))

                score = 0
                details = []
                if rsi < 40: score += 2; details.append(f'RSI={rsi:.0f}')
                if kdj_j < 20: score += 2; details.append(f'KDJ-J={kdj_j:.0f}')
                if cci < -100: score += 2; details.append(f'CCI={cci:.0f}')
                if wr > 80: score += 2; details.append(f'WR={wr:.0f}')
                if mfi < 30: score += 1; details.append(f'MFI={mfi:.0f}')
                if macd_dif > macd_dea: score += 2; details.append('MACD金叉')
                if adx > 25: score += 2; details.append(f'ADX={adx:.0f}')
                if vol_ratio > 1.2: score += 1; details.append(f'量比={vol_ratio:.2f}')

                # 后续涨幅
                entry = closes[i]
                gain_10d = (closes[i+10] - entry) / entry * 100 if i + 10 < n else None
                gain_20d = (closes[i+20] - entry) / entry * 100 if i + 20 < n else None

                all_signals.append({
                    'ts_code': code,
                    'trade_date': df.iloc[i]['trade_date'],
                    'entry_price': round(entry, 2),
                    'wave1_gain': round(surge * 100, 1),
                    'adjust_days': adjust_days,
                    'breakout_pct': round((closes[i] - wave1_high) / wave1_high * 100, 1),
                    'score': score,
                    'score_details': '; '.join(details),
                    'rsi': round(rsi, 1),
                    'volume_ratio': round(vol_ratio, 2),
                    'macd_golden': macd_dif > macd_dea,
                    'adx': round(adx, 1),
                    'gain_10d': round(gain_10d, 2) if gain_10d else None,
                    'gain_20d': round(gain_20d, 2) if gain_20d else None,
                })
                break  # 只记录最近的wave1

            # 限制每个股票最多记录5个信号
            if len([s for s in all_signals if s['ts_code'] == code]) >= 5:
                break

        time.sleep(0.12)
    except Exception as e:
        continue

# ────────────────────────────────────────────────────────────────────
print(f'\n扫描完成！找到 {len(all_signals)} 个信号')

if not all_signals:
    print('未找到信号')
else:
    df = pd.DataFrame(all_signals)
    df['win_10d'] = df['gain_10d'] > 0
    df['win_20d'] = df['gain_20d'] > 0

    print(f'\n总体统计:')
    print(f'  10日胜率: {df["win_10d"].mean()*100:.1f}%  均涨{df["gain_10d"].mean():.2f}%')
    print(f'  20日胜率: {df["win_20d"].mean()*100:.1f}%  均涨{df["gain_20d"].mean():.2f}%')

    # 按评分分层
    print('\n--- 评分分层 ---')
    df['tier'] = pd.cut(df['score'], bins=[-1, 4, 8, 100], labels=['0-4分', '5-8分', '9+分'])
    tier_stats = df.groupby('tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
        avg_gain_20d=('gain_20d', 'mean'),
    ).reset_index()
    tier_stats['win_rate_10d'] = (tier_stats['win_rate_10d'] * 100).round(1)
    tier_stats['avg_gain_10d'] = tier_stats['avg_gain_10d'].round(2)
    tier_stats['avg_gain_20d'] = tier_stats['avg_gain_20d'].round(2)
    print(tier_stats.to_string(index=False))

    # 指标组合
    print('\n--- 指标组合胜率 ---')
    combo1 = df[(df['macd_golden']) & (df['adx'] > 25)]
    if len(combo1) >= 5:
        print(f'MACD金叉+ADX>25 ({len(combo1)}): 胜率{combo1["win_10d"].mean()*100:.1f}% 均涨{combo1["gain_10d"].mean():.2f}%')

    combo2 = df[(df['macd_golden']) & (df['volume_ratio'] > 1.2)]
    if len(combo2) >= 5:
        print(f'MACD金叉+量比>1.2 ({len(combo2)}): 胜率{combo2["win_10d"].mean()*100:.1f}% 均涨{combo2["gain_10d"].mean():.2f}%')

    combo3 = df[df['score'] >= 9]
    if len(combo3) >= 5:
        print(f'评分>=9分 ({len(combo3)}): 胜率{combo3["win_10d"].mean()*100:.1f}% 均涨{combo3["gain_10d"].mean():.2f}%')

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = f'{out_dir}\\breakout_test_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')
