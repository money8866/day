# -*- coding: utf-8 -*-
"""
三花聚顶研究 · 逐年稳定性 / Walk-Forward / OOS 诊断（第五阶段）

输出: out/tf_yearly.csv、out/tf_walkforward.csv
"""
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'out')
HOR = (1, 3, 5, 10, 20)
N_WIN = 46
MIN_BUCKET = 8


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
            'win': round(float(w.mean()), 4), 'pf': (None if not np.isfinite(pf) else round(pf, 3))}


def bucket_adv(df, gfmt, N, mask, koff, minn=MIN_BUCKET, dmask=None):
    """dmask: 参照组限定掩码（逐年/分期分析时必须传入，避免参照组跨年份污染）"""
    gcol = gfmt % N
    if gcol not in df.columns:
        return dict(adv=np.nan, n=0, t=np.nan, posb=np.nan, nb=0, refn=0)
    gv = df[gcol].values
    gok = np.isfinite(gv)
    diffs, ns, refs = [], [], 0
    for K in range(N_WIN):
        ccol = 'c%d_r%d' % (K, N)
        if ccol not in df.columns:
            continue
        cv = df[ccol].values
        m = mask & (koff == K) & gok
        n = int(m.sum())
        if n < minn:
            continue
        rf = np.isfinite(cv) & ~mask
        if dmask is not None:
            rf = rf & dmask
        rn = int(rf.sum())
        if rn < minn:
            continue
        diffs.append(float(np.median(gv[m]) - np.median(cv[rf])))
        ns.append(n); refs += rn
    if not diffs:
        return dict(adv=np.nan, n=0, t=np.nan, posb=np.nan, nb=0, refn=0)
    diffs, ns = np.asarray(diffs), np.asarray(ns, dtype=np.float64)
    adv = float((ns * diffs).sum() / ns.sum())
    t = float(diffs.mean() / (diffs.std(ddof=1) / np.sqrt(len(diffs)))) if len(diffs) >= 3 and diffs.std(ddof=1) > 0 else np.nan
    return dict(adv=adv, n=int(ns.sum()), t=(t if np.isfinite(t) else np.nan),
                posb=float((diffs > 0).mean()), nb=len(diffs), refn=refs)


def fam_mask(df, fam, prt=0.01, vt=0.10, vd=0.80, pg=0.01, mc=0.05, mdd=0.10, span=6, wobs=15):
    P1, P2, P3 = df['P1'].values, df['P2'].values, df['P3'].values
    V0, V1, V2, V3 = df['V0'].values, df['V1'].values, df['V2'].values, df['V3'].values
    sd, mcv, sp = df['struct_dd'].values, df['ma_conv'].values, df['span'].values
    kA = df['k_A'].fillna(-1).values
    base = df['has_tf'].values & (kA >= 0) & (kA <= wobs)
    h1 = base & (P2 >= P1 * (1 - prt)) & (P3 >= P2 * (1 - prt)) & (V2 <= V1 * (1 + vt)) & (V3 <= V2 * (1 + vt))
    h3 = base & (P2 >= P1 * (1 + pg)) & (P3 >= P2 * (1 + pg))
    h4 = base & (mcv <= mc)
    h2 = base & (sd <= mdd) & (V1 <= vd * V0) & (V2 <= vd * V0) & (V3 <= vd * V0)
    h5 = h1 & h3 & h4
    return {'H1': h1, 'H2': h2, 'H3': h3, 'H4': h4, 'H5': h5,
            'H6': h5 & (sp >= span)}[fam]


def main():
    df = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet')).reset_index(drop=True)
    kA = df['k_A'].fillna(-1).astype(int).values
    kB = df['Cb0_k'].fillna(-1).astype(int).values
    has_brk = df['Cb0_r5'].notna().values
    vr0 = df['brk_b0_vr'].fillna(9).values
    yr = df['trade_date'].astype(str).str[:4].values
    years = sorted(set(yr.tolist()))
    print('年份覆盖:', {y: int((yr == y).sum()) for y in years})

    defs = {
        'CORE(have_tf)': (df['has_tf'].values, 'A_r%d', kA),
        'H1': (fam_mask(df, 'H1'), 'A_r%d', kA),
        'H4': (fam_mask(df, 'H4'), 'A_r%d', kA),
        'H5': (fam_mask(df, 'H5'), 'A_r%d', kA),
        'H6': (fam_mask(df, 'H6'), 'A_r%d', kA),
        'H1+缩量突破(VR<1.2)': (fam_mask(df, 'H1') & has_brk & (vr0 < 1.2), 'Cb0_r%d', kB),
        'H1+突破(全体)': (fam_mask(df, 'H1') & has_brk, 'Cb0_r%d', kB),
        '全部三花+突破': (has_brk, 'Cb0_r%d', kB),
    }

    # ============ 逐年 ============
    rows = []
    for name, (mask, fmt, koff) in defs.items():
        for y in years:
            d = (yr == y)
            mm = mask & d
            if mm.sum() < 20:
                continue
            r = {'def': name, 'year': y, 'n': int(mm.sum())}
            for N in HOR:
                a = bucket_adv(df, fmt, N, mm, koff, dmask=d)
                s = st(df.loc[mm, fmt % N].values)
                r['adv%d' % N] = None if not np.isfinite(a['adv']) else round(a['adv'], 5)
                r['t%d' % N] = None if not np.isfinite(a['t']) else round(a['t'], 2)
                r['posb%d' % N] = None if not np.isfinite(a['posb']) else round(a['posb'], 3)
                r['nb%d' % N] = a['nb']
                r['med%d' % N] = s.get('med'); r['win%d' % N] = s.get('win'); r['pf%d' % N] = s.get('pf')
            rows.append(r)
    ydf = pd.DataFrame(rows)
    ydf.to_csv(os.path.join(OUT, 'tf_yearly.csv'), index=False, encoding='utf-8-sig')

    print('\n=== 逐年配对超额 (T+5 / T+10 / T+20, 括号内 T+10 有效桶数) ===')
    for name in defs:
        sub = ydf[ydf['def'] == name]
        if sub.empty:
            continue
        txt = ' | '.join('%s %+.2f/%+.2f/%+.2f(n%d,b%s)' % (
            r['year'], 100 * (r['adv5'] or 0), 100 * (r['adv10'] or 0), 100 * (r['adv20'] or 0),
            r['n'], r['nb10']) for _, r in sub.iterrows())
        print('%-20s %s' % (name, txt))
    print('\n=== 逐年中位数/胜率 (T+10) ===')
    for name in defs:
        sub = ydf[ydf['def'] == name]
        if sub.empty:
            continue
        txt = ' | '.join('%s med %+.2f%% win %.0f%% pf%s' % (
            r['year'], 100 * (r['med10'] or 0), 100 * (r['win10'] or 0),
            ('%.2f' % r['pf10']) if pd.notna(r['pf10']) else '-') for _, r in sub.iterrows())
        print('%-20s %s' % (name, txt))

    # ============ Walk-Forward：训练窗3年 → 次年 ============
    print('\n=== Walk-Forward（训练窗 3 年 → 次年测试，看 adv 符号一致性）===')
    wf = []
    for name, (mask, fmt, koff) in defs.items():
        for i in range(3, len(years)):
            tr_years, te_year = years[i - 3:i], years[i]
            tr = mask & np.isin(yr, tr_years)
            te = mask & (yr == te_year)
            if tr.sum() < 50 or te.sum() < 20:
                continue
            a_tr = bucket_adv(df, fmt, 10, tr, koff, dmask=np.isin(yr, tr_years))
            a_te = bucket_adv(df, fmt, 10, te, koff, dmask=(yr == te_year))
            wf.append({'def': name, 'train': '-'.join(tr_years), 'test': te_year,
                       'n_tr': int(tr.sum()), 'n_te': int(te.sum()),
                       'adv_tr': None if not np.isfinite(a_tr['adv']) else round(a_tr['adv'], 5),
                       'adv_te': None if not np.isfinite(a_te['adv']) else round(a_te['adv'], 5),
                       'ok': bool(np.isfinite(a_tr['adv']) and np.isfinite(a_te['adv']) and a_tr['adv'] > 0 and a_te['adv'] > 0)})
    wdf = pd.DataFrame(wf)
    wdf.to_csv(os.path.join(OUT, 'tf_walkforward.csv'), index=False, encoding='utf-8-sig')
    for name in defs:
        sub = wdf[wdf['def'] == name]
        if sub.empty:
            continue
        txt = ' | '.join('%s: tr%+.2f%%→te%+.2f%% %s' % (
            r['test'], 100 * (r['adv_tr'] or 0), 100 * (r['adv_te'] or 0),
            'OK' if r['ok'] else 'X') for _, r in sub.iterrows())
        rate = 100 * sub['ok'].mean()
        print('%-20s 一致率 %.0f%%  %s' % (name, rate, txt))

    # ============ OOS 诊断 ============
    print('\n=== OOS(2026) 诊断 ===')
    oos = (yr == '2026')
    print('OOS 事件数', int(oos.sum()), '| 首板年份分布', {y: int((yr == y).sum()) for y in years})
    for name, (mask, fmt, koff) in defs.items():
        mm = mask & oos
        if mm.sum() < 5:
            print('%-20s OOS 样本不足 (%d)' % (name, int(mm.sum())))
            continue
        parts = []
        for N in HOR:
            a = bucket_adv(df, fmt, N, mm, koff, dmask=oos)
            s = st(df.loc[mm, fmt % N].values)
            parts.append('T+%d adv%+.2f%% med%+.2f%% win%.0f%% n%d/b%d' % (
                N, 100 * (a['adv'] or 0), 100 * (s.get('med') or 0), 100 * (s.get('win') or 0),
                a['n'], a['nb']))
        print('%-20s OOS n=%-4d %s' % (name, int(mm.sum()), ' | '.join(parts)))

    # ============ Regime × 年份 交叉 ============
    import sqlite3
    conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db', timeout=30.0)
    idx = pd.read_sql_query("SELECT trade_date, close FROM index_daily_cache "
                            "WHERE ts_code='000001.SH' ORDER BY trade_date", conn)
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
    rg = df['regime'].values

    print('\n=== Regime × 年份 事件分布 ===')
    tab = pd.crosstab(pd.Series(yr, name='year'), pd.Series(rg, name='regime'))
    print(tab.to_string())

    print('\n=== 按 Regime 分层：逐年配对超额 T+10（参照组限定同年同 regime 外）===')
    for name in ['CORE(have_tf)', 'H1', 'H4', '全部三花+突破']:
        mask, fmt, koff = defs[name]
        for r_ in ['strong', 'neutral', 'weak', 'bear']:
            dm = (rg == r_)
            mm = mask & dm
            if mm.sum() < 40:
                continue
            parts = []
            for y in years:
                d = (yr == y) & dm
                m2 = mask & d
                if m2.sum() < 20:
                    continue
                a = bucket_adv(df, fmt, 10, m2, koff, dmask=d)
                if not np.isfinite(a['adv']):
                    continue
                parts.append('%s %+.2f%%(n%d)' % (y, 100 * a['adv'], a['n']))
            a_all = bucket_adv(df, fmt, 10, mm, koff, dmask=dm)
            print('%-16s %-8s n=%-5d 全期 %+.2f%% | %s' % (
                name, r_, int(mm.sum()), 100 * (a_all['adv'] or 0), ' | '.join(parts)))


if __name__ == '__main__':
    main()
