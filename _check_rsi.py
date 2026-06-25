import tushare as ts, json
pro = ts.pro_api()
df = pro.stk_factor_pro(ts_code='603950.SH', trade_date='20260624', fields='ts_code,trade_date,rsi_qfq_6,rsi_qfq_12,rsi_qfq_24')
print(json.dumps(df.to_dict('records'), ensure_ascii=False, indent=2))
df2 = pro.stk_factor_pro(ts_code='603950.SH', trade_date='20260625', fields='ts_code,trade_date,rsi_qfq_6,rsi_qfq_12,rsi_qfq_24')
print(json.dumps(df2.to_dict('records'), ensure_ascii=False, indent=2))
