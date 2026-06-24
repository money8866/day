# -*- coding: utf-8 -*-
"""
主板涨停调整后突破策略回测
条件：
  1. 主板股票，市值>100亿
  2. 一个涨停（>9.9%）
  3. 调整后再次突破涨停日高点
  4. 验证多指标共振评分对胜率提升
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
LIMIT_UP_MIN = 9.9  # 涨幅>=9.9%视为涨停
ADJUST_MIN = 2      # 调整至少2天
ADJUST_MAX = 30     # 调整最多30天
BREAKOUT_TOLERANCE = 0.97  # 突破涨停高点的97%即视为突破
START_DATE = '20240101'
END_DATE = '20260620'
MIN_MARKET_CAP = 100  # 市值>100亿

# 获取主板100亿以上股票池
print('获取主板100亿以上股票池...')
try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    # 主板：60开头（沪市）+ 00开头（深市）
    main_board = sb[sb['ts_code'].str.match(r'^(60|00)')]['ts_code'].tolist()

    # 获取市值数据
    print(f'主板股票{len(main_board)}只，筛选市值>100亿...')
    mv_data = []
    for i in range(0, len(main_board), 500):
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
        # fallback
        pool = main_board[:150]
        print(f'使用fallback池: {len(pool)}只')
except Exception as e:
    print(f'获取股票池失败: {e}')
    pool = []
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────
all_signals = []
t0 = time.time()

print(f'\n开始扫描...')
for idx, code in enumerate(pool):
    if (idx + 1) % 30 == 0:
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
            limit_up_vol = float(df.iloc[i].get('vol', 0))

            # 向后找突破
            for j in range(i + ADJUST_MIN, min(i + ADJUST_MAX + 1, len(df) - 5)):
                close_j = float(df.iloc[j]['close'])
                if close_j < limit_up_high * BREAKOUT_TOLERANCE:
                    continue

                # 检查是否为调整期（未继续大涨）
                adjust_period = df.iloc[i+1:j]
                if len(adjust_period) == 0:
                    continue

                # 调整期内最大涨幅应<7%
                adjust_max_gain = adjust_period['pct_chg'].max()
                if adjust_max_gain > 7:
                    continue  # 调整期内继续大涨，不符合

                # ── 计算评分 ──
                row = df.iloc[j]
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
                ma5 = float(row.get('ma_qfq_5', 0))
                ma20 = float(row.get('ma_qfq_20', 0))

                score = 0
                details = []

                # 正向信号
                if rsi < 40: score += 3; details.append(f'RSI={rsi:.0f}<40')
                elif rsi < 50: score += 2; details.append(f'RSI={rsi:.0f}<50')

                if kdj_j < 20: score += 2; details.append(f'KDJ-J={kdj_j:.0f}<20')

                if cci < -100: score += 2; details.append(f'CCI={cci:.0f}<-100')

                if wr > 80: score += 2; details.append(f'WR={wr:.0f}>80')

                if mfi < 30: score += 2; details.append(f'MFI={mfi:.0f}<30')

                if macd_dif > macd_dea: score += 2; details.append('MACD金叉')

                if adx > 25: score += 2; details.append(f'ADX={adx:.0f}>25')

                if vol_ratio > 1.5: score += 2; details.append(f'量比={vol_ratio:.2f}>1.5')
                elif vol_ratio > 1.2: score += 1; details.append(f'量比={vol_ratio:.2f}>1.2')

                if bias1 < -5: score += 2; details.append(f'BIAS={bias1:.1f}%')

                # 突破日成交量 vs 涨停日成交量
                breakthrough_vol = float(row.get('vol', 0))
                if breakthrough_vol > limit_up_vol * 1.2:
                    score += 2; details.append(f'放量突破(+2)')
                elif breakthrough_vol > limit_up_vol:
                    score += 1; details.append(f'温和放量(+1)')

                # MA位置
                close_j = float(row['close'])
                if close_j > ma5 and ma5 > 0:
                    score += 1
                if close_j > ma20 and ma20 > 0:
                    score += 1; details.append('MA20上方')

                # 超买扣分
                if rsi > 70: score -= 3; details.append(f'⚠RSI>70(-3)')
                elif rsi > 60: score -= 1; details.append(f'RSI>60(-1)')

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
                    'breakout_pct': round((entry - limit_up_high) / limit_up_high * 100, 1),
                    'score': score,
                    'score_details': '; '.join(details),
                    'rsi': round(rsi, 1),
                    'volume_ratio': round(vol_ratio, 2),
                    'macd_golden': macd_dif > macd_dea,
                    'adx': round(adx, 1),
                    'gain_5d': round(gain_5d, 2) if gain_5d else None,
                    'gain_10d': round(gain_10d, 2) if gain_10d else None,
                })
                break  # 只记录第一次突破

            # 每只股票最多记录3个信号
            if len([s for s in all_signals if s['ts_code'] == code]) >= 3:
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
    print(f'  主板涨停调整后突破策略回测结果')
    print(f'{'='*70}')
    print(f'\n总体统计 ({len(df)}个信号):')
    print(f'  5日胜率: {df["win_5d"].mean()*100:.1f}%  均涨{df["gain_5d"].mean():.2f}%')
    print(f'  10日胜率: {df["win_10d"].mean()*100:.1f}%  均涨{df["gain_10d"].mean():.2f}%')

    # 按评分分层
    print('\n--- 评分分层 ---')
    df['tier'] = pd.cut(df['score'], bins=[-100, 0, 4, 8, 100],
                        labels=['负分', '0-4分', '5-8分', '9+分'])
    tier_stats = df.groupby('tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_5d=('win_5d', 'mean'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_5d=('gain_5d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
    ).reset_index()
    tier_stats['win_rate_5d'] = (tier_stats['win_rate_5d'] * 100).round(1)
    tier_stats['win_rate_10d'] = (tier_stats['win_rate_10d'] * 100).round(1)
    tier_stats['avg_gain_5d'] = tier_stats['avg_gain_5d'].round(2)
    tier_stats['avg_gain_10d'] = tier_stats['avg_gain_10d'].round(2)
    print(tier_stats.to_string(index=False))

    # RSI分层
    print('\n--- RSI分层 ---')
    rsi_low = df[df['rsi'] < 50]
    rsi_mid = df[(df['rsi'] >= 50) & (df['rsi'] < 70)]
    rsi_high = df[df['rsi'] >= 70]

    if len(rsi_low) > 0:
        print(f'RSI<50 ({len(rsi_low)}): 5日胜率{rsi_low["win_5d"].mean()*100:.1f}% 均涨{rsi_low["gain_5d"].mean():.2f}%')
    if len(rsi_mid) > 0:
        print(f'RSI 50-70 ({len(rsi_mid)}): 5日胜率{rsi_mid["win_5d"].mean()*100:.1f}% 均涨{rsi_mid["gain_5d"].mean():.2f}%')
    if len(rsi_high) > 0:
        print(f'RSI>70 ({len(rsi_high)}): 5日胜率{rsi_high["win_5d"].mean()*100:.1f}% 均涨{rsi_high["gain_5d"].mean():.2f}%')

    # 指标组合
    print('\n--- 指标组合胜率 ---')
    combo1 = df[(df['macd_golden']) & (df['adx'] > 25)]
    if len(combo1) >= 5:
        print(f'MACD金叉+ADX>25 ({len(combo1)}): 5日胜率{combo1["win_5d"].mean()*100:.1f}%')

    combo2 = df[(df['macd_golden']) & (df['volume_ratio'] > 1.2)]
    if len(combo2) >= 5:
        print(f'MACD金叉+量比>1.2 ({len(combo2)}): 5日胜率{combo2["win_5d"].mean()*100:.1f}%')

    combo3 = df[df['score'] >= 8]
    if len(combo3) >= 5:
        print(f'评分>=8分 ({len(combo3)}): 5日胜率{combo3["win_5d"].mean()*100:.1f}% 10日胜率{combo3["win_10d"].mean()*100:.1f}%')

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = f'{out_dir}\\limit_up_breakout_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')

    # TOP20
    print('\n--- TOP20信号（按评分排序）---')
    top20 = df.sort_values('score', ascending=False).head(20)
    for _, r in top20.iterrows():
        win = '✅' if r['win_5d'] else '❌'
        print(f"{r['ts_code']:<12} 涨停{r['limit_up_date']} 突破{r['trade_date']} 调整{r['adjust_days']:>2}天 "
              f"评分{r['score']:>2}分 RSI{r['rsi']:>3.0f} 5日{r['gain_5d']:>6.2f}% {win}")
