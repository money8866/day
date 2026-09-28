# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P2：单变量筛查（§32 / §33）

三块产出
  A) 事件层：E1 / E2 前向收益（按交易日聚类的 Newey-West t）、分期、分年、深度带 / 天数带
  B) 横截面层：把强趋势日长表还原为 (NCODES, NCAL) 面板，
     对每个特征做逐日横截面 Spearman IC / top-decile AUC / 十分位分桶 / 单调性，分期报告
     分组：pb = 仅回撤 Episode 内有效；trend = 全部强趋势日有效
  C) 落盘 CSV，供 P4/P5/P6 直接引用

统计口径
  · 逐日横截面秩统计使用 scipy.stats.rankdata(axis=1)（同 hp_common.rank_avg 的平均秩口径，
    仅实现改为向量化）；每一条统计都在「当日 S 与目标同时有限」的子集内计算
  · IC/AUC 的 t 值由日度序列经 Newey-West（滞后 19）估计

纪律：本脚本只做描述与筛查，不做任何筛选、不调参、不设阈值；不改变 §3「禁止预设方向」。
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from hp_common import (DATA, OUTD, PREREG, mklog, pload_meta, pload_day,
                       ic_stats, monotonicity)

warnings.filterwarnings('ignore', category=RuntimeWarning)
LOG = mklog('single')

HZ = tuple(PREREG['primary_horizons'])
PHASES = ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE')
MIN_XS = 30

PB_FEATS = ['depth', 'speed', 'ep_days', 'since_high', 'since_pb',
            'cl_ma20', 'cl_ma60', 'lo_ma20', 'lo_ma60',
            'ep_low_ma20', 'ep_low_ma60',
            'vol_ratio', 'to_ratio', 'atr_ratio', 'rv_ratio',
            'dn_vol_ratio', 'dn_day_ratio', 'rs_pb_mkt', 'rs_pb_ind']
TREND_FEATS = ['mom20', 'mom60', 'ret_5', 'ret_10',
               'rs5', 'rs10', 'rs20', 'total_mv', 'turnover', 'atr_pct',
               'rv_20', 'rsi14', 'pos_60', 'pos_120', 'dd_60', 'trend_age',
               'ts_ret20', 'ts_ret60', 'ts_cl_ma20', 'ts_ma20_ma60', 'ts_slope20']
DEPTH_BANDS = [(0.0, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05),
               (0.05, 0.08), (0.08, 0.10), (0.10, np.inf)]
DUR_BANDS = [(2, 2), (3, 4), (5, 7), (8, np.inf)]
REC_BANDS = [(1, 1), (2, 3), (4, 7), (8, np.inf)]


# ─────────────────────────────────────────────── 向量化横截面统计
# 约定：以下函数统一接收「转置面板」A = (NCAL, NCODES)，即 axis=1 为当日全体个股
def _row_ranks(A):
    """按行（=按日）平均秩；NaN 位保持 NaN（等价 hp_common.rank_avg 的并列口径）"""
    return rankdata(A, axis=1, method='average', nan_policy='omit')


def _corr_rows(RS, RT, VAL, min_xs=MIN_XS):
    """逐日横截面秩相关 = Spearman IC。返回 (NCAL,)"""
    a = np.where(VAL, RS, np.nan)
    b = np.where(VAL, RT, np.nan)
    n = VAL.sum(1).astype(np.float64)
    da = a - np.nanmean(a, 1, keepdims=True)
    db = b - np.nanmean(b, 1, keepdims=True)
    num = np.nansum(da * db, 1)
    den = np.sqrt(np.nansum(da * da, 1) * np.nansum(db * db, 1))
    out = np.full(len(n), np.nan)
    ok = (n >= min_xs) & (den > 0)
    out[ok] = num[ok] / den[ok]
    return out


def _auc_top_rows(RS, T, VAL, top_pct=0.10, min_xs=MIN_XS):
    """逐日「目标前 top_pct」二值标签 AUC（Mann-Whitney，平均秩）。返回 (NCAL,)"""
    n = VAL.sum(1).astype(np.float64)
    thr = np.nanquantile(np.where(VAL, T, np.nan), 1.0 - top_pct, axis=1)
    Y = VAL & (T >= thr[:, None])
    P = Y.sum(1).astype(np.float64)
    N = n - P
    RY = np.where(Y, RS, 0.0).sum(1)
    out = np.full(len(n), np.nan)
    ok = (n >= min_xs) & (P > 0) & (N > 0)
    out[ok] = (RY[ok] - P[ok] * (P[ok] + 1) / 2.0) / (P[ok] * N[ok])
    return out


def _bucket_rows(RS, T, VAL, nq=10):
    """逐日分位分桶的目标均值（日等权）。返回 (means, se, cnt)"""
    n = VAL.sum(1).astype(np.float64)
    ok = VAL & (n[:, None] >= nq * 3)
    b = np.minimum(nq - 1, ((np.where(VAL, RS - 1.0, 0.0)
                             / np.where(VAL, n[:, None], 1.0)) * nq).astype(np.int64))
    means = np.full(nq, np.nan)
    se = np.full(nq, np.nan)
    cnt = np.zeros(nq, dtype=np.int64)
    for q in range(nq):
        v = np.where(ok & (b == q), T, np.nan)
        dm = np.nanmean(v, 1)
        f = np.isfinite(dm)
        cnt[q] = int(f.sum())
        if f.sum():
            means[q] = float(dm[f].mean())
            se[q] = (float(dm[f].std(ddof=1) / np.sqrt(f.sum())) if f.sum() > 1 else np.nan)
    return means, se, cnt


def to_panel(TD, col, NCODES, NCAL):
    """long 表 -> (NCAL, NCODES) 转置面板（同一 (code,k) 唯一；axis=1 为当日全体个股）"""
    P = np.full((NCAL, NCODES), np.nan, dtype=np.float32)
    v = TD[col].values.astype(np.float64)
    ok = np.isfinite(v)
    P[TD['k'].values[ok], TD['code'].values[ok]] = v[ok]
    return P


def _phase_slice(series, day_ph, pi, name):
    m = (day_ph == pi) & np.isfinite(series)
    return ic_stats(series[m], name=name)


def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-TREND-PULLBACK-01  P2 单变量筛查  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    M = pload_meta()
    NCODES, NCAL = int(M['NCODES']), int(M['NCAL'])
    day = pload_day()
    day_ph = day['phase']

    LOG('读入 hp_trenddays.parquet ...')
    TD = pd.read_parquet(os.path.join(DATA, 'hp_trenddays.parquet'))
    EV = pd.read_parquet(os.path.join(DATA, 'hp_events.parquet'))
    E2 = pd.read_parquet(os.path.join(DATA, 'hp_events_e2.parquet'))
    LOG('   trenddays %d 行 / E1 %d / E2 %d' % (len(TD), len(EV), len(E2)))

    TG = {h: to_panel(TD, 'exe_%d' % h, NCODES, NCAL) for h in HZ}

    # ══════════════════════════════ A) 事件层 ══════════════════════════════
    rows_ev = []
    for nm, E in (('E1', EV), ('E2', E2)):
        for h in HZ:
            y = E['exe_%d' % h]
            m = np.isfinite(y)
            dm = E.loc[m].groupby('date')['exe_%d' % h].mean()
            st = ic_stats(dm.values, name='%s_h%d' % (nm, h))
            rows_ev.append({'anchor': nm, 'horizon': h, 'phase': 'ALL',
                            'n_event': int(m.sum()), 'n_day': int(len(dm)),
                            'mean': float(y[m].mean()), 'median': float(y[m].median()),
                            'winrate': float((y[m] > 0).mean()), 't_nw': st['t_nw']})
            for pn, pi in zip(PHASES, (1, 2, 3, 4)):
                mm = m & (E['phase'] == pi)
                if mm.sum() >= 20:
                    dm2 = E.loc[mm].groupby('date')['exe_%d' % h].mean()
                    st2 = ic_stats(dm2.values, name='%s_h%d_%s' % (nm, h, pn))
                    rows_ev.append({'anchor': nm, 'horizon': h, 'phase': pn,
                                    'n_event': int(mm.sum()), 'n_day': int(len(dm2)),
                                    'mean': float(E.loc[mm, 'exe_%d' % h].mean()),
                                    'median': float(E.loc[mm, 'exe_%d' % h].median()),
                                    'winrate': float((E.loc[mm, 'exe_%d' % h] > 0).mean()),
                                    't_nw': st2['t_nw']})
    EVT = pd.DataFrame(rows_ev)
    LOG.sep()
    LOG('A) 事件层（次日开盘进场）')
    LOG('   mean/median/win 为事件等权；t 由「日度事件均值」序列 NW 估计（日等权），两者口径不同可异号')
    for nm in ('E1', 'E2'):
        for h in HZ:
            r = EVT[(EVT['anchor'] == nm) & (EVT['horizon'] == h) & (EVT['phase'] == 'ALL')]
            if len(r):
                r = r.iloc[0]
                LOG('   %s h=%-2d  n=%6d  日=%5d  mean %+.4f  median %+.4f  win %.3f  t %+.2f'
                    % (nm, h, r['n_event'], r['n_day'], r['mean'], r['median'],
                       r['winrate'], r['t_nw']))
    LOG('   分期（h=5）:')
    for nm in ('E1', 'E2'):
        for pn in PHASES:
            r = EVT[(EVT['anchor'] == nm) & (EVT['horizon'] == 5) & (EVT['phase'] == pn)]
            if len(r):
                r = r.iloc[0]
                LOG('     %s %-10s n=%6d  mean %+.4f  t %+.2f'
                    % (nm, pn, r['n_event'], r['mean'], r['t_nw']))
    LOG('   分年（h=5）:')
    for nm, E in (('E1', EV), ('E2', E2)):
        for yy in PREREG['year_report']:
            sel = E[(E['year'] == yy) & np.isfinite(E['exe_5'])]
            if len(sel) >= 20:
                LOG('     %s %d  n=%5d  mean %+.4f' % (nm, yy, len(sel),
                                                       float(sel['exe_5'].mean())))

    # 深度带 / 天数带（§11 §12，全报不择优，含上开口档）
    rows_band = []
    for lo_, hi_ in DEPTH_BANDS:
        sel = EV[(EV['depth_at_sig'] > lo_) & (EV['depth_at_sig'] <= hi_)]
        for h in HZ:
            y = sel['exe_%d' % h].values
            y = y[np.isfinite(y)]
            if len(y) >= 30:
                rows_band.append({'anchor': 'E1', 'kind': 'depth',
                                  'band': '%s-%s' % (lo_, 'inf' if hi_ == np.inf else hi_),
                                  'horizon': h, 'n': len(y), 'mean': float(y.mean()),
                                  'median': float(np.median(y))})
    for lo_, hi_ in DUR_BANDS:
        sel = EV[(EV['ep_days'] >= lo_) & (EV['ep_days'] <= hi_)]
        for h in HZ:
            y = sel['exe_%d' % h].values
            y = y[np.isfinite(y)]
            if len(y) >= 30:
                rows_band.append({'anchor': 'E1', 'kind': 'dur',
                                  'band': '%d-%s' % (lo_, 'inf' if hi_ == np.inf else hi_),
                                  'horizon': h, 'n': len(y), 'mean': float(y.mean()),
                                  'median': float(np.median(y))})
    for lo_, hi_ in REC_BANDS:
        sel = E2[(E2['since_e1'] >= lo_) & (E2['since_e1'] <= hi_)]
        for h in HZ:
            y = sel['exe_%d' % h].values
            y = y[np.isfinite(y)]
            if len(y) >= 30:
                rows_band.append({'anchor': 'E2', 'kind': 'since_e1',
                                  'band': '%d-%s' % (lo_, 'inf' if hi_ == np.inf else hi_),
                                  'horizon': h, 'n': len(y), 'mean': float(y.mean()),
                                  'median': float(np.median(y))})
    BAND = pd.DataFrame(rows_band)
    LOG.sep()
    LOG('   深度带（h=5，E1；全报不择优）:')
    for _, r in BAND[(BAND['anchor'] == 'E1') & (BAND['kind'] == 'depth')
                     & (BAND['horizon'] == 5)].iterrows():
        LOG('     depth    %-9s n=%6d  mean %+.4f  median %+.4f'
            % (r['band'], r['n'], r['mean'], r['median']))
    LOG('   天数带（h=5，E1）:')
    for _, r in BAND[(BAND['anchor'] == 'E1') & (BAND['kind'] == 'dur')
                     & (BAND['horizon'] == 5)].iterrows():
        LOG('     ep_days  %-9s n=%6d  mean %+.4f' % (r['band'], r['n'], r['mean']))
    LOG('   再启动滞后带（h=5，E2）:')
    for _, r in BAND[(BAND['anchor'] == 'E2') & (BAND['kind'] == 'since_e1')
                     & (BAND['horizon'] == 5)].iterrows():
        LOG('     since_e1 %-9s n=%6d  mean %+.4f' % (r['band'], r['n'], r['mean']))

    # ══════════════════════════ B) 横截面单变量筛查 ══════════════════════════
    LOG.sep()
    LOG('B) 横截面单变量筛查（逐日 Spearman IC / top10% AUC / 十分位分桶，分期报告）')
    rows_ic, rows_bk = [], []
    nfeat = len(PB_FEATS) + len(TREND_FEATS)
    iF = 0
    for grp, feats in (('pb', PB_FEATS), ('trend', TREND_FEATS)):
        for c_ in feats:
            iF += 1
            S = to_panel(TD, c_, NCODES, NCAL)
            if not np.isfinite(S).any():
                LOG('   [WARN] %s 全为 NaN，跳过' % c_)
                continue
            base = np.isfinite(S)
            for h in HZ:
                T = TG[h]
                VAL = base & np.isfinite(T)
                RS = _row_ranks(np.where(VAL, S, np.nan))
                RT = _row_ranks(np.where(VAL, T, np.nan))
                ic = _corr_rows(RS, RT, VAL)
                au = _auc_top_rows(RS, T, VAL)
                bm, bse, bcnt = _bucket_rows(RS, T, VAL)
                rho, slope = monotonicity(bm)
                rec = {'group': grp, 'feature': c_, 'horizon': h,
                       'n_obs': int(VAL.sum()),
                       'ic_all': float(np.nanmean(ic)) if np.isfinite(ic).any() else np.nan,
                       'auc_all': float(np.nanmean(au)) if np.isfinite(au).any() else np.nan,
                       'mono_rho': rho, 'mono_slope': slope,
                       'q_spread': (float(bm[9] - bm[0])
                                    if np.isfinite(bm[9]) and np.isfinite(bm[0]) else np.nan)}
                for pn, pi in zip(PHASES, (1, 2, 3, 4)):
                    s1 = _phase_slice(ic, day_ph, pi, '%s_%s_h%d_ic' % (grp, c_, h))
                    s2 = _phase_slice(au, day_ph, pi, '%s_%s_h%d_auc' % (grp, c_, h))
                    rec['ic_%s' % pn] = s1['mean']
                    rec['ic_t_%s' % pn] = s1['t_nw']
                    rec['ic_n_%s' % pn] = s1['n']
                    rec['auc_%s' % pn] = s2['mean']
                    rec['auc_t_%s' % pn] = s2['t_nw']
                rows_ic.append(rec)
                if h == 5:
                    for q in range(10):
                        rows_bk.append({'group': grp, 'feature': c_, 'horizon': h,
                                        'bucket': q, 'mean': bm[q], 'se': bse[q],
                                        'nday': int(bcnt[q])})
            LOG('   [%2d/%2d] %-14s (%s) 完成 %.0fs'
                % (iF, nfeat, c_, grp, time.time() - t0))
    IC = pd.DataFrame(rows_ic)
    BK = pd.DataFrame(rows_bk)

    LOG.sep()
    LOG('   h=5 摘要（ALL 期 IC / AUC；各期 IC 的 NW t）')
    LOG('   %-14s %-5s %8s %8s %8s %8s %8s %8s %8s'
        % ('feature', 'grp', 'IC_all', 'AUC_all', 't_TR', 't_VA', 't_OO', 't_LV', 'q_spr'))
    for _, r in IC[IC['horizon'] == 5].sort_values('ic_all').iterrows():
        LOG('   %-14s %-5s %+8.4f %8.4f %+8.2f %+8.2f %+8.2f %+8.2f %+8.4f'
            % (r['feature'], r['group'], r['ic_all'], r['auc_all'],
               r['ic_t_TRAIN'], r['ic_t_VALID'], r['ic_t_OOS'], r['ic_t_LIVE-LIKE'],
               r['q_spread']))

    # ══════════════════════════ C) 落盘 ══════════════════════════
    EVT.to_csv(os.path.join(OUTD, 'hp_single_events.csv'), index=False)
    BAND.to_csv(os.path.join(OUTD, 'hp_single_bands.csv'), index=False)
    IC.to_csv(os.path.join(OUTD, 'hp_single_ic.csv'), index=False)
    BK.to_csv(os.path.join(OUTD, 'hp_single_bucket.csv'), index=False)
    LOG.sep()
    LOG('落盘: out/hp_single_events.csv / hp_single_bands.csv / hp_single_ic.csv / hp_single_bucket.csv')
    LOG('P2 完成 %.0fs' % (time.time() - t0))
    LOG.sep('=')
    print('DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
