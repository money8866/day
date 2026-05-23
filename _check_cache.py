import sqlite3, os, pickle
db = r'C:\Users\kongx\mystock\cache_daily\hot_sector.db'
conn = sqlite3.connect(db)
cur = conn.execute("SELECT COUNT(*) FROM hot_sector WHERE date=(SELECT MAX(date) FROM hot_sector)")
print('Hot sectors at latest:', cur.fetchone()[0])
cur = conn.execute("SELECT MIN(date), MAX(date) FROM hot_sector")
row = cur.fetchone()
print(f'Date range: {row[0]} ~ {row[1]}')
conn.close()

cache = r'C:\Users\kongx\mystock\basic_cache.pkl'
if os.path.exists(cache):
    with open(cache,'rb') as f: d = pickle.load(f)
    print(f'Daily basic cache: {len(d)} entries')

# Check TDX data quality - compare first day price vs 360th day for same stock
import struct, pandas as pd

def parse_day_file(path):
    records = []
    with open(path, 'rb') as f:
        while True:
            data = f.read(32)
            if len(data) < 32: break
            date, o, h, l, c = struct.unpack('<IIIII', data[:20])
            if date == 0: break
            records.append({'date': date, 'close': c/100.0})
    return pd.DataFrame(records)

# Test: sh600519 (Kweichow Moutai)
p = r'C:\new_tdx\vipdoc\sh\lday\sh600519.day'
df = parse_day_file(p)
df['date_str'] = df['date'].apply(lambda x: f"{x//10000}-{x%10000//100:02d}-{x%100:02d}")
df = df.sort_values('date')
print(f'\nsh600519: {len(df)} days, first={df.iloc[0]["date_str"]} close={df.iloc[0]["close"]}, last={df.iloc[-1]["date_str"]} close={df.iloc[-1]["close"]}')