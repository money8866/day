# -*- coding: utf-8 -*-
"""probe40：决定性检验 —— 超额是不是"指数环境的函数"？

线索
  TRAIN 指数 -2.87%/年 → 超额 +
  VALID 指数 +16.93%/年 → 超额 -
  OOS   指数 -7.99%/年  → 超额 ++
  三段符号与指数收益**完全反向**。若属实，则"净值超额"不是一个策略属性，
  而是"低仓位 × 指数收益"的算术结果。

机制假设
  财报信号是脉冲式的：一年约 50 个入场日，每个 sleeve 只投 1/6 NAV，
  sleeve 存活 6 个交易日 → 平均并发 sleeve ≈ 0.2045/日 × 6 ≈ 1.23 个
  → 平均仓位 ≈ 1.23 / 6 ≈ 20%。
  20% 仓位下，取款能力只有 cohort alpha 的 1/5，于是指数涨跌直接决定超额符号。

本脚本只做测量（只读），不改任何口径与裁定。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log
from _fs_trade_gate2 import (load_px, top_decile, build_day_baskets,
                             simulate_nav, COST, MV_MIN, HOLD, SLOT)

log = Log('_fs_probe40.txt')

td, cmap, C, O, lvl = load_px()
df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
df = E.add_derived(df)
df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
df['ann_date'] = df['ann_date'].astype(str)
mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))


def rankpost(d, col):
    A = top_decile(d, col)
    return A[mv80.reindex(A.index).fillna(False).values].copy()


PER = {'TRAIN': ('20180101', '20231231'), 'VALID': ('20240101', '20251231'),
       'OOS': ('20260101', '20260930')}
NCAL = len(td)

variants = [('rev_acc_ALL', rankpost(df, 'rev_acc')),
            ('rev_acc_FY', rankpost(df, 'rev_acc')),
            ('B2_MV80_ALL', df[mv80].copy())]
variants[1] = ('rev_acc_FY', variants[1][1][
    variants[1][1]['ann_date'].str[4:6].isin({'01', '04', '07', '08', '10'})])

log('=' * 78)
log('S1 平均仓位（exposure）—— 计划：exposure_t = 并发sleeve数 / 6')
erows = []
for nm, ev in variants:
    for pn, (a, b) in PER.items():
        v = ev[(ev['ann_date'] >= a) & (ev['ann_date'] <= b)]
        kk = v['k1'].values.astype(int)
        if len(kk) == 0:
            continue
        conc = np.zeros(NCAL)
        for k in kk:
            conc[k:k + HOLD + 1] += 1
        m = np.array([(td[i] >= a) and (td[i] <= b) for i in range(NCAL)])
        expo = conc[m] / 6.0
        erows.append(dict(variant=nm, period=pn, n_cohort=len(kk),
                          days=int(m.sum()),
                          avg_concurrent=round(float(conc[m].mean()), 3),
                          avg_exposure=round(float(expo.mean()), 4),
                          full_days_pct=round(float((conc[m] >= 6).mean()) * 100, 1)))
log(pd.DataFrame(erows).to_string(index=False))

log('')
log('=' * 78)
log('S2 月度回归：策略月收益 = a + b × 沪深300月收益')
bl = pd.Series(lvl, index=pd.to_datetime(td))
mret = bl.resample('ME').last().pct_change().dropna()
for nm, ev in variants[:2]:
    rm, n, nd = build_day_baskets(ev, cmap, C, O, NCAL, COST)
    nav = simulate_nav(rm, NCAL)
    s = pd.Series(nav, index=pd.to_datetime(td)).dropna()
    sr = s.resample('ME').last().pct_change().dropna()
    X = pd.concat([sr.rename('y'), mret.rename('x')], axis=1).dropna()
    log('')
    log('  [%s] 全样本 %d 个月' % (nm, len(X)))
    for pn, (a, b) in list(PER.items()) + [('FULL', ('20180101', '20260930'))]:
        z = X[(X.index >= pd.to_datetime(a)) & (X.index <= pd.to_datetime(b))]
        if len(z) < 12:
            continue
        bb = np.polyfit(z['x'], z['y'], 1)
        yh = np.polyval(bb, z['x'])
        r2 = 1 - ((z['y'] - yh) ** 2).sum() / ((z['y'] - z['y'].mean()) ** 2).sum()
        log('    %-6s n=%-4d beta=%.3f  alpha=%+.3f%%/月 (%+.2f%%/年)  R2=%.3f  '
            'corr=%.3f'
            % (pn, len(z), bb[0], bb[1] * 100, ((1 + bb[1]) ** 12 - 1) * 100,
               r2, np.corrcoef(z['x'], z['y'])[0, 1]))
    z = X
    bb = np.polyfit(z['x'], z['y'], 1)
    log('    （FULL beta=%.3f → 隐含平均仓位 %.1f%%）' % (bb[0], bb[0] * 100))

log('')
log('=' * 78)
log('S3 逐期对照表：指数收益 vs 策略超额')
rec = []
rm, n, nd = build_day_baskets(variants[0][1], cmap, C, O, NCAL, COST)
nav = simulate_nav(rm, NCAL)
s = pd.Series(nav, index=td).dropna()
for pn, (a, b) in PER.items():
    x = s[(s.index >= a) & (s.index <= b)]
    b0 = pd.Series(lvl, index=td)
    b0 = b0[(b0.index >= a) & (b0.index <= b)]
    yrs = len(x) / 242.0
    sr = (x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1
    ir = (b0.iloc[-1] / b0.iloc[0]) ** (1 / yrs) - 1
    rec.append(dict(period=pn, index_cagr=ir * 100, strat_cagr=sr * 100,
                    excess=(sr - ir) * 100))
log(pd.DataFrame(rec).to_string(index=False, float_format=lambda v: '%.2f' % v))
log('  → 超额符号 = -(指数收益 - 策略取款能力)')
log.save()
print('DONE')
