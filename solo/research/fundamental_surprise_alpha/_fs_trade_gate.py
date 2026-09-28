# -*- coding: utf-8 -*-
"""可交易性闸门（Tradability Gate）—— 不改任何生产模块，只做诊断

对 §34 已裁定 PASS 的 2 个 Alpha（rev_acc / np_qoq）与 3 个 CONDITIONAL
（np_acc / rev_qoq / dp_acc）回答四个「能不能真的拿来交易」的问题：

  GATE-A 多头腿增量：纯多头 Top 十分位绝对超额 − 全事件基准(B1) 的增量，
         扣 30bp 后是否仍为正、且 TRAIN/VALID/OOS 三段同号
  GATE-B 成交时点滑点：把入场价从 k1 开盘 沿 k1 日内路径后移
         （λ=0 开盘 / 0.15≈10:00 / 0.30 / 0.50 / 1.00 收盘），
         看 Top 十分位超额还剩多少；并给出 Top 十分位平均 Gap（开盘已 price in 多少）
  GATE-C 容量与流动性：逐 cohort Top 十分位只数分布、披露季集中度、
         实盘子集（总市值≥80亿 且 非北交所）内的 Alpha 是否还在
  GATE-D 因子重复度：与 mom_5/10/20/60、Gap、ln_mv、turnover、ep_ttm 的
         截面相关，以及中性化后的残差 IC 保留率

判定规则在跑之前先写死（PREREG），结果出来后只做事实陈述，不改规则、不调参。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import OUTD, Log
from fs_run_robust import load_panel, cal_map

log = Log('_fs_trade_gate.txt')

# ---------------------------------------------------------------- 预注册判定规则
PREREG = """
PRE-REGISTERED GATE RULES（跑之前写死，禁止事后修改）
  GATE-A 多头腿：Top十分位净超额(扣30bp) > 0
                 且 增量(top−全事件) 的 cohort 级 t ≥ 2.0
                 且 增量在 TRAIN/VALID/OOS 三段同号
  GATE-B 滑点  ：λ=0.30（约 k1 10:00 成交）时 Top十分位超额 ≥ 0.50 × λ=0 的值
  GATE-C 容量  ：Top十分位只数中位数 ≥ 5
                 且 非披露季（1/2/3/7/8/9/10/11月之外）仍有 ≥ 10 个 cohort
                 且 实盘子集(mv≥80亿 ∧ 非北交所)内 Top十分位超额同号且 ≥ 0.5×全样本
  GATE-D 重复度：对 mom_5/10/20/60 + ln_mv + turnover_rate + ep_ttm + bp
                 中性化后，|残差IC| ≥ 0.50 × |原始IC|
  四项全过 → 允许讨论接入回放；任一不过 → 按 §37 归档，不再调参
"""

LAMS = [0.00, 0.15, 0.30, 0.50, 1.00]
PASS_F = ['rev_acc', 'np_qoq']
COND_F = ['np_acc', 'rev_qoq', 'dp_acc']
ALL_F = PASS_F + COND_F
MV_MIN = 800000.0          # 万元，= 80 亿


# ---------------------------------------------------------------- 多头腿
def long_leg(df, col, ycol, dec=10, min_n=10, min_bin=3):
    """逐 cohort：Top/Bottom 十分位均值、全事件均值、只数

    返回 DataFrame{k, n, n_top, top, bot, all}
      top/bot 达 min_bin 才算；all 为同 cohort 内 x 有限的全部事件均值（与 top 同子集）
    """
    t = pd.DataFrame({'k': np.asarray(df['k']),
                      'x': np.asarray(df[col], float),
                      'y': np.asarray(df[ycol], float)})
    t = t[np.isfinite(t['x']) & np.isfinite(t['y'])]
    if len(t) == 0:
        return None
    t = t.sort_values('k', kind='stable')
    codes, uniq = pd.factorize(t['k'].values, sort=True)
    cnt = np.bincount(codes, minlength=len(uniq)).astype(float)
    xr = E.rank_pct_1d_group(t['x'].values, codes)
    b = np.where(np.isfinite(xr), np.clip(xr * dec, 0, dec - 1), dec)
    M, C = E.seg_mean(codes, b, t['y'].values, dec + 1)
    A, _ = E.seg_mean(codes, np.zeros(len(codes), dtype=np.int64), t['y'].values, 1)
    ihi, ilo = dec - 1, 0
    top = np.where(np.isfinite(M[:, ihi]) & (C[:, ihi] >= min_bin), M[:, ihi], np.nan)
    bot = np.where(np.isfinite(M[:, ilo]) & (C[:, ilo] >= min_bin), M[:, ilo], np.nan)
    out = pd.DataFrame({'k': np.asarray(uniq), 'n': cnt, 'n_top': C[:, ihi],
                        'top': top, 'bot': bot, 'all': A[:, 0]})
    return out[out['n'] >= min_n].reset_index(drop=True)


def _stat(v):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return np.nan, np.nan, 0
    if len(v) == 1:
        return float(v[0]), np.nan, 1
    sd = float(np.std(v, ddof=1))
    return float(np.mean(v)), (float(np.mean(v) / (sd / np.sqrt(len(v))))
                               if sd > 1e-12 else np.nan), int(len(v))


def gate_a(df, feats, ycols=('ex_E1_T5', 'ex_E1_T20')):
    """多头腿增量表"""
    rows = []
    for f in feats:
        for yc in ycols:
            if yc not in df.columns:
                continue
            L = long_leg(df, f, yc)
            if L is None:
                continue
            L['dk'] = (L['top'] - L['all'])
            m_top = np.nanmean(L['top'])
            m_all = np.nanmean(L['all'])
            d_m, d_t, d_n = _stat(L['dk'])
            n_top = L['n_top']
            pooled = (np.nansum(L['top'].fillna(0) * n_top)
                      / max(np.nansum(np.where(np.isfinite(L['top']), n_top, 0)), 1))
            rows.append(dict(
                feature=f, target=yc, horizon=int(yc.split('T')[1]),
                n_coh=len(L), n_top_med=float(np.nanmedian(n_top)),
                n_top_sum=float(np.nansum(n_top)),
                top_eq=m_top, top_pooled=pooled, all_eq=m_all,
                top_net30=m_top - 0.0030, all_net30=m_all - 0.0030,
                delta=d_m, delta_t=d_t, delta_n=d_n))
    return pd.DataFrame(rows)


def gate_a_period(df, feats, kmap):
    """多头腿增量的分期/分年分解（E1/T+5）"""
    rows = []
    per = df.drop_duplicates('k').set_index('k')['period'].to_dict()
    for f in feats:
        L = long_leg(df, f, 'ex_E1_T5')
        if L is None:
            continue
        L['dk'] = L['top'] - L['all']
        L['date'] = L['k'].map(kmap).astype(str)
        L['year'] = L['date'].str[:4]
        L['period'] = L['k'].map(per)
        for seg, g in list(L.groupby('period')) + list(L.groupby('year')):
            seg = str(seg)
            m, t, n = _stat(g['dk'])
            rows.append(dict(feature=f, seg=seg, n_coh=len(g),
                             top=np.nanmean(g['top']), all=np.nanmean(g['all']),
                             delta=m, delta_t=t))
    return pd.DataFrame(rows)


def gate_b(df, feats):
    """成交时点滑点曲线：λ 从 0(开盘) 到 1(收盘)"""
    d = df.copy()
    ok = (np.isfinite(d['ret_E1_T5']) & np.isfinite(d['Intra_R1'])
          & np.isfinite(d['idxret_E1_T5']))
    d = d[ok]
    # 一致性校验：(1+ret_E1_T5)/(1+ret_E2_T5) 应 = entry_px_E2/entry_px_E1
    rr = (1 + d['ret_E1_T5']) / (1 + d['ret_E2_T5']) - 1
    px = d['entry_px_E2'] / d['entry_px_E1'] - 1
    log('  [B] 一致性校验 中位(px_E2/px_E1-1)=%.5f  中位 Intra_R1=%.5f  '
        '中位(ret 比值-1)=%.5f' % (float(np.nanmedian(px)),
                                 float(np.nanmedian(d['Intra_R1'])),
                                 float(np.nanmedian(rr))))
    rows, curve = [], []
    for lam in LAMS:
        col = '_lam%03d' % int(round(lam * 100))
        d[col] = (1 + d['ret_E1_T5']) / (1 + lam * d['Intra_R1']) - 1 \
            - d['idxret_E1_T5']
        # 全事件基准（同 λ）
        curve.append(dict(lam=lam, all_ex=float(np.nanmean(d[col])),
                          n=int(np.isfinite(d[col]).sum())))
        for f in feats:
            L = long_leg(d, f, col)
            if L is None:
                continue
            rows.append(dict(feature=f, lam=lam, top=float(np.nanmean(L['top'])),
                             all=float(np.nanmean(L['all'])),
                             delta=float(np.nanmean(L['top'] - L['all'])),
                             n_coh=len(L)))
    return pd.DataFrame(rows), pd.DataFrame(curve), d


def gate_c(df, feats, kmap):
    """容量与流动性 + 实盘子集"""
    res = {}
    L = long_leg(df, PASS_F[0], 'ex_E1_T5')
    L['date'] = L['k'].map(kmap).astype(str)
    L['ym'] = L['date'].str[:6]
    res['n_top_dist'] = {q: float(np.nanpercentile(L['n_top'], q))
                         for q in (10, 25, 50, 75, 90, 100)}
    res['coh_ge'] = {k: float((L['n_top'] >= k).mean()) for k in (1, 3, 5, 10)}
    res['n_coh'] = len(L)
    # 披露季 vs 淡季（按 cohort 数的月份分布）
    L['month'] = L['date'].str[4:6]
    res['by_month'] = L.groupby('month').agg(
        n_coh=('k', 'size'), n_top_sum=('n_top', 'sum')).to_dict('index')
    # 流动性画像（Top 十分位成员）
    def _top_members(dd, col):
        x = np.asarray(dd[col], float)
        kk = pd.factorize(np.asarray(dd['k']))[0]
        r = E.rank_pct_1d_group(x, kk)
        return dd[np.isfinite(r) & (r >= 0.9)]
    tm = _top_members(df, PASS_F[0])
    res['liq'] = {c: float(pd.to_numeric(tm[c], errors='coerce').median())
                  for c in ('total_mv', 'turnover_rate', 'amount_t0')}
    res['liq_all'] = {c: float(pd.to_numeric(df[c], errors='coerce').median())
                      for c in ('total_mv', 'turnover_rate', 'amount_t0')}
    res['bj_share'] = float(df['ts_code'].str.endswith('.BJ').mean())
    # 实盘子集
    sub = df[(pd.to_numeric(df['total_mv'], errors='coerce') >= MV_MIN)
             & (~df['ts_code'].str.endswith('.BJ'))]
    res['sub_rows'] = len(sub)
    rows = []
    for f in ALL_F:
        a = long_leg(df, f, 'ex_E1_T5')
        b = long_leg(sub, f, 'ex_E1_T5')
        if a is None or b is None:
            continue
        rows.append(dict(feature=f,
                         top_all=float(np.nanmean(a['top'])),
                         top_sub=float(np.nanmean(b['top'])),
                         all_sub=float(np.nanmean(b['all'])),
                         delta_sub=float(np.nanmean(b['top'] - b['all'])),
                         n_coh_sub=len(b),
                         n_top_med_sub=float(np.nanmedian(b['n_top']))))
    res['subset'] = pd.DataFrame(rows)
    return res


def cohort_corr(df, col, other, min_n=10):
    t = pd.DataFrame({'k': np.asarray(df['k']),
                      'x': np.asarray(df[col], float),
                      'z': np.asarray(df[other], float)}) \
        if other in df.columns else None
    if t is None:
        return np.nan, 0
    t = t[np.isfinite(t['x']) & np.isfinite(t['z'])]
    if len(t) == 0:
        return np.nan, 0
    t = t.sort_values('k', kind='stable')
    codes, _ = pd.factorize(t['k'].values, sort=True)
    xr = E.rank_pct_1d_group(t['x'].values, codes)
    zr = E.rank_pct_1d_group(t['z'].values, codes)
    _, cnt, r = E.seg_corr(codes, xr, zr)
    ok = np.isfinite(r) & (cnt >= min_n)
    return (float(np.mean(r[ok])) if ok.any() else np.nan), int(ok.sum())


def gate_d(df, feats):
    ctrls = ['mom_5', 'mom_10', 'mom_20', 'mom_60', 'ln_mv',
             'turnover_rate', 'ep_ttm', 'bp']
    ctrls = [c for c in ctrls if c in df.columns]
    per = df.drop_duplicates('k').set_index('k')['period'].to_dict()
    rows, cr = [], []
    for f in feats:
        YC = 'ex_E1_T5'
        mnn = E.REQ['all']
        base = E.ic_stats(*E.cohort_ic(df, [f], YC)[f], per, min_n=mnn)['ALL']
        R = E.neutralize(df, [f], ctrls, min_n=mnn)
        R['k'] = df['k'].values
        R[YC] = df[YC].values
        rr = E.ic_stats(*E.cohort_ic(R, [f], YC)[f], per, min_n=mnn)['ALL']
        keep = (abs(rr['ic_mean']) / abs(base['ic_mean'])
                if base and np.isfinite(base['ic_mean']) and abs(base['ic_mean']) > 1e-9
                else np.nan)
        rows.append(dict(feature=f, ic_raw=base['ic_mean'], icir_raw=base['icir'],
                         ic_resid_all=rr['ic_mean'], icir_resid=rr['icir'], keep=keep))
        for o in ('mom_5', 'mom_10', 'mom_20', 'mom_60', 'Gap', 'ln_mv',
                  'turnover_rate', 'ep_ttm', 'volume_ratio'):
            c, m = cohort_corr(df, f, o)
            cr.append(dict(feature=f, other=o, corr=c, n_coh=m))
    return pd.DataFrame(rows), pd.DataFrame(cr)


def main():
    log(PREREG)
    log('=' * 70)
    log('0) 载入面板')
    df = load_panel()
    kmap = cal_map()
    log('  %d 行 / %d cohort' % (len(df), df['k'].nunique()))
    log('  period 分布 %s' % df.drop_duplicates('k')['period'].value_counts().to_dict())

    # ---------------- GATE-A ----------------
    log('=' * 70)
    log('1) GATE-A 多头腿（Top十分位 vs 全事件基准）')
    ga = gate_a(df, ALL_F)
    log(ga.to_string(index=False, float_format=lambda v: '%.5f' % v))
    gap = gate_a_period(df, ALL_F, kmap)
    log('')
    log('  分期/分年（E1/T+5）：')
    log(gap.to_string(index=False, float_format=lambda v: '%.5f' % v))

    # ---------------- GATE-B ----------------
    log('=' * 70)
    log('2) GATE-B 成交时点滑点（λ=0 开盘 → 1 收盘）')
    gb, gbc, _ = gate_b(df, ALL_F)
    log('  全事件基准曲线：')
    log(gbc.to_string(index=False, float_format=lambda v: '%.5f' % v))
    log('')
    log('  Top 十分位：')
    log(gb.to_string(index=False, float_format=lambda v: '%.5f' % v))
    log('')
    for f in ALL_F:
        g = gb[gb['feature'] == f].set_index('lam')['top']
        if 0.0 in g.index and 0.30 in g.index:
            log('  %-8s top(λ0)=%.5f  top(λ0.3)=%.5f  保留=%.3f  '
                'top(λ1)=%.5f  保留=%.3f' %
                (f, g[0.0], g[0.30], g[0.30] / g[0.0] if abs(g[0.0]) > 1e-12 else np.nan,
                 g[1.0], g[1.0] / g[0.0] if abs(g[0.0]) > 1e-12 else np.nan))
    # Top 十分位平均 Gap
    gg = []
    for f in ALL_F:
        x = np.asarray(df[f], float)
        kk = pd.factorize(np.asarray(df['k']))[0]
        r = E.rank_pct_1d_group(x, kk)
        gp = pd.to_numeric(df['Gap'], errors='coerce')
        gg.append(dict(feature=f,
                       gap_top=float(gp[np.isfinite(r) & (r >= 0.9)].mean()),
                       gap_all=float(gp[np.isfinite(r)].mean())))
    log('')
    log('  k1 开盘 Gap（公告次日跳空，衡量「开盘已 price in」）：')
    log(pd.DataFrame(gg).to_string(index=False, float_format=lambda v: '%.5f' % v))

    # ---------------- GATE-C ----------------
    log('=' * 70)
    log('3) GATE-C 容量 / 流动性 / 实盘子集')
    gc = gate_c(df, ALL_F, kmap)
    log('  cohort 数 %d' % gc['n_coh'])
    log('  Top十分位只数分布 %s' % {k: round(v, 1) for k, v in gc['n_top_dist'].items()})
    log('  cohort 中 Top十分位 ≥k 只 的占比 %s'
        % {k: round(v, 3) for k, v in gc['coh_ge'].items()})
    log('  按月份 cohort 数 / Top十分位总只数：')
    log(pd.DataFrame(gc['by_month']).T.to_string())
    log('  Top十分位成员中位数：%s' % {k: round(v, 2) for k, v in gc['liq'].items()})
    log('  全样本中位数      ：%s' % {k: round(v, 2) for k, v in gc['liq_all'].items()})
    log('  北交所占比 %.4f   实盘子集行数 %d' % (gc['bj_share'], gc['sub_rows']))
    log('  实盘子集(mv≥80亿∧非BJ)：')
    log(gc['subset'].to_string(index=False, float_format=lambda v: '%.5f' % v))

    # ---------------- GATE-D ----------------
    log('=' * 70)
    log('4) GATE-D 因子重复度（与动量/规模/估值/反应 的截面相关 + 中性化残差）')
    gd, gdc = gate_d(df, ALL_F)
    log('  中性化(x = mom_5/10/20/60 + ln_mv + turnover + ep_ttm + bp)：')
    log(gd.to_string(index=False, float_format=lambda v: '%.5f' % v))
    log('')
    log('  逐 cohort Spearman（信号 vs 其他因子）：')
    log(gdc.pivot_table(index='feature', columns='other',
                        values='corr').to_string(float_format=lambda v: '%.4f' % v))

    # ---------------- 汇总判定 ----------------
    log('=' * 70)
    log('5) 闸门判定（按 PREREG，逐条给事实）')
    for _, r in ga[ga['target'] == 'ex_E1_T5'].iterrows():
        f = r['feature']
        A1 = r['top_net30'] > 0
        A2 = np.isfinite(r['delta_t']) and r['delta_t'] >= 2.0
        g = gap[(gap['feature'] == f) & (gap['seg'].isin(['TRAIN', 'VALID', 'OOS']))]
        A3 = bool(len(g) == 3 and (g['delta'] > 0).all()) or bool(
            len(g) == 3 and (g['delta'] < 0).all())
        bb = gb[gb['feature'] == f].set_index('lam')['top']
        B1 = (0.30 in bb.index and 0.0 in bb.index
              and abs(bb[0.0]) > 1e-12 and bb[0.30] >= 0.50 * bb[0.0])
        C1 = r['n_top_med'] >= 5
        log('  %-8s A[净额>0:%s 增量t=%.2f≥2:%s 三段同号:%s] '
            'B[λ0.3保留≥50%%:%s] C[中位只数=%.1f]'
            % (f, A1, r['delta_t'], A2, A3, B1, r['n_top_med']))

    pd.concat([ga.assign(tbl='gateA'), gap.assign(tbl='gateA_period')],
              ignore_index=True).to_csv(
        os.path.join(OUTD, 'trade_gate.csv'), index=False, encoding='utf-8-sig')
    log('')
    log('  已写出 out/trade_gate.csv')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
