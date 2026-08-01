"""验证7月30日信号次日表现，并分析所有历史信号的次日表现"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
import pandas as pd
import numpy as np

spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

# 验证江苏索普和工商银行7月31日表现
test_codes = ['600746.SH', '601398.SH']
print("=" * 70)
print("【7月30日信号 - 次日(7月31日)表现验证】")
print("=" * 70)
for code in test_codes:
    tq.TRADE_DATE = "20260731"
    df = tq.get_hist_data(code)
    if df is not None and len(df) >= 3:
        last3 = df.tail(3)
        print(f"\n{code} {tq.get_stock_name(code)}")
        for _, r in last3.iterrows():
            print(f"  {r['trade_date']}: 收盘{r['close']:.2f}  涨幅{r.get('pct_chg',0):.2f}%  成交量{r['vol']:.0f}")

# 读取月分析数据，统计所有信号的次日(T+1)表现
print("\n" + "=" * 70)
print("【所有历史信号 T+1(次日)表现统计】")
print("=" * 70)
sig_df = pd.read_excel(r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx")
print(f"总信号数: {len(sig_df)}")

# 对每个信号，查询T+1日表现
t1_chgs = []
for _, row in sig_df.iterrows():
    code = row['code']
    date = str(row['date'])
    try:
        tq.TRADE_DATE = "20260801"  # 用最新日期获取完整数据
        df = tq.get_hist_data(code)
        if df is None:
            continue
        df['trade_date'] = df['trade_date'].astype(str)
        # 找到信号日位置
        idx_list = df.index[df['trade_date'] == date].tolist()
        if not idx_list:
            continue
        idx = idx_list[0]
        if idx + 1 >= len(df):
            continue
        next_day = df.iloc[idx + 1]
        # 次日开盘和收盘
        next_open = next_day.get('open', None)
        next_close = next_day['close']
        signal_close = df.iloc[idx]['close']
        
        if next_open and not pd.isna(next_open):
            # 次日追高买入（开盘买入）vs 收盘买入的收益
            buy_at_open_chg = (next_close / next_open - 1) * 100
            buy_at_signal_close_chg = (next_close / signal_close - 1) * 100
            next_day_chg = next_day.get('pct_chg', (next_close / signal_close - 1) * 100)
            t1_chgs.append({
                'code': code, 'name': row['name'], 'date': date,
                'score': row['score'],
                'signal_close': signal_close,
                'next_open': next_open, 'next_close': next_close,
                'next_day_chg': next_day_chg,
                'open_buy_chg': buy_at_open_chg,
                'close_buy_next_chg': buy_at_signal_close_chg,
            })
    except Exception as e:
        pass

t1_df = pd.DataFrame(t1_chgs)
print(f"可统计T+1表现: {len(t1_df)}只")

if len(t1_df) > 0:
    print(f"\n--- 次日(T+1)平均涨幅: {t1_df['next_day_chg'].mean():+.2f}%")
    print(f"--- 信号日收盘买入→次日收盘: 平均{t1_df['close_buy_next_chg'].mean():+.2f}%")
    print(f"--- 次日开盘买入→次日收盘: 平均{t1_df['open_buy_chg'].mean():+.2f}%")
    
    up_days = (t1_df['next_day_chg'] > 0).sum()
    down_days = (t1_df['next_day_chg'] < 0).sum()
    print(f"\n--- 次日上涨: {up_days}只 ({up_days/len(t1_df)*100:.1f}%)")
    print(f"--- 次日下跌: {down_days}只 ({down_days/len(t1_df)*100:.1f}%)")
    
    # 次日最大回撤
    print(f"\n--- 次日平均最大跌幅: {t1_df['next_day_chg'].min():.2f}%")
    
    print("\n--- T+1表现最差Top10（信号日追高的后果）:")
    worst = t1_df.nsmallest(10, 'next_day_chg')
    for _, r in worst.iterrows():
        print(f"  {r['date']} {r['code']} {str(r['name'])[:8]} 评分{r['score']}  次日{r['next_day_chg']:+.2f}%")
