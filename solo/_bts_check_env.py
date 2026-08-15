import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3, pandas as pd

conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
df = pd.read_sql_query(
    "SELECT trade_date,open,high,low,close,pre_close,pct_chg,vol,amount FROM daily_cache "
    "WHERE ts_code='300404.SZ' AND trade_date>='20260501' ORDER BY trade_date", conn)
conn.close()
print('300404 since 20260501:', len(df))
print(df.tail(30).to_string())

tdx = r'C:\new_tdx\vipdoc\sh\lday'
print('\nindex files:', [f for f in os.listdir(tdx) if f in ('sh999999.day','sh000001.day')])

sys.path.insert(0, r'd:\mystock\solo')
from tail_backtest_tdx import parse_tdx_day_file
tdf = parse_tdx_day_file(r'C:\new_tdx\vipdoc\sh\lday\sh999999.day')
print('sh999999 range:', tdf['trade_date'].min(), '->', tdf['trade_date'].max(), 'rows:', len(tdf))

# TDX vs daily_cache 单位一致性（300404 @20260804/0807）
t404 = parse_tdx_day_file(r'C:\new_tdx\vipdoc\sz\lday\sz300404.day')
for d in ('20260804', '20260807'):
    trow = t404[tdf0['trade_date'] == d] if False else t404[t404['trade_date'] == d]
    drow = df[df['trade_date'] == d]
    if len(trow) and len(drow):
        print(f"{d}: TDX close={trow.iloc[0]['close']} vol={trow.iloc[0]['vol']:.0f} amt={trow.iloc[0]['amount']:.0f} | "
              f"DB close={drow.iloc[0]['close']} vol={drow.iloc[0]['vol']:.0f} amt={drow.iloc[0]['amount']:.0f}")
