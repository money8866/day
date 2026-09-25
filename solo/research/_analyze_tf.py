# -*- coding: utf-8 -*-
"""
三花聚顶研究 · 批量实验层（第三阶段）

输入: out/tf_base.parquet
输出: out/tf_groups.csv / tf_defs.csv / tf_entries.csv / tf_layers.csv / tf_events_dim.parquet

口径与无偏处理:
  - 所有收益列已扣双边成本（≈0.30%），组间可比
  - 「offset 分层配对超额」(matched_adv)：三花/突破事件的买入时点普遍靠后（确认日中位 T+11），
    若直接与全体首板对比会混入"市场期间涨跌"的时点效应。故按买入 offset K 分桶，
    组内(T+N 收益中位数 − 同 offset 参照组中位数)，再按桶样本量加权 → 得到剔除时点效应的纯结构超额
  - 参照组: TF 用「同 offset 的非三花首板」；突破用「同 offset 的全体首板」
  - Regime 复用 bts/data.market_regime 口径（上证 MA20/MA60 + 20日涨幅），按首板日 T0 判定
"""
import os
import json
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'out')
DB = r'D:\mystock\cache_daily\stock_data.db'

HOR = (1, 3, 5, 10, 20)
N_WIN = 46
MIN_BUCKET = 8
REG_NAME = {'strong': 'BULL(strong)', 'neutral': 'NORMAL(neutral)',
            'weak': 'RANGE(weak)', 'bear': 'BEAR(bear)'}


# ────────────────────────── 统计工具 ──────────────────────────
def st(x, tag=''):
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {'tag': tag, 'n': 0}
    w = x > 0
    pos = float(x[w].sum())
    neg = float(-x[~w].sum())
    pf = (pos / neg) if neg > 0 else (np.inf if pos > 0 else np.nan)
    return {'tag': tag, 'n': int(n),
            'mean': round(float(x.mean()), 5), 'med': round(float(np.median(x)), 5),
            'win': round(float(w.mean()), 4),
            'p25': round(float(np.percentile(x, 25)), 5),
            'p75': round(float(np.percentile(x, 75)), 5),
            'pf': (None if not np.isfinite(pf) else round(pf, 3)),
            'max': round(float(x.max()), 4), 'min': round(float(x.min()), 4)}


def bucket_adv(df, gfmt, N, mask, koff, minn=MIN_BUCKET):
    """offset 分层配对超额（参照组同时限定为同一年份集合，避免年份基准错配）。

    gfmt: 组内收益列格式（如 'A_r%d' / 'Cb0_r%d'），列值仅在组内有效
    参照列: 同 offset K 的 'c{K}_r{N}'（非本组首板在该 offset 买入），且限定在 mask 覆盖的年份内
    对每个桶 K: 组内 T+N 中位数 − 参照组中位数；总超额 = Σ n_K·diff_K / Σ n_K
    """
    gcol = gfmt % N
    if gcol not in df.columns:
        return np.nan, 0, np.nan, 0, np.nan
    yr = df['_yr'].values
    yrs = np.unique(yr[mask]) if mask.any() else np.array([], dtype=yr.dtype)
    dmask = np.isin(yr, yrs)
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
        ref = np.isfinite(cv) & ~mask & dmask
        if int(ref.sum()) < minn:
            continue
        diffs.append(float(np.median(gv[m]) - np.median(cv[ref])))
        ns.append(n)
    if not diffs:
        return np.nan, 0, np.nan, 0, np.nan
    diffs = np.asarray(diffs)
    ns = np.asarray(ns, dtype=np.float64)
    adv = float((ns * diffs).sum() / ns.sum())
    posb = float((diffs > 0).mean())
    nb = len(diffs)
    t = float(diffs.mean() / (diffs.std(ddof=1) / np.sqrt(nb))) if nb >= 3 and diffs.std(ddof=1) > 0 else np.nan
    return adv, int(ns.sum()), posb, nb, t


# ────────────────────────── 附加维度 ──────────────────────────
def load_daily_basic(dates):
    conn = sqlite3.connect(DB, timeout=30.0)
    ds = [str(int(d)) for d in sorted(set(dates))]
    frames = []
    for i in range(0, len(ds), 250):
        ch = ds[i:i + 250]
        q = ("SELECT ts_code, trade_date, total_mv, turnover_rate FROM daily_basic_cache "
             "WHERE trade_date IN (%s)" % ",".join("?" * len(ch)))
        frames.append(pd.read_sql_query(q, conn, params=ch))
    conn.close()
    x = pd.concat(frames, ignore_index=True)
    x['trade_date'] = x['trade_date'].astype(np.int64)
    return x


def build_regime():
    conn = sqlite3.connect(DB, timeout=30.0)
    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000001.SH' "
        "ORDER BY trade_date", conn)
    conn.close()
    idx['trade_date'] = idx['trade_date'].astype(str)
    c = idx['close'].astype(np.float64)
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    ret20 = c / c.shift(20) - 1.0
    strong = ((c > ma20) & (ma20 > ma60) & (ret20 > 0.03)).values
    bear = ((c < ma20) & (c < ma60) & (ret20 < -0.05)).values
    weak = ((c < ma20 * 0.99) | (ret20 < -0.02)).values
    reg = np.full(len(idx), 'neutral', dtype=object)
    reg[weak] = 'weak'
    reg[bear] = 'bear'
    reg[strong] = 'strong'
    reg[:70] = 'neutral'
    return dict(zip(idx['trade_date'], reg))


# ────────────────────────── 主流程 ──────────────────────────
def main():
    df = pd.read_parquet(os.path.join(OUT, 'tf_base.parquet')).reset_index(drop=True)
    n_all = len(df)
    print('事件总数', n_all, '| 列数', df.shape[1])

    reg = build_regime()
    df['regime'] = df['trade_date'].astype(str).map(reg).fillna('neutral')
    db = load_daily_basic(df['trade_date'].values)
    db['mv_pct'] = db.groupby('trade_date')['total_mv'].rank(pct=True)
    db['to_pct'] = db.groupby('trade_date')['turnover_rate'].rank(pct=True)
    df = df.merge(db, on=['ts_code', 'trade_date'], how='left')
    df['mv_grp'] = pd.cut(df['mv_pct'], [0, 1 / 3, 2 / 3, 1.0],
                          labels=['Small', 'Mid', 'Large'], include_lowest=True).astype(object)
    df['to_grp'] = pd.cut(df['to_pct'], [0, 1 / 3, 2 / 3, 1.0],
                          labels=['Low', 'Mid', 'High'], include_lowest=True).astype(object)
    yy = df['trade_date'].astype(str).str[:4]
    df['_yr'] = yy.values
    cov = df.assign(_y=yy).groupby('_y')['total_mv'].apply(lambda s: round(float(s.notna().mean()), 3))
    print('市值/换手覆盖(按年):', cov.to_dict())

    have = df['has_tf'].values
    kA = df['k_A'].fillna(-1).astype(int).values
    kB = df['Cb0_k'].fillna(-1).astype(int).values
    has_brk = df['Cb0_r5'].notna().values
    vr0 = df['brk_b0_vr'].fillna(0).values
    abv0 = df['brk_b0_abv'].fillna(0).values
    df['TF'] = have
    df['TF_BRK'] = has_brk

    # ================= 1. 样本规模 =================
    print('\n=== 样本规模 ===')
    print('首板 %d | 核心三花 %d (%.1f%%) | 三花+突破 %d (%.1f%%)'
          % (n_all, have.sum(), 100 * have.mean(), has_brk.sum(), 100 * has_brk.mean()))
    print('定义命中', {d: int(df['d_' + d].sum()) for d in ['H1', 'H2', 'H3', 'H4', 'H5', 'H6']})

    # ================= 2. 四组对照（第21节）+ 配对超额 =================
    rows = []
    groups = {
        'A_全体首板(offset10)': (None, np.ones(n_all, bool), None, 'c10_r%d'),
        'B_三花形成': (have, have, kA, 'A_r%d'),
        'C_三花+突破': (has_brk, has_brk, kB, 'Cb0_r%d'),
        'D_三花+放量突破(VR>=1.2)': (has_brk & (vr0 >= 1.2), has_brk & (vr0 >= 1.2), kB, 'Cb0_r%d'),
        'D2_三花+缩量突破(VR<1.2)': (has_brk & (vr0 < 1.2), has_brk & (vr0 < 1.2), kB, 'Cb0_r%d'),
        'E_三花+突破但收盘破MA20': (has_brk & (abv0 <= 0.5), has_brk & (abv0 <= 0.5), kB, 'Cb0_r%d'),
        'F_首板后无三花(BM3)': (None, ~have, np.full(n_all, 10), 'c10_r%d'),
    }
    for gname, (mraw, m, koff, fmt) in groups.items():
        for N in HOR:
            c = fmt % N
            if c not in df.columns:
                continue
            v = df.loc[m, c].values
            s = st(v, gname)
            s['group'], s['horizon'] = gname, N
            if koff is not None:
                adv, nused, posb, nb, tt = bucket_adv(df, fmt, N, m, koff)
                s['adv'], s['adv_n'], s['adv_posb'], s['adv_nb'], s['adv_t'] = adv, nused, posb, nb, tt
            rows.append(s)
    g = pd.DataFrame(rows)
    g.to_csv(os.path.join(OUT, 'tf_groups.csv'), index=False, encoding='utf-8-sig')

    print('\n=== 四组对照  median / win / pf / n  ||  配对超额(时点中性) / 正桶占比 / t ===')
    for key in groups:
        sub = g[g['group'] == key]
        if sub.empty:
            continue
        txt = ' | '.join(
            'T+%d %+.2f%% w%.0f%% pf%s [adv%+.2f%% %s t%s]' % (
                r['horizon'], 100 * r['med'], 100 * r['win'],
                ('%.2f' % r['pf']) if r['pf'] is not None and pd.notna(r['pf']) else '-',
                100 * (r['adv'] if pd.notna(r.get('adv')) else 0),
                ('%.0f%%' % (100 * r['adv_posb'])) if pd.notna(r.get('adv_posb')) else '-',
                ('%.1f' % r['adv_t']) if pd.notna(r.get('adv_t')) else '-')
            for _, r in sub.iterrows() if r['n'] > 0)
        print('%-26s n=%-6s %s' % (key, int(sub['n'].max()), txt))

    # ================= 3. 定义比较 =================
    drows = []
    for d in ['H1', 'H2', 'H3', 'H4', 'H5', 'H6']:
        m = df['d_' + d].values
        for N in HOR:
            if 'A_r%d' % N not in df.columns:
                continue
            s = st(df.loc[m, 'A_r%d' % N].values, d)
            s['group'], s['horizon'] = d, N
            adv, nused, posb, nb, tt = bucket_adv(df, 'A_r%d', N, m, kA)
            s['adv'], s['adv_n'], s['adv_posb'], s['adv_nb'], s['adv_t'] = adv, nused, posb, nb, tt
            drows.append(s)
    dd = pd.DataFrame(drows)
    dd.to_csv(os.path.join(OUT, 'tf_defs.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 定义比较  median | win | pf | 配对超额 | 正桶 | t | n ===')
    for d in ['H1', 'H2', 'H3', 'H4', 'H5', 'H6']:
        sub = dd[dd['group'] == d]
        txt = ' | '.join('T+%d %+.2f%% w%.0f%% pf%s adv%+.2f%% %s' % (
            r['horizon'], 100 * r['med'], 100 * r['win'],
            ('%.2f' % r['pf']) if r['pf'] is not None and pd.notna(r['pf']) else '-',
            100 * (r['adv'] if pd.notna(r.get('adv')) else 0),
            ('t%.1f' % r['adv_t']) if pd.notna(r.get('adv_t')) else 't-')
            for _, r in sub.iterrows() if r['n'] > 0)
        print('%-4s n=%-5s %s' % (d, int(sub['n'].max()), txt))

    # ================= 4. Entry 比较 =================
    ent = {
        'A_确认日收盘': ('A_r%d', have & (kA >= 0), kA),
        'B_确认日次开': ('B_r%d', df['B_r5'].notna().values, kA + 1),
        'C_突破收盘': ('Cb0_r%d', has_brk, kB),
        'C_突破0.5%': ('Cb5_r%d', df['Cb5_r5'].notna().values, kB),
        'C_突破1%': ('Cb10_r%d', df['Cb10_r5'].notna().values, kB),
        'C_突破2%': ('Cb20_r%d', df['Cb20_r5'].notna().values, kB),
        'D_突破放量1.2': ('Cb0_r%d', has_brk & (vr0 >= 1.2), kB),
        'E_突破放量+MA20上': ('Cb0_r%d', has_brk & (vr0 >= 1.2) & (abv0 > 0.5), kB),
        'E2_突破+MA20上': ('Cb0_r%d', has_brk & (abv0 > 0.5), kB),
    }
    erows = []
    for k, (fmt, m, koff) in ent.items():
        for N in HOR:
            c = fmt % N
            if c not in df.columns:
                continue
            s = st(df.loc[m, c].values, k)
            s['group'], s['horizon'] = k, N
            adv, nused, posb, nb, tt = bucket_adv(df, fmt, N, m, koff)
            s['adv'], s['adv_n'], s['adv_posb'], s['adv_nb'], s['adv_t'] = adv, nused, posb, nb, tt
            erows.append(s)
    ee = pd.DataFrame(erows)
    ee.to_csv(os.path.join(OUT, 'tf_entries.csv'), index=False, encoding='utf-8-sig')
    print('\n=== Entry 比较  median | win | pf | 配对超额 | 正桶 | t | n ===')
    for k in ent:
        sub = ee[ee['group'] == k]
        txt = ' | '.join('T+%d %+.2f%% w%.0f%% pf%s adv%+.2f%% %s' % (
            r['horizon'], 100 * r['med'], 100 * r['win'],
            ('%.2f' % r['pf']) if r['pf'] is not None and pd.notna(r['pf']) else '-',
            100 * (r['adv'] if pd.notna(r.get('adv')) else 0),
            ('t%.1f' % r['adv_t']) if pd.notna(r.get('adv_t')) else 't-')
            for _, r in sub.iterrows() if r['n'] > 0)
        print('%-20s n=%-5s %s' % (k, int(sub['n'].max()), txt))

    # ================= 5. 分层 =================
    h1m = df['d_H1'].values
    layers = []
    for col in ['regime', 'board', 't0_quality', 'mv_grp', 'to_grp']:
        vals = df[col].dropna().unique()
        for lv in vals:
            base_m = (df[col].values == lv)
            m = have & base_m
            m1 = h1m & base_m
            mb = has_brk & base_m
            r = {'layer': col, 'level': str(lv), 'n_firstboard': int(base_m.sum()),
                 'n_tf': int(m.sum()), 'n_brk': int(mb.sum()), 'n_h1': int(m1.sum()),
                 'tf_rate': round(float(m.sum() / max(base_m.sum(), 1)), 4)}
            for N in HOR:
                r['tf_med_%d' % N] = st(df.loc[m, 'A_r%d' % N].values).get('med')
                r['tf_win_%d' % N] = st(df.loc[m, 'A_r%d' % N].values).get('win')
                r['tf_adv_%d' % N] = bucket_adv(df, 'A_r%d', N, m, kA)[0]
                r['h1_adv_%d' % N] = bucket_adv(df, 'A_r%d', N, m1, kA)[0]
                b = st(df.loc[mb, 'Cb0_r%d' % N].values)
                r['brk_med_%d' % N] = b.get('med'); r['brk_n_%d' % N] = b.get('n')
                r['brk_adv_%d' % N] = bucket_adv(df, 'Cb0_r%d', N, mb, kB)[0]
                vb = mb & (vr0 >= 1.2)
                r['brkv_med_%d' % N] = st(df.loc[vb, 'Cb0_r%d' % N].values).get('med')
                r['brkv_n_%d' % N] = int(vb.sum())
            layers.append(r)
    ldf = pd.DataFrame(layers)
    ldf.to_csv(os.path.join(OUT, 'tf_layers.csv'), index=False, encoding='utf-8-sig')

    print('\n=== 分层: 三花率 / CORE超额 / H1超额 / BRK超额 (T+10) ===')
    for _, r in ldf.iterrows():
        fm = lambda k: ('%+.2f' % (100 * r[k])) if pd.notna(r[k]) else '  na '
        print('%-11s %-12s n=%-6d 三花率%.0f%% | CORE(n%d) %s | H1(n%d) %s | BRK(n%d) %s'
              % (r['layer'], r['level'], r['n_firstboard'], 100 * r['tf_rate'],
                 r['n_tf'], fm('tf_adv_10'), r['n_h1'], fm('h1_adv_10'), r['n_brk'], fm('brk_adv_10')))

    # 行业
    irows = []
    top_ind = df['industry'].value_counts().head(15).index.tolist()
    for ind in top_ind:
        bm = (df['industry'].values == ind)
        m = have & bm
        m1 = h1m & bm
        mb = has_brk & bm
        irows.append({'industry': ind, 'n_firstboard': int(bm.sum()), 'n_tf': int(m.sum()),
                      'n_brk': int(mb.sum()), 'n_h1': int(m1.sum()),
                      'tf_adv_10': bucket_adv(df, 'A_r%d', 10, m, kA)[0],
                      'h1_adv_10': bucket_adv(df, 'A_r%d', 10, m1, kA)[0],
                      'brk_adv_10': bucket_adv(df, 'Cb0_r%d', 10, mb, kB)[0]})
    pd.DataFrame(irows).to_csv(os.path.join(OUT, 'tf_layers_industry.csv'), index=False, encoding='utf-8-sig')

    # 首板 VR 分层
    print('\n=== 首板量比分层 (配对超额) ===')
    vrv = df['t0_vr20'].values
    for lo, hi, nm in [(0, 1.0, '<1.0'), (1.0, 1.5, '1.0-1.5'), (1.5, 2.0, '1.5-2.0'),
                       (2.0, 3.0, '2.0-3.0'), (3.0, 5.0, '3.0-5.0'), (5.0, 1e9, '>5.0')]:
        m = have & (vrv >= lo) & (vrv < hi)
        mb = has_brk & (vrv >= lo) & (vrv < hi)
        a5 = bucket_adv(df, 'A_r%d', 5, m, kA)[0]
        a20 = bucket_adv(df, 'A_r%d', 20, m, kA)[0]
        b5 = bucket_adv(df, 'Cb0_r%d', 5, mb, kB)[0]
        b20 = bucket_adv(df, 'Cb0_r%d', 20, mb, kB)[0]
        print('  首板VR %-8s nTF=%-6d TF adv T+5 %+.2f%% T+20 %+.2f%% | nBRK=%-5d BRK adv T+5 %+.2f%% T+20 %+.2f%%'
              % (nm, int(m.sum()), 100 * (a5 or 0), 100 * (a20 or 0), int(mb.sum()),
                 100 * (b5 or 0), 100 * (b20 or 0)))

    keep = ['ts_code', 'name', 'industry', 'board', 'trade_date', 't0_vr20', 't0_amount',
            't0_quality', 'one_word', 'opened_board', 'regime', 'mv_grp', 'to_grp', 'total_mv',
            'turnover_rate', 'has_tf', 'k_A', 'Cb0_k', 'TF', 'TF_BRK']
    df[keep].to_parquet(os.path.join(OUT, 'tf_events_dim.parquet'), index=False)
    print('\n完成: tf_groups.csv / tf_defs.csv / tf_entries.csv / tf_layers.csv / tf_events_dim.parquet')


if __name__ == '__main__':
    main()
