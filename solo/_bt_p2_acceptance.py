#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P2 验收：改造后的情绪分是否真的摆脱了「当日涨跌复读」？
"""
import os
import sys
import importlib.util
import numpy as np
import pandas as pd
from scipy import stats as sps

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
BAK = os.path.join(BASE, '_bak_pre_p012_20261031')
sys.path.insert(0, BASE)

# 备份目录下的旧模块用相对路径找 .env 会失败 → 显式注入 token
from dotenv import load_dotenv
_env = os.path.join(os.path.dirname(BASE), 'config', '.env')
load_dotenv(_env)
print(f'✓ TUSHARE_TOKEN 已注入: {"有" if os.getenv("TUSHARE_TOKEN") else "无"}')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# 旧版打分函数：从备份的 market_analysis.py 里加载
old_ma = load_module('old_ma', os.path.join(BAK, 'market_analysis.py'))
print('✓ 旧版 market_analysis 已载入')
# 新版直接用当前文件（避免重复执行主流程）
import types
src = open(os.path.join(BASE, 'market_analysis.py'), encoding='utf-8').read()
src = src.split("if __name__ == '__main__':")[0]
new_ma = types.ModuleType('new_ma')
new_ma.__file__ = os.path.join(BASE, 'market_analysis.py')
exec(compile(src, new_ma.__file__, 'exec'), new_ma.__dict__)
print('✓ 新版 market_analysis 已载入')

d = pd.read_csv(os.path.join(CACHE, 'bt_idx_932000.CSI_20260101.csv'),
                 dtype={'trade_date': str}).sort_values('trade_date').reset_index(drop=True)
panel = pd.read_csv(os.path.join(BASE, '_bt_out', 'panel_full.csv'),
                    dtype={'trade_date': str}).set_index('trade_date')

rows = []
for i in range(25, len(d)):
    dt = d['trade_date'].iloc[i]
    if dt not in panel.index:
        continue
    r = panel.loc[dt]
    sub = d.iloc[:i + 1].copy()
    zt = r.get('zt2', 0); zt = 0 if zt != zt else int(zt)
    zb = r.get('zb_rate', 0); zb = 0.0 if zb != zb else float(zb)
    up = r.get('up', 0); up = 0 if up != up else int(up)
    dn = r.get('down', 0); dn = 0 if dn != dn else int(dn)
    total = up + dn

    old_ts, old_st, _ = old_ma.calc_trend_score(sub, up, total)
    old_ss, old_sst = old_ma.calc_sentiment_score(sub, zt, zb, r.get('amount_yi', 0) or 0)
    new_ts, new_st, new_det = new_ma.calc_trend_score(sub, up, total)
    new_ss, new_sst = new_ma.calc_sentiment_score(sub, zt, zb, r.get('amount_yi', 0) or 0)

    rows.append({
        'trade_date': dt, 'old_sent': old_ss, 'new_sent': new_ss,
        'old_trend': old_ts, 'new_trend': new_ts,
        'ret1': r['中证2000_ret1'], 'fwd1': r['中证2000_fwd1'],
        'fwd3': r['中证2000_fwd3'], 'fwd5': r['中证2000_fwd5'],
        'fwd10': r['中证2000_fwd10'],
        'pos_pct': new_det.get('pos_pct', 50), 'vol_pct': new_det.get('vol_pct', 50),
    })

df = pd.DataFrame(rows)
print(f'\n样本 N={len(df)}\n')


def sp(a, b):
    m = pd.Series(a).notna() & pd.Series(b).notna()
    if m.sum() < 20:
        return np.nan, np.nan
    x = pd.Series(a)[m]; y = pd.Series(b)[m]
    r = x.rank().corr(y.rank())
    try:
        p = sps.spearmanr(x, y).pvalue
    except Exception:
        p = np.nan
    return r, p


print('=' * 80)
print('验收 1：情绪分与「当日涨跌幅」的相关性（复读程度，越低越好）')
print('=' * 80)
ro, po = sp(df['old_sent'], df['ret1'])
rn, pn = sp(df['new_sent'], df['ret1'])
print(f'  旧情绪分  ρ(当日涨跌) = {ro:+.3f}  (p={po:.3f})')
print(f'  新情绪分  ρ(当日涨跌) = {rn:+.3f}  (p={pn:.3f})')
print(f'  → 复读程度下降 {abs(ro) - abs(rn):+.3f}')

print('\n' + '=' * 80)
print('验收 2：情绪分对「未来收益」的预测力（越显著越好）')
print('=' * 80)
print(f'  {"信号":<12} {"ρ(fwd1)":>9} {"p":>7} {"ρ(fwd3)":>9} {"p":>7} {"ρ(fwd5)":>9} {"p":>7} {"ρ(fwd10)":>9} {"p":>7}')
for name, col in [('旧情绪分', 'old_sent'), ('新情绪分', 'new_sent'),
                   ('新趋势分', 'new_trend'), ('旧趋势分', 'old_trend')]:
    line = f'  {name:<12}'
    for h in (1, 3, 5, 10):
        r, p = sp(df[col], df[f'fwd{h}'])
        line += f'{r:>9.3f} {p:>7.3f}' if not np.isnan(r) else f'{"NA":>9} {"":>7}'
    print(line)

print('\n' + '=' * 80)
print('验收 3：新情绪分各分项对未来的贡献（拆解验证）')
print('=' * 80)
d2 = pd.read_csv(os.path.join(CACHE, 'bt_idx_932000.CSI_20260101.csv'),
                 dtype={'trade_date': str}).sort_values('trade_date').reset_index(drop=True)
# 复算各分项
items = []
for i in range(25, len(d2)):
    dt = d2['trade_date'].iloc[i]
    if dt not in panel.index:
        continue
    r = panel.loc[dt]
    sub = d2.iloc[:i + 1].copy()
    reg = new_ma.calc_position_vol_regime(sub)
    vol5 = sub['vol'].tail(5).mean(); vol20 = sub['vol'].tail(20).mean()
    vr = vol5 / vol20 if vol20 > 0 else 1
    amt5 = sub['amount'].tail(5).mean(); amt20 = sub['amount'].tail(20).mean()
    ar = amt5 / amt20 if amt20 > 0 else 1
    zt = r.get('zt2', 0); zt = 0 if zt != zt else int(zt)
    zb = r.get('zb_rate', 0); zb = 0.0 if zb != zb else float(zb)
    zq = 12
    if zt > 0:
        zq = 25 if zb < 10 else 20 if zb < 20 else 12 if zb < 35 else 5
    elif zb > 50:
        zq = 0
    items.append({'trade_date': dt, 'ztq': zq, 'vr': vr, 'vp': reg['vol_pct'],
                  'ar': ar, 'pp': reg['pos_pct']})
it = pd.DataFrame(items)
m = df.merge(it, on='trade_date')
print(f'  {"分项":<10} {"ρ(当日涨跌)":>11} {"ρ(fwd1)":>9} {"p":>7} {"ρ(fwd5)":>9} {"p":>7} {"ρ(fwd10)":>9} {"p":>7}')
for c, nm in [('ztq', '封板质量'), ('vr', '量能结构'), ('vp', '波动率状态'),
              ('ar', '成交额趋势'), ('pp', '位置状态')]:
    rd, _ = sp(m[c], m['ret1'])
    r1, p1 = sp(m[c], m['fwd1'])
    r5, p5 = sp(m[c], m['fwd5'])
    r10, p10 = sp(m[c], m['fwd10'])
    print(f'  {nm:<10} {rd:>11.3f} {r1:>9.3f} {p1:>7.3f} {r5:>9.3f} {p5:>7.3f} {r10:>9.3f} {p10:>7.3f}')

print('\n' + '=' * 80)
print('验收 4：新旧情绪分的相关性与分布')
print('=' * 80)
rr, _ = sp(df['old_sent'], df['new_sent'])
print(f'  新旧情绪分 ρ = {rr:.3f}')
print(f'  旧情绪分: 均值{df["old_sent"].mean():.1f}  标准差{df["old_sent"].std():.1f}  '
      f'区间[{df["old_sent"].min():.0f}, {df["old_sent"].max():.0f}]')
print(f'  新情绪分: 均值{df["new_sent"].mean():.1f}  标准差{df["new_sent"].std():.1f}  '
      f'区间[{df["new_sent"].min():.0f}, {df["new_sent"].max():.0f}]')

df.to_csv(os.path.join(BASE, '_bt_out', 'p2_acceptance.csv'), index=False)
print(f'\n  → _bt_out/p2_acceptance.csv')
