"""回测对比：追高买点 vs 回踩低吸买点 - 修复数据索引bug"""
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
print(f"月分析信号数: {len(sig_df)}")


def find_pullback_buy_point(df, signal_idx):
    """在信号日(signal_idx)后T+1~T+3寻找回踩低吸买点
    df已经是升序排列且reset_index，iloc[0]=最旧，iloc[-1]=最新
    """
    close_arr = df['close'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    vol_arr = df['vol'].values.astype(float)
    
    ma5_arr = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10_arr = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    ma20_arr = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    
    signal_vol = vol_arr[signal_idx]
    
    best_buy = None
    
    for offset in range(1, 4):
        idx = signal_idx + offset
        if idx >= len(df):
            break
        
        c = close_arr[idx]
        l = low_arr[idx]
        v = vol_arr[idx]
        pct = (c / close_arr[idx-1] - 1) * 100
        
        ma5 = ma5_arr[idx]
        ma10 = ma10_arr[idx]
        ma20 = ma20_arr[idx]
        
        # 条件1：缩量
        vol_shrink = v < signal_vol * 0.7
        
        # 条件2：回踩到MA5或MA10附近（最低价触及均线）
        touch_ma5 = (l <= ma5 * 1.02) and (l >= ma5 * 0.97)
        touch_ma10 = (l <= ma10 * 1.03) and (l >= ma10 * 0.96)
        near_ma = touch_ma5 or touch_ma10
        
        # 条件3：不跌破MA20
        not_broken_ma20 = l > ma20 * 0.98
        
        # 条件4：小阴小阳/十字星（不能是大阳线追高，不能是大阴线破位）
        small_candle = -3.5 <= pct <= 2.5
        
        # 条件5：收盘站稳
        close_support = c >= ma5 * 0.98 or c >= ma10 * 0.98
        
        if vol_shrink and near_ma and not_broken_ma20 and small_candle and close_support:
            buy_price = c
            
            t3_chg = None
            t5_chg = None
            t5_maxup = None
            t5_maxdd = None
            
            if idx + 3 < len(df):
                t3_chg = (close_arr[idx + 3] / buy_price - 1) * 100
            if idx + 5 < len(df):
                t5_chg = (close_arr[idx + 5] / buy_price - 1) * 100
                window_high = max(high_arr[idx+1:idx+6])
                window_low = min(low_arr[idx+1:idx+6])
                t5_maxup = (window_high / buy_price - 1) * 100
                t5_maxdd = (window_low / buy_price - 1) * 100
            
            ma_level = 'MA5' if touch_ma5 else 'MA10'
            
            result = {
                'buy_offset': offset,
                'buy_date': str(df.iloc[idx]['trade_date']),
                'buy_price': round(buy_price, 2),
                'pct_on_buy_day': round(pct, 2),
                'vol_ratio_vs_signal': round(v / signal_vol, 2),
                'touch_ma': ma_level,
                'dist_ma5': round((c / ma5 - 1) * 100, 2),
                'dist_ma10': round((c / ma10 - 1) * 100, 2),
                't3_chg': round(t3_chg, 2) if t3_chg is not None else None,
                't5_chg': round(t5_chg, 2) if t5_chg is not None else None,
                't5_maxup': round(t5_maxup, 2) if t5_maxup is not None else None,
                't5_maxdd': round(t5_maxdd, 2) if t5_maxdd is not None else None,
            }
            
            if best_buy is None or offset < best_buy['buy_offset']:
                best_buy = result
    
    return best_buy


print("正在分析每个信号的回踩买点...")
t0 = time.time()
pullback_results = []
chase_results = []

for i, row in sig_df.iterrows():
    code = row['code']
    date = str(row['date'])
    
    try:
        df = tq.get_hist_data(code)
        if df is None or len(df) < 80:
            continue
        
        # 关键修复：reset_index确保位置索引正确
        df = df.reset_index(drop=True)
        df['trade_date'] = df['trade_date'].astype(str)
        
        # 找到信号日的位置索引（升序排列，iloc[0]=旧）
        mask = df['trade_date'] == date
        if not mask.any():
            continue
        idx = df.index[mask][0]  # 这是正确的位置索引
        
        close_arr = df['close'].values.astype(float)
        high_arr = df['high'].values.astype(float)
        signal_close = close_arr[idx]
        
        # 追高买点
        t5_chg_chase = None
        t5_maxup_chase = None
        if idx + 5 < len(df):
            t5_chg_chase = (close_arr[idx + 5] / signal_close - 1) * 100
            window_high = max(high_arr[idx+1:idx+6])
            t5_maxup_chase = (window_high / signal_close - 1) * 100
        
        chase_results.append({
            'code': code, 'name': row['name'], 'date': date,
            'score': row['score'],
            't5_chg': round(t5_chg_chase, 2) if t5_chg_chase is not None else None,
            't5_maxup': round(t5_maxup_chase, 2) if t5_maxup_chase is not None else None,
        })
        
        # 回踩买点
        pb = find_pullback_buy_point(df, idx)
        if pb:
            pb['code'] = code
            pb['name'] = row['name']
            pb['signal_date'] = date
            pb['signal_score'] = row['score']
            pullback_results.append(pb)
    
    except Exception as e:
        pass

elapsed = time.time() - t0
print(f"分析完成，耗时{elapsed:.1f}秒")
print(f"追高买点(信号日): {len(chase_results)}个")
print(f"回踩买点(T+1~T+3缩量回踩): {len(pullback_results)}个")

# 统计对比
print("\n" + "=" * 70)
print("【追高买点 vs 回踩低吸买点 - T+5胜率对比】")
print("=" * 70)

chase_valid = [r for r in chase_results if r['t5_chg'] is not None]
if chase_valid:
    chase_df = pd.DataFrame(chase_valid)
    chase_win3 = int((chase_df['t5_maxup'] >= 3).sum())
    chase_win = int((chase_df['t5_chg'] >= 3).sum())
    chase_profit = int((chase_df['t5_chg'] > 0).sum())
    print(f"\n【追高买点】信号日收盘买入 (n={len(chase_valid)})")
    print(f"  最终涨≥3%胜率: {chase_win}/{len(chase_valid)} = {chase_win/len(chase_valid)*100:.1f}%")
    print(f"  动态止盈(最大涨≥3%): {chase_win3}/{len(chase_valid)} = {chase_win3/len(chase_valid)*100:.1f}%")
    print(f"  T+5盈利概率: {chase_profit}/{len(chase_valid)} = {chase_profit/len(chase_valid)*100:.1f}%")
    print(f"  平均T+5收益: {chase_df['t5_chg'].mean():+.2f}%")
    print(f"  平均最大涨幅: {chase_df['t5_maxup'].mean():+.2f}%")

pb_valid = [r for r in pullback_results if r['t5_chg'] is not None]
if pb_valid:
    pb_df = pd.DataFrame(pb_valid)
    pb_win3 = int((pb_df['t5_maxup'] >= 3).sum())
    pb_win = int((pb_df['t5_chg'] >= 3).sum())
    pb_profit = int((pb_df['t5_chg'] > 0).sum())
    print(f"\n【回踩低吸买点】T+1~T+3缩量回踩MA买入 (n={len(pb_valid)})")
    print(f"  最终涨≥3%胜率: {pb_win}/{len(pb_valid)} = {pb_win/len(pb_valid)*100:.1f}%")
    print(f"  动态止盈(最大涨≥3%): {pb_win3}/{len(pb_valid)} = {pb_win3/len(pb_valid)*100:.1f}%")
    print(f"  T+5盈利概率: {pb_profit}/{len(pb_valid)} = {pb_profit/len(pb_valid)*100:.1f}%")
    print(f"  平均T+5收益: {pb_df['t5_chg'].mean():+.2f}%")
    print(f"  平均最大涨幅: {pb_df['t5_maxup'].mean():+.2f}%")
    print(f"  平均最大回撤: {pb_df['t5_maxdd'].mean():+.2f}%")
    
    print(f"\n--- 按回踩均线分组 ---")
    for ma in ['MA5', 'MA10']:
        sub = pb_df[pb_df['touch_ma'] == ma]
        if len(sub) > 0:
            wr = int((sub['t5_maxup'] >= 3).sum()) / len(sub) * 100
            avg = sub['t5_chg'].mean()
            win_final = int((sub['t5_chg'] >= 3).sum())
            profit_n = int((sub['t5_chg'] > 0).sum())
            print(f"  回踩{ma}: {len(sub)}只, 止盈={wr:.1f}%(涨≥3%:{win_final}只, 盈利:{profit_n}只), 均收益={avg:+.2f}%")
    
    print(f"\n--- 按回踩天数分组 ---")
    for d in [1, 2, 3]:
        sub = pb_df[pb_df['buy_offset'] == d]
        if len(sub) > 0:
            wr = int((sub['t5_maxup'] >= 3).sum()) / len(sub) * 100
            avg = sub['t5_chg'].mean()
            print(f"  T+{d}回踩: {len(sub)}只, 止盈={wr:.1f}%, 均收益={avg:+.2f}%")
    
    print(f"\n--- 按评分分组 ---")
    for lo, hi in [(75,80),(80,85),(85,100)]:
        sub = pb_df[(pb_df['signal_score'] >= lo) & (pb_df['signal_score'] < hi)]
        if len(sub) > 0:
            wr = int((sub['t5_maxup'] >= 3).sum()) / len(sub) * 100
            avg = sub['t5_chg'].mean()
            print(f"  评分[{lo}-{hi}): {len(sub)}只, 止盈={wr:.1f}%, 均收益={avg:+.2f}%")
    
    print(f"\n--- 按月份分组 ---")
    for month in ['202606', '202607']:
        sub = pb_df[pb_df['signal_date'].str.startswith(month)]
        if len(sub) > 0:
            wr = int((sub['t5_maxup'] >= 3).sum()) / len(sub) * 100
            avg = sub['t5_chg'].mean()
            print(f"  {month[:4]}年{month[4:]}月: {len(sub)}只, 止盈={wr:.1f}%, 均收益={avg:+.2f}%")
    
    # 明细
    print(f"\n--- 回踩买点明细（按信号日期排序）---")
    pb_sorted = pb_df.sort_values('signal_date', ascending=True)
    for _, r in pb_sorted.iterrows():
        t5_str = f"{r['t5_chg']:+.2f}%" if r['t5_chg'] is not None else "未到期"
        maxup_str = f"{r['t5_maxup']:+.2f}%" if r['t5_maxup'] is not None else "-"
        maxdd_str = f"{r['t5_maxdd']:+.2f}%" if r['t5_maxdd'] is not None else "-"
        print(f"  {r['signal_date']}→{r['buy_date']}(T+{r['buy_offset']}) {r['code']} {str(r['name'])[:8]:<8} "
              f"评分{r['signal_score']} 回踩{r['touch_ma']} 量缩{r['vol_ratio_vs_signal']:.2f} "
              f"T+5={t5_str} 最大涨={maxup_str} 回撤={maxdd_str}")

if pb_valid:
    pb_df = pd.DataFrame(pb_valid)
    cols = ['signal_date','buy_date','buy_offset','code','name','signal_score',
            'buy_price','touch_ma','vol_ratio_vs_signal','pct_on_buy_day',
            'dist_ma5','dist_ma10','t3_chg','t5_chg','t5_maxup','t5_maxdd']
    pb_df[cols].to_csv(r"d:\mystock\cache_daily\PullbackBuy_Backtest.csv", index=False, encoding='utf-8-sig')
    print(f"\n明细已保存")
