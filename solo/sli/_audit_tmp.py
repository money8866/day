import sys
sys.path.insert(0, r'd:\mystock\solo')
from sli.config import CACHE_DIR
import glob
import os
import pandas as pd

fs = sorted(glob.glob(os.path.join(CACHE_DIR, 'daily_*.parquet')))
print('CACHE_DIR =', CACHE_DIR)
print('daily files =', len(fs), '| last =', os.path.basename(fs[-1]) if fs else None)

t5 = pd.read_csv(r'd:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.csv', low_memory=False)
fu = pd.read_csv(r'd:\mystock\solo\sli\output\sli_full_20260901.csv', low_memory=False)

print('\n[top5 columns]', list(t5.columns)[:30])
print('[full columns]', list(fu.columns)[:40])

print('\n[coverage %] top5:')
for c in ['Product', 'Purity', 'Dominance', 'SLI_V2', '龙头类型', '生命周期']:
    print(f'  {c}: {t5[c].notna().mean()*100:.1f}%' if c in t5.columns else f'  {c}: MISSING')
print('[coverage %] full:')
for c in ['roe_dt', 'ocf_to_profit', 'or_yoy', 'dt_netprofit_yoy', 'q_profit_yoy',
          'g1', 'g2', 'g3', 'pe_ttm', 'lifecycle', 'netprofit_margin']:
    print(f'  {c}: {fu[c].notna().mean()*100:.1f}%' if c in fu.columns else f'  {c}: MISSING')
