import tushare as ts, json
pro = ts.pro_api()
# 查最近几个交易日的数据
for d in ['20260625','20260624','20260623','20260622','20260621','20260620','20260619']:
    df = pro.stk_factor_pro(ts_code='603950.SH', trade_date=d, fields='ts_code,trade_date,rsi_qfq_6,rsi_qfq_12,rsi_qfq_24')
    if len(df):
        print(json.dumps(df.to_dict('records'), ensure_ascii=False, indent=2))
