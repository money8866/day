"""回测对比：追高买点 vs 回踩低吸买点"""
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

spec2 = importlib.util.spec_from_file_location("vms", r"d:\mystock\solo\vol_ma_sync_surge_scan.py")
vms = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(vms)


def find_pullback_buy_point(df, signal_idx):
    """在信号日(signal_idx)后T+1~T+3寻找回踩低吸买点
    
    回踩买点条件：
    1. 缩量：当日成交量 < 信号日成交量 * 0.7
    2. 回踩到MA5或MA10附近：最低价触及MA5/MA10 ±2%
    3. 不跌破MA20（最低价 > MA20 * 0.98）
    4. 小阴小阳/十字星：当日涨跌幅在 -3% ~ +2%
    5. 收盘价在MA5或MA10上方（支撑未破）
    
    返回: dict 或 None
    """
    close_arr = df['close'].values.astype(float)
    open_arr = df['open'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    vol_arr = df['vol'].values.astype(float)
    
    # 计算MA
    ma5_arr = pd.Series(close_arr).rolling(5, min_periods=1).mean().values
    ma10_arr = pd.Series(close_arr).rolling(10, min_periods=1).mean().values
    ma20_arr = pd.Series(close_arr).rolling(20, min_periods=1).mean().values
    
    signal_vol = vol_arr[signal_idx]
    signal_close = close_arr[signal_idx]
    
    best_buy = None
    
    for offset in range(1, 4):  # T+1, T+2, T+3
        idx = signal_idx + offset
        if idx >= len(df):
            break
        
        c = close_arr[idx]
        o = open_arr[idx]
        h = high_arr[idx]
        l = low_arr[idx]
        v = vol_arr[idx]
        pct = (c / close_arr[idx-1] - 1) * 100
        
        ma5 = ma5_arr[idx]
        ma10 = ma10_arr[idx]
        ma20 = ma20_arr[idx]
        
        # 条件1：缩量
        vol_shrink = v < signal_vol * 0.7
        
        # 条件2：回踩到MA5或MA10附近（最低价在均线±2%范围内，且不是远离均线）
        touch_ma5 = (l <= ma5 * 1.02) and (l >= ma5 * 0.98)
        touch_ma10 = (l <= ma10 * 1.02) and (l >= ma10 * 0.97)
        near_ma = touch_ma5 or touch_ma10
        
        # 条件3：不跌破MA20
        not_broken_ma20 = l > ma20 * 0.98
        
        # 条件4：小阴小阳/十字星
        small_candle = -3 <= pct <= 2
        
        # 条件5：收盘在MA5或MA10上方
        close_above_support = c > ma5 * 0.99 or c > ma10 * 0.99
        
        if vol_shrink and near_ma and not_broken_ma20 and small_candle and close_above_support:
            # 计算T+3和T+5收益（从买点日开始算）
            buy_price = c  # 回踩日收盘买入
            
            # 找T+3和T+5
            t3_chg = None
            t5_chg = None
            t5_maxup = None
            t5_maxdd = None
            
            if idx + 3 < len(df):
                t3_close = close_arr[idx + 3]
                t3_chg = (t3_close / buy_price - 1) * 100
            if idx + 5 < len(df):
                t5_close = close_arr[idx + 5]
                t5_chg = (t5_close / buy_price - 1) * 100
                # 最大涨幅和回撤
                window = close_arr[idx+1:idx+6]
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
            
            # 取最早的回踩买点
            if best_buy is None or offset < best_buy['buy_offset']:
                best_buy = result
    
    return best_buy


# 扫描近2个月数据（6月和7月）找所有信号
print("正在扫描近2个月的量能爆发信号...")
tq.TRADE_DATE = "20260801"

# 获取交易日历
trade_cal = tq.pro.trade_cal(exchange='SSE', start_date='20260601', end_date='20260801')
trade_dates = trade_cal[trade_cal['is_open'] == 1]['cal_date'].astype(str).tolist()
trade_dates = sorted(trade_dates)

# 获取股票池
all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
# 过滤北交所
all_stocks = [c for c in all_stocks if not (c.startswith('8') or c.startswith('4') or c.startswith('9'))]
print(f"股票数: {len(all_stocks)}, 交易日数: {len(trade_dates)}")

# 收集所有信号和买点
chase_results = []  # 追高买点
pullback_results = []  # 回踩买点

t0 = time.time()
count = 0

for code in all_stocks[:]:
    count += 1
    if count % 500 == 0:
        elapsed = time.time() - t0
        print(f"  进度: {count}/{len(all_stocks)}, 信号{len(chase_results)}个, 回踩买点{len(pullback_results)}个, {elapsed:.0f}s")
    
    try:
        df = tq.get_hist_data(code)
        if df is None or len(df) < 80:
            continue
        
        close_arr = df['close'].values.astype(float)
        
        # 遍历每一天检测信号（限制在6-7月）
        for i in range(60, len(df)):
            trade_date = str(df.iloc[i]['trade_date'])
            if trade_date < '20260601' or trade_date > '20260725':
                continue
            
            # 检测T日量能爆发信号
            result = vms.detect_vol_ma_sync_surge(df, target_idx=i)
            if result is None or result['score'] < 75:
                continue
            
            signal_close = close_arr[i]
            name = tq.get_stock_name(code) if hasattr(tq, 'get_stock_name') else ''
            
            # 追高买点：T日收盘买入
            t5_chg_chase = None
            t5_maxup_chase = None
            if i + 5 < len(df):
                t5_close_chase = close_arr[i + 5]
                t5_chg_chase = (t5_close_chase / signal_close - 1) * 100
                window_high = max(df['high'].values.astype(float)[i+1:i+6])
                t5_maxup_chase = (window_high / signal_close - 1) * 100
            
            chase_results.append({
                'code': code, 'name': name, 'date': trade_date,
                'score': result['score'],
                't5_chg': round(t5_chg_chase, 2) if t5_chg_chase is not None else None,
                't5_maxup': round(t5_maxup_chase, 2) if t5_maxup_chase is not None else None,
            })
            
            # 回踩买点
            pb = find_pullback_buy_point(df, i)
            if pb:
                pb['code'] = code
                pb['name'] = name
                pb['signal_date'] = trade_date
                pb['signal_score'] = result['score']
                pullback_results.append(pb)
    
    except Exception as e:
        continue

elapsed = time.time() - t0
print(f"\n扫描完成，耗时{elapsed:.1f}秒")
print(f"量能爆发信号: {len(chase_results)}个")
print(f"回踩低吸买点: {len(pullback_results)}个")

# 统计对比
print("\n" + "=" * 70)
print("【追高买点 vs 回踩低吸买点 - T+5胜率对比】")
print("=" * 70)

# 追高统计
chase_valid = [r for r in chase_results if r['t5_chg'] is not None]
if chase_valid:
    chase_df = pd.DataFrame(chase_valid)
    chase_win3 = (chase_df['t5_maxup'] >= 3).sum()
    chase_win = (chase_df['t5_chg'] >= 3).sum()
    print(f"\n--- 追高买点（信号日收盘买入）---")
    print(f"  样本数: {len(chase_valid)}")
    print(f"  最终涨>=3%胜率: {chase_win}/{len(chase_valid)} = {chase_win/len(chase_valid)*100:.1f}%")
    print(f"  动态止盈胜率(最大涨>=3%): {chase_win3}/{len(chase_valid)} = {chase_win3/len(chase_valid)*100:.1f}%")
    print(f"  平均T+5收益: {chase_df['t5_chg'].mean():+.2f}%")
    print(f"  平均最大涨幅: {chase_df['t5_maxup'].mean():+.2f}%")

# 回踩统计
pb_valid = [r for r in pullback_results if r['t5_chg'] is not None]
if pb_valid:
    pb_df = pd.DataFrame(pb_valid)
    pb_win3 = (pb_df['t5_maxup'] >= 3).sum()
    pb_win = (pb_df['t5_chg'] >= 3).sum()
    pb_profit = (pb_df['t5_chg'] > 0).sum()
    print(f"\n--- 回踩低吸买点（T+1~T+3缩量回踩MA买入）---")
    print(f"  样本数: {len(pb_valid)}")
    print(f"  最终涨>=3%胜率: {pb_win}/{len(pb_valid)} = {pb_win/len(pb_valid)*100:.1f}%")
    print(f"  动态止盈胜率(最大涨>=3%): {pb_win3}/{len(pb_valid)} = {pb_win3/len(pb_valid)*100:.1f}%")
    print(f"  T+5盈利概率: {pb_profit}/{len(pb_valid)} = {pb_profit/len(pb_valid)*100:.1f}%")
    print(f"  平均T+5收益: {pb_df['t5_chg'].mean():+.2f}%")
    print(f"  平均最大涨幅: {pb_df['t5_maxup'].mean():+.2f}%")
    print(f"  平均最大回撤: {pb_df['t5_maxdd'].mean():+.2f}%")
    
    # 按回踩到哪根均线统计
    print(f"\n--- 按回踩均线分组 ---")
    for ma in ['MA5', 'MA10']:
        sub = pb_df[pb_df['touch_ma'] == ma]
        if len(sub) > 0:
            wr = (sub['t5_maxup'] >= 3).sum() / len(sub) * 100
            avg = sub['t5_chg'].mean()
            print(f"  回踩{ma}: {len(sub)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")
    
    # 按信号日后几天回踩统计
    print(f"\n--- 按回踩天数分组 ---")
    for d in [1, 2, 3]:
        sub = pb_df[pb_df['buy_offset'] == d]
        if len(sub) > 0:
            wr = (sub['t5_maxup'] >= 3).sum() / len(sub) * 100
            avg = sub['t5_chg'].mean()
            print(f"  T+{d}回踩: {len(sub)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")
    
    # 按评分分组
    print(f"\n--- 回踩买点按评分分组 ---")
    for lo, hi in [(75,80),(80,85),(85,100)]:
        sub = pb_df[(pb_df['signal_score'] >= lo) & (pb_df['signal_score'] < hi)]
        if len(sub) > 0:
            wr = (sub['t5_maxup'] >= 3).sum() / len(sub) * 100
            avg = sub['t5_chg'].mean()
            print(f"  评分[{lo}-{hi}): {len(sub)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")

# 保存回踩买点明细
if pb_valid:
    pb_df = pd.DataFrame(pb_valid)
    cols = ['signal_date','buy_date','buy_offset','code','name','signal_score',
            'buy_price','touch_ma','vol_ratio_vs_signal','pct_on_buy_day',
            't3_chg','t5_chg','t5_maxup','t5_maxdd']
    pb_df[cols].to_csv(r"d:\mystock\cache_daily\PullbackBuy_Backtest.csv", index=False, encoding='utf-8-sig')
    print(f"\n回踩买点明细已保存")
