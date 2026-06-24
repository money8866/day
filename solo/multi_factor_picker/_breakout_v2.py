# -*- coding: utf-8 -*-
"""
双创板60日新高突破 - 加入超买过滤
关键改进：RSI>70为超买，扣分！
"""
import os, sys, time, datetime
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import numpy as np
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

SURGE_MIN = 0.15
ADJUST_MAX = 90
LOOKBACK_DAYS = 20
START_DATE = '20240101'
END_DATE = '20260620'

try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    pool = sb[sb['ts_code'].str.startswith(('300', '688'))]['ts_code'].tolist()[:100]
except:
    pool = []

print(f'双创板样本: {len(pool)} 只')

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

        for i in range(60, n - 10):
            high_60 = closes[i-60:i].max()
            if closes[i] <= high_60:
                continue

            for lookback in range(3, min(ADJUST_MAX, i)):
                end_idx = i - lookback
                if end_idx < LOOKBACK_DAYS:
                    continue

                window = closes[end_idx - LOOKBACK_DAYS:end_idx + 1]
                low_idx = np.argmin(window)
                high_idx = np.argmax(window)

                if high_idx <= low_idx or high_idx - low_idx < 5:
                    continue

                surge = (window[high_idx] - window[low_idx]) / window[low_idx]
                if surge < SURGE_MIN:
                    continue

                wave1_high_idx = end_idx - LOOKBACK_DAYS + high_idx
                wave1_high = closes[wave1_high_idx]
                adjust_days = i - wave1_high_idx

                if adjust_days > ADJUST_MAX or adjust_days < 2:
                    continue

                if closes[i] < wave1_high * 0.95:
                    continue

                # ── 新评分系统：加入超买扣分 ──
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
                bias1 = float(row.get('bias1_qfq', 0))

                score = 0
                details = []

                # ── 正向信号 ──
                if rsi < 40: score += 3; details.append(f'RSI={rsi:.0f}<40')
                elif rsi < 50: score += 2; details.append(f'RSI={rsi:.0f}<50')

                if kdj_j < 0: score += 3; details.append(f'KDJ-J={kdj_j:.0f}<0')
                elif kdj_j < 20: score += 2; details.append(f'KDJ-J={kdj_j:.0f}<20')

                if cci < -100: score += 2; details.append(f'CCI={cci:.0f}<-100')

                if wr > 80: score += 2; details.append(f'WR={wr:.0f}>80')

                if mfi < 30: score += 2; details.append(f'MFI={mfi:.0f}<30')

                if macd_dif > macd_dea: score += 2; details.append('MACD金叉')
                if adx > 25: score += 2; details.append(f'ADX={adx:.0f}>25')

                if vol_ratio > 1.5: score += 2; details.append(f'量比={vol_ratio:.2f}>1.5')
                elif vol_ratio > 1.2: score += 1; details.append(f'量比={vol_ratio:.2f}>1.2')

                if bias1 < -5: score += 2; details.append(f'BIAS1={bias1:.1f}%<-5%')

                # ── 负向信号（超买扣分！）──
                if rsi > 70: score -= 4; details.append(f'⚠RSI={rsi:.0f}>70超买(-4)')
                elif rsi > 60: score -= 2; details.append(f'RSI={rsi:.0f}>60偏高(-2)')

                if kdj_j > 100: score -= 3; details.append(f'⚠KDJ-J={kdj_j:.0f}>100超买(-3)')
                elif kdj_j > 80: score -= 1; details.append(f'KDJ-J={kdj_j:.0f}>80偏高(-1)')

                if cci > 100: score -= 2; details.append(f'CCI={cci:.0f}>100超买(-2)')

                # ── 后续涨幅 ──
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
                break

            if len([s for s in all_signals if s['ts_code'] == code]) >= 5:
                break

        time.sleep(0.12)
    except Exception:
        continue

# ────────────────────────────────────────────────────────────────────
print(f'\n扫描完成！找到 {len(all_signals)} 个信号')

if all_signals:
    df = pd.DataFrame(all_signals)
    df['win_10d'] = df['gain_10d'] > 0
    df['win_20d'] = df['gain_20d'] > 0

    print(f'\n总体统计:')
    print(f'  10日胜率: {df["win_10d"].mean()*100:.1f}%  均涨{df["gain_10d"].mean():.2f}%')
    print(f'  20日胜率: {df["win_20d"].mean()*100:.1f}%  均涨{df["gain_20d"].mean():.2f}%')

    # 按评分分层
    print('\n--- 评分分层（含超买扣分）---')
    df['tier'] = pd.cut(df['score'], bins=[-100, 0, 5, 100], labels=['负分(超买)', '0-5分', '6+分(优选)'])
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

    # RSI过滤效果
    print('\n--- RSI过滤效果 ---')
    rsi_low = df[df['rsi'] < 50]
    rsi_mid = df[(df['rsi'] >= 50) & (df['rsi'] < 70)]
    rsi_high = df[df['rsi'] >= 70]

    if len(rsi_low) > 0:
        print(f'RSI<50 ({len(rsi_low)}): 胜率{rsi_low["win_10d"].mean()*100:.1f}% 均涨{rsi_low["gain_10d"].mean():.2f}%')
    if len(rsi_mid) > 0:
        print(f'RSI 50-70 ({len(rsi_mid)}): 胜率{rsi_mid["win_10d"].mean()*100:.1f}% 均涨{rsi_mid["gain_10d"].mean():.2f}%')
    if len(rsi_high) > 0:
        print(f'RSI>70 ({len(rsi_high)}): 胜率{rsi_high["win_10d"].mean()*100:.1f}% 均涨{rsi_high["gain_10d"].mean():.2f}%')

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = f'{out_dir}\\breakout_v2_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')
