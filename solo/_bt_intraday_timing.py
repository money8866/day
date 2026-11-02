# -*- coding: utf-8 -*-
"""研究：日内各时点对判定的重要性

三个问题：
  Q1 各时点读数离收盘有多远（代表性）
  Q2 各时点读数对「当日最终方向」的解释力（决策价值）
  Q3 各时点读数对「次日方向」的解释力（跨日价值）
  Q4 盘中「假信号」风险：午盘看起来强但尾盘反转
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd

SRC = '_bt_out/index_5min_full.csv'
KEY = ['09:45','10:00','10:30','11:00','11:30','13:30','14:00','14:30','14:50','15:00']

df = pd.read_csv(SRC)
df = df.sort_values(['sym', 'trade_date', 'tm'])

def analyze(sym, label):
    d = df[df.sym == sym]
    piv = d.pivot_table(index='trade_date', columns='tm', values='close')
    if '15:00' not in piv.columns: return None
    full = piv.dropna(subset=[c for c in piv.columns if c >= '15:00'])
    if len(full) < 12: return None
    avail = [c for c in KEY if c in full.columns]
    out = []
    for c in avail:
        dev = (full[c] / full['15:00'] - 1) * 100
        chg_t = (full[c] / full[c].shift(1) - 1) * 100
        chg_c = (full['15:00'] / full['15:00'].shift(1) - 1) * 100
        j = pd.concat([chg_t, chg_c], axis=1).dropna()
        j.columns = ['t', 'c']
        corr = j.t.corr(j.c) if len(j) > 4 else np.nan
        same = ((j.t > 0) == (j.c > 0)).mean() if len(j) > 0 else np.nan
        # 假信号率：该时点为正但收盘为负
        fake = ((j.t > 0) & (j.c < 0)).mean() if len(j) > 0 else np.nan
        out.append({'sym': label, 'tm': c, 'n': len(full),
                    'dev_mean': dev.abs().mean(), 'dev_p90': dev.abs().quantile(0.9),
                    'corr': corr, 'same': same*100, 'fake': fake*100})
    return pd.DataFrame(out)

print('='*76)
print('  日内时点重要性研究（5分钟K线，指数）')
print('='*76)
res = []
for sym, nm in [('sh000001','上证指数'), ('sz399001','深证成指'),
                ('sz399006','创业板指'), ('sh000300','沪深300'),
                ('sh000905','中证500'), ('sh000852','中证1000')]:
    r = analyze(sym, nm)
    if r is not None: res.append(r)
R = pd.concat(res, ignore_index=True)
R.to_csv('_bt_out/intraday_timing.csv', index=False, encoding='utf-8')

n_days = R.n.max()
print(f'样本 {n_days} 个交易日 × {R.sym.nunique()} 个指数\n')
print('=== 各时点汇总（6 指数平均）===')
print(f"{'时点':<8} {'离收盘均值':>10} {'离收盘P90':>10} {'与收盘相关':>10} {'方向一致率':>10} {'假信号率':>9}")
agg = R.groupby('tm').agg(dev=('dev_mean','mean'), dev90=('dev_p90','mean'),
                            cc=('corr','mean'), same=('same','mean'), fake=('fake','mean'))
for tm in [c for c in KEY if c in agg.index]:
    a = agg.loc[tm]
    print(f"{tm:<8} {a['dev']:>9.2f}% {a['dev90']:>9.2f}% {a['cc']:>10.3f} {a['same']:>9.1f}% {a['fake']:>8.1f}%")

print('\n=== 关键分界：早盘 vs 午后 ===')
morning = [c for c in ['09:45','10:00','10:30','11:00','11:30'] if c in agg.index]
afternoon = [c for c in ['13:30','14:00','14:30','14:50'] if c in agg.index]
for grp, nm in [(morning,'早盘(09:45~11:30)'), (afternoon,'午后(13:30~14:50)')]:
    if not grp: continue
    a = agg.loc[grp]
    print(f"  {nm}: 离收盘 {a['dev'].mean():.2f}%  相关 {a['cc'].mean():.3f}  "
          f"方向一致 {a.same.mean():.1f}%  假信号 {a.fake.mean():.1f}%")

print('\n=== 假信号最高危的时点（午盘看着涨、收盘跌）===')
top = R.groupby('tm').agg(fake=('fake','mean'), same=('same','mean')).sort_values('fake', ascending=False)
for tm, r in top.head(4).iterrows():
    print(f"  {tm}: 假信号率 {r.fake:.1f}%  方向一致率 {r.same:.1f}%")
