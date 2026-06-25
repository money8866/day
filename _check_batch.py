import tushare as ts, sys, os, json
pro = ts.pro_api()
# Check the batch download for 20260624 - get 长源东谷's row
df_all = pro.stk_factor_pro(trade_date='20260624', fields=['ts_code','trade_date','rsi_qfq_6','rsi_hfq_6','rsi_bfq_6','rsi_qfq_12','rsi_qfq_24','close','close_qfq','close_hfq','close_bfq'])
if df_all is not None and not df_all.empty:
    row = df_all[df_all['ts_code']=='603950.SH']
    if len(row):
        print('Batch download data for 603950.SH:')
        print(json.dumps(row.to_dict('records'), ensure_ascii=False, indent=2))
    else:
        print('603950.SH not found in batch download')
        print(f'Total records: {len(df_all)}')
else:
    print('Batch download returned empty')
