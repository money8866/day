# -*- coding: utf-8 -*-
"""TL-01 状态定义诊断（只读）：各条件原始命中率 / 覆盖关系 / 转移计数"""
import os
import numpy as np
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
FEAT = os.path.join(HERE, 'tl01_feature_daily.parquet')
ST = os.path.join(HERE, 'tl01_state_daily.parquet')

COLS = ['ret_20', 'c_ma18', 'c_ma20', 'c_ma22', 'c_ma60',
        'ma18_ma60', 'ma20_ma60', 'ma22_ma60', 'ma60_slope', 'slope_20',
        'pos_60', 'pos_120', 'rs_18', 'rs_20', 'rs_25', 'atr_pct_pct',
        'v_trend', 'v_ma5_ma20', 'dd_60', 'dd_min10', 'tv']
pf = pq.ParquetFile(FEAT)
pfs = pq.ParquetFile(ST)

N = pf.metadata.num_rows
cnt = {i: 0 for i in range(1, 11)}
n = 0
va = {k: 0 for k in ['pos_60', 'atr_pct_pct', 'tv', 'ts_raw']}
# 逐 row group 处理，避免一次性载入
stv_all = []
seqv = []
for g in range(pf.num_row_groups):
    t = pf.read_row_group(g, columns=COLS)
    s = pfs.read_row_group(g, columns=['state', 'state_prev'])
    d = {c: t.column(c).to_numpy().astype(np.float64) for c in COLS}
    S = s.column('state').to_numpy()
    Sp = s.column('state_prev').to_numpy()
    stv_all.append(S)
    C = {}
    up = d['ma20_ma60'] > 0
    s20 = d['slope_20'] > 0
    C[1] = (d['c_ma20'] > 0) & (~up) & s20
    C[2] = up & s20 & (d['ret_20'] > 0)
    C[3] = C[2] & (d['pos_60'] >= 0.40) & (d['pos_60'] <= 0.85) & (d['rs_20'] > 0)
    C[6] = up & (d['tv'] < 0) & (d['pos_60'] >= 0.50)
    C[4] = up & (d['pos_60'] >= 0.50) & (d['tv'] >= 0) & \
        (d['atr_pct_pct'] >= 0.60) & (d['v_trend'] > 0)
    C[5] = up & (d['pos_120'] >= 0.90) & (d['atr_pct_pct'] >= 0.70) & (d['v_trend'] > 0)
    C[7] = up & (d['dd_60'] <= -0.05)
    C[8] = (d['dd_min10'] <= -0.05) & (d['c_ma20'] > 0) & (d['tv'] > 0) & \
        (d['v_ma5_ma20'] < 1.0) & (d['dd_60'] > -0.05) & (d['dd_60'] > -0.20)
    C[9] = (~up) & (d['ret_20'] > 0) & (d['dd_60'] <= -0.15)
    C[10] = (~up) & (d['ret_20'] > 0) & (d['dd_60'] <= -0.20) & (d['ma60_slope'] < 0)
    for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        cnt[k] += int(C[k].sum())
    n += len(S)
    if g == 0:
        seqv.append((S, Sp, C))
print('=' * 78)
print('样本 %d' % n)
print('-' * 78)
print('各条件「原始」命中（未考虑优先级覆盖）：')
for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
    print('  c%-3d %10d  %6.3f%%' % (k, cnt[k], 100.0 * cnt[k] / n))

# 最终状态分布
sv = np.concatenate(stv_all)
print('-' * 78)
print('最终状态分布：')
vc = np.bincount(sv, minlength=11)
for i in range(11):
    print('  S%-3d %10d  %6.3f%%' % (i, vc[i], 100.0 * vc[i] / len(sv)))

# 主报告转移计数（用 state_prev → state，仅取同股票相邻日）
pv = pfs.read(columns=['state_prev'])
sp = pv.column('state_prev').to_numpy()
ok = sp >= 0
print('-' * 78)
print('相邻日转移计数（From -> To），仅列出报告要求的 11 条：')
REQ = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (7, 9),
       (8, 3), (8, 4)]
for a, b in REQ:
    m = ok & (sp == a) & (sv == b)
    print('  S%d -> S%-3d %9d' % (a, b, int(m.sum())))
print('-' * 78)
print('全部转移 Top20：')
import collections
cc = collections.Counter(zip(sp[ok].tolist(), sv[ok].tolist()))
for (a, b), c in cc.most_common(20):
    print('  S%d -> S%-3d %9d' % (a, b, c))

# 关键特征分布
print('-' * 78)
for g in (0,):
    S, Sp, C = seqv[0]
    t = pf.read_row_group(g, columns=['pos_60', 'pos_120', 'atr_pct_pct', 'v_trend',
                                      'dd_60'])
    for c in t.schema.names:
        v = t.column(c).to_numpy().astype(np.float64)
        v = v[np.isfinite(v)]
        print('  %-14s n=%d  p10=%.4f p50=%.4f p90=%.4f' %
              (c, len(v), np.percentile(v, 10), np.percentile(v, 50),
               np.percentile(v, 90)))
