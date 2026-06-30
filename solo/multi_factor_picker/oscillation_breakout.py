# -*- coding: utf-8 -*-
"""
震荡突破规律验证
条件：
  1. 震荡幅度>30%
  2. 震荡周期>30天
  3. 突破前缩量洗盘
  4. 突破日放量+刚突破前高
验证：突破后20日涨幅分布与胜率
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

# 参数（基于烽火通信案例）
OSCILLATION_MIN = 0.30      # 震荡幅度>30%
OSCILLATION_DAYS_MIN = 30   # 震荡周期>30天
BREAKOUT_VOL_MIN = 1.3      # 突破日量比>1.3
BREAKOUT_TOLERANCE = 0.03   # 突破幅度<3%（刚突破）
WASH_DAYS = 5               # 洗盘天数（突破前5天）
WASH_VOL_MAX = 1.0          # 洗盘量比<1.0

START_DATE = '20240101'
END_DATE = '20260620'

# 获取股票池
print('获取股票池...')
try:
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
    # 主板 + 双创板各100只
    main = sb[sb['ts_code'].str.match(r'^(60|00)')]['ts_code'].tolist()[:100]
    cy = sb[sb['ts_code'].str.startswith(('300', '688'))]['ts_code'].tolist()[:100]
    pool = main + cy
    print(f'股票池: {len(pool)}只')
except Exception as e:
    print(f'获取股票池失败: {e}')
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────
all_signals = []
t0 = time.time()

print(f'\n开始扫描震荡突破...')
for idx, code in enumerate(pool):
    if (idx + 1) % 30 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (idx + 1) * (len(pool) - idx - 1)
        print(f'进度 {idx+1}/{len(pool)}  找到{len(all_signals)}个信号  ETA{eta:.0f}s')

    try:
        df = pro.stk_factor_pro(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is None or len(df) < 150:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values
        vols = df['vol'].values if 'vol' in df.columns else None
        n = len(df)

        # 遍历寻找震荡突破
        for i in range(OSCILLATION_DAYS_MIN + 20, n - 20):
            # 回看震荡区间
            window_start = max(0, i - OSCILLATION_DAYS_MIN - 30)
            window = df.iloc[window_start:i]

            if len(window) < OSCILLATION_DAYS_MIN:
                continue

            # 找震荡高点和低点
            high_idx = window['close'].idxmax()
            low_idx = window['close'].idxmin()
            high = window.loc[high_idx, 'close']
            low = window.loc[low_idx, 'close']

            # 震荡幅度
            oscillation_pct = (high - low) / low
            if oscillation_pct < OSCILLATION_MIN:
                continue

            # 震荡天数（高点到当前）
            oscillation_days = i - window_start

            # 当前价格
            current_close = closes[i]

            # 突破确认：当前价格接近或超过震荡高点
            if current_close < high * 0.97:  # 未突破
                continue

            breakout_pct = (current_close - high) / high

            # 突破幅度控制（刚突破）
            if breakout_pct > BREAKOUT_TOLERANCE:
                continue  # 已经突破太多，非刚突破

            # 突破日量比
            breakout_vol_ratio = float(df.iloc[i].get('volume_ratio', 1.0))
            if breakout_vol_ratio < BREAKOUT_VOL_MIN:
                continue

            # 检查突破前是否有缩量洗盘
            wash_window = df.iloc[max(0, i-WASH_DAYS):i]
            if len(wash_window) < WASH_DAYS - 1:
                continue

            # 洗盘期间至少有1天量比<1.0
            wash_vol_ratios = [float(wash_window.iloc[j].get('volume_ratio', 1.0))
                              for j in range(len(wash_window))]
            has_wash = any(vr < WASH_VOL_MAX for vr in wash_vol_ratios)

            if not has_wash:
                continue

            # 计算后续涨幅
            entry = current_close
            gain_5d = (closes[i+5] - entry) / entry * 100 if i + 5 < n else None
            gain_10d = (closes[i+10] - entry) / entry * 100 if i + 10 < n else None
            gain_20d = (closes[i+20] - entry) / entry * 100 if i + 20 < n else None

            # 最大涨幅
            if i + 20 < n:
                future_high = closes[i:i+20].max()
                max_gain = (future_high - entry) / entry * 100
            else:
                max_gain = None

            # 突破日指标
            rsi = float(df.iloc[i].get('rsi_qfq_6', 50))
            macd_dif = float(df.iloc[i].get('macd_dif_qfq', 0))
            macd_dea = float(df.iloc[i].get('macd_dea_qfq', 0))

            all_signals.append({
                'ts_code': code,
                'trade_date': df.iloc[i]['trade_date'],
                'oscillation_pct': round(oscillation_pct * 100, 1),
                'oscillation_days': oscillation_days,
                'breakout_pct': round(breakout_pct * 100, 2),
                'breakout_vol_ratio': round(breakout_vol_ratio, 2),
                'entry_price': round(entry, 2),
                'rsi': round(rsi, 1),
                'macd_golden': macd_dif > macd_dea,
                'gain_5d': round(gain_5d, 2) if gain_5d else None,
                'gain_10d': round(gain_10d, 2) if gain_10d else None,
                'gain_20d': round(gain_20d, 2) if gain_20d else None,
                'max_gain': round(max_gain, 2) if max_gain else None,
            })

            # 每只股票最多记录5个信号
            if len([s for s in all_signals if s['ts_code'] == code]) >= 5:
                break

        time.sleep(0.12)
    except Exception:
        continue

# ────────────────────────────────────────────────────────────────────
print(f'\n扫描完成！找到 {len(all_signals)} 个震荡突破信号')

if not all_signals:
    print('未找到信号')
else:
    df = pd.DataFrame(all_signals)
    df['win_5d'] = df['gain_5d'] > 0
    df['win_10d'] = df['gain_10d'] > 0
    df['win_20d'] = df['gain_20d'] > 0

    print(f'\n{'='*70}')
    print(f'  震荡突破规律验证结果')
    print(f'{'='*70}')

    print(f'\n总体统计 ({len(df)}个信号):')
    print(f'  5日胜率: {df["win_5d"].mean()*100:.1f}%  均涨{df["gain_5d"].mean():.2f}%')
    print(f'  10日胜率: {df["win_10d"].mean()*100:.1f}%  均涨{df["gain_10d"].mean():.2f}%')
    print(f'  20日胜率: {df["win_20d"].mean()*100:.1f}%  均涨{df["gain_20d"].mean():.2f}%')
    print(f'  最大涨幅均值: {df["max_gain"].mean():.2f}%')

    # 按震荡幅度分层
    print('\n--- 按震荡幅度分层 ---')
    df['osc_tier'] = pd.cut(df['oscillation_pct'], bins=[0, 40, 50, 100],
                             labels=['30-40%', '40-50%', '>50%'])
    osc_stats = df.groupby('osc_tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
        avg_max_gain=('max_gain', 'mean'),
    ).reset_index()
    osc_stats['win_rate_10d'] = (osc_stats['win_rate_10d'] * 100).round(1)
    osc_stats['avg_gain_10d'] = osc_stats['avg_gain_10d'].round(2)
    osc_stats['avg_max_gain'] = osc_stats['avg_max_gain'].round(2)
    print(osc_stats.to_string(index=False))

    # 按震荡天数分层
    print('\n--- 按震荡天数分层 ---')
    df['days_tier'] = pd.cut(df['oscillation_days'], bins=[0, 40, 60, 100],
                              labels=['30-40天', '40-60天', '>60天'])
    days_stats = df.groupby('days_tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
    ).reset_index()
    days_stats['win_rate_10d'] = (days_stats['win_rate_10d'] * 100).round(1)
    days_stats['avg_gain_10d'] = days_stats['avg_gain_10d'].round(2)
    print(days_stats.to_string(index=False))

    # 按RSI分层
    print('\n--- 按突破日RSI分层 ---')
    df['rsi_tier'] = pd.cut(df['rsi'], bins=[0, 60, 70, 100],
                             labels=['<60', '60-70', '>70'])
    rsi_stats = df.groupby('rsi_tier', observed=True).agg(
        n=('ts_code', 'count'),
        win_rate_10d=('win_10d', 'mean'),
        avg_gain_10d=('gain_10d', 'mean'),
    ).reset_index()
    rsi_stats['win_rate_10d'] = (rsi_stats['win_rate_10d'] * 100).round(1)
    rsi_stats['avg_gain_10d'] = rsi_stats['avg_gain_10d'].round(2)
    print(rsi_stats.to_string(index=False))

    # MACD金叉效果
    print('\n--- MACD金叉效果 ---')
    macd_yes = df[df['macd_golden']]
    macd_no = df[~df['macd_golden']]
    if len(macd_yes) > 0:
        print(f'MACD金叉 ({len(macd_yes)}): 10日胜率{macd_yes["win_10d"].mean()*100:.1f}% 均涨{macd_yes["gain_10d"].mean():.2f}%')
    if len(macd_no) > 0:
        print(f'MACD死叉 ({len(macd_no)}): 10日胜率{macd_no["win_10d"].mean()*100:.1f}% 均涨{macd_no["gain_10d"].mean():.2f}%')

    # 组合分析
    print('\n--- 组合条件效果 ---')

    # 震荡幅度>40% + 震荡天数>40 + RSI<70
    combo1 = df[(df['oscillation_pct'] >= 40) & (df['oscillation_days'] >= 40) & (df['rsi'] < 70)]
    if len(combo1) > 0:
        print(f'\n震荡>40% + 天数>40 + RSI<70 ({len(combo1)}个):')
        print(f'  10日胜率: {combo1["win_10d"].mean()*100:.1f}%')
        print(f'  均涨: {combo1["gain_10d"].mean():.2f}%')
        print(f'  最大涨幅: {combo1["max_gain"].mean():.2f}%')

    # 震荡幅度>40% + MACD金叉
    combo2 = df[(df['oscillation_pct'] >= 40) & (df['macd_golden'])]
    if len(combo2) > 0:
        print(f'\n震荡>40% + MACD金叉 ({len(combo2)}个):')
        print(f'  10日胜率: {combo2["win_10d"].mean()*100:.1f}%')
        print(f'  均涨: {combo2["gain_10d"].mean():.2f}%')

    # 保存
    out_dir = r'D:\mystock\solo\multi_factor_picker\output'
    ts_str = datetime.datetime.now().strftime('%H%M%S')
    csv_path = f'{out_dir}\\oscillation_breakout_{ts_str}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\n已保存: {csv_path}')

    # TOP20案例
    print('\n--- TOP20成功案例 ---')
    top20 = df.nlargest(20, 'gain_10d')
    for _, r in top20.iterrows():
        win = '✅' if r['win_10d'] else '❌'
        print(f"{r['ts_code']:<12} {r['trade_date']} 震荡{r['oscillation_pct']:>4.0f}%/{r['oscillation_days']:>2}天 "
              f"突破{r['breakout_pct']:>4.1f}% RSI{r['rsi']:>3.0f} 10日{r['gain_10d']:>6.2f}% {win}")
