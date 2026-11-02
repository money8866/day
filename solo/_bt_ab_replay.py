#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
改动前后 A/B 回放对比
用同一份历史数据分别跑「备份的旧引擎」和「改造后的新引擎」，对比仓位序列与收益。
"""
import os
import sys
import importlib.util
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, 'cache_backbone_tushare')
BAK = os.path.join(BASE, '_bak_pre_p012_20261031')
sys.path.insert(0, BASE)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# ---------- 载入新旧两套引擎 ----------
print('载入引擎...')
sys.path.insert(0, BAK)
old_v95 = load_module('old_v95', os.path.join(BAK, 'position_engine_v95.py'))
sys.path.remove(BAK)
new_v95 = load_module('new_v95', os.path.join(BASE, 'position_engine_v95.py'))
import position_engine_v9 as v9mod
print('  ✓ 旧/新 position_engine_v95 已载入')

# ---------- 准备历史数据 ----------
panel = pd.read_csv(os.path.join(BASE, '_bt_out', 'panel_full.csv'), dtype={'trade_date': str})
panel = panel.sort_values('trade_date').reset_index(drop=True)

IDX_FILES = {
    '中证2000': 'bt_idx_932000.CSI_20260101.csv',
    '上证指数': 'bt_idx_000001.SH_20260101.csv',
    '沪深300': 'bt_idx_000300.SH_20260101.csv',
    '中证1000': 'bt_idx_000852.SH_20260101.csv',
    '创业板指': 'bt_idx_399006.SZ_20260101.csv',
    '科创50': 'bt_idx_000688.SH_20260101.csv',
}
idx_all = {}
for nm, f in IDX_FILES.items():
    d = pd.read_csv(os.path.join(CACHE, f), dtype={'trade_date': str}).sort_values('trade_date')
    idx_all[nm] = d.reset_index(drop=True)

dates = [d for d in panel['trade_date'].tolist() if d >= '20260520']
print(f'回放区间: {dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日\n')


def _num(v, default=0.0):
    """安全转数字：NaN → default"""
    try:
        f = float(v)
        if f != f:
            return default
        return f
    except Exception:
        return default


def build_bundle(dt, panel_row, panel_hist):
    """构造某一天的 MarketDataBundle"""
    b = v9mod.MarketDataBundle()
    idd = {}
    for nm, d in idx_all.items():
        sub = d[d['trade_date'] <= dt]
        if len(sub) >= 25:
            idd[nm] = sub.reset_index(drop=True)
    b.index_data = idd

    up = panel_row.get('up', 0)
    dn = panel_row.get('down', 0)
    zt = panel_row.get('zt2', 0)
    dt_cnt = panel_row.get('dt2', 0)
    zb = panel_row.get('zb_rate', 0)
    b.overview = {
        'up_count': int(_num(up)),
        'down_count': int(_num(dn)),
        'zt_count': int(_num(zt)),
        'dt_count': int(_num(dt_cnt)),
        'zb_count': int(_num(panel_row.get('zhaban_count', 0))),
        'zb_rate': _num(zb),
        'amount': _num(panel_row.get('amount_yi', 0)) * 1e8,
    }
    b.limit_stats = {
        'zt_count': int(_num(zt)),
        'dt_count': int(_num(dt_cnt)),
        'broken_rate': _num(zb),
        'zhaban_count': int(_num(panel_row.get('zhaban_count', 0))),
        'up_count': int(_num(up)),
        'down_count': int(_num(dn)),
    }
    b.max_limit_height = int(_num(panel_row.get('max_limit_height', 0)))
    b.theme_top3_scores = []
    hist = panel_hist[['trade_date', 'zt2', 'dt2', 'zb_rate', 'up_ratio']].copy()
    hist = hist.rename(columns={'zt2': 'zt_count', 'dt2': 'dt_count', 'up_ratio': 'up_ratio'})
    b.hist_limit = hist
    return b


def run_series(engine_cls, label, use_prev_up=True):
    """链式回放，产出每日仓位"""
    pos, states, details = [], [], []
    prev_pos = None
    for dt in dates:
        mask = panel['trade_date'] <= dt
        panel_hist = panel[mask].copy()
        row = panel[panel['trade_date'] == dt].iloc[0]
        b = build_bundle(dt, row, panel_hist)
        # 前一日上涨占比（钝化解除用）
        hist_dates = [d for d in panel_hist['trade_date'].tolist() if d < dt]
        if use_prev_up and hist_dates:
            prev_row = panel[panel['trade_date'] == hist_dates[-1]].iloc[0]
            b.prev_up_ratio = _num(prev_row.get('up_ratio', 50), 50.0)
        else:
            b.prev_up_ratio = None
        try:
            r = engine_cls().analyze(b, prev_pos)
            p = int(r.position_target)
            states.append(getattr(r, 'market_state', ''))
            details.append({
                'action': r.action, 'regime': r.market_regime,
                'up_ratio': round(r.up_ratio * 100, 1),
                'pos_pct': round(getattr(r, 'pos_pct', 50), 1),
                'vol_pct': round(getattr(r, 'vol_pct', 50), 1),
            })
        except Exception as e:
            p = 0
            states.append('ERR')
            details.append({'action': f'ERR:{e}', 'regime': '', 'up_ratio': 0,
                            'pos_pct': 0, 'vol_pct': 0})
        pos.append(p)
        prev_pos = p
    return pd.DataFrame({'trade_date': dates, 'pos': pos, 'state': states}), details


print('回放旧引擎...')
old_df, old_det = run_series(old_v95.PositionEngineV99, 'old')
print('回放新引擎...')
new_df, new_det = run_series(new_v95.PositionEngineV99, 'new')

df = panel.set_index('trade_date')
df = df.reindex(old_df['trade_date']).reset_index()
df['old_pos'] = old_df['pos'].values
df['new_pos'] = new_df['pos'].values
df['state'] = old_df['state'].values
df['old_action'] = [d['action'] for d in old_det]
df['new_action'] = [d['action'] for d in new_det]
df['old_regime'] = [d['regime'] for d in old_det]
df['new_regime'] = [d['regime'] for d in new_det]
df['pos_pct'] = [d['pos_pct'] for d in new_det]
df['vol_pct'] = [d['vol_pct'] for d in new_det]
df['up_ratio'] = [d['up_ratio'] for d in new_det]

f1 = df['中证2000_fwd1'].values / 100.0
df['old_ret'] = df['old_pos'] / 100.0 * f1
df['new_ret'] = df['new_pos'] / 100.0 * f1
df['bh_ret'] = f1

print('=' * 84)
print('A/B 回放对比（中证2000，信号当日收盘 → 次日收益）')
print('=' * 84)


def perf(r, label):
    r = r.dropna()
    if len(r) == 0:
        return
    cum = (1 + r).prod() - 1
    vol = r.std() * np.sqrt(252)
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    eq = (1 + r).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    print(f'  {label:<24} 累计{cum*100:>8.2f}%  年化波动{vol*100:>6.2f}%  '
          f'Sharpe{sh:>6.2f}  回撤{mdd*100:>7.2f}%  胜率{(r>0).mean()*100:>5.1f}%')


perf(df['old_ret'], '旧引擎仓位')
perf(df['new_ret'], '新引擎仓位')
perf(df['bh_ret'], '满仓买入持有')
perf(df['bh_ret'] * 0.5, '半仓买入持有')

exc = (df['new_ret'] - df['old_ret']).dropna()
from scipy import stats as sps
t, p = sps.ttest_1samp(exc, 0)
print(f'\n  新旧超额(日频) 均值{exc.mean()*100:+.4f}%  t={t:.2f}  p={p:.3f}  '
      f'→ {"显著" if p < 0.05 else "不显著"}')
print(f'  仓位变化天数: {(df["old_pos"] != df["new_pos"]).sum()} / {len(df)}')
print(f'  旧引擎平均仓位 {df["old_pos"].mean():.1f}%  新引擎 {df["new_pos"].mean():.1f}%')
print(f'  新引擎空仓天数 {int((df["new_pos"]==0).sum())}  旧引擎 {int((df["old_pos"]==0).sum())}')

print('\n  仓位变动明细:')
chg = df[df['old_pos'] != df['new_pos']]
print(f'  {"日期":<10} {"旧":>4} {"新":>4} {"up%":>6} {"位置":>6} {"波动":>6} {"fwd1":>7}  新动作')
for _, r in chg.iterrows():
    print(f'  {r["trade_date"]:<10} {r["old_pos"]:>4} {r["new_pos"]:>4} {r["up_ratio"]:>6.1f} '
          f'{r["pos_pct"]:>6.1f} {r["vol_pct"]:>6.1f} {r["中证2000_fwd1"]:>7.2f}  {r["new_action"][:22]}')

df.to_csv(os.path.join(BASE, '_bt_out', 'ab_replay.csv'), index=False)
print(f'\n  → _bt_out/ab_replay.csv')
