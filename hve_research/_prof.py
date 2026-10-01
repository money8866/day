import sqlite3,time,numpy as np,pandas as pd,importlib.util,sys
spec=importlib.util.spec_from_file_location('m','01_build_panel.py'); m=importlib.util.module_from_spec(spec); sys.argv=['x']; spec.loader.exec_module(m)
con=sqlite3.connect(m.DB)
codes=[r[0] for r in con.execute('select distinct ts_code from daily_cache order by ts_code limit 200 offset 1000')]
t=time.time()
ph=','.join('?'*len(codes))
sql=f"""select d.ts_code,d.trade_date,d.open,d.high,d.low,d.close,d.pre_close,d.pct_chg,d.vol,d.amount,
 b.turnover_rate,b.total_mv,b.circ_mv from daily_cache d
 left join daily_basic_cache b on b.ts_code=d.ts_code and b.trade_date=d.trade_date
 where d.ts_code in ({ph}) order by d.ts_code, d.trade_date"""
df=pd.read_sql(sql,con,params=codes)
print('sql',round(time.time()-t,2),'rows',len(df))
t=time.time()
n=0
for code,g in df.groupby('ts_code',sort=False):
    g=g.sort_values('trade_date').reset_index(drop=True)
    n+=1
    if n<=20: m.compute_stock(g)
print('compute20',round(time.time()-t,2),'stocks',n)
