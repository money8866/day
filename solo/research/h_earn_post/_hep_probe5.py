# -*- coding: utf-8 -*-
"""_hep_probe5: 行业映射覆盖率侦察（为 §18 选择唯一口径）"""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import CD, DATA

d = pd.read_parquet(os.path.join(DATA, 'hep_events.parquet'), columns=['ts_code', 'rep_year'])
uni = set(d['ts_code'].astype(str).unique())
print('事件股票全集 %d 只' % len(uni))

p = os.path.join(CD, 'industry', 'sw_industry_map.csv')
sw = pd.read_csv(p, dtype=str)
print('sw_industry_map.csv 行=%d 唯一ts_code=%d 唯一l1=%d' % (
    len(sw), sw['ts_code'].nunique(), sw['l1_name'].nunique()))
print('  out_date 非空(已剔除)行=%d' % int(sw['out_date'].notna().sum()))
s1 = set(sw['ts_code'])
print('  与事件股票交集=%d / %d = %.4f' % (len(s1 & uni), len(uni), len(s1 & uni) / len(uni)))
print('  l1 分布 top12: %s' % sw['l1_name'].value_counts().head(12).to_dict())

p2 = os.path.join(CD, 'stock_basic.csv')
sb = pd.read_csv(p2, dtype=str)
print('stock_basic.csv 行=%d 唯一ts_code=%d 唯一industry=%d' % (
    len(sb), sb['ts_code'].nunique(), sb['industry'].nunique()))
s2 = set(sb['ts_code'])
print('  与事件股票交集=%d / %d = %.4f' % (len(s2 & uni), len(uni), len(s2 & uni) / len(uni)))
print('  industry 分布 top12: %s' % sb['industry'].value_counts().head(12).to_dict())
both = s1 & s2 & uni
print('  SW∩basic∩事件 = %d；仅 basic（无 SW）= %d'
      % (len(both), len((s2 & uni) - s1)))
print('  SW∩basic 内部一致性抽查（前 800 只，SW l1 vs basic industry 是否同名）:')
m = sw[sw['ts_code'].isin(both)][['ts_code', 'l1_name']].drop_duplicates('ts_code')
m = m.merge(sb[['ts_code', 'industry']], on='ts_code', how='left').head(800)
print('    同名比例 %.4f' % float((m['l1_name'] == m['industry']).mean()))

for f in ('dc_member_20260624.csv', 'dc_member_20260610.csv'):
    fp = os.path.join(CD, f)
    if os.path.exists(fp):
        dd = pd.read_csv(fp, dtype=str, nrows=5)
        print('%s 列=%s' % (f, list(dd.columns)))
        full = pd.read_csv(fp, dtype=str)
        print('   行=%d 唯一ts_code=%d 唯一板块=%d；与事件股票交集=%d'
              % (len(full), full['ts_code'].nunique(),
                 full.iloc[:, 1].nunique(), len(set(full['ts_code']) & uni)))
        if len(full.columns) > 1:
            print('   板块 top10: %s' % full.iloc[:, 1].value_counts().head(10).to_dict())
