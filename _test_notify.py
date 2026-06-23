# Quick smoke test: 10 stocks, wave2 scanner only
import os, sys, time, datetime, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
import wave2_daily as wd

sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
mask = sb['ts_code'].str.startswith(('600', '601', '603', '000', '002', '300'))
stocks = sb[mask]['ts_code'].tolist()[:10]

results = []
for code in stocks:
    r = wd.scan_stock(code, lookback=90)
    if r:
        results.append(r)
        print(f'SIGNAL: {r["ts_code"]} | {r["pattern"]} | {r["combo"]}')
    time.sleep(0.12)

print(f'\nSignals: {len(results)} / {len(stocks)}')
