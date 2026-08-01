"""多策略买点对比：追高 vs 回踩当日买 vs 回踩次日确认买 vs 回踩日低点买"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
import pandas as pd
import numpy as np
import time

spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

sig_df = pd.read_excel(r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx")
tq.TRADE_DATE = "20260801"


def test_buy_strategies(df, signal_idx):
    """对一个信号测试多种买点策略
    
    返回各策略的买入信号和T+5表现
    """
    close_arr = df['close'].values.astype(float)
    open_arr = df['open'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    vol_arr = df['vol'].values.astype(float)
    
    ma5_arr = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10_arr = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    ma20_arr = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    
    signal_close = close_arr[signal_idx]
    signal_vol = vol_arr[signal_idx]
    results = {}
    
    # ========== 策略1：追高（信号日收盘买）==========
    if signal_idx + 5 < len(df):
        buy_p = signal_close
        t5c = close_arr[signal_idx + 5]
        wh = max(high_arr[signal_idx+1:signal_idx+6])
        wl = min(low_arr[signal_idx+1:signal_idx+6])
        results['chase_close'] = {
            'buy_date': str(df.iloc[signal_idx]['trade_date']),
            'buy_price': round(buy_p, 2),
            't5_chg': round((t5c/buy_p-1)*100, 2),
            't5_maxup': round((wh/buy_p-1)*100, 2),
            't5_maxdd': round((wl/buy_p-1)*100, 2),
        }
    
    # ========== 策略2：T+1低开/回踩时买（开盘价低于信号日收盘1%以上）==========
    for offset in range(1, 4):
        idx = signal_idx + offset
        if idx + 5 >= len(df):
            continue
        o = open_arr[idx]
        # 开盘回踩：开盘价比信号日收盘低0.5%以上
        if o < signal_close * 0.995:
            ma5 = ma5_arr[idx]
            ma10 = ma10_arr[idx]
            l = low_arr[idx]
            c = close_arr[idx]
            v = vol_arr[idx]
            # 开盘接近MA5或MA10（或低于MA5但不低于MA10*0.98）
            near_ma_support = (o <= ma5 * 1.01 and o >= ma10 * 0.98) or (l <= ma5 * 1.01 and l >= ma10 * 0.97)
            if near_ma_support:
                buy_p = o  # 开盘买
                t5c = close_arr[idx + 5]
                wh = max(high_arr[idx+1:idx+6])
                wl = min(low_arr[idx+1:idx+6])
                results[f'pullback_open_T{offset}'] = {
                    'buy_date': str(df.iloc[idx]['trade_date']),
                    'buy_price': round(buy_p, 2),
                    'buy_offset': offset,
                    'open_gap': round((o/signal_close-1)*100, 2),
                    't5_chg': round((t5c/buy_p-1)*100, 2),
                    't5_maxup': round((wh/buy_p-1)*100, 2),
                    't5_maxdd': round((wl/buy_p-1)*100, 2),
                }
                break  # 取最早的
    
    # ========== 策略3：回踩日收盘买（最低价触及MA5/MA10+缩量+小K线）==========
    for offset in range(1, 4):
        idx = signal_idx + offset
        if idx + 5 >= len(df):
            continue
        c = close_arr[idx]
        l = low_arr[idx]
        v = vol_arr[idx]
        pct = (c / close_arr[idx-1] - 1) * 100
        ma5 = ma5_arr[idx]
        ma10 = ma10_arr[idx]
        ma20 = ma20_arr[idx]
        
        vol_shrink = v < signal_vol * 0.7
        touch_ma5 = l <= ma5 * 1.02 and l >= ma5 * 0.97
        touch_ma10 = l <= ma10 * 1.03 and l >= ma10 * 0.96
        near_ma = touch_ma5 or touch_ma10
        not_broken = l > ma20 * 0.98
        small_candle = -3.5 <= pct <= 2.5
        
        if vol_shrink and near_ma and not_broken and small_candle:
            buy_p = c
            t5c = close_arr[idx + 5]
            wh = max(high_arr[idx+1:idx+6])
            wl = min(low_arr[idx+1:idx+6])
            results[f'pullback_close_T{offset}'] = {
                'buy_date': str(df.iloc[idx]['trade_date']),
                'buy_price': round(buy_p, 2),
                'buy_offset': offset,
                'touch_ma': 'MA5' if touch_ma5 else 'MA10',
                'pct': round(pct, 2),
                'vol_ratio': round(v/signal_vol, 2),
                't5_chg': round((t5c/buy_p-1)*100, 2),
                't5_maxup': round((wh/buy_p-1)*100, 2),
                't5_maxdd': round((wl/buy_p-1)*100, 2),
            }
            break  # 取最早的
    
    # ========== 策略4：回踩+次日确认（回踩日后一天收阳站上MA5）==========
    for offset in range(1, 4):
        pb_idx = signal_idx + offset
        confirm_idx = pb_idx + 1
        if confirm_idx + 5 >= len(df):
            continue
        
        # 回踩日条件
        l_pb = low_arr[pb_idx]
        v_pb = vol_arr[pb_idx]
        c_pb = close_arr[pb_idx]
        pct_pb = (c_pb / close_arr[pb_idx-1] - 1) * 100
        ma5_pb = ma5_arr[pb_idx]
        ma10_pb = ma10_arr[pb_idx]
        ma20_pb = ma20_arr[pb_idx]
        
        vol_shrink = v_pb < signal_vol * 0.7
        touch_ma = (l_pb <= ma5_pb * 1.02 and l_pb >= ma5_pb * 0.97) or \
                   (l_pb <= ma10_pb * 1.03 and l_pb >= ma10_pb * 0.96)
        not_broken = l_pb > ma20_pb * 0.98
        small_candle = -3.5 <= pct_pb <= 2.5
        
        if not (vol_shrink and touch_ma and not_broken and small_candle):
            continue
        
        # 确认日条件
        c_cf = close_arr[confirm_idx]
        o_cf = open_arr[confirm_idx]
        pct_cf = (c_cf / close_arr[confirm_idx-1] - 1) * 100
        ma5_cf = ma5_arr[confirm_idx]
        v_cf = vol_arr[confirm_idx]
        
        # 确认：收阳线（涨幅>0.5%）、收盘站上MA5、不是继续缩量
        confirm_yang = pct_cf >= 0.5
        confirm_above_ma5 = c_cf >= ma5_cf * 0.99
        confirm_vol = v_cf >= v_pb * 0.8  # 成交量不再大幅萎缩
        
        if confirm_yang and confirm_above_ma5:
            buy_p = c_cf
            t5c = close_arr[confirm_idx + 5]
            wh = max(high_arr[confirm_idx+1:confirm_idx+6])
            wl = min(low_arr[confirm_idx+1:confirm_idx+6])
            results[f'confirm_T{offset}+1'] = {
                'buy_date': str(df.iloc[confirm_idx]['trade_date']),
                'buy_price': round(buy_p, 2),
                'buy_offset': f'T{offset}+1',
                'pct_confirm': round(pct_cf, 2),
                'vol_confirm': round(v_cf/v_pb, 2),
                't5_chg': round((t5c/buy_p-1)*100, 2),
                't5_maxup': round((wh/buy_p-1)*100, 2),
                't5_maxdd': round((wl/buy_p-1)*100, 2),
            }
            break
    
    return results


# 测试所有信号
print("正在回测多种买点策略...")
t0 = time.time()

strategy_results = {}

for i, row in sig_df.iterrows():
    code = row['code']
    date = str(row['date'])
    
    try:
        df = tq.get_hist_data(code)
        if df is None or len(df) < 80:
            continue
        df = df.reset_index(drop=True)
        df['trade_date'] = df['trade_date'].astype(str)
        
        mask = df['trade_date'] == date
        if not mask.any():
            continue
        idx = df.index[mask][0]
        
        res = test_buy_strategies(df, idx)
        res['code'] = code
        res['name'] = row['name']
        res['signal_date'] = date
        res['signal_score'] = row['score']
        res['month'] = date[:6]
        
        for k, v in res.items():
            if k in ('code','name','signal_date','signal_score','month'):
                continue
            if k not in strategy_results:
                strategy_results[k] = []
            v2 = dict(v)
            v2['code'] = code
            v2['name'] = row['name']
            v2['signal_date'] = date
            v2['signal_score'] = row['score']
            v2['month'] = date[:6]
            strategy_results[k].append(v2)
    
    except Exception as e:
        pass

elapsed = time.time() - t0
print(f"回测完成，耗时{elapsed:.1f}秒\n")

# 统计对比
print("=" * 80)
print("【多策略买点对比 - T+5表现】")
print("=" * 80)

strategy_names = {
    'chase_close': '追高(信号日收盘买)',
    'pullback_open_T1': 'T+1开盘回踩买',
    'pullback_open_T2': 'T+2开盘回踩买',
    'pullback_open_T3': 'T+3开盘回踩买',
    'pullback_close_T1': 'T+1回踩收盘买',
    'pullback_close_T2': 'T+2回踩收盘买',
    'pullback_close_T3': 'T+3回踩收盘买',
    'confirm_T1+1': 'T+1回踩+T+2确认买',
    'confirm_T2+1': 'T+2回踩+T+3确认买',
    'confirm_T3+1': 'T+3回踩+T+4确认买',
}

# 合并所有pullback_open
all_pullback_open = []
all_pullback_close = []
all_confirm = []

for sname, sdesc in strategy_names.items():
    if sname not in strategy_results:
        continue
    data = strategy_results[sname]
    df_s = pd.DataFrame(data)
    
    # 排除未到期
    df_s = df_s[df_s['t5_chg'].notna()]
    if len(df_s) == 0:
        continue
    
    n = len(df_s)
    win3 = int((df_s['t5_maxup'] >= 3).sum())
    win_final = int((df_s['t5_chg'] >= 3).sum())
    profit = int((df_s['t5_chg'] > 0).sum())
    avg = df_s['t5_chg'].mean()
    avg_maxup = df_s['t5_maxup'].mean()
    avg_maxdd = df_s['t5_maxdd'].mean()
    
    # 6月和7月分开
    jun = df_s[df_s['month'] == '202606']
    jul = df_s[df_s['month'] == '202607']
    jun_wr = int((jun['t5_maxup'] >= 3).sum()) / len(jun) * 100 if len(jun) > 0 else 0
    jul_wr = int((jul['t5_maxup'] >= 3).sum()) / len(jul) * 100 if len(jul) > 0 else 0
    
    print(f"\n【{sdesc}】n={n}")
    print(f"  动态止盈(涨≥3%): {win3}/{n} = {win3/n*100:.1f}%")
    print(f"  最终涨≥3%: {win_final}/{n} = {win_final/n*100:.1f}%")
    print(f"  T+5盈利: {profit}/{n} = {profit/n*100:.1f}%")
    print(f"  均收益: {avg:+.2f}% | 均最大涨: {avg_maxup:+.2f}% | 均最大撤: {avg_maxdd:+.2f}%")
    print(f"  6月止盈: {jun_wr:.0f}%({len(jun)}只) | 7月止盈: {jul_wr:.0f}%({len(jul)}只)")
    
    if 'pullback_open' in sname:
        all_pullback_open.extend(data)
    elif 'pullback_close' in sname:
        all_pullback_close.extend(data)
    elif 'confirm' in sname:
        all_confirm.extend(data)

# 综合对比
print("\n" + "=" * 80)
print("【综合对比】")
print("=" * 80)

for name, combined in [
    ('所有回踩开盘买(T+1~T+3)', all_pullback_open),
    ('所有回踩收盘买(T+1~T+3)', all_pullback_close),
    ('所有回踩+次日确认买', all_confirm),
]:
    if not combined:
        continue
    df_c = pd.DataFrame(combined)
    df_c = df_c[df_c['t5_chg'].notna()]
    n = len(df_c)
    win3 = int((df_c['t5_maxup'] >= 3).sum())
    win_final = int((df_c['t5_chg'] >= 3).sum())
    profit = int((df_c['t5_chg'] > 0).sum())
    avg = df_c['t5_chg'].mean()
    avg_maxup = df_c['t5_maxup'].mean()
    avg_maxdd = df_c['t5_maxdd'].mean()
    
    jun = df_c[df_c['month'] == '202606']
    jul = df_c[df_c['month'] == '202607']
    jun_wr = int((jun['t5_maxup'] >= 3).sum()) / len(jun) * 100 if len(jun) > 0 else 0
    jul_wr = int((jul['t5_maxup'] >= 3).sum()) / len(jul) * 100 if len(jul) > 0 else 0
    
    print(f"\n【{name}】n={n}")
    print(f"  动态止盈: {win3/n*100:.1f}% | 最终胜率: {win_final/n*100:.1f}% | 盈利率: {profit/n*100:.1f}%")
    print(f"  均收益: {avg:+.2f}% | 均最大涨: {avg_maxup:+.2f}% | 均最大撤: {avg_maxdd:+.2f}%")
    print(f"  6月: {jun_wr:.0f}%止盈({len(jun)}只, 均收益{jun['t5_chg'].mean():+.2f}%) | 7月: {jul_wr:.0f}%止盈({len(jul)}只, 均收益{jul['t5_chg'].mean():+.2f}%)")

# 追高基准
chase = strategy_results.get('chase_close', [])
if chase:
    df_ch = pd.DataFrame(chase)
    n = len(df_ch)
    print(f"\n【基准：追高(信号日收盘)】n={n}")
    print(f"  动态止盈: {int((df_ch['t5_maxup']>=3).sum())/n*100:.1f}% | 均收益: {df_ch['t5_chg'].mean():+.2f}%")
