"""
查询688135.SH股票信息
"""
import tushare as ts
import os

# 设置token
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
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
