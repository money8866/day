# Step 1: Batch pre-fetch all missing financial data using Tushare batch API
import os, pickle, sys, time
sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts
from pathlib import Path
import pandas as pd

BASE = r'C:\Users\kongx\mystock'
FCACHE = os.path.join(BASE, 'fin_cache_v4.pkl')

# Load existing cache
fcache_data = pickle.load(open(FCACHE, 'rb')) if os.path.exists(FCACHE) else {}
cached_tcs = set(fcache_data.keys())
print(f'Already cached: {len(cached_tcs)}', flush=True)

env = Path(os.path.join(BASE, '.env')).read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=', 1)[1].strip())
pro = ts.pro_api()

START, END = '20241125', '20260522'
cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=START, end_date=END)
dates = sorted(cal['cal_date'].tolist())
print(f'Trade days: {len(dates)}', flush=True)

MV_MIN, MV_MAX, PE_MAX = 10, 5000, 80
all_tcs = set()
print('Scanning daily_basic...', flush=True)
for i in range(0, len(dates), 30):
    batch = dates[i:i+30]
    for td in batch:
        try:
            df = pro.daily_basic(trade_date=td, fields='ts_code,close,pe,total_mv')
            if not df.empty:
                rows = df[
                    (df['total_mv']/10000 >= MV_MIN) &
                    (df['total_mv']/10000 <= MV_MAX) &
                    (~df['ts_code'].str.startswith(('8','4','9'))) &
                    (df['pe'] > 0) & (df['pe'] <= PE_MAX)
                ]
                all_tcs.update(rows['ts_code'].tolist())
        except: pass
    if (i+30) % 90 == 0:
        print(f'  {i+30}/{len(dates)}: {len(all_tcs)} candidates', flush=True)

print(f'Total: {len(all_tcs)} candidates', flush=True)
missing = sorted(all_tcs - cached_tcs)
print(f'Missing fin: {len(missing)} stocks', flush=True)

if not missing:
    print('All done!')
else:
    BATCH = 50
    fetched = 0; errors = 0
    print(f'Batch fetching fin (BATCH={BATCH}, ~{len(missing)/BATCH*0.5:.0f}s estimated)...', flush=True)
    for bi in range(0, len(missing), BATCH):
        batch = missing[bi:bi+BATCH]
        tcs_str = ','.join(batch)
        try:
            df = pro.fina_indicator(ts_code=tcs_str, period='20260331',
                fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
            if not df.empty:
                for _, row in df.iterrows():
                    fcache_data[row['ts_code']] = row.to_dict()
                    fetched += 1
        except Exception as e:
            errors += 1
        if (bi+BATCH) % 200 == 0 or bi+BATCH >= len(missing):
            pickle.dump(fcache_data, open(FCACHE, 'wb'))
            print(f'  {bi+BATCH}/{len(missing)}: fetched={fetched} errors={errors}', flush=True)
        time.sleep(0.3)

    pickle.dump(fcache_data, open(FCACHE, 'wb'))
    print(f'Done! {fetched} new, {errors} errors. Total: {len(fcache_data)}', flush=True)
