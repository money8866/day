import os, tushare as ts, pandas as pd
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()
# 查moneyflow列名
mf = pro.moneyflow(ts_code='603379.SH', start_date='20260601', end_date='20260620')
if mf is not None:
    print('moneyflow列名:', mf.columns.tolist())
    print(mf.head(2).to_string())
print()
# 查stk_holdernumber列名
hn = pro.stk_holdernumber(ts_code='603379.SH', limit=2)
if hn is not None:
    print('\nstk_holdernumber列名:', hn.columns.tolist())
    print(hn.head(2).to_string())
print()
# 查fund_portfolio列名  
fp = pro.fund_portfolio(ts_code='603379.SH', limit=2)
if fp is not None:
    print('\nfund_portfolio列名:', fp.columns.tolist())
    print(fp.head(2).to_string())
