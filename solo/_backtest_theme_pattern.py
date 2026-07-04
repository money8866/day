# -*- coding: utf-8 -*-
"""
回测 theme_pattern_stock_picker.py 20260630 选股结果
计算选股后1日、3日、5日收益表现
"""
import os
import sys
import time
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import tushare as ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DATA_DIR = os.path.dirname(BASE_DIR)

load_dotenv(os.path.join(STOCK_DATA_DIR, "config", ".env"))
TS_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TS_TOKEN)
pro = ts.pro_api()

CACHE_DIR = os.path.join(STOCK_DATA_DIR, "cache_daily")

# 选股日期
PICK_DATE = '20260630'
# 基准日期（选股后下一个交易日）
BASE_DATES = {
    'T+1': '20260701',
    'T+3': None,  # 动态计算
    'T+5': None,  # 动态计算
}

# 读取选股结果
csv_file = os.path.join(BASE_DIR, "report_daily", f"theme_pattern_stocks_{PICK_DATE}.csv")
df_pick = pd.read_csv(csv_file)

# 去重
df_pick_unique = df_pick.drop_duplicates(subset=['code'])[['code', 'name', 'close', 'final_score', 'buy_type', 'theme_name']].copy()
df_pick_unique['close'] = pd.to_numeric(df_pick_unique['close'], errors='coerce')
df_pick_unique['final_score'] = pd.to_numeric(df_pick_unique['final_score'], errors='coerce')

print(f"选股日期: {PICK_DATE}")
print(f"选股结果总数: {len(df_pick)} 条记录")
print(f"去重后股票数: {len(df_pick_unique)} 只")
print()

# 获取交易日历
cal = pro.trade_cal(exchange='', start_date=PICK_DATE, end_date='20260715')
cal = cal[cal['is_open'] == 1].sort_values('cal_date').reset_index(drop=True)
pick_idx = cal[cal['cal_date'] == PICK_DATE].index[0]

# 计算T+1, T+3, T+5对应的交易日
trade_dates = {}
for offset, label in [(1, 'T+1'), (3, 'T+3'), (5, 'T+5')]:
    if pick_idx + offset < len(cal):
        trade_dates[label] = cal.iloc[pick_idx + offset]['cal_date']
    else:
        trade_dates[label] = None

print(f"选股日: {PICK_DATE} (收盘价)")
for label, date in trade_dates.items():
    if date:
        print(f"  {label}: {date}")
print()

# 获取每只股票的后续行情
print("获取后续行情数据...")
results = []

for idx, row in df_pick_unique.iterrows():
    code = row['code']
    cache_file = os.path.join(CACHE_DIR, f"stock_{code.replace('.', '_')}.csv")
    
    if os.path.exists(cache_file):
        df_stock = pd.read_csv(cache_file)
        df_stock['trade_date'] = df_stock['trade_date'].astype(str)
    else:
        time.sleep(0.12)
        df_stock = pro.daily(ts_code=code, start_date=PICK_DATE, end_date='20260715')
        if not df_stock.empty:
            df_stock['trade_date'] = df_stock['trade_date'].astype(str)
            df_stock.to_csv(cache_file, index=False)
    
    if df_stock.empty:
        print(f"  [WARN] {code} 无数据")
        continue
    
    df_stock = df_stock.sort_values('trade_date')
    
    # 选股日收盘价
    base_row = df_stock[df_stock['trade_date'] == PICK_DATE]
    if base_row.empty:
        # 用选股结果中的收盘价
        base_close = row['close']
    else:
        base_close = float(base_row.iloc[0]['close'])
    
    result = {
        'code': code,
        'name': row['name'],
        'pick_close': base_close,
        'final_score': row['final_score'],
        'buy_type': row['buy_type'],
        'theme_name': row['theme_name'],
    }
    
    # 计算各持仓周期收益
    for label, target_date in trade_dates.items():
        if target_date is None:
            result[f'{label}_close'] = None
            result[f'{label}_pct'] = None
            continue
        
        future_row = df_stock[df_stock['trade_date'] == target_date]
        if not future_row.empty:
            future_close = float(future_row.iloc[0]['close'])
            result[f'{label}_close'] = future_close
            result[f'{label}_pct'] = (future_close / base_close - 1) * 100
        else:
            result[f'{label}_close'] = None
            result[f'{label}_pct'] = None
    
    results.append(result)
    
    if (idx + 1) % 10 == 0:
        print(f"  已处理 {idx + 1}/{len(df_pick_unique)}")

df_result = pd.DataFrame(results)

# === 统计汇总 ===
print("\n" + "=" * 70)
print(f"回测结果汇总（选股日: {PICK_DATE}）")
print("=" * 70)

print(f"\n样本数: {len(df_result)} 只")

for label in ['T+1', 'T+3', 'T+5']:
    col = f'{label}_pct'
    valid = df_result[col].dropna()
    if len(valid) > 0:
        win = (valid > 0).sum()
        loss = (valid <= 0).sum()
        win_rate = win / len(valid) * 100
        avg_ret = valid.mean()
        median_ret = valid.median()
        max_ret = valid.max()
        min_ret = valid.min()
        
        print(f"\n--- {label} 收益 ---")
        print(f"  有效样本: {len(valid)}")
        print(f"  胜率: {win_rate:.1f}% ({win}涨 / {loss}跌)")
        print(f"  平均收益: {avg_ret:+.2f}%")
        print(f"  中位数: {median_ret:+.2f}%")
        print(f"  最大: {max_ret:+.2f}%  最小: {min_ret:+.2f}%")

# === 按buy_type分组 ===
print("\n" + "=" * 70)
print("按买入类型分组（T+3收益）")
print("=" * 70)

for buy_type in df_result['buy_type'].unique():
    group = df_result[df_result['buy_type'] == buy_type]
    valid = group['T+3_pct'].dropna()
    if len(valid) > 0:
        win_rate = (valid > 0).sum() / len(valid) * 100
        avg_ret = valid.mean()
        print(f"  {buy_type}: {len(valid)}只 | 胜率{win_rate:.0f}% | 平均{avg_ret:+.2f}%")

# === 按final_score分组 ===
print("\n" + "=" * 70)
print("按评分分组（T+3收益）")
print("=" * 70)

for threshold, label in [(90, '评分>=90'), (80, '评分80-90'), (50, '评分50-80'), (0, '评分<50')]:
    if threshold == 80:
        group = df_result[(df_result['final_score'] >= 80) & (df_result['final_score'] < 90)]
    elif threshold == 50:
        group = df_result[(df_result['final_score'] >= 50) & (df_result['final_score'] < 80)]
    elif threshold == 0:
        group = df_result[df_result['final_score'] < 50]
    else:
        group = df_result[df_result['final_score'] >= threshold]
    
    valid = group['T+3_pct'].dropna()
    if len(valid) > 0:
        win_rate = (valid > 0).sum() / len(valid) * 100
        avg_ret = valid.mean()
        print(f"  {label}: {len(valid)}只 | 胜率{win_rate:.0f}% | 平均{avg_ret:+.2f}%")

# === TOP10 和 BOTTOM10 ===
print("\n" + "=" * 70)
print("T+3 收益 TOP 10")
print("=" * 70)
top10 = df_result.nlargest(10, 'T+3_pct')[['code', 'name', 'final_score', 'buy_type', 'T+1_pct', 'T+3_pct', 'T+5_pct']]
for _, row in top10.iterrows():
    t1 = f"{row['T+1_pct']:+.1f}%" if pd.notna(row['T+1_pct']) else "N/A"
    t3 = f"{row['T+3_pct']:+.1f}%" if pd.notna(row['T+3_pct']) else "N/A"
    t5 = f"{row['T+5_pct']:+.1f}%" if pd.notna(row['T+5_pct']) else "N/A"
    print(f"  {row['code']} {row['name']:8s} | 评分{row['final_score']:.0f} {row['buy_type']:6s} | T+1={t1} T+3={t3} T+5={t5}")

print("\n" + "=" * 70)
print("T+3 收益 BOTTOM 10")
print("=" * 70)
bot10 = df_result.nsmallest(10, 'T+3_pct')[['code', 'name', 'final_score', 'buy_type', 'T+1_pct', 'T+3_pct', 'T+5_pct']]
for _, row in bot10.iterrows():
    t1 = f"{row['T+1_pct']:+.1f}%" if pd.notna(row['T+1_pct']) else "N/A"
    t3 = f"{row['T+3_pct']:+.1f}%" if pd.notna(row['T+3_pct']) else "N/A"
    t5 = f"{row['T+5_pct']:+.1f}%" if pd.notna(row['T+5_pct']) else "N/A"
    print(f"  {row['code']} {row['name']:8s} | 评分{row['final_score']:.0f} {row['buy_type']:6s} | T+1={t1} T+3={t3} T+5={t5}")

# === 保存详细结果 ===
output_file = os.path.join(BASE_DIR, "report_daily", f"theme_pattern_backtest_{PICK_DATE}.csv")
df_result.to_csv(output_file, index=False, encoding='utf-8-sig')
print(f"\n详细结果已保存: {output_file}")
