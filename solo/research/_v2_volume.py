# -*- coding: utf-8 -*-
"""三花聚顶 V2 · 突破量能机制研究（主分析层）

输入: out/tf_v2.parquet, out/tf_base.parquet, out/tf_events_dim.parquet
输出: report_daily/research/
  three_flower_v2_volume_curve.csv      VR 分层 / 突破前量能 / Breakout_VR÷PreBreakout_VR
  three_flower_v2_breakout_analysis.csv 幅度 / 涨幅 / 位置 / 结构紧密 / 价格结构 / 四象限
  three_flower_v2_regime.csv            BULL/NORMAL/RANGE/BEAR 交叉
  three_flower_v2_marketcap.csv         市值 / 换手 交叉
  three_flower_v2_oos.csv               TRAIN / VALID / OOS + 逐年 + VR 阈值稳健性
  three_flower_v2_significance.csv      bootstrap CI / Welch-t / Mann-Whitney U / Cliff's delta
  three_flower_v2_curve.png             VR→T+5/10/20 曲线 + 四象限热图

口径:
  - 收益列已扣双边成本 0.30%；MAE/MFE 为未扣成本口径
  - 「三花形成」= V1 核心有效结构集 (has_tf, n=13269)，与 V1 报告的 13263 同口径
  - 「三花+突破」= 核心结构 + buffer=0 首次突破 (Cb0_k>=0, n=4604)
  - adv     : 配对超额(自我剔除)，参照=同 offset 的「其余全体首板」，同一年份集合
  - adv_fix : 配对超额(固定基准)，参照=同 offset 的「无三花首板」
"""
import os
import json
import math
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
ROOT = os.path.dirname(HERE)
RD = os.path.join(ROOT, 'report_daily', 'research')
os.makedirs(RD, exist_ok=True)

DATE = '20260925'
HOR = (1, 3, 5, 10, 20)
N_WIN = 46
KMAX = 25
MINB = 8                     # 配对桶最小样本
MIN_LAYER = 30               # 分层最小样本
COST = 0.003

REG_MAP = {'strong': 'BULL', 'neutral': 'NORMAL', 'weak': 'RANGE', 'bear': 'BEAR'}
REG_ORDER = ['BULL', 'NORMAL', 'RANGE', 'BEAR']
PERIODS = ['TRAIN', 'VALID', 'OOS']

VR_BINS = [(0, 0.6, '<0.6'), (0.6, 0.8, '0.6-0.8'), (0.8, 1.0, '0.8-1.0'),
           (1.0, 1.1, '1.0-1.1'), (1.1, 1.2, '1.1-1.2'), (1.2, 1.3, '1.2-1.3'),
           (1.3, 1.5, '1.3-1.5'), (1.5, 2.0, '1.5-2.0'), (2.0, np.inf, '>2.0')]
VR_MID = [0.45, 0.7, 0.9, 1.05, 1.15, 1.25, 1.4, 1.75, 2.5]

AMP_BINS = [(0, 0.01, '<1%'), (0.01, 0.02, '1-2%'), (0.02, 0.03, '2-3%'),
            (0.03, 0.05, '3-5%'), (0.05, 0.07, '5-7%'), (0.07, 0.10, '7-10%'),
            (0.10, np.inf, '>10%')]
AMPCL_BINS = [(-np.inf, 0.0, '<0%'), (0.0, 0.01, '0-1%'), (0.01, 0.02, '1-2%'),
              (0.02, 0.03, '2-3%'), (0.03, 0.05, '3-5%'), (0.05, 0.07, '5-7%'),
              (0.07, np.inf, '>7%')]
RET_BINS = [(-np.inf, 0.02, '<2%'), (0.02, 0.03, '2-3%'), (0.03, 0.05, '3-5%'),
            (0.05, 0.07, '5-7%'), (0.07, 0.10, '7-10%'), (0.10, np.inf, '>10%')]
DD_BINS = [(0, 0.03, '0-3%'), (0.03, 0.05, '3-5%'), (0.05, 0.08, '5-8%'),
           (0.08, 0.10, '8-10%'), (0.10, 1.01, '>10%')]
PB_BINS = [(0, 0.6, 'PreVR<0.6'), (0.6, 0.8, '0.6-0.8'), (0.8, 1.0, '0.8-1.0'),
           (1.0, np.inf, '1.0+')]
POS_BINS = [(0, 0.5, '<0.5'), (0.5, 0.7, '0.5-0.7'), (0.7, 0.85, '0.7-0.85'),
            (0.85, 1.01, '>0.85')]
VRR_BINS = [(0, 1.0, '<1.0'), (1.0, 1.5, '1.0-1.5'), (1.5, 2.5, '1.5-2.5'), (2.5, np.inf, '>2.5')]
ABS_BINS = [(-np.inf, 0.0, '<0元'), (0.0, 0.1, '0-0.1元'), (0.1, 0.3, '0.1-0.3元'),
            (0.3, 1.0, '0.3-1元'), (1.0, np.inf, '>1元')]
AR_BINS = [(0, 0.1, '<0.1'), (0.1, 0.25, '0.1-0.25'), (0.25, 0.5, '0.25-0.5'),
           (0.5, 1.0, '0.5-1.0'), (1.0, np.inf, '>1.0')]
SPAN_BINS = [(0, 4, '0-4'), (4, 6, '4-6'), (6, 8, '6-8'), (8, 10, '8-10'), (10, 99, '>10')]
GAP_BINS = [(2, 3, '2-3'), (3, 4, '3-4'), (4, 6, '4-6'), (6, 99, '>6')]
FP_BINS = [(-np.inf, 0.0, '<0%'), (0.0, 0.01, '0-1%'), (0.01, 0.03, '1-3%'),
           (0.03, 0.06, '3-6%'), (0.06, np.inf, '>6%')]
FV_BINS = [(-np.inf, -0.2, '<-20%'), (-0.2, -0.05, '-20~-5%'), (-0.05, 0.05, '-5~5%'),
           (0.05, 0.2, '5-20%'), (0.2, np.inf, '>20%')]
BODY_BINS = [(-np.inf, 0.0, '<0%(阴线)'), (0.0, 0.02, '0-2%'), (0.02, 0.05, '2-5%'),
             (0.05, 0.08, '5-8%'), (0.08, np.inf, '>8%')]
USH_BINS = [(0, 0.01, '<1%'), (0.01, 0.02, '1-2%'), (0.02, 0.04, '2-4%'), (0.04, np.inf, '>4%')]
LSH_BINS = [(0, 0.005, '<0.5%'), (0.005, 0.015, '0.5-1.5%'), (0.015, 0.03, '1.5-3%'),
            (0.03, np.inf, '>3%')]


# ══════════════════════════ 统计工具 ══════════════════════════
def _fin(x):
    x = np.asarray(x, dtype=np.float64)
    return x[np.isfinite(x)]


def stx(x, tag=''):
    x = _fin(x)
    n = len(x)
    if n == 0:
        return {'tag': tag, 'n': 0}
    w = x > 0
    pos = float(x[w].sum())
    neg = float(-x[~w].sum())
    pf = (pos / neg) if neg > 0 else (np.inf if pos > 0 else np.nan)
    return {'tag': tag, 'n': int(n), 'mean': float(x.mean()), 'med': float(np.median(x)),
            'win': float(w.mean()), 'p25': float(np.percentile(x, 25)),
            'p75': float(np.percentile(x, 75)), 'pf': pf,
            'std': float(x.std(ddof=1)) if n > 1 else np.nan,
            'mean_all': float(x.mean()), 'min': float(x.min()), 'max': float(x.max())}


def pf_out(v):
    return None if (v is None or not np.isfinite(v)) else round(float(v), 3)


def rankdata(x):
    o = np.argsort(x, kind='stable')
    r = np.empty(len(x), dtype=np.float64)
    r[o] = np.arange(1, len(x) + 1, dtype=np.float64)
    # 并列取平均
    xs = x[o]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            r[o[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return r


def spearman(x, y):
    x, y = _fin(np.asarray(x, float)), _fin(np.asarray(y, float))
    if len(x) < 5 or len(x) != len(y):
        return np.nan
    rx, ry = rankdata(x), rankdata(y)
    rx = rx - rx.mean(); ry = ry - ry.mean()
    d = math.sqrt(float((rx ** 2).sum()) * float((ry ** 2).sum()))
    return float((rx * ry).sum() / d) if d > 0 else np.nan


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def welch_t(a, b):
    a, b = _fin(a), _fin(b)
    if len(a) < 10 or len(b) < 10:
        return np.nan, np.nan, np.nan
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se <= 0:
        return np.nan, np.nan, np.nan
    t = (a.mean() - b.mean()) / se
    p = 2 * (1 - norm_cdf(abs(t)))
    return float(t), float(p), float(a.mean() - b.mean())


def mwu_cliff(a, b):
    """Mann-Whitney U 正态近似 p 值 + Cliff's delta 效应量"""
    a, b = _fin(a), _fin(b)
    n1, n2 = len(a), len(b)
    if n1 < 10 or n2 < 10:
        return np.nan, np.nan, np.nan
    allv = np.concatenate([a, b])
    r = rankdata(allv)
    r1 = r[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u = u1
    mu = n1 * n2 / 2.0
    # 并列修正
    _, cnt = np.unique(allv, return_counts=True)
    tie = float((cnt ** 3 - cnt).sum())
    n = n1 + n2
    sd = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))))
    z = (u - mu) / sd if sd > 0 else 0.0
    p = 2 * (1 - norm_cdf(abs(z)))
    delta = 2 * u / (n1 * n2) - 1
    return float(z), float(p), float(delta)


def boot_diff(a, b, B=2000, seed=7, stat='mean', chunk=200):
    """两组差值(1-2)的 bootstrap 95% CI，分块以控内存"""
    a, b = _fin(a), _fin(b)
    n1, n2 = len(a), len(b)
    if n1 < 20 or n2 < 20:
        return np.nan, np.nan, np.nan
    f = np.mean if stat == 'mean' else np.median
    rng = np.random.default_rng(seed)
    out = np.empty(B, dtype=np.float64)
    i = 0
    while i < B:
        m = min(chunk, B - i)
        ia = rng.integers(0, n1, size=(m, n1))
        ib = rng.integers(0, n2, size=(m, n2))
        out[i:i + m] = f(a[ia], axis=1) - f(b[ib], axis=1)
        i += m
    return float(f(a) - f(b)), float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


# ══════════════════════════ 数据加载 ══════════════════════════
def load():
    tb = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet'))
    tv = pd.read_parquet(os.path.join(OUT, 'tf_v2.parquet'))
    dim = pd.read_parquet(os.path.join(OUT, 'tf_events_dim.parquet'))
    assert len(tb) == len(tv) == len(dim)
    assert (tb['ts_code'].values == tv['ts_code'].values).all()
    assert (tb['ts_code'].values == dim['ts_code'].values).all()

    df = tb.copy()
    for c in ['amp_hi', 'amp_cl', 'amp_abs', 'amp_ratio', 'stage_amp', 'brk_ret', 'brk_body',
              'ush', 'lsh', 'ush_r', 'lsh_r', 'brk_pos', 'brk_gap2', 'pbvr1', 'pbvr3',
              'pbvr5', 'vr_ratio3', 'brk_lag', 'brk_off']:
        df[c] = tv[c].values
    for c in ['c%d_r%d' % (K, N) for K in range(17, KMAX + 1) for N in HOR]:
        df[c] = tv[c].values
    df['regime_raw'] = dim['regime'].values
    df['regime'] = pd.Series(dim['regime'].values).map(REG_MAP).values
    df['mv_grp'] = dim['mv_grp'].values
    df['to_grp'] = dim['to_grp'].values
    df['board'] = dim['board'].values
    df['t0_quality'] = dim['t0_quality'].values
    df['_yr'] = df['trade_date'].astype(str).str[:4].values
    yy = df['trade_date'].values // 10000
    df['period'] = np.where(yy <= 2023, 'TRAIN', np.where(yy <= 2025, 'VALID', 'OOS'))

    df['vr'] = df['brk_b0_vr'].values.astype(np.float64)
    df['amp'] = df['amp_hi'].values.astype(np.float64)
    df['gap12'] = (df['f2_off'] - df['f1_off']).values
    df['gap23'] = (df['f3_off'] - df['f2_off']).values
    df['f_p12'] = (df['P2'] / df['P1'] - 1.0).values
    df['f_p23'] = (df['P3'] / df['P2'] - 1.0).values
    df['f_v12'] = (df['V2'] / df['V1'] - 1.0).values
    df['f_v23'] = (df['V3'] / df['V2'] - 1.0).values
    return df


# ══════════════════════════ 配对超额 ══════════════════════════
def bucket_adv(df, fmt, N, mask, koff, ref_mask, minn=MINB):
    """offset 分层配对超额：Σ n_K·[med(组|K) − med(参照|K)] / Σ n_K

    参照同时限定为「同一年份集合」以避免年份基准错配。
    """
    gcol = fmt % N
    if gcol not in df.columns:
        return {}
    gv = df[gcol].values
    yr = df['_yr'].values
    yrs = np.unique(yr[mask]) if mask.any() else np.array([], dtype=yr.dtype)
    dm = np.isin(yr, yrs)
    diffs, ns = [], []
    for K in range(KMAX + 1):
        ccol = 'c%d_r%d' % (K, N)
        if ccol not in df.columns:
            continue
        m = mask & (koff == K) & np.isfinite(gv)
        n = int(m.sum())
        if n < minn:
            continue
        cv = df[ccol].values
        ref = ref_mask & dm & np.isfinite(cv)
        nr = int(ref.sum())
        if nr < minn:
            continue
        diffs.append(float(np.median(gv[m]) - np.median(cv[ref])))
        ns.append(n)
    if not diffs:
        return {'adv': np.nan, 'adv_n': 0, 'adv_posb': np.nan, 'adv_nb': 0, 'adv_t': np.nan}
    d = np.asarray(diffs)
    w = np.asarray(ns, dtype=np.float64)
    t = float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))) if len(d) >= 3 and d.std(ddof=1) > 0 else np.nan
    return {'adv': float((w * d).sum() / w.sum()), 'adv_n': int(w.sum()),
            'adv_posb': float((d > 0).mean()), 'adv_nb': int(len(d)), 'adv_t': t}


def layer(df, rows, section, name, mask, fmt, koff, pool, ref_self, ref_fix,
          mae=None, mfe=None, horizons=HOR):
    mask = np.asarray(mask, dtype=bool)
    n0 = int(mask.sum())
    for N in horizons:
        col = fmt % N
        s = stx(df[col].values[mask])
        r = {'section': section, 'group': name, 'n': n0, 'n_obs': s.get('n', 0), 'horizon': N,
             'mean': s.get('mean'), 'med': s.get('med'), 'win': s.get('win'),
             'p25': s.get('p25'), 'p75': s.get('p75'), 'pf': pf_out(s.get('pf')),
             'std': s.get('std')}
        r['mae_med'] = float(np.nanmedian(mae[mask])) if mae is not None and n0 else np.nan
        r['mfe_med'] = float(np.nanmedian(mfe[mask])) if mfe is not None and n0 else np.nan
        if n0 >= MINB:
            a1 = bucket_adv(df, fmt, N, mask, koff, ref_self)
            a2 = bucket_adv(df, fmt, N, mask, koff, ref_fix)
            r.update({'adv': a1.get('adv'), 'adv_posb': a1.get('adv_posb'),
                      'adv_nb': a1.get('adv_nb'), 'adv_t': a1.get('adv_t'),
                      'adv_fix': a2.get('adv'), 'adv_fix_posb': a2.get('adv_posb'),
                      'adv_fix_nb': a2.get('adv_nb'), 'adv_fix_t': a2.get('adv_t')})
        else:
            r.update({k: np.nan for k in ('adv', 'adv_posb', 'adv_nb', 'adv_t',
                                          'adv_fix', 'adv_fix_posb', 'adv_fix_nb', 'adv_fix_t')})
            r['note'] = '样本不足'
        rows.append(r)


def bins_of(v, bins, lo_open=True):
    out = np.full(len(v), None, dtype=object)
    for lo, hi, nm in bins:
        m = np.isfinite(v) & (v >= lo) if lo_open else np.isfinite(v) & (v > lo)
        m = m & (v < hi)
        out[m] = nm
    return out


# ══════════════════════════ 主流程 ══════════════════════════
def main():
    df = load()
    n_all = len(df)
    m_all = np.ones(n_all, dtype=bool)
    has_tf = df['has_tf'].values.astype(bool)
    brk = has_tf & (df['Cb0_k'].fillna(-1).values >= 0)
    m_tf, m_brk = has_tf, brk
    m_nobrk = has_tf & ~brk
    m_notf = ~has_tf
    k_A = df['k_A'].fillna(-1).astype(int).values
    k_B = df['Cb0_k'].fillna(-1).astype(int).values
    ref_self = m_all
    ref_fix = m_notf

    print('=' * 78)
    print('三花聚顶 V2 · 突破量能机制研究')
    print('首板 %d | 三花形成 %d (%.1f%%) | 三花+突破 %d (%.1f%%) | 三花无突破 %d | 无三花 %d'
          % (n_all, m_tf.sum(), 100 * m_tf.mean(), m_brk.sum(), 100 * m_brk.mean(),
             m_nobrk.sum(), m_notf.sum()))
    print('区间 %d ~ %d' % (df['trade_date'].min(), df['trade_date'].max()))
    print('分期: ' + ' | '.join('%s %d' % (p, int((df['period'] == p).sum())) for p in PERIODS))
    vr0 = df['vr'].values
    print('突破组 VR: VR<1.2 %d | VR>=1.2 %d (与 V1 报告 1313 / 3267 对齐校验)'
          % (int((m_brk & (vr0 < 1.2)).sum()), int((m_brk & (vr0 >= 1.2)).sum())))

    vol_rows, ana_rows, reg_rows, mv_rows, oos_rows, sig_rows = [], [], [], [], [], []

    # ═══ 1. VR 分层 ═══
    print('\n' + '=' * 78)
    print('【1】VR 分层（固定三花定义，只改突破量能）')
    vrb = bins_of(vr0, VR_BINS)
    for _, _, nm in VR_BINS:
        m = m_brk & (vrb == nm)
        layer(df, vol_rows, 'VR_全体突破', nm, m, 'Cb0_r%d', k_B, m_all, ref_self, ref_fix,
              mae=df['Cb0_mae'].values, mfe=df['Cb0_mfe'].values)
    for _, _, nm in VR_BINS:
        m = m_brk & (vrb == nm) & df['d_H1'].values.astype(bool)
        layer(df, vol_rows, 'VR_仅H1结构', nm, m, 'Cb0_r%d', k_B, m_all, ref_self, ref_fix)
    print('%-28s %-6s %8s %8s %8s %8s %7s %7s %8s %8s' % (
        'VR区间', 'n', 'T+1', 'T+5', 'T+10', 'T+20', 'win10', 'pf10', 'med10', 'advfix10'))
    vrmap = {}
    for r in vol_rows:
        if r['section'] == 'VR_全体突破':
            vrmap[(r['group'], r['horizon'])] = r
    for _, _, nm in VR_BINS:
        m = m_brk & (vrb == nm)
        s = {N: stx(df['Cb0_r%d' % N].values[m]) for N in (1, 5, 10, 20)}
        if s[10]['n'] == 0:
            print('%-28s %-6d  —' % (nm, int(m.sum())))
            continue
        g = lambda N, k: vrmap.get((nm, N), {}).get(k)
        print('%-28s %-6d %+7.2f%% %+7.2f%% %+7.2f%% %+7.2f%% %6.0f%% %7s %+7.2f%% %+7.2f%%' % (
            nm, int(m.sum()), 100 * s[1]['mean'], 100 * s[5]['mean'], 100 * s[10]['mean'],
            100 * s[20]['mean'], 100 * s[10]['win'],
            ('%.2f' % s[10]['pf']) if np.isfinite(s[10]['pf']) else '-',
            100 * s[10]['med'], 100 * (g(10, 'adv_fix') if g(10, 'adv_fix') is not None else np.nan)))

    # ═══ 2. 单调性 / 拐点 ═══
    print('\n【2】VR → 未来收益 单调性与拐点')
    means = {N: [] for N in HOR}
    meds = {N: [] for N in HOR}
    ns = []
    for _, _, nm in VR_BINS:
        m = m_brk & (vrb == nm)
        for N in HOR:
            s = stx(df['Cb0_r%d' % N].values[m])
            means[N].append(s.get('mean', np.nan))
            meds[N].append(s.get('med', np.nan))
        ns.append(int(m.sum()))
    for N in HOR:
        print('  VR→T+%-3d Spearman(mean) %+0.3f  Spearman(median) %+0.3f' % (
            N, spearman(VR_MID, means[N]), spearman(VR_MID, meds[N])))
    vr_mono = pd.DataFrame({'vr_bin': [b[2] for b in VR_BINS], 'n': ns,
                            **{'mean_T%d' % N: means[N] for N in HOR},
                            **{'med_T%d' % N: meds[N] for N in HOR}})

    # ═══ 3~8. 幅度 / 涨幅 / 位置 / 结构 / 前量 / 价格结构 ═══
    print('\n' + '=' * 78)
    print('【3-8】突破幅度 / 涨幅 / 位置 / 结构紧密 / 突破前量能 / 突破日结构')
    specs = [
        ('AMP_突破幅度(high/hi_lvl-1)', 'amp', AMP_BINS, m_brk),
        ('AMPCL_收盘突破幅度', 'amp_cl', AMPCL_BINS, m_brk),
        ('AMPABS_突破价-阶段最高价(元)', 'amp_abs', ABS_BINS, m_brk),
        ('AMPRATIO_突破幅度/阶段振幅', 'amp_ratio', AR_BINS, m_brk),
        ('BRKRET_突破日涨幅', 'brk_ret', RET_BINS, m_brk),
        ('DDBIN_三花最大回撤', 'struct_dd', DD_BINS, m_tf),
        ('SPAN_三花持续时间', 'span', SPAN_BINS, m_tf),
        ('GAP12_Flower1-2间隔', 'gap12', GAP_BINS, m_tf),
        ('GAP23_Flower2-3间隔', 'gap23', GAP_BINS, m_tf),
        ('FPRICE12_Flower1-2价格差', 'f_p12', FP_BINS, m_tf),
        ('FPRICE23_Flower2-3价格差', 'f_p23', FP_BINS, m_tf),
        ('FVOL12_Flower1-2量差', 'f_v12', FV_BINS, m_tf),
        ('FVOL23_Flower2-3量差', 'f_v23', FV_BINS, m_tf),
        ('PBVR3_突破前3日量比', 'pbvr3', PB_BINS, m_brk),
        ('PBVR1_突破前1日量比', 'pbvr1', PB_BINS, m_brk),
        ('VRRATIO_VR/PreVR', 'vr_ratio3', VRR_BINS, m_brk),
        ('BODY_突破日实体', 'brk_body', BODY_BINS, m_brk),
        ('USH_突破日上影线', 'ush', USH_BINS, m_brk),
        ('LSH_突破日下影线', 'lsh', LSH_BINS, m_brk),
        ('POS_突破日收盘位置', 'brk_pos', POS_BINS, m_brk),
    ]
    for sec, col, bins, base_m in specs:
        v = df[col].values.astype(np.float64)
        bb = bins_of(v, bins)
        fmt, koff = ('Cb0_r%d', k_B) if base_m is m_brk else ('A_r%d', k_A)
        for _, _, nm in bins:
            m = base_m & (bb == nm)
            layer(df, ana_rows, sec, nm, m, fmt, koff, m_all, ref_self, ref_fix,
                  mae=df['Cb0_mae'].values if base_m is m_brk else df['A_mae'].values,
                  mfe=df['Cb0_mfe'].values if base_m is m_brk else df['A_mfe'].values)
        # 前量能三项另存 volume_curve
        if sec.startswith('PBVR') or sec.startswith('VRRATIO'):
            for _, _, nm in bins:
                m = base_m & (bb == nm)
                layer(df, vol_rows, sec, nm, m, fmt, koff, m_all, ref_self, ref_fix)
        print('  %-30s 档位 %s' % (sec.split('_')[0], ', '.join(
            '%s(n=%d)' % (b[2], int((base_m & (bb == b[2])).sum())) for b in bins)))

    # ═══ 9. 四象限 ═══
    print('\n' + '=' * 78)
    print('【9】四象限（突破幅度 × 突破量能）')
    amp_med = float(np.nanmedian(df['amp'].values[m_brk]))
    vr_med = float(np.nanmedian(df['vr'].values[m_brk]))
    print('  中位数分割点（数据驱动，无预设）：突破幅度 %.2f%% | 突破量能 %.2f' % (
        100 * amp_med, vr_med))
    QUAD = {'A_低量+低幅': (df['vr'].values < vr_med) & (df['amp'].values < amp_med),
            'B_高量+低幅': (df['vr'].values >= vr_med) & (df['amp'].values < amp_med),
            'C_低量+高幅': (df['vr'].values < vr_med) & (df['amp'].values >= amp_med),
            'D_高量+高幅': (df['vr'].values >= vr_med) & (df['amp'].values >= amp_med)}
    for nm, qm in QUAD.items():
        m = m_brk & qm
        layer(df, ana_rows, 'QUAD_四象限(中位分割)', nm, m, 'Cb0_r%d', k_B, m_all, ref_self, ref_fix,
              mae=df['Cb0_mae'].values, mfe=df['Cb0_mfe'].values)
        s5, s10, s20 = (stx(df['Cb0_r%d' % N].values[m]) for N in (5, 10, 20))
        print('  %-14s n=%-5d T+5 %+0.2f%% (med %+0.2f%%) | T+10 %+0.2f%% (med %+0.2f%%) | T+20 %+0.2f%%'
              % (nm, int(m.sum()), 100 * s5['mean'], 100 * s5['med'], 100 * s10['mean'],
                 100 * s10['med'], 100 * s20['mean']))
    print('  --- 分割点敏感性网格 (T+10 均值) ---')
    for at in (0.01, 0.02, 0.03):
        for vt in (0.8, 1.0, 1.2):
            q = {'A': (df['vr'].values < vt) & (df['amp'].values < at),
                 'B': (df['vr'].values >= vt) & (df['amp'].values < at),
                 'C': (df['vr'].values < vt) & (df['amp'].values >= at),
                 'D': (df['vr'].values >= vt) & (df['amp'].values >= at)}
            txt = []
            for kk in 'ABCD':
                m = m_brk & q[kk]
                s = stx(df['Cb0_r10'].values[m])
                layer(df, ana_rows, 'QUADGRID_网格(幅%.0f%%/量%.1f)' % (100 * at, vt),
                      kk, m, 'Cb0_r%d', k_B, m_all, ref_self, ref_fix)
                txt.append('%s n%-4d %+0.2f%%' % (kk, int(m.sum()), 100 * (s.get('mean') or np.nan)))
            print('    幅%-4s 量%-4s  %s' % ('%.0f%%' % (100 * at), vt, ' | '.join(txt)))

    # ═══ 10/13. 反事实 A/B/C/D ═══
    print('\n' + '=' * 78)
    print('【10/13】反事实：三花 Alpha / 突破 Alpha / 低量突破 Alpha 拆解')
    cf = {
        'B_三花+突破(全体)': (m_brk, 'Cb0_r%d', k_B),
        'C_三花形成无突破': (m_nobrk, 'A_r%d', k_A),
        'A_三花+低量突破(VR<1.2)': (m_brk & (vr0 < 1.2), 'Cb0_r%d', k_B),
        'A2_三花+高量突破(VR>=1.2)': (m_brk & (vr0 >= 1.2), 'Cb0_r%d', k_B),
        'E_三花+突破收盘破MA20': (m_brk & (df['brk_b0_abv'].fillna(0).values <= 0.5), 'Cb0_r%d', k_B),
    }
    for nm, (m, fmt, koff) in cf.items():
        layer(df, ana_rows, 'CF_反事实', nm, m, fmt, koff, m_all, ref_self, ref_fix,
              mae=df['Cb0_mae'].values if fmt.startswith('Cb0') else df['A_mae'].values,
              mfe=df['Cb0_mfe'].values if fmt.startswith('Cb0') else df['A_mfe'].values)
    mD = m_notf
    layer(df, ana_rows, 'CF_反事实', 'D_无三花(offset10)', mD, 'c10_r%d', np.full(n_all, 10),
          m_all, ref_self, ref_fix)
    layer(df, ana_rows, 'CF_反事实', 'F_全体首板(offset10)', m_all, 'c10_r%d', np.full(n_all, 10),
          m_all, np.zeros(n_all, bool), ref_fix)
    print('%-26s %-6s %8s %8s %8s %8s %7s %8s' % ('组', 'n', 'T+5', 'T+10', 'T+20', 'med10', 'pf10', 'adv_fix10'))
    for nm, _, _ in [('B_三花+突破(全体)', 0, 0), ('C_三花形成无突破', 0, 0),
                     ('A_三花+低量突破(VR<1.2)', 0, 0), ('A2_三花+高量突破(VR>=1.2)', 0, 0),
                     ('D_无三花(offset10)', 0, 0)]:
        sub = [r for r in ana_rows if r['section'] == 'CF_反事实' and r['group'] == nm]
        if not sub:
            continue
        g = {r['horizon']: r for r in sub}
        f = lambda N, k: g.get(N, {}).get(k)
        print('%-26s %-6d %+7.2f%% %+7.2f%% %+7.2f%% %+7.2f%% %7s %+7.2f%%' % (
            nm, g[10]['n'], 100 * f(5, 'mean'), 100 * f(10, 'mean'), 100 * f(20, 'mean'),
            100 * f(10, 'med'), ('%.2f' % f(10, 'pf')) if f(10, 'pf') else '-',
            100 * (f(10, 'adv_fix') or np.nan)))

    # ═══ 11. Regime 交叉 ═══
    print('\n' + '=' * 78)
    print('【11】市场 Regime 交叉验证（BULL/NORMAL/RANGE/BEAR，按首板日 T0 判定）')
    for rg in REG_ORDER:
        pool = (df['regime'].values == rg)
        for nm, (m, fmt, koff) in cf.items():
            layer(df, reg_rows, 'REGIME_%s' % rg, nm, m & pool, fmt, koff,
                  pool, pool, pool & m_notf)
        layer(df, reg_rows, 'REGIME_%s' % rg, 'D_无三花(offset10)', m_notf & pool,
              'c10_r%d', np.full(n_all, 10), pool, pool, pool & m_notf)
        vv = bins_of(vr0, VR_BINS)
        for _, _, nm in VR_BINS:
            layer(df, reg_rows, 'REGIME_%s_VR明细' % rg, nm,
                  m_brk & pool & (vv == nm), 'Cb0_r%d', k_B, pool, pool, pool & m_notf)
        sub = [r for r in reg_rows if r['section'] == 'REGIME_%s' % rg and r['horizon'] == 10]
        txt = ' | '.join('%s n%-5d %+0.2f%% advF%+0.2f%%' % (
            r['group'][:12], r['n'], 100 * (r['mean'] or np.nan),
            100 * (r['adv_fix'] or np.nan)) for r in sub)
        print('  %-7s 首板%-6d %s' % (rg, int(pool.sum()), txt))

    # ═══ 12. 市值 / 换手 ═══
    print('\n' + '=' * 78)
    print('【12】市值 / 换手交叉验证（daily_basic 仅覆盖 2023+）')
    for col, order in (('mv_grp', ['Small', 'Mid', 'Large']),
                       ('to_grp', ['Low', 'Mid', 'High'])):
        vals = df[col].values
        for lv in order:
            pool = pd.Series(vals).eq(lv).values & pd.notna(vals)
            for nm, (m, fmt, koff) in cf.items():
                layer(df, mv_rows, '%s_%s' % (col, lv), nm, m & pool, fmt, koff,
                      pool, pool, pool & m_notf)
            layer(df, mv_rows, '%s_%s' % (col, lv), 'D_无三花(offset10)', m_notf & pool,
                  'c10_r%d', np.full(n_all, 10), pool, pool, pool & m_notf)
            vv2 = bins_of(vr0, VR_BINS)
            for _, _, nm in VR_BINS:
                layer(df, mv_rows, '%s_%s_VR明细' % (col, lv), nm,
                      m_brk & pool & (vv2 == nm), 'Cb0_r%d', k_B, pool, pool, pool & m_notf)
            sub = [r for r in mv_rows if r['section'] == '%s_%s' % (col, lv) and r['horizon'] == 10]
            print('  %-12s 首板%-6d %s' % (lv, int(pool.sum()), ' | '.join(
                '%s n%-5d %+0.2f%%' % (r['group'][:10], r['n'], 100 * (r['mean'] or np.nan))
                for r in sub)))

    # ═══ 14. 统计显著性 ═══
    print('\n' + '=' * 78)
    print('【14】统计显著性（Welch-t / Mann-Whitney U / Cliff-delta / bootstrap CI）')
    vv = bins_of(vr0, VR_BINS)
    pairs = [
        ('三花形成 vs 无三花', m_tf, 'A_r%d', m_notf, 'c10_r%d', False),
        ('三花+突破 vs 三花形成', m_brk, 'Cb0_r%d', m_nobrk, 'A_r%d', True),
        ('低量突破(<1.2) vs 高量突破(>=1.2)', m_brk & (vr0 < 1.2), 'Cb0_r%d',
         m_brk & (vr0 >= 1.2), 'Cb0_r%d', True),
        ('低量突破(<1.2) vs 三花形成', m_brk & (vr0 < 1.2), 'Cb0_r%d', m_nobrk, 'A_r%d', True),
        ('低量突破(<1.2) vs 无三花', m_brk & (vr0 < 1.2), 'Cb0_r%d', m_notf, 'c10_r%d', True),
        ('VR<0.6 vs VR>2.0', m_brk & (vv == '<0.6'), 'Cb0_r%d', m_brk & (vv == '>2.0'), 'Cb0_r%d', True),
        ('四象限A(低量低幅) vs D(高量高幅)', m_brk & QUAD['A_低量+低幅'], 'Cb0_r%d',
         m_brk & QUAD['D_高量+高幅'], 'Cb0_r%d', True),
    ]
    for nm, m1, f1, m2, f2, tadj in pairs:
        for N in (5, 10, 20):
            a = df[f1 % N].values[m1]
            b = df[f2 % N].values[m2]
            t, p, d = welch_t(a, b)
            z, pm, delta = mwu_cliff(a, b)
            dm, lo, hi = boot_diff(a, b, B=2000, stat='mean')
            ab, alo, ahi = boot_diff(a, b, B=2000, stat='median')
            sig_rows.append(dict(pair=nm, horizon=N, n1=int(m1.sum()), n2=int(m2.sum()),
                                 mean1=float(np.nanmean(a)) if len(_fin(a)) else np.nan,
                                 mean2=float(np.nanmean(b)) if len(_fin(b)) else np.nan,
                                 t=t, t_p=p, mwu_z=z, mwu_p=pm, cliff_delta=delta,
                                 d_mean=dm, d_mean_lo=lo, d_mean_hi=hi,
                                 d_med=ab, d_med_lo=alo, d_med_hi=ahi,
                                 boot_sig_mean=(bool(lo > 0 or hi < 0) if np.isfinite(lo) else None),
                                 boot_sig_med=(bool(alo > 0 or ahi < 0) if np.isfinite(alo) else None)))
        r10 = [r for r in sig_rows if r['pair'] == nm and r['horizon'] == 10][0]
        print('  %-34s n1=%-5d n2=%-5d T+10 Δmean %+0.2f%% CI[%+0.2f%%,%+0.2f%%] t=%s p=%s δ=%s'
              % (nm, r10['n1'], r10['n2'], 100 * (r10['d_mean'] or np.nan),
                 100 * (r10['d_mean_lo'] or np.nan), 100 * (r10['d_mean_hi'] or np.nan),
                 ('%.2f' % r10['t']) if np.isfinite(r10['t']) else '-',
                 ('%.3f' % r10['t_p']) if np.isfinite(r10['t_p']) else '-',
                 ('%+.3f' % r10['cliff_delta']) if np.isfinite(r10['cliff_delta']) else '-'))

    # ═══ 15. 分期 + VR 阈值样本外纪律 ═══
    print('\n' + '=' * 78)
    print('【15】TRAIN(2021-2023) / VALID(2024-2025) / OOS(2026) 分期')
    for pd_ in PERIODS:
        pool = (df['period'].values == pd_)
        for nm, (m, fmt, koff) in cf.items():
            layer(df, oos_rows, 'PERIOD_%s' % pd_, nm, m & pool, fmt, koff,
                  pool, pool, pool & m_notf)
        layer(df, oos_rows, 'PERIOD_%s' % pd_, 'D_无三花(offset10)', m_notf & pool,
              'c10_r%d', np.full(n_all, 10), pool, pool, pool & m_notf)
        layer(df, oos_rows, 'PERIOD_%s' % pd_, '三花形成(确认日)', m_tf & pool,
              'A_r%d', k_A, pool, pool, pool & m_notf)
        vv3 = bins_of(vr0, VR_BINS)
        for _, _, nm in VR_BINS:
            layer(df, oos_rows, 'PERIOD_%s_VR明细' % pd_, nm,
                  m_brk & pool & (vv3 == nm), 'Cb0_r%d', k_B, pool, pool, pool & m_notf)
        sub = [r for r in oos_rows if r['section'] == 'PERIOD_%s' % pd_ and r['horizon'] == 10]
        print('  %-6s 首板%-6d %s' % (pd_, int(pool.sum()), ' | '.join(
            '%s n%-5d %+0.2f%%' % (r['group'][:10], r['n'], 100 * (r['mean'] or np.nan))
            for r in sub)))
    for y in sorted(df['_yr'].unique()):
        pool = (df['_yr'].values == y)
        layer(df, oos_rows, 'YEAR_%s' % y, '三花形成(确认日)', m_tf & pool, 'A_r%d', k_A, pool, pool, pool & m_notf)
        layer(df, oos_rows, 'YEAR_%s' % y, '三花+突破', m_brk & pool, 'Cb0_r%d', k_B, pool, pool, pool & m_notf)
        layer(df, oos_rows, 'YEAR_%s' % y, '三花+低量突破(VR<1.2)', m_brk & pool & (vr0 < 1.2),
              'Cb0_r%d', k_B, pool, pool, pool & m_notf)
        layer(df, oos_rows, 'YEAR_%s' % y, '三花+高量突破(VR>=1.2)', m_brk & pool & (vr0 >= 1.2),
              'Cb0_r%d', k_B, pool, pool, pool & m_notf)
        layer(df, oos_rows, 'YEAR_%s' % y, '无三花(offset10)', m_notf & pool, 'c10_r%d',
              np.full(n_all, 10), pool, pool, pool & m_notf)

    print('\n  --- VR 阈值扫描（只在 TRAIN 上寻找，再拿到 VALID/OOS 检验）---')
    print('  %-6s %6s %6s %8s %8s %8s %8s %8s' % ('θ', 'n低', 'n高', 'Δd5', 'Δd10', 'Δd20', '低adv10', '高adv10'))
    sweep = []
    tr = (df['period'].values == 'TRAIN')
    for th in np.round(np.arange(0.60, 2.61, 0.05), 2):
        lo_m = m_brk & tr & (vr0 < th)
        hi_m = m_brk & tr & (vr0 >= th)
        row = {'threshold': float(th), 'n_low': int(lo_m.sum()), 'n_high': int(hi_m.sum())}
        for N in (5, 10, 20):
            a = _fin(df['Cb0_r%d' % N].values[lo_m])
            b = _fin(df['Cb0_r%d' % N].values[hi_m])
            row['d%d' % N] = (float(a.mean() - b.mean()) if len(a) >= 20 and len(b) >= 20 else np.nan)
            row['low_med%d' % N] = float(np.median(a)) if len(a) else np.nan
            row['high_med%d' % N] = float(np.median(b)) if len(b) else np.nan
        a10 = bucket_adv(df, 'Cb0_r%d', 10, lo_m, k_B, m_notf & tr & (df['_yr'].values <= '2023'))
        b10 = bucket_adv(df, 'Cb0_r%d', 10, hi_m, k_B, m_notf & tr & (df['_yr'].values <= '2023'))
        row['low_adv10'] = a10.get('adv'); row['high_adv10'] = b10.get('adv')
        ok = (row['n_low'] >= 100 and row['n_high'] >= 100
              and all(np.isfinite(row.get('d%d' % N, np.nan)) and row['d%d' % N] > 0 for N in (5, 10, 20)))
        row['robust_train'] = bool(ok)
        sweep.append(row)
        if abs(th * 100 - round(th * 100)) < 1e-9 and int(round(th * 100)) % 10 == 0:
            print('  %-6.2f %6d %6d %+7.2f%% %+7.2f%% %+7.2f%% %+7.2f%% %+7.2f%%' % (
                th, row['n_low'], row['n_high'], 100 * (row['d5'] or np.nan),
                100 * (row['d10'] or np.nan), 100 * (row['d20'] or np.nan),
                100 * (row['low_adv10'] or np.nan), 100 * (row['high_adv10'] or np.nan)))
    sw = pd.DataFrame(sweep)
    robust = sw[sw['robust_train']]
    print('  TRAIN 上稳健阈值区间: %s' % (' 无' if robust.empty else
                                     'θ ∈ [%.2f, %.2f]（共 %d 个网格）' % (
                                         robust['threshold'].min(), robust['threshold'].max(), len(robust))))
    VR_TH = float(robust['threshold'].median()) if not robust.empty else float('nan')
    if not np.isfinite(VR_TH):
        VR_TH = 1.20
        print('  TRAIN 未出现稳健阈值 → 回退用 1.20 作为对照阈值（明确标注为「无证据支撑」）')
    else:
        print('  取 TRAIN 稳健区间中位 θ* = %.2f（禁止用 VALID/OOS 调参）' % VR_TH)

    print('\n  --- θ* 在三个分期的表现（低量组 VR<θ* vs 高量组 VR>=θ*）---')
    for pd_ in PERIODS:
        pool = (df['period'].values == pd_)
        for tag, m in (('低量 VR<θ*', m_brk & pool & (vr0 < VR_TH)),
                       ('高量 VR>=θ*', m_brk & pool & (vr0 >= VR_TH)),
                       ('低量 VR<1.2(对照)', m_brk & pool & (vr0 < 1.2)),
                       ('高量 VR>=1.2(对照)', m_brk & pool & (vr0 >= 1.2))):
            layername = tag.replace('θ*', '%.2f' % VR_TH)
            layer(df, oos_rows, 'VRTH_%s' % pd_, layername, m, 'Cb0_r%d', k_B,
                  pool, pool, pool & m_notf)
            s5 = stx(df['Cb0_r5'].values[m]); s10 = stx(df['Cb0_r10'].values[m]); s20 = stx(df['Cb0_r20'].values[m])
            print('    %-6s %-18s n=%-5d T+5 %+0.2f%% T+10 %+0.2f%% T+20 %+0.2f%% win10 %.0f%%'
                  % (pd_, layername, int(m.sum()), 100 * (s5.get('mean') or np.nan),
                     100 * (s10.get('mean') or np.nan), 100 * (s20.get('mean') or np.nan),
                     100 * (s10.get('win') or np.nan)))

    # ═══ 输出 ═══
    cols = ['section', 'group', 'n', 'n_obs', 'horizon', 'mean', 'med', 'win', 'p25', 'p75',
            'pf', 'std', 'mae_med', 'mfe_med', 'adv', 'adv_posb', 'adv_nb', 'adv_t',
            'adv_fix', 'adv_fix_posb', 'adv_fix_nb', 'adv_fix_t', 'note']
    def dump(rows, fn):
        d = pd.DataFrame(rows)
        for c in cols:
            if c not in d.columns:
                d[c] = np.nan
        d = d[cols]
        d.to_csv(os.path.join(RD, fn), index=False, encoding='utf-8-sig')
        print('  写出 %s  %s' % (fn, d.shape))
        return d

    print('\n' + '=' * 78)
    print('输出文件:')
    dump(vol_rows, 'three_flower_v2_volume_curve.csv')
    dump(ana_rows, 'three_flower_v2_breakout_analysis.csv')
    dump(reg_rows, 'three_flower_v2_regime.csv')
    dump(mv_rows, 'three_flower_v2_marketcap.csv')
    dump(oos_rows, 'three_flower_v2_oos.csv')
    pd.DataFrame(sig_rows).to_csv(os.path.join(RD, 'three_flower_v2_significance.csv'),
                                 index=False, encoding='utf-8-sig')
    print('  写出 three_flower_v2_significance.csv  %s' % (len(sig_rows),))
    vr_mono.to_csv(os.path.join(RD, 'three_flower_v2_vr_monotonicity.csv'),
                   index=False, encoding='utf-8-sig')
    sw.to_csv(os.path.join(RD, 'three_flower_v2_vr_threshold_sweep.csv'),
              index=False, encoding='utf-8-sig')
    print('  写出 three_flower_v2_vr_monotonicity.csv / _vr_threshold_sweep.csv')

    meta = dict(date=DATE, n_all=int(n_all), n_tf=int(m_tf.sum()), n_brk=int(m_brk.sum()),
                n_nobrk=int(m_nobrk.sum()), n_notf=int(m_notf.sum()),
                period_range=[str(df['trade_date'].min()), str(df['trade_date'].max())],
                buckets=MINB, min_layer=MIN_LAYER, cost=COST,
                vr_threshold_train=VR_TH,
                vr_robust_range=[float(robust['threshold'].min()), float(robust['threshold'].max())]
                if not robust.empty else None,
                quad_split=dict(amp_med=amp_med, vr_med=vr_med),
                spearman={str(N): dict(mean=spearman(VR_MID, means[N]),
                                       med=spearman(VR_MID, meds[N])) for N in HOR})
    with open(os.path.join(OUT, 'tf_v2_meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    print('  写出 out/tf_v2_meta.json')
    print('\n完成')


if __name__ == '__main__':
    main()
