from dotenv import load_dotenv
load_dotenv()
import tushare as ts, os, pandas as pd
pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])
# 查看cyq_chips结构
df = pro.cyq_chips(ts_code='000729.SZ', trade_date='20260717')
print('=== cyq_chips 字段 ===')
print(df.columns.tolist())
print(df.head(5))
print(f'总行数: {len(df)}')
pct_sum = df['percent'].sum()
print(f'percent合计: {pct_sum:.2f}')
# 查看cyq_perf结构
perf = pro.cyq_perf(ts_code='000729.SZ', trade_date='20260717')
print('\n=== cyq_perf 字段 ===')
print(perf.columns.tolist())
print(perf)
