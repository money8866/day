# -*- coding: utf-8 -*-
"""验证按 ts_code 拉取 report_rc 的效果（小样本测试）"""
import sys
sys.path.insert(0, '.')

from main import load_config, get_token, DataFetcher
from datetime import datetime

config = load_config()
token = get_token(config)
fetcher = DataFetcher(token, config)

# 只测5只已知有研报的股票
test_stocks = ['300308.SZ', '688256.SH', '603893.SH', '688313.SH', '601012.SH']

print(f"测试 {len(test_stocks)} 只股票，按 ts_code 拉全量历史...")
rc_map = fetcher.get_report_rc_batch(stock_list=test_stocks)
print(f"\n结果: {len(rc_map)} 只有效数据\n")

for code, data in sorted(rc_map.items(), key=lambda x: x[1]['analyst_count'], reverse=True):
    print(f"  {code}: analyst={data['analyst_count']:>3}家, np_growth={data['np_growth_current']*100:>6.1f}%, "
          f"buy={data['buy_ratio']*100:>5.1f}%, revision_30d={data['analyst_revision_30d']*100:>5.1f}%, "
          f"latest={data['latest_report_date']}")
