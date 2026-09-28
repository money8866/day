# -*- coding: utf-8 -*-
"""probe41：核算 sleeve 记账法的真实杠杆与净值口径一致性

疑点
  probe40 显示 rev_acc_ALL 平均并发 sleeve = 12~24 个，每个投 1/6 NAV
  → 名义敞口 = 并发数/6 = 200%~394%。B2_MV80_ALL 更是 29~38 倍。
  若真如此，净值应随事件股漂移爆炸式增长，但实测 VALID 全事件篮子只有 +6.83%/年。

  → 必须直接测量，判断是「记账法有隐性杠杆」还是「我的敞口推算写错了」。

做法
  内联重实现 simulate_nav，逐日输出：当日 P&L、净值、名义敞口 Σnt/NAV、
  当日加权篮子收益、当日新开 sleeve 数。只读，不改任何既有文件。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log
from _fs_trade_gate2 import (load_px, top_decile, build_day_baskets,
                             COST, MV_MIN, HOLD, SLOT)

log = Log('_fs_probe41.txt')

td, cmap, C, O, lvl = load_px()
df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
df = E.add_derived(df)
df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
df['ann_date'] = df['ann_date'].astype(str)
df['ym'] = [td[k][:6] for k in df['k1']]
mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))


def run(ev, tag):
    rm, n, nd = build_day_baskets(ev, cmap, C, O, len(td), COST)
    NCAL = len(td)
    nav = np.full(NCAL, np.nan)
    nav[0] = 1.0
    live = []
    lev = np.zeros(NCAL)
    pnl_s = np.zeros(NCAL)
    new_s = np.zeros(NCAL)
    for t in range(1, NCAL):
        pnl = 0.0
        for nt, exp, dm in live:
            v = dm.get(t)
            if v is not None:
                pnl += nt * v
        pnl_s[t] = pnl
        nav[t] = nav[t - 1] + pnl
        lev[t] = sum(x[0] for x in live) / nav[t - 1] if nav[t - 1] > 0 else np.nan
        live = [x for x in live if t <= x[1]]
        if t in rm:
            dm, exp = rm[t]
            live.append((SLOT * nav[t], exp, dm))
            new_s[t] = 1
    s = pd.Series(nav, index=td)
    r = s.pct_change()
    log('  [%s] 事件=%d 入场日=%d 均只数=%.2f' % (tag, n, nd, n / max(nd, 1)))
    log('       名义敞口 Σnt/NAV: 均值=%.3f p50=%.3f p90=%.3f 最大=%.3f'
        % (np.nanmean(lev), np.nanpercentile(lev, 50),
           np.nanpercentile(lev, 90), np.nanmax(lev)))
    log('       净值日收益: 均值=%.5f%% 标准差=%.4f%% 最小=%.2f%% 最大=%.2f%%'
        % (r.mean() * 100, r.std() * 100, r.min() * 100, r.max() * 100))
    for pn, (a, b) in (('TRAIN', ('20180101', '20231231')),
                       ('VALID', ('20240101', '20251231')),
                       ('OOS', ('20260101', '20260930'))):
        m = (s.index >= a) & (s.index <= b)
        x = s[m]
        bb = pd.Series(lvl, index=td)[m]
        yrs = len(x) / 242.0
        log('       %-6s 策略CAGR=%+.2f%% 指数CAGR=%+.2f%% 名义敞口均值=%.2f'
            % (pn, ((x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1) * 100,
               ((bb.iloc[-1] / bb.iloc[0]) ** (1 / yrs) - 1) * 100,
               np.nanmean(lev[m])))
    return pd.Series(nav, index=td)


log('=' * 78)
log('S1 名义敞口与净值波动（现口径 SLOT=1/6）')
log('')
run(df[mv80].copy(), 'B2_MV80_ALL_EVENTS')
log('')
A = top_decile(df, 'rev_acc')
A = A[mv80.reindex(A.index).fillna(False).values].copy()
run(A, 'rev_acc_RANKPOST')
log('')
log('=' * 78)
log('S2 对照：把 SLOT 改成 1/60（即按实际并发数均分，总敞口封顶 100%）')
log('   —— 不改任何既有文件，只在本脚本内做对照实验')


def run_slot(ev, tag, slot):
    rm, n, nd = build_day_baskets(ev, cmap, C, O, len(td), COST)
    NCAL = len(td)
    nav = np.full(NCAL, np.nan)
    nav[0] = 1.0
    live = []
    for t in range(1, NCAL):
        pnl = 0.0
        for nt, exp, dm in live:
            v = dm.get(t)
            if v is not None:
                pnl += nt * v
        nav[t] = nav[t - 1] + pnl
        live = [x for x in live if t <= x[1]]
        if t in rm:
            dm, exp = rm[t]
            live.append((slot * nav[t], exp, dm))
    s = pd.Series(nav, index=td)
    log('  [%s] slot=1/%.0f' % (tag, 1 / slot))
    for pn, (a, b) in (('TRAIN', ('20180101', '20231231')),
                       ('VALID', ('20240101', '20251231')),
                       ('OOS', ('20260101', '20260930'))):
        m = (s.index >= a) & (s.index <= b)
        x = s[m]
        bb = pd.Series(lvl, index=td)[m]
        yrs = len(x) / 242.0
        sc = ((x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1) * 100
        bc = ((bb.iloc[-1] / bb.iloc[0]) ** (1 / yrs) - 1) * 100
        log('       %-6s 策略CAGR=%+.2f%% 指数CAGR=%+.2f%% 超额=%+.2f%%'
            % (pn, sc, bc, sc - bc))
    return s


for slot, tag in ((1.0 / 12, 'rev_acc_RANKPOST_s12'), (1.0 / 24, 'rev_acc_RANKPOST_s24')):
    run_slot(A, tag, slot)
log.save()
print('DONE')
