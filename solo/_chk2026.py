import pandas as pd
import numpy as np

pd.set_option('display.width', 260)
pd.set_option('display.max_columns', 60)

df = pd.read_csv('d:/mystock/solo/output/edb/edb_backtest_20240601_20260918.csv', dtype={'ts_code': str})
df['date'] = df['date'].astype(str)
d26 = df[df['date'].str.startswith('2026')].copy()

cols = ['ts_code', 'name', 'industry', 'date', 'status', 'edb_score_adj', 'dry_up_ratio',
        'breakout_vr', 'breakout_distance', 'day_gain', 'close_position', 'days_after',
        'breakout_date', 'fut3', 'fut5', 'fut10', 'fut20', 'fut_max_dd']

act = d26[d26['status'].isin(['EDB', 'EDB_STRONG', 'EDB_PULLBACK', 'EDB_REBREAKOUT'])].sort_values(['ts_code', 'status', 'date'])
first = act.drop_duplicates(['ts_code', 'status'], keep='first')

print('=== A. 2026 可操作信号（每个 ts_code+status 取首个信号日 = 实际入场点）===')
print('去重后行数 =', len(first), '| status 分布:', first['status'].value_counts().to_dict())
print(first[cols].to_string(index=False))

print()
print('=== B. 民爆光电全部原始行 ===')
mb = d26[d26['name'].astype(str).str.contains('民爆', na=False)]
print(mb[cols].to_string(index=False))

print()
print('=== C. 其余个案全部原始行 ===')
for nm in ['和展', '香飘飘', '豪森', '众生', '四川美丰', '华孚', '银信', '天能']:
    sub = d26[d26['name'].astype(str).str.contains(nm, na=False)]
    if len(sub):
        print(f'--- {nm}（{len(sub)} 行）---')
        print(sub[cols].to_string(index=False))

print()
print('=== D. 成绩单（首个信号行，fut20 已到期）===')
m = first.dropna(subset=['fut20']).copy()
print(m[['name', 'status', 'date', 'fut3', 'fut5', 'fut10', 'fut20', 'fut_max_dd']].to_string(index=False))
print('fut20>0 笔数:', int((m['fut20'] > 0).sum()), '/', len(m))
m3 = m[~m['name'].astype(str).str.contains('民爆', na=False)]
print('剔除民爆光电后: fut20>0 笔数:', int((m3['fut20'] > 0).sum()), '/', len(m3))

print()
print('=== E. fut20 未到期的可操作信号 ===')
print(first[first['fut20'].isna()][['ts_code', 'name', 'status', 'date', 'days_after']].to_string(index=False))
