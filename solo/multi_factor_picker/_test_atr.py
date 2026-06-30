# -*- coding: utf-8 -*-
import tushare as ts
for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
ts.set_token(token)
pro = ts.pro_api()
df = pro.stk_factor_pro(ts_code='300773.SZ', start_date='20260623', end_date='20260623')
row = df.iloc[0]
print(f'close_bfq: {row["close"]}')
print(f'close_qfq: {row["close_qfq"]}')
print(f'atr_bfq: {row["atr_bfq"]}')
print(f'atr_qfq: {row["atr_qfq"]}')
print(f'atr_bfq/close_bfq = {row["atr_bfq"]/row["close"]*100:.2f}%')
print(f'atr_qfq/close_qfq = {row["atr_qfq"]/row["close_qfq"]*100:.2f}%')
