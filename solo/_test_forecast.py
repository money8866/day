"""测试Tushare forecast API"""
import tushare as ts
from datetime import datetime, timedelta

pro = ts.pro_api()

today = datetime.now()
found = False
for i in range(30):
    d = (today - timedelta(days=i)).strftime('%Y%m%d')
    try:
        df = pro.forecast(ann_date=d, fields='ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,last_parent_net,summary')
        if df is not None and len(df) > 0:
            end_dates = df['end_date'].unique()
            print(f'{d}: {len(df)} records, end_dates={end_dates}')
            print(df.head(5)[['ts_code','type','p_change_min','p_change_max']].to_string())
            found = True
            break
    except Exception as e:
        print(f'{d}: {e}')
        found = True
        break

if not found:
    print('No forecast data found in recent 30 days')