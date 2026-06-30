"""
查询688135.SH股票信息
"""
import tushare as ts
import os

# 设置token
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()

# 查询股票基本信息
df = pro.stock_basic(ts_code='688135.SH', fields='ts_code,name,industry,market,list_date')
if df is not None and len(df) > 0:
    print(f'股票名称: {df.iloc[0]["name"]}')
    print(f'股票代码: {df.iloc[0]["ts_code"]}')
    print(f'所属行业: {df.iloc[0]["industry"]}')
    print(f'板块: {df.iloc[0]["market"]}')
    print(f'上市日期: {df.iloc[0]["list_date"]}')
else:
    print('未找到该股票')
