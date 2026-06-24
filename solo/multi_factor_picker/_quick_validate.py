# -*- coding: utf-8 -*-
"""
沪深300小样本快速验证 - 60日新高突破策略
只测试50只股票，验证评分分层有效性
"""
import os, sys, time, datetime
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import numpy as np
import tushare as ts
import pickle

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 参数
SURGE_DAYS = 20
SURGE_MIN = 0.20
LOOKBACK = 150
FORWARD = 20
START_DATE = '20240101'
END_DATE = '20260620'

# 获取沪深300前50只
cache = r'D:\mystock\dragon\cache\csi2000_stocks.pkl'
try:
    with open(cache, 'rb') as f:
        pool = list(pickle.load(f))[:50]
except:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    pool = sb[~sb['ts_code'].str.startswith('688')]['ts_code'].tolist()[:50]

print(f'沪深300样本: {len(pool)} 只')

# 扫描
all_signals = []
for idx, code in enumerate(pool):
    if (idx + 1) % 10 == 0:
        print(f'进度 {idx+1}/{len(pool)}')
    try:
        df = pro.stk_factor_pro(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 100:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)

        closes = df['close'].values
        n = len(df)

        for i in range(60, n - 5):
            # 60日新高
            high_60 = closes[i-60:i].max()
            if closes[i] <= high_60:
                continue

            # 找wave1
            wave1_found = False
            for lookback in range(3, min(100, i)):
                end_idx = i - lookback
                if end_idx < SURGE_DAYS:
                    continue
                window = closes[end_idx - SURGE_DAYS:end_idx + 1]
                low_idx = np.argmin(window)
                high_idx = np.argmax(window)
                if high_idx <= low_idx:
                    continue
                surge = (window[high_idx] - window[low_idx]) / window[low_idx]
                if surge < SURGE_MIN:
                    continue

                wave1_high_idx = end_idx - SURGE_DAYS + high_idx
                if i - wave1_high_idx > 90 or i - wave1_high_idx < 2:
                    continue  # 调整期放宽到90天

                # 放宽：允许突破wave1高点或接近（-3%内）
                if closes[i] < closes[wave1_high_idx] * 0.97:
                    continue

                wave1_found = True

                # 评分
                row = df.iloc[i]
                rsi = float(row.get('rsi_qfq_6', 50))
                kdj_j = float(row.get('kdj_qfq', 50))
                macd_dif = float(row.get('macd_dif_qfq', 0))
                macd_dea = float(row.get('macd_dea_qfq', 0))
                adx = float(row.get('dmi_adx_qfq', 0))
                vol_ratio = float(row.get('volume_ratio', 1.0))

                score = 0
                if rsi < 40: score += 2
                elif rsi < 50: score += 1
                if kdj_j < 20: score += 2
                if macd_dif > macd_dea: score += 2
                if adx > 25: score += 2
                if vol_ratio > 1.2: score += 1

                # 后续涨幅
                entry = closes[i]
                gain_10d = (closes[i+10] - entry) / entry * 100 if i + 10 < n else None
                gain_20d = (closes[i+20] - entry) / entry * 100 if i + 20 < n else None

                all_signals.append({
                    'ts_code': code,
                    'trade_date': df.iloc[i]['trade_date'],
                    'score': score,
                    'gain_10d': gain_10d,
                    'gain_20d': gain_20d,
                    'rsi': rsi,
                    'volume_ratio': vol_ratio,
                    'macd_golden': macd_dif > macd_dea,
                    'adx': adx,
                })
                break

            if wave1_found:
                continue

        time.sleep(0.12)
    except Exception:
        continue

# 统计
df = pd.DataFrame(all_signals)
if len(df) == 0:
    print('未找到信号')
else:
    df['win_10d'] = df['gain_10d'] > 0
    df['win_20d'] = df['gain_20d'] > 0

    print(f'\n总样本: {len(df)}')
    print(f'10日胜率: {df["win_10d"].mean()*100:.1f}%  均涨{df["gain_10d"].mean():.2f}%')
    print(f'20日胜率: {df["win_20d"].mean()*100:.1f}%  均涨{df["gain_20d"].mean():.2f}%')

    # 按评分分层
    df['tier'] = pd.cut(df['score'], bins=[-1, 5, 10, 100], labels=['低分0-5', '中分6-10', '高分11+'])
    tier_stats = df.groupby('tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate=('win_10d', 'mean'),
        avg_gain=('gain_10d', 'mean'),
    ).reset_index()
    tier_stats['win_rate'] = (tier_stats['win_rate'] * 100).round(1)
    tier_stats['avg_gain'] = tier_stats['avg_gain'].round(2)
    print('\n评分分层:')
    print(tier_stats.to_string(index=False))

    # 组合统计
    print('\n指标组合:')
    combo1 = df[(df['macd_golden']) & (df['adx'] > 25)]
    if len(combo1) > 0:
        print(f'MACD金叉+ADX>25 ({len(combo1)}): 胜率{combo1["win_10d"].mean()*100:.1f}% 均涨{combo1["gain_10d"].mean():.2f}%')

    combo2 = df[(df['macd_golden']) & (df['volume_ratio'] > 1.2)]
    if len(combo2) > 0:
        print(f'MACD金叉+量比>1.2 ({len(combo2)}): 胜率{combo2["win_10d"].mean()*100:.1f}% 均涨{combo2["gain_10d"].mean():.2f}%')

    combo3 = df[df['score'] >= 8]
    if len(combo3) > 0:
        print(f'评分>=8分 ({len(combo3)}): 胜率{combo3["win_10d"].mean()*100:.1f}% 均涨{combo3["gain_10d"].mean():.2f}%')
