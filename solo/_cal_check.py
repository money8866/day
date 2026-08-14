# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from data_fetcher import DataFetcher
import importlib.util
spec = importlib.util.spec_from_file_location("main_config", r'D:\mystock\solo\multi_factor_picker\main.py')
main_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_mod)
load_config = main_mod.load_config
get_token = main_mod.get_token
config = load_config()
token = get_token(config)
fetcher = DataFetcher(token, config)

# 1. 最近交易日历
try:
    cal = fetcher.get_trade_cal(start_date='20260807', end_date='20260816')
    out = ['== 交易日历 ==']
    for _, r in cal.iterrows():
        out.append(f"{r['cal_date']} is_open={r.get('is_open')}")
except Exception as e:
    out = [f'cal err: {e}']

# 2. 高德红外最新 K 线日期
try:
    d = fetcher.get_daily_by_code('002414.SZ', start_date='20260810', end_date='20260816')
    out.append(f"\n== 002414 近K线 ==\n{d[['trade_date','close']].tail(6).to_string()}")
except Exception as e:
    out.append(f'daily err: {e}')

with open(r'D:\mystock\solo\_cal.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('done')
