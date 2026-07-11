"""验证半导体设备ETF 6月补涨信号发出后5日/10日涨幅和胜率。"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:\mystock\solo')
os.chdir(r'd:\mystock\solo')

from dotenv import load_dotenv
from multi_factor_picker.data_fetcher import DataFetcher

load_dotenv(r'd:\mystock\config\.env' if os.path.exists(r'd:\mystock\config\.env') else r'd:\mystock\solo\.env')
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
dfetcher = DataFetcher(TS_TOKEN, {
    'cache': {'enabled': True, 'dir': r'd:\mystock\solo\multi_factor_picker\cache', 'expire_hours': 720},
    'tushare': {'max_retry': 3, 'retry_delay': 5}
})

# 读取回测信号
csv_path = r'd:\mystock\solo\etf_resonance\output\backtest_159516_june.csv'
df_signals = pd.read_csv(csv_path, dtype={'code': str})
print(f"读取信号: {len(df_signals)} 条, 涉及 {df_signals['code'].nunique()} 只股票")
print()

# 获取股票名称
try:
    stock_basic = dfetcher.get_stock_list(list_status='L')
    name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
except Exception:
    name_map = {}

# 补充名称
df_signals['name'] = df_signals['code'].map(lambda c: name_map.get(c, ''))

# 下载这些股票的完整日线 (到7月10日，确保有信号后10个交易日)
codes = df_signals['code'].unique().tolist()
END_DATE = '20260710'
START_DATE = '20260501'

print(f"下载 {len(codes)} 只股票日线...")
stock_daily = {}
for code in codes:
    try:
        df = dfetcher.get_daily_by_code(ts_code=code, start_date=START_DATE, end_date=END_DATE)
        if df is not None and not df.empty:
            stock_daily[code] = df.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        pass
print(f"成功下载: {len(stock_daily)} 只")

# 也下载ETF数据做基准对比
etf_df = dfetcher.get_fund_daily(ts_code='159516.SZ', start_date=START_DATE, end_date=END_DATE)
if etf_df is not None and not etf_df.empty:
    etf_df = etf_df.sort_values('trade_date').reset_index(drop=True)

# 获取交易日历 (5月-7月)
cal_df = dfetcher.get_trade_cal(start_date='20260501', end_date='20260715', is_open='1')
all_dates = sorted(cal_df[cal_df['is_open'] == 1]['cal_date'].tolist())

def get_future_returns(code, signal_date, n_days):
    """计算信号发出后n个交易日的涨幅。"""
    if code not in stock_daily:
        return None, None
    df = stock_daily[code]
    idx = df.index[df['trade_date'] == signal_date].tolist()
    if not idx:
        return None, None
    pos = idx[0]
    if pos + n_days >= len(df):
        return None, None
    buy_close = df.iloc[pos]['close']
    sell_close = df.iloc[pos + n_days]['close']
    if buy_close <= 0:
        return None, None
    ret = (sell_close / buy_close - 1) * 100
    max_high = df.iloc[pos + 1:pos + 1 + n_days]['high'].max()
    min_low = df.iloc[pos + 1:pos + 1 + n_days]['low'].min()
    max_ret = (max_high / buy_close - 1) * 100
    min_ret = (min_low / buy_close - 1) * 100
    return ret, (max_ret, min_ret)

def get_etf_future_returns(signal_date, n_days):
    """计算ETF信号发出后n个交易日的涨幅。"""
    if etf_df is None:
        return None
    idx = etf_df.index[etf_df['trade_date'] == signal_date].tolist()
    if not idx:
        return None
    pos = idx[0]
    if pos + n_days >= len(etf_df):
        return None
    buy_close = etf_df.iloc[pos]['close']
    sell_close = etf_df.iloc[pos + n_days]['close']
    if buy_close <= 0:
        return None
    return (sell_close / buy_close - 1) * 100

# 计算每条信号的5日和10日收益
print(f"\n{'='*120}")
print(f"  半导体设备ETF(159516.SZ) 6月补涨信号 -> 5日/10日涨幅验证")
print(f"{'='*120}")
print(f"{'日期':<10}{'代码':<12}{'名称':<10}{'补涨分':<8}{'趋势':<6}{'交叉天':<6}"
      f"{'5日涨':<10}{'5日最大涨':<10}{'5日最大跌':<10}{'10日涨':<10}{'ETF5日':<10}{'超额5日':<10}")
print(f"{'-'*120}")

results_5d = []
results_10d = []

for _, sig in df_signals.iterrows():
    code = sig['code']
    date = str(sig['date'])
    name = sig.get('name', '')

    ret5, ext5 = get_future_returns(code, date, 5)
    ret10, ext10 = get_future_returns(code, date, 10)
    etf_ret5 = get_etf_future_returns(date, 5)

    max5 = ext5[0] if ext5 else None
    min5 = ext5[1] if ext5 else None
    excess5 = (ret5 - etf_ret5) if (ret5 is not None and etf_ret5 is not None) else None

    print(f"{date:<10}{code:<12}{name:<10}{sig['catchup_score']:<8.1f}{sig['trend_setup']:<6.0f}"
          f"{sig['ma_cross_days']:<6d}"
          f"{ret5 if ret5 is not None else 'N/A':<10}"
          f"{max5 if max5 is not None else 'N/A':<10}"
          f"{min5 if min5 is not None else 'N/A':<10}"
          f"{ret10 if ret10 is not None else 'N/A':<10}"
          f"{etf_ret5 if etf_ret5 is not None else 'N/A':<10}"
          f"{excess5 if excess5 is not None else 'N/A':<10}")

    if ret5 is not None:
        results_5d.append({
            'date': date, 'code': code, 'name': name,
            'catchup_score': sig['catchup_score'],
            'trend_setup': sig['trend_setup'],
            'ma_cross_days': sig['ma_cross_days'],
            'ret5': ret5, 'max5': max5, 'min5': min5,
            'etf_ret5': etf_ret5, 'excess5': excess5,
        })
    if ret10 is not None:
        results_10d.append({
            'date': date, 'code': code, 'name': name,
            'catchup_score': sig['catchup_score'],
            'ret10': ret10,
        })

# 汇总统计
print(f"\n{'='*80}")
print(f"  胜率统计汇总")
print(f"{'='*80}")

if results_5d:
    df5 = pd.DataFrame(results_5d)
    win5 = (df5['ret5'] > 0).sum()
    total5 = len(df5)
    avg5 = df5['ret5'].mean()
    med5 = df5['ret5'].median()
    avg_max5 = df5['max5'].mean()
    avg_min5 = df5['min5'].mean()
    avg_etf5 = df5['etf_ret5'].mean()
    avg_excess5 = df5['excess5'].mean()
    win_excess5 = (df5['excess5'] > 0).sum()

    print(f"\n  5日持有:")
    print(f"    样本数: {total5}")
    print(f"    胜率(涨): {win5}/{total5} = {win5/total5*100:.1f}%")
    print(f"    平均涨幅: {avg5:+.2f}%")
    print(f"    中位数: {med5:+.2f}%")
    print(f"    平均最大涨幅: {avg_max5:+.2f}%")
    print(f"    平均最大跌幅: {avg_min5:+.2f}%")
    print(f"    ETF平均涨幅: {avg_etf5:+.2f}%")
    print(f"    平均超额收益: {avg_excess5:+.2f}%")
    print(f"    超额胜率: {win_excess5}/{total5} = {win_excess5/total5*100:.1f}%")

if results_10d:
    df10 = pd.DataFrame(results_10d)
    win10 = (df10['ret10'] > 0).sum()
    total10 = len(df10)
    avg10 = df10['ret10'].mean()
    med10 = df10['ret10'].median()

    print(f"\n  10日持有:")
    print(f"    样本数: {total10}")
    print(f"    胜率(涨): {win10}/{total10} = {win10/total10*100:.1f}%")
    print(f"    平均涨幅: {avg10:+.2f}%")
    print(f"    中位数: {med10:+.2f}%")

# 按交叉天数分组分析
if results_5d:
    print(f"\n  按EMA交叉天数分组 (5日):")
    print(f"  {'交叉天':<8}{'样本':<6}{'胜率':<8}{'平均涨':<10}{'中位':<10}{'平均最大涨':<10}{'平均最大跌':<10}")
    print(f"  {'-'*70}")
    for cd, grp in df5.groupby('ma_cross_days'):
        w = (grp['ret5'] > 0).sum()
        print(f"  {cd:<8d}{len(grp):<6d}{w/len(grp)*100:<8.1f}"
              f"{grp['ret5'].mean():<+10.2f}{grp['ret5'].median():<+10.2f}"
              f"{grp['max5'].mean():<+10.2f}{grp['min5'].mean():<+10.2f}")

# 按补涨分分组分析
if results_5d:
    print(f"\n  按补涨分组 (5日):")
    print(f"  {'补涨分':<10}{'样本':<6}{'胜率':<8}{'平均涨':<10}{'中位':<10}{'平均超额':<10}")
    print(f"  {'-'*60}")
    for lo, hi, label in [(70, 75, '70-75'), (75, 80, '75-80'), (80, 85, '80-85'), (85, 100, '85+')]:
        grp = df5[(df5['catchup_score'] >= lo) & (df5['catchup_score'] < hi)]
        if len(grp) > 0:
            w = (grp['ret5'] > 0).sum()
            print(f"  {label:<10}{len(grp):<6d}{w/len(grp)*100:<8.1f}"
                  f"{grp['ret5'].mean():<+10.2f}{grp['ret5'].median():<+10.2f}"
                  f"{grp['excess5'].mean():<+10.2f}")

# 按股票分组
if results_5d:
    print(f"\n  按股票分组 (5日):")
    print(f"  {'代码':<12}{'名称':<10}{'样本':<6}{'胜率':<8}{'平均涨':<10}{'平均最大涨':<10}{'平均最大跌':<10}")
    print(f"  {'-'*76}")
    for (code, name), grp in df5.groupby(['code', 'name']):
        w = (grp['ret5'] > 0).sum()
        print(f"  {code:<12}{name:<10}{len(grp):<6d}{w/len(grp)*100:<8.1f}"
              f"{grp['ret5'].mean():<+10.2f}{grp['max5'].mean():<+10.2f}{grp['min5'].mean():<+10.2f}")

# 保存详细结果
out_path = r'd:\mystock\solo\etf_resonance\output\backtest_159516_june_verify.csv'
if results_5d:
    pd.DataFrame(results_5d).to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n  已保存: {out_path}")
