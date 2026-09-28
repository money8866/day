# -*- coding: utf-8 -*-
"""probe39：OOS 那 177 天到底是"能力"还是"少数 cohort + 有利环境"？

动机
  附录 C 得出「因子没失效，载体崩了」。用户追问 OOS（2026 年）超额 +74%，
  需要回答：OOS 的强势能否推翻 VALID 的负号？

  两条可能的解释必须分开：
    (H1) OOS 是少量 cohort 拉动的（n=46），单年小样本放大
    (H2) OOS 期「事件池 β」极度有利（事件池 +32.5% vs 沪深300 -8.0%），
         换个环境符号就变 —— 这与 VALID 恰好镜像

  本脚本只做分布刻画（只读），不改任何口径与裁定。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log
from _fs_trade_gate2 import load_px, top_decile, MV_MIN, HOLD

log = Log('_fs_probe39.txt')

FACTORS = ('rev_acc', 'np_qoq')
FY = {'01', '04', '07', '08', '10'}

td, cmap, C, O, lvl = load_px()
df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
df = E.add_derived(df)
df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
df['ann_date'] = df['ann_date'].astype(str)
df['is_fy'] = df['ann_date'].str[4:6].isin(FY)
mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))


def rp(d, col):
    A = top_decile(d, col)
    return A[mv80.reindex(A.index).fillna(False).values].copy()


def rp_rankpost(d, col):
    """RANK-POST：全样本 top 十分位 → 再剔 mv80"""
    A = top_decile(d, col)
    return A[mv80.reindex(A.index).fillna(False).values].copy()


# 指数同期 5 日收益
idx = pd.Series(lvl, index=np.arange(len(td)))
ks = df['k1'].values.astype(int)
ok = ks + HOLD <= len(td) - 1
df = df[ok].reset_index(drop=True)
df['idx5'] = idx.reindex(df['k1'].values + HOLD).values / \
    idx.reindex(df['k1'].values).values - 1.0
df['ym'] = td[df['k1'].values][:, :6] if False else [td[k][:6] for k in df['k1']]

allg = df.groupby('k1')['ret_E1_T5'].mean()
allx = df.groupby('k1')['ret_E1_T5'].mean() - df.groupby('k1')['idx5'].mean()

PER = {'TRAIN': ('201801', '202312'), 'VALID': ('202401', '202512'),
       'OOS': ('202601', '202609')}


def stats(g, tag, rec):
    g = g.dropna()
    if len(g) == 0:
        return
    v = np.sort(g.values)
    k = max(int(len(v) * 0.10), 0)
    trim = v[k:len(v) - k] if len(v) - 2 * k >= 5 else v
    s = v.sum()
    top3 = np.sort(v)[-3:].sum()
    rec.append(dict(slice=tag, n=len(v), mean=v.mean() * 100,
                    median=np.median(v) * 100, trim10=trim.mean() * 100,
                    win=float((v > 0).mean()) * 100,
                    p10=np.percentile(v, 10) * 100,
                    p90=np.percentile(v, 90) * 100,
                    top3_share=(top3 / s * 100) if abs(s) > 1e-12 else np.nan))


log('=' * 78)
log('S1 每日 cohort 层：篮子5日收益（相对沪深300）—— 分布刻画')
log('   口径：RANK-POST + mv80；一个入场日 = 一个 cohort')
for pn, (a, b) in PER.items():
    log('')
    log('  【%s】' % pn)
    rec = []
    z = df[(df['ym'] >= a) & (df['ym'] <= b)]
    g = z.groupby('k1').apply(
        lambda x: x['ret_E1_T5'].mean() - x['idx5'].mean()) if False else \
        (z.groupby('k1')['ret_E1_T5'].mean() - z.groupby('k1')['idx5'].mean())
    stats(g[z.groupby('k1')['is_fy'].first()], 'ALL_EVENTS 财报月', rec)
    for f in FACTORS:
        x = rp_rankpost(df, f)
        x = x[(x['ym'] >= a) & (x['ym'] <= b)]
        gg = (x.groupby('k1')['ret_E1_T5'].mean() - x.groupby('k1')['idx5'].mean())
        fyflag = x.groupby('k1')['is_fy'].first()
        stats(gg[fyflag], '%s 财报月' % f, rec)
    log(pd.DataFrame(rec).to_string(index=False, float_format=lambda v: '%.2f' % v))

log('')
log('=' * 78)
log('S2 相对同日全事件篮子（剥离事件池β后的纯相对 Alpha）')
for pn, (a, b) in PER.items():
    log('')
    log('  【%s】' % pn)
    rec = []
    for f in FACTORS:
        x = rp_rankpost(df, f).dropna(subset=['ret_E1_T5'])
        x = x[(x['ym'] >= a) & (x['ym'] <= b)]
        gs = x.groupby('k1')['ret_E1_T5'].mean()
        d = gs - allg.reindex(gs.index)
        fyflag = x.groupby('k1')['is_fy'].first()
        stats(d[fyflag], '%s 财报月' % f, rec)
    log(pd.DataFrame(rec).to_string(index=False, float_format=lambda v: '%.3f' % v))

log('')
log('=' * 78)
log('S3 事件池 β（全事件篮子 − 沪深300），逐期 —— 环境是否主导')
rec = []
for pn, (a, b) in PER.items():
    z = df[(df['ym'] >= a) & (df['ym'] <= b)]
    g = z.groupby('k1')['ret_E1_T5'].mean() - z.groupby('k1')['idx5'].mean()
    stats(g, pn, rec)
log(pd.DataFrame(rec).to_string(index=False, float_format=lambda v: '%.3f' % v))

log('')
log('S4 OOS 期逐月 cohort（看是否集中在个别月）')
for f in FACTORS:
    x = rp_rankpost(df, f).dropna(subset=['ret_E1_T5'])
    x = x[(x['ym'] >= '202601') & (x['ym'] <= '202609')]
    gs = x.groupby('k1')['ret_E1_T5'].mean()
    d = gs - allg.reindex(gs.index)
    dd = pd.DataFrame({'k1': d.index, 'exc': d.values * 100})
    dd['ym'] = [td[int(k)][:6] for k in dd['k1']]
    p = dd.groupby('ym')['exc'].agg(['mean', 'median', 'count']).round(3)
    log('  [%s] OOS 逐月（相对同日全事件篮子）' % f)
    log(p.to_string())
log.save()
print('DONE')
