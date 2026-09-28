# -*- coding: utf-8 -*-
"""TL-01 §38 成本 / §39 Exposure / §41–§44 组合模拟 / §45 右尾 / §46 删除超级赢家

交易口径（§41/§42 纪律）
  Signal at Close T  ->  Earliest execution T+1
  面板字段 exe_h[t] = Cq[t+h+1] / Oq[t+1]（价格倍数），即「T+1 开盘买入、T+h+1 收盘卖出」，
  已经天然满足 T+1 成交约束；买入可行性用 buy_ok[t]（T+1 开盘未涨停/未停牌/有成交）过滤。
  注意：tl_build 落盘的 exe_h 是价格倍数（未减 1），本脚本加载后统一 -1.0 转为收益率。
  非重叠建仓：每 STEP = h+1 个交易日换仓一次，cohort 之间不重叠 -> 换手率口径干净。

组合（§43 仅报告，不回头改信号）
  P10 / P20 / P30 : SIG_TRANS 排序取前 10% / 20% / 30%，等权
  P10_RW          : 前 10% 风险权重（1/RealizedVol_20 归一）
  P10_TREND       : 仅趋势状态 S1..S8 内取前 10%，等权（用于观察 Exposure < 1 的情形）

§38 成本：0 / 10 / 20 / 30 / 50 bp（单边），每次换仓 = 100% 卖出 + 100% 买入 = 2 倍单边成本
§39 同时报告 Signal Frequency / Exposure / Turnover，禁止只看 Portfolio Return
§45 右尾：全部 (cohort, stock) 收益中 Top1% / Top5% / Top10% 对总 PnL 的贡献
§46 删除超级赢家：每个 cohort 内剔除收益最高的 1% / 5% 后重算，判定 EXTREME_TAIL_DEPENDENT

产出：tl01_portfolio.csv、tl01_cost.csv、_tl_portfolio_run.txt
"""
import os
import time
import numpy as np
import pandas as pd

from tl_common import (HERE, PREREG, Log, panel_meta, pget, ic_stats, phase_of,
                       daily_ic, qstats)

H = int(PREREG['primary_horizon'])
STEP = H + 1
COSTS = list(PREREG['cost_bps'])
PCTS = list(PREREG['pf_pct'])
PHASES = [p[0] for p in PREREG['phases']]
TDAY = 243.0
MIN_POS = 10


def fit_trans_map(frm, to, y, valid, min_n=30):
    ps = np.zeros((11, 11))
    cn = np.zeros((11, 11))
    m = valid & (frm >= 0) & (to >= 0) & np.isfinite(y)
    np.add.at(ps, (frm[m], to[m]), y[m].astype(np.float64))
    np.add.at(cn, (frm[m], to[m]), 1.0)
    mp = np.where(cn >= min_n, ps / np.maximum(cn, 1.0), np.nan)
    s1 = np.zeros(11)
    c1 = np.zeros(11)
    m2 = valid & (to >= 0) & np.isfinite(y)
    np.add.at(s1, to[m2], y[m2].astype(np.float64))
    np.add.at(c1, to[m2], 1.0)
    stm = np.where(c1 > 0, s1 / np.maximum(c1, 1.0), np.nan)
    gm = float(np.mean(y[m2])) if m2.sum() else 0.0
    for a in range(11):
        for b in range(11):
            if not np.isfinite(mp[a, b]):
                mp[a, b] = stm[b] if np.isfinite(stm[b]) else gm
    return mp


def metrics(ret, cps, exposure, turnover, ic_mean, icir):
    """ret: 每 cohort 净收益（已扣成本）; cps: cohort/年"""
    r = np.asarray(ret, dtype=np.float64)
    r = r[np.isfinite(r)]
    n = len(r)
    out = dict(n_cohort=n, exposure=exposure, turnover=turnover,
               ic=ic_mean, icir=icir)
    if n < 3:
        out.update(ann=np.nan, vol_ann=np.nan, sharpe=np.nan, sortino=np.nan,
                   maxdd=np.nan, calmar=np.nan, winrate=np.nan, cum=np.nan)
        return out
    eq = np.cumprod(1.0 + r)
    yrs = n / cps if cps > 0 else np.nan
    ann = float(eq[-1] ** (1.0 / yrs) - 1.0) if yrs and yrs > 0 else np.nan
    sd = float(r.std(ddof=1))
    dn = r[r < 0]
    dsd = float(dn.std(ddof=1)) if len(dn) > 1 else np.nan
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1.0).min())
    out.update(ann=ann, vol_ann=sd * np.sqrt(cps),
               sharpe=(float(r.mean()) / sd * np.sqrt(cps)) if sd > 1e-12 else np.nan,
               sortino=(float(r.mean()) / dsd * np.sqrt(cps)) if dsd and dsd > 1e-12 else np.nan,
               maxdd=mdd, calmar=(ann / abs(mdd)) if mdd and abs(mdd) > 1e-12 else np.nan,
               winrate=float((r > 0).mean()), cum=float(eq[-1] - 1.0))
    return out


def main():
    t0 = time.time()
    log = Log('_tl_portfolio_run.txt')
    log('=' * 78)
    log('TL-01 §38/§39/§43–§46 组合模拟（T+1 开盘买入，非重叠 %d 日换仓）' % STEP)

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    dates = meta['dates']
    ph_d = phase_of(np.array([int(d[:4]) if d else 0 for d in dates], dtype=np.int32))
    ST = np.asarray(pget('state'))
    SP = np.asarray(pget('state_prev'))
    VAL = ST >= 0
    EXE = np.asarray(pget('exe_%d' % H)) - 1.0   # 价格倍数 -> 收益率
    FWD = np.asarray(pget('fwd_%d' % H))
    BOK = np.asarray(pget('buy_ok'))
    RV = np.asarray(pget('rv_20'))
    log('  EXE_%d（T+1 开盘 -> T+%d 收盘）mean=%+.4f  p50=%+.4f  样本=%d'
        % (H, H + 1, float(np.nanmean(EXE)), float(np.nanpercentile(EXE, 50)),
           int(np.isfinite(EXE).sum())))
    tr_day = np.broadcast_to((ph_d == 'TRAIN')[:, None], ST.shape)
    mp = fit_trans_map(SP[VAL], ST[VAL], FWD[VAL], tr_day[VAL])
    SIG = np.full(ST.shape, np.nan, dtype=np.float32)
    m = (SP >= 0) & (ST >= 0)
    SIG[m] = mp[SP[m], ST[m]]
    log('  SIG_TRANS 就绪（TRAIN 拟合）%.0fs' % (time.time() - t0))

    # ---------------- 建仓日与 cohort ----------------
    valid_day = np.array([bool((VAL[k] & np.isfinite(SIG[k]) & np.isfinite(EXE[k])
                                & (BOK[k] > 0)).sum() >= MIN_POS)
                          for k in range(NCAL)])
    cal = np.flatnonzero(valid_day)
    first = int(cal[0]) if len(cal) else 0
    cohorts = [k for k in range(first, NCAL - STEP, STEP) if valid_day[k]]
    log('  信号可用日 %d / %d   建仓 cohort %d 个'
        % (int(valid_day.sum()), NCAL, len(cohorts)))

    # 覆盖率（§39 Signal Frequency / Exposure）
    hold = np.zeros(NCAL, dtype=bool)
    for k in cohorts:
        hold[k + 1:min(k + STEP, NCAL)] = True
    freq_all = float(valid_day.mean())
    exp_all = float(hold.mean())

    # ---------------- 逐 cohort 采样 ----------------
    recs = []
    for k in cohorts:
        base = VAL[k] & np.isfinite(SIG[k]) & np.isfinite(EXE[k]) & (BOK[k] > 0)
        idx = np.flatnonzero(base)
        if len(idx) < MIN_POS:
            continue
        s = SIG[k][idx]
        e = EXE[k][idx].astype(np.float64)
        rv = RV[k][idx].astype(np.float64)
        trend = (ST[k][idx] >= 1) & (ST[k][idx] <= 8)
        bench = float(np.nanmean(e))
        rec = dict(k=int(k), phase=ph_d[k], n=int(len(idx)),
                   bench=bench, bench_tr=(float(np.nanmean(e[trend]))
                                          if trend.any() else np.nan))
        order = np.argsort(-s)                       # 由高到低
        for pct in PCTS:
            nsel = max(MIN_POS, int(round(len(idx) * pct)))
            sel = order[:nsel]
            rec['e%d' % int(pct * 100)] = e[sel]
            rec['w%d' % int(pct * 100)] = None
        # 风险权重（1/vol）
        sel10 = order[:max(MIN_POS, int(round(len(idx) * PCTS[0])))]
        w = 1.0 / np.maximum(rv[sel10], 1e-4)
        rec['w10'] = w / w.sum()
        # 仅趋势状态
        tidx = idx[trend]
        if len(tidx) >= MIN_POS:
            o2 = np.argsort(-SIG[k][tidx])
            n2 = max(MIN_POS, int(round(len(tidx) * PCTS[0])))
            rec['et10'] = EXE[k][tidx[o2[:n2]]].astype(np.float64)
        recs.append(rec)
    log('  有效 cohort %d  %.0fs' % (len(recs), time.time() - t0))

    # SIG_TRANS 的 IC（组合信号本身，§44）
    ic_all = daily_ic(SIG, FWD, VAL)
    st_tr = (ST >= 1) & (ST <= 8)
    ic_tr = daily_ic(SIG, FWD, VAL & st_tr)
    # §41/§42 可执行性检验：以 T+1 开盘可达收益替代 T 收盘口径，看 IC 是否残留
    ic_exe = daily_ic(SIG, EXE, VAL)

    def phase_ic(x, pn):
        r = ic_stats(x[ph_d == pn], H)
        return r['mean_ic'], r['icir']

    log('  §41 可执行性检验（IC：收盘口径 fwd_20 vs T+1 开盘可成交口径 exe_20）')
    for pn in PHASES:
        a = ic_stats(ic_all[ph_d == pn], H)
        b = ic_stats(ic_exe[ph_d == pn], H)
        log('    %-10s close-entry IC=%+.4f (icir %+.3f)   open-entry IC=%+.4f (icir %+.3f)'
            % (pn, a['mean_ic'], a['icir'], b['mean_ic'], b['icir']))

    # ---------------- 组合汇总 ----------------
    rows = []

    def add(kind, variant, pn, rets, exposure, turnover, icm, icr, bench=None,
            **extra):
        cps = TDAY / STEP
        mt = metrics(rets, cps, exposure, turnover, icm, icr)
        d = dict(kind=kind, variant=variant, phase=pn,
                 ann_ret=mt['ann'], bench_ann=None, excess_ann=None,
                 sharpe=mt['sharpe'], sortino=mt['sortino'], maxdd=mt['maxdd'],
                 calmar=mt['calmar'], winrate=mt['winrate'], cum=mt['cum'],
                 n_cohort=mt['n_cohort'], exposure=mt['exposure'],
                 turnover=mt['turnover'], ic=mt['ic'], icir=mt['icir'],
                 cost_bp=extra.get('cost_bp', 0), avg_pos=extra.get('avg_pos', np.nan))
        if bench is not None:
            bth = np.asarray(bench, dtype=np.float64)
            bth = bth[np.isfinite(bth)]
            if len(bth) >= 3:
                beq = np.cumprod(1.0 + bth)
                byrs = len(bth) / cps
                d['bench_ann'] = float(beq[-1] ** (1.0 / byrs) - 1.0)
                if mt['ann'] is not None and np.isfinite(d['bench_ann']):
                    d['excess_ann'] = mt['ann'] - d['bench_ann']
        rows.append(d)
        return d

    VAR = [('P10_all', 'e10', 'all'), ('P20_all', 'e20', 'all'),
           ('P30_all', 'e30', 'all'), ('P10_riskw', 'rw10', 'all'),
           ('P10_trend', 'et10', 'trend')]
    for pn in PHASES:
        sub = [r for r in recs if r['phase'] == pn]
        if not sub:
            continue
        icm, icr = phase_ic(ic_all, pn)
        icm2, icr2 = phase_ic(ic_tr, pn)
        for name, key, uni in VAR:
            rets, bench = [], []
            for r in sub:
                if key == 'rw10':
                    if r.get('w10') is None:
                        continue
                    x = r['e10']
                    rets.append(float(np.dot(r['w10'], x)))
                else:
                    x = r.get(key)
                    if x is None or len(x) == 0:
                        continue
                    rets.append(float(np.nanmean(x)))
                bench.append(r['bench_tr'] if uni == 'trend' else r['bench'])
            if not rets:
                continue
            expo = float(np.mean([1.0 if len(r[key]) else 0.0 for r in sub
                                  if r.get(key) is not None])) if name != 'P10_trend' else \
                float(np.mean([1.0 if r.get('et10') is not None else 0.0 for r in sub]))
            add('portfolio', name, pn, rets, expo, 1.0,
                icm2 if uni == 'trend' else icm, icr2 if uni == 'trend' else icr,
                bench=bench,
                avg_pos=float(np.mean([r['n'] * (0.10 if name.startswith('P10') else
                                                 0.20 if name.startswith('P20') else 0.30)
                                       for r in sub])))
        # 信号覆盖率：全样本口径落到 TOTAL 行
        add('coverage', 'SIG_TRANS_all', pn, [np.nan], exp_all, 1.0, icm, icr)

    # ---------------- §38 成本敏感 ----------------
    log('-' * 78)
    log('  §38 成本敏感（单边 bp；每次换仓 100% 卖 + 100% 买，成本 = 2 x bp）')
    for pn in PHASES:
        sub = [r for r in recs if r['phase'] == pn]
        if not sub:
            continue
        for name, key in (('P10_all', 'e10'), ('P20_all', 'e20'), ('P30_all', 'e30')):
            gross = [float(np.nanmean(r[key])) for r in sub if r.get(key) is not None]
            bench = [r['bench'] for r in sub if r.get(key) is not None]
            if not gross:
                continue
            line = []
            for bp in COSTS:
                net = [x - 2.0 * bp / 10000.0 for x in gross]
                d = add('cost', name, pn, net, np.nan, 1.0, np.nan, np.nan,
                        bench=bench, cost_bp=bp)
                line.append('%dbp ann=%s/ex=%s' % (
                    bp,
                    ('%.1f%%' % (d['ann_ret'] * 100)) if np.isfinite(d['ann_ret']) else 'n/a',
                    ('%.1f%%' % (d['excess_ann'] * 100))
                    if d['excess_ann'] is not None and np.isfinite(d['excess_ann']) else 'n/a'))
            log('    %-6s %-10s %s' % (pn, name, '  '.join(line)))

    # ---------------- §45 右尾 / §46 删除超级赢家 ----------------
    log('-' * 78)
    log('  §45 右尾贡献 / §46 删除超级赢家（P10_all，全部 cohort 内个股）')
    pool = np.concatenate([r['e10'] for r in recs if r.get('e10') is not None])
    pool = pool[np.isfinite(pool)]
    tot = float(pool.sum())
    tail = {}
    for pct in (0.01, 0.05, 0.10):
        cut = np.percentile(pool, 100 * (1 - pct))
        tail[pct] = float(pool[pool >= cut].sum() / tot) if tot else np.nan
    for pn in PHASES:
        sub = [r for r in recs if r['phase'] == pn]
        if not sub:
            continue
        for pct, tag in ((0.01, 'leaveout_top1%'), (0.05, 'leaveout_top5%')):
            red = []
            for r in sub:
                x = r.get('e10')
                if x is None or not len(x):
                    continue
                ncut = max(1, int(np.ceil(len(x) * pct)))
                xs = np.sort(x)[:-ncut]
                if len(xs):
                    red.append(float(np.nanmean(xs)))
            if red:
                gm = [float(np.nanmean(r['e10'])) for r in sub if r.get('e10') is not None]
                d = add('leaveout', tag, pn, red, np.nan, 1.0, np.nan, np.nan)
                log('    %-10s %-14s 剩余 ann=%s  (原始 ann=%s)'
                    % (pn, tag, ('%.1f%%' % (d['ann_ret'] * 100))
                       if np.isfinite(d['ann_ret']) else 'n/a',
                       ('%.1f%%' % (metrics(gm, TDAY / STEP, np.nan, 1.0, np.nan,
                                            np.nan)['ann'] * 100))))
    for pct, v in tail.items():
        rows.append(dict(kind='tail', variant='P10_all', phase='ALL',
                         ann_ret=np.nan, sharpe=np.nan, sortino=np.nan, maxdd=np.nan,
                         calmar=np.nan, winrate=np.nan,
                         cum=tail[pct], n_cohort=len(pool), exposure=np.nan,
                         turnover=np.nan, ic=np.nan, icir=np.nan, cost_bp=0,
                         avg_pos=np.nan, tag='top%d%%_pnl_share' % (pct * 100)))
        log('    Top%-4s 个股收益占总收益比 = %+.1f%%' % ('%d%%' % (pct * 100), v * 100))

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, 'tl01_portfolio.csv'), index=False,
              encoding='utf-8-sig')
    log('  已写 tl01_portfolio.csv')

    cost = df[(df['kind'] == 'cost')]
    cost.to_csv(os.path.join(HERE, 'tl01_cost.csv'), index=False,
                encoding='utf-8-sig')
    log('  已写 tl01_cost.csv')
    # 主口径（P10_all）净收益概览
    log('  P10_all 分期（扣 30bp）:')
    for pn in PHASES:
        s = cost[(cost['phase'] == pn) & (cost['variant'] == 'P10_all')
                 & (cost['cost_bp'] == 30)]
        if len(s):
            r = s.iloc[0]
            log('    %-10s ann=%s  bench=%s  excess=%s  sharpe=%s  maxdd=%s  '
                'winrate=%s  cohorts=%d'
                % (pn, ('%.1f%%' % (r['ann_ret'] * 100)) if np.isfinite(r['ann_ret']) else 'n/a',
                   ('%.1f%%' % (r['bench_ann'] * 100)) if np.isfinite(r['bench_ann']) else 'n/a',
                   ('%.1f%%' % (r['excess_ann'] * 100)) if np.isfinite(r['excess_ann']) else 'n/a',
                   ('%.2f' % r['sharpe']) if np.isfinite(r['sharpe']) else 'n/a',
                   ('%.1f%%' % (r['maxdd'] * 100)) if np.isfinite(r['maxdd']) else 'n/a',
                   ('%.2f' % r['winrate']) if np.isfinite(r['winrate']) else 'n/a',
                   int(r['n_cohort'])))
    log('  信号覆盖率：可用日占比 %.1f%%   持仓覆盖率 %.1f%%'
        % (freq_all * 100, exp_all * 100))
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
