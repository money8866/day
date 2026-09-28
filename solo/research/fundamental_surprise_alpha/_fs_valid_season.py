# -*- coding: utf-8 -*-
"""VALID 失效归因：2024-2025 负超额，是「因子失效」还是「财报季错位」？

背景
  gate3（RANK-POST 口径）读数：
    rev_acc  TRAIN +30.11% / VALID -5.59% / OOS +50.89%
    np_qoq   TRAIN +37.09% / VALID -33.22% / OOS +33.79%
  这个因子天然只在财报季有事件（4月+8月占 58.9%，1月/12月 0 个 cohort）。
  所以 VALID 为负有两种互斥解释：
    (a) 因子在 2024-2025 真的失效         → 永久归档
    (b) 财报季错位：VALID 期超额集中在财报月，非财报月拖累
                                          → 因子仍在，形态只能是季节性 overlay

PREREG（跑之前写死，结果出来后禁止修改）
  S1 口径
     与 _fs_trade_gate3.py 完全一致：RANK-POST（全样本排 Top 十分位 → 再剔
     mv<80亿 ∧ .BJ）；sleeve 记账同 gate2（一个入场日=一个等权篮子，
     k1 开盘买 / k1+5 收盘卖，投 1/6 当期 NAV，每笔扣 30bp）。
  S2 切片
     财报月 = ann_date 月份 ∈ {1,4,7,8,10}（年报预告/年报/一季报/中报/三季报）
     非财报月 = 其余月份
     切片依据一律用 ann_date（公告月），不用 k1 月，避免月末跨月污染。
  S3 两个独立读数（都必须看，不得只报一个）
     (i)  NAV 层  ：只用该切片的 sleeve 重建净值 → 区间超额 CAGR / IR / MDD
     (ii) Cohort 层：每个入场日 篮子5日收益 − 同日全事件篮子5日收益（同日对冲），
                     报 均值 / 中位 / 胜率 / cohort 数
          —— 用途：绕开净值复利放大效应（probe34 已证该效应会放大符号）
  S4 判读规则（触发任一条 → 归档，不再投入）
     R1  财报月 NAV 超额 ≤ 0                       → (a) 永久归档
     R2  财报月 NAV 超额 > 0 但 IR < 0.5            → 证据不足，归档
     R3  财报月 cohort 超额均值 ≤ 0                 → (a) 永久归档
     R4  财报月 cohort 数 < 30                      → 样本不足，归档
     全部不触发，且 非财报月超额 |x| 很小            → 走 (b) 季节性 overlay
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log
from _fs_trade_gate2 import (load_px, top_decile, build_day_baskets,
                             simulate_nav, metrics, COST, MV_MIN, HOLD)

log = Log('_fs_valid_season.txt')

FACTORS = ('rev_acc', 'np_qoq')
FY_MONTHS = {'01', '04', '07', '08', '10'}


def rp(df, col, mask):
    """RANK-POST：先在全样本排 Top 十分位，只保留 mask 内的成员"""
    A = top_decile(df, col)
    return A[mask.reindex(A.index).fillna(False).values].copy()


def seg_metrics(nav, lvl, td, a, b, name):
    """区间绩效：只用 [a,b] 的净值与指数"""
    s = pd.Series(nav, index=td).dropna()
    s = s[(s.index >= a) & (s.index <= b)]
    bl = pd.Series(lvl, index=td)
    bl = bl[(bl.index >= a) & (bl.index <= b)]
    if len(s) < 30 or len(bl) < 30:
        return None
    r = s.pct_change().dropna()
    br = bl.pct_change().reindex(r.index).fillna(0.0)
    yrs = len(r) / 242.0
    ex = r - br
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1
    mb = (1 + br).prod() ** (1 / yrs) - 1
    sd = ex.std(ddof=1)
    return dict(name=name, n_days=len(r), cagr=float(cagr),
                excess_cagr=float((1 + r).prod() ** (1 / yrs) - 1 - mb),
                ir=float(ex.mean() / sd * np.sqrt(242)) if sd > 1e-12 else np.nan,
                win=float((r > 0).mean()),
                mdd=float((s / s.cummax() - 1).min()))


def main():
    log('=' * 72)
    log('S0 载入')
    td, cmap, C, O, lvl = load_px()
    df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    df = E.add_derived(df)
    df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
    df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
    df['ann_date'] = df['ann_date'].astype(str)
    df['ann_m'] = df['ann_date'].str[4:6]
    df['is_fy'] = df['ann_m'].isin(FY_MONTHS)

    mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))
    log('  全样本事件 %d / mv80 事件 %d' % (len(df), int(mv80.sum())))
    log('  ann_date 范围 %s ~ %s' % (df['ann_date'].min(), df['ann_date'].max()))

    sel = {}
    for f in FACTORS:
        sel[f] = rp(df, f, mv80)
        sel[f]['is_fy'] = sel[f]['ann_date'].str[4:6].isin(FY_MONTHS)
    fy_share = df[df['ann_date'].str[:4].isin(['2024', '2025'])]['is_fy'].mean()
    log('  VALID 期(2024-2025)全事件中财报月占比 = %.1f%%' % (100 * fy_share))

    # ---------- S1 切片概览 ----------
    log('')
    log('S1 切片概览（RANK-POST / mv80）')
    for f in FACTORS:
        x = sel[f]
        v = x[(x['ann_date'] >= '20240101') & (x['ann_date'] <= '20251231')]
        rows = []
        for tag, m in (('财报月', v['is_fy']), ('非财报月', ~v['is_fy'])):
            z = v[m]
            nb = z.groupby('k1').size()
            rows.append(dict(slice=tag, n_event=len(z), n_entry=len(nb),
                             size_med=float(nb.median()) if len(nb) else 0.0,
                             size_min=int(nb.min()) if len(nb) else 0))
        log('  [%s] VALID 期' % f)
        log(pd.DataFrame(rows).to_string(index=False))

    # ---------- S2 Cohort 层（同日对冲，不受净值复利影响） ----------
    log('')
    log('S2 Cohort 层：篮子5日收益 − 同日全事件篮子5日收益（无成本，两边同额抵消）')
    allg = df.groupby('k1')['ret_E1_T5'].mean()
    for f in FACTORS:
        x = sel[f].dropna(subset=['ret_E1_T5'])
        log('  [%s]' % f)
        rec = []
        for pn, (a, b) in (('TRAIN', ('20180101', '20231231')),
                           ('VALID', ('20240101', '20251231')),
                           ('OOS', ('20260101', '20260930')),
                           ('FULL', ('20180101', '20260930'))):
            v = x[(x['ann_date'] >= a) & (x['ann_date'] <= b)]
            for tag, m in (('财报月', v['is_fy']), ('非财报月', ~v['is_fy'])):
                z = v[m]
                if not len(z):
                    rec.append(dict(period=pn, slice=tag, cohort=0, mean=np.nan,
                                    median=np.nan, win=np.nan))
                    continue
                gs = z.groupby('k1')['ret_E1_T5'].mean()
                d = gs - allg.reindex(gs.index)
                d = d.dropna()
                rec.append(dict(period=pn, slice=tag, cohort=len(d),
                                mean=float(d.mean()) if len(d) else np.nan,
                                median=float(d.median()) if len(d) else np.nan,
                                win=float((d > 0).mean()) if len(d) else np.nan))
        t = pd.DataFrame(rec)
        for c in ('mean', 'median', 'win'):
            t[c] = t[c].map(lambda v: np.nan if v != v else round(v * 100, 3))
        log(t.to_string(index=False))

    # ---------- S3 NAV 层切片 ----------
    log('')
    log('S3 NAV 层：只用该切片 sleeve 重建净值（1/6 NAV，30bp，持有5日）')
    per = {'TRAIN': ('20180101', '20231231'),
           'VALID': ('20240101', '20251231'),
           'OOS': ('20260101', '20260930')}
    nav_rows = []
    navs = {}
    for f in FACTORS:
        for tag, m in (('FY', sel[f]['is_fy']), ('NONFY', ~sel[f]['is_fy']),
                       ('ALL', pd.Series(True, index=sel[f].index))):
            ev = sel[f][m.values]
            rm, n, nd = build_day_baskets(ev, cmap, C, O, len(td), COST)
            nav = simulate_nav(rm, len(td))
            navs['%s_%s' % (f, tag)] = nav
            log('  %-18s 事件=%-6d 入场日=%-5d 均只数=%.2f'
                % (f + '_' + tag, n, nd, n / max(nd, 1)))
            for pn, (a, b) in per.items():
                mt = seg_metrics(nav, lvl, td, a, b, '%s_%s' % (f, tag))
                if mt:
                    mt['period'] = pn
                    nav_rows.append(mt)
    N = pd.DataFrame(nav_rows)[['name', 'period', 'n_days', 'excess_cagr',
                                'ir', 'win', 'mdd']]
    log('')
    log(N.to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------- S4 判读 ----------
    log('')
    log('=' * 72)
    log('S4 判读（PREREG §S4，触发任一条即归档）')
    verdict = {}
    for f in FACTORS:
        vfy = N[(N['name'] == '%s_FY' % f) & (N['period'] == 'VALID')]
        vnon = N[(N['name'] == '%s_NONFY' % f) & (N['period'] == 'VALID')]
        ec = float(vfy['excess_cagr'].iloc[0]) if len(vfy) else np.nan
        ir = float(vfy['ir'].iloc[0]) if len(vfy) else np.nan
        nc = float(vnon['excess_cagr'].iloc[0]) if len(vnon) else np.nan
        cv = sel[f]
        cv = cv[(cv['ann_date'] >= '20240101') & (cv['ann_date'] <= '20251231')
                & cv['is_fy'] & cv['ret_E1_T5'].notna()]
        gs = cv.groupby('k1')['ret_E1_T5'].mean()
        d = (gs - allg.reindex(gs.index)).dropna()
        cmean, cnum = float(d.mean()) if len(d) else np.nan, len(d)

        hits = []
        if not (ec > 0):
            hits.append('R1 财报月 NAV 超额 %.4f ≤ 0' % ec)
        elif ir < 0.5:
            hits.append('R2 财报月 IR %.3f < 0.5' % ir)
        if not (cmean > 0):
            hits.append('R3 财报月 cohort 超额均值 %.5f ≤ 0' % (cmean if cmean == cmean else np.nan))
        if cnum < 30:
            hits.append('R4 财报月 cohort 数 %d < 30' % cnum)

        log('  [%s] VALID 财报月 NAV超额=%+.4f IR=%.3f | 非财报月 NAV超额=%+.4f '
            '| cohort 均值=%+.5f 数=%d'
            % (f, ec, ir, nc, cmean if cmean == cmean else np.nan, cnum))
        if hits:
            log('        → 归档（%s）' % '；'.join(hits))
            verdict[f] = 'ARCHIVE'
        else:
            log('        → 走 (b) 季节性 overlay，可继续')
            verdict[f] = 'SEASONAL_OVERLAY'

    # ---------- S5 月度分布（补充证据） ----------
    log('')
    log('S5 VALID 期逐月超额（cohort 同日对冲均值 × 100）')
    for f in FACTORS:
        x = sel[f].dropna(subset=['ret_E1_T5'])
        x = x[(x['ann_date'] >= '20240101') & (x['ann_date'] <= '20251231')].copy()
        gs = x.groupby('k1')['ret_E1_T5'].mean()
        d = (gs - allg.reindex(gs.index)).dropna()
        dd = pd.DataFrame({'k1': d.index, 'exc': d.values})
        dd['ym'] = [td[int(k)][:6] for k in dd['k1']]
        p = dd.pivot_table(index='ym', values='exc',
                           aggfunc=['mean', 'median', 'count'])
        p.columns = ['mean', 'median', 'n_cohort']
        p[['mean', 'median']] = (p[['mean', 'median']] * 100).round(3)
        log('  [%s]' % f)
        log(p.to_string())

    log('')
    log('=' * 72)
    log('裁定：%s' % verdict)
    pd.DataFrame([dict(factor=k, verdict=v) for k, v in verdict.items()]) \
        .to_csv(os.path.join(OUTD, 'valid_season_verdict.csv'),
                index=False, encoding='utf-8-sig')
    N.to_csv(os.path.join(OUTD, 'valid_season_nav.csv'),
             index=False, encoding='utf-8-sig')
    log('  已写出 out/valid_season_verdict.csv / out/valid_season_nav.csv')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
