# -*- coding: utf-8 -*-
"""
涨停调整期分层回测：2-5天 vs 6-10天 vs >10天
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
LIMIT_UP_MIN = 9.9
ADJUST_MAX = 60  # 扩展到60天
BREAKOUT_TOLERANCE = 0.97
START_DATE = '20240101'
END_DATE = '20260620'
MIN_MARKET_CAP = 100

# 获取股票池
print('获取主板100亿以上股票池...')
try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    main_board = sb[sb['ts_code'].str.match(r'^(60|00)')]['ts_code'].tolist()

    mv_data = []
    for i in range(0, min(len(main_board), 500), 500):
        batch = main_board[i:i+500]
        try:
            mv = pro.daily_basic(ts_code=','.join(batch), trade_date='20260620',
                                 fields='ts_code,total_mv')
            if mv is not None and len(mv) > 0:
                mv_data.append(mv)
            time.sleep(0.12)
        except:
            pass

    if mv_data:
        mv_df = pd.concat(mv_data, ignore_index=True)
        mv_df = mv_df[mv_df['total_mv'] >= MIN_MARKET_CAP]
        pool = mv_df['ts_code'].tolist()
        print(f'市值>100亿股票: {len(pool)}只')
    else:
        pool = main_board[:200]
        print(f'使用fallback池: {len(pool)}只')
except Exception as e:
    print(f'获取股票池失败: {e}')
    pool = []
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────
all_signals = []
t0 = time.time()

print(f'\n开始扫描（调整期最长{ADJUST_MAX}天）...')
for idx, code in enumerate(pool):
    if (idx + 1) % 50 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (idx + 1) * (len(pool) - idx - 1)
        print(f'进度 {idx+1}/{len(pool)}  耗时{elapsed:.0f}s  ETA{eta:.0f}s')

    try:
        df = pro.stk_factor_pro(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 60:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 找涨停日
        for i in range(20, len(df) - 10):
            pct_chg = float(df.iloc[i].get('pct_chg', 0))
            if pct_chg < LIMIT_UP_MIN:
                continue

            limit_up_high = float(df.iloc[i]['close'])

            # 向后找突破
            for j in range(i + 2, min(i + ADJUST_MAX + 1, len(df) - 5)):
                close_j = float(df.iloc[j]['close'])
                if close_j < limit_up_high * BREAKOUT_TOLERANCE:
                    continue

                # 检查调整期
                adjust_period = df.iloc[i+1:j]
                if len(adjust_period) == 0:
                    continue

                adjust_max_gain = adjust_period['pct_chg'].max()
                if adjust_max_gain > 7:
                    continue

                # 计算评分
                row = df.iloc[j]
                rsi = float(row.get('rsi_qfq_6', 50))
                kdj_j = float(row.get('kdj_qfq', 50))
                macd_dif = float(row.get('macd_dif_qfq', 0))
                macd_dea = float(row.get('macd_dea_qfq', 0))
                adx = float(row.get('dmi_adx_qfq', 0))
                vol_ratio = float(row.get('volume_ratio', 1.0))

                score = 0
                if rsi < 40: score += 3
                elif rsi < 50: score += 2

                if kdj_j < 20: score += 2
                if macd_dif > macd_dea: score += 2
                if adx > 25: score += 2
                if vol_ratio > 1.2: score += 1

                if rsi > 70: score -= 3
                elif rsi > 60: score -= 1

                # 后续涨幅
                entry = float(row['close'])
                gain_5d = (float(df.iloc[j+5]['close']) - entry) / entry * 100 if j + 5 < len(df) else None
                gain_10d = (float(df.iloc[j+10]['close']) - entry) / entry * 100 if j + 10 < len(df) else None

                all_signals.append({
                    'ts_code': code,
                    'trade_date': df.iloc[j]['trade_date'],
                    'limit_up_date': df.iloc[i]['trade_date'],
                    'entry_price': round(entry, 2),
                    'adjust_days': j - i,
                    'score': score,
                    'rsi': round(rsi, 1),
                    'volume_ratio': round(vol_ratio, 2),
                    'macd_golden': macd_dif > macd_dea,
                    'adx': round(adx, 1),
                    'gain_5d': round(gain_5d, 2) if gain_5d else None,
                    'gain_10d': round(gain_10d, 2) if gain_10d else None,
                })
                break

            if len([s for s in all_signals if s['ts_code'] == code]) >= 5:
                break

        time.sleep(0.12)
    except Exception:
        continue

# ────────────────────────────────────────────────────────────────────
print(f'\n扫描完成！找到 {len(all_signals)} 个信号')

if not all_signals:
    print('未找到信号')
else:
    df = pd.DataFrame(all_signals)
    df['win_5d'] = df['gain_5d'] > 0
    df['win_10d'] = df['gain_10d'] > 0

    print(f'\n{'='*70}')
    print(f'  涨停调整期分层回测结果')
    print(f'{'='*70}')

    # 按调整期分层
    print('\n--- 按调整期分层统计 ---')
    df['adjust_tier'] = pd.cut(df['adjust_days'], bins=[0, 5, 10, 100],
                                labels=['2-5天', '6-10天', '>10天'])

    adjust_stats = df.groupby('adjust_tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_5d=('win_5d', 'mean'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_5d=('gain_5d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
        avg_rsi=('rsi', 'mean'),
    ).reset_index()

    adjust_stats['win_rate_5d'] = (adjust_stats['win_rate_5d'] * 100).round(1)
    adjust_stats['win_rate_10d'] = (adjust_stats['win_rate_10d'] * 100).round(1)
    adjust_stats['avg_gain_5d'] = adjust_stats['avg_gain_5d'].round(2)
    adjust_stats['avg_gain_10d'] = adjust_stats['avg_gain_10d'].round(2)
    adjust_stats['avg_rsi'] = adjust_stats['avg_rsi'].round(1)

    print(adjust_stats.to_string(index=False))

    # 调整期 + RSI交叉分析
    print('\n--- 调整期×RSI分层 ---')
    for tier in ['2-5天', '6-10天', '>10天']:
        tier_df = df[df['adjust_tier'] == tier]
        if len(tier_df) == 0:
            continue

        print(f'\n【{tier}】({len(tier_df)}个信号)')

        rsi_low = tier_df[tier_df['rsi'] < 50]
        rsi_mid = tier_df[(tier_df['rsi'] >= 50) & (tier_df['rsi'] < 70)]
        rsi_high = tier_df[tier_df['rsi'] >= 70]

        if len(rsi_low) > 0:
            print(f'  RSI<50 ({len(rsi_low)}): 5日胜率{rsi_low["win_5d"].mean()*100:.1f}% 均涨{rsi_low["gain_5d"].mean():.2f}%')
        if len(rsi_mid) > 0:
            print(f'  RSI 50-70 ({len(rsi_mid)}): 5日胜率{rsi_mid["win_5d"].mean()*100:.1f}% 均涨{rsi_mid["gain_5d"].mean():.2f}%')
        if len(rsi_high) > 0:
            print(f'  RSI>70 ({len(rsi_high)}): 5日胜率{rsi_high["win_5d"].mean()*100:.1f}% 均涨{rsi_high["gain_5d"].mean():.2f}%')

    # >10天样本详细分析
    print('\n--- 调整>10天样本详情 ---')
    long_adjust = df[df['adjust_days'] > 10].copy()
    if len(long_adjust) > 0:
        long_adjust['adjust_range'] = pd.cut(long_adjust['adjust_days'],
                                              bins=[10, 15, 20, 30, 100],
                                              labels=['11-15天', '16-20天', '21-30天', '>30天'])

        range_stats = long_adjust.groupby('adjust_range', observed=True).agg(
            n=('ts_code', 'count'),
            win_rate_5d=('win_5d', 'mean'),
            avg_gain_5d=('gain_5d', 'mean'),
            avg_rsi=('rsi', 'mean'),
        ).reset_index()

        range_stats['win_rate_5d'] = (range_stats['win_rate_5d'] * 100).round(1)
        range_stats['avg_gain_5d'] = range_stats['avg_gain_5d'].round(2)
        range_stats['avg_rsi'] = range_stats['avg_rsi'].round(1)

        print(range_stats.to_string(index=False))

        # RSI<50的高胜率样本
        low_rsi_long = long_adjust[long_adjust['rsi'] < 50]
        if len(low_rsi_long) > 0:
            print(f'\n调整>10天且RSI<50的样本 ({len(low_rsi_long)}个):')
            print(f'  5日胜率: {low_rsi_long["win_5d"].mean()*100:.1f}%')
            print(f'  均涨: {low_rsi_long["gain_5d"].mean():.2f}%')
            print(f'  平均调整: {low_rsi_long["adjust_days"].mean():.0f}天')

            print('\n  TOP10:')
            for _, r in low_rsi_long.nlargest(10, 'gain_5d').iterrows():
                win = '✅' if r['win_5d'] else '❌'
                print(f"    {r['ts_code']:<12} 调整{r['adjust_days']:>2}天 RSI{r['rsi']:>4.1f} 5日{r['gain_5d']:>6.2f}% {win}")

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = f'{out_dir}\\limit_up_adjust_tiers_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')
