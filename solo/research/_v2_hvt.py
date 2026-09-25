# -*- coding: utf-8 -*-
"""三花聚顶 V2 · 与 HVT-BULL 正交复核（回答 Q6 / Q7）

原则: 只读 HVT 产物，不修改 HVT 任何逻辑与代码。
问题: 「三花 + 低量突破」的 Alpha 是否独立于 HVT?
      → 即：在 HVT 未捕捉到的事件里，低量突破是否仍然存在正超额?

口径:
  - 锚点日 = 决策/买入参照日（按组定义，见下）
      突破组(A/A2/B)  锚点 = 首板日 T0 + Cb0_k（首次突破日）
      三花无突破(C)   锚点 = 首板日 T0 + k_A（三花确认日）
      无三花(D)       锚点 = 首板日 T0 + 10（与 V2 v2-13 的 offset10 口径一致）
  - HVT 对齐（同股票）: alignA = 锚点日 == HVT t0_date；alignB = |锚点日 - HVT t0_date| <= 3 交易日
  - 收益列: 突破组 Cb0_rN（自突破日起算）；C 组 A_rN；D 组 c10_rN（均为已扣 0.30% 双边成本）
  - 注意 A ⊂ B、A2 ⊂ B，组间允许重叠，因此不做单一 group 标签，按掩码分别统计
  - 因 HVT 全历史事件仅覆盖 2025-02-05 ~ 2026-08-28，所有对齐只在锚点日落于该期内的子集上有意义

输出: report_daily/research/three_flower_v2_hvt_orth.csv
"""
import os
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
ROOT = os.path.dirname(HERE)
RD = os.path.join(ROOT, 'report_daily')
RDRE = os.path.join(RD, 'research')
DB = r'D:\mystock\cache_daily\stock_data.db'
HVT_EV = os.path.join(RD, 'hvt_bull_backtest_events_20250101_20260828.csv')
HOR = (5, 10, 20)
VR_TH = 1.2


def stx(v):
    v = np.asarray(v, dtype=np.float64)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return {}
    return {'n': int(len(v)), 'mean': float(v.mean()), 'med': float(np.median(v)),
            'win': float((v > 0).mean())}


def fmts(s):
    if not s:
        return 'n/a'
    return '%+0.2f%%/%+0.2f%%' % (100 * s['mean'], 100 * s['med'])


def main():
    tb = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet'))
    tv = pd.read_parquet(os.path.join(OUT, 'tf_v2.parquet'))
    assert len(tb) == len(tv) and (tb['ts_code'].values == tv['ts_code'].values).all()
    df = tb[['ts_code', 'trade_date', 'has_tf', 'k_A', 'Cb0_k', 'brk_b0_vr',
             'A_r5', 'A_r10', 'A_r20', 'Cb0_r5', 'Cb0_r10', 'Cb0_r20']].copy()
    for c in ['c10_r%d' % N for N in HOR]:
        df[c] = tv[c].values

    # ── 交易日历
    conn = sqlite3.connect(DB, timeout=30.0)
    cal = pd.read_sql_query(
        "SELECT trade_date FROM index_daily_cache WHERE ts_code='000001.SH' ORDER BY trade_date",
        conn)['trade_date'].astype(np.int64).tolist()
    conn.close()
    cal = [int(x) for x in cal]
    pos = {d: i for i, d in enumerate(cal)}

    t0 = df['trade_date'].astype(np.int64).values
    t0i = np.array([pos.get(int(t), -1) for t in t0], dtype=np.int64)
    kA = df['k_A'].fillna(-1).astype(int).values
    kB = df['Cb0_k'].fillna(-1).astype(int).values
    has_tf = df['has_tf'].values.astype(bool)
    brk = has_tf & (kB >= 0)
    vr = df['brk_b0_vr'].values.astype(np.float64)
    codes = df['ts_code'].values

    m_lo = brk & np.isfinite(vr) & (vr < VR_TH)
    m_hi = brk & np.isfinite(vr) & (vr >= VR_TH)
    m_C = has_tf & ~brk
    m_D = ~has_tf
    off10 = np.full(len(df), 10, dtype=np.int64)

    # ── HVT 全历史事件（只读）
    he = pd.read_csv(HVT_EV, usecols=lambda c: c in ['ts_code', 't0_date'])
    he['t0n'] = he['t0_date'].astype(np.int64)
    he = he[he['t0n'].isin(pos)]
    by_code = {}
    for c, d in zip(he['ts_code'].values, he['t0n'].values):
        by_code.setdefault(c, []).append(pos[d])
    ev_lo, ev_hi = int(he['t0n'].min()), int(he['t0n'].max())
    print('HVT 事件 %d | 期间 %d ~ %d | 覆盖股票 %d' % (len(he), ev_lo, ev_hi, len(by_code)))

    per_day = he.groupby('t0n')['ts_code'].nunique()
    univ = len(set(codes))
    base_rate = float((per_day / univ).mean())
    print('HVT 日均收录 %.1f 只 / 样本股票池 %d 只 → 随机同日命中基准 %.2f%%'
          % (per_day.mean(), univ, 100 * base_rate))

    # ── 各组锚点 + HVT 对齐
    GROUPS = [('A_三花+低量突破', m_lo, kB, 'Cb0_r%d'),
              ('A2_三花+高量突破', m_hi, kB, 'Cb0_r%d'),
              ('B_三花+突破(全体)', brk, kB, 'Cb0_r%d'),
              ('C_三花形成无突破', m_C, kA, 'A_r%d'),
              ('D_无三花', m_D, off10, 'c10_r%d')]
    G = {}
    for nm, m, kk, fmt in GROUPS:
        idx = np.where(m & (t0i >= 0) & (kk >= 0))[0]
        idx = idx[(t0i[idx] + kk[idx]) < len(cal)]
        a = t0i[idx] + kk[idx]
        inper = (a >= pos[ev_lo]) & (a <= pos[ev_hi])
        hA = np.zeros(len(idx), dtype=bool)
        hB = np.zeros(len(idx), dtype=bool)
        for j, i in enumerate(idx):
            lst = by_code.get(codes[i])
            if not lst:
                continue
            aj = int(a[j])
            hA[j] = any(x == aj for x in lst)
            hB[j] = any(abs(x - aj) <= 3 for x in lst)
        G[nm] = {'idx': idx, 'anch': a, 'inper': inper, 'hitA': hA, 'hitB': hB, 'fmt': fmt}

    rows = []

    def put(sec, grp, sub, hz, s, extra=None):
        r = {'section': sec, 'group': grp, 'subset': sub, 'horizon': hz, 'n': 0,
             'mean': np.nan, 'med': np.nan, 'win': np.nan}
        if s:
            r.update({'n': s['n'], 'mean': s['mean'], 'med': s['med'], 'win': s['win']})
        if extra:
            r.update(extra)
        rows.append(r)

    print('\n' + '=' * 78)
    print('【Q6-1】各组锚点日落在 HVT 覆盖期内的样本与命中率')
    print('%-22s %7s %7s %10s %10s %10s' % ('组', '全样本', '期内n', '同日命中', '±3日命中', '随机基准'))
    for nm, _, _, _ in GROUPS:
        g = G[nm]
        n = int(g['inper'].sum())
        ra = float(g['hitA'][g['inper']].mean()) if n else np.nan
        rb = float(g['hitB'][g['inper']].mean()) if n else np.nan
        print('%-22s %7d %7d %9s %9s %9.2f%%'
              % (nm, len(g['idx']), n, ('%.2f%%' % (100 * ra)) if n else 'n/a',
                 ('%.2f%%' % (100 * rb)) if n else 'n/a', 100 * base_rate))
        put('OVERLAP', nm, 'alignA_same_day', 0, None,
            {'n': n, 'n_all': len(g['idx']), 'hit_n': int(g['hitA'][g['inper']].sum()),
             'hit_rate': ra, 'random_baseline': base_rate})
        put('OVERLAP', nm, 'alignB_near3', 0, None,
            {'n': n, 'n_all': len(g['idx']), 'hit_n': int(g['hitB'][g['inper']].sum()),
             'hit_rate': rb, 'random_baseline': base_rate})

    print('\n' + '=' * 78)
    print('【Q6-2】低量突破 vs 高量突破：HVT 命中率差异（HVT 是否偏好放量突破）')
    gl, gh = G['A_三花+低量突破'], G['A2_三花+高量突破']
    n1, n2 = int(gl['inper'].sum()), int(gh['inper'].sum())
    r1 = float(gl['hitB'][gl['inper']].mean()) if n1 else np.nan
    r2 = float(gh['hitB'][gh['inper']].mean()) if n2 else np.nan
    r1a = float(gl['hitA'][gl['inper']].mean()) if n1 else np.nan
    r2a = float(gh['hitA'][gh['inper']].mean()) if n2 else np.nan
    print('  低量突破(VR<1.2) ±3日 %.2f%% (n=%d) | 高量突破(VR>=1.2) ±3日 %.2f%% (n=%d) | 差 %+.2f%%'
          % (100 * r1, n1, 100 * r2, n2, 100 * (r1 - r2)))
    print('  低量突破(VR<1.2) 同日 %.2f%% | 高量突破(VR>=1.2) 同日 %.2f%% | 差 %+.2f%%'
          % (100 * r1a, 100 * r2a, 100 * (r1a - r2a)))
    put('VR_INT', '低量-高量', 'alignB_near3', 0, None,
        {'n': n1 + n2, 'hit_rate': r1 - r2, 'hit_rate_lo': r1, 'hit_rate_hi': r2,
         'n_lo': n1, 'n_hi': n2, 'random_baseline': base_rate})
    put('VR_INT', '低量-高量', 'alignA_same_day', 0, None,
        {'n': n1 + n2, 'hit_rate': r1a - r2a, 'hit_rate_lo': r1a, 'hit_rate_hi': r2a,
         'n_lo': n1, 'n_hi': n2, 'random_baseline': base_rate})

    print('\n' + '=' * 78)
    print('【Q6-3】HVT 命中 / 未命中子集表现（±3日口径）')
    print('%-22s %-9s %6s | %-16s %-16s %-16s' % ('组', '子集', 'n', 'T+5 均值/中位',
                                                  'T+10 均值/中位', 'T+20 均值/中位'))
    for nm, _, _, _ in GROUPS:
        g = G[nm]
        for sub_nm, sm in (('HVT命中', g['inper'] & g['hitB']),
                           ('HVT未命中', g['inper'] & ~g['hitB'])):
            idxs = g['idx'][sm]
            if len(idxs) == 0:
                continue
            txt, outs = [], {}
            for N in HOR:
                s = stx(df[g['fmt'] % N].values[idxs])
                outs[N] = s
                txt.append(fmts(s))
                put('SUBSET', nm, sub_nm, N, s,
                    {'fmt': g['fmt'], 'n_grp': int(g['inper'].sum()),
                     'n_hit_grp': int((g['inper'] & g['hitB']).sum())})
            print('%-22s %-9s %6d | %-16s %-16s %-16s'
                  % (nm, sub_nm, len(idxs), txt[0], txt[1], txt[2]))

    print('\n' + '=' * 78)
    print('【Q6-4】关键判定：低量突破「排除 HVT 命中后」是否仍有正超额')
    for N in HOR:
        idx = gl['idx'][gl['inper'] & ~gl['hitB']]
        idh = gh['idx'][gh['inper'] & ~gh['hitB']]
        s = stx(df['Cb0_r%d' % N].values[idx])
        sh = stx(df['Cb0_r%d' % N].values[idh])
        print('  T+%-2d 低量突破∩非HVT n=%-4d 均值 %-8s 中位 %-8s 胜率 %-5s | 高量突破∩非HVT n=%-4d 均值 %s'
              % (N, s.get('n', 0), ('%+.2f%%' % (100 * s['mean'])) if s else 'n/a',
                 ('%+.2f%%' % (100 * s['med'])) if s else 'n/a',
                 ('%.0f%%' % (100 * s['win'])) if s else 'n/a',
                 sh.get('n', 0), ('%+.2f%%' % (100 * sh['mean'])) if sh else 'n/a'))
        put('NO_HVT', 'A_三花+低量突破∩非HVT', 'excl_hvt_near3', N, s,
            {'cmp_group': 'A2_三花+高量突破∩非HVT', 'cmp_mean': sh.get('mean') if sh else np.nan,
             'cmp_n': sh.get('n', 0) if sh else 0})

    print('\n' + '=' * 78)
    print('【Q7】体系重叠：全部锚点日与 HVT 的重叠（三套体系是否识别同一批机会）')
    all_ok = np.zeros(len(df), dtype=bool)
    all_hit = np.zeros(len(df), dtype=bool)
    for nm, _, _, _ in GROUPS:
        g = G[nm]
        all_ok[g['idx'][g['inper']]] = True
        all_hit[g['idx'][g['inper'] & g['hitB']]] = True
    print('  锚点日落在 HVT 期内的全部事件 %d | ±3日命中 %d (%.2f%%) | 随机基准 %.2f%%'
          % (int(all_ok.sum()), int((all_ok & all_hit).sum()),
             100 * all_hit[all_ok].mean(), 100 * base_rate))
    put('SUMMARY', 'ALL', 'alignB_near3', 0, None,
        {'n': int(all_ok.sum()), 'hit_n': int((all_ok & all_hit).sum()),
         'hit_rate': float(all_hit[all_ok].mean()), 'random_baseline': base_rate})

    out = pd.DataFrame(rows)
    fp = os.path.join(RDRE, 'three_flower_v2_hvt_orth.csv')
    out.to_csv(fp, index=False, encoding='utf-8-sig')
    print('\n已写 %s (%d 行)' % (fp, len(out)))


if __name__ == '__main__':
    main()
