# -*- coding: utf-8 -*-
"""计算6/26信号股到今天的累计收益（D2表现）"""
import os
import pandas as pd
from datetime import datetime
from pytdx.hq import TdxHq_API
from pytdx.config.hosts import hq_hosts
import time

# === 读取信号数据 ===
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
df = pd.read_csv(csv_path, dtype={'ts_code': str})
target_dt = '20260626'
sub = df[df['signal_date'] == int(target_dt)].sort_values('entry_score', ascending=False)
print(f'6/26信号股共 {len(sub)} 只')

# === 获取名称映射 ===
name_map = {}
bull_csv = r'D:\mystock\solo\multi_factor_picker\output\bull_stocks_20260629_235153.csv'
if os.path.exists(bull_csv):
    bdf = pd.read_csv(bull_csv, dtype={'code': str})
    for _, row in bdf.iterrows():
        code = str(row['code']).strip().zfill(6)
        name = str(row['name']).strip()
        if name and name != 'nan':
            name_map[code] = name

try:
    import tushare as ts
    from dotenv import load_dotenv
    load_dotenv(r'D:\mystock\config\.env')
    import os as _os
    ts.set_token(_os.getenv('TUSHARE_TOKEN'))
    pro = ts.pro_api()
    stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
    for _, row in stock_basic.iterrows():
        code = str(row['symbol']).zfill(6)
        name = str(row['name']).strip()
        if code not in name_map and name:
            name_map[code] = name
except:
    pass

# === 获取6/26收盘价（信号日基准价）===
print('获取6/26收盘价...')
sig_close_map = {}
for _, row in sub.iterrows():
    ts_code = str(row['ts_code']).strip()
    try:
        sd = pro.daily(ts_code=ts_code, start_date=target_dt, end_date=target_dt)
        if not sd.empty:
            sig_close_map[ts_code] = sd.iloc[0]['close']
    except:
        pass
    time.sleep(0.06)

print(f'获取到 {len(sig_close_map)}/{len(sub)} 只6/26收盘价')

# === 连接pytdx获取今日实时价格 ===
api = TdxHq_API()
connected = False
for name, ip, port in hq_hosts:
    try:
        api.connect(ip, port)
        test = api.get_security_bars(1, 0, '000001', 0, 1)
        if test is not None:
            connected = True
            print(f'已连接行情服务器：{ip}:{port}')
            break
    except:
        continue

today_prices = {}
if connected:
    for ts_code in sig_close_map.keys():
        code6 = ts_code.split('.')[0].zfill(6)
        markets = [0, 1] if (code6[0] == '6' and not code6.startswith('688')) else [1, 0]
        q = None
        for m in markets:
            try:
                data = api.get_security_quotes([(m, code6)])
                if data and data[0] and data[0].get('price', 0) > 0:
                    q = data[0]
                    break
            except:
                pass
        if q:
            today_prices[ts_code] = q['price']
    api.disconnect()
    print(f'获取到 {len(today_prices)} 只今日实时价格')

# === 输出D2累计收益 ===
print(f'\n{"="*75}')
print(f'【6/26信号股 D2表现（信号日→今日累计收益）】')
print(f'{"="*75}')
print(f'{"代码":<10} {"名称":<10} {"评分":<6} {"信号日收盘":<12} {"今日最新价":<12} {"累计收益":<12} {"今日涨幅":<10}')
print(f'{"-"*75}')

results = []
for _, row in sub.iterrows():
    ts_code = str(row['ts_code']).strip()
    code6 = ts_code.split('.')[0].zfill(6)
    name = name_map.get(code6, '')
    entry_score = int(row['entry_score'])
    sig_ret_1d = row['return_1d']  # 信号日当天收益

    sig_close = sig_close_map.get(ts_code)
    today_price = today_prices.get(ts_code)

    if sig_close and today_price:
        cum_ret = (today_price - sig_close) / sig_close * 100
        # 今日涨幅（vs昨收6/27）
        results.append({
            'ts_code': ts_code, 'code6': code6, 'name': name,
            'score': entry_score, 'sig_close': sig_close,
            'today_price': today_price, 'cum_ret': cum_ret,
        })

if results:
    results.sort(key=lambda x: x['cum_ret'], reverse=True)
    up = sum(1 for r in results if r['cum_ret'] > 0)
    avg = sum(r['cum_ret'] for r in results) / len(results)
    print(f'  （{up}/{len(results)} 只盈利，平均累计收益 {avg:.2f}%）\n')
    for r in results:
        icon = '🟢' if r['cum_ret'] > 0 else '🔴'
        print(f'  {icon} {r["code6"]}  {r["name"]:<10s}  评分{r["score"]:3d}  信号日{r["sig_close"]:7.2f}  今日{r["today_price"]:7.2f}  {r["cum_ret"]:+.2f}%')
else:
    print('  （无完整数据）')

print(f'\n{"="*75}')
print('完成。')
