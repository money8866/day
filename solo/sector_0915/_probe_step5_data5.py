# -*- coding: utf-8 -*-
"""Step5 probe 5: value scales for ramp design (throwaway)."""
import json, sys, os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))

seos = pd.read_csv(os.path.join(BASE, 'data', 'sector_seos_daily.csv'))
cols = ['seos_score', 'theme_health', 'theme_health_delta_5', 'theme_health_delta_10',
        'breadth', 'breadth_delta_5', 'breadth_expansion_5', 'core_breadth', 'core_breadth_delta_5',
        'primary_breadth', 'secondary_breadth', 'relative_strength', 'relative_strength_delta_5',
        'volume_ratio_5', 'volume_ratio_delta_5', 'theme_amount_share', 'amount_share_delta_5',
        'ew_ret_5', 'ew_ret_10', 'ew_ret_20', 'ret_dispersion_5', 'top5_concentration',
        'data_quality_score', 'phase_duration', 'startup_quality', 'extension_penalty']
sub = seos[seos['theme_phase'].isin(['EARLY', 'EMERGING', 'CONFIRMING', 'STRONG'])]
print('=== active-phase rows:', len(sub), '===')
print(sub[cols].describe().T[['min', '25%', '50%', '75%', 'max']].round(4).to_string())

print('\n=== 20260703 themes sorted by seos_score (top 32) ===')
d = seos[seos['trade_date'] == '20260703']
print(d[['sector_id', 'sector_name', 'theme_phase', 'seos_score', 'theme_health',
         'breadth', 'core_breadth', 'rotation_signal']].sort_values('seos_score', ascending=False).head(32).to_string(index=False))

print('\n=== opportunity pool buckets ===')
with open(os.path.join(BASE, 'output', 'sector_opportunity_pool.json'), encoding='utf-8') as f:
    pool = json.load(f)
print('keys:', list(pool.keys()))
bk = pool.get('buckets', {})
for k, v in bk.items():
    ids = [x.get('sector_id') for x in (v if isinstance(v, list) else v.get('themes', []))] if v else []
    print(k, '->', ids)

print('\n=== CORE/PRIMARY member counts per sector (top 20) ===')
mem = pd.read_csv(os.path.join(BASE, 'data', 'sector_membership.csv'))
core = mem[mem['membership_type'].isin(['CORE', 'PRIMARY'])].groupby('sector_id').size().sort_values(ascending=False)
print(core.head(20).to_string())
print('\nall sectors CORE/PRIMARY count: min=%d median=%d max=%d' % (core.min(), core.median(), core.max()))

print('\n=== 20260703 active themes CORE/PRIMARY counts ===')
act_ids = d[d['theme_phase'].isin(['EARLY', 'EMERGING', 'CONFIRMING', 'STRONG'])]['sector_id'].tolist()
print({s: int(core.get(s, 0)) for s in act_ids})

print('\nPROBE5 DONE')
