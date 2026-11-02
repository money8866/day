#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P2 信号体系标定 —— 用全样本198天做组件筛选与权重反推
Q1 情绪分各分项：哪些对 fwd1/fwd5 真有信息？（用真实 zt/zhaban/amount 数据）
Q2 趋势分：ma/index/breadth 三组件各自对 fwd 的区分度如何？权重怎么标定？
"""
import os
import itertools
import numpy as np
import pandas as pd
from scipy import stats as sps

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, '_bt_out')

df = pd.read_csv(os.path.join(OUT, 'panel_full.csv'), dtype={'trade_date': str}).sort_values('trade_date')
df = df.reset_index(drop=True)
df['mkt_state'] = np.where(df['中证2000_ret1'] > 0.5, '普涨日',
                    np.where(df['中证2000_ret1'] < -0.5, '普跌日', '震荡日'))

print('=' * 88)
print('P2 信号体系标定分析（全样本 198 天）')
print('=' * 88)

# ---------- 构造组件分数（与 market_analysis.py 口径一致） ----------
def calc_trend_components(sub_df, up_count, total_count):
    """复刻 calc_trend_score 的三个组件"""
    if sub_df is None or len(sub_df) < 20:
        return None
    latest = sub_df.iloc[-1]
    ma5 = sub_df['close'].rolling(5).mean().iloc[-1]
    ma10 = sub_df['close'].rolling(10).mean().iloc[-1]
    ma20 = sub_df['close'].rolling(20).mean().iloc[-1]
    ma5s = sub_df['close'].rolling(5).mean()
    ma20s = sub_df['close'].rolling(20).mean()
    ma5_slope = ma20_slope = 0.0
    if len(ma5s) >= 6 and not pd.isna(ma5s.iloc[-6]) and ma5s.iloc[-6] > 0:
        ma5_slope = (ma5 - ma5s.iloc[-6]) / ma5s.iloc[-6] * 100
    if len(ma20s) >= 21 and not pd.isna(ma20s.iloc[-21]) and ma20s.iloc[-21] > 0:
        ma20_slope = (ma20 - ma20s.iloc[-21]) / ma20s.iloc[-21] * 100
    if ma5 > ma10 > ma20:
        ma = 50 if (ma5_slope > 2 and ma20_slope > 0.5) else (40 if ma5_slope < 0.5 else 45)
    elif ma5 > ma10:
        ma = 38
    elif ma5 > ma20:
        ma = 25
    elif ma5 < ma10 < ma20:
        ma = 22 if (ma5_slope > 0 and len(ma5s) >= 4 and ma5 > ma5s.iloc[-4]) else 12
    else:
        ma = 18
    idx = 30 if latest['close'] > ma20 else (20 if latest['close'] > ma10 else
           (10 if latest['close'] > ma5 else 0))
    if total_count > 0:
        ur = up_count / total_count * 100
        br = (20 if ur > 70 else 17 if ur >= 60 else 14 if ur >= 50 else
              8 if ur >= 40 else 4 if ur >= 30 else 0)
    else:
        br = 0
    return ma, idx, br, latest


# 从缓存复现指数序列
IDX = {'上证指数': 'bt_idx_000001.SH_20260101.csv',
       '沪深300': 'bt_idx_000300.SH_20260101.csv',
       '中证2000': 'bt_idx_932000.CSI_20260101.csv'}
idx_data = {}
for nm, f in IDX.items():
    p = os.path.join(BASE, 'cache_backbone_tushare', f)
    d = pd.read_csv(p, dtype={'trade_date': str}).sort_values('trade_date').reset_index(drop=True)
    idx_data[nm] = d

rows = []
for nm, d in idx_data.items():
    for i in range(25, len(d)):
        dt = d['trade_date'].iloc[i]
        m = df.index[df['trade_date'] == dt]
        if len(m) == 0:
            continue
        r = df.loc[m[0]]
        up = r['up']; dn = r['down']
        if pd.isna(up):
            continue
        comp = calc_trend_components(d.iloc[:i + 1].copy(), up, up + dn)
        if comp is None:
            continue
        ma, ix, br, latest = comp
        sub = d.iloc[:i + 1]
        vol5 = sub['vol'].tail(5).mean(); vol20 = sub['vol'].tail(20).mean()
        vol_ratio = vol5 / vol20 if vol20 > 0 else 1
        amt5 = sub['amount'].tail(5).mean(); amt20 = sub['amount'].tail(20).mean()
        amt_ratio = amt5 / amt20 if amt20 > 0 else 1
        vola = (latest['high'] - latest['low']) / latest['low'] * 100
        std20 = sub['pct_chg'].tail(20).std()
        rows.append({
            'trade_date': dt, 'name': nm,
            'ma': ma, 'idx': ix, 'br': br,
            'pct_chg': latest['pct_chg'],
            'vol_ratio': vol_ratio, 'amt_ratio': amt_ratio,
            'amp': vola, 'std20': std20 if not pd.isna(std20) else 1.5,
            'zt': r.get('zt2', np.nan), 'zb_rate': r.get('zb_rate', np.nan),
            'fwd1': r['中证2000_fwd1'], 'fwd3': r['中证2000_fwd3'],
            'fwd5': r['中证2000_fwd5'], 'fwd10': r['中证2000_fwd10'],
            'up_ratio': r['up_ratio'], 'mkt_state': r['mkt_state'],
        })
comp_df = pd.DataFrame(rows)
print(f'\n组件样本: {len(comp_df)} 行')

# ================= Q1 情绪分组件信息含量 =================
print('\n' + '=' * 88)
print('Q1 情绪分候选组件 与未来收益的相关性（中证2000 口径）')
print('=' * 88)
z = comp_df[comp_df['name'] == '中证2000'].copy()
# 构造候选组件（对照新方案）
z['c_vol'] = (z['vol_ratio'] - 1) * 15                      # 相对量能（不含涨跌方向）
z['c_amp_risk'] = -z['amp']                                  # 振幅越大→追高风险越高
z['c_volatility'] = z['std20']                               # 20日波动率
z['c_ztq'] = -z['zb_rate']                                   # 炸板率越低质量越高
z['c_amt'] = z['amt_ratio']                                  # 成交额比
z['c_pos60'] = np.nan                                        # 位置分位稍后填

# 位置分位：收盘在 60 日区间的分位
d = idx_data['中证2000']
for i in range(len(z)):
    pass
pos_map = {}
for i in range(25, len(d)):
    w = d['close'].iloc[max(0, i - 59):i + 1]
    lo, hi = w.min(), w.max()
    pos_map[d['trade_date'].iloc[i]] = (d['close'].iloc[i] - lo) / (hi - lo) * 100 if hi > lo else 50
z['c_pos60'] = z['trade_date'].map(pos_map)

cands = ['pct_chg', 'vol_ratio', 'amp', 'std20', 'zb_rate', 'amt_ratio', 'c_pos60',
         'c_volatility', 'c_amp_risk', 'c_ztq', 'c_vol', 'c_amt']
print(f'  {"组件":<14} {"ρ(当日涨跌)":>11} {"ρ(fwd1)":>9} {"p":>7} {"ρ(fwd5)":>9} {"p":>7} {"ρ(fwd10)":>9} {"p":>7}')
print('  ' + '-' * 82)
info = {}
for c in cands:
    g = z[[c, 'fwd1', 'fwd3', 'fwd5', 'fwd10', 'pct_chg']].copy()
    g.columns = ['v', 'f1', 'f3', 'f5', 'f10', 'd0']
    g = g.dropna()
    if len(g) < 30:
        continue
    def sp(a, b):
        m = a.notna() & b.notna()
        if m.sum() < 20: return np.nan, np.nan
        r = a[m].rank().corr(b[m].rank())
        try: p = sps.spearmanr(a[m], b[m]).pvalue
        except Exception: p = np.nan
        return r, p
    rd, _ = sp(g['v'], g['d0'])
    r1, p1 = sp(g['v'], g['f1'])
    r5, p5 = sp(g['v'], g['f5'])
    r10, p10 = sp(g['v'], g['f10'])
    print(f'  {c:<14} {rd:>11.3f} {r1:>9.3f} {p1:>7.3f} {r5:>9.3f} {p5:>7.3f} {r10:>9.3f} {p10:>7.3f}')
    info[c] = {'r_fwd1': r1, 'p_fwd1': p1, 'r_fwd5': r5, 'p_fwd5': p5,
               'r_fwd10': r10, 'p_fwd10': p10, 'r_day0': rd}

print('\n  ▶ 现有情绪分组件（对照）：')
print('    direction(涨跌) / streak(连涨跌) 与"当日涨跌"共线 → 复读，无前瞻')
print('    vol(量能) 与 amt(成交额) 高度共线 → 只算其一')

# 量能 vs 成交额 二选一
if 'vol_ratio' in info and 'amt_ratio' in info:
    print(f'    ρ(vol_ratio, amt_ratio) = {z["vol_ratio"].corr(z["amt_ratio"]):.3f} → 共线')

# ================= Q2 趋势分权重标定 =================
print('\n' + '=' * 88)
print('Q2 趋势分权重标定：搜索让"分档单调性"最好的权重组合')
print('=' * 88)

z2 = z.dropna(subset=['ma', 'idx', 'br', 'fwd5']).copy()
print(f'  标定样本: {len(z2)} 行（中证2000）')


def mono_score(ts_vals, fwd):
    """把趋势分分5档，返回 (单调性rho, 各档均值)"""
    try:
        b = pd.qcut(pd.Series(ts_vals).rank(method='first'), 5, labels=[1, 2, 3, 4, 5])
    except Exception:
        return np.nan, None
    g = pd.DataFrame({'b': b, 'f': fwd}).dropna()
    if len(g) < 50:
        return np.nan, None
    means = g.groupby('b')['f'].mean()
    if len(means) < 5:
        return np.nan, None
    rho = sps.spearmanr(means.index.astype(float), means.values).statistic
    return rho, means


base_w = {'ma': 50, 'idx': 30, 'br': 20}
r0, m0 = mono_score(z2['ma'] * base_w['ma'] / 50 + z2['idx'] * base_w['idx'] / 30
                    + z2['br'] * base_w['br'] / 20, z2['fwd5'])
print(f'\n  当前权重 ma50/idx30/br20 → 分档单调性 ρ={r0:.3f}')
if m0 is not None:
    print('    各档 fwd5 均值: ' + '  '.join(f'档{int(i)}:{v:.2f}%' for i, v in m0.items()))

print('\n  网格搜索（权重和=100，单调性 ρ 越高越好）：')
results = []
for wm in range(20, 61, 5):
    for wi in range(10, 51, 5):
        wb = 100 - wm - wi
        if wb < 0 or wb > 40:
            continue
        ts = z2['ma'] * wm / 50 + z2['idx'] * wi / 30 + z2['br'] * wb / 20
        rho, means = mono_score(ts, z2['fwd5'])
        if np.isnan(rho):
            continue
        results.append({'wm': wm, 'wi': wi, 'wb': wb, 'mono_rho': rho,
                        'r_fwd5': sps.spearmanr(ts, z2['fwd5']).statistic})
res = pd.DataFrame(results).sort_values('mono_rho', ascending=False)
print(f'    {"ma":>4} {"idx":>4} {"br":>4} {"单调性ρ":>9} {"ρ(fwd5)":>9}')
for _, r in res.head(8).iterrows():
    print(f'    {r["wm"]:>4} {r["wi"]:>4} {r["wb"]:>4} {r["mono_rho"]:>9.3f} {r["r_fwd5"]:>9.3f}')
print('  ...')
for _, r in res.tail(3).iterrows():
    print(f'    {r["wm"]:>4} {r["wi"]:>4} {r["wb"]:>4} {r["mono_rho"]:>9.3f} {r["r_fwd5"]:>9.3f}')

best = res.iloc[0]
print(f'\n  最优组合: ma{best["wm"]:.0f}/idx{best["wi"]:.0f}/br{best["wb"]:.0f}  '
      f'单调性ρ={best["mono_rho"]:.3f}（当前 {r0:.3f}）')

# 稳健性：各组件单独分档的 fwd5（是否有区分度）
print('\n  各组件单独分档的 fwd5 均值（看组件本身有无区分度）:')
for c in ['ma', 'idx', 'br']:
    b = pd.qcut(z2[c].rank(method='first'), 4, labels=[1, 2, 3, 4])
    g = pd.DataFrame({'b': b, 'f': z2['fwd5']}).dropna()
    means = g.groupby('b')['f'].mean()
    rho = sps.spearmanr(means.index.astype(float), means.values).statistic
    print(f'    {c:<4} ' + '  '.join(f'档{int(i)}:{v:>6.2f}%(N={len(g[g["b"]==i])})'
                                     for i, v in means.items()) + f'   单调性ρ={rho:+.3f}')

z.to_csv(os.path.join(OUT, 'panel_p2_components.csv'), index=False)
res.to_csv(os.path.join(OUT, 'p2_weight_grid.csv'), index=False)
print(f'\n  → {OUT}/panel_p2_components.csv, p2_weight_grid.csv')
