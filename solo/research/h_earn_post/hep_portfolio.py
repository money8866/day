# -*- coding: utf-8 -*-
"""hep_portfolio: H-EARN-POST §26–§31 组合层模拟（Exposure / 成本网格 / 组合 vs 基准）

隔离声明（§1）：本模块只读 data/hep_events.parquet 与市场级 Tushare cache
（calendar / price_panel / index_panel），不 import 亦不调用 ARCHIVE / HVT / DLG /
F120 / Theme Quant / te_buy_pool / 突破策略 / 天量策略 / 首板策略 的任何代码或结果。

组合模型（预注册）
  §27 固定持有：信号在 E+1 开盘建仓，持有 T 个交易日后于 E+1+T 收盘强制平仓；
      无主观止盈 / 止损 / 加仓 / 提前离场；E20/E25/E30 与 T+5/10/20/60 全部预先列出，
      不允许事后择优。
  仓位：等权、每日再平衡至 1/n_t（n_t = 当日持仓数）；有持仓即满仓，无持仓为现金（收益 0）。
  收益口径与 hep_build 的 ex_T 严格一致：Entry 日个股 Cq[b]/Oq[b]-1，其后 Cq[t]/Cq[t-1]-1；
        基准在 Entry 日贡献 0；故 sum_t (r_t - bench_t) == ex_T（组合层与横截面层可比）。
  换手：trade_t = survivors*|1/n_t - 1/n_{t-1}| + entrants/n_t + exits/n_{t-1}
        turnover_t(单边) = trade_t / 2；成本 = c * trade_t（按实际成交名义额，双向）。
  成本：0/10/20/30/50 bp 预注册网格（§31）。成本与毛收益线性可分离，故毛序列只构造一次，
        各档成本解析扣除（与逐档重算等价）。

基准（§29）
  Primary = 沪深300（000300.SH）买入持有，与组合同一日历区间（含现金日）。
  中证1000 / 中证2000 的指数行情**不在本地 cache** -> 以「全 A 等权日收益」作中小盘代理
  （PROXY，非官方指数），见 hep_portfolio 日志与报告披露。

组合变体
  LONG_EW（§27 预注册口径）：仅做多入选组，与 100% 沪深300 比较。
  LS_DN（**诊断用**，非预注册实盘口径）：等额多入选组 / 空未入选对照组，美元中性，
        用于判断「组合层失败」究竟源于市场 beta/现金拖累还是横截面 Alpha 缺失。

输出：out/h_earn_post_portfolio.csv, out/h_earn_post_cost.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (OUTD, FS_DATA, PREREG, ENTRIES, HORIZONS, Log)
import hep_engine as E

log = Log('_hep_portfolio.log')

FREQ = 252.0
COST_BPS = list(PREREG['cost_bps'])
COST_PRIMARY = 30.0
M_LONG = 'LONG_EW'
M_LS = 'LS_DN'
MODEL_DESC = {
    M_LONG: '§27 预注册：等权多头、E+1 开盘进、持有 T 日后 E+1+T 收盘平、无干预',
    M_LS: '§30 诊断：同口径多空（多入选 / 空对照），美元中性，非实盘口径',
}

# 预注册选股口径（§13/§14/§40，不做事后择优）
SELS = [
    ('QUADA', '§13 High FSC(fsc_rank>=0.7) & Low/Mid PA(pa_rank<=0.6) 全体'),
    ('RESID_T05', '§14 主信号 SIG_RESID 前 5%'),
    ('RESID_T10', '§14 主信号 SIG_RESID 前 10%'),
    ('RESID_T20', '§14 主信号 SIG_RESID 前 20%'),
    ('FSC_T10', '§13 纯基本面 FSC 前 10%'),
    ('INTER_T10', '§14 FSC×(-PA) 交互前 10%'),
]
# 诊断用多空（多 = 同一排序的前 q，空 = 同一排序的后 q）
LS_SELS = [
    ('LS_RESID_T10', 'sig_resid_d', 0.10),
    ('LS_RESID_T20', 'sig_resid_d', 0.20),
    ('LS_FSC_T10', 'fsc_z_d', 0.10),
    ('LS_INTER_T10', 'sig_inter_d', 0.10),
]


# ------------------------------------------------------------------ 市场面板
def load_market():
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    px = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date', 'qfq_open', 'qfq_close'])
    px['trade_date'] = px['trade_date'].astype(str)
    codes = np.sort(px['ts_code'].astype(str).unique())
    cmap = pd.Series(np.arange(len(codes)), index=codes)
    si = px['ts_code'].map(cmap).values
    kk = px['trade_date'].map(kmap).values
    ok = pd.notna(si) & pd.notna(kk)
    si, kk = si[ok].astype(np.int64), kk[ok].astype(np.int64)
    Oq = np.full((len(codes), NCAL), np.nan, dtype=np.float32)
    Cq = np.full((len(codes), NCAL), np.nan, dtype=np.float32)
    Oq[si, kk] = pd.to_numeric(px['qfq_open'], errors='coerce').values[ok]
    Cq[si, kk] = pd.to_numeric(px['qfq_close'], errors='coerce').values[ok]
    del px
    idx = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
    idx = idx[idx['ts_code'] == '000300.SH'].copy()
    idx['k'] = idx['trade_date'].astype(str).map(kmap)
    idx = idx[idx['k'].notna()].sort_values('k')
    lvl = np.full(NCAL, np.nan)
    lvl[idx['k'].values.astype(int)] = pd.to_numeric(idx['close'], errors='coerce').values
    lvl = pd.Series(lvl).ffill().bfill().values
    # 全 A 等权（中小盘代理基准，§29 辅助；官方中证1000/2000 无本地行情）
    with np.errstate(invalid='ignore', divide='ignore'):
        rr = Cq[:, 1:] / Cq[:, :-1] - 1.0
    m = np.isfinite(rr)
    ew = np.full(NCAL, np.nan)
    ew[1:] = np.where(m.sum(axis=0) > 0,
                      np.nansum(np.where(m, rr, 0.0), axis=0) / np.maximum(m.sum(axis=0), 1),
                      np.nan)
    return dict(cmap=cmap, Oq=Oq, Cq=Cq, lvl=lvl, ew=ew, td=td, NCAL=NCAL,
                n_codes=len(codes))


def bench_ann(mkt, k0, k1, series='lvl'):
    """区间年化。'lvl'=价格level比值；'ew'=日收益序列需先复利成净值。"""
    if series == 'ew':
        v = mkt['ew'][k0 + 1:k1 + 1]
        v = v[np.isfinite(v)]
        if len(v) < 1:
            return np.nan
        end = float(np.prod(1.0 + v))
    else:
        v = mkt[series][k0:k1 + 1]
        v = v[np.isfinite(v)]
        if len(v) < 2:
            return np.nan
        end = float(v[-1] / v[0])
    yrs = (k1 - k0 + 1) / FREQ
    if end <= 0 or yrs <= 0:
        return np.nan
    return end ** (1.0 / yrs) - 1.0


# ------------------------------------------------------------------ 选股
def _topq(de, col, q):
    v = pd.to_numeric(de[col], errors='coerce')
    thr = v.groupby(de['sig_k']).transform(lambda s: s.quantile(1.0 - q))
    return (v >= thr).to_numpy()


def _botq(de, col, q):
    v = pd.to_numeric(de[col], errors='coerce')
    thr = v.groupby(de['sig_k']).transform(lambda s: s.quantile(q))
    return (v <= thr).to_numpy()


def sel_mask(de, kind):
    if kind == 'QUADA':
        return pd.to_numeric(de['sig_quada_d'], errors='coerce').fillna(0).to_numpy() > 0.5
    if kind.startswith('RESID_T'):
        return _topq(de, 'sig_resid_d', int(kind.rsplit('_T', 1)[1]) / 100.0)
    if kind.startswith('FSC_T'):
        return _topq(de, 'fsc_z_d', int(kind.rsplit('_T', 1)[1]) / 100.0)
    if kind.startswith('INTER_T'):
        return _topq(de, 'sig_inter_d', int(kind.rsplit('_T', 1)[1]) / 100.0)
    raise KeyError(kind)


# ------------------------------------------------------------------ 组合模拟
def simulate(si, b, T, mkt):
    """§27 固定持有 T 日的等权组合（向量化构造日度聚合量）。"""
    NCAL = mkt['NCAL']
    x = b + T
    ok = (si >= 0) & (b >= 1) & (x <= NCAL - 1)
    si, b, x = si[ok], b[ok], x[ok]
    if len(b) == 0:
        return None
    j = np.arange(T + 1)
    tgrid = b[:, None] + j[None, :]
    S = mkt['Cq'][si[:, None], tgrid].astype(np.float64)
    prev = np.empty_like(S)
    prev[:, 0] = mkt['Oq'][si, b]
    if T >= 1:
        prev[:, 1:] = mkt['Cq'][si[:, None], tgrid[:, 1:] - 1]
    with np.errstate(invalid='ignore', divide='ignore'):
        r = S / prev - 1.0
    good = np.isfinite(r[:, 0]) & np.isfinite(prev[:, 0])
    if not good.any():
        return None
    si, b, x, tgrid, r = si[good], b[good], x[good], tgrid[good], r[good]
    bmk = np.zeros_like(r)
    if T >= 1:
        lv = mkt['lvl']
        bmk[:, 1:] = lv[tgrid[:, 1:]] / lv[tgrid[:, 1:] - 1] - 1.0
    v = np.isfinite(r)
    r0 = np.where(v, r, 0.0)
    b0 = np.where(v, bmk, 0.0)
    ti = tgrid.ravel()
    raw_sum = np.zeros(NCAL)
    ex_sum = np.zeros(NCAL)
    vcnt = np.zeros(NCAL)
    np.add.at(raw_sum, ti, r0.ravel())
    np.add.at(ex_sum, ti, (r0 - b0).ravel())
    np.add.at(vcnt, ti, v.ravel().astype(float))
    cnt = np.zeros(NCAL + 2)
    ent = np.zeros(NCAL + 1)
    np.add.at(cnt, b, 1.0)
    np.add.at(cnt, x + 1, -1.0)
    np.add.at(ent, b, 1.0)
    n = np.cumsum(cnt[:NCAL + 1])
    ext = np.zeros(NCAL + 1)
    np.add.at(ext, x + 1, 1.0)
    return dict(n=n, raw_sum=raw_sum, ex_sum=ex_sum, ent=ent, ext=ext, vcnt=vcnt,
                k_first=int(b.min()), k_last=int(x.max()), n_pos=int(len(b)))


def daily_series(sim, mkt, k0=None, k1=None, neutral_bmk=False):
    """组合日度**毛**收益序列（成本由调用方按 trade 序列扣除）。"""
    if sim is None:
        return None
    if k0 is None:
        k0 = sim['k_first']
    if k1 is None:
        k1 = sim['k_last']
    idx = np.arange(k0, k1 + 1)
    n = sim['n'][idx]
    vc = sim['vcnt'][idx]
    ent = sim['ent'][idx]
    ext = sim['ext'][idx]
    with np.errstate(invalid='ignore', divide='ignore'):
        rabs = np.where(vc > 0, sim['raw_sum'][idx] / np.maximum(vc, 1), 0.0)
        rex = np.where(vc > 0, sim['ex_sum'][idx] / np.maximum(vc, 1), 0.0)
    nprev = np.concatenate([[0.0], n[:-1]])
    inv_n = np.where(n > 0, 1.0 / np.maximum(n, 1), 0.0)
    inv_p = np.where(nprev > 0, 1.0 / np.maximum(nprev, 1), 0.0)
    surv = np.maximum(n - ent, 0.0)
    trade = surv * np.abs(inv_n - inv_p) + ent * inv_n + ext * inv_p
    if neutral_bmk:
        bmk = np.zeros(len(idx))
    else:
        lv = mkt['lvl'][idx]
        bmk = np.zeros(len(idx))
        bmk[1:] = lv[1:] / lv[:-1] - 1.0
    return dict(idx=idx, n=n, rabs=rabs, rex=rex, trade=trade, bmk=bmk)


def metrics(ser, cost_bps, mkt, cap_excess=None):
    c = float(cost_bps) / 1e4
    idx = ser['idx']
    rabs = ser['rabs'] - c * ser['trade']
    rex = ser['rex'] - c * ser['trade']
    n = ser['n']
    act = n > 0
    spans = len(idx)
    yrs = spans / FREQ
    nav = np.cumprod(1.0 + rabs)
    bn = np.cumprod(1.0 + ser['bmk'])
    ann = nav[-1] ** (1.0 / yrs) - 1.0 if yrs > 0 else np.nan
    bann = bn[-1] ** (1.0 / yrs) - 1.0 if yrs > 0 else np.nan
    sd = float(np.std(rabs, ddof=1)) * np.sqrt(FREQ)
    sharpe = ann / sd if sd > 1e-12 else np.nan
    mdd = float(np.min(nav / np.maximum.accumulate(nav) - 1.0))
    sdex = float(np.std(rex, ddof=1))
    ir = float(np.mean(rex) / sdex * np.sqrt(FREQ)) if sdex > 1e-12 else np.nan
    turn = float(np.sum(ser['trade']) / yrs) if yrs > 0 else np.nan
    ann_act = float(np.mean(rabs[act])) * FREQ if act.any() else np.nan
    exc_act = float(np.mean(rex[act])) * FREQ if act.any() else np.nan
    return dict(span_start=mkt['td'][idx[0]], span_end=mkt['td'][idx[-1]],
                span_days=spans, active_days=int(act.sum()),
                exposure=float(act.mean()), cash_ratio=float(1.0 - act.mean()),
                n_pos_mean=float(n[act].mean()) if act.any() else np.nan,
                n_pos_med=float(np.median(n[act])) if act.any() else np.nan,
                n_pos_max=float(n.max()),
                turnover_ann=turn, ann=ann, vol_ann=sd, sharpe=sharpe, mdd=mdd,
                bench_ann=bann, excess_ann=ann - bann, ir=ir,
                ann_active=ann_act, excess_active=exc_act,
                mdd_bench=float(np.min(bn / np.maximum.accumulate(bn) - 1.0)))


# ------------------------------------------------------------------ 主流程
def run_long(de, e, kind, T, mkt, port_rows, cost_rows):
    si = de['si'].to_numpy().astype(np.int64)
    bk = de['buy_k'].to_numpy().astype(np.int64)
    m = sel_mask(de, kind)
    sim = simulate(si[m], bk[m], T, mkt)
    if sim is None:
        return
    ser = daily_series(sim, mkt)
    g = metrics(ser, 0.0, mkt)
    by = {}
    for c in COST_BPS:
        mc = metrics(ser, c, mkt)
        by[float(c)] = mc
        cost_rows.append(dict(model=M_LONG, benchmark='000300.SH', entry='E%d' % e,
                              selection=kind, horizon='T+%d' % T, cost_bps=float(c),
                              ann_return=mc['ann'], excess_ann=mc['excess_ann'],
                              ir=mc['ir'], sharpe=mc['sharpe'], mdd=mc['mdd'],
                              turnover_ann=mc['turnover_ann'], exposure=mc['exposure'],
                              n_pos_signal=sim['n_pos']))
    n30 = by[COST_PRIMARY]
    port_rows.append(dict(
        model=M_LONG, benchmark='000300.SH', entry='E%d' % e, selection=kind,
        horizon='T+%d' % T, n_pos_signal=sim['n_pos'], span_start=g['span_start'],
        span_end=g['span_end'], span_days=g['span_days'], active_days=g['active_days'],
        exposure=g['exposure'], cash_ratio=g['cash_ratio'], n_pos_mean=g['n_pos_mean'],
        n_pos_med=g['n_pos_med'], n_pos_max=g['n_pos_max'], turnover_ann=g['turnover_ann'],
        bench_ann=g['bench_ann'], bench_mdd=g['mdd_bench'],
        ann_gross=g['ann'], excess_gross=g['excess_ann'], ir_gross=g['ir'],
        excess_active_gross=g['excess_active'], n_pos_signal_diag=g['n_pos_mean'],
        ann_net30=n30['ann'], excess_net30=n30['excess_ann'], ir_net30=n30['ir'],
        sharpe_net30=n30['sharpe'], mdd_net30=n30['mdd'], excess_active_net30=n30['excess_active']))


def run_ls(de, e, kind, col, q, T, mkt, port_rows, cost_rows):
    si = de['si'].to_numpy().astype(np.int64)
    bk = de['buy_k'].to_numpy().astype(np.int64)
    hi = _topq(de, col, q)
    lo = _botq(de, col, q)
    sH = simulate(si[hi], bk[hi], T, mkt)
    sL = simulate(si[lo], bk[lo], T, mkt)
    if sH is None or sL is None:
        return
    k0 = min(sH['k_first'], sL['k_first'])
    k1 = max(sH['k_last'], sL['k_last'])
    A = daily_series(sH, mkt, k0, k1, neutral_bmk=True)
    B = daily_series(sL, mkt, k0, k1, neutral_bmk=True)
    ser = dict(idx=A['idx'], n=A['n'] + B['n'],
               rabs=A['rabs'] - B['rabs'], rex=A['rex'] - B['rex'],
               trade=A['trade'] + B['trade'], bmk=A['bmk'])
    g = metrics(ser, 0.0, mkt)
    by = {}
    for c in COST_BPS:
        mc = metrics(ser, c, mkt)
        by[float(c)] = mc
        cost_rows.append(dict(model=M_LS, benchmark='DOLLAR_NEUTRAL', entry='E%d' % e,
                              selection=kind, horizon='T+%d' % T, cost_bps=float(c),
                              ann_return=mc['ann'], excess_ann=mc['ann'],
                              ir=mc['ir'], sharpe=mc['sharpe'], mdd=mc['mdd'],
                              turnover_ann=mc['turnover_ann'], exposure=mc['exposure'],
                              n_pos_signal=sH['n_pos'] + sL['n_pos']))
    n30 = by[COST_PRIMARY]
    port_rows.append(dict(
        model=M_LS, benchmark='DOLLAR_NEUTRAL', entry='E%d' % e, selection=kind,
        horizon='T+%d' % T, n_pos_signal=sH['n_pos'] + sL['n_pos'], span_start=g['span_start'],
        span_end=g['span_end'], span_days=g['span_days'], active_days=g['active_days'],
        exposure=g['exposure'], cash_ratio=g['cash_ratio'],
        n_pos_mean=g['n_pos_mean'], n_pos_med=g['n_pos_med'], n_pos_max=g['n_pos_max'],
        turnover_ann=g['turnover_ann'], bench_ann=0.0, bench_mdd=np.nan,
        ann_gross=g['ann'], excess_gross=g['ann'], ir_gross=g['ir'],
        excess_active_gross=g['excess_active'], n_pos_signal_diag=g['n_pos_mean'],
        ann_net30=n30['ann'], excess_net30=n30['ann'], ir_net30=n30['ir'],
        sharpe_net30=n30['sharpe'], mdd_net30=n30['mdd'], excess_active_net30=n30['excess_active']))


def main():
    log('=' * 78)
    log('H-EARN-POST §26–§31 组合模拟（预注册 %s）' % PREREG['hypothesis_id'])
    for k, v in MODEL_DESC.items():
        log('  %-9s %s' % (k, v))
    d = E.load()
    d = E.add_features(d)
    mkt = load_market()
    log('行情面板：%d 只 × %d 交易日 %s ~ %s' % (mkt['n_codes'], mkt['NCAL'],
                                          mkt['td'][0], mkt['td'][-1]))
    log('基准：Primary=000300.SH；中证1000/2000 本地无指数行情 -> 全A等权 PROXY')

    port_rows, cost_rows = [], []
    for e in ENTRIES:
        de = d[d['E'] == e].reset_index(drop=True)
        log('-' * 78)
        log('E%d：%d 行 / %d 个 cohort' % (e, len(de), de['sig_k'].nunique()))
        for kind, _desc in SELS:
            for T in HORIZONS:
                run_long(de, e, kind, T, mkt, port_rows, cost_rows)
            log('  [LONG_EW] %-10s 完成' % kind)
        for kind, col, q in LS_SELS:
            for T in HORIZONS:
                run_ls(de, e, kind, col, q, T, mkt, port_rows, cost_rows)
            log('  [LS_DN]   %-10s 完成' % kind)

    pdf = pd.DataFrame(port_rows)
    keep = ['model', 'benchmark', 'entry', 'selection', 'horizon', 'n_pos_signal',
            'span_start', 'span_end', 'span_days', 'active_days', 'exposure', 'cash_ratio',
            'n_pos_mean', 'n_pos_med', 'n_pos_max', 'turnover_ann', 'bench_ann', 'bench_mdd',
            'ann_gross', 'excess_gross', 'ir_gross', 'excess_active_gross',
            'ann_net30', 'excess_net30', 'ir_net30', 'sharpe_net30', 'mdd_net30',
            'excess_active_net30']
    pdf = pdf[[c for c in keep if c in pdf.columns]]
    pdf.to_csv(os.path.join(OUTD, 'h_earn_post_portfolio.csv'), index=False,
               encoding='utf-8-sig')
    log('已存 out/h_earn_post_portfolio.csv  %d 行' % len(pdf))

    cdf = pd.DataFrame(cost_rows)
    cdf.to_csv(os.path.join(OUTD, 'h_earn_post_cost.csv'), index=False,
               encoding='utf-8-sig')
    log('已存 out/h_earn_post_cost.csv  %d 行' % len(cdf))

    # ---------------- 汇总 ----------------
    L = pdf[pdf['model'] == M_LONG]
    S = pdf[pdf['model'] == M_LS]
    log('=' * 78)
    log('1) §28 Exposure / §30 组合 vs 基准（LONG_EW，成本 30bp）')
    for kind in ('QUADA', 'RESID_T10'):
        sub = L[L['selection'] == kind].set_index(['entry', 'horizon'])
        if sub.empty:
            continue
        log('  [' + kind + ']')
        log(E.fmt_tbl(sub[['n_pos_signal', 'span_days', 'exposure', 'cash_ratio',
                           'n_pos_mean', 'n_pos_max', 'turnover_ann', 'bench_ann',
                           'ann_gross', 'excess_gross', 'ann_net30', 'excess_net30',
                           'ir_net30']], 4))
    log('=' * 78)
    log('2) 归因：组合年化超额 = 全区间（含现金拖累）vs 仅持仓日（部署资金口径），毛成本')
    for kind in ('QUADA', 'RESID_T10', 'FSC_T10'):
        sub = L[L['selection'] == kind].set_index(['entry', 'horizon'])
        if sub.empty:
            continue
        log('  [' + kind + ']')
        log(E.fmt_tbl(sub[['exposure', 'bench_ann', 'ann_gross', 'excess_gross',
                           'excess_active_gross']], 4))
    log('=' * 78)
    log('3) §31 成本网格：LONG_EW 年化超额（%）随成本')
    piv = L.pivot_table(index=['entry', 'selection'], columns='cost_bps' if 'cost_bps' in L else None,
                        values='excess_net30')  # placeholder
    piv = cdf[cdf['model'] == M_LONG].pivot_table(index=['entry', 'selection'],
                                                  columns='cost_bps',
                                                  values='excess_ann') * 100.0
    log(E.fmt_tbl(piv, 3))
    log('=' * 78)
    log('4) §45 组合层转化判定（LONG_EW，30bp；cohort alpha -> portfolio alpha）')
    bad = 0
    for _, r in S[[]].iterrows():
        pass
    for _, r in L.iterrows():
        flag = 'OK' if r['excess_net30'] > 0 else 'FAIL'
        bad += 0 if r['excess_net30'] > 0 else 1
        log('  %-9s %-10s %s net30_excess=%+.4f ir=%+.3f %s'
            % (r['entry'], r['selection'], r['horizon'], r['excess_net30'],
               r['ir_net30'], flag))
    log('  -> %d/%d 个（Entry×选股×Horizon）组合在 30bp 后超额 <= 0' % (bad, len(L)))
    log('=' * 78)
    log('5) §30 诊断：LS_DN（美元中性）年化收益 —— 检验横截面 Alpha 是否真实存在')
    sub = S.set_index(['entry', 'selection', 'horizon'])
    log(E.fmt_tbl(sub[['turnover_ann', 'ann_gross', 'ir_gross', 'ann_net30', 'ir_net30']], 4))
    log('=' * 78)
    log('6) 辅助基准：全 A 等权 PROXY 年化（同一区间，供 §29 参考）')
    for e in ENTRIES:
        for T in HORIZONS:
            r = L[(L['entry'] == 'E%d' % e) & (L['selection'] == 'RESID_T10')
                  & (L['horizon'] == 'T+%d' % T)]
            if r.empty:
                continue
            k0 = int(np.where(mkt['td'] == r['span_start'].iloc[0])[0][0])
            k1 = int(np.where(mkt['td'] == r['span_end'].iloc[0])[0][0])
            log('  E%d T+%d  沪深300=%+.4f  全A等权PROXY=%+.4f'
                % (e, T, bench_ann(mkt, k0, k1, 'lvl'), bench_ann(mkt, k0, k1, 'ew')))
    log('=' * 78)
    log('DONE')
    log.save()


if __name__ == '__main__':
    main()
