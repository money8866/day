"""诊断趋势评分因子"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import json
from pathlib import Path
from data_fetcher import DataFetcher
from trend_picker import get_daily_data, get_moneyflow_data, get_daily_basic, get_holder_data
from trend_picker import score_fundamental, score_capital, score_technical

# 配置
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}
fetcher = DataFetcher(token, config)

ts_code = '002409.SZ'
name = '雅克科技'
industry = '半导体'
end_date = '20260530'
start_date = '20260301'

print(f'=== {name}({ts_code}) 趋势因子诊断 ===')
print(f'截止日期: {end_date}\n')

# 获取数据
daily = get_daily_data(fetcher, ts_code, start_date, end_date)
moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
daily_basic = get_daily_basic(fetcher, ts_code, end_date)
income = fetcher.get_income(ts_code)
holders = get_holder_data(fetcher, ts_code)

print(f'--- 数据完整性 ---')
print(f'日线数据: {len(daily)}条')
print(f'资金流数据: {len(moneyflow)}条')
print(f'每日基本面: {len(daily_basic)}条')
print(f'财务数据: {len(income)}条')
print(f'股东数据: {len(holders)}条')

if len(daily) > 0:
    print(f'\n--- 最新行情 ---')
    latest = daily.iloc[-1]
    print(f'日期: {latest["trade_date"]}')
    print(f'收盘: {latest["close"]:.2f}')
    print(f'涨跌: {latest["pct_chg"]:.2f}%')
    print(f'换手率: {latest.get("turnover_rate", 0):.2f}%')
    
    print(f'\n--- 区间统计 ---')
    print(f'最高: {daily["high"].max():.2f}')
    print(f'最低: {daily["low"].min():.2f}')
    print(f'最大涨幅: {daily["pct_chg"].max():.2f}%')
    print(f'平均换手: {daily.get("turnover_rate", pd.Series([0])).mean():.2f}%')

# 评分诊断
print(f'\n=== 因子评分详情 ===')

# 基本面
fund_score, fund_detail = score_fundamental(fetcher, ts_code, industry, income, daily_basic)
print(f'\n[基本面] 总分: {fund_score:.2f}')
for f in ['F1', 'F2', 'F3']:
    if f in fund_detail:
        print(f'  {f}: {fund_detail[f].get("score", 0):.1f}分')
        for k, v in fund_detail[f].items():
            if k != 'score' and v and v != 0 and v != {}:
                print(f'    {k}: {v}')

# 资金面
cap_score, cap_detail = score_capital(fetcher, ts_code, moneyflow, daily)
print(f'\n[资金面] 总分: {cap_score:.2f}')
for f in ['F4', 'F5', 'F6']:
    if f in cap_detail:
        print(f'  {f}: {cap_detail[f].get("score", 0):.1f}分')
        for k, v in cap_detail[f].items():
            if k != 'score' and v and v != 0 and v != {}:
                print(f'    {k}: {v}')

# 技术面
tech_score, tech_detail = score_technical(daily)
print(f'\n[技术面] 总分: {tech_score:.2f}')
for f in ['F7', 'F8', 'F9']:
    if f in tech_detail:
        print(f'  {f}: {tech_detail[f].get("score", 0):.1f}分')
        for k, v in tech_detail[f].items():
            if k != 'score' and v and v != 0 and v != {}:
                print(f'    {k}: {v}')

total = fund_score + cap_score + tech_score
print(f'\n=== 总分: {total:.1f}/18 (标准化: {total/18*100:.1f}/100) ===')
