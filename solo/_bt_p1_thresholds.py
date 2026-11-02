#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P1 闸门阈值专项验证 —— 用全样本198天（不只引擎有输出的111天）定阈值
问题：
  Q1 广度否决阈值该定多少？按市场状态分层后如何？
  Q2 钝化解除阈值该定多少？（35/45/55/65 对比）
  Q3 炸板率是否应该状态相关？
"""
import os
import numpy as np
import pandas as pd
from scipy import stats as sps

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, '_bt_out')

df = pd.read_csv(os.path.join(OUT, 'panel_full.csv'), dtype={'trade_date': str}).sort_values('trade_date')
df = df.reset_index(drop=True)

# 市场状态：用中证2000 当日涨跌幅分层（短线小盘代表）
df['mkt_state'] = np.where(df['中证2000_ret1'] > 0.5, '普涨日',
                    np.where(df['中证2000_ret1'] < -0.5, '普跌日', '震荡日'))

F5 = '中证2000_fwd5'
F1 = '中证2000_fwd1'
F3 = '中证2000_fwd3'


def grp(g):
    f5 = g[F5].dropna(); f1 = g[F1].dropna()
    if len(f5) == 0:
        return None
    return dict(n=len(g), f1=f1.mean(), f3=g[F3].dropna().mean(), f5=f5.mean(),
                f5_med=f5.median(), win=(f5 > 0).mean() * 100)


def show(title, s):
    print(f'\n  {title}')
    print(f'    {"分组":<22} {"N":>4} {"fwd1":>8} {"fwd3":>8} {"fwd5":>8} {"fwd5中位":>9} {"胜率":>7}')
    for k, v in s.items():
        if v is None:
            print(f'    {k:<22} {0:>4}   (无样本)')
            continue
        print(f'    {k:<22} {v["n"]:>4} {v["f1"]:>8.2f} {v["f3"]:>8.2f} {v["f5"]:>8.2f} '
              f'{v["f5_med"]:>9.2f} {v["win"]:>6.1f}%')


print('=' * 84)
print('P1 闸门阈值专项验证（全样本 198 天）')
print('=' * 84)
print(f'  样本区间 {df["trade_date"].min()} ~ {df["trade_date"].max()}，共 {len(df)} 天')
print(f'  市场状态分布: {df["mkt_state"].value_counts().to_dict()}')

# ================= Q1 广度否决阈值 =================
print('\n' + '=' * 84)
print('Q1 广度否决阈值：up_ratio 落在不同区间时，后续收益如何？')
print('=' * 84)

# 按当日市场状态 × 广度区间
for st in ['普跌日', '震荡日', '普涨日']:
    g = df[df['mkt_state'] == st]
    bins = [(0, 15, 'up<15%'), (15, 25, '15-25%'), (25, 35, '25-35%'),
            (35, 50, '35-50%'), (50, 65, '50-65%'), (65, 101, 'up>=65%')]
    s = {}
    for lo, hi, nm in bins:
        s[nm] = grp(g[(g['up_ratio'] >= lo) & (g['up_ratio'] < hi)])
    show(f'【{st}】按广度区间', s)

# 全体
s = {}
for lo, hi, nm in [(0, 15, 'up<15%'), (15, 25, '15-25%'), (25, 35, '25-35%'),
                   (35, 50, '35-50%'), (50, 65, '50-65%'), (65, 101, 'up>=65%')]:
    s[nm] = grp(df[(df['up_ratio'] >= lo) & (df['up_ratio'] < hi)])
show('【全体】按广度区间', s)

# 关键对比：普跌日下 up<35% vs up>=35%
print('\n  ▶ 关键对比（普跌日）：')
g = df[df['mkt_state'] == '普跌日']
a = grp(g[g['up_ratio'] < 35]); b = grp(g[g['up_ratio'] >= 35])
print(f'    up<35%  : N={a["n"]:<4} fwd5={a["f5"]:.2f}%  胜率={a["win"]:.0f}%')
print(f'    up>=35% : N={b["n"]:<4} fwd5={b["f5"]:.2f}%  胜率={b["win"]:.0f}%')
fa = g[g['up_ratio'] < 35][F5].dropna(); fb = g[g['up_ratio'] >= 35][F5].dropna()
try:
    _, p = sps.mannwhitneyu(fa, fb, alternative='two-sided')
    print(f'    Mann-Whitney p = {p:.3f} → {"显著差异" if p < 0.05 else "无显著差异"}')
except Exception:
    pass

# ================= Q2 钝化解除阈值 =================
print('\n' + '=' * 84)
print('Q2 钝化解除阈值：昨日<35%，今日 up 达到多少才解除？')
print('=' * 84)
df['prev_up'] = df['up_ratio'].shift(1)
df['prev2_up'] = df['up_ratio'].shift(2)

dazed = df[(df['prev_up'] < 35) & (df['up_ratio'] >= 35)].copy()
print(f'  钝化候选日（昨<35% 且今>=35%）: N={len(dazed)}')

for T in [35, 45, 55, 65]:
    rel = dazed[dazed['up_ratio'] >= T]      # 解除组
    nrel = dazed[dazed['up_ratio'] < T]      # 维持否决组
    r = grp(rel); nr = grp(nrel)
    if r and nr:
        # 检验解除组是否真的更好
        a = rel[F5].dropna(); b = nrel[F5].dropna()
        try:
            _, p = sps.mannwhitneyu(a, b, alternative='greater')
        except Exception:
            p = np.nan
        print(f'\n  阈值 T={T}%  解除组 N={r["n"]:<3} fwd5={r["f5"]:>6.2f}% 胜率={r["win"]:>5.1f}%   '
              f'维持组 N={nr["n"]:<3} fwd5={nr["f5"]:>6.2f}% 胜率={nr["win"]:>5.1f}%   '
              f'p(解除>维持)={p:.3f}')

print('\n  ▶ 钝化期内按今日广度分档（全体样本，不限钝化）：')
daz_all = df[df['prev_up'] < 35]
bins = [(0, 25, '今<25%'), (25, 35, '25-35%'), (35, 45, '35-45%'),
        (45, 55, '45-55%'), (55, 65, '55-65%'), (65, 101, '今>=65%')]
s = {nm: grp(daz_all[(daz_all['up_ratio'] >= lo) & (daz_all['up_ratio'] < hi)])
     for lo, hi, nm in bins}
show('（昨<35% 的所有日子）', s)

# ================= Q3 炸板率状态相关性 =================
print('\n' + '=' * 84)
print('Q3 炸板率是否状态相关？（按市场状态 × 炸板率高低）')
print('=' * 84)
for st in ['普涨日', '震荡日', '普跌日']:
    g = df[df['mkt_state'] == st]
    s = {
        '炸板<=25%': grp(g[g['zb_rate'] <= 25]),
        '25-35%': grp(g[(g['zb_rate'] > 25) & (g['zb_rate'] <= 35)]),
        '35-45%': grp(g[(g['zb_rate'] > 35) & (g['zb_rate'] <= 45)]),
        '炸板>45%': grp(g[g['zb_rate'] > 45]),
    }
    show(f'【{st}】', s)

# ================= Q4 组合模拟：候选规则对比 =================
print('\n' + '=' * 84)
print('Q4 端到端模拟：广度上限规则对策略收益的影响')
print('=' * 84)


def simulate(breadth_rule, label):
    """breadth_rule: (entry_thr_plain, entry_thr_bearish, release_thr)
       返回逐日仓位上限（0 或 1）与决策质量指标

       端到端累计收益会被样本期整体涨跌污染（本期中证2000 -19.7%），
       因此主指标用「决策准确率」：
         - 拦截日 fwd 收益：应显著为负（拦对了）
         - 解除日 fwd 收益：应显著为正（放对了）
       """
    up = df['up_ratio'].values / 100.0        # 统一成比例
    state = df['mkt_state'].values
    cap = np.zeros(len(df))
    dazed = False
    for i in range(len(df)):
        u = up[i]
        if np.isnan(u):
            cap[i] = np.nan; continue
        thr = breadth_rule[1] if state[i] == '普跌日' else breadth_rule[0]
        if u < thr:
            dazed = True
            cap[i] = 0
        elif dazed:
            if u >= breadth_rule[2]:
                dazed = False
                cap[i] = 1
            else:
                cap[i] = 0
        else:
            cap[i] = 1
    ret = df[F1].values / 100.0
    valid = ~np.isnan(cap) & ~np.isnan(ret)
    cap, ret = cap[valid], ret[valid]

    # 拦截日：闸门说"出"的日子
    blocked = cap == 0
    # 解除日：曾经被拦截、当天放行（上一日 blocked 且今日 unblocked）
    prev_blocked = np.r_[False, blocked[:-1]]
    released = (~blocked) & prev_blocked

    out = dict(label=label, n=int(valid.sum()))
    out['blk_n'] = int(blocked.sum())
    out['blk_f1'] = (ret[blocked].mean() * 100) if blocked.any() else np.nan
    out['blk_f5'] = (df[F5].values[valid][blocked].mean()) if blocked.any() else np.nan
    out['rel_n'] = int(released.sum())
    out['rel_f1'] = (ret[released].mean() * 100) if released.any() else np.nan
    out['rel_f5'] = (df[F5].values[valid][released].mean()) if released.any() else np.nan
    out['keep_f1'] = (ret[~blocked].mean() * 100) if (~blocked).any() else np.nan
    out['mean_pos'] = cap.mean() * 100
    return out


rules = [
    ((0.35, 0.35, 0.55), '当前: 35%阈值 + 55%解除'),
    ((0.35, 0.35, 0.45), '解除降到45%'),
    ((0.35, 0.35, 0.65), '解除升到65%'),
    ((0.35, 0.35, 0.70), '解除升到70%'),
    ((0.35, 0.25, 0.55), '普跌日阈值25%'),
    ((0.35, 0.25, 0.65), '普跌日25% + 解除65%'),
    ((0.35, 0.20, 0.65), '普跌日20% + 解除65%'),
    ((0.40, 0.30, 0.65), '普跌日30% + 解除65%'),
]
print(f'  {"规则":<26} {"拦截天":>6} {"拦截fwd1":>9} {"拦截fwd5":>9} | '
      f'{"解除天":>6} {"解除fwd1":>9} {"解除fwd5":>9} | {"放行fwd1":>9}')
print('  ' + '-' * 104)
for r, lab in rules:
    s = simulate(r, lab)
    def f(v, w=9, p=2):
        return f'{v:>{w}.{p}f}' if not np.isnan(v) else f'{"NA":>{w}}'
    print(f'  {lab:<26} {s["blk_n"]:>6} {f(s["blk_f1"])} {f(s["blk_f5"])} | '
          f'{s["rel_n"]:>6} {f(s["rel_f1"])} {f(s["rel_f5"])} | {f(s["keep_f1"])}')

print('\n  判读：')
print('    · 拦截fwd 越负越好（说明拦住的确实是下跌）')
print('    · 解除fwd 越正越好（说明放行的确实是上涨）')
print('    · 对比"放行fwd1"（不在否决区的普通日子）可判断解除日是否更优')
df.to_csv(os.path.join(OUT, 'panel_p1.csv'), index=False)
print(f'\n  → {OUT}/panel_p1.csv')
