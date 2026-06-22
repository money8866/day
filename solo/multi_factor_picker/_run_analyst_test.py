# -*- coding: utf-8 -*-
"""测试 report_rc 集成后的 BullScore 全流程"""
import sys
sys.path.insert(0, '.')

from main import load_config, DataFetcher, bull_scan
from datetime import datetime

config = load_config()
from main import get_token
token = get_token(config)
fetcher = DataFetcher(token, config)
results = bull_scan(config, fetcher)

ts = datetime.now().strftime('%Y%m%d_%H%M%S')

print('\n\n=== 最终 Top 30 ===')
for i, r in enumerate(results[:30], 1):
    print(f'{i:>3}. {r.name:<10} chain={r.chain_tag:<12} Bull={r.bull_score:>5.1f} Final={r.final_score:>5.1f} lv={r.bull_level}')
    print(f'       NP={r.profit_yoy*100:>5.1f}% ROE={r.roe:>4.1f}% GM={r.gross_margin:>4.1f}%')
    print(f'       analyst={r.analyst_count:>2}家 np_growth={r.np_growth_current:>5.1f}% buy={r.buy_ratio:>5.1f}% rev30d={r.analyst_revision_30d:>5.1f}% exp_score={r.analyst_expectation_score:>5.1f}')

# 输出CSV
from bull_scorer import BullScorer
bs = BullScorer(config, fetcher)
df = bs.to_dataframe(results)
import os
os.makedirs('output', exist_ok=True)
out_path = f'output/bullscore_{ts}.csv'
df.to_csv(out_path, index=False, encoding='utf-8-sig')
print(f'\nCSV已保存: {out_path}, {len(df)} 行, {len(df.columns)} 列')
print('最后 5 列:', list(df.columns[-7:]))
