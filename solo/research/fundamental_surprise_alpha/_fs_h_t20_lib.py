# -*- coding: utf-8 -*-
"""H-T20 共享引擎：ARCHIVE 信号完全冻结 + 多持有期记账

冻结（与 _fs_trade_gate2.py / _fs_trade_gate3.py 逐行一致）
  信号池 : data/panel.parquet（可交易/非ST/非一字板/上市≥60日 已在 fs_build_panel 完成）
  排序   : RANK-POST = 先在 cohort(k1) 内排 Top 十分位，再剔 (总市值<80亿 ∨ .BJ)
  因子   : rev_acc（主）/ np_qoq（辅）—— 两者均为 ARCHIVE §3 表中 18/18 门通过
  Entry  : E1 = k1 开盘（ARCHIVE 声明的锚点；所有 horizon 共用同一天同一价）
  Exit   : k1 + H 收盘
  Regime : ARCHIVE §30 原定义（指数 200 日均线 + 60 日动量）
  基准   : 沪深300 买入持有（data/index_level.parquet）
  成本   : 往返 30bp（单边 15bp），按真实换手计

唯一改动变量：H ∈ {3, 5, 10, 15, 20}（T+5 = ARCHIVE 原基准）

关于 gate2/gate3 记账法的两处实现事实（本引擎如实处理并单独报告）
  1) gate2 `simulate_nav` 在当日 pnl 结算之后才 append 新 sleeve，
     因此 ret_map 中 key=k1 的那一天（= 入场日开盘→收盘收益）**从未被应用**。
  2) ARCHIVE 的 30bp 成本恰好只加在 $R[:,0]$（即上面那一天）上，
     因此成本也**从未被应用**。
  合并后果：ARCHIVE 发布的组合层 NAV = 「E2(k1 收盘)入场 + 零成本」。
  本引擎按 ARCHIVE 声明的 Entry（E1 = k1 开盘）正确实现，并把
  「gate2 逐字复现」单独输出，用于 §G1-G3 冻结校验，不参与 horizon 比较。
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fs_engine as E
from fs_common import DATA, OUTD
from _fs_trade_gate2 import (load_px, top_decile, build_day_baskets, simulate_nav,
                             metrics as g2_metrics, COST, MV_MIN,
                             HOLD as ARCHIVE_HOLD, SLOT as ARCHIVE_SLOT)

HORIZONS = (3, 5, 10, 15, 20)
LIFECYCLE = (1, 3, 5, 10, 15, 20)
COST_RT = COST                     # 0.0030 往返（与 ARCHIVE 一致）
COST_SIDE = COST_RT / 2.0          # 0.0015 单边
FACTORS = ('rev_acc', 'np_qoq')
PRIMARY = 'rev_acc'
BASE_H = 5                         # ARCHIVE 原持有期
MV_MIN_ = MV_MIN                   # 800000 万元 = 80 亿
PHASES = (('TRAIN', '20180101', '20231231'),
          ('VALID', '20240101', '20251231'),
          ('OOS', '20260101', '20260930'))
WF = [('W1', '20180101', '20201231', '2021'),
      ('W2', '20190101', '20211231', '2022'),
      ('W3', '20200101', '20221231', '2023'),
      ('W4', '20210101', '20231231', '2024'),
      ('W5', '20220101', '20241231', '2025'),
      ('W6', '20230101', '20251231', '2026')]


# ---------------------------------------------------------------- 环境
def load_env(verbose=True):
    """载入价格矩阵 + 冻结信号池（信号池截断口径 = ARCHIVE 原 HOLD=5）"""
    td, cmap, C, O, lvl = load_px()
    NCAL = len(td)
    df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    df = E.add_derived(df)
    df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
    df['ann_date'] = df['ann_date'].astype(str)
    # ARCHIVE 原截断：只保留 k1+5 落在样本内的事件（冻结信号池，不随 H 变化）
    df = df[df['k1'] + ARCHIVE_HOLD <= (NCAL - 1)].reset_index(drop=True)
    mv80 = (df['total_mv'] >= MV_MIN_) & (~df['ts_code'].str.endswith('.BJ'))
    env = dict(td=td, cmap=cmap, C=C, O=O, lvl=lvl, NCAL=NCAL, df=df, mv80=mv80)
    if verbose:
        print('  [env] 交易日 %d / 账本事件 %d / mv80 事件 %d'
              % (NCAL, len(df), int(mv80.sum())), flush=True)
    return env


def frozen_select(df, mv80, factor):
    """RANK-POST：cohort 内全样本 Top 十分位 → 再剔 (mv<80亿 ∨ .BJ)"""
    A = top_decile(df, factor)
    return A[mv80.reindex(A.index).fillna(False).values].copy()


def regime_map(env):
    """ARCHIVE §30 原定义：>MA200 且 60 日动量>0 = BULL；<MA200 且 <0 = BEAR；其余 NORMAL"""
    s = pd.Series(env['lvl'], index=env['td'])
    ma200 = s.rolling(200, min_periods=60).mean()
    r60 = s / s.shift(60) - 1.0
    reg = pd.Series('NORMAL', index=s.index)
    reg[(s > ma200) & (r60 > 0)] = 'BULL'
    reg[(s < ma200) & (r60 < 0)] = 'BEAR'
    return reg


# ---------------------------------------------------------------- 单事件层
def ret_arrays(ev, env, H):
    """E1 口径：k1 开盘买入 → k1+H 收盘卖出。

    返回与 ev 等长的 (ret, mfe, mae, ok)。
    MFE/MAE 为持有窗口内 close 相对入场开盘价的最大 / 最小偏离
    （与本研究「以收盘价指标为主」的口径一致）。
    """
    C, O, NCAL = env['C'], env['O'], env['NCAL']
    si_f = np.asarray(ev['ts_code'].map(env['cmap']), dtype=float)
    k1 = np.asarray(ev['k1'], dtype=np.int64)
    ok = np.isfinite(si_f) & ((k1 + H) <= (NCAL - 1))
    n = len(ev)
    ret = np.full(n, np.nan)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return ret, mfe, mae, ok
    si = si_f[idx].astype(np.int64)
    kk = k1[idx]
    o = O[si, kk]
    with np.errstate(invalid='ignore', divide='ignore'):
        ret[idx] = C[si, kk + H] / o - 1.0
    hi = np.full(len(idx), -np.inf)
    lo = np.full(len(idx), np.inf)
    for j in range(1, H + 1):
        with np.errstate(invalid='ignore', divide='ignore'):
            p = C[si, kk + j] / o - 1.0
        hi = np.fmax(hi, p)
        lo = np.fmin(lo, p)
    hi[~np.isfinite(hi)] = np.nan
    lo[~np.isfinite(lo)] = np.nan
    mfe[idx] = hi
    mae[idx] = lo
    ret[~np.isfinite(ret)] = np.nan
    return ret, mfe, mae, ok


def cohort_alpha(ev_sel, env, H):
    """逐入场日 cohort alpha = 篮子 H 日收益 − 同日全事件篮子 H 日收益（同日对冲，无成本）"""
    r_all, _, _, _ = ret_arrays(env['df'], env, H)
    r_sel, _, _, _ = ret_arrays(ev_sel, env, H)
    g_all = pd.Series(r_all, index=np.asarray(env['df']['k1'])).groupby(level=0).mean()
    g_sel = pd.Series(r_sel, index=np.asarray(ev_sel['k1'])).groupby(level=0).mean()
    return (g_sel - g_all.reindex(g_sel.index)).dropna()


def ev_alpha_vs_index(ev_sel, env, H):
    """逐入场日：篮子 H 日收益 − 指数同期收益（同一 E1 锚点到 k1+H 收盘）"""
    r_sel, _, _, _ = ret_arrays(ev_sel, env, H)
    C, O, NCAL = env['C'], env['O'], env['NCAL']
    lvl = env['lvl']
    k1 = np.asarray(ev_sel['k1'], dtype=np.int64)
    si_f = np.asarray(ev_sel['ts_code'].map(env['cmap']), dtype=float)
    ok = np.isfinite(si_f) & ((k1 + H) <= (NCAL - 1))
    ka = np.clip(k1, 0, NCAL - 1)
    kb = np.clip(k1 + H, 0, NCAL - 1)
    # 指数从 k1 开盘不可得 → 用 k0(=k1-1) 收盘 → k1+H 收盘 的 ARCHIVE 原口径（ex_E1_T*）
    k0 = np.clip(k1 - 1, 0, NCAL - 1)
    idxr = lvl[kb] / lvl[k0] - 1.0
    d = r_sel - idxr
    d[~ok] = np.nan
    g = pd.Series(d, index=k1).groupby(level=0).mean()
    return g.dropna()


# ---------------------------------------------------------------- 篮子层
def build_baskets(ev, env, H):
    """按入场日聚合成等权篮子（毛收益，成本由组合层按名义额计）

    返回 {k1: (dm{daily gross ret}, expire_day, size)}
    """
    C, O, NCAL = env['C'], env['O'], env['NCAL']
    si_f = np.asarray(ev['ts_code'].map(env['cmap']), dtype=float)
    k1 = np.asarray(ev['k1'], dtype=np.int64)
    ok = np.isfinite(si_f) & ((k1 + H) <= (NCAL - 1))
    si = si_f[ok].astype(np.int64)
    k1 = k1[ok]
    n = len(si)
    if n == 0:
        return {}, 0, 0
    R = np.full((n, H + 1), np.nan)
    for j in range(H + 1):
        num = C[si, k1 + j]
        den = O[si, k1] if j == 0 else C[si, k1 + j - 1]
        with np.errstate(invalid='ignore', divide='ignore'):
            R[:, j] = num / den - 1.0
    dfb = pd.DataFrame({'k1': np.repeat(k1, H + 1),
                        'k': (k1[:, None] + np.arange(H + 1)[None, :]).ravel(),
                        'r': R.ravel()}).dropna()
    gm = dfb.groupby(['k1', 'k'])['r'].mean()
    cnt = pd.Series(k1).value_counts()
    ret_map = {}
    for a, g in gm.groupby(level=0):
        a = int(a)
        ret_map[a] = ({int(b): float(c) for b, c in
                       zip(g.index.get_level_values(1), g.values)},
                      a + H, int(cnt.get(a, 0)))
    return ret_map, n, len(ret_map)


# ---------------------------------------------------------------- 组合层
# 记账时序（对 gate2 缺陷的修正，见文件头）
#   日 t 开盘：新篮子按 E1 口径建仓（k1 开盘），并计入当日的 dm[t]
#   日 t 收盘：到期篮子（expire = k1+H）平仓；Portfolio A 再平衡至等权 1/m
#   日 t 收益：新篮子 dm[t] = 开盘→收盘；存量篮子 dm[t] = 上一收盘→本收盘
#   现金不计息；不允许隐性杠杆
def sim_fixed(ret_map, NCAL, slot, cost_side, cap=True):
    """Portfolio B：固定仓位。每个入场篮子投入 slot × 上日 NAV，持有期内不调整。"""
    nav = np.full(NCAL, np.nan)
    nav[0] = 1.0
    live = []                      # [value, expire, dm, size]
    expo = np.zeros(NCAL)
    npos = np.zeros(NCAL)
    nbask = np.zeros(NCAL)
    traded = np.zeros(NCAL)
    buyd = np.zeros(NCAL)
    selld = np.zeros(NCAL)
    costd = np.zeros(NCAL)
    buy_nt = sell_nt = cost_tot = 0.0
    for t in range(1, NCAL):
        navp = nav[t - 1]
        # 1) 到期平仓（持仓在 k1+H 收盘卖出 → t = k1+H+1 起移除）
        keep, sell = [], 0.0
        for x in live:
            if x[1] < t:
                sell += x[0]
            else:
                keep.append(x)
        live = keep
        c_sell = sell * cost_side
        sell_nt += sell
        # 2) 当日新入场（k1 = t 开盘买入）
        nt = 0.0
        c_buy = 0.0
        if t in ret_map:
            dm, exp, size = ret_map[t]
            nt = slot * navp
            if cap:
                nt = min(nt, max(0.0, navp - sum(x[0] for x in live)))
            c_buy = nt * cost_side
            buy_nt += nt
            live.append([nt, exp, dm, size])
        cost_tot += c_buy + c_sell
        # 3) 当日收益（含新篮子的入场日开盘→收盘）
        pnl = 0.0
        for x in live:
            v = x[2].get(t)
            if v is not None:
                pnl += x[0] * v
                x[0] *= (1.0 + v)
        navt = navp + pnl - c_buy - c_sell
        nav[t] = navt
        tv = sum(x[0] for x in live)
        expo[t] = tv / navt if navt > 0 else 0.0
        npos[t] = sum(x[3] for x in live)
        nbask[t] = len(live)
        traded[t] = nt + sell
        buyd[t] = nt
        selld[t] = sell
        costd[t] = c_buy + c_sell
    return nav, expo, npos, nbask, dict(buy_nt=buy_nt, sell_nt=sell_nt, cost=cost_tot,
                                        traded=traded, buyd=buyd, selld=selld,
                                        costd=costd)


def sim_equal(ret_map, NCAL, cost_side):
    """Portfolio A：等权满仓。存活篮子各占 1/m，日度再平衡（含新开与到期），空仓则全现金。"""
    nav = np.full(NCAL, np.nan)
    nav[0] = 1.0
    live = []                      # [value, expire, dm, size]
    expo = np.zeros(NCAL)
    npos = np.zeros(NCAL)
    nbask = np.zeros(NCAL)
    traded = np.zeros(NCAL)
    buyd = np.zeros(NCAL)
    selld = np.zeros(NCAL)
    costd = np.zeros(NCAL)
    buy_nt = sell_nt = cost_tot = 0.0
    for t in range(1, NCAL):
        navp = nav[t - 1]
        live = [x for x in live if x[1] >= t]
        if t in ret_map:
            dm, exp, size = ret_map[t]
            live.append([0.0, exp, dm, size])
        if not live:
            nav[t] = navp
            continue
        m = len(live)
        tgt = navp / m
        buys = sum(max(tgt - x[0], 0.0) for x in live)
        sells = sum(max(x[0] - tgt, 0.0) for x in live)
        c = (buys + sells) * cost_side
        buy_nt += buys
        sell_nt += sells
        cost_tot += c
        pnl = 0.0
        for x in live:
            x[0] = tgt
            v = x[2].get(t)
            if v is not None:
                g = tgt * v
                pnl += g
                x[0] += g
        navt = navp + pnl - c
        nav[t] = navt
        tv = sum(x[0] for x in live)
        expo[t] = tv / navt if navt > 0 else 0.0
        npos[t] = sum(x[3] for x in live)
        nbask[t] = m
        traded[t] = buys + sells
        buyd[t] = buys
        selld[t] = sells
        costd[t] = c
    return nav, expo, npos, nbask, dict(buy_nt=buy_nt, sell_nt=sell_nt, cost=cost_tot,
                                        traded=traded, buyd=buyd, selld=selld,
                                        costd=costd)


def expo_adj_bench(env, expo):
    """Exposure-adjusted benchmark：bench_t = bench_{t-1} × (1 + expo_{t-1} × 指数收益_t)"""
    lvl, td = env['lvl'], env['td']
    b = np.full(len(td), np.nan)
    b[0] = 1.0
    for t in range(1, len(td)):
        e = expo[t - 1] if expo[t - 1] > 0 else 0.0
        ir = lvl[t] / lvl[t - 1] - 1.0 if lvl[t - 1] > 0 else 0.0
        b[t] = b[t - 1] * (1.0 + e * ir)
    return b


# ---------------------------------------------------------------- 绩效
def _perf(nav, env, a=None, b=None, name='', expo=None, trade=None):
    td, lvl = env['td'], env['lvl']
    s = pd.Series(nav, index=td).dropna()
    bl = pd.Series(lvl, index=td)
    if a is not None:
        m = (s.index >= a) & (s.index <= b)
        s = s[m]
        bl = bl[m]
    if len(s) < 30:
        return None
    r = s.pct_change().dropna()
    yrs = len(r) / 242.0
    ret_tot = float(s.iloc[-1] / s.iloc[0] - 1.0)
    cagr = float((1 + ret_tot) ** (1 / yrs) - 1) if ret_tot > -1 else np.nan
    sd = r.std(ddof=1)
    dn = r[r < 0].std(ddof=1)
    mdd = float((s / s.cummax() - 1).min())
    br = bl.pct_change().reindex(r.index).fillna(0.0)
    bcagr = float((1 + br).prod() ** (1 / yrs) - 1)
    ex = r - br
    esd = ex.std(ddof=1)
    ds = pd.Series(s.values, index=pd.to_datetime(s.index, format='%Y%m%d'))
    db = pd.Series(bl.reindex(s.index).values, index=ds.index)
    qr = ds.resample('QE').last().pct_change().dropna()
    bqr = db.resample('QE').last().pct_change().reindex(qr.index)
    mr = ds.resample('ME').last().pct_change().dropna()
    bmr = db.resample('ME').last().pct_change().reindex(mr.index)
    out = dict(name=name, n_days=len(r), years=round(yrs, 3),
               ret_total=ret_tot, ann_ret=cagr,
               ann_vol=float(sd * np.sqrt(242)),
               sharpe=float(r.mean() / sd * np.sqrt(242)) if sd > 1e-12 else np.nan,
               sortino=float(r.mean() / dn * np.sqrt(242)) if dn > 1e-12 else np.nan,
               mdd=mdd,
               calmar=float(cagr / abs(mdd)) if mdd < -1e-9 else np.nan,
               bench_ret=bcagr,
               bench_total=float(bl.iloc[-1] / bl.iloc[0] - 1.0),
               excess_ret=float((1 + r).prod() ** (1 / yrs) - 1 - bcagr),
               excess_simple=float((ret_tot) - (bl.iloc[-1] / bl.iloc[0] - 1)),
               ir=float(ex.mean() / esd * np.sqrt(242)) if esd > 1e-12 else np.nan,
               te=float(esd * np.sqrt(242)),
               win_q=float((mr > 0).mean()) if len(mr) else np.nan,
               win_q_ex=float((mr > bmr).mean()) if len(mr) else np.nan)
    if expo is not None:
        e = pd.Series(expo, index=td)
        if a is not None:
            e = e[(e.index >= a) & (e.index <= b)]
        e = e.reindex(r.index).fillna(0.0)
        out['avg_expo'] = float(e.mean())
        out['med_expo'] = float(e.median())
        out['p90_expo'] = float(e.quantile(0.90))
        out['max_expo'] = float(e.max())
        out['pct_invested'] = float((e > 1e-9).mean())
        out['cash_ratio'] = float(1.0 - e.mean())
    if trade is not None:
        tn = trade['buy_nt'] + trade['sell_nt']
        avg_nav = float(s.mean())
        out['turnover_ann'] = float(tn / avg_nav / yrs) if avg_nav > 0 else np.nan
        out['trade_cost'] = float(trade['cost'])
        out['cost_drag_ann'] = float(trade['cost'] / avg_nav / yrs) if avg_nav > 0 else np.nan
    return out
