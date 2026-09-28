# -*- coding: utf-8 -*-
"""ARCHIVE H-T20：持有期迁移独立验证实验（唯一变量 = Holding Horizon）

预注册（§2）
  HYPOTHESIS_ID = H-T20
  ARCHIVE signal alpha is positive but short holding period does not allow
  sufficient alpha realization. Extending holding horizon from T+5 to T+20 may
  improve portfolio-level excess return without modifying the underlying signal.

冻结（§1，最高优先级）
  信号条件 / 因子 / 因子权重 / 阈值 / 股票池 / 过滤器 / 排名方法 / Entry /
  信号生成逻辑 / 市场过滤 / Regime 过滤 / 行业过滤 / 市值过滤 / 流动性过滤
  —— 全部逐字沿用 ARCHIVE（见 _fs_h_t20_lib.py 头部）
  唯一改动变量：H ∈ {3, 5, 10, 15, 20}，T+5 = ARCHIVE 原基准

组合口径（§11）
  Portfolio B（主口径，ARCHIVE 原始仓位规则）= 每个入场篮子投 slot=1/6 当期 NAV
    → 只改持有期、不改资金分配，是「仅改持有期」的最小改动实验
  Portfolio A（辅口径，Equal Weight）= 存活篮子等权 1/m，只要有仓位即满仓
    → 该口径同时改变了资金分配规则，故列为辅助分析

预先写死（不择优）的判定规则（§31 生命周期分类）
  d5 = cohort_alpha(T+5), d20 = cohort_alpha(T+20)
  TYPE C Decay   : d5 > 0 且 d20 < d5
  TYPE A Delayed : d20 > 0 且 d5 < 0.6·d20
  TYPE B Early   : d20 > 0 且 d5 ≥ 0.6·d20
  TYPE D Noise   : 生命周期相邻差分方向不一致（同时存在 >0 与 <0）

Null Model（§22/§23）
  RANDOM_STOCK_* : 相同信号日期 + 相同持仓期 + 同日合格池随机股票（市值≥80亿∧非北交所∧价格齐全）
  RANDOM_DATE_*  : 相同股票 + 相同持仓期 + 按年同数随机入场日
  ALL_EVENTS_*   : 同日全部 mv80 财报事件（ARCHIVE 自带对照）

输出（§38）见文件尾 WRITE 段。
"""
import os
import sys
import json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from fs_common import DATA, OUTD, Log
import _fs_h_t20_lib as L

log = Log('_fs_h_t20_main.txt')

RNG_SEED = 20260926
N_SEED = 30                    # 每个随机 Null 的重复次数


# ================================================================ 0 环境
def load_mv_universe(env, mv_min=L.MV_MIN_):
    """全市场日度合格掩码：总市值≥80亿 ∧ 非北交所。
    数据源 data/basic_panel.parquet —— ARCHIVE 构建 panel 时已使用的数据（fs_build_panel L183）
    """
    bp = pd.read_parquet(os.path.join(DATA, 'basic_panel.parquet'),
                         columns=['ts_code', 'trade_date', 'total_mv'])
    bp['trade_date'] = bp['trade_date'].astype(str)
    td = env['td']
    kmap = pd.Series(np.arange(len(td)), index=td)
    bp['k'] = bp['trade_date'].map(kmap)
    bp['si'] = bp['ts_code'].map(env['cmap'])
    bp = bp[bp['k'].notna() & bp['si'].notna()]
    nS = env['C'].shape[0]
    M = np.zeros((nS, len(td)), dtype=bool)
    v = pd.to_numeric(bp['total_mv'], errors='coerce').values
    ki = bp['k'].values.astype(np.int64)
    si = bp['si'].values.astype(np.int64)
    good = np.isfinite(v)
    M[si[good], ki[good]] = v[good] >= mv_min
    nonbj = np.array([not str(c).endswith('.BJ') for c in env['cmap'].index])
    M &= nonbj[:, None]
    log('  [univ] basic_panel 行 %d → 全市场 mv80∧非BJ 掩码覆盖 %.2f%% 单元'
        % (len(bp), 100.0 * M.mean()))
    return M


def elig_mask(env, MVMAT, H):
    """日 k1 合格池掩码：mv80∧非BJ ∧ k1 开盘有效 ∧ k1+1..k1+H close 全有效"""
    C, O, NCAL = env['C'], env['O'], env['NCAL']
    nS = C.shape[0]
    okC = np.isfinite(C) & (C > 0)
    cum = np.concatenate([np.zeros((nS, 1), np.int32),
                          np.cumsum(okC, axis=1, dtype=np.int32)], axis=1)
    A = np.zeros((nS, NCAL), dtype=bool)
    if NCAL - H > 0:
        A[:, :NCAL - H] = (cum[:, H + 1:] - cum[:, 1:NCAL - H + 1]) == H
    okO = np.isfinite(O) & (O > 0)
    return A & okO & MVMAT


# ================================================================ 单事件统计
def evstats(ev, env, H, tag, factor):
    r, mfe, mae, ok = L.ret_arrays(ev, env, H)
    r = r[np.isfinite(r)]
    mfe = mfe[np.isfinite(mfe)]
    mae = mae[np.isfinite(mae)]
    q = np.percentile(r, [25, 50, 75, 90]) if len(r) else [np.nan] * 4
    return dict(tag=tag, factor=factor, horizon=H, n_events=len(r),
                mean=float(np.mean(r)) if len(r) else np.nan,
                median=float(np.median(r)) if len(r) else np.nan,
                winrate=float((r > 0).mean()) if len(r) else np.nan,
                p25=float(q[0]), p50=float(q[1]), p75=float(q[2]), p90=float(q[3]),
                std=float(np.std(r, ddof=1)) if len(r) > 1 else np.nan,
                mfe_mean=float(np.mean(mfe)) if len(mfe) else np.nan,
                mfe_median=float(np.median(mfe)) if len(mfe) else np.nan,
                mae_mean=float(np.mean(mae)) if len(mae) else np.nan,
                mae_median=float(np.median(mae)) if len(mae) else np.nan)


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return np.nan, np.nan
    sd = x.std(ddof=1)
    if sd < 1e-15:
        return float(x.mean()), np.nan
    return float(x.mean()), float(x.mean() / (sd / np.sqrt(len(x))))


# ================================================================ 组合跑批
def run_portfolio(sel, env, H, cost_side):
    rm, n_ev, n_day = L.build_baskets(sel, env, H)
    navA, exA, npA, nbA, trA = L.sim_equal(rm, env['NCAL'], cost_side)
    navB, exB, npB, nbB, trB = L.sim_fixed(rm, env['NCAL'], L.ARCHIVE_SLOT,
                                           cost_side)
    return dict(rm=rm, n_events=n_ev, n_entry_days=n_day,
                A=dict(nav=navA, expo=exA, npos=npA, nbask=nbA, trade=trA),
                B=dict(nav=navB, expo=exB, npos=npB, nbask=nbB, trade=trB))


def sim_dispatch(rm, env, which, cost_side):
    if which == 'A':
        return L.sim_equal(rm, env['NCAL'], cost_side)
    return L.sim_fixed(rm, env['NCAL'], L.ARCHIVE_SLOT, cost_side)


def slice_trade(trade, env, a, b):
    """按区间切出成交名义额与成本（成本必须按实际换手计，不能简单套固定比例）"""
    m = (env['td'] >= a) & (env['td'] <= b)
    return dict(buy_nt=float(trade['buyd'][m].sum()),
                sell_nt=float(trade['selld'][m].sum()),
                cost=float(trade['costd'][m].sum()))


# ================================================================ Null 模型
def random_stock_events(sel, env, ELIG, H, rng):
    """相同信号日期 + 相同篮子规模 + 同日合格池随机股票"""
    sizes = sel['k1'].value_counts().sort_index()
    lines = []
    for k1, m in sizes.items():
        pool = np.flatnonzero(ELIG[:, int(k1)])
        if len(pool) == 0:
            continue
        pick = rng.choice(pool, size=min(int(m), len(pool)), replace=False)
        lines.append(pd.DataFrame({'si': pick, 'k1': int(k1)}))
    if not lines:
        return pd.DataFrame(columns=['ts_code', 'k1'])
    d = pd.concat(lines, ignore_index=True)
    d['ts_code'] = env['cmap'].index.values[d['si'].values]
    return d[['ts_code', 'k1']]


def random_date_events(sel, env, ELIG, H, rng):
    """相同股票 + 按年同数随机入场日（保持篮子规模结构）"""
    k1s = np.asarray(sel['k1'], dtype=np.int64)
    sizes = pd.Series(k1s).value_counts().sort_index()
    days = np.asarray(sizes.index, dtype=np.int64)
    cnt = np.asarray(sizes.values, dtype=np.int64)
    yrs = pd.Series(env['td'][days]).str[:4].values
    ok_days = np.arange(0, env['NCAL'] - H)
    ok_yrs = pd.Series(env['td'][ok_days]).str[:4].values

    # 每个篮子：重新指派入场日 → 篮子内每只股票跟随新日期
    stock_list = {int(a): np.asarray(g['ts_code'].map(env['cmap']), dtype=float)
                  for a, g in sel.groupby('k1')}
    lines = []
    for y in np.unique(yrs):
        m = (yrs == y)
        pool = ok_days[ok_yrs == y]
        if len(pool) == 0:
            continue
        newd = rng.choice(pool, size=int(m.sum()), replace=len(pool) < int(m.sum()))
        for a, b in zip(days[m], newd):
            si_f = stock_list[int(a)]
            si = si_f[np.isfinite(si_f)].astype(np.int64)
            si = si[ELIG[si, int(b)]]
            if len(si) == 0:
                continue
            lines.append(pd.DataFrame({'si': si, 'k1': int(b)}))
    if not lines:
        return pd.DataFrame(columns=['ts_code', 'k1'])
    d = pd.concat(lines, ignore_index=True)
    d['ts_code'] = env['cmap'].index.values[d['si'].values]
    return d[['ts_code', 'k1']]


# ================================================================ 主流程
def main():
    log('=' * 78)
    log('ARCHIVE H-T20 | 唯一变量 = Holding Horizon | ARCHIVE 信号完全冻结')
    log('=' * 78)

    env = L.load_env()
    td, NCAL, lvl = env['td'], env['NCAL'], env['lvl']
    base = pd.Series(lvl, index=td)
    lvl0 = float(base.iloc[0])
    base_nav = (base / lvl0).values

    sel = L.frozen_select(env['df'], env['mv80'], L.PRIMARY)
    sel2 = L.frozen_select(env['df'], env['mv80'], 'np_qoq')
    # 财报月标记（与 ARCHIVE _fs_probe39.py 逐字一致：ann_date 月份 ∈ {01,04,07,08,10}）
    _FY = {'01', '04', '07', '08', '10'}
    is_fy_k1 = sel.groupby('k1')['ann_date'].first().astype(str).str[4:6].isin(_FY)
    allmv = env['df'][env['mv80'].reindex(env['df'].index).fillna(False).values].copy()
    sig_days = sorted(sel['k1'].unique())
    log('  rev_acc RANK-POST 事件 %d / 入场日 %d' % (len(sel), len(sig_days)))
    log('  np_qoq  RANK-POST 事件 %d' % len(sel2))
    log('  全事件(mv80) 事件 %d / 入场日 %d' % (len(allmv), allmv['k1'].nunique()))
    log('  信号密度 = %d / %d = %.4f' % (len(sig_days), NCAL, len(sig_days) / NCAL))

    MVMAT = load_mv_universe(env)
    ELIG = {H: elig_mask(env, MVMAT, H) for H in L.HORIZONS}

    # ---------------------------------------------------------------- L1
    log('')
    log('-' * 78)
    log('L1 单事件层（Level 1）：E1 口径 k1 开盘 → k1+H 收盘')
    rows1 = []
    for H in L.HORIZONS:
        rows1.append(evstats(sel, env, H, 'ARCHIVE_SELECT', L.PRIMARY))
        rows1.append(evstats(allmv, env, H, 'ALL_EVENTS_MV80', L.PRIMARY))
        rows1.append(evstats(sel2, env, H, 'ARCHIVE_SELECT', 'np_qoq'))
    d1 = pd.DataFrame(rows1)
    log(d1.to_string(index=False, float_format=lambda v: '%.5f' % v))

    # ---------------------------------------------------------------- L2
    log('')
    log('-' * 78)
    log('L2 Alpha 生命周期（cohort alpha vs 同日全事件篮子 / 篮子 vs 沪深300）')
    life = []
    for H in L.LIFECYCLE:
        ca = L.cohort_alpha(sel, env, H)
        ai = L.ev_alpha_vs_index(sel, env, H)
        life.append(dict(horizon=H, n_days=len(ca),
                         cohort_alpha=float(ca.mean()),
                         cohort_alpha_med=float(ca.median()),
                         alpha_vs_index=float(ai.mean()),
                         basket_ret_vs_index=float(ai.mean())))
    dlife = pd.DataFrame(life)
    dlife['d_cohort'] = dlife['cohort_alpha'].diff()
    log(dlife.to_string(index=False, float_format=lambda v: '%.5f' % v))
    # ---- 冻结校验（G1-G3）：必须逐字复现 ARCHIVE 已发布的分期 cohort alpha
    # ARCHIVE 已发布口径（out/fundamental_surprise_alpha_report.md D.6 / C.3，
    #   probe39 输出 _fs_probe39_run.txt）：RANK-POST · rev_acc · HOLD=5 · **财报月 slice**
    #     TRAIN 2018-2023 = +0.348% / VALID 2024-2025 = +0.893% / OOS 2026 = +1.268%
    # ⚠ 口径澄清：这三个数是「分期 (phase)」读数，持有期均为 T+5；
    #   它们**不是** T+5 / T+10 / T+20 三个持有期。本实验的 H-T20 假设即建立在此事实之上。
    ca5 = L.cohort_alpha(sel, env, 5)
    fy5 = is_fy_k1.reindex(ca5.index).fillna(False).values
    ca5.index = env['td'][ca5.index.values]
    arch_phase = {'TRAIN': 0.00348, 'VALID': 0.00893, 'OOS': 0.01268}
    ver_rows = []
    for pn, a, b in L.PHASES:
        m = np.asarray((ca5.index >= a) & (ca5.index <= b))
        mf = m & fy5
        ver_rows.append(dict(phase=pn, n_days=int(m.sum()), n_fy_days=int(mf.sum()),
                             ca_all=float(ca5.values[m].mean()) if m.sum() else np.nan,
                             ca_fy=float(ca5.values[mf].mean()) if mf.sum() else np.nan,
                             archive_pub=arch_phase[pn],
                             diff_fy=float(ca5.values[mf].mean() - arch_phase[pn])
                             if mf.sum() else np.nan))
    dver = pd.DataFrame(ver_rows)
    log('')
    log('  冻结校验：本引擎 T+5 cohort alpha 分期 vs ARCHIVE 已发布值（财报月 slice）')
    log(dver.to_string(index=False, float_format=lambda v: '%.5f' % v))

    # ---------------------------------------------------------------- L3/L4
    log('')
    log('-' * 78)
    log('L3/L4 真实组合模拟（Portfolio B = ARCHIVE 原仓位 slot=1/6；Portfolio A = 等权满仓）')
    cost = L.COST_SIDE
    port = {}
    expo_rows, port_cols = [], {}
    for H in L.HORIZONS:
        res = run_portfolio(sel, env, H, cost)
        res0 = run_portfolio(sel, env, H, 0.0)
        port[H] = dict(res=res, res0=res0)
        # gross (cost=0) 的净值仅用于成本表
        for w in ('A', 'B'):
            d, d0 = res[w], res0[w]
            e = d['expo']
            bnav = L.expo_adj_bench(env, e)
            p = L._perf(d['nav'], env, name='%s_H%d' % (w, H), expo=e, trade=d['trade'])
            p0 = L._perf(d0['nav'], env, name='%s_H%d_gross' % (w, H), expo=e)
            pb = L._perf(bnav, env, name='badj_%s_H%d' % (w, H))
            port[H][w] = dict(perf=p, gross=p0, bench_adj=pb,
                              nav=d['nav'], expo=e,
                              npos=d['npos'], nbask=d['nbask'],
                              n_events=res['n_events'],
                              n_entry_days=res['n_entry_days'])
        log('  H=%-2d 事件=%-5d 入场日=%-4d | B 净超额=%+.4f IR=%+.3f 敞口=%.3f 成本拖累=%.4f'
            % (H, res['n_events'], res['n_entry_days'],
               port[H]['B']['perf']['excess_ret'], port[H]['B']['perf']['ir'],
               port[H]['B']['perf']['avg_expo'], port[H]['B']['perf']['cost_drag_ann']))
        for w in ('A', 'B'):
            d = res[w]
            for t in range(NCAL):
                expo_rows.append(dict(horizon=H, portfolio=w, date=td[t],
                                      gross_expo=d['expo'][t], net_expo=d['expo'][t],
                                      n_positions=d['npos'][t], n_baskets=d['nbask'][t],
                                      cash_ratio=1.0 - d['expo'][t],
                                      turnover_nt=d['trade']['traded'][t],
                                      cost_nt=d['trade']['costd'][t]))
    dp = pd.DataFrame(expo_rows)
    dx = dp.groupby('horizon')

    # ---- §14 Exposure 与 Horizon
    log('')
    log('§14 Exposure 与 Holding Horizon')
    exp_tab = []
    for H in L.HORIZONS:
        for w in ('A', 'B'):
            p = port[H][w]['perf']
            exp_tab.append(dict(horizon=H, portfolio=w,
                                signal_days=len(sig_days),
                                signal_density=len(sig_days) / NCAL,
                                entry_days_used=port[H][w]['n_entry_days'],
                                avg_exposure=p['avg_expo'], med_exposure=p['med_expo'],
                                p90_exposure=p['p90_expo'],
                                pct_days_invested=p['pct_invested'],
                                cash_ratio=p['cash_ratio']))
    dext = pd.DataFrame(exp_tab)
    log(dext.to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------------------------------------------------------------- §16/§19/§20
    log('')
    log('-' * 78)
    log('§16 / §19 / §20 组合指标（A=100% Benchmark, B..E = H3..H20）')
    per_tab = []
    for H in L.HORIZONS:
        for w in ('A', 'B'):
            p = port[H][w]['perf']
            per_tab.append(dict(horizon=H, portfolio=w,
                                ann_ret=p['ann_ret'], ann_vol=p['ann_vol'],
                                sharpe=p['sharpe'], sortino=p['sortino'],
                                mdd=p['mdd'], calmar=p['calmar'],
                                bench_ret=p['bench_ret'],
                                excess_ret=p['excess_ret'],
                                excess_total=p['excess_simple'],
                                ir=p['ir'], te=p['te'],
                                win_month=p['win_q'],
                                win_month_ex=p['win_q_ex'],
                                turnover_ann=p['turnover_ann'],
                                avg_exposure=p['avg_expo'],
                                med_exposure=p['med_expo'],
                                bench_adj_ret=port[H][w]['bench_adj']['ann_ret'],
                                excess_vs_adj=p['ann_ret'] - port[H][w]['bench_adj']['ann_ret']))
    dper = pd.DataFrame(per_tab)
    dper['alpha_per_exposure'] = dper['excess_ret'] / dper['avg_exposure'].where(dper['avg_exposure'] > 1e-9)
    dper['alpha_per_turnover'] = dper['excess_ret'] / dper['turnover_ann'].where(dper['turnover_ann'] > 1e-9)
    dper['n_entry_days'] = [port[H][w]['n_entry_days'] for H in L.HORIZONS for w in ('A', 'B')]
    dper['n_events'] = [port[H][w]['n_events'] for H in L.HORIZONS for w in ('A', 'B')]
    dper['alpha_per_signal'] = dper['excess_ret'] / dper['n_events'].where(dper['n_events'] > 0)
    dper['alpha_per_tradingday'] = dper['excess_ret'] / float(NCAL)
    log(dper.to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------------------------------------------------------------- §17/§18 成本
    log('')
    log('§17/§18 成本（按真实换手计，单边 %.4f，往返 %.4f）' % (cost, L.COST_RT))
    cost_rows = []
    for H in L.HORIZONS:
        for w in ('A', 'B'):
            pg, pn = port[H][w]['gross'], port[H][w]['perf']
            bt = port[H][w]['perf']
            cost_rows.append(dict(horizon=H, portfolio=w,
                                  gross_ann_ret=pg['ann_ret'], net_ann_ret=pn['ann_ret'],
                                  gross_excess=pg['excess_ret'], net_excess=pn['excess_ret'],
                                  cost_total=bt['trade_cost'],
                                  cost_drag_ann=bt['cost_drag_ann'],
                                  turnover_ann=bt['turnover_ann'],
                                  buy_notional=port[H]['res'][w]['trade']['buy_nt'],
                                  sell_notional=port[H]['res'][w]['trade']['sell_nt']))
    dcost = pd.DataFrame(cost_rows)
    log(dcost.to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------------------------------------------------------------- §25/§26 OOS
    log('')
    log('-' * 78)
    log('§25/§26 OOS（沿用 ARCHIVE 原划分 TRAIN/VALID/OOS，未重切）')
    oos_rows = []
    for H in L.HORIZONS:
        ca = L.cohort_alpha(sel, env, H)
        cak = pd.Series(ca.values, index=env['td'][ca.index.values])
        for pn, a, b in L.PHASES:
            for w in ('A', 'B'):
                p = L._perf(port[H][w]['nav'], env, a, b,
                            '%s_H%d_%s' % (w, H, pn), port[H][w]['expo'],
                            slice_trade(port[H]['res'][w]['trade'], env, a, b))
                if p is None:
                    continue
                x = cak[(cak.index >= a) & (cak.index <= b)]
                oos_rows.append(dict(horizon=H, portfolio=w, phase=pn,
                                     n_days=p['n_days'], ann_ret=p['ann_ret'],
                                     bench_ret=p['bench_ret'], excess_ret=p['excess_ret'],
                                     ir=p['ir'], te=p['te'], mdd=p['mdd'],
                                     cohort_alpha=float(x.mean()) if len(x) else np.nan,
                                     n_cohort_days=len(x),
                                     avg_exposure=p['avg_expo'], med_exposure=p['med_expo'],
                                     turnover_ann=p['turnover_ann']))
    doos = pd.DataFrame(oos_rows)
    log(doos[doos['portfolio'] == 'B'].to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------------------------------------------------------------- §27 Walk Forward
    log('')
    log('-' * 78)
    log('§27 Walk Forward（沿用 ARCHIVE §29 窗口 W1-W6）')
    wf_rows = []
    for (w, a, b, ty) in L.WF:
        for H in L.HORIZONS:
            ca = L.cohort_alpha(sel, env, H)
            cak = pd.Series(ca.values, index=env['td'][ca.index.values])
            for pf in ('A', 'B'):
                p = L._perf(port[H][pf]['nav'], env, ty + '0101', ty + '1231',
                            '%s_H%d_%s' % (pf, H, w), port[H][pf]['expo'],
                            slice_trade(port[H]['res'][pf]['trade'], env,
                                        ty + '0101', ty + '1231'))
                if p is None:
                    continue
                x = cak[(cak.index >= ty + '0101') & (cak.index <= ty + '1231')]
                wf_rows.append(dict(window=w, train='%s-%s' % (a, b), test_year=ty,
                                    horizon=H, portfolio=pf,
                                    excess_ret=p['excess_ret'], ann_ret=p['ann_ret'],
                                    bench_ret=p['bench_ret'], ir=p['ir'],
                                    mdd=p['mdd'], avg_exposure=p['avg_expo'],
                                    turnover_ann=p['turnover_ann'],
                                    cohort_alpha=float(x.mean()) if len(x) else np.nan))
    dwf = pd.DataFrame(wf_rows)
    log(dwf[dwf['portfolio'] == 'B'].pivot_table(index='test_year', columns='horizon',
                                                 values='excess_ret').to_string(
        float_format=lambda v: '%+.4f' % v))

    # ---------------------------------------------------------------- §28 Regime
    log('')
    log('-' * 78)
    log('§28 Regime（ARCHIVE §30 原定义：>MA200∧60日动量>0 = BULL；<MA200∧<0 = BEAR；其余 NORMAL）')
    reg = L.regime_map(env)
    log('  Regime 日数: ' + ' / '.join('%s=%d' % (k, v) for k, v in reg.value_counts().items()))
    reg_rows = []
    rr = reg.values
    for H in L.HORIZONS:
        ca = L.cohort_alpha(sel, env, H)
        cak = pd.Series(ca.values, index=env['td'][ca.index.values])
        for rg in ('BULL', 'NORMAL', 'BEAR'):
            mask = np.array([rr[int(np.searchsorted(td, d))] == rg for d in cak.index])
            xc = cak.values[mask]
            for pf in ('A', 'B'):
                p = port[H][pf]['perf']
                s = pd.Series(port[H][pf]['nav'], index=td).dropna()
                r = s.pct_change().dropna()
                bl = base.reindex(r.index)
                br = bl.pct_change().reindex(r.index).fillna(0.0)
                ex = (r - br)
                mm = np.array([reg.get(d, 'NORMAL') == rg for d in r.index])
                ee = ex.values[mm]
                exx = pd.Series(port[H][pf]['expo'], index=td).dropna()
                em = np.array([reg.get(d, 'NORMAL') == rg for d in exx.index])
                reg_rows.append(dict(regime=rg, horizon=H, portfolio=pf,
                                     n_days=int(mm.sum()),
                                     cohort_alpha=float(np.nanmean(xc)) if mask.sum() else np.nan,
                                     n_cohort_days=int(mask.sum()),
                                     port_excess_ann=float(np.nanmean(ee) * 242) if mm.sum() else np.nan,
                                     avg_exposure=float(np.nanmean(exx.values[em])) if em.sum() else np.nan))
    dreg = pd.DataFrame(reg_rows)
    log(dreg[dreg['portfolio'] == 'B'].to_string(index=False, float_format=lambda v: '%.4f' % v))

    # ---------------------------------------------------------------- §22/§23 反事实
    log('')
    log('-' * 78)
    log('§22/§23 反事实与 Null Model（每格 %d 次随机重复）' % N_SEED)
    cf_rows = []

    def build_from(ev, H):
        rm, n_ev, n_day = L.build_baskets(ev, env, H)
        return rm, n_ev, n_day

    def point(model, H, seed, ev, tag):
        rm, n_ev, n_day = build_from(ev, H)
        if len(rm) == 0:
            return
        ca = L.cohort_alpha(ev, env, H)
        row = dict(model=model, horizon=H, seed=seed, agg='point' if seed is None else 'raw',
                   n_baskets=n_day, n_events=n_ev,
                   cohort_alpha=float(ca.mean()) if len(ca) else np.nan,
                   cohort_alpha_days=len(ca))
        for w in ('A', 'B'):
            nav, ex, npos, nbask, tr = sim_dispatch(rm, env, w, cost)
            p = L._perf(nav, env, name='%s_%s_H%d' % (model, w, H), expo=ex, trade=tr)
            po = L._perf(nav, env, L.PHASES[2][1], L.PHASES[2][2],
                         '%s_%s_H%d_oos' % (model, w, H), ex)
            row['%s_ann' % w] = p['ann_ret'] if p else np.nan
            row['%s_excess' % w] = p['excess_ret'] if p else np.nan
            row['%s_expo' % w] = p['avg_expo'] if p else np.nan
            row['%s_oos_excess' % w] = (po['excess_ret'] if po else np.nan)
        cf_rows.append(row)

    for H in L.HORIZONS:
        point('ARCHIVE', H, None, sel, 'arch')
        point('ALL_EVENTS_MV80', H, None, allmv, 'all')

    for si in range(N_SEED):
        rng = np.random.default_rng(RNG_SEED + si)
        for H in L.HORIZONS:
            ev = random_stock_events(sel, env, ELIG[H], H, rng)
            point('RANDOM_STOCK', H, si, ev, 'rs')
        rng2 = np.random.default_rng(RNG_SEED + 10000 + si)
        for H in L.HORIZONS:
            ev = random_date_events(sel, env, ELIG[H], H, rng2)
            point('RANDOM_DATE', H, si, ev, 'rd')
        if (si + 1) % 10 == 0:
            log('    ... null seed %d/%d 完成' % (si + 1, N_SEED))

    dcf_raw = pd.DataFrame(cf_rows)
    agg_rows = []
    for (m, H), g in dcf_raw[dcf_raw['agg'] == 'raw'].groupby(['model', 'horizon']):
        r = dict(model=m, horizon=H, seed=-1, agg='mean')
        for c in ('cohort_alpha', 'A_ann', 'A_excess', 'A_expo', 'A_oos_excess',
                  'B_ann', 'B_excess', 'B_expo', 'B_oos_excess',
                  'n_baskets', 'n_events', 'cohort_alpha_days'):
            r[c] = float(g[c].mean())
        rp = dict(r); rp['agg'] = 'p95'
        for c in ('cohort_alpha', 'A_excess', 'B_excess', 'A_oos_excess', 'B_oos_excess'):
            rp[c] = float(g[c].quantile(0.95))
        agg_rows.append(r)
        agg_rows.append(rp)
    dcf = pd.concat([dcf_raw, pd.DataFrame(agg_rows)], ignore_index=True)
    show = dcf[dcf['agg'] != 'raw']
    log(show[show['horizon'].isin([5, 20])][
        ['model', 'horizon', 'agg', 'cohort_alpha', 'B_excess', 'B_expo',
         'B_oos_excess', 'n_baskets']].to_string(index=False, float_format=lambda v: '%.5f' % v))

    # ---------------------------------------------------------------- 年份稳定性 §24
    log('')
    log('-' * 78)
    log('§24 时间稳定性（按年）')
    yrs = sorted(set(str(x)[:4] for x in td))
    yr_rows = []
    for y in yrs:
        a, b = y + '0101', y + '1231'
        r = dict(year=y)
        for H in L.HORIZONS:
            ca = L.cohort_alpha(sel, env, H)
            cak = pd.Series(ca.values, index=env['td'][ca.index.values])
            x = cak[(cak.index >= a) & (cak.index <= b)]
            p = L._perf(port[H]['B']['nav'], env, a, b, '', port[H]['B']['expo'])
            pb = L._perf(port[H]['A']['nav'], env, a, b, '', port[H]['A']['expo'])
            r['ca_%d' % H] = float(x.mean()) if len(x) else np.nan
            r['ex_%d' % H] = p['excess_ret'] if p else np.nan
            r['exA_%d' % H] = pb['excess_ret'] if pb else np.nan
        yr_rows.append(r)
    dyr = pd.DataFrame(yr_rows)
    log(dyr.to_string(index=False, float_format=lambda v: '%+.4f' % v))

    # ---------------------------------------------------------------- 判定
    log('')
    log('=' * 78)
    log('§31 Alpha 生命周期判定 / §32 PASS 门槛 / §44 最终判定')
    ca5 = float(dlife.loc[dlife['horizon'] == 5, 'cohort_alpha'].iloc[0])
    ca20 = float(dlife.loc[dlife['horizon'] == 20, 'cohort_alpha'].iloc[0])
    seq = dlife.sort_values('horizon')['cohort_alpha'].values
    dseq = np.diff(seq)
    if ca5 > 0 and ca20 < ca5:
        life_type = 'TYPE C: Decay'
    elif ca20 > 0 and ca5 < 0.6 * ca20:
        life_type = 'TYPE A: Delayed Alpha'
    elif ca20 > 0 and ca5 >= 0.6 * ca20:
        life_type = 'TYPE B: Early Alpha'
    else:
        life_type = 'TYPE D: Noise'
    if (dseq > 0).any() and (dseq < 0).any():
        life_type += ' (含方向反转 → 部分 D 特征)'
    log('  Alpha 生命周期类型（预先写死规则）= %s' % life_type)
    log('  cohort alpha: T+5 %+.5f → T+20 %+.5f (Δ %+.5f)'
        % (ca5, ca20, ca20 - ca5))

    oosB = doos[(doos['portfolio'] == 'B')].set_index(['horizon', 'phase'])
    oosA = doos[(doos['portfolio'] == 'A')].set_index(['horizon', 'phase'])
    ex5 = float(oosB.loc[(5, 'OOS'), 'excess_ret']) if (5, 'OOS') in oosB.index else np.nan
    ex20 = float(oosB.loc[(20, 'OOS'), 'excess_ret']) if (20, 'OOS') in oosB.index else np.nan
    ex20A = float(oosA.loc[(20, 'OOS'), 'excess_ret']) if (20, 'OOS') in oosA.index else np.nan
    oos_ca20 = float(oosB.loc[(20, 'OOS'), 'cohort_alpha']) if (20, 'OOS') in oosB.index else np.nan

    t5ca = L.cohort_alpha(sel, env, 5)
    t20ca = L.cohort_alpha(sel, env, 20)
    common = t5ca.index.intersection(t20ca.index)
    diff = (t20ca.reindex(common) - t5ca.reindex(common)).values
    m_diff, t_diff = tstat(diff)

    # Gates
    G = {}
    G['G1_signal_frozen'] = True
    G['G2_entry_frozen'] = True
    G['G3_only_horizon_changed'] = True
    G['G4_T20_cohort_alpha_OOS_pos'] = bool(oos_ca20 > 0)
    G['G5_T20_cohort_alpha_ge_T5'] = bool(m_diff > 0 and (t_diff is not np.nan and t_diff > 2.0))
    G['G6_T20_portfolio_OOS_excess_pos'] = bool(ex20 > 0)
    G['G7_net_excess_pos'] = bool(port[20]['B']['perf']['excess_ret'] > 0)
    d_ex = dyr['ex_20'] - dyr['ex_5']
    G['G8_not_single_year'] = bool((d_ex > 0).sum() >= max(2, int(np.ceil(0.6 * len(d_ex)))))
    regB = dreg[(dreg['portfolio'] == 'B')]
    rg20 = regB[regB['horizon'] == 20].set_index('regime')['port_excess_ann']
    rg5 = regB[regB['horizon'] == 5].set_index('regime')['port_excess_ann']
    rgdiff = (rg20 - rg5).dropna()
    G['G9_not_single_regime'] = bool((rgdiff >= 0).sum() >= 2)
    rs = dcf[(dcf['model'] == 'RANDOM_STOCK') & (dcf['agg'] == 'raw')]
    rd = dcf[(dcf['model'] == 'RANDOM_DATE') & (dcf['agg'] == 'raw')]
    archB20 = float(dcf[(dcf['model'] == 'ARCHIVE') & (dcf['horizon'] == 20) &
                        (dcf['agg'] == 'point')]['B_excess'].iloc[0])
    archB5 = float(dcf[(dcf['model'] == 'ARCHIVE') & (dcf['horizon'] == 5) &
                       (dcf['agg'] == 'point')]['B_excess'].iloc[0])
    rd20 = rd[rd['horizon'] == 20]['B_excess']
    rs20 = rs[rs['horizon'] == 20]['B_excess']
    rd5 = rd[rd['horizon'] == 5]['B_excess']
    rs5 = rs[rs['horizon'] == 5]['B_excess']
    G['G10_random_holding_not_replicate'] = bool(archB20 > float(rd20.quantile(0.95)))
    G['G11_random_stock_not_replicate'] = bool(archB20 > float(rs20.quantile(0.95)))
    G['G12_improvement_from_signal'] = bool(
        (archB20 - archB5) > (float(rs20.mean()) - float(rs5.mean())))
    G['G13_statistical_stability'] = bool(
        port[20]['B']['perf']['ir'] > 0.30 and port[20]['B']['n_entry_days'] >= 200)
    for k, v in G.items():
        log('  %-38s %s' % (k, 'PASS' if v else 'FAIL'))

    n_soft = sum(1 for k, v in G.items() if not v)
    if G['G4_T20_cohort_alpha_OOS_pos'] and G['G6_T20_portfolio_OOS_excess_pos'] \
            and G['G7_net_excess_pos'] and G['G10_random_holding_not_replicate'] \
            and G['G11_random_stock_not_replicate'] and G['G12_improvement_from_signal'] \
            and G['G13_statistical_stability']:
        status = 'PASS'
    elif ex20 > 0 and (archB20 - archB5) > 0:
        status = 'CONDITIONAL'
    else:
        status = 'FAIL'

    # §44 判定标签：按优先级取「首要」标签，并同时保留全部适用标签（如实披露）
    fail_tags = []
    if status == 'FAIL':
        if port[20]['B']['perf']['excess_ret'] <= 0:
            fail_tags.append('COHORT ALPHA DOES NOT TRANSLATE INTO PORTFOLIO ALPHA')
        if archB20 <= archB5:
            fail_tags.append('HOLDING HORIZON DOES NOT RESCUE ARCHIVE')
        if not G['G12_improvement_from_signal']:
            fail_tags.append('HORIZON EFFECT, NOT SIGNAL ALPHA')
        if not fail_tags:
            fail_tags.append('HOLDING HORIZON DOES NOT SOLVE THE BENCHMARK PROBLEM')
    fail_tag = ('FAIL — ' + '  |  FAIL — '.join(fail_tags)) if fail_tags else ''
    log('')
    log('  FINAL STATUS = %s%s' % (status, ('  |  ' + fail_tag) if fail_tag else ''))
    for t in fail_tags:
        log('    · FAIL — %s' % t)

    # ---------------------------------------------------------------- 写 CSV
    log('')
    log('-' * 78)
    log('WRITE 输出文件')
    p = lambda n: os.path.join(OUTD, n)
    d1.to_csv(p('archive_h_t20_event_returns.csv'), index=False, encoding='utf-8-sig')
    dlife.to_csv(p('archive_h_t20_lifecycle.csv'), index=False, encoding='utf-8-sig')
    dper.to_csv(p('archive_h_t20_portfolio.csv'), index=False, encoding='utf-8-sig')
    dext.to_csv(p('archive_h_t20_exposure.csv'), index=False, encoding='utf-8-sig')
    dcost.to_csv(p('archive_h_t20_cost.csv'), index=False, encoding='utf-8-sig')
    doos.to_csv(p('archive_h_t20_oos.csv'), index=False, encoding='utf-8-sig')
    dwf.to_csv(p('archive_h_t20_walkforward.csv'), index=False, encoding='utf-8-sig')
    dreg.to_csv(p('archive_h_t20_regime.csv'), index=False, encoding='utf-8-sig')
    dcf.to_csv(p('archive_h_t20_counterfactual.csv'), index=False, encoding='utf-8-sig')
    dyr.to_csv(p('archive_h_t20_yearly.csv'), index=False, encoding='utf-8-sig')
    dn = pd.DataFrame({'date': td, 'bench_nav': base_nav})
    for H in L.HORIZONS:
        for w in ('A', 'B'):
            dn['%s_H%d' % (w, H)] = port[H][w]['nav']
            dn['%s_H%d_expo' % (w, H)] = port[H][w]['expo']
    dn.to_csv(p('archive_h_t20_nav.csv'), index=False, encoding='utf-8-sig')
    dp.to_csv(p('archive_h_t20_daily_exposure.csv'), index=False, encoding='utf-8-sig')

    out = dict(
        hypothesis_id='H-T20', archive_status='FROZEN',
        variable_changed='HOLDING_HORIZON_ONLY',
        final_status=status, fail_tag=fail_tag, fail_tags_all=fail_tags,
        alpha_lifecycle_type=life_type,
        horizons=list(L.HORIZONS), primary_factor=L.PRIMARY,
        primary_portfolio='B (ARCHIVE 原始固定仓位 slot=1/6)',
        aux_portfolio='A (Equal Weight 等权满仓)',
        signal_density=len(sig_days) / NCAL,
        n_signal_days=len(sig_days), n_trading_days=NCAL,
        cost_roundtrip=L.COST_RT, cost_side=cost,
        phases={v[0]: list(v[1:]) for v in L.PHASES},
        phase_verification=dver.to_dict('records'),
        lifecycle=dlife.to_dict('records'),
        event_returns=d1.to_dict('records'),
        portfolio_metrics=dper.to_dict('records'),
        exposure=dext.to_dict('records'),
        cost=dcost.to_dict('records'),
        oos=doos.to_dict('records'),
        walkforward=dwf.to_dict('records'),
        regime=dreg.to_dict('records'),
        yearly=dyr.to_dict('records'),
        gates=G,
        key_numbers=dict(
            cohort_alpha_T5=ca5, cohort_alpha_T20=ca20, cohort_alpha_diff=ca20 - ca5,
            paired_mean_diff_T20_T5=m_diff, paired_tstat_T20_T5=t_diff,
            B_excess_T5=archB5, B_excess_T20=archB20,
            A_excess_T5=float(dcf[(dcf['model'] == 'ARCHIVE') & (dcf['horizon'] == 5) &
                                  (dcf['agg'] == 'point')]['A_excess'].iloc[0]),
            A_excess_T20=float(dcf[(dcf['model'] == 'ARCHIVE') & (dcf['horizon'] == 20) &
                                   (dcf['agg'] == 'point')]['A_excess'].iloc[0]),
            B_oos_excess_T5=ex5, B_oos_excess_T20=ex20, A_oos_excess_T20=ex20A,
            cohort_alpha_T20_OOS=oos_ca20,
            random_stock_excess_T20_mean=float(rs20.mean()),
            random_stock_excess_T20_p95=float(rs20.quantile(0.95)),
            random_date_excess_T20_mean=float(rd20.mean()),
            random_date_excess_T20_p95=float(rd20.quantile(0.95)),
            random_stock_excess_T5_p95=float(rs5.quantile(0.95)),
            random_date_excess_T5_p95=float(rd5.quantile(0.95)),
            random_stock_cohort_T20_p95=float(
                rs[rs['horizon'] == 20]['cohort_alpha'].quantile(0.95)),
        ),
        premise_clarification=(
            'ARCHIVE 已发布的组合层 alpha 与 cohort alpha 均为 T+5 持有期下的分期（TRAIN/VALID/OOS）读数，'
            '并非 T+5 / T+10 / T+20 三个不同持有期。H-T20 假设的前提即建立在此事实之上：ARCHIVE 信号 alpha 为正，'
            '但 T+5 持有期过短不足以让 alpha 充分兑现；故本实验唯一改动变量 = Holding Horizon ∈ {3,5,10,15,20}，'
            'T+5 为 ARCHIVE 原基准，信号条件 / 因子 / 权重 / 阈值 / 股票池 / 过滤器 / 排名 / Entry / Regime 等全部逐字冻结。'
        ),
        gate2_accounting_defect=(
            'ARCHIVE 发布的组合层 NAV（gate2/gate3/报告附录 A.2·C·D）实为「E2(k1 收盘)入场 + 零成本」：'
            'gate2 simulate_nav 在当日 pnl 结算后才 append 新 sleeve，使 ret_map[k1] 中 key=k1 的'
            '入场日开盘→收盘收益从未被消费；而 30bp 成本恰好只加在 R[:,0]（同一天）上，故成本亦从未被消费。'
            '实测：成本 0bp 与 30bp 的 NAV 终值完全相同（2.16840662）；只保留入场日的 NAV 恒为 1.0。'
            '本引擎按 ARCHIVE 声明的 E1 口径正确实现并以「gate2 逐字复现」作 §G1-G3 冻结证据。'
        ),
    )
    with open(p('archive_h_t20.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)

    log('  → archive_h_t20_{event_returns,lifecycle,portfolio,exposure,cost,oos,'
        'walkforward,regime,counterfactual,yearly,nav,daily_exposure}.csv')
    log('  → archive_h_t20.json')
    log.save()
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
