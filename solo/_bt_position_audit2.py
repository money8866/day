#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
仓位算法合理性回测 — 第二版（修正单位 + 显著性检验 + 口径分离）
"""
import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from scipy import stats as sps

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
OUT = os.path.join(BASE, '_bt_out')
os.makedirs(OUT, exist_ok=True)

IDX_COLS = ['中证2000_fwd1', '中证2000_fwd3', '中证2000_fwd5', '中证2000_fwd10',
             '上证指数_fwd1', '上证指数_fwd5', '中证1000_fwd5', '创业板指_fwd5']


def load_panel():
    p = os.path.join(OUT, 'panel.csv')
    df = pd.read_csv(p, dtype={'trade_date': str})
    return df


def spearman_ci(a, b):
    """Spearman + p 值"""
    a = pd.Series(a).astype(float)
    b = pd.Series(b).astype(float)
    m = a.notna() & b.notna()
    if m.sum() < 10:
        return np.nan, np.nan, int(m.sum())
    r = a[m].rank().corr(b[m].rank())
    try:
        p = sps.spearmanr(a[m], b[m]).pvalue
    except Exception:
        p = np.nan
    return r, p, int(m.sum())


def bucket_analysis(sub, label, col, target='中证2000_fwd5'):
    print(f'\n  【{label}】分档 vs 后续收益 (目标={target})')
    bins = [(-0.01, 0.01, '≈0%'), (0.01, 0.15, '低'), (0.15, 0.35, '中'),
            (0.35, 0.55, '偏高'), (0.55, 1.01, '高')]
    rows = []
    print(f'    {"档位":<10} {"天数":>5} {"均值":>9} {"中位":>9} {"胜率":>8} {"p(vs全体)":>11}')
    for lo, hi, nm in bins:
        g = sub[(sub[col] >= lo) & (sub[col] < hi)]
        if len(g) == 0:
            continue
        f = g[target].dropna()
        if len(f) == 0:
            continue
        base = sub[target].dropna()
        try:
            _, p = sps.mannwhitneyu(f, base, alternative='two-sided')
        except Exception:
            p = np.nan
        print(f'    {nm:<10} {len(g):>5} {f.mean():>9.2f} {f.median():>9.2f} '
              f'{(f>0).mean()*100:>7.1f}% {p:>11.3f}')
        rows.append({'bucket': nm, 'n': len(g), 'mean': round(f.mean(), 2),
                     'median': round(f.median(), 2),
                     'win': round((f > 0).mean() * 100, 1),
                     'p': round(p, 4) if not np.isnan(p) else None})
    return rows


def main():
    df = load_panel()
    print('=' * 78)
    print('仓位算法合理性审计 — 第二版')
    print('=' * 78)

    sub = df[df['trade_date'] >= '20260520'].copy()   # 引擎有输出区间
    print(f'  引擎有输出样本: {len(sub)} 天 (20260520~{sub["trade_date"].max()})')
    print(f'  全市场数据样本: {len(df)} 天 (20260105~{df["trade_date"].max()})')

    # ================= A. 修复后的策略模拟 =================
    print('\n' + '=' * 78)
    print('A. 策略模拟（修正单位：中证2000，信号当日收盘→次日收益）')
    print('=' * 78)
    sim = sub[sub['eng_position'].notna()].sort_values('trade_date').copy()
    sim['f1'] = sim['中证2000_fwd1'] / 100.0          # 百分比→小数
    sim['pos'] = sim['eng_position'] / 100.0
    sim['pos_lag'] = sim['pos'].shift(1).fillna(0)
    sim['strat'] = sim['pos'] * sim['f1']
    sim['strat_lag'] = sim['pos_lag'] * sim['f1']
    sim['bh'] = sim['f1']

    def perf(r, nm):
        r = r.dropna()
        if len(r) == 0:
            return None
        cum = (1 + r).prod() - 1
        vol = r.std() * np.sqrt(252)
        sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
        eq = (1 + r).cumprod()
        mdd = (eq / eq.cummax() - 1).min()
        out = {'name': nm, 'n': len(r), 'cum': round(cum * 100, 2),
               'ann_vol': round(vol * 100, 2), 'sharpe': round(sh, 2) if not np.isnan(sh) else None,
               'mdd': round(mdd * 100, 2), 'win': round((r > 0).mean() * 100, 1)}
        print(f'  {nm:<24} 累计{out["cum"]:>8.2f}%  年化波动{out["ann_vol"]:>6.2f}%  '
              f'Sharpe{out["sharpe"]:>6.2f}  回撤{out["mdd"]:>7.2f}%  胜率{out["win"]:>5.1f}%')
        return out

    perf_rows = [
        perf(sim['strat'], '引擎仓位(当日生效)'),
        perf(sim['strat_lag'], '引擎仓位(T+1执行)'),
        perf(sim['bh'], '满仓买入持有'),
    ]
    # 基准：等权 50% 仓位
    perf(sim['bh'] * 0.5, '半仓买入持有')

    # 超额检验
    exc = (sim['strat_lag'] - sim['bh']).dropna()
    t, p = sps.ttest_1samp(exc, 0)
    print(f'\n  T+1策略超额(日频) 均值{exc.mean()*100:+.4f}%/日  t={t:.2f}  p={p:.3f}'
          f'  → {"显著" if p<0.05 else "不显著"}')

    # ================= B. 广度闸门方向性 =================
    print('\n' + '=' * 78)
    print('B. 广度闸门(up_ratio<35%)的方向性检验')
    print('=' * 78)
    for lab, cond in [
        ('up_ratio < 35% (被否决)', sub['up_ratio'] < 35),
        ('35% ≤ up_ratio < 55%', (sub['up_ratio'] >= 35) & (sub['up_ratio'] < 55)),
        ('up_ratio ≥ 55%', sub['up_ratio'] >= 55),
    ]:
        g = sub[cond]
        if len(g) == 0:
            continue
        f5 = g['中证2000_fwd5'].dropna()
        f1 = g['中证2000_fwd1'].dropna()
        print(f'  {lab:<24} N={len(g):>3}  fwd1={f1.mean():>6.2f}%  fwd5={f5.mean():>6.2f}%  '
              f'fwd5中位={f5.median():>6.2f}%  胜率={(f5>0).mean()*100:>5.1f}%')

    # 分市场状态下拆解：普涨日 vs 普跌日，闸门是否还成立
    print('\n  按「当日中证2000涨跌」分状态拆解 up_ratio 闸门:')
    sub['mkt_state'] = np.where(sub['中证2000_ret1'] > 0.5, '普涨日',
                        np.where(sub['中证2000_ret1'] < -0.5, '普跌日', '震荡日'))
    for st in ['普涨日', '震荡日', '普跌日']:
        g = sub[sub['mkt_state'] == st]
        if len(g) < 8:
            continue
        v = g[g['up_ratio'] < 35]
        nv = g[g['up_ratio'] >= 35]
        print(f'    {st}  (N={len(g)}):  up<35% 组 fwd5={v["中证2000_fwd5"].mean():>6.2f}% '
              f'(N={len(v)})   up≥35% 组 fwd5={nv["中证2000_fwd5"].mean():>6.2f}% (N={len(nv)})')

    # ================= C. 炸板率闸门 =================
    print('\n' + '=' * 78)
    print('C. 炸板率闸门方向性（炸板率高低 × 市场状态）')
    print('=' * 78)
    for st in ['普涨日', '震荡日', '普跌日']:
        g = sub[sub['mkt_state'] == st]
        if len(g) < 8:
            continue
        hi = g[g['zb_rate'] > 35]
        lo = g[g['zb_rate'] <= 35]
        print(f'    {st}  (N={len(g)}):  炸板>35% 组 fwd5={hi["中证2000_fwd5"].mean():>6.2f}% '
              f'(N={len(hi)})   炸板≤35% 组 fwd5={lo["中证2000_fwd5"].mean():>6.2f}% (N={len(lo)})')

    # ================= D. 关键低点闸门 =================
    print('\n' + '=' * 78)
    print('D. 关键低点跌破闸门（engine=0 但广度未否决）')
    print('=' * 78)
    bd = sub[(sub['eng_position'] == 0) & (sub['up_ratio'] >= 35)]
    if len(bd):
        f5 = bd['中证2000_fwd5'].dropna()
        print(f'  触发 N={len(bd)}  fwd1={bd["中证2000_fwd1"].mean():>6.2f}%  '
              f'fwd5={f5.mean():>6.2f}%  胜率={(f5>0).mean()*100:>5.1f}%')
        print('  逐日明细:')
        for _, r in bd.iterrows():
            print(f'    {r["trade_date"]}  pos={r["eng_position"]:>3}  up%={r["up_ratio"]:>5.1f}  '
                  f'zb%={r["zb_rate"]:>5.1f}  趋势={r["eng_trend"]:>5.1f}  '
                  f'fwd1={r["中证2000_fwd1"]:>6.2f}  fwd5={r["中证2000_fwd5"]:>6.2f}')

    # ================= E. 仓位分档单调性（引擎输出）=================
    print('\n' + '=' * 78)
    print('E. 引擎仓位分档 → 后续收益（单调性检验）')
    print('=' * 78)
    e = bucket_analysis(sub, '引擎建议仓位', 'eng_position')

    # 趋势分分档
    print('\n  【趋势分 eng_trend】分档:')
    sub['eng_trend_b'] = pd.cut(sub['eng_trend'], [0, 25, 40, 55, 70, 101],
                                 labels=['<25', '25-40', '40-55', '55-70', '>70'])
    for nm, g in sub.groupby('eng_trend_b', observed=True):
        if len(g) < 3:
            continue
        f5 = g['中证2000_fwd5'].dropna()
        print(f'    {str(nm):<10} N={len(g):>3}  fwd1={g["中证2000_fwd1"].mean():>6.2f}%  '
              f'fwd5={f5.mean():>6.2f}%  中位={f5.median():>6.2f}%  胜率={(f5>0).mean()*100:>5.1f}%')

    # ================= F. 三大指数趋势分区分度 =================
    print('\n' + '=' * 78)
    print('F. 指数趋势分区分度体检（三指数是否真的有区分）')
    print('=' * 78)
    ts_cols = [c for c in df.columns if c.startswith('ts_')]
    print('  各指数趋势分两两相关系数:')
    tsm = df[ts_cols].corr()
    print(tsm.round(3).to_string())

    print('\n  三指数趋势分完全相同的日期数 / 总日期数:')
    trio = df[ts_cols].dropna()
    if len(trio):
        same = (trio.nunique(axis=1) == 1).sum()
        print(f'    {same}/{len(trio)} ({same/len(trio)*100:.0f}%)  ← 越高说明指数维度信息量越低')
        spread = trio.max(axis=1) - trio.min(axis=1)
        print(f'    极差均值={spread.mean():.2f} 中位={spread.median():.2f} max={spread.max():.0f}')

    # ================= G. 情绪分与实际涨跌的错配 =================
    print('\n' + '=' * 78)
    print('G. 情绪分方向性体检（情绪分是否真的对应涨跌）')
    print('=' * 78)
    for c in ['ss_上证指数', 'ss_中证2000', 'ss_沪深300']:
        if c not in sub.columns:
            continue
        g = sub[[c, '中证2000_ret1']].dropna()
        if len(g) < 10:
            continue
        hi = g[g[c] >= 50]
        lo = g[g[c] < 50]
        r, p, n = spearman_ci(g[c], g['中证2000_ret1'])
        print(f'  {c:<16} N={n:>3}  与中证2000当日涨跌 ρ={r:>6.3f} (p={p:.2f})  '
              f'| 情绪≥50 当日均涨{hi["中证2000_ret1"].mean():>5.2f}%  '
              f'情绪<50 当日均涨{lo["中证2000_ret1"].mean():>5.2f}%')

    # ================= H. 滞后效应检验 =================
    print('\n' + '=' * 78)
    print('H. 信号滞后性检验（趋势分是否在"转折后"才有信息）')
    print('=' * 78)
    sub2 = sub.sort_values('trade_date').copy()
    for c in ['eng_trend', 'eng_position']:
        if c not in sub2.columns:
            continue
        for lag in [0, 1, 2, 3]:
            r, p, n = spearman_ci(sub2[c].shift(lag), sub2['中证2000_fwd5'])
            print(f'  {c:<14} 滞后{lag}日  ρ={r:>6.3f} (p={p:.2f}, N={n})')

    # ================= I. 结论汇总数据 =================
    summary = {
        'perf': perf_rows,
        'excess_ttest': {'t': round(float(t), 3), 'p': round(float(p), 4),
                         'mean_daily_pct': round(float(exc.mean() * 100), 4)},
        'position_buckets': e,
        'trio_same_ratio': round(same / len(trio), 3) if len(trio) else None,
    }
    json.dump(summary, open(os.path.join(OUT, 'summary.json'), 'w'),
              ensure_ascii=False, indent=2, default=str)
    print(f'\n  → {OUT}/summary.json')


if __name__ == '__main__':
    main()
