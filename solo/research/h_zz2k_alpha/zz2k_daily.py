# -*- coding: utf-8 -*-
"""H-ZZ2K-A1 盘后候选排名生成器 (方案 A) —— 复用已冻结口径, 不产买卖信号.

定位
    研究层 H-ZZ2K-A1 已验证「因子级」增量. 本脚本把同一套 PIT 口径搬到「每日盘后」:
    取 <= asof 日的面板 -> 复算 38 个原始因子 -> 类内 Löwdin -> 类间 cross_orth
    -> 只取 §14 判决已 PASS 的两个 Alpha 组 (A1 Value/Quality/Growth,
    A2 Short-Term Reversal), 按预注册权重 COMP_PRIOR 归一合成
    -> 输出当日候选排名 (研究观察名单).

硬约束 (继承 SPEC §0)
    trading_authorization = NO —— 不产 BUY/SELL, 不含仓位/止损/择时, 不做权重优化.
    权重唯一来源 = zz2k_common.COMP_PRIOR (缺失键由 composite_from_rank 自动重归一).
    未 PASS 的 A3/A4/A5 仅作对照列输出, 不进入排序分.
    方向在 IS 段一次性定死; 本脚本不引入任何新参数.

日更提速 (默认)
    lowdin / gs_orth / cross_orth / composite_from_rank 均为逐日独立运算, 故默认只把
    asof 当日切片送入口径函数. 因子 / M0 / 覆盖仍按全历史计算 (它们含时序信息),
    因此 keep/drop 门禁与打分口径与研究层完全一致 —— 数值逐位相同, 只是不做
    其余 2119 天的无效复算. `--full` 可回退到全历史路径 (仅用于口径自查).

用法
    python -u zz2k_daily.py                      # U-ZZ2K, 数据末日, Top 50 (单日快算)
    python -u zz2k_daily.py --pool U-PROXY
    python -u zz2k_daily.py --asof 20260924 --top 100
    python -u zz2k_daily.py --fresh              # 忽略因子 npz 缓存, 全量重算
    python -u zz2k_daily.py --all                # 输出全部合格个股
    python -u zz2k_daily.py --full               # 全历史口径 (复算全部交易日, 慢)

退出码
    SPEC 哈希不一致 -> 2 ; 池/日期不可用 -> 4 ; 正常 -> 0
"""
import os
import sys
import time

import numpy as np
import pandas as pd

import zz2k_common as R
import zz2k_run as RUN

SEP = '\u2550' * 78
SUB = '\u2500' * 78

# SPEC §A3/A5 基线窗口 (与 zz2k_run.main 调用一致; 非本脚本新参数)
W_RM, W_VOL, W_VOL2 = 250, 60, 120

# 仅纳入 §14 判决已 PASS 的组: C_A1 PROMISING / C_A2 PROMISING
SCORE_GROUPS = ('A1', 'A2')

# 继承自研究的风险标注 (来源 out/zz2k_group_composite.csv, 不重算)
RISK_LABELS = ('MEMBER_HISTORY_SHORT', 'OOS_SHORT', 'FACTOR_LEVEL_ONLY',
               'NO_TRADABILITY_MODEL', 'NO_INTRADAY_TIMING')


def _arg(name, default=None):
    v = sys.argv[1:]
    for i, x in enumerate(v):
        if x.lower() == name and i + 1 < len(v):
            return v[i + 1]
    return default


def _flag(name):
    return name.lower() in [x.lower() for x in sys.argv[1:]]


# ------------------------------------------------------------------ helpers
def day_pct(M, u, t):
    """t 日截面 rank/(n+1) in (0,1); 池外/缺失 -> NaN."""
    out = np.full(M.shape[0], np.nan, dtype='float64')
    m = u[:, t] & np.isfinite(M[:, t])
    v = M[m, t].astype('float64')
    if v.size < 2:
        return out
    out[m] = pd.Series(v).rank().to_numpy() / (v.size + 1.0)
    return out


def load_names(codes, asof):
    """按 asof 日有效简称读取 (cache_daily/treasure_namechg_<code>.parquet)."""
    out = {}
    for tc in codes:
        fp = os.path.join(R.CD, 'treasure_namechg_%s.parquet'
                          % str(tc).replace('.', '_'))
        if not os.path.exists(fp):
            continue
        try:
            d = pd.read_parquet(fp)
        except Exception:
            continue
        if not len(d) or 'name' not in d.columns:
            continue
        s = d['start_date'].astype(str).str.replace('.0', '', regex=False)
        e = d['end_date'].astype(str).str.replace('.0', '', regex=False)
        s = s.where(s.str.match(r'^\d{8}$'), '19000101')
        e = e.where(e.str.match(r'^\d{8}$'), '99999999')
        m = (s <= asof) & (asof <= e)
        row = d.loc[m].iloc[-1] if m.any() else d.iloc[-1]
        out[tc] = str(row['name'])
    return out


def load_basic_fast(g, lg=None):
    """等价 R.load_basic 但 si/di 只算一次.

    R._scatter 对每列都重算 `.map` (8 列 x 2 = 16 次 x 9.7M 行 ≈ 24.8s).
    这里 factorize(C 级哈希) + 查表一次搞定; 结果与 R._scatter 逐位相同 (已对照).
    """
    src = os.path.join(R.FS_DATA, 'basic_panel.parquet')
    cols = ['turnover_rate', 'volume_ratio', 'pe_ttm', 'pb', 'ps_ttm',
            'dv_ttm', 'total_mv', 'circ_mv']
    if not os.path.exists(src):
        return R.load_basic(g, lg=lg)
    df = pd.read_parquet(src, columns=['ts_code', 'trade_date'] + cols)
    S, N = g['close'].shape
    cs, cu = pd.factorize(df['ts_code'].astype(str).to_numpy())
    ds, du = pd.factorize(df['trade_date'].astype(str).to_numpy())
    cs = cs.astype('int64')
    ds = ds.astype('int64')
    lut_s = np.array([g['s2i'].get(x, -1) for x in cu], dtype='int64')
    lut_d = np.array([g['d2i'].get(x, -1) for x in du], dtype='int64')
    si = np.where(cs >= 0, lut_s[np.maximum(cs, 0)], -1)
    di = np.where(ds >= 0, lut_d[np.maximum(ds, 0)], -1)
    ok = (si >= 0) & (di >= 0)
    si, di = si[ok], di[ok]
    out = {}
    for c in cols:
        M = np.full((S, N), np.nan, dtype='float32')
        M[si, di] = df[c].to_numpy(dtype='float64')[ok]
        out[c] = M
    if lg:
        lg('panel basic(fast) cols=%d rows=%d matched=%d'
           % (len(cols), len(df), int(ok.sum())))
    return out


def read_independent(pool):
    """已选中独立增量因子 (来源 out/zz2k_independent_factors.csv)."""
    fp = os.path.join(R.OUT, 'zz2k_independent_factors.csv')
    if not os.path.exists(fp):
        return []
    d = pd.read_csv(fp)
    d = d[(d['pool'] == pool) & (d['selected'] == 1)]
    return list(d['factor'])


def read_verdicts(pool):
    """继承研究判决 (来源 out/zz2k_group_composite.csv), 不重算."""
    fp = os.path.join(R.OUT, 'zz2k_group_composite.csv')
    if not os.path.exists(fp):
        return {}
    keep = ('C_A1', 'Cperp_A1', 'C_A2', 'Cperp_A2',
            'C_A3', 'Cperp_A3', 'C_A4', 'Cperp_A4', 'C_A5', 'Cperp_A5',
            'COMP_EQ', 'COMP_PRIOR')
    d = pd.read_csv(fp)
    d = d[(d['pool'] == pool) & (d['series'].isin(keep))]
    out = {}
    for _, r in d.iterrows():
        out[str(r['series'])] = {
            'verdict': str(r['verdict']),
            'notes': '' if pd.isna(r['notes']) else str(r['notes']),
            'ALL_ic': float(r['ALL_ic']),
            'dedup_ALL_ic': float(r['dedup_ALL_ic']),
            'IS_ic': float(r['IS_ic']), 'OOS_ic': float(r['OOS_ic']),
        }
    return out


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    lg = R.Log('zz2k_daily')
    lg(SEP)
    lg('H-ZZ2K-A1 盘后候选排名生成器 (方案 A)   trading_authorization=NO')
    lg('不产买卖信号 / 不含仓位与择时 / 不做权重优化')
    lg(SEP)

    if not R.spec_check(lg=lg, strict=False):
        lg('SPEC_HASH_MISMATCH 口径层已变更 -> 停止 (退出码 2)')
        return 2

    pool = (_arg('--pool', 'U-ZZ2K') or 'U-ZZ2K').upper()
    fresh = _flag('--fresh')
    full = _flag('--full')
    top = 0 if _flag('--all') else int(_arg('--top', 50) or 50)

    g = R.load_grid(lg=lg)
    dates = g['dates']
    basic = load_basic_fast(g, lg=lg)
    bench = R.load_bench(g, lg=lg)
    ret = R.ret_panel(g)

    asof = str(_arg('--asof', dates[-1]))
    if asof not in g['d2i']:
        lg('asof=%s 不在交易日序列; 数据末日=%s 最近可选: %s'
           % (asof, dates[-1], ','.join(dates[-5:])))
        return 4
    t = int(g['d2i'][asof])
    lg('asof=%s (t=%d, 数据末日=%s)  pool=%s  fresh=%s  mode=%s  top=%s'
       % (asof, t, dates[-1], pool, fresh,
          'full-history' if full else 'single-day', top or 'ALL'))

    # -------- 池 ------------------------------------------------------
    lg(SUB)
    uni, _ = R.build_universe(g, basic, lg=lg)
    if pool not in uni or uni[pool] is None:
        lg('POOL_UNAVAILABLE %s' % pool)
        return 4
    u = uni[pool]
    n_pool = int(u[:, t].sum())
    lg('pool=%s asof 合格域 n=%d' % (pool, n_pool))
    if n_pool < 30:
        lg('池内当日有效样本不足 (%d < 30) -> 停止' % n_pool)
        return 4

    # -------- 因子层 --------------------------------------------------
    lg(SUB)
    lg('因子层: A1/A2/A4 (共享) + A3/A5 (逐口径)  cache=%s' % (not fresh))
    l1s, _ = R.industry_l1(g, lg=lg)
    Fp = {}
    for k in ('A1', 'A2', 'A4'):
        Fp.update(R.build_group(k, g, basic, bench, ret, u, l1s, lg=lg,
                                cache=not fresh))
    Fp.update(RUN.build_A35(pool, g, bench, basic, ret, u, l1s,
                            W_RM, W_VOL, W_VOL2, lg=lg, cache=not fresh))
    lg('原始因子就绪: %d' % len(Fp))

    # -------- 正交层 (与研究层同一函数, 口径一致) ----------------------
    # 全历史量: 覆盖门禁(决定 keep/drop) 与 M0(beta60 含时序) 必须按全历史算,
    # 这样 keep/drop 与研究层完全一致, 口径不变.
    cov = R.factor_coverage(Fp, u, dates=dates)
    M0 = R.build_M0(g, basic, bench, ret, l1s, u, lg=lg)
    td = t                                   # 真实交易日列号 (行情/基础面取数用)
    if full:
        ORTH, C, Cperp, gd = RUN.pool_orth(pool, Fp, u, M0, cov, lg)
    else:
        # lowdin/gs_orth/cross_orth 逐日独立 -> 只把 asof 当日切片送入同一批口径函数.
        # 面板列号随之变为 0 (t=0), 行情/基础面仍用 td 取当日列.
        lg('单日切片: 仅 asof 单日进入 standardize/lowdin/gs_orth/cross_orth')
        u = u[:, td:td + 1]
        Fp = {n: M[:, td:td + 1] for n, M in Fp.items()}
        M0 = {'mv': M0['mv'][:, td:td + 1], 'beta': M0['beta'][:, td:td + 1],
              'l1': M0['l1'][:, td:td + 1]}
        t = 0
        ORTH, C, Cperp, gd = RUN.pool_orth(pool, Fp, u, M0, cov, lg)

    if not all(k in Cperp for k in SCORE_GROUPS):
        lg('SCORE_GROUP_MISSING %s' % str(SCORE_GROUPS))
        return 4

    # -------- 当日覆盖诊断 (cross_orth 需 5 组同时有效, 最稀组决定截面) ---
    lg(SUB)
    lg('asof 截面完整度 (合格域 %d):' % n_pool)
    cin, fin = {}, {}
    for k in R.GROUPS:
        cin[k] = int((u[:, t] & np.isfinite(C[k][:, t])).sum())
        fin[k] = int((u[:, t] & np.isfinite(Cperp[k][:, t])).sum())
        lg('  C_%-2s %5d (%5.1f%%)   Cperp_%-2s %5d'
           % (k, cin[k], 100.0 * cin[k] / max(1, n_pool), k, fin[k]))
    bind = min(cin, key=lambda k: cin[k])
    n_joint = fin[R.GROUPS[0]]
    lg('  类内最稀组 C_%s (%d/%d); cross_orth 需 5 组同时有效 -> 可用截面 %d (%.1f%%)'
       % (bind, cin[bind], n_pool, n_joint, 100.0 * n_joint / max(1, n_pool)))

    # -------- 合成 (预注册权重, 缺失键自动重归一) ----------------------
    w = {k: R.COMP_PRIOR[k] for k in SCORE_GROUPS}
    sc, _ = R.composite_from_rank(
        {k: Cperp[k] for k in SCORE_GROUPS}, u, w)
    sc_grp, _ = R.composite_from_rank(
        {k: C[k] for k in SCORE_GROUPS}, u, w)
    wsum = float(sum(w.values()))
    lg('score = COMP_PRIOR 归一 %s -> %s'
       % (w, {k: round(v / wsum, 4) for k, v in w.items()}))

    # -------- 当日截面截面表达 -----------------------------------------
    m = u[:, t] & np.isfinite(sc[:, t])
    idx = np.where(m)[0]
    if idx.size == 0:
        lg('asof 当日无有效合成分 (截面样本不足或因子全缺) -> 停止')
        return 4
    lg('当日有效截面 n=%d (%.1f%% of 合格域)' % (idx.size, 100.0 * idx.size / max(1, n_pool)))

    df = pd.DataFrame({
        'ts_code': [g['codes'][s] for s in idx],
        'score': sc[idx, t].astype('float64'),
        'score_grp': sc_grp[idx, t].astype('float64'),
        'leg_A1': day_pct(Cperp['A1'], u, t)[idx],
        'leg_A2': day_pct(Cperp['A2'], u, t)[idx],
        'close': g['close'][idx, td].astype('float64'),
        'ret_pct': ret[idx, td].astype('float64') * 100.0,
        'turnover_rate': basic['turnover_rate'][idx, td].astype('float64'),
        'volume_ratio': basic['volume_ratio'][idx, td].astype('float64'),
        'total_mv_yi': basic['total_mv'][idx, td].astype('float64') / 1e4,
        'circ_mv_yi': basic['circ_mv'][idx, td].astype('float64') / 1e4,
        'pe_ttm': basic['pe_ttm'][idx, td].astype('float64'),
        'pb': basic['pb'][idx, td].astype('float64'),
    })
    # 未纳入排序的组: 仅作对照 (A3/A4/A5 判决 FRAGILE + SIGN_CONFLICT)
    for k in ('A3', 'A4', 'A5'):
        df['perp_pct_%s' % k] = day_pct(Cperp[k], u, t)[idx]
    # 独立增量因子当日分位 (ORTH 口径; 门禁剔除者回退原始面板)
    ind_f = read_independent(pool)
    for f in ind_f:
        pl = ORTH.get(f)
        if pl is None:
            pl = Fp[f]
        df['f_%s' % f] = day_pct(pl, u, t)[idx]

    df = df.sort_values('score', ascending=False, kind='stable')
    df = df.reset_index(drop=True)
    df.insert(0, 'rank', np.arange(1, len(df) + 1))
    if top > 0:
        df = df.head(top).copy()

    nm = load_names(list(df['ts_code']), asof)
    df.insert(2, 'name', [nm.get(c, '') for c in df['ts_code']])
    n_named = int(sum(1 for c in df['ts_code'] if nm.get(c)))
    lg('简称覆盖 %d/%d (cache_daily/treasure_namechg_*.parquet)'
       % (n_named, len(df)))

    for c in df.columns:
        if df[c].dtype.kind == 'f':
            df[c] = df[c].round(4)

    tag = '%s_%s' % (pool.replace('-', ''), asof)
    fp = R.save_csv(df, 'zz2k_daily_%s.csv' % tag, lg=lg)

    # -------- 元数据 / 风险标注 -----------------------------------------
    cov_rows = {}
    for n in list(R.GROUP_FACTORS['A1']) + list(R.GROUP_FACTORS['A2']):
        c = cov.get(n) or {}
        cov_rows[n] = {'cov': float(c.get('cov', np.nan)),
                       'days': int(c.get('days', 0)),
                       'flag': R.coverage_flag(c)}
    meta = {
        'research_id': 'H-ZZ2K-A1', 'version': '1.1',
        'product': 'zz2k_daily 盘后候选排名 (方案 A)',
        'trading_authorization': 'NO',
        'run_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'asof': asof, 'data_last_date': str(dates[-1]), 'pool': pool,
        'pool_size': n_pool, 'n_ranked': int(idx.size),
        'n_output': int(len(df)),
        'asof_coverage': {'pool': n_pool, 'ranked': int(idx.size),
                          'joint': n_joint, 'binding': bind,
                          'c_in': cin, 'perp_valid': fin},
        'top': 'ALL' if top == 0 else int(top),
        'score': {
            'panel': 'Cperp_k (类间 cross_orth 残差)',
            'legs': list(SCORE_GROUPS),
            'weight_source': 'zz2k_common.COMP_PRIOR',
            'weights_raw': w,
            'weights_normalized': {k: round(v / wsum, 4)
                                   for k, v in w.items()},
            'note': 'score_grp 为 C_k (类内 Löwdin 组复合) 同权重对照列',
        },
        'groups_excluded': {k: 'NOT_SELECTED (§14 判决 FRAGILE/NO EDGE)'
                            for k in ('A3', 'A4', 'A5')},
        'independent_factors': ind_f,
        'inherited_verdicts': read_verdicts(pool),
        'factor_coverage': cov_rows,
        'orth_diag': {k: {'keep': list(v['keep']), 'drop': list(v['drop']),
                          'days': int(v['days']),
                          'trunc_ratio': float(v['trunc_ratio'])}
                      for k, v in gd.items()},
        'orth_scope': 'full_history' if full else 'single_day(asof)',
        'risk_labels': list(RISK_LABELS),
        'limitations': [
            '因子级验证 != 策略级验证; COMP_PRIOR/COMP_EQ 判决 FRAGILE+SIGN_CONFLICT',
            '成本口径无冲击成本/涨跌停/融券约束',
            'MEMBER_HISTORY_SHORT: 中证2000 月度快照仅 37 期',
            'OOS_SHORT: OOS 段约 8 个月',
            'A2 为短期反转 (低吸) 风格, 与追强势偏好相反',
            '输出仅为研究观察名单, 不得直接作为交易依据',
        ],
    }
    R.save_json(meta, 'zz2k_daily_%s.json' % tag, lg=lg)

    lg(SUB)
    lg('Top %d 候选 (研究观察名单, 非交易信号):' % min(10, len(df)))
    for _, r in df.head(10).iterrows():
        lg('  #%-3d %-10s %-9s score=%.4f  A1=%.3f A2=%.3f  mv=%.1f亿'
           % (int(r['rank']), r['ts_code'], r['name'], r['score'],
              r['leg_A1'], r['leg_A2'], r['total_mv_yi']))
    lg(SEP)
    lg('done rows=%d  (%.1fs)  trading_authorization=NO'
       % (len(df), time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
