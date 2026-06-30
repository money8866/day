# -*- coding: utf-8 -*-
"""用 pytdx 查询6/26和6/29信号股今日实时表现（完全参考 realtime_monitor.py）"""
import os
import pandas as pd
from pytdx.hq import TdxHq_API
from pytdx.config.hosts import hq_hosts
import time

# === 读取信号数据 ===
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
df = pd.read_csv(csv_path, dtype={'ts_code': str})

target_dates = ['20260626', '20260629']
all_codes = []
for dt in target_dates:
    sub = df[df['signal_date'] == int(dt)]
    for _, row in sub.iterrows():
        all_codes.append(str(row['ts_code']).strip())
all_codes = list(set(all_codes))
print(f'需要查询 {len(all_codes)} 只股票实时行情...')

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

# === 连接 pytdx（完全参考 realtime_monitor.py）===
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

if not connected:
    print('❌ 无法连接行情服务器')
    exit(1)

# === 获取实时行情（完全参考 fetch_realtime_snapshot）===
all_quotes = {}  # {ts_code: quote_dict}
not_found = []

for ts_code in all_codes:
    code6 = ts_code.split('.')[0].zfill(6)
    # market 判断（同 realtime_monitor.py）
    if code6[0] == '6' and not code6.startswith('688'):
        markets = [0, 1]  # 沪市主板先试0，再试1
    else:
        markets = [1, 0]  # 科创/深市先试1，再试0

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
        all_quotes[ts_code] = q
    else:
        not_found.append(ts_code)

api.disconnect()
print(f'✅ 获取到 {len(all_quotes)}/{len(all_codes)} 只实时行情')
if not_found:
    print(f'未获取到：{not_found}')

# === 计算并输出表现 ===
for dt in target_dates:
    sub = df[df['signal_date'] == int(dt)].sort_values('entry_score', ascending=False)
    print(f'\n{"="*72}')
    print(f'【{dt[:4]}-{dt[4:6]}-{dt[6:8]} 信号股 {len(sub)} 只 → 今日实时表现')
    print(f'{"="*72}')

    today_up = 0
    today_pcts = []

    for _, row in sub.iterrows():
        ts_code = str(row['ts_code']).strip()
        code6 = ts_code.split('.')[0].zfill(6)
        name = name_map.get(code6, '')
        entry_score = int(row['entry_score'])

        if ts_code not in all_quotes:
            print(f'  {code6} {name:8s}  评分{entry_score:3d}  ⚠ 实时行情未获取')
            continue

        q = all_quotes[ts_code]
        price = q.get('price', 0)
        last_close = q.get('last_close', 0)

        if last_close and last_close > 0:
            today_pct = (price - last_close) / last_close * 100
        else:
            today_pct = 0

        today_pcts.append(today_pct)
        if today_pct > 0:
            today_up += 1

        icon = '🔴' if today_pct < 0 else '🟢'
        print(f'  {icon} {code6} {name:8s}  评分{entry_score:3d}  今日{today_pct:+.2f}%  最新价{price:.2f}  昨收{last_close:.2f}')

    if today_pcts:
        avg_pct = sum(today_pcts) / len(today_pcts)
        print(f'\n  今日上涨: {today_up}/{len(today_pcts)}  平均涨幅: {avg_pct:.2f}%')
    else:
        print('  （无实时行情数据）')

print('\n完成。')
