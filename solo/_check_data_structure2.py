import json, os
import pandas as pd

# 3. 东财板块数据
cache_path = 'd:/mystock/solo/cache_backbone_tushare/dc_all_members.csv'
df = pd.read_csv(cache_path)
print('=== East Money board data ===')
print(f'Shape: {df.shape}')
print(f'Columns: {list(df.columns)}')
print(f'\nSample row:')
print(df.iloc[0].to_dict())

# 找几个关键股票的板块归属
print('\n=== 典型股票板块归属 ===')
for name in ['中科曙光', '万丰奥威', '博纳影业', '麦迪科技', '沪硅产业']:
    rows = df[df['name'] == name]
    print(f'\n{name} ({len(rows)} boards):')
    for _, r in rows.head(10).iterrows():
        print(f'  {r["concept_name"]} ({r["ts_code"]})')
