# -*- coding: utf-8 -*-
import sqlite3, numpy as np, pandas as pd
DB=r'D:/mystock/cache_daily/stock_data.db'
c=sqlite3.connect(DB)
df=pd.read_sql("""select d.ts_code,d.trade_date,d.close,d.pre_close,d.pct_chg,a.adj_factor
  from daily_cache d join adj_factor_cache a on a.ts_code=d.ts_code and a.trade_date=d.trade_date
  where d.trade_date between '20230101' and '20231231'""",c)
print('rows',len(df))
df=df.sort_values(['ts_code','trade_date'])
df['adj_prev']=df.groupby('ts_code')['adj_factor'].shift(1)
df['ratio']=df['adj_factor']/df['adj_prev']
ex=df[(df['ratio']-1).abs()>0.0005].copy()
print('corp action days:',len(ex))
print('pct_chg on those days: mean %.4f median %.4f  pct<-5%%: %.2f%%'%(ex['pct_chg'].mean(),ex['pct_chg'].median(),(ex['pct_chg']<-5).mean()*100))
# 对比：这些天 raw return vs adj return
ex['ret_raw']=ex['close']/ex['pre_close']-1
ex['close_prev_raw']=df.groupby('ts_code')['close'].shift(1).loc[ex.index]
ex['ret_adj']=ex['close']*ex['adj_factor']/(ex['close_prev_raw']*ex['adj_prev'])-1
print('mean ret_raw %.4f  mean ret_adj %.4f  mean pct/100 %.4f'%(ex['ret_raw'].mean(),ex['ret_adj'].mean(),(ex['pct_chg']/100).mean()))
print(ex[['ts_code','trade_date','close','pre_close','close_prev_raw','pct_chg','adj_factor','adj_prev']].head(8).to_string(index=False))
