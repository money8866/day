# -*- coding: utf-8 -*-
"""突破前横截面 Alpha · 事件级特征构建

只读 out/panel.parquet（唯一数据源），输出 out/brk_cand.parquet + out/brk_meta.json

设计约束（对应 Research Prompt V1.0）
  §3  压力位只用 EVENT_DAY 之前的数据：R_N = max(High[t-N .. t-1])  → rolling(N).max().shift(1)
  §4  候选池 = Universe(剔除ST/退市/北交所/上市未满60日) → 流动性过滤 → 接近压力位
  §15 Entry Anchor：Signal = T close，Entry = T+1 open；同时保留 T-close 锚点做漂移审计
  §13 市场环境按日绑定；§12 行业/市场相对强弱
  无未来函数：所有特征只用 t 及之前的信息；所有前瞻量只用 t+1 及之后的价格
"""
import os
import json
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
np.seterr(all='ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

RS_N = (10, 15, 20, 30, 40, 60)      # 压力位窗口候选空间（§25）
POOL_N = (10, 20, 40, 60)            # 主候选池使用的压力位窗口
POOL_Q = 0.10                        # 距压力位最近的前 10%（按日横截面分位）
LIQ_MIN = 5.0e4                      # amount 单位千元 → 5000 万元


def S(x):
    return pd.Series(np.asarray(x, dtype=np.float64))


print('== 读取 panel ==', flush=True)
cols = ['ts_code', 'trade_date', 'seq', 'board', 'open', 'high', 'low', 'close', 'pre_close',
        'pct_chg', 'vol', 'amount', 'a_open', 'a_high', 'a_low', 'a_close', 'is_limit_up']
p = pd.read_parquet(os.path.join(OUT, 'panel.parquet'), columns=cols)
print('  panel', p.shape, flush=True)

code = p['ts_code'].astype(str).values
board = p['board'].astype(str).values
td = p['trade_date'].values.astype(np.int32)
seq = p['seq'].values.astype(np.int64)
acl = p['a_close'].values.astype(np.float64)
aho = p['a_high'].values.astype(np.float64)
alo = p['a_low'].values.astype(np.float64)
aop = p['a_open'].values.astype(np.float64)
vol = p['vol'].values.astype(np.float64)
amt = p['amount'].values.astype(np.float64)
pre = p['pre_close'].values.astype(np.float64)
rcl = p['close'].values.astype(np.float64)
rpct = p['pct_chg'].values.astype(np.float64)
isl = p['is_limit_up'].values.astype(bool)
n = len(td)
del p
udates, di = np.unique(td, return_inverse=True)
di = di.astype(np.int64)
nd = udates.size
print('  交易日', nd, udates[0], udates[-1], flush=True)

# ── 静态信息（行业 / ST / 退市）
b = pd.read_parquet(os.path.join(OUT, 'basic.parquet'))
stset = set(b.loc[b['is_st_name'].fillna(False) | b['is_delisted'].fillna(False), 'ts_code'])
ind_map = dict(zip(b['ts_code'], b['industry']))
is_st = np.isin(code, list(stset))
indv = np.array([ind_map.get(x) for x in code], dtype=object)
okind = np.array([isinstance(x, str) and x != '' for x in indv])
inds = sorted(set(indv[okind]))
ind2i = {v: i for i, v in enumerate(inds)}
ii = np.full(n, -1, dtype=np.int32)
ii[okind] = np.array([ind2i[v] for v in indv[okind]], dtype=np.int32)
print('  行业数', len(inds), '  ST/退市股', len(stset), flush=True)

# ── Universe（§4）
uni = ((seq >= 60) & np.isfinite(acl) & (acl > 0) & np.isfinite(vol) & (vol > 0)
       & np.isfinite(amt) & (~is_st) & (board != 'BSE') & (amt >= LIQ_MIN))
print('  Universe', int(uni.sum()), ' 日均 %.0f' % (uni.sum() / nd), flush=True)

# ── 基础序列
Cs, Hs, Ls, Os = S(acl), S(aho), S(alo), S(aop)
Vs, Amts = S(vol), S(amt)
Cprev = Cs.shift(1)
Rng = Hs - Ls
dayret = Cs / Cprev - 1.0
absd = (Cs - Cprev).abs()
up = (dayret > 0).astype(float)
dn = (dayret < 0).astype(float)
TRs = pd.Series(np.fmax.reduce([(Hs - Ls).values, (Hs - Cprev).abs().values,
                                (Ls - Cprev).abs().values]))

# ── 压力位与距离（§3）
DIST = {}
for N in RS_N:
    R = Hs.rolling(N, min_periods=N).max().shift(1)
    DIST[N] = (Cs / R - 1.0).values
print('  压力位建立完成', flush=True)

# ── Breakout Candidate Pool（§4）
cand = np.zeros(n, dtype=bool)
u_idx = np.where(uni)[0]
for N in POOL_N:
    d = DIST[N][u_idx]
    f = np.isfinite(d)
    tmp = pd.DataFrame({'t': di[u_idx][f], 'd': d[f], 'i': u_idx[f]})
    tmp['r'] = tmp.groupby('t')['d'].rank(pct=True)
    cand[tmp.loc[tmp['r'] <= POOL_Q, 'i'].values] = True
ci = np.where(cand)[0]
nC = ci.size
print('  Candidate Pool', nC, ' 日均 %.0f  占U %.1f%%' % (nC / nd, 100.0 * nC / uni.sum()), flush=True)

FEAT = []


def emit(name, fam, full, lo=None, hi=None):
    a = np.asarray(full, dtype=np.float64)[ci]
    if lo is not None or hi is not None:
        a = np.clip(a, -np.inf if lo is None else lo, np.inf if hi is None else hi)
    FEAT.append((name, fam, a.astype(np.float32)))


def rmean(x, k, mp=None):
    return x.rolling(k, min_periods=k if mp is None else mp).mean()


# ══════════════════ §5 价格位置 ══════════════════
for N in RS_N:
    emit('Distance_to_R%d' % N, 'price_position', DIST[N], -1.0, 2.0)
for N in (20, 40, 60):
    lo = Ls.rolling(N, min_periods=N).min()
    hi = Hs.rolling(N, min_periods=N).max()
    emit('Close_Position_%d' % N, 'price_position', (Cs - lo) / (hi - lo), 0.0, 1.0)
lo250 = Ls.rolling(250, min_periods=250).min()
hi250 = Hs.rolling(250, min_periods=250).max()
emit('Pos_52W_high', 'price_position', Cs / hi250 - 1.0, -1.0, 2.0)
emit('Pos_250D_range', 'price_position', (Cs - lo250) / (hi250 - lo250), 0.0, 1.0)
emit('Dist_to_20D_Low', 'price_position', Cs / Ls.rolling(20, min_periods=20).min() - 1.0, -0.5, 3.0)

# ══════════════════ §6 趋势 ══════════════════
for k in (1, 3, 5, 10, 20, 60):
    emit('Ret_%d' % k, 'trend', Cs / Cs.shift(k) - 1.0, -1.0, 3.0)
AM = {}
for k in (5, 10, 20, 60):
    AM[k] = rmean(Cs, k)
    emit('Close_vs_MA%d' % k, 'trend', Cs / AM[k] - 1.0, -1.0, 1.5)
    emit('MA%d_slope' % k, 'trend', AM[k] / AM[k].shift(k) - 1.0, -1.0, 2.0)
emit('Trend_Consistency_20', 'trend', rmean(up, 20), 0.0, 1.0)
emit('Trend_Acceleration', 'trend', (Cs / Cs.shift(5) - 1.0) / 5.0 - (Cs / Cs.shift(20) - 1.0) / 20.0)
emit('MA_Align', 'trend', (AM[5] > AM[10]).astype(float) + (AM[10] > AM[20]).astype(float)
     + (AM[20] > AM[60]).astype(float), 0.0, 3.0)

# ══════════════════ §7 波动率 ══════════════════
TR = {}
for k in (5, 10, 20, 60):
    TR[k] = rmean(TRs, k)
for k in (5, 10, 20):
    emit('ATR%d_pct' % k, 'volatility', TR[k] / Cs, 0.0, 0.5)
emit('ATR_ratio_5_20', 'volatility', TR[5] / TR[20] - 1.0, -1.0, 3.0)
emit('ATR_ratio_10_60', 'volatility', TR[10] / TR[60] - 1.0, -1.0, 3.0)
STD = {}
for k in (5, 10, 20):
    STD[k] = dayret.rolling(k, min_periods=k).std()
    emit('STD%d' % k, 'volatility', STD[k], 0.0, 0.3)
emit('STD_ratio_5_20', 'volatility', STD[5] / STD[20] - 1.0, -1.0, 3.0)
emit('Range_20_pct', 'volatility', (Hs.rolling(20, min_periods=20).max()
                                    / Ls.rolling(20, min_periods=20).min() - 1.0), 0.0, 5.0)

# ══════════════════ §8 成交量 ══════════════════
VM = {}
for N in (5, 10, 20, 60):
    VM[N] = rmean(Vs, N)
    emit('Vol_over_MA%d' % N, 'volume', Vs / VM[N], 0.0, 10.0)
emit('VR1', 'volume', Vs / VM[20], 0.0, 10.0)
emit('VR3', 'volume', rmean(Vs, 3) / VM[20], 0.0, 10.0)
emit('VR5', 'volume', VM[5] / VM[20], 0.0, 10.0)
emit('VR10', 'volume', rmean(Vs, 10) / VM[20], 0.0, 10.0)
VSL = VM[5] / VM[20]
emit('Volume_Slope', 'volume', VSL - 1.0, -0.9, 5.0)
emit('Volume_Acceleration', 'volume', VSL / VSL.shift(3) - 1.0, -0.9, 5.0)
emit('Volume_Concentration', 'volume', Vs / Vs.rolling(20, min_periods=20).sum(), 0.0, 1.0)
CV10 = Vs.rolling(10, min_periods=10).std() / VM[10]
emit('Volume_Stability', 'volume', 1.0 / (1.0 + CV10), 0.0, 1.0)
emit('Volume_Shock', 'volume', Vs / Vs.rolling(60, min_periods=20).max(), 0.0, 5.0)
emit('PriceVolumeEff_5', 'volume', (Cs / Cs.shift(5) - 1.0) / VSL.replace(0, np.nan), -10.0, 10.0)
emit('PriceVolumeEff_20', 'volume', (Cs / Cs.shift(20) - 1.0)
     / (VM[20] / VM[60]).replace(0, np.nan), -10.0, 10.0)
emit('Vol_UpDown_Ratio_20', 'volume', (Vs * up).rolling(20, min_periods=20).sum()
     / np.maximum((Vs * dn).rolling(20, min_periods=20).sum(), 1e-9), 0.0, 50.0)

# ══════════════════ §9 换手率（daily_basic，2023-01 起）══════════════════
print('  读取 daily_basic ...', flush=True)
import sqlite3
conn = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db", timeout=120)
db = pd.read_sql_query("SELECT ts_code, trade_date, turnover_rate, volume_ratio, total_mv, "
                       "circ_mv FROM daily_basic_cache", conn)
conn.close()
key = pd.DataFrame({'ts_code': code, 'trade_date': td})
db['trade_date'] = db['trade_date'].astype(np.int32)
mg = key.merge(db, on=['ts_code', 'trade_date'], how='left')
print('  daily_basic 命中率 %.1f%%' % (100.0 * mg['turnover_rate'].notna().mean()), flush=True)
TO = S(mg['turnover_rate'].values)
T5 = rmean(TO, 5, 3)
T10 = rmean(TO, 10, 5)
T20 = rmean(TO, 20, 10)
for k, s in ((1, TO), (3, rmean(TO, 3, 2)), (5, T5), (10, T10), (20, T20)):
    emit('Turnover_%d' % k, 'turnover', s, 0.0, 60.0)
emit('Turnover_vs_MA20', 'turnover', TO / T20.replace(0, np.nan), 0.0, 10.0)
emit('Turnover_Slope', 'turnover', T5 / T20.replace(0, np.nan) - 1.0, -0.9, 5.0)
TACC = (T5 / T20.replace(0, np.nan))
emit('Turnover_Acceleration', 'turnover', TACC / TACC.shift(3) - 1.0, -0.9, 5.0)
CVT = rmean(TO, 10, 5).replace(0, np.nan)
emit('Turnover_Stability', 'turnover', 1.0 / (1.0 + TO.rolling(10, min_periods=5).std() / CVT), 0.0, 1.0)
emit('Turnover_Shock', 'turnover', TO / TO.rolling(60, min_periods=20).max(), 0.0, 5.0)
emit('DB_VolumeRatio', 'turnover', mg['volume_ratio'].values.astype(np.float64), 0.0, 20.0)
TMV = mg['total_mv'].values.astype(np.float64)
emit('LogMV', 'size', np.log(np.where(TMV > 0, TMV, np.nan)), 0.0, 20.0)
CMV = mg['circ_mv'].values.astype(np.float64)
emit('LogCircMV', 'size', np.log(np.where(CMV > 0, CMV, np.nan)), 0.0, 20.0)
emit('LogAmount', 'size', np.log(np.maximum(amt, 1.0)), 0.0, 25.0)
emit('Turnover_per_MV', 'turnover', TO / np.maximum(np.log(np.where(TMV > 0, TMV, np.nan)), 1.0), 0.0, 30.0)
del mg, db, key

# ══════════════════ §10 K 线结构 ══════════════════
R2 = Rng.where(Rng > 0)
Body = (Cs - Os) / R2
UpSh = (Hs - np.maximum(Os, Cs)) / R2
LoSh = (np.minimum(Os, Cs) - Ls) / R2
emit('K_Body', 'candle', Body, -1.0, 1.0)
emit('K_UpperShadow', 'candle', UpSh, 0.0, 1.0)
emit('K_LowerShadow', 'candle', LoSh, 0.0, 1.0)
emit('K_ClosePos', 'candle', (Cs - Ls) / R2, 0.0, 1.0)
Gap = Os / Cprev - 1.0
emit('K_Gap', 'candle', Gap, -0.3, 0.3)
emit('K_GapFreq20', 'candle', rmean(Gap.abs() > 0.01, 20), 0.0, 1.0)
emit('K_PosCandleRatio20', 'candle', rmean((Cs > Os).astype(float), 20), 0.0, 1.0)
emit('K_NegCandleRatio20', 'candle', rmean((Cs < Os).astype(float), 20), 0.0, 1.0)
emit('K_LargeBodyFreq20', 'candle', rmean(((Cs - Os).abs() / Cprev > 0.05), 20), 0.0, 1.0)
emit('K_LargeUpShadowFreq20', 'candle', rmean(((Hs - np.maximum(Os, Cs)) / Cprev > 0.03), 20), 0.0, 1.0)
emit('K_LargeLoShadowFreq20', 'candle', rmean(((np.minimum(Os, Cs) - Ls) / Cprev > 0.03), 20), 0.0, 1.0)
emit('K_BodyAbs20', 'candle', rmean(((Cs - Os).abs() / Cprev), 20), 0.0, 0.3)

# ══════════════════ §11 价格效率 ══════════════════
EFF = {}
for k in (5, 10, 20):
    EFF[k] = (Cs - Cs.shift(k)).abs() / absd.rolling(k, min_periods=k).sum().replace(0, np.nan)
    emit('Efficiency_%d' % k, 'efficiency', EFF[k], 0.0, 1.0)
AMA20 = AM[20]
ama_absd = (AMA20 - AMA20.shift(1)).abs()
emit('TrendEfficiency_20', 'efficiency',
     (AMA20 - AMA20.shift(20)).abs() / ama_absd.rolling(20, min_periods=20).sum().replace(0, np.nan), 0.0, 1.0)
emit('PriceNoise_20', 'efficiency', 1.0 / EFF[20].replace(0, np.nan) - 1.0, 0.0, 100.0)
emit('Efficiency_5_over_20', 'efficiency', EFF[5] / EFF[20].replace(0, np.nan), 0.0, 10.0)
emit('DirectionalConsistency_20', 'efficiency', 2.0 * rmean(up, 20) - 1.0, -1.0, 1.0)

# ══════════════════ §12 相对强弱（市场 / 行业）══════════════════
mkt = pd.Series(dayret.values).where(uni).groupby(di).mean().reindex(range(nd))
mkcum = (1.0 + mkt.fillna(0.0)).cumprod()
sel = uni & (ii >= 0)
sub = pd.DataFrame({'i': ii[sel], 't': di[sel], 'r': dayret.values[sel]})
gm = sub.groupby(['i', 't'])['r'].mean().unstack('t').reindex(index=range(len(inds)), columns=range(nd))
gm = gm.fillna(0.0)
gm = gm / gm.sum(axis=0).replace(0, np.nan)     # 归一（剔除空行业）
gcum = (1.0 + gm.fillna(0.0)).cumprod(axis=1)
del sub
for k in (5, 10, 20):
    mr = (mkcum / mkcum.shift(k) - 1.0).values
    irm = (gcum / gcum.shift(k, axis=1) - 1.0).values
    emit('Market_Return_%d' % k, 'relative_strength', mr[di], -1.0, 3.0)
    ir_row = np.full(n, np.nan)
    ok = ii >= 0
    ir_row[ok] = irm[ii[ok], di[ok]]
    emit('Industry_Return_%d' % k, 'relative_strength', ir_row, -1.0, 3.0)
    sr = (Cs / Cs.shift(k) - 1.0).values
    emit('RelativeStrength_%d' % k, 'relative_strength', sr - mr[di], -2.0, 2.0)
    emit('RS_Ratio_%d' % k, 'relative_strength', (1.0 + sr) / (1.0 + mr[di]) - 1.0, -2.0, 5.0)
    emit('Stock_vs_Industry_%d' % k, 'relative_strength', sr - ir_row, -2.0, 2.0)
    emit('Stock_vs_Market_diff_%d' % k, 'relative_strength', sr - mr[di], -2.0, 2.0)

# ══════════════════ 前瞻收益 / 锚点（§14 §15）══════════════════
print('  前瞻收益 ...', flush=True)
entry = S(aop).shift(-1).values
gap_entry = entry / acl - 1.0
sqf = {k: S(seq).shift(-k).values for k in range(1, 11)}
dif = {k: S(di).shift(-k).values for k in range(1, 11)}
LAB = {}
mx = np.full(n, np.nan)
mn = np.full(n, np.nan)
vall = np.ones(n, dtype=bool)
for j in range(1, 11):
    v = (sqf[j] == seq + j) & (dif[j] == di + j)
    vall &= v
    hj = np.where(v, S(aho).shift(-j).values, np.nan)
    lj = np.where(v, S(alo).shift(-j).values, np.nan)
    mx = hj if j == 1 else np.fmax(mx, hj)
    mn = lj if j == 1 else np.fmin(mn, lj)
    if j in (1, 3, 5, 10):
        ex = S(acl).shift(-j).values
        e = np.where(v, ex / entry - 1.0, np.nan)
        e0 = np.where(v, ex / acl - 1.0, np.nan)
        LAB['fwd_%d' % j] = e
        LAB['fwdT_%d' % j] = e0
        LAB['mfe_%d' % j] = np.where(vall, mx / entry - 1.0, np.nan)
        LAB['mae_%d' % j] = np.where(vall, mn / entry - 1.0, np.nan)
LAB['gap_entry'] = gap_entry

# ══════════════════ §13 市场环境（按日）══════════════════
r_main = np.where(board == 'MAIN', 0.10, np.where(board == 'BSE', 0.30, 0.20))
lim_dn = np.round(pre * (1 - r_main), 2)
vl = seq >= 10
is_ld = vl & (np.abs(rcl - lim_dn) < 0.005) & (rpct <= -(r_main * 100 - 0.6))
ENV = {}
ENV['LimitUpCount'] = pd.Series(isl.astype(float)).groupby(di).sum().reindex(range(nd)).fillna(0).values
ENV['LimitDownCount'] = pd.Series(is_ld.astype(float)).groupby(di).sum().reindex(range(nd)).fillna(0).values
ENV['MarketBreadth'] = pd.Series((Cs > AM[20]).values.astype(float)).where(uni).groupby(
    di).mean().reindex(range(nd)).values
ENV['MarketAmount'] = pd.Series(amt).where(uni).groupby(di).sum().reindex(range(nd)).values
ENV['MarketVolatility'] = pd.Series(dayret.values).where(uni).groupby(
    di).std().reindex(range(nd)).values
ENV['MarketReturn_1'] = mkt.values
ma_amt = pd.Series(ENV['MarketAmount']).rolling(20, min_periods=20).mean().values
ENV['MarketAmountRatio'] = ENV['MarketAmount'] / ma_amt
ENV['Breadth_MA5'] = pd.Series(ENV['MarketBreadth']).rolling(5, min_periods=5).mean().values

ix = pd.read_parquet(os.path.join(OUT, 'index.parquet'))
h300 = ix[ix['ts_code'] == '000300.SH'].copy()
h300['dd'] = h300['trade_date'].astype(np.int64)   # index.parquet 的 trade_date 是字符串, 必须转 int
h300 = h300.sort_values('dd')
dmap = {d: i for i, d in enumerate(udates)}
hidx = np.full(nd, np.nan)
for d, c in zip(h300['dd'].values, h300['close'].values.astype(np.float64)):
    if d in dmap:
        hidx[dmap[d]] = c
assert np.isfinite(hidx).sum() > nd * 0.9, 'HS300 对齐失败 %d/%d' % (np.isfinite(hidx).sum(), nd)
hs = pd.Series(hidx)
ENV['HS300_Return_1'] = (hs / hs.shift(1) - 1.0).values
ENV['HS300_Return_5'] = (hs / hs.shift(5) - 1.0).values
ENV['HS300_Return_20'] = (hs / hs.shift(20) - 1.0).values
h20 = hs.rolling(20, min_periods=20).mean()
h60 = hs.rolling(60, min_periods=60).mean()
ENV['HS300_vs_MA20'] = (hs / h20 - 1.0).values
ENV['HS300_vs_MA60'] = (hs / h60 - 1.0).values
reg = np.full(nd, 'NA', dtype=object)
b_ = np.asarray((hs.values > h20.values) & (hs.values > h60.values))
s_ = np.asarray((hs.values < h20.values) & (hs.values < h60.values))
reg[np.isfinite(hs.values)] = 'NORMAL'
reg[b_] = 'BULL'
reg[s_] = 'BEAR'
ENV['Regime'] = reg

# ══════════════════ 组装 ══════════════════
print('  组装 %d 特征 ...' % len(FEAT), flush=True)
out = pd.DataFrame({'ts_code': code[ci], 'trade_date': td[ci], 'di': di[ci],
                    'industry': indv[ci], 'board': board[ci]})
out['amount'] = amt[ci]
out['close_t'] = acl[ci]
out['entry_open'] = entry[ci]
for k, v in LAB.items():
    out[k] = v[ci]
for k, v in ENV.items():
    out[k] = np.asarray(v, dtype=object)[di[ci]] if v.dtype == object else v[di[ci]]
for name, fam, a in FEAT:
    out[name] = a

meta = {'pool': {'N': list(POOL_N), 'q': POOL_Q, 'liq_min': LIQ_MIN,
                 'n_rows': int(nC), 'n_dates': int(nd), 'n_stocks': int(out['ts_code'].nunique()),
                 'rows_per_day': float(nC / nd), 'universe_rows': int(uni.sum()),
                 'pool_frac_of_universe': float(nC / uni.sum())},
        'window': {'d0': int(udates[0]), 'd1': int(udates[-1])},
        'features': [{'name': nm, 'family': fm} for nm, fm, _ in FEAT],
        'n_features': len(FEAT)}
out.to_parquet(os.path.join(OUT, 'brk_cand.parquet'), index=False, compression='zstd')
with open(os.path.join(OUT, 'brk_meta.json'), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)
print('已写 brk_cand.parquet', out.shape, flush=True)

# ── 诊断
print('\n=== 家族分布 ===')
fams = {}
for nm, fm, _ in FEAT:
    fams[fm] = fams.get(fm, 0) + 1
for k, v in sorted(fams.items(), key=lambda x: -x[1]):
    print('  %-18s %d' % (k, v))
print('\n=== 缺失率 top15 ===')
mr = [(nm, float(np.mean(~np.isfinite(a)))) for nm, fm, a in FEAT]
for nm, r in sorted(mr, key=lambda x: -x[1])[:15]:
    print('  %-26s %.3f' % (nm, r))
print('\n=== 前瞻收益（原始，未扣成本）===')
for H in (1, 3, 5, 10):
    v = out['fwd_%d' % H]
    print('  T+%-2d n=%d mean=%.4f%% med=%.4f%% win=%.1f%%'
          % (H, v.notna().sum(), 100 * v.mean(), 100 * v.median(), 100 * (v > 0).mean()))
print('\n=== Regime 分布(候选行)===')
print(out['Regime'].value_counts().to_string())
