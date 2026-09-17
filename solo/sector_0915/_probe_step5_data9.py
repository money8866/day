# -*- coding: utf-8 -*-
"""Step5 probe 9: breadth scale confirmation (throwaway)."""
import sys, os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
seos = pd.read_csv(os.path.join(BASE, 'data', 'sector_seos_daily.csv'), dtype={'trade_date': str})
d = seos[seos['trade_date'] == '20260703']
cols = ['breadth', 'core_breadth', 'primary_breadth', 'secondary_breadth',
        'breadth_delta_5', 'core_breadth_delta_5', 'primary_breadth_delta_3',
        'top5_concentration', 'top10_concentration', 'volume_ratio_5', 'volume_ratio_delta_5',
        'theme_amount_share', 'amount_share_delta_5', 'ret_dispersion_5',
        'ew_ret_5', 'ew_ret_10', 'ew_ret_20', 'theme_health', 'seos_score',
        'positive_contribution_ratio', 'improving_dim_ratio', 'weighted_breadth',
        'health_slope_5', 'rs_turn_5']
print(d[cols].describe().T[['min', '25%', '50%', '75%', 'max']].round(4).to_string())
print('\nbreadth raw values sample:', d['breadth'].head(12).tolist())
print('core_breadth sample:', d['core_breadth'].head(12).tolist())
print('primary_breadth sample:', d['primary_breadth'].head(12).tolist())
print('secondary_breadth sample:', d['secondary_breadth'].head(12).tolist())
print('top5_concentration sample:', d['top5_concentration'].head(12).tolist())
print('\nT10 wind chain rows all dates sample (breadth scale over time):')
t10 = seos[seos['sector_id'] == 'T10'].tail(6)
print(t10[['trade_date', 'theme_phase', 'seos_score', 'breadth', 'core_breadth',
           'primary_breadth', 'secondary_breadth', 'breadth_delta_5',
           'core_breadth_delta_5', 'ew_ret_5', 'top5_concentration']].to_string(index=False))
print('\nPROBE9 DONE')
