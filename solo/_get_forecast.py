"""批量获取中报预告数据"""
import tushare as ts
import pandas as pd
from datetime import datetime, timedelta
import time

pro = ts.pro_api()

# 查询最近90天所有已发布的中报预告（end_date=20260630）
# 也查一下一季报预告（end_date=20250331）
all_dfs = []
for end_date in ['20260630', '20250331']:
    today = datetime.now()
    for i in range(90):
        d = (today - timedelta(days=i)).strftime('%Y%m%d')
        try:
            df = pro.forecast(ann_date=d, end_date=end_date,
                              fields='ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,last_parent_net,summary')
            if df is not None and len(df) > 0:
                all_dfs.append(df)
                print(f'{d} ({end_date}): {len(df)} records')
        except Exception:
            pass
        time.sleep(0.13)

if all_dfs:
    df_all = pd.concat(all_dfs).drop_duplicates(subset=['ts_code','end_date']).reset_index(drop=True)
    print(f'\nTotal unique records: {len(df_all)}')
    print(f'End date distribution:')
    print(df_all['end_date'].value_counts().to_string())
    print(f'Type distribution:')
    print(df_all['type'].value_counts().to_string())
    df_all.to_csv('d:\\mystock\\solo\\report_daily\\forecast_all.csv', index=False, encoding='utf-8-sig')
    print('\nSaved to forecast_all.csv')
else:
    print('No data')