"""
利扬芯片（688135.SH）基本面分析 v3
简化版，只获取关键数据，不依赖复杂字段。
"""

import tushare as ts
import os
import pandas as pd

if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
pro = ts.pro_api()

ts_code = '688135.SH'

print('=' * 60)
print(f'利扬芯片（{ts_code}）基本面分析')
print('=' * 60)
print()

# 1. 基本信息
print('1. 基本信息')
print('-' * 60)
basic = pro.stock_basic(ts_code=ts_code, fields='ts_code,name,industry,market,list_date')
if basic is not None and len(basic) > 0:
    b = basic.iloc[0]
    print(f'股票名称: {b["name"]}')
    print(f'股票代码: {b["ts_code"]}')
    print(f'所属行业: {b["industry"]}')
    print("板块: 科创板")  # 从代码688可知
    print(f'上市日期: {b["list_date"]}')
print()

# 2. 最新股价与市值（从daily_basic获取）
print('2. 最新股价与估值')
print('-' * 60)
try:
    df_basic = pro.daily_basic(ts_code=ts_code, trade_date='20260629', fields='ts_code,trade_date,close,pe_ttm,pb,ps_ttml,total_mv,circ_mv')
    if df_basic is not None and len(df_basic) > 0:
        row = df_basic.iloc[0]
        print(f'最新收盘价: {row["close"]:.2f}元 ({row["trade_date"]})')
        if row['pe_ttm'] and row['pe_ttm'] > 0:
            print(f'PE(TTM): {row["pe_ttm"]:.1f}')
        if row['pb'] and row['pb'] > 0:
            print(f'PB: {row["pb"]:.1f}')
        if row['ps_ttml'] and row['ps_ttml'] > 0:
            print(f'PS(TTM): {row["ps_ttml"]:.1f}')
        if row['total_mv'] and row['total_mv'] > 0:
            print(f'总市值: {row["total_mv"]/10000:.1f}亿')
        if row['circ_mv'] and row['circ_mv'] > 0:
            print(f'流通市值: {row["circ_mv"]/10000:.1f}亿')
    else:
        print('未获取到最新估值数据')
except Exception as e:
    print(f'获取估值数据失败: {e}')
print()

# 3. 近3年利润表
print('3. 近3年利润表（年报）')
print('-' * 60)
income = pro.income(ts_code=ts_code, period='20231231,20221231,20211231', fields='ts_code,end_date,revenue,operate_profit,total_profit,net_profit,deducted_profit')
if income is not None and len(income) > 0:
    for i, row in income.iterrows():
        print(f'{row["end_date"]}:')
        print(f'  营收: {row["revenue"]/10000:.1f}亿')
        print(f'  营业利润: {row["operate_profit"]/10000:.1f}亿')
        print(f'  净利润: {row["net_profit"]/10000:.1f}亿')
        print(f'  扣非净利润: {row["deducted_profit"]/10000:.1f}亿')
        print()
else:
    print('未获取到利润表数据')
    print()

# 4. 近3年资产负债表
print('4. 近3年资产负债表（年报）')
print('-' * 60)
balance = pro.balancesheet(ts_code=ts_code, period='20231231,20221231,20211231', fields='ts_code,end_date,total_assets,total_liab,total_equity,equity_ratio')
if balance is not None and len(balance) > 0:
    for i, row in balance.iterrows():
        print(f'{row["end_date"]}:')
        print(f'  总资产: {row["total_assets"]/10000:.1f}亿')
        print(f'  总负债: {row["total_liab"]/10000:.1f}亿')
        if row['total_equity'] and row['total_equity'] > 0:
            print(f'  净资产: {row["total_equity"]/10000:.1f}亿')
        if row['equity_ratio'] and row['equity_ratio'] > 0:
            print(f'  权益比率: {row["equity_ratio"]:.1%}')
        print()
else:
    print('未获取到资产负债表数据')
    print()

# 5. 2026Q1业绩
print('5. 2026Q1业绩')
print('-' * 60)
df_q1 = pro.income(ts_code=ts_code, period='20260331', fields='ts_code,end_date,revenue,operate_profit,net_profit,deducted_profit')
if df_q1 is not None and len(df_q1) > 0:
    row = df_q1.iloc[0]
    print(f'{row["end_date"]}:')
    print(f'  营收: {row["revenue"]/10000:.1f}亿')
    print(f'  营业利润: {row["operate_profit"]/10000:.1f}亿')
    print(f'  净利润: {row["net_profit"]/10000:.1f}亿')
    print(f'  扣非净利润: {row["deducted_profit"]/10000:.1f}亿')
else:
    print('未获取到2026Q1数据')
print()

# 6. 成长性分析（近3年营收/净利润增速）
print('6. 成长性分析')
print('-' * 60)
income_all = pro.income(ts_code=ts_code, start_date='20200101', end_date='20251231', fields='ts_code,end_date,revenue,net_profit,deducted_profit')
if income_all is not None and len(income_all) > 0:
    income_all = income_all.sort_values('end_date')
    print('近3年营收/净利润增速:')
    for i in range(len(income_all)-1):
        row_cur = income_all.iloc[i]
        row_next = income_all.iloc[i+1]
        rev_growth = (row_next['revenue'] - row_cur['revenue']) / row_cur['revenue'] * 100 if row_cur['revenue'] > 0 else 0
        profit_growth = (row_next['net_profit'] - row_cur['net_profit']) / abs(row_cur['net_profit']) * 100 if row_cur['net_profit'] != 0 else 0
        print(f'  {row_cur["end_date"]}→{row_next["end_date"]}: 营收增速={rev_growth:.1f}%, 净利润增速={profit_growth:.1f}%')
    print()
else:
    print('未获取到成长性数据')
    print()

# 7. ROE分析
print('7. ROE分析')
print('-' * 60)
if income is not None and balance is not None and len(income) > 0 and len(balance) > 0:
    merged = pd.merge(income, balance, on='end_date', how='left')
    for i, row in merged.iterrows():
        if row['total_equity'] and row['total_equity'] > 0:
            roe = row['net_profit'] / row['total_equity'] * 100
            print(f'{row["end_date"]}: ROE={roe:.1f}%')
    print()
else:
    print('未获取到ROE数据')
    print()

# 8. 业绩预告
print('8. 业绩预告')
print('-' * 60)
try:
    forecast = pro.forecast(ts_code=ts_code, start_date='20260101', end_date='20260630')
    if forecast is not None and len(forecast) > 0:
        for i, row in forecast.iterrows():
            print(f'{row["ann_date"]}: {row["type"]}')
    else:
        print('未获取到业绩预告数据')
except Exception as e:
    print(f'获取业绩预告失败: {e}')
print()

print('=' * 60)
print('分析完成！')
print('=' * 60)
