"""
利扬芯片（688135.SH）基本面分析 v2
使用正确的Tushare字段名。
"""

import tushare as ts
import os
import pandas as pd
from datetime import datetime

os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
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
    print(f'板块: {b["market"]}')
    print(f'上市日期: {b["list_date"]}')
    
    # 获取最新市值（从日线数据）
    df_daily = pro.daily(ts_code=ts_code, trade_date='20260629', fields='ts_code,trade_date,close,vol,amount')
    if df_daily is not None and len(df_daily) > 0:
        close = df_daily.iloc[0]['close']
        # 总股本从stock_basic获取
        basic_info = pro.stock_basic(ts_code=ts_code, fields='ts_code,total_share,circ_share')
        if basic_info is not None and len(basic_info) > 0:
            total_share = basic_info.iloc[0]['total_share']  # 单位：万股
            circ_share = basic_info.iloc[0]['circ_share']    # 单位：万股
            if total_share and total_share > 0:
                total_mv = close * total_share / 10000  # 亿元
                print(f'总市值: {total_mv:.1f}亿')
            if circ_share and circ_share > 0:
                circ_mv = close * circ_share / 10000    # 亿元
                print(f'流通市值: {circ_mv:.1f}亿')
    print()

# 2. 近3年利润表
print('2. 近3年利润表（年报）')
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

# 3. 近3年资产负债表
print('3. 近3年资产负债表（年报）')
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

# 4. 近3年现金流量表
print('4. 近3年现金流量表（年报）')
print('-' * 60)
cashflow = pro.cashflow(ts_code=ts_code, period='20231231,20221231,20211231', fields='ts_code,end_date,net_profit,cashflow_from_operate,cashflow_from_invest,cashflow_from_finance')
if cashflow is not None and len(cashflow) > 0:
    for i, row in cashflow.iterrows():
        print(f'{row["end_date"]}:')
        print(f'  净利润: {row["net_profit"]/10000:.1f}亿')
        if row['cashflow_from_operate'] and row['cashflow_from_operate'] != 0:
            print(f'  经营现金流: {row["cashflow_from_operate"]/10000:.1f}亿')
        if row['cashflow_from_invest'] and row['cashflow_from_invest'] != 0:
            print(f'  投资现金流: {row["cashflow_from_invest"]/10000:.1f}亿')
        if row['cashflow_from_finance'] and row['cashflow_from_finance'] != 0:
            print(f'  筹资现金流: {row["cashflow_from_finance"]/10000:.1f}亿')
        print()
else:
    print('未获取到现金流量表数据')
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

# 7. 盈利能力指标（ROE/ROA/毛利率）
print('7. 盈利能力指标')
print('-' * 60)
# 从利润表和资产负债表计算ROE
if income is not None and balance is not None and len(income) > 0 and len(balance) > 0:
    # 合并数据
    merged = pd.merge(income, balance, on='end_date', how='left')
    for i, row in merged.iterrows():
        if row['total_equity'] and row['total_equity'] > 0:
            roe = row['net_profit'] / row['total_equity'] * 100
            print(f'{row["end_date"]}: ROE={roe:.1f}%')
    print()
else:
    print('未获取到盈利能力数据')
    print()

# 8. 机构持仓（公募/北向/社保）
print('8. 机构持仓')
print('-' * 60)
# 北向资金
try:
    floating_holders = pro.floating_holders(ts_code=ts_code, start_date='20260301', end_date='20260630')
    if floating_holders is not None and len(floating_holders) > 0:
        # 筛选北向资金
        nb_holders = floating_holders[floating_holders['holder_name'].str.contains('香港中央结算|北上资金|陆股通')]
        if len(nb_holders) > 0:
            print('北向资金持仓:')
            for i, row in nb_holders.iterrows():
                print(f'  {row["ann_date"]}: 持股数={row["hold_amount"]}股, 持股比例={row["hold_ratio"]:.2%}')
        else:
            print('未找到北向资金持仓数据')
    else:
        print('未获取到机构持仓数据')
except Exception as e:
    print(f'获取机构持仓数据失败: {e}')
print()

# 9. 业绩预告/快报
print('9. 业绩预告/快报')
print('-' * 60)
try:
    forecast = pro.forecast(ts_code=ts_code, start_date='20260101', end_date='20260630')
    if forecast is not None and len(forecast) > 0:
        for i, row in forecast.iterrows():
            print(f'{row["ann_date"]}: {row["type"]}, 净利润区间: [{row["min"]/10000:.1f}亿, {row["max"]/10000:.1f}亿]')
    else:
        print('未获取到业绩预告数据')
except Exception as e:
    print(f'获取业绩预告数据失败: {e}')
print()

# 10. 估值水平（PE/PB）
print('10. 估值水平')
print('-' * 60)
try:
    # 获取最近交易日数据
    df_daily_basic = pro.daily_basic(ts_code=ts_code, trade_date='20260629', fields='ts_code,trade_date,pe_ttm,pb,ps_ttml,total_mv,circ_mv')
    if df_daily_basic is not None and len(df_daily_basic) > 0:
        row = df_daily_basic.iloc[0]
        print(f'PE(TTM): {row["pe_ttm"]:.1f}' if row['pe_ttm'] and row['pe_ttm'] > 0 else 'PE(TTM): N/A')
        print(f'PB: {row["pb"]:.1f}' if row['pb'] and row['pb'] > 0 else 'PB: N/A')
        print(f'PS(TTM): {row["ps_ttml"]:.1f}' if row['ps_ttml'] and row['ps_ttml'] > 0 else 'PS(TTM): N/A')
        print(f'总市值: {row["total_mv"]/10000:.1f}亿' if row['total_mv'] and row['total_mv'] > 0 else '总市值: N/A')
        print(f'流通市值: {row["circ_mv"]/10000:.1f}亿' if row['circ_mv'] and row['circ_mv'] > 0 else '流通市值: N/A')
    else:
        print('未获取到估值数据')
except Exception as e:
    print(f'获取估值数据失败: {e}')
print()

print('=' * 60)
print('分析完成！')
print('=' * 60)
