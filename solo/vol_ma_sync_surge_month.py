"""
信立泰式量能爆发形态 - 近一个月信号分析
=====================================
扫描近30天内每天的信号，跟踪T+5和T+10实际表现
"""
import sys, os, time
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from vol_ma_sync_surge_scan import detect_vol_ma_sync_surge


def backtest_stock(df, target_idx, forward_days=[5, 10]):
    """回测目标日后的涨跌幅"""
    if df is None or target_idx is None:
        return {}
    close_arr = df['close'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    target_close = close_arr[target_idx]
    result = {}
    for d in forward_days:
        end_idx = target_idx + d
        if end_idx < len(close_arr):
            future_close = close_arr[end_idx]
            chg = (future_close / target_close - 1) * 100
            max_dd = (np.min(low_arr[target_idx+1:end_idx+1]) / target_close - 1) * 100
            max_up = (np.max(high_arr[target_idx+1:end_idx+1]) / target_close - 1) * 100
            result[f'T+{d}_chg'] = round(chg, 2)
            result[f'T+{d}_win'] = chg >= 3
            result[f'T+{d}_maxdd'] = round(max_dd, 2)
            result[f'T+{d}_maxup'] = round(max_up, 2)
            result[f'T+{d}_maxup_win'] = max_up >= 3
        else:
            result[f'T+{d}_chg'] = None
            result[f'T+{d}_win'] = None
            result[f'T+{d}_maxdd'] = None
            result[f'T+{d}_maxup'] = None
            result[f'T+{d}_maxup_win'] = None
    return result


# 获取最近30天的交易日
print("=" * 60)
print("信立泰式量能爆发形态 - 近一个月信号分析")
print("=" * 60)

# 计算日期范围：今天往前推40天（确保覆盖30个交易日）
today = datetime.now()
end_date = today.strftime("%Y%m%d")
start_date = (today - timedelta(days=45)).strftime("%Y%m%d")
print(f"分析区间: {start_date} ~ {end_date}")

# 加载股票池
all_stocks = list(tq.TURNOVER_CACHE.keys()) if hasattr(tq, 'TURNOVER_CACHE') else []
print(f"待扫描股票数: {len(all_stocks)}")
print()

# 扫描所有股票在近30天的信号
signals = []
scanned = 0
t0 = time.time()

for ts_code in all_stocks:
    if ts_code.startswith('8') or ts_code.startswith('4') or ts_code.startswith('9'):
        continue
    
    scanned += 1
    if scanned % 1000 == 0:
        elapsed = time.time() - t0
        print(f"  扫描进度: {scanned}/{len(all_stocks)}, 命中{len(signals)}只, 耗时{elapsed:.0f}s")
    
    try:
        stock_df = tq.get_hist_data(ts_code)
        if stock_df is None or len(stock_df) < 80:
            continue
        
        stock_df = stock_df.copy()
        if 'trade_date' in stock_df.columns:
            stock_df['trade_date'] = stock_df['trade_date'].astype(str)
            stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)
        
        # 扫描最近30个交易日
        for i in range(60, len(stock_df)):
            td = str(stock_df['trade_date'].iloc[i])
            if td < start_date or td > end_date:
                continue
            
            result = detect_vol_ma_sync_surge(stock_df, i)
            if result and result['score'] >= 75:
                bt = backtest_stock(stock_df, i, [5, 10])
                name = tq.get_stock_name(ts_code) if hasattr(tq, 'get_stock_name') else ts_code
                signals.append({
                    'code': ts_code,
                    'name': name,
                    'date': td,
                    'score': result['score'],
                    'vol_surge': result['vol_surge_ratio'],
                    'ma20_slope_5d': result['ma20_slope_5d'],
                    'ma20_slope_pre': result['ma20_slope_pre'],
                    'macd_status': result['macd_status'],
                    'dist_ma20': result['dist_ma20'],
                    'vol_price_coord': result['vol_price_coord'],
                    'last_chg': result['last_chg'],
                    'close': result['close'],
                    **bt
                })
    except Exception:
        pass

elapsed = time.time() - t0
print(f"\n扫描完成: {scanned}只, 命中{len(signals)}只信号, 耗时{elapsed:.0f}s")

if not signals:
    print("\n❌ 近一个月无符合条件的信号")
    sys.exit(0)

sig_df = pd.DataFrame(signals)
sig_df = sig_df.sort_values(['date', 'score'], ascending=[False, False]).reset_index(drop=True)

# ============ 1. 总体统计 ============
print("\n" + "=" * 60)
print("【1. 总体统计】")
print("=" * 60)
total = len(sig_df)

# 只统计T+5有回测数据的
valid_t5 = sig_df[sig_df['T+5_win'].notna()]
valid_t10 = sig_df[sig_df['T+10_win'].notna()]

print(f"\n信号总数: {total}")
print(f"T+5已验证: {len(valid_t5)}只, T+10已验证: {len(valid_t10)}只")

if len(valid_t5) > 0:
    t5_win = valid_t5['T+5_win'].sum()
    t5_maxup_win = valid_t5['T+5_maxup_win'].sum()
    print(f"\n--- T+5统计（{len(valid_t5)}只已验证）---")
    print(f"  最终涨幅>=3%: {t5_win}/{len(valid_t5)} = {t5_win/len(valid_t5)*100:.1f}%")
    print(f"  动态止盈(最大涨幅>=3%): {t5_maxup_win}/{len(valid_t5)} = {t5_maxup_win/len(valid_t5)*100:.1f}%")
    print(f"  平均涨幅: {valid_t5['T+5_chg'].mean():+.2f}%")
    print(f"  中位数: {valid_t5['T+5_chg'].median():+.2f}%")
    print(f"  最大涨幅均值: {valid_t5['T+5_maxup'].mean():+.2f}%")
    print(f"  最大回撤均值: {valid_t5['T+5_maxdd'].mean():+.2f}%")

if len(valid_t10) > 0:
    t10_win = valid_t10['T+10_win'].sum()
    t10_maxup_win = valid_t10['T+10_maxup_win'].sum()
    print(f"\n--- T+10统计（{len(valid_t10)}只已验证）---")
    print(f"  最终涨幅>=3%: {t10_win}/{len(valid_t10)} = {t10_win/len(valid_t10)*100:.1f}%")
    print(f"  动态止盈(最大涨幅>=3%): {t10_maxup_win}/{len(valid_t10)} = {t10_maxup_win/len(valid_t10)*100:.1f}%")
    print(f"  平均涨幅: {valid_t10['T+10_chg'].mean():+.2f}%")

# ============ 2. 按日期分组 ============
print("\n" + "=" * 60)
print("【2. 按日期分组】")
print("=" * 60)
print(f"\n{'日期':<12}{'信号数':<8}{'T+5验证':<10}{'最终胜率':<12}{'止盈胜率':<12}{'均收益':<10}")
print("-" * 70)

for date in sorted(sig_df['date'].unique(), reverse=True):
    sub = sig_df[sig_df['date'] == date]
    sub_valid = sub[sub['T+5_win'].notna()]
    if len(sub_valid) > 0:
        win_w = sub_valid['T+5_win'].sum()
        maxup_w = sub_valid['T+5_maxup_win'].sum()
        avg = sub_valid['T+5_chg'].mean()
        print(f"{date:<12}{len(sub):<8}{len(sub_valid):<10}"
              f"{win_w}/{len(sub_valid)}={win_w/len(sub_valid)*100:.0f}%     "
              f"{maxup_w}/{len(sub_valid)}={maxup_w/len(sub_valid)*100:.0f}%     "
              f"{avg:+.2f}%")
    else:
        # 未到T+5的
        print(f"{date:<12}{len(sub):<8}{'未到期':<10}---       ---       ---")

# ============ 3. 按评分分组 ============
print("\n" + "=" * 60)
print("【3. 按评分分组】")
print("=" * 60)
if len(valid_t5) > 0:
    print(f"\n{'评分区间':<15}{'信号数':<8}{'最终胜率':<14}{'止盈胜率':<14}{'均收益':<10}")
    print("-" * 70)
    for score_range in [(75, 80), (80, 85), (85, 90), (90, 100)]:
        mask = (valid_t5['score'] >= score_range[0]) & (valid_t5['score'] < score_range[1])
        sub = valid_t5[mask]
        if len(sub) > 0:
            win_w = sub['T+5_win'].sum()
            maxup_w = sub['T+5_maxup_win'].sum()
            avg = sub['T+5_chg'].mean()
            print(f"[{score_range[0]}-{score_range[1]})    {len(sub):<8}"
                  f"{win_w}/{len(sub)}={win_w/len(sub)*100:.0f}%      "
                  f"{maxup_w}/{len(sub)}={maxup_w/len(sub)*100:.0f}%      "
                  f"{avg:+.2f}%")

# ============ 4. 盈亏统计 ============
print("\n" + "=" * 60)
print("【4. 盈亏统计】")
print("=" * 60)
if len(valid_t5) > 0:
    wins = valid_t5[valid_t5['T+5_chg'] > 0]['T+5_chg']
    losses = valid_t5[valid_t5['T+5_chg'] < 0]['T+5_chg']
    print(f"\n盈利次数: {len(wins)}, 平均盈利: {wins.mean() if len(wins)>0 else 0:+.2f}%")
    print(f"亏损次数: {len(losses)}, 平均亏损: {losses.mean() if len(losses)>0 else 0:+.2f}%")
    if len(wins) > 0 and len(losses) > 0:
        win_loss_ratio = wins.mean() / abs(losses.mean())
        print(f"盈亏比: {win_loss_ratio:.2f}")
    # 最大单笔盈利和亏损
    print(f"\n最大单笔盈利: {valid_t5['T+5_chg'].max():+.2f}% ({valid_t5.loc[valid_t5['T+5_chg'].idxmax(), 'name']})")
    print(f"最大单笔亏损: {valid_t5['T+5_chg'].min():+.2f}% ({valid_t5.loc[valid_t5['T+5_chg'].idxmin(), 'name']})")

# ============ 5. 个股明细（按T+5收益排序）============
print("\n" + "=" * 60)
print("【5. 个股明细（按T+5收益排序，前20）】")
print("=" * 60)
if len(valid_t5) > 0:
    sorted_valid = valid_t5.sort_values('T+5_chg', ascending=False)
    print(f"\n{'日期':<12}{'代码':<12}{'名称':<10}{'评分':<6}{'T+5':<10}{'T+5最大':<10}{'T+5回撤':<10}{'T+10':<10}")
    print("-" * 80)
    for _, s in sorted_valid.head(20).iterrows():
        t5 = f"{s['T+5_chg']:+.2f}%" if pd.notna(s['T+5_chg']) else 'N/A'
        t5m = f"{s['T+5_maxup']:+.2f}%" if pd.notna(s['T+5_maxup']) else 'N/A'
        t5d = f"{s['T+5_maxdd']:+.2f}%" if pd.notna(s['T+5_maxdd']) else 'N/A'
        t10 = f"{s['T+10_chg']:+.2f}%" if pd.notna(s['T+10_chg']) else 'N/A'
        print(f"{s['date']:<12}{s['code']:<12}{s['name']:<10}{s['score']:<6}"
              f"{t5:<10}{t5m:<10}{t5d:<10}{t10:<10}")

# ============ 6. 亏损案例分析 ============
print("\n" + "=" * 60)
print("【6. 亏损案例（T+5跌幅最大前10）】")
print("=" * 60)
if len(valid_t5) > 0:
    losers = valid_t5.sort_values('T+5_chg').head(10)
    print(f"\n{'日期':<12}{'代码':<12}{'名称':<10}{'评分':<6}{'量能放大':<10}{'距MA20':<10}{'T+5':<10}{'T+5回撤':<10}")
    print("-" * 80)
    for _, s in losers.iterrows():
        print(f"{s['date']:<12}{s['code']:<12}{s['name']:<10}{s['score']:<6}"
              f"{s['vol_surge']:<10.2f}{s['dist_ma20']:<+8.2f}%  "
              f"{s['T+5_chg']:+.2f}%   {s['T+5_maxdd']:+.2f}%")

# ============ 7. 今日信号 ============
print("\n" + "=" * 60)
print("【7. 最新日期信号】")
print("=" * 60)
latest_date = sig_df['date'].max()
latest = sig_df[sig_df['date'] == latest_date].sort_values('score', ascending=False)
print(f"\n最新日期: {latest_date}, 信号数: {len(latest)}")
print(f"\n{'排名':<4}{'代码':<12}{'名称':<10}{'评分':<6}{'量能放大':<8}{'MA20斜率':<14}{'MACD':<10}{'距MA20':<10}{'当日涨幅':<8}")
print("-" * 90)
for i, (_, s) in enumerate(latest.iterrows(), 1):
    print(f"{i:<4}{s['code']:<12}{s['name']:<10}{s['score']:<6}{s['vol_surge']:<8.2f}"
          f"{s['ma20_slope_5d']:<+6.2f}/{s['ma20_slope_pre']:<+5.2f}  "
          f"{s['macd_status']:<10}{s['dist_ma20']:<+8.2f}%  {s['last_chg']:<+6.2f}%")

# 保存
out_path = r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx"
sig_df.to_excel(out_path, index=False)
print(f"\n✅ 信号已保存: {out_path}")
