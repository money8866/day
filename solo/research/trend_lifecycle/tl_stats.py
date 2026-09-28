# -*- coding: utf-8 -*-
"""TL-01 §6/§8/§19/§20/§23/§24/§47/§48 状态与状态转换统计

产出（§53）：
  tl01_state_stats.csv       §6 状态分布 + §8/§23 平均持续时间（§48 生命周期状态报告）
  tl01_age_effect.csv        §24 Age Effect + §23 Duration Effect
  tl01_transition.csv        §47 Transition Matrix（from x to，含概率与 T+5/20/60）
  tl01_transition_stats.csv  §19 分布统计（Mean/Median/Std/Win/P10-P90、MFE/MAE、延续/终结率）
  tl01_mfe_mae.csv           §20 各状态与预注册转换的 MFE / MAE

纪律：本脚本只做统计描述，不做任何参数选择、不做“最佳/最差”投资评级（§49）。
      状态定义只使用决策日 t 及之前的可观测信息（构建期已保证）；前向列仅作被预测对象。
"""
import os
import time
import numpy as np
import pandas as pd

from tl_common import (HERE, PREREG, STATE_NAMES, Log, panel_meta, pget,
                       phase_of, qstats)

HZ = PREREG['horizons']
AG_EDGES = [0, 5, 10, 20, 40, 60, 10 ** 9]
AG_LBL = ['1-5', '6-10', '11-20', '21-40', '41-60', '60+']

TR_AGG = (['fwd_%d' % h for h in HZ] + ['rel_20'] +
          ['mfe_%d' % h for h in (5, 10, 20, 60)] +
          ['mae_%d' % h for h in (5, 10, 20, 60)] +
          ['cont_nh20', 'cont_ma20', 'cont_rs20',
           'fail_ma20', 'fail_ma60', 'fail_low', 'fail_rs'])


def main():
    t0 = time.time()
    log = Log('_tl_stats_run.txt')
    log('=' * 78)
    log('TL-01 §6/§8/§19/§20/§23/§24/§47 状态与转换统计')
    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    log('  面板 %d 天 x %d 只   日期 %s ~ %s'
        % (NCAL, NCODE, meta['dates'][0], meta['dates'][-1]))

    ST = np.asarray(pget('state'))
    mask = ST >= 0
    log('  有效样本 %d 行' % int(mask.sum()))
    st = ST[mask].astype(np.int16)
    N = len(st)
    yr = np.asarray(pget('year'))[mask]
    ph = phase_of(yr)
    age = np.asarray(pget('trend_age'))[mask].astype(np.int32)
    dur = np.asarray(pget('state_dur'))[mask].astype(np.int32)
    sp = np.asarray(pget('state_prev'))[mask].astype(np.int16)

    # 事件（episode）识别：沿时间轴（面板行序）连续同态 = 同一事件
    cont = np.zeros_like(mask)
    cont[1:] = (ST[1:] == ST[:-1]) & (ST[1:] >= 0)
    term = mask & ~cont
    dur_all = np.asarray(pget('state_dur'))

    agg = {c: np.asarray(pget(c))[mask] for c in TR_AGG}
    log('  聚合列 %d 个已载入  %.0fs' % (len(agg), time.time() - t0))

    def _m(c, m):
        v = agg[c][m]
        return float(np.nanmean(v)) if len(v) else np.nan

    # ------------------------------------------------------------------ §6/§8
    log('-' * 78)
    log('1) §6 状态分布 + §8/§23 平均持续时间（§48）')
    rows = []
    for s in range(11):
        m = st == s
        idxs = np.flatnonzero(m)
        dd = dur_all[term & (ST == s)]
        dd = dd[dd > 0]
        rec = dict(state=s, name=STATE_NAMES[s], n_obs=int(m.sum()),
                   share=float(m.sum()) / N, n_episodes=int(len(dd)),
                   avg_dur=float(dd.mean()) if len(dd) else np.nan,
                   med_dur=float(np.median(dd)) if len(dd) else np.nan,
                   p90_dur=float(np.percentile(dd, 90)) if len(dd) else np.nan,
                   max_dur=int(dd.max()) if len(dd) else 0)
        for h in (5, 20, 60):
            v = agg['fwd_%d' % h][idxs]
            rec['fwd%d_mean' % h] = float(np.nanmean(v)) if len(v) else np.nan
            rec['fwd%d_win' % h] = float(np.nanmean(v > 0)) if len(v) else np.nan
        for c in ('cont_nh20', 'cont_ma20', 'cont_rs20',
                  'fail_ma20', 'fail_ma60', 'fail_low', 'fail_rs'):
            rec[c] = _m(c, m)
        rows.append(rec)
    log('  状态分布：' + '  '.join(
        '%s=%.3f%%' % (STATE_NAMES[s], 100.0 * rows[s]['share']) for s in range(11)))
    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'tl01_state_stats.csv'),
                              index=False, encoding='utf-8-sig')

    # ------------------------------------------------------------------ §47
    log('-' * 78)
    log('2) §47 Transition Matrix（from -> to）')
    ok = sp >= 0
    aggok = {c: v[ok] for c, v in agg.items()}      # 转换口径（state_prev>=0）子样本
    log('  可识别转换 %d 次（state_prev>=0）' % int(ok.sum()))
    frm, to = sp[ok], st[ok]
    trows = []
    pair_cnt = {}
    for a in range(11):
        na = int((frm == a).sum())
        if na == 0:
            continue
        ma = frm == a
        for b in range(11):
            m = ma & (to == b)
            n = int(m.sum())
            if n == 0:
                continue
            pair_cnt[(a, b)] = n
            rec = dict(from_state=a, from_name=STATE_NAMES[a], to_state=b,
                       to_name=STATE_NAMES[b], count=n, probability=n / float(na),
                       is_prereg=int((a, b) in PREREG['report_transitions']))
            for h in HZ:
                v = aggok['fwd_%d' % h][m]
                rec['fwd%d_mean' % h] = float(np.nanmean(v)) if len(v) else np.nan
            trows.append(rec)
    pd.DataFrame(trows).sort_values(['from_state', 'to_state']).to_csv(
        os.path.join(HERE, 'tl01_transition.csv'), index=False, encoding='utf-8-sig')

    # ------------------------------------------------------------------ §19/§49
    log('-' * 78)
    log('3) §19 转换分布统计 + §49 Transition Ranking（仅统计，不做投资评级）')
    keys = [(int(a), int(b)) for a, b in PREREG['report_transitions']]
    extra = [k2 for k2, n in sorted(pair_cnt.items(), key=lambda x: -x[1])
             if k2 not in keys and n >= 200][:24]
    use = keys + extra
    log('  预注册转换 %d 条 + 补充 n>=200 的转换 %d 条' % (len(keys), len(extra)))

    srows = []
    for a, b in use:
        m = (frm == a) & (to == b)
        n = int(m.sum())
        rec = dict(from_state=a, from_name=STATE_NAMES[a], to_state=b,
                   to_name=STATE_NAMES[b], n=n,
                   prob_from=n / float((frm == a).sum()) if (frm == a).sum() else np.nan,
                   is_prereg=int((a, b) in keys))
        for h in HZ:
            q = qstats(aggok['fwd_%d' % h][m])
            rec.update({'fwd%d_%s' % (h, k2): v for k2, v in q.items()})
        for h in (5, 10, 20, 60):
            rec['mfe%d_mean' % h] = float(np.nanmean(aggok['mfe_%d' % h][m])) if n else np.nan
            rec['mae%d_mean' % h] = float(np.nanmean(aggok['mae_%d' % h][m])) if n else np.nan
        for c in ('rel_20', 'cont_nh20', 'cont_ma20', 'cont_rs20',
                  'fail_ma20', 'fail_ma60', 'fail_low', 'fail_rs'):
            rec[c] = float(np.nanmean(aggok[c][m])) if n else np.nan
        for nm, _a, _b in PREREG['phases']:
            pm = m & (ph[ok] == nm)
            rec['n_' + nm] = int(pm.sum())
            v = aggok['fwd_20'][pm]
            rec['fwd20_' + nm] = float(np.nanmean(v)) if len(v) else np.nan
        srows.append(rec)
    pd.DataFrame(srows).to_csv(os.path.join(HERE, 'tl01_transition_stats.csv'),
                               index=False, encoding='utf-8-sig')

    # ------------------------------------------------------------------ §20
    log('-' * 78)
    log('4) §20 MFE / MAE（按状态与预注册转换）')
    mrows = []
    for s in range(11):
        m = st == s
        rec = dict(kind='state', id='S%d' % s, name=STATE_NAMES[s], n=int(m.sum()))
        for h in (5, 10, 20, 60):
            v, w = agg['mfe_%d' % h][m], agg['mae_%d' % h][m]
            rec['mfe%d_mean' % h] = float(np.nanmean(v)) if len(v) else np.nan
            rec['mfe%d_p90' % h] = float(np.nanpercentile(v, 90)) if len(v) else np.nan
            rec['mae%d_mean' % h] = float(np.nanmean(w)) if len(w) else np.nan
            rec['mae%d_p10' % h] = float(np.nanpercentile(w, 10)) if len(w) else np.nan
        rec['rr20'] = (rec['mfe20_mean'] / abs(rec['mae20_mean'])
                       if np.isfinite(rec['mae20_mean'])
                       and abs(rec['mae20_mean']) > 1e-9 else np.nan)
        mrows.append(rec)
    for a, b in keys:
        m = (frm == a) & (to == b)
        if m.sum() < 20:
            continue
        rec = dict(kind='transition', id='S%d>S%d' % (a, b),
                   name='%s -> %s' % (STATE_NAMES[a], STATE_NAMES[b]), n=int(m.sum()))
        for h in (5, 10, 20, 60):
            v, w = aggok['mfe_%d' % h][m], aggok['mae_%d' % h][m]
            rec['mfe%d_mean' % h] = float(np.nanmean(v))
            rec['mfe%d_p90' % h] = float(np.nanpercentile(v, 90))
            rec['mae%d_mean' % h] = float(np.nanmean(w))
            rec['mae%d_p10' % h] = float(np.nanpercentile(w, 10))
        rec['rr20'] = (rec['mfe20_mean'] / abs(rec['mae20_mean'])
                       if abs(rec['mae20_mean']) > 1e-9 else np.nan)
        mrows.append(rec)
    pd.DataFrame(mrows).to_csv(os.path.join(HERE, 'tl01_mfe_mae.csv'),
                               index=False, encoding='utf-8-sig')

    # ------------------------------------------------------------------ §24/§23
    log('-' * 78)
    log('5) §24 Trend Age Effect + §23 Duration Effect')
    arows = []
    for dim, vals in (('age', age), ('duration', dur)):
        nb = np.digitize(vals, AG_EDGES, right=True)
        for i, lb in enumerate(AG_LBL):
            m = nb == (i + 1)
            n = int(m.sum())
            if n == 0:
                continue
            rec = dict(dim=dim, bucket=lb, unit='days', n=n, share=n / float(N))
            for h in HZ:
                v = agg['fwd_%d' % h][m]
                rec['fwd%d_mean' % h] = float(np.nanmean(v))
                rec['fwd%d_med' % h] = float(np.nanmedian(v))
                rec['fwd%d_win' % h] = float(np.nanmean(v > 0))
            for c in ('cont_nh20', 'cont_ma20', 'cont_rs20',
                      'fail_ma20', 'fail_ma60', 'fail_rs'):
                rec[c] = float(np.nanmean(agg[c][m]))
            rec['rel20_mean'] = float(np.nanmean(agg['rel_20'][m]))
            arows.append(rec)
    for s in range(11):
        dd = dur_all[term & (ST == s)]
        dd = dd[dd > 0]
        if len(dd) == 0:
            continue
        nb = np.digitize(dd, AG_EDGES, right=True)
        for i, lb in enumerate(AG_LBL):
            n = int((nb == (i + 1)).sum())
            if n == 0:
                continue
            arows.append(dict(dim='episode_len_S%d' % s, bucket=lb, unit='days',
                              n=n, share=n / float(len(dd))))
    pd.DataFrame(arows).to_csv(os.path.join(HERE, 'tl01_age_effect.csv'),
                               index=False, encoding='utf-8-sig')

    log('-' * 78)
    log('输出：tl01_state_stats.csv / tl01_transition.csv / tl01_transition_stats.csv'
        ' / tl01_mfe_mae.csv / tl01_age_effect.csv')
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
