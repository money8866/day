# -*- coding: utf-8 -*-
"""闸门最后一项：sleeve 记账的组合可行性（净值 / 回撤 / 成本）

动机
  GATE-A 出现口径分歧：同一个 Top 十分位，按 cohort 等权只有 +0.78%/5日，
  按股票加权（pooled）是 +1.82%/5日，差 2.3 倍。谁对取决于资金分配方式，
  统计平均回答不了，只能用真实组合记账。

记账口径（预先写死，不择优）
  sleeve = 一个公告入场日 k1 的全部入选股票，构成一只等权篮子
  每个新 sleeve 投入 0.2 × 当期 NAV（最多 5 个 sleeve 并存 → 满仓；无信号则空仓）
  sleeve 在 k1 开盘买入、k1+5 收盘卖出（与面板 ret_E1_T5 完全同口径）
  成本：每笔 sleeve 一次性扣 30bp（§27 口径）；另给 60bp 压力情形
  空闲现金不计息（保守）；基准 = 沪深300 买入持有

变体
  TOP_rev_acc / TOP_np_qoq  Top 十分位
  TOP_*_mv80                再叠加实盘约束（总市值≥80亿 ∧ 非北交所）
  B1_ALL_EVENTS             不加筛选、买全部财报事件（同 sleeve 机制）
  B2_HS300                  沪深300 买入持有
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log

log = Log('_fs_trade_gate2.txt')

COST = 0.0030          # 每笔 sleeve 30bp
COST_HI = 0.0060       # 压力 60bp
SLOT = 1.0 / 6         # k1..k1+5 共 6 个交易日 → 6 个 sleeve 循环即满仓（不加杠杆）
HOLD = 5               # 持有交易日数（与 ret_E1_T5 同口径）
MV_MIN = 800000.0      # 万元 = 80 亿


def load_px():
    cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    px = pd.read_parquet(os.path.join(DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date', 'qfq_open', 'qfq_close'])
    px['k'] = px['trade_date'].astype(str).map(kmap)
    px = px[px['k'].notna()].copy()
    px['k'] = px['k'].astype(np.int32)
    px = px[(px['qfq_open'] > 0) & (px['qfq_close'] > 0)]
    codes, si = np.unique(px['ts_code'].values, return_inverse=True)
    C = np.full((len(codes), NCAL), np.nan, np.float32)
    O = np.full((len(codes), NCAL), np.nan, np.float32)
    C[si, px['k'].values] = px['qfq_close'].values.astype(np.float32)
    O[si, px['k'].values] = px['qfq_open'].values.astype(np.float32)
    cmap = pd.Series(np.arange(len(codes)), index=codes)
    lv = pd.read_parquet(os.path.join(DATA, 'index_level.parquet')).sort_values('trade_date')
    lvl = (pd.Series(pd.to_numeric(lv['idx_level'], errors='coerce').values,
                     index=lv['trade_date'].astype(str).values)
           .reindex(td).ffill().bfill().values)
    log('  价格矩阵 %s / 交易日 %d / 指数 %d' % (C.shape, NCAL, len(lvl)))
    return td, cmap, C, O, lvl


def top_decile(df, col, sub=None):
    d = df if sub is None else df[sub]
    d = d[np.isfinite(np.asarray(d[col], float))]
    kk = pd.factorize(np.asarray(d['k1']))[0]
    r = E.rank_pct_1d_group(np.asarray(d[col], float), kk)
    return d[np.isfinite(r) & (r >= 0.9)].copy()


def build_day_baskets(ev, cmap, C, O, NCAL, cost):
    """按「入场日」聚合成篮子：一个入场日 = 一个 sleeve

    返回 (ret_map{entry_day: ({day: basket_ret}, expire_day)}, n_events, n_days)
    """
    si = ev['ts_code'].map(cmap)
    keep = si.notna().values
    ev = ev[keep].reset_index(drop=True)
    si = si[keep].values.astype(np.int64)
    k1 = ev['k1'].values.astype(np.int64)
    keep = (k1 + HOLD) <= (NCAL - 1)
    si, k1 = si[keep], k1[keep]
    n = len(si)
    R = np.full((n, HOLD + 1), np.nan)
    for j in range(HOLD + 1):
        num = C[si, k1 + j]
        den = O[si, k1] if j == 0 else C[si, k1 + j - 1]
        with np.errstate(invalid='ignore', divide='ignore'):
            R[:, j] = num / den - 1.0
    R[:, 0] -= cost
    dfb = pd.DataFrame({'k1': np.repeat(k1, HOLD + 1),
                        'k': (k1[:, None] + np.arange(HOLD + 1)[None, :]).ravel(),
                        'r': R.ravel()}).dropna()
    gm = dfb.groupby(['k1', 'k'])['r'].mean().reset_index()
    ret_map = {}
    for a, g in gm.groupby('k1'):
        ret_map[int(a)] = ({int(b): float(c) for b, c in zip(g['k'], g['r'])},
                           int(a) + HOLD)
    return ret_map, n, len(ret_map)


def simulate_nav(ret_map, NCAL, slot=SLOT):
    """ret_map 每个 key 是一个 sleeve（= 一个入场日）；每个 sleeve 投 slot×当期NAV"""
    nav = np.full(NCAL, np.nan)
    nav[0] = 1.0
    live = []                       # (notional, expire_day, {day: basket_ret})
    for t in range(1, NCAL):
        pnl = 0.0
        for nt, exp, dm in live:
            v = dm.get(t)
            if v is not None:
                pnl += nt * v
        nav[t] = nav[t - 1] + pnl
        live = [x for x in live if t <= x[1]]
        if t in ret_map:
            dm, exp = ret_map[t]
            live.append((slot * nav[t], exp, dm))
    return nav


def metrics(nav, lvl, td, name):
    s = pd.Series(nav, index=td).dropna()
    r = s.pct_change().dropna()
    if len(r) < 100:
        return None
    yrs = len(r) / 242.0
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1
    sd = r.std(ddof=1)
    dd = float((s / s.cummax() - 1).min())
    bl = pd.Series(lvl, index=td)
    br = bl.pct_change().reindex(r.index).fillna(0.0)
    mb = (1 + br).prod() ** (1 / yrs) - 1
    ex = r - br
    return dict(name=name, cagr=float(cagr), vol=float(sd * np.sqrt(242)),
                sharpe=float(r.mean() / sd * np.sqrt(242)) if sd > 1e-12 else np.nan,
                mdd=dd, calmar=float(cagr / abs(dd)) if dd < -1e-9 else np.nan,
                bench_cagr=float(mb), excess_cagr=float((1 + r).prod() ** (1 / yrs) - 1 - mb),
                ann_over=float(ex.mean() * 242),
                ir=float(ex.mean() / ex.std(ddof=1) * np.sqrt(242))
                if ex.std(ddof=1) > 1e-12 else np.nan,
                n_days=len(r))


def main():
    log('=' * 70)
    log('S1 载入价格矩阵与面板')
    td, cmap, C, O, lvl = load_px()
    df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    df = E.add_derived(df)
    df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
    df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
    log('  事件 %d 行 / %d 入场日' % (len(df), df['k1'].nunique()))

    mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))
    variants = []
    for f in ('rev_acc', 'np_qoq'):
        variants.append(('TOP_%s' % f, top_decile(df, f)))
        variants.append(('TOP_%s_mv80' % f, top_decile(df, f, mv80)))
    variants.append(('B1_ALL_EVENTS', df.copy()))

    rows, navs, info = [], {}, []
    for nm, ev in variants:
        for c, tag in ((COST, ''), (COST_HI, '_c60')):
            ret_map, n, n_days = build_day_baskets(ev, cmap, C, O, len(td), c)
            nav = simulate_nav(ret_map, len(td))
            mt = metrics(nav, lvl, td, nm + tag)
            if mt:
                rows.append(mt)
            if tag == '':
                navs[nm] = nav
            info.append(dict(name=nm + tag, n_event=n, n_entry_days=n_days,
                             avg_size=round(n / max(n_days, 1), 2)))
            log('  %-20s 事件=%-6d 入场日=%-5d 均只数=%.1f' %
                (nm + tag, n, n_days, n / max(n_days, 1)))

    base = pd.Series(lvl, index=td)
    rows.append(metrics((base / base.iloc[0]).values, lvl, td, 'B2_HS300'))

    res = pd.DataFrame(rows)
    log('')
    log('S2 组合绩效（sleeve 记账，每日投 1/5 资金，空仓现金不计息）')
    log(res.to_string(index=False, float_format=lambda v: '%.4f' % v))
    log('')
    log(pd.DataFrame(info).to_string(index=False))

    log('')
    log('S3 分期（区间收益 / 区间年化 / 同期沪深300）')
    per = {'TRAIN': ('20180101', '20231231'), 'VALID': ('20240101', '20251231'),
           'OOS': ('20260101', '20260930')}
    pr = []
    for nm, nav in navs.items():
        s = pd.Series(nav, index=td).dropna()
        for pn, (a, b) in per.items():
            x = s[(s.index >= a) & (s.index <= b)]
            if len(x) < 30:
                continue
            b0 = base[(base.index >= a) & (base.index <= b)]
            yrs = len(x) / 242.0
            ret = x.iloc[-1] / x.iloc[0] - 1
            br = b0.iloc[-1] / b0.iloc[0] - 1
            pr.append(dict(name=nm, period=pn, n=len(x), ret=ret,
                           cagr=(1 + ret) ** (1 / yrs) - 1, bench=br,
                           excess=ret - br,
                           mdd=float((x / x.cummax() - 1).min())))
    log(pd.DataFrame(pr).to_string(index=False, float_format=lambda v: '%.4f' % v))

    log('')
    log('S4 TOP_rev_acc 月度收益（季节集中度）')
    s = pd.Series(navs['TOP_rev_acc'], index=pd.to_datetime(td)).dropna()
    mo = s.resample('ME').last().pct_change().dropna()
    ym = mo.index.strftime('%Y-%m')
    tab = pd.DataFrame({'ret': mo.values}, index=ym)
    piv = tab.assign(y=tab.index.str[:4], m=tab.index.str[5:]) \
        .pivot_table(index='y', columns='m', values='ret')
    log(piv.to_string(float_format=lambda v: '%+.3f' % v))

    pd.DataFrame(rows).to_csv(os.path.join(OUTD, 'trade_gate_nav.csv'),
                              index=False, encoding='utf-8-sig')
    log('')
    log('  已写出 out/trade_gate_nav.csv')
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
