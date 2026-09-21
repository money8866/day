# -*- coding: utf-8 -*-
"""全期成绩单：均值 vs 中位数、离群值敏感性（backtest-expert 纪律）"""
import pandas as pd

pd.set_option('display.width', 220)

df = pd.read_csv('d:/mystock/solo/output/edb/edb_backtest_20240601_20260918.csv', dtype={'ts_code': str})
act = df[df['status'].isin(['EDB', 'EDB_STRONG', 'EDB_PULLBACK', 'EDB_REBREAKOUT'])].copy()
act['date'] = act['date'].astype(str)
act['year'] = act['date'].str[:4]
outlier = (act['ts_code'] == '301362.SZ') & (act['date'] == '20260116')
print(f"全期可操作信号总行数: {len(act)}  其中民爆光电20260116: {int(outlier.sum())} 行")


def stats(g, label):
    x = g['fut20'].dropna()
    xex = g.loc[~outlier.reindex(g.index, fill_value=False), 'fut20'].dropna()
    print(f"{label}: n={len(x):3d}  mean={x.mean():+7.2f}%  median={x.median():+6.2f}%  "
          f"win={(x > 0).mean() * 100:3.0f}%  |  剔除民爆: n={len(xex):3d}  mean={xex.mean():+6.2f}%  "
          f"median={xex.median():+6.2f}%  win={(xex > 0).mean() * 100:3.0f}%")


print()
print('=== fut20 口径，按年（含 / 剔除 民爆20260116）===')
stats(act, '全期    ')
for y, g in act.groupby('year'):
    stats(g, f'{y}年   ')

print()
print('=== 按状态（全期）===')
for s, g in act.groupby('status'):
    x = g['fut20'].dropna()
    xex = g.loc[~outlier.reindex(g.index, fill_value=False), 'fut20'].dropna()
    print(f"{s:16s} n={len(x):3d}  mean={x.mean():+7.2f}%  median={x.median():+6.2f}%  "
          f"win={(x > 0).mean() * 100:3.0f}%  | 剔民爆 mean={xex.mean():+6.2f}%  median={xex.median():+6.2f}%")

print()
print('=== fut20 前 8 名（看离群值集中度）===')
top = act.nlargest(8, 'fut20')[['ts_code', 'status', 'date', 'fut3', 'fut20']]
print(top.to_string(index=False))

matured = act['fut20'].dropna()
print()
print(f"全期 fut20 均值 {matured.mean():+.2f}%，剔除民爆后 {matured[~(outlier & matured.notna())].mean():+.2f}%")
print(f"民爆一行对全期均值的贡献: {(142.06 - matured.mean()) / len(matured):+.2f} 个百分点 / 总均值")
