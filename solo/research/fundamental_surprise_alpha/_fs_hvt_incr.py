# -*- coding: utf-8 -*-
"""HVT-BULL 增量检验（只读）：rev_acc / np_qoq 能否为执行分提供增量

背景
  可交易性闸门已裁定：这 2 个因子统计有效、但不够独立成书（IR 0.38/0.19、
  每篮中位 4 只、VALID 期超额为负）。唯一可能的落点是作为 HVT-BULL 执行分的
  一个分量。本脚本只回答这一个问题：**加进去有没有增量**。

只读约束
  仅读取 report_daily/te_backtest_events_*.csv 与本研究 data/panel.parquet；
  不写入、不导入、不修改任何 hvt_bull 生产文件。

挂载口径
  对每个 (ts_code, decision_date)：取 panel 中 ann_date ≤ decision_date 的**最近一条**
  财报事件，把它的 rev_acc / np_qoq 转成「该事件所在 cohort 的横截面分位」（0~1）。
  decision_date 为收盘后决策、次日开盘执行（er* 口径），故 ann_date ≤ decision_date
  即信息已公开，无未来函数。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log

log = Log('_fs_hvt_incr.txt')

PREREG = """
PRE-REGISTERED INCREMENTALITY RULES（跑之前写死，禁止事后修改）
  样本：report_daily/te_backtest_events_20250101_20260828.csv（2025-01-02~2026-09-23）
  收益：er10 / er20（decision_date 次日开盘买入）；胜率 = er20 > 0
  I1 正交性：因子分位 vs execution_score 的**逐日 Spearman 中位 |rho| ≤ 0.30
  I2 双分层：execution_score 上三分位内，因子上三分位 − 因子下三分位 的 er20 差
             ≥ +0.50%，且 2025 与 2026 两段同号
  I3 目标池：next_day_action ∈ {BUY, BUY_ON_CONFIRM} 内，按因子上三分位过滤后
             的 er20 均值 ≥ 过滤前，且子样本 ≥ 100
  I4 容量  ：挂载成功率 ≥ 60%，且 I3 过滤后平均每日仍有 ≥ 3 只
  I1~I4 全过 → 值得作为执行分的一个分量（另行确认后再动生产）
  任一不过 → 归档，不改任何生产文件
"""

TE = r'd:\mystock\solo\report_daily\te_backtest_events_20250101_20260828.csv'
FACT = ['rev_acc', 'np_qoq']
MV_MIN = 800000.0      # 万元 = 80 亿（用户实盘约束）


def load_events():
    cols = ['ts_code', 'signal_date', 'decision_date', 'execution_score',
            'execution_state', 'next_day_action', 'decision_point',
            'r10', 'r20', 'er10', 'er20']
    d = pd.read_csv(TE, usecols=lambda c: c in cols, low_memory=False)
    d['decision_date'] = pd.to_numeric(d['decision_date'], errors='coerce').astype('int64')
    d = d.dropna(subset=['decision_date']).reset_index(drop=True)
    for c in ('r10', 'r20', 'er10', 'er20', 'execution_score'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    # 单位判定：er20 中位绝对值 < 1 → 分数；否则为百分数
    m = float(d['er20'].abs().median())
    sc = 1.0 if m < 1.0 else 0.01
    log('  er20 中位绝对值=%.4f → 单位系数 %.2f（%s）'
        % (m, sc, '分数' if sc == 1.0 else '百分数'))
    for c in ('r10', 'r20', 'er10', 'er20'):
        d[c] = d[c] * sc
    return d


def load_panel():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'ann_date', 'k1', 'end_date',
                                 'rev_acc', 'np_qoq', 'total_mv'])
    p['ann_date'] = pd.to_numeric(p['ann_date'], errors='coerce')
    p = p.dropna(subset=['ann_date']).reset_index(drop=True)
    p['ann_date'] = p['ann_date'].astype('int64')
    p['total_mv'] = pd.to_numeric(p['total_mv'], errors='coerce')
    # 同一 (ts_code, ann_date) 多行（不同报告期）→ 留 end_date 最新的一条
    p = p.sort_values(['ts_code', 'ann_date', 'end_date'])
    p = p.drop_duplicates(['ts_code', 'ann_date'], keep='last').reset_index(drop=True)
    # cohort 内横截面分位
    kk = pd.factorize(p['k1'].values)[0]
    for f in FACT:
        x = pd.to_numeric(p[f], errors='coerce').values
        r = E.rank_pct_1d_group(x, kk)
        p[f + '_pct'] = r
    log('  panel %d 行 / %d 只 / ann_date %d~%d'
        % (len(p), p['ts_code'].nunique(), p['ann_date'].min(), p['ann_date'].max()))
    return p


def attach(d, p):
    """as-of 挂载：每个 (ts_code, decision_date) 取 ann_date ≤ decision_date 的最近一条"""
    a = d[['ts_code', 'decision_date']].copy()
    a['_r'] = np.arange(len(a))
    a = a.sort_values(['decision_date', 'ts_code'], kind='stable')
    q = p[['ts_code', 'ann_date', 'rev_acc_pct', 'np_qoq_pct', 'total_mv']] \
        .sort_values(['ann_date', 'ts_code'], kind='stable')
    m = pd.merge_asof(a, q, left_on='decision_date', right_on='ann_date',
                      by='ts_code', direction='backward')
    m = m.sort_values('_r').reset_index(drop=True)
    out = d.copy()
    for c in ('rev_acc_pct', 'np_qoq_pct', 'total_mv', 'ann_date'):
        out[c] = m[c].values
    out['mv80'] = (pd.to_numeric(out['total_mv'], errors='coerce') >= MV_MIN) \
        & (~out['ts_code'].str.endswith('.BJ'))
    return out


def tercile_by_day(d, col):
    """按日三分位标签（0/1/2），不足 6 只的日子返回 NaN"""
    def _f(g):
        v = pd.to_numeric(g[col], errors='coerce')
        if v.notna().sum() < 6:
            return pd.Series(np.nan, index=g.index)
        r = v.rank(pct=True)
        return pd.Series(np.where(r > 2 / 3, 2, np.where(r < 1 / 3, 0, 1)), index=g.index)
    return d.groupby('decision_date', group_keys=False).apply(_f)


def spearman_daily(d, x, y):
    r = []
    for _, g in d.groupby('decision_date'):
        a = pd.to_numeric(g[x], errors='coerce')
        b = pd.to_numeric(g[y], errors='coerce')
        ok = a.notna() & b.notna()
        if ok.sum() < 10:
            continue
        r.append(a[ok].corr(b[ok], method='spearman'))
    r = np.array(r, dtype=float)
    return float(np.nanmedian(r)), int(np.isfinite(r).sum())


def main():
    log(PREREG)
    log('=' * 70)
    log('S0 载入事件表与面板')
    d = load_events()
    log('  事件 %d 行 / %d 决策日 / %d 只 / %s~%s'
        % (len(d), d['decision_date'].nunique(), d['ts_code'].nunique(),
           d['decision_date'].min(), d['decision_date'].max()))
    p = load_panel()
    d = attach(d, p)
    for f in FACT:
        ok = d[f + '_pct'].notna().mean()
        log('  %s 挂载成功率 %.4f' % (f, ok))
    log('  mv80 子集 %d 行 (%.2f%%)' % (int(d['mv80'].sum()), 100 * d['mv80'].mean()))

    # ---------------- I1 正交性 ----------------
    log('')
    log('I1 正交性：因子分位 vs execution_score 的逐日 Spearman')
    i1 = {}
    for f in FACT:
        rho, n = spearman_daily(d, f + '_pct', 'execution_score')
        rho2, n2 = spearman_daily(d[d['mv80']], f + '_pct', 'execution_score')
        i1[f] = rho
        log('  %-8s 全池 rho=%.4f (n日=%d) | mv80 rho=%.4f (n日=%d) → %s'
            % (f, rho, n, rho2, n2, 'PASS' if abs(rho) <= 0.30 else 'FAIL'))

    # ---------------- I2 双分层 ----------------
    log('')
    log('I2 双分层：execution_score 上三分位内，因子上/下三分位的 er20 差')
    d['_es_t'] = tercile_by_day(d, 'execution_score')
    i2 = {}
    rows2 = []
    for f in FACT:
        d['_f_t'] = tercile_by_day(d, f + '_pct')
        sub = d[d['_es_t'] == 2]
        for nm, s in (('全池', sub), ('mv80', sub[sub['mv80']])):
            hi = s[s['_f_t'] == 2]['er20'].dropna()
            lo = s[s['_f_t'] == 0]['er20'].dropna()
            if len(hi) < 30 or len(lo) < 30:
                continue
            rows2.append(dict(feature=f, pool=nm, n_hi=len(hi), n_lo=len(lo),
                              hi=hi.mean(), lo=lo.mean(), delta=hi.mean() - lo.mean()))
        # 分段（全池）
        seg = {}
        for pn, (a, b) in (('2025', (20250101, 20251231)),
                           ('2026', (20260101, 20261231))):
            s = sub[(sub['decision_date'] >= a) & (sub['decision_date'] <= b)]
            hi = s[s['_f_t'] == 2]['er20'].dropna()
            lo = s[s['_f_t'] == 0]['er20'].dropna()
            seg[pn] = (hi.mean() - lo.mean()) if (len(hi) >= 20 and len(lo) >= 20) else np.nan
        i2[f] = seg
    t2 = pd.DataFrame(rows2)
    log(t2.to_string(index=False, float_format=lambda v: '%.4f' % v))
    for f in FACT:
        s = i2[f]
        same = (np.isfinite(s['2025']) and np.isfinite(s['2026'])
                and np.sign(s['2025']) == np.sign(s['2026']))
        base = t2[(t2['feature'] == f) & (t2['pool'] == '全池')]['delta']
        okd = (len(base) > 0 and float(base.iloc[0]) >= 0.005)
        log('  %-8s 2025=%+.4f 2026=%+.4f 同号=%s 增量≥0.5%%=%s → %s'
            % (f, s['2025'], s['2026'], same, okd,
               'PASS' if (same and okd) else 'FAIL'))

    # ---------------- I3 目标池增量 ----------------
    log('')
    log('I3 目标池（BUY ∪ BUY_ON_CONFIRM）内按因子上三分位过滤')
    tgt = d[d['next_day_action'].isin(['BUY', 'BUY_ON_CONFIRM'])].copy()
    log('  目标池 %d 行 / %d 决策日' % (len(tgt), tgt['decision_date'].nunique()))
    rows3 = []
    for f in FACT:
        tgt['_f_t'] = tercile_by_day(tgt, f + '_pct')
        for nm, s in (('全池', tgt), ('mv80', tgt[tgt['mv80']])):
            base = s['er20'].dropna()
            kept = s[s['_f_t'] == 2]['er20'].dropna()
            if len(base) < 100 or len(kept) < 100:
                continue
            nd = s[s['_f_t'] == 2]['decision_date'].nunique()
            rows3.append(dict(feature=f, pool=nm, n_base=len(base), n_kept=len(kept),
                              base=base.mean(), kept=kept.mean(),
                              delta=kept.mean() - base.mean(),
                              win_base=float((base > 0).mean()),
                              win_kept=float((kept > 0).mean()),
                              kept_per_day=round(len(kept) / max(nd, 1), 2)))
    t3 = pd.DataFrame(rows3)
    log(t3.to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------------- 汇总 ----------------
    log('')
    log('=' * 70)
    log('闸门判定汇总')
    for f in FACT:
        c1 = abs(i1[f]) <= 0.30
        s = i2[f]
        c2 = (np.isfinite(s['2025']) and np.isfinite(s['2026'])
              and np.sign(s['2025']) == np.sign(s['2026']))
        b2 = t2[(t2['feature'] == f) & (t2['pool'] == '全池')]['delta']
        c2 = bool(c2 and len(b2) > 0 and float(b2.iloc[0]) >= 0.005)
        r3 = t3[(t3['feature'] == f) & (t3['pool'] == 'mv80')]
        c3 = bool(len(r3) > 0 and float(r3['delta'].iloc[0]) >= 0
                  and int(r3['n_kept'].iloc[0]) >= 100)
        pk = float(d[f + '_pct'].notna().mean())
        c4 = bool(pk >= 0.60 and len(r3) > 0
                  and float(r3['kept_per_day'].iloc[0]) >= 3)
        log('  %-8s I1[%s] I2[%s] I3[%s] I4[挂载%.2f 每日%.2f→%s]  → %s'
            % (f, c1, c2, c3, pk,
               float(r3['kept_per_day'].iloc[0]) if len(r3) else -1,
               c4, 'PASS' if (c1 and c2 and c3 and c4) else 'FAIL'))

    t2.to_csv(os.path.join(OUTD, 'hvt_incr_I2.csv'), index=False, encoding='utf-8-sig')
    t3.to_csv(os.path.join(OUTD, 'hvt_incr_I3.csv'), index=False, encoding='utf-8-sig')
    log('')
    log('  已写出 out/hvt_incr_I2.csv / out/hvt_incr_I3.csv')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
