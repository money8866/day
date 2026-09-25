# -*- coding: utf-8 -*-
"""Post-FirstBoard 可交易量价 Alpha V2.0 · 特征表 + 目标收益

Entry Anchor 严格口径（§7/§8）
  E2（主口径）: SIGNAL = T+2 收盘（信息截止 T+2 close）  ENTRY = T+3 开盘
  E1（次口径）: SIGNAL = T+1 收盘                       ENTRY = T+2 开盘
  盘中 Entry：历史缓存无可靠分钟/逐笔数据 → 不模拟（§7 强制）
  持有 N 交易日 = ENTRY 开盘 → 第 N 个交易日收盘

输出 out/pfb_feat.parquet
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
KMAX = 25
HOR = (3, 5, 10)

M = dict(np.load(os.path.join(OUT, 'tr_mats.npz'), allow_pickle=False))
X = dict(np.load(os.path.join(OUT, 'pfb_extra.npz'), allow_pickle=False))
B = pd.read_parquet(os.path.join(OUT, 'pfb_base.parquet'))

cl, hi, lo, op, vo, amt = M['cl'], M['hi'], M['lo'], M['op'], M['vo'], M['amt']
idxc, vr, vov0, vov5 = M['idxc'], M['vr'], M['vov0'], M['vov5']
cp, dayret, dma5, dma10, dma20, dma60 = M['cp'], M['dayret'], M['dma5'], M['dma10'], M['dma20'], M['dma60']
rs, isl, lim, ow, to_rate = M['rs'], M['isl'], M['lim'], M['ow'], M['to_rate']
C0, H0, L0, O0, V0 = M['C0'], M['H0'], M['L0'], M['O0'], M['V0']
ma5, ma10, ma20, ma60 = X['ma5'], X['ma10'], X['ma20'], X['ma60']
hi20, lo20, atr14, rvol20 = X['hi20'], X['lo20'], X['atr14'], X['rvol20']
vma5n, vma10, vma20n, ama5p = X['vma5'], X['vma10'], X['vma20'], X['ama5p']
ind_ret, pc0a = X['ind_ret'], X['pc0a']
n = cl.shape[0]


def c(m, k):
    return m[:, k].astype(np.float64)


def ratio(a, b):
    with np.errstate(all='ignore'):
        return np.where(np.isfinite(a) & np.isfinite(b) & (np.abs(b) > 1e-12), a / b, np.nan)


F = {}

# ── A. 收益（T+1 / T+2） ──
F['r1'] = c(cl, 1) / c(cl, 0) - 1
F['r2'] = c(cl, 2) / c(cl, 1) - 1
F['r_cum12'] = c(cl, 2) / c(cl, 0) - 1
F['rs1'] = c(rs, 1)
F['rs2'] = c(rs, 2)
F['rs_cum12'] = F['r_cum12'] - (c(idxc, 2) / c(idxc, 0) - 1)
F['rs_ind1'] = c(dayret, 1) - c(ind_ret, 1)
F['rs_ind2'] = c(dayret, 2) - c(ind_ret, 2)
F['rs_ind_cum12'] = F['r_cum12'] - (c(ind_ret, 1) + c(ind_ret, 2))

# ── B. 价格位置 ──
F['cp1'] = c(cp, 1)
F['cp2'] = c(cp, 2)
F['cp_mean12'] = np.nanmean(np.stack([c(cp, 1), c(cp, 2)], 1), axis=1)
F['hp1'] = ratio(c(hi, 1) - np.maximum(c(op, 1), c(cl, 1)), c(hi, 1) - c(lo, 1))
F['hp2'] = ratio(c(hi, 2) - np.maximum(c(op, 2), c(cl, 2)), c(hi, 2) - c(lo, 2))
F['distC0_1'] = c(cl, 1) / C0 - 1
F['distC0_2'] = c(cl, 2) / C0 - 1
F['distH0_1'] = c(cl, 1) / H0 - 1
F['distH0_2'] = c(cl, 2) / H0 - 1
F['distL0_2'] = c(cl, 2) / L0 - 1
F['disthi20_1'] = c(cl, 1) / c(hi20, 1) - 1
F['disthi20_2'] = c(cl, 2) / c(hi20, 2) - 1
F['distlo20_2'] = c(cl, 2) / c(lo20, 2) - 1

# ── C. 波动 ──
F['amp1'] = (c(hi, 1) - c(lo, 1)) / c(cl, 0)
F['amp2'] = (c(hi, 2) - c(lo, 2)) / c(cl, 1)
F['gap1'] = c(op, 1) / c(cl, 0) - 1
F['gap2'] = c(op, 2) / c(cl, 1) - 1
F['atr14_2'] = c(atr14, 2) / c(cl, 2)
F['rvol20_2'] = c(rvol20, 2)
F['rng2'] = (c(hi, 2) - c(lo, 2)) / c(cl, 1)
F['hl_ratio_2'] = c(hi, 2) / c(lo, 2) - 1

# ── D. 成交量（T+1 / T+2） ──
F['vr1'] = c(vr, 1)
F['vr2'] = c(vr, 2)
F['vr_mean12'] = np.nanmean(np.stack([c(vr, 1), c(vr, 2)], 1), axis=1)
F['vo_vma5_2'] = c(vo, 2) / c(vma5n, 2)
F['vo_vma10_2'] = c(vo, 2) / c(vma10, 2)
F['vo_vma20_2'] = c(vo, 2) / c(vma20n, 2)
F['vo_vov0_1'] = c(vov0, 1)
F['vo_vov0_2'] = c(vov0, 2)
F['vo_vov0_mean12'] = np.nanmean(np.stack([c(vov0, 1), c(vov0, 2)], 1), axis=1)
F['vol_chg2'] = c(vo, 2) / c(vo, 1) - 1
F['vol_acc2'] = (c(vo, 2) / c(vo, 1)) - (c(vo, 1) / c(vo, 0))
F['vol_cum12_v0'] = (c(vo, 1) + c(vo, 2)) / V0
F['amt_cum12'] = c(amt, 1) + c(amt, 2)

# ── E. 换手 ──
F['to2'] = c(to_rate, 2)
F['to1'] = c(to_rate, 1)
F['to_ma3_2'] = ratio(c(to_rate, 2), np.nanmean(np.stack([c(to_rate, 0), c(to_rate, 1), c(to_rate, 2)]), 0))
F['to_chg2'] = c(to_rate, 2) - c(to_rate, 1)
F['to_cum012'] = np.nansum(np.stack([c(to_rate, 0), c(to_rate, 1), c(to_rate, 2)]), 0)
F['to_vr2'] = ratio(c(to_rate, 2), c(to_rate, 1))

# ── F. K线结构 ──
for k, tag in ((1, '1'), (2, '2')):
    rng = c(hi, k) - c(lo, k)
    F['body' + tag] = ratio(c(cl, k) - c(op, k), rng)
    F['upper' + tag] = ratio(c(hi, k) - np.maximum(c(op, k), c(cl, k)), rng)
    F['lower' + tag] = ratio(np.minimum(c(op, k), c(cl, k)) - c(lo, k), rng)
    F['oc' + tag] = c(cl, k) / c(op, k) - 1
    F['co' + tag] = c(op, k) / c(cl, k - 1) - 1

F['body_sum12'] = F['body1'] + F['body2']
F['upper_sum12'] = F['upper1'] + F['upper2']

# ── G. 趋势 ──
F['dma5_1'] = ratio(c(cl, 1), c(ma5, 1)) - 1
F['dma5_2'] = ratio(c(cl, 2), c(ma5, 2)) - 1
F['dma10_2'] = ratio(c(cl, 2), c(ma10, 2)) - 1
F['dma20_2'] = ratio(c(cl, 2), c(ma20, 2)) - 1
F['dma60_2'] = ratio(c(cl, 2), c(ma60, 2)) - 1
F['ma5slope_1'] = ratio(c(ma5, 1), c(ma5, 0)) - 1
F['ma5slope_2'] = ratio(c(ma5, 2), c(ma5, 1)) - 1
F['ma10slope_2'] = ratio(c(ma10, 2), c(ma10, 1)) - 1
F['ma20slope_2'] = ratio(c(ma20, 2), c(ma20, 1)) - 1
F['ma5_over_ma20'] = ratio(c(ma5, 2), c(ma20, 2)) - 1

# ── H. 市场环境（T+1 / T+2，Entry 前可见） ──
for kk, tag in ((1, '1'), (2, '2')):
    F['mkt_net_' + tag] = X['br_net'][:, kk].astype(np.float64)
    F['mkt_lu_' + tag] = X['br_lu'][:, kk].astype(np.float64)
    F['mkt_prevlu_' + tag] = X['br_prevlu'][:, kk].astype(np.float64)
    F['mkt_ret_' + tag] = X['br_mkt'][:, kk].astype(np.float64)
F['mkt_net_2'] = X['br_net'][:, 2].astype(np.float64)

# ── T0 首板特征（仅作条件变量） ──
R0 = H0 - L0
F['t0_ret'] = C0 / M['PC0'] - 1
F['t0_turnover'] = c(to_rate, 0)
F['t0_vr20'] = c(vr, 0)
F['t0_amt'] = np.log10(np.maximum(c(amt, 0), 1.0))
F['t0_amp'] = (H0 - L0) / M['PC0']
F['t0_cp'] = (C0 - L0) / np.where(R0 > 0, R0, np.nan)
F['t0_body'] = (C0 - O0) / R0
F['t0_upper'] = (H0 - np.maximum(O0, C0)) / R0
F['t0_lower'] = (np.minimum(O0, C0) - L0) / R0
F['t0_oneword'] = c(ow, 0)
F['t0_distma20'] = C0 / c(ma20, 0) - 1
F['t0_distma60'] = C0 / c(ma60, 0) - 1

feat = pd.DataFrame(F)
feat.insert(0, 'event_id', B['event_id'].values)
feat['t0_ret20prev'] = np.nan  # 由 _build_first_board 的 ret20_prev 提供，见下
fb0 = pd.read_parquet(os.path.join(OUT, 'first_board_events.parquet'))
fb0 = fb0.merge(B[['event_id', 'ts_code', 'trade_date']], on=['ts_code', 'trade_date'], how='inner')
fb0 = fb0.drop_duplicates(['ts_code', 'trade_date'])
feat = feat.merge(fb0[['event_id', 't0_ret20prev', 't0_ret5prev', 'one_word', 'opened_board',
                       't0_quality', 'limit_pct', 'amplitude']], on='event_id', how='left')

# ── 目标收益（Entry Anchor 严格） ──
def fwd_from_entry(k_entry, N):
    e = c(op, k_entry)
    x = c(cl, k_entry + N - 1)
    with np.errstate(all='ignore'):
        return np.where((e > 0) & np.isfinite(e) & np.isfinite(x), x / e - 1.0, np.nan)


def mae_mfe(k_entry, N):
    e = c(op, k_entry)
    j = np.arange(k_entry, k_entry + N)
    lows = lo[:, j]
    highs = hi[:, j]
    with np.errstate(all='ignore'):
        return np.nanmin(lows, 1) / e - 1, np.nanmax(highs, 1) / e - 1


T = {}
for N in HOR:
    T['fwd%d' % N] = fwd_from_entry(3, N)
    T['fwd%d_e1' % N] = fwd_from_entry(2, N)
T['d1_ret'] = c(cl, 3) / c(op, 3) - 1
mae3, mfe3 = mae_mfe(3, 3)
mae5, mfe5 = mae_mfe(3, 5)
mae10, mfe10 = mae_mfe(3, 10)
T['mae3'], T['mfe3'] = mae3, mfe3
T['mae5'], T['mfe5'] = mae5, mfe5
T['mae10'], T['mfe10'] = mae10, mfe10
# T0 锚点参照（与历史研究可比，但含 anchor drift）
for N in HOR:
    T['t0_fwd%d' % N] = c(cl, N) / c(cl, 0) - 1
# 事件锚点参照（T+2 收盘进场 = 不可交易基准）
for N in HOR:
    T['sig_fwd%d' % N] = c(cl, 2 + N) / c(cl, 2) - 1

tg = pd.DataFrame(T)
tg.insert(0, 'event_id', B['event_id'].values)

# ── 可交易性（§21） ──
e_open, e_lim, e_ow, e_vo, e_amt, e_gap = (c(op, 3), c(lim, 3), c(ow, 3), c(vo, 3),
                                           c(amt, 3), c(M['gap'], 3))
trad = pd.DataFrame({'event_id': B['event_id'].values})
trad['entry_open'] = e_open
trad['entry_limit'] = e_lim
trad['entry_oneword'] = e_ow
trad['entry_amt'] = e_amt
trad['entry_gap'] = e_gap
trad['entry_to'] = c(to_rate, 3)
trad['can_buy'] = (np.isfinite(e_open) & (e_vo > 0) & np.isfinite(e_gap) & (e_gap <= 10)
                   & (e_ow < 0.5) & (np.isfinite(e_lim) & (e_open < e_lim * 0.999)))
trad['liq_ok'] = np.isfinite(e_amt) & (e_amt >= 2e7)

# ── 维度标签 ──
lab = B[['event_id', 'ts_code', 'name', 'industry', 'board', 'trade_date', 't0_date', 'yr', 'period',
         'clean', 'listed_days']].copy()
lab['log_mv'] = np.log10(np.maximum(c(M['to_mv'], 0), 1e-6))
mv_pct = lab.groupby('t0_date')['log_mv'].rank(pct=True)
lab['mv_grp'] = pd.cut(mv_pct, [0, 1 / 3, 2 / 3, 1.0], labels=['Small', 'Mid', 'Large'],
                       include_lowest=True).astype(object)
to_pct = feat.groupby(lab['t0_date'].values)['t0_turnover'].rank(pct=True)
lab['to_grp'] = pd.cut(to_pct, [0, 1 / 3, 2 / 3, 1.0], labels=['Low', 'Mid', 'High'],
                       include_lowest=True).astype(object)
# Regime（复用现有口径：_tr_build.build_regime）
import sqlite3
DB = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB, timeout=30.0)
ix = pd.read_sql_query("SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000001.SH' ORDER BY trade_date", conn)
conn.close()
ix['trade_date'] = ix['trade_date'].astype(str)
cc = ix['close'].astype(float)
ma20i, ma60i = cc.rolling(20).mean(), cc.rolling(60).mean()
r20 = cc / cc.shift(20) - 1
reg = np.full(len(ix), 'NORMAL', dtype=object)
reg[((cc > ma20i) & (ma20i > ma60i) & (r20 > 0.03)).values] = 'BULL'
reg[((cc < ma20i) & (cc < ma60i) & (r20 < -0.05)).values] = 'BEAR'
weak = ((cc < ma20i * 0.99) | (r20 < -0.02)).values
reg[weak & (reg != 'BEAR') & (reg != 'BULL')] = 'RANGE'
reg[:70] = 'NORMAL'
rmap = dict(zip(ix['trade_date'], reg))
lab['regime'] = lab['trade_date'].astype(str).map(rmap).fillna('NORMAL')
# 以 T+2 日的 Regime 为准（Entry 前可见）
d2 = M['dt'][:, 2]
lab['regime'] = [rmap.get(str(int(d)), 'NORMAL') if d > 0 else 'NORMAL' for d in d2]

out = lab.merge(feat, on='event_id', how='left').merge(tg, on='event_id', how='left') \
         .merge(trad, on='event_id', how='left')
out.to_parquet(os.path.join(OUT, 'pfb_feat.parquet'), index=False)
print('已写 out/pfb_feat.parquet', out.shape)
print('特征列', len(feat.columns) - 1)
ok = out['clean'] & (out['listed_days'] >= 60) & out['can_buy'] & out['fwd5'].notna()
print('可用样本: clean&n60&can_buy&fwd5 =', int(ok.sum()), '/', len(out))
print(out.loc[ok, 'period'].value_counts().to_string())
print(out.loc[ok, 'regime'].value_counts().to_string())
print('\n=== fwd 描述（可用样本）===')
print(out.loc[ok, ['fwd3', 'fwd5', 'fwd10', 'fwd3_e1', 'fwd5_e1']].describe().to_string())
print('\n=== T0 锚点参照（同样本）===')
print(out.loc[ok, ['t0_fwd3', 't0_fwd5', 't0_fwd10']].median().to_string())
