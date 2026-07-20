"""临时回测脚本 - 全量412只对比基准 vs ma_35_20"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')

import pandas as pd
from daily_timing import _load_token
from multi_factor_picker.data_fetcher import DataFetcher
from backtest_timing import default_params, run_backtest, print_detail_report

token = _load_token()
config = {'cache':{'enabled':True,'dir':'cache'},'tushare':{'max_retry':3,'retry_delay':5}}
fetcher = DataFetcher(token, config)

df = pd.read_csv('report_daily/bull_stocks_qualified.csv')
daily = fetcher.get_daily_history('20260717', 180)
codes = set(str(c).strip().zfill(6) for c in df['code'])
ts_codes = set(f'{c}.SH' if c.startswith('6') else f'{c}.SZ' for c in codes)
daily = daily[daily['ts_code'].isin(ts_codes)].copy()
n_stocks = daily['ts_code'].nunique()
print(f'数据: {len(daily)}条, {n_stocks}只')

base_p = default_params()
opt_p = base_p.copy()
opt_p['trend_ma_strong'] = 35
opt_p['trend_ma_weak'] = 20

for name, params in [('基准参数', base_p), ('ma_35_20', opt_p)]:
    r = run_backtest(daily, params, name)
    print_detail_report(r, 10)

# 再测几个候选
candidates = [
    ('dip_ma_pct=2.0', {'dip_ma10_pct': 2.0, 'dip_ma20_pct': 4.0, 'dip_ma20_weak_pct': 7.0}),
]
for name, p_overrides in candidates:
    p = base_p.copy()
    p.update(p_overrides)
    r = run_backtest(daily, p, name)
    print_detail_report(r, 10)
