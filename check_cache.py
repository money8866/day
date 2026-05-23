# -*- coding: utf-8 -*-
import pickle
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Load daily data
with open('cache_kpl/daily_20260521.pkl', 'rb') as f:
    df = pickle.load(f)
print(f'DAILY_DATE:{df["trade_date"].iloc[0]} RECORDS:{len(df)}')

# Our 5 stocks
targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']
df5 = df[df['ts_code'].isin(targets)]
print('TARGET_STOCKS:')
for _, r in df5.iterrows():
    print(f'{r["ts_code"]}|{r["close"]}|{r["pct_chg"]}|{r["vol"]}')

# Load concept
with open('cache_kpl/kpl_concept_20260521.pkl', 'rb') as f:
    df_con = pickle.load(f)
print(f'CONCEPTS:{len(df_con)}')

# Top hot concepts
df_hot = df_con.sort_values('hot_num', ascending=False)
print('TOP10_HOT_CONCEPTS:')
for _, r in df_hot.head(10).iterrows():
    print(f'HOT|{r["con_name"]}|{r["hot_num"]}|{str(r["desc"])[:60]}')

# Our stocks concepts
with open('cache_kpl/stock_basic.pkl', 'rb') as f:
    df_basic = pickle.load(f)
print('STOCKS_CONCEPTS:')
for code in targets:
    names = df_basic[df_basic['ts_code']==code]['name'].values
    name = names[0] if len(names)>0 else 'N/A'
    stk = df_con[df_con['ts_code']==code]
    print(f'STOCK|{code}|{name}|CONS:{len(stk)}')
    for _, r in stk.iterrows():
        print(f'  CON|{r["con_name"]}|{r["hot_num"]}|{str(r["desc"])[:60]}')