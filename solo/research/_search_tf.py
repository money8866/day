# -*- coding: utf-8 -*-
"""
三花聚顶研究 · 参数搜索 / 稳健性 / 样本内外（第四、五阶段）

关键架构优势: 引擎里的「结构选择」与「H 定义筛选」已彻底分离 ——
  结构（花三元组、k_A、hi_lvl、Entry A/B/C 收益）不随 H 定义参数变化，
  因此所有 H1–H6 参数化筛选可在本层直接重建，无需重跑引擎，且不会引入
  "参数改变导致事后重新挑选结构" 的偏差。

输出: out/tf_experiments.csv（每次实验一行，含全样本/TRAIN/VAL/OOS 的分层配对超额）
      out/tf_robust.csv（参数稳健区间汇总）
"""
import os
import json
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'out')

HOR = (1, 3, 5, 10, 20)
N_WIN = 46
MIN_BUCKET = 8
PERIODS = {'ALL': (0, 99999999), 'TRAIN': (20210101, 20231231),
           'VAL': (20240101, 20251231), 'OOS': (20260101, 20260925)}


def st(x):
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {}
    w = x > 0
    pos, neg = float(x[w].sum()), float(-x[~w].sum())
    pf = (pos / neg) if neg > 0 else (np.inf if pos > 0 else np.nan)
    return {'n': int(n), 'med': round(float(np.median(x)), 5), 'mean': round(float(x.mean()), 5),
            'win': round(float(w.mean()), 4),
            'pf': (None if not np.isfinite(pf) else round(pf, 3))}


def bucket_adv(df, gfmt, N, mask, koff, minn=MIN_BUCKET, dmask=None):
    """offset 分层配对超额；dmask 用于把参照组限定在同一考察期（分期分析时必须传入，
    否则参照组跨期污染会产生年份基准错配的假象）。"""
    gcol = gfmt % N
    if gcol not in df.columns:
        return np.nan, 0, np.nan, np.nan
    gv = df[gcol].values
    gok = np.isfinite(gv)
    diffs, ns = [], []
    for K in range(N_WIN):
        ccol = 'c%d_r%d' % (K, N)
        if ccol not in df.columns:
            continue
        cv = df[ccol].values
        m = mask & (koff == K) & gok
        n = int(m.sum())
        if n < minn:
            continue
        ref = np.isfinite(cv) & ~mask
        if dmask is not None:
            ref = ref & dmask
        if int(ref.sum()) < minn:
            continue
        diffs.append(float(np.median(gv[m]) - np.median(cv[ref])))
        ns.append(n)
    if not diffs:
        return np.nan, 0, np.nan, np.nan
    diffs, ns = np.asarray(diffs), np.asarray(ns, dtype=np.float64)
    adv = float((ns * diffs).sum() / ns.sum())
    posb = float((diffs > 0).mean())
    t = float(diffs.mean() / (diffs.std(ddof=1) / np.sqrt(len(diffs)))) if len(diffs) >= 3 and diffs.std(ddof=1) > 0 else np.nan
    return adv, int(ns.sum()), (t if np.isfinite(t) else np.nan), posb


# ── 定义重建（与引擎内 H1..H6 完全同口径）──
def fam_mask(df, fam, prt=0.01, vt=0.10, vd=0.80, pg=0.01, mc=0.05, mdd=0.10, span=6, wobs=15,
             ddmax=None, fmin=0):
    """ddmax: 通用结构回撤上限（对所有家族生效，检验"结构越紧越好"）；
    fmin: 最少可识别花数（检验"三花数量 2/3/4"）。两者默认不启用，保持 H1–H6 基线不变。"""
    P1, P2, P3 = df['P1'].values, df['P2'].values, df['P3'].values
    V0, V1, V2, V3 = df['V0'].values, df['V1'].values, df['V2'].values, df['V3'].values
    sd, mcv, sp = df['struct_dd'].values, df['ma_conv'].values, df['span'].values
    kA = df['k_A'].fillna(-1).values
    base = df['has_tf'].values & (kA >= 0) & (kA <= wobs)
    if ddmax is not None:
        base = base & np.isfinite(sd) & (sd <= ddmax)
    if fmin:
        base = base & (df['n_flower'].values >= fmin)
    h1 = base & (P2 >= P1 * (1 - prt)) & (P3 >= P2 * (1 - prt)) & (V2 <= V1 * (1 + vt)) & (V3 <= V2 * (1 + vt))
    h3 = base & (P2 >= P1 * (1 + pg)) & (P3 >= P2 * (1 + pg))
    h4 = base & (mcv <= mc)
    h2 = base & (sd <= mdd) & (V1 <= vd * V0) & (V2 <= vd * V0) & (V3 <= vd * V0)
    h5 = h1 & h3 & h4
    if fam == 'H1':
        return h1
    if fam == 'H2':
        return h2
    if fam == 'H3':
        return h3
    if fam == 'H4':
        return h4
    if fam == 'H5':
        return h5
    if fam == 'H6':
        return h5 & (sp >= span)
    if fam == 'H1L':      # 放宽：价格不降 + 仅末端缩量（V3<=V1）
        return base & (P3 >= P1 * (1 - prt)) & (V3 <= V1 * (1 + vt))
    if fam == 'H1S':      # 收紧：价格抬升 + 严格连续缩量
        return base & (P2 >= P1) & (P3 >= P2) & (V2 <= V1 * (1 - vt)) & (V3 <= V2 * (1 - vt))
    return base


def eval_cfg(df, mask, koff, fmt, tag, extra=None, periods=PERIODS):
    """对配置做 全样本/TRAIN/VAL/OOS 的分层配对超额 + 分布统计"""
    row = {'tag': tag}
    if extra:
        row.update(extra)
    td = df['trade_date'].values
    for pname, (lo, hi) in periods.items():
        d = (td >= lo) & (td <= hi)
        mm = mask & d
        row['n_' + pname] = int(mm.sum())
        for N in HOR:
            a = bucket_adv(df, fmt, N, mm, koff, dmask=d)
            row['adv_%s_%d' % (pname, N)] = None if not np.isfinite(a[0]) else round(a[0], 5)
            row['t_%s_%d' % (pname, N)] = None if not np.isfinite(a[2]) else round(a[2], 2)
            row['pos_%s_%d' % (pname, N)] = None if not np.isfinite(a[3]) else round(a[3], 3)
            s = st(df.loc[mm, fmt % N].values) if (fmt % N) in df.columns else {}
            row['med_%s_%d' % (pname, N)] = s.get('med')
            row['win_%s_%d' % (pname, N)] = s.get('win')
            row['pf_%s_%d' % (pname, N)] = s.get('pf')
    return row


def main():
    df = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet')).reset_index(drop=True)
    n = len(df)
    have = df['has_tf'].values
    kA = df['k_A'].fillna(-1).astype(int).values
    kB = df['Cb0_k'].fillna(-1).astype(int).values
    has_brk = df['Cb0_r5'].notna().values
    vr0 = df['brk_b0_vr'].fillna(9).values
    print('事件', n, '| 核心三花', int(have.sum()), '| 三花突破', int(has_brk.sum()))

    rows = []
    # ============ A. 六家族基线 ============
    for fam in ['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H1L', 'H1S']:
        m = fam_mask(df, fam)
        rows.append(eval_cfg(df, m, kA, 'A_r%d', 'BASE_%s' % fam, {'dim': 'family', 'val': fam, 'fam': fam}))
    # ============ B. OFAT 逐参数扫描（以 H1 为基线）============
    grid = {
        'prt': [('prt', [0.0, 0.005, 0.01, 0.02, 0.03])],
        'vt': [('vt', [0.0, 0.05, 0.10, 0.20])],
        'wobs': [('wobs', [3, 5, 7, 10, 15])],
        'pg': [('pg', [0.0, 0.01, 0.02, 0.03])],
        'mc': [('mc', [0.02, 0.03, 0.05, 0.08])],
        'mdd': [('mdd', [0.05, 0.08, 0.10, 0.12, 0.15])],
    }
    for dim, items in grid.items():
        for pname, vals in items:
            for v in vals:
                kw = {pname: v}
                for fam in ['H1', 'H5', 'H6']:
                    m = fam_mask(df, fam, **kw)
                    rows.append(eval_cfg(df, m, kA, 'A_r%d', 'OFAT_%s_%s=%s_%s' % (dim, pname, v, fam),
                                         {'dim': dim, 'val': v, 'fam': fam, 'param': pname,
                                          'param_val': v}))
    # ============ C. 突破参数扫描（结构固定 = H1 默认）============
    m1 = fam_mask(df, 'H1')
    mb1 = m1 & has_brk
    for buf in ['b0', 'b5', 'b10', 'b20']:
        fmt = 'C%s_r%%d' % buf
        m = m1 & df['%s_r5' % ('C' + buf)].notna().values
        rows.append(eval_cfg(df, m, df['C%s_k' % buf].fillna(-1).astype(int).values, fmt,
                             'BRK_buf_%s' % buf, {'dim': 'breakout_buf', 'val': buf, 'fam': 'H1'}))
    for thr in [1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0]:
        m = mb1 & (vr0 >= thr)
        rows.append(eval_cfg(df, m, kB, 'Cb0_r%d', 'BRK_vr>=%s' % thr,
                             {'dim': 'breakout_vr', 'val': thr, 'fam': 'H1'}))
    for thr in [1.0, 1.1, 1.2, 1.3, 1.5]:
        m = mb1 & (vr0 < thr)
        rows.append(eval_cfg(df, m, kB, 'Cb0_r%d', 'BRK_vr<%s' % thr,
                             {'dim': 'breakout_vr_lt', 'val': thr, 'fam': 'H1'}))
    # 结构族 × 突破
    for fam in ['H1', 'H5', 'H6']:
        m = fam_mask(df, fam)
        rows.append(eval_cfg(df, m & has_brk, kB, 'Cb0_r%d', 'BRK_%s' % fam, {'dim': 'brk_family', 'val': fam, 'fam': fam}))
        rows.append(eval_cfg(df, m & has_brk & (vr0 < 1.2), kB, 'Cb0_r%d', 'BRK_%s_vrlt1.2' % fam, {'dim': 'brk_family_vr', 'val': fam, 'fam': fam}))
    # ============ C2. 通用结构回撤上限（第16节：结构越紧是否越稳）============
    for dd in [0.05, 0.08, 0.10, 0.12, 0.15, 0.25]:
        for fam in ['H1', 'H4']:
            m = fam_mask(df, fam, ddmax=dd)
            rows.append(eval_cfg(df, m, kA, 'A_r%d', 'DD_%s<=%s' % (fam, dd),
                                 {'dim': 'ddmax', 'val': dd, 'fam': fam}))
    # ============ C3. 花数量（第23节：三花数量 2/3/4）============
    for nf in [2, 3, 4]:
        for fam in ['H1', 'H4']:
            m = fam_mask(df, fam, fmin=nf)
            rows.append(eval_cfg(df, m, kA, 'A_r%d', 'NF_%s>=%d' % (fam, nf),
                                 {'dim': 'nflower', 'val': nf, 'fam': fam}))
    # ============ C4. 突破量能分档（第28节：量能太大是否兑现）============
    for lo, hi in [(0.0, 1.0), (1.0, 1.3), (1.3, 1.5), (1.5, 2.0), (2.0, 1e9)]:
        m = m1 & has_brk & (vr0 >= lo) & (vr0 < hi)
        rows.append(eval_cfg(df, m, kB, 'Cb0_r%d', 'BRKVR_%.1f-%.1f' % (lo, hi),
                             {'dim': 'brkvr_band', 'val': '%.1f-%.1f' % (lo, hi), 'fam': 'H1'}))

    # ============ D. 组合收紧（H1 + 低换手 / 弱市 / 主板）============
    m = m1
    rows.append(eval_cfg(df, m & (df['board'].values == 'MAIN'), kA, 'A_r%d',
                         'H1_MAIN', {'dim': 'combo', 'val': 'board=MAIN', 'fam': 'H1'}))
    # regime 列按 bts/data.market_regime 口径重算
    import sqlite3
    conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db', timeout=30.0)
    idx = pd.read_sql_query("SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000001.SH' ORDER BY trade_date", conn)
    conn.close()
    idx['trade_date'] = idx['trade_date'].astype(str)
    c = idx['close'].astype(np.float64)
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    r20 = c / c.shift(20) - 1.0
    reg = np.full(len(idx), 'neutral', dtype=object)
    reg[((c < ma20 * 0.99) | (r20 < -0.02)).values] = 'weak'
    reg[((c < ma20) & (c < ma60) & (r20 < -0.05)).values] = 'bear'
    reg[((c > ma20) & (ma20 > ma60) & (r20 > 0.03)).values] = 'strong'
    reg[:70] = 'neutral'
    df['regime'] = df['trade_date'].astype(str).map(dict(zip(idx['trade_date'], reg))).fillna('neutral')
    for rg in ['weak', 'bear']:
        rows.append(eval_cfg(df, m1 & (df['regime'].values == rg), kA, 'A_r%d',
                             'H1_regime=%s' % rg, {'dim': 'combo', 'val': 'regime=%s' % rg, 'fam': 'H1'}))
    for rg in ['neutral', 'strong']:
        rows.append(eval_cfg(df, m1 & (df['regime'].values == rg), kA, 'A_r%d',
                             'H1_regime=%s' % rg, {'dim': 'combo', 'val': 'regime=%s' % rg, 'fam': 'H1'}))

    ex = pd.DataFrame(rows)
    ex.to_csv(os.path.join(OUT, 'tf_experiments.csv'), index=False, encoding='utf-8-sig')
    print('实验数', len(ex))

    # ============ 输出：家族对照 ============
    print('\n=== 家族基线 (adv T+5 / T+10 / T+20 | 正桶 | n)  ALL / VAL / OOS ===')
    for fam in ['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H1L', 'H1S']:
        r = ex[ex['tag'] == 'BASE_%s' % fam]
        if r.empty:
            continue
        r = r.iloc[0]
        f = lambda k, p='ALL': ('%+.2f' % (100 * r['adv_%s_%d' % (p, k)])) if pd.notna(r['adv_%s_%d' % (p, k)]) else '  na'
        print('%-4s n=%-5d ALL %s/%s/%s (pos %s/%s/%s) | VAL %s/%s/%s | OOS %s/%s/%s'
              % (fam, r['n_ALL'], f(5), f(10), f(20),
                 r['pos_ALL_5'], r['pos_ALL_10'], r['pos_ALL_20'],
                 f(5, 'VAL'), f(10, 'VAL'), f(20, 'VAL'),
                 f(5, 'OOS'), f(10, 'OOS'), f(20, 'OOS')))

    # ============ OFAT 稳健区间 ============
    print('\n=== OFAT 参数扫描 (T+10 adv) ===')
    rob = []
    alldims = list(grid.keys()) + ['ddmax', 'nflower', 'brkvr_band']
    for dim, grp in ex[ex['dim'].isin(alldims)].groupby('dim'):
        print('--', dim)
        for fam in [f for f in ['H1', 'H4', 'H5', 'H6'] if f in set(grp['fam'])]:
            g = grp[grp['fam'] == fam].sort_values('param_val')
            if g.empty:
                continue
            txt = ' | '.join('%s:%+.2f%%(n%d,t%s)' % (
                r['param_val'],
                100 * (r['adv_ALL_10'] if pd.notna(r['adv_ALL_10']) else 0), r['n_ALL'],
                ('%.1f' % r['t_ALL_10']) if pd.notna(r['t_ALL_10']) else '-')
                for _, r in g.iterrows())
            print('   %-4s %s' % (fam, txt))
            ok = g[(g['adv_ALL_10'] > 0) & (g['adv_ALL_5'] > 0) & (g['n_ALL'] >= 100)]['param_val'].tolist()
            rob.append({'dim': dim, 'fam': fam, 'robust_vals': ok,
                        'n_vals': len(g), 'mono': bool(len(ok) == len(g))})
    pd.DataFrame(rob).to_csv(os.path.join(OUT, 'tf_robust.csv'), index=False, encoding='utf-8-sig')

    # ============ 突破参数 ============
    print('\n=== 突破参数 (H1 结构) ===')
    for tag in ['BRK_buf_b0', 'BRK_buf_b5', 'BRK_buf_b10', 'BRK_buf_b20',
                'BRK_vr>=1.0', 'BRK_vr>=1.1', 'BRK_vr>=1.2', 'BRK_vr>=1.3',
                'BRK_vr>=1.5', 'BRK_vr>=1.8', 'BRK_vr>=2.0',
                'BRK_vr<1.0', 'BRK_vr<1.1', 'BRK_vr<1.2', 'BRK_vr<1.3', 'BRK_vr<1.5',
                'BRK_H1', 'BRK_H5', 'BRK_H6', 'BRK_H1_vrlt1.2', 'BRK_H5_vrlt1.2', 'BRK_H6_vrlt1.2']:
        r = ex[ex['tag'] == tag]
        if r.empty:
            continue
        r = r.iloc[0]
        f = lambda k, p: ('%+.2f' % (100 * r['adv_%s_%d' % (p, k)])) if pd.notna(r['adv_%s_%d' % (p, k)]) else ' na '
        print('%-18s n=%-5d ALL %s/%s/%s (pos %s) | TRAIN %s/%s | VAL %s/%s | OOS %s/%s'
              % (tag, r['n_ALL'], f(5, 'ALL'), f(10, 'ALL'), f(20, 'ALL'), r['pos_ALL_10'],
                 f(5, 'TRAIN'), f(10, 'TRAIN'), f(5, 'VAL'), f(10, 'VAL'), f(5, 'OOS'), f(10, 'OOS')))

    for tag in ['DD_H1<=0.05', 'DD_H1<=0.08', 'DD_H1<=0.1', 'DD_H1<=0.12', 'DD_H1<=0.15', 'DD_H1<=0.25',
                'DD_H4<=0.05', 'DD_H4<=0.08', 'DD_H4<=0.1', 'DD_H4<=0.12', 'DD_H4<=0.15', 'DD_H4<=0.25']:
        r = ex[ex['tag'] == tag]
        if r.empty:
            continue
        r = r.iloc[0]
        f = lambda k, p: ('%+.2f' % (100 * r['adv_%s_%d' % (p, k)])) if pd.notna(r['adv_%s_%d' % (p, k)]) else ' na '
        print('%-14s n=%-5d ALL %s/%s/%s (pos %s) | VAL %s/%s | OOS %s/%s'
              % (tag, r['n_ALL'], f(5, 'ALL'), f(10, 'ALL'), f(20, 'ALL'), r['pos_ALL_10'],
                 f(5, 'VAL'), f(10, 'VAL'), f(5, 'OOS'), f(10, 'OOS')))

    print('\n=== 花数量 / 突破量能分档 ===')
    for tag in ['NF_H1>=2', 'NF_H1>=3', 'NF_H1>=4', 'NF_H4>=2', 'NF_H4>=3', 'NF_H4>=4',
                'BRKVR_0.0-1.0', 'BRKVR_1.0-1.3', 'BRKVR_1.3-1.5', 'BRKVR_1.5-2.0', 'BRKVR_2.0-1000000000.0']:
        r = ex[ex['tag'] == tag]
        if r.empty:
            continue
        r = r.iloc[0]
        f = lambda k, p: ('%+.2f' % (100 * r['adv_%s_%d' % (p, k)])) if pd.notna(r['adv_%s_%d' % (p, k)]) else ' na '
        print('%-18s n=%-5d ALL %s/%s/%s (pos %s) | VAL %s/%s | OOS %s/%s'
              % (tag, r['n_ALL'], f(5, 'ALL'), f(10, 'ALL'), f(20, 'ALL'), r['pos_ALL_10'],
                 f(5, 'VAL'), f(10, 'VAL'), f(5, 'OOS'), f(10, 'OOS')))

    print('\n=== 组合 (H1) ===')
    for tag in ['H1_MAIN', 'H1_regime=weak', 'H1_regime=bear', 'H1_regime=neutral', 'H1_regime=strong']:
        r = ex[ex['tag'] == tag]
        if r.empty:
            continue
        r = r.iloc[0]
        f = lambda k, p: ('%+.2f' % (100 * r['adv_%s_%d' % (p, k)])) if pd.notna(r['adv_%s_%d' % (p, k)]) else ' na '
        print('%-18s n=%-5d ALL %s/%s/%s | VAL %s/%s | OOS %s/%s'
              % (tag, r['n_ALL'], f(5, 'ALL'), f(10, 'ALL'), f(20, 'ALL'),
                 f(5, 'VAL'), f(10, 'VAL'), f(5, 'OOS'), f(10, 'OOS')))
    print('\n已写 tf_experiments.csv / tf_robust.csv')


if __name__ == '__main__':
    main()
