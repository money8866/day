# -*- coding: utf-8 -*-
"""
ER20 V2.2 TDX 回测 — 2025H1 中报季
=====================================
样本: 2025 中报(end_date=20250630) 已披露事件(ann 2025-07~10), 随机抽样 N=400(seed=42)
扫描: 公告后第 T+5 / T+10 交易日(双时点) 按 V2.2 全链评分
      防未来函数: 每个扫描时点 daily 切片到扫描日(≤扫描日), 未来行情仅用于收益统计
未来: 扫描日后 T+5/10/20 收益 + 上证(000001)同期超额
分层: grade / Alpha分位 / Event / Decay / Trigger / 扫描时点
数据: 行情=TDX(C:\\new_tdx\\vipdoc) via bts.data; 财务=treasure_fin_ind 全历史缓存
      主题加分在回测中禁用(当前主题快照有前视偏差, theme_adj=0)
用法: python -X utf8 er20_v22_backtest_tdx.py [--n 400] [--seed 42]
输出: report_daily/er20_v22_backtest_2025H1.csv
"""
import os, sys, glob, argparse, time
import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
sys.path.insert(0, os.path.join(SOLO_DIR, 'multi_factor_picker'))
sys.path.insert(0, os.path.join(SOLO_DIR, 'etf_alpha_ranking'))

CACHE_DIR = r'D:\mystock\cache_daily'
REPORT_DIR = r'D:\mystock\solo\report_daily'
TDX_PATH = r'C:\new_tdx\vipdoc'

from er20_v2 import (
    ER20Config, _valid, classify_event, fundamental_quality, calc_rqs,
    expect_gap_score, overheat_penalty, calc_ars, trend_structure,
    volume_structure, pullback_quality, calc_tqs, risk_score,
    market_multiplier, load_daily_for,
)
from er20_strategy import _calc_q2_single
from er20_v21 import (
    _get_industry_map, calc_event_age_tradingdays, relative_risk_score,
    earnings_quality_context, data_confidence_v21, _precompute_industry_atr,
)
from er20_v22 import (
    V22Config, cashflow_context_engine_v22, price_absorption,
    calc_alpha_decay_v22, trigger_score_v22, calc_ees_v22, grade_v22,
)
from er20_inst import load_northbound_snapshot, inst_adj_score
from bts.data import load_daily, parse_tdx_day_file, load_stock_basic
from bts.indicators import add_ma, add_rsi

PERIOD = '20250630'
Q1_PERIOD = '20250331'
ANN_LO, ANN_HI = '20250701', '20251031'
MARKS = (5, 10)   # 公告后扫描时点（交易日）


def load_hist_pool():
    """2025H1 中报事件池：treasure 全历史缓存，取首次披露锚点"""
    files = sorted(glob.glob(os.path.join(CACHE_DIR, 'treasure_fin_ind_*.parquet')))
    rows = []
    for fp in files:
        try:
            df = pd.read_parquet(fp)
        except Exception:
            continue
        h = df[df['end_date'] == PERIOD]
        if h.empty:
            continue
        rec = h.sort_values('ann_date').iloc[0].copy()
        ann = str(rec.get('ann_date', ''))
        if not (ANN_LO <= ann <= ANN_HI):
            continue
        q1 = df[df['end_date'] == Q1_PERIOD]
        rec['q1_profit_yoy'] = q1.sort_values('ann_date')['netprofit_yoy'].iloc[0] if not q1.empty else np.nan
        rec['q2_proxy'] = False
        rows.append(rec)
    pool = pd.DataFrame(rows)
    if pool.empty:
        return pool
    basic = load_stock_basic()
    nm = dict(zip(basic['ts_code'], basic['name'])) if basic is not None else {}
    pool['name'] = pool.get('name', pd.Series('', index=pool.index)).fillna('')
    miss_nm = pool['name'].astype(str).str.strip() == ''
    pool.loc[miss_nm, 'name'] = pool.loc[miss_nm, 'ts_code'].map(nm)
    pool = pool[~pool['ts_code'].astype(str).str.endswith('.BJ')]
    pool = pool[~pool['name'].astype(str).str.startswith(('*ST', 'ST'))]
    q2s, prox = [], []
    for _, r in pool.iterrows():
        q2, pr = _calc_q2_single(r['ts_code'], PERIOD, r)
        q2s.append(q2)
        prox.append(pr)
    pool['q2_profit_yoy'] = q2s
    pool['q2_proxy'] = prox
    pool = pool[pool['q2_profit_yoy'].notna() | pool['netprofit_yoy'].notna()].copy()
    return pool.reset_index(drop=True)


def load_daily_full(code):
    """全序列（到最新），供双时点评分 + 未来收益；防未来由调用处切片保证"""
    try:
        daily = load_daily(code, '20991231', lookback_bars=800)
    except Exception:
        return None
    if daily is None or len(daily) < 120:
        return None
    daily = daily.reset_index(drop=True)
    daily = add_ma(daily)
    if 'rsi' not in daily.columns:
        daily = add_rsi(daily)
    if 'pct_chg' not in daily.columns:
        daily['pct_chg'] = daily['close'].pct_change() * 100.0
    daily['pct_chg'] = daily['pct_chg'].fillna(0.0)
    return daily


def load_bench_full():
    for p in (os.path.join(TDX_PATH, 'sh', 'lday', 'sh000001.day'),
              os.path.join(TDX_PATH, 'vipdoc', 'sh', 'lday', 'sh000001.day')):
        idx = parse_tdx_day_file(p)
        if idx is not None and len(idx):
            return idx.reset_index(drop=True)
    return None


def score_one(r, daily, ann_idx, s_idx, scan_date, bench, industry_atr, market_mult):
    """在扫描日 s_idx 处按 V2.2 全链评分（daily 已切片到 s_idx）"""
    if 'date' not in bench.columns:
        bench = bench.copy()
        bench['date'] = bench['trade_date'].astype(str)
    if 'pct_chg' not in bench.columns:
        bench = bench.copy()
        bench['pct_chg'] = bench['close'].astype(float).pct_change() * 100.0
    missing = []
    code = r['ts_code']
    strategy, cls_reason = classify_event(r, daily, ann_idx, s_idx)
    fq = fundamental_quality(r, missing)
    gap_s, _ = expect_gap_score(r, daily, ann_idx)
    ars, _ = calc_ars(daily, ann_idx, s_idx, bench)
    risk_v2 = risk_score(r, daily, s_idx, ann_idx)
    overheat = overheat_penalty(r, daily, ann_idx, s_idx)
    cfcs, cf_label, cf_adj, cf_reason = cashflow_context_engine_v22(r, strategy, daily, ann_idx, s_idx)
    eq_label, eq_penalty, eq_detail = earnings_quality_context(r)
    ind = _get_industry_map().get(code, '')
    benchmark_vol = industry_atr.get(ind, None)
    rel_risk = relative_risk_score(r, daily, s_idx, ann_idx, benchmark_vol)
    conf = data_confidence_v21(r, daily, ann_idx, missing, cf_label, eq_label)
    pqs = pullback_quality(daily, s_idx, ann_idx, missing)
    trend = trend_structure(daily, s_idx, missing)
    volume = volume_structure(daily, s_idx, missing)
    rqs = calc_rqs(r, missing) if strategy == 'B_REVERSAL' else None
    tqs = calc_tqs(daily, s_idx, ann_idx, missing) if strategy == 'B_REVERSAL' else None

    event_age = calc_event_age_tradingdays(str(r['ann_date']), scan_date)
    ab = price_absorption(daily, ann_idx, s_idx, event_age, bench)
    decay, refresh, mult, decay_state = calc_alpha_decay_v22(daily, ann_idx, s_idx, event_age, ab)
    ts, ttype, tdesc = trigger_score_v22(daily, s_idx)
    ees = calc_ees_v22(trend, ts, volume, pqs, overheat)

    wmap = ER20Config.W_B if strategy == 'B_REVERSAL' else ER20Config.W_A
    parts = {}
    if strategy == 'B_REVERSAL':
        parts = {'rqs': rqs, 'fq': fq, 'gap': gap_s, 'ars': ars, 'tqs': tqs, 'risk': rel_risk}
    else:
        parts = {'fq': fq, 'gap': gap_s, 'ars': ars, 'pqs': pqs, 'trend': trend, 'risk': rel_risk}
    wsum, ssum = 0.0, 0.0
    for k, wgt in wmap.items():
        v = parts.get(k)
        if _valid(v):
            wsum += wgt
            ssum += wgt * float(v)
        else:
            missing.append(k)
    raw = ssum / wsum if wsum > 0 else np.nan

    row = {
        'ts_code': code, 'name': r.get('name', ''), 'ann_date': str(r['ann_date']),
        'scan_mark': s_idx - ann_idx + 1, 'scan_date': scan_date, 'gap': s_idx - ann_idx + 1,
        'strategy': strategy, 'cls_reason': cls_reason,
        'raw': raw, 'fq': fq, 'rqs': rqs, 'gap_s': gap_s, 'ars': ars,
        'pqs': pqs, 'trend': trend, 'tqs': tqs, 'volume': volume,
        'risk_v2': risk_v2, 'rel_risk': rel_risk, 'overheat': overheat,
        'conf': conf, 'cfcs': cfcs, 'cf_label': cf_label, 'cf_adj': cf_adj,
        'eq_label': eq_label, 'eq_penalty': eq_penalty,
        'post_ret': round(ab['post_ret'] * 100, 2),
        'max_ret': round(ab['max_ret'] * 100, 2),
        'drawdown': round(ab['drawdown'] * 100, 2),
        'rel_str': round(ab['rel_str'] * 100, 2) if ab['rel_str'] is not None else None,
        'vol_struct': round(ab['vol_struct'], 2),
        'pre_ret': round(ab['pre_ret'] * 100, 2), 'pre_priced': ab['pre_priced'],
        'decay_factor': decay, 'alpha_refresh': refresh, 'decay_state': decay_state,
        'ts': ts, 'ttype': ttype, 'tdesc': tdesc, 'ees': ees,
        'dt_netprofit_yoy': r.get('dt_netprofit_yoy'), 'tr_yoy': r.get('tr_yoy'),
    }
    return row


def fwd_returns(daily, s_idx, bench_full, scan_date):
    out = {}
    base = float(daily.iloc[s_idx]['close'])
    for h in (5, 10, 20):
        if s_idx + h < len(daily):
            out[f'fwd{h}'] = round((float(daily.iloc[s_idx + h]['close']) / base - 1) * 100, 2)
        else:
            out[f'fwd{h}'] = np.nan
    # ── -8% 止损模拟：持有至 T+20，期间收盘价较买入价回撤≥8% 则按 -8% 止损 ──
    STOP = 0.08
    stop_price = base * (1 - STOP)
    stop_day = None
    for h in range(1, 21):
        if s_idx + h >= len(daily):
            break
        if float(daily.iloc[s_idx + h]['close']) <= stop_price:
            stop_day = h
            break
    if stop_day is not None:
        out['fwd20_stop'] = round(-STOP * 100, 2)
        out['stop_day'] = stop_day
    elif pd.notna(out.get('fwd20')):
        out['fwd20_stop'] = out['fwd20']
        out['stop_day'] = 0
    else:
        out['fwd20_stop'] = np.nan
        out['stop_day'] = np.nan
    b = bench_full[bench_full['trade_date'] == str(scan_date)]
    if not b.empty:
        bi = b.index[0]
        if bi + 20 < len(bench_full):
            bbase = float(bench_full.iloc[bi]['close'])
            b20 = (float(bench_full.iloc[bi + 20]['close']) / bbase - 1) * 100
            for h in (5, 10, 20):
                x = out.get(f'fwd{h}')
                if pd.notna(x):
                    out[f'fwd{h}x'] = round(x - b20 if h == 20 else
                                            x - (float(bench_full.iloc[bi + h]['close']) / bbase - 1) * 100, 2)
            if pd.notna(out.get('fwd20_stop')):
                out['fwd20_stop_x'] = round(out['fwd20_stop'] - b20, 2)
    return out


def summarize(bt, tag, group_col, key_cols=('fwd5x', 'fwd10x', 'fwd20x')):
    print(f'\n── {tag} ──')
    if group_col is None:
        sub = bt
        parts = [f'n={len(sub)}']
        for k in key_cols:
            s = sub[k].dropna()
            if s.empty:
                continue
            parts.append(f'{k}:均值{s.mean():+.2f}% 胜率{(s > 0).mean() * 100:.0f}% 中位{s.median():+.2f}%')
        print(' | '.join(parts))
        return
    for gname, sub in bt.groupby(group_col, dropna=False):
        parts = [f'{str(gname)[:16]} n={len(sub)}']
        for k in key_cols:
            s = sub[k].dropna()
            if s.empty:
                continue
            parts.append(f'{k[:5]} {s.mean():+.2f}%/{((s > 0).mean() * 100):.0f}%')
        print(' | '.join(parts))


def grade_variant(row, vcfg):
    """按变体配置分级：t1_cap 拦截 T1 买入；gate 覆盖门槛后恢复；alpha_col 选择带/不带机构加分"""
    g = V22Config.GATE
    saved = {}
    for k, v in vcfg.get('gate', {}).items():
        saved[k] = g[k]
        g[k] = v
    try:
        st, rs = grade_v22(row[vcfg.get('alpha_col', 'alpha')], row['ees'], row['ts'], row['ttype'],
                           row['rel_risk'], row['overheat'], row['conf'], row['fq'],
                           row['rqs'], row['strategy'], row['cf_label'], row['eq_label'], [])
    finally:
        g.update(saved)
    # t1_cap：T1_BREAKOUT 一律不得买入（最高 WAIT_PULLBACK）
    if vcfg.get('t1_cap') and row['ttype'] == 'T1_BREAKOUT' \
            and st in ('CORE_BUY', 'TEST_BUY', 'PROBE_BUY'):
        st = 'WAIT_PULLBACK'
        rs = 'T1追突破受限→等回踩'
    return st, rs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=400, help='每时点抽样事件数')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    t0 = time.time()
    pool = load_hist_pool()
    print(f'[回测] 2025H1 事件池: {len(pool)} 只 (去ST/北交所, ann 20250701~20251031)')
    if pool.empty:
        return
    pool = pool.sample(min(args.n, len(pool)), random_state=args.seed)
    print(f'抽样 {len(pool)} 只 (seed={args.seed})')

    bench_full = load_bench_full()
    if bench_full is None:
        print('基准数据缺失'); return

    # ── 北向机构加分（2025H1 扫描窗口前最近季末快照 20250630，无前视） ──
    north_map = {}
    try:
        north_map = load_northbound_snapshot('20250630')
        print(f'[机构加分] 北向快照 20250630: {len(north_map)} 只')
    except Exception as e:
        print(f'[机构加分] 北向快照加载失败，跳过: {e}')

    rows = []
    daily_cache = {}
    industry_atr_cache = {}
    for i, (_, r) in enumerate(pool.iterrows()):
        code = r['ts_code']
        ann = str(r['ann_date'])
        if code in daily_cache:
            daily = daily_cache[code]
        else:
            daily = load_daily_full(code)
            daily_cache[code] = daily
        if daily is None:
            continue
        nxt = daily[daily['trade_date'] > ann]
        if nxt.empty:
            continue
        ann_idx = nxt.index[0]
        for mark in MARKS:
            s_idx = ann_idx + mark - 1
            if s_idx >= len(daily) or s_idx + 20 >= len(daily):
                continue
            scan_date = str(daily.iloc[s_idx]['trade_date'])
            if scan_date not in industry_atr_cache:
                industry_atr_cache[scan_date] = _precompute_industry_atr(pool, scan_date)
            industry_atr = industry_atr_cache[scan_date]
            market_mult = market_multiplier(scan_date)[1]
            bench = bench_full[bench_full['trade_date'] <= scan_date].reset_index(drop=True)
            d = daily.iloc[:s_idx + 1].reset_index(drop=True)
            row = score_one(r, d, ann_idx, s_idx, scan_date, bench, industry_atr, market_mult)
            row.update(fwd_returns(daily, s_idx, bench_full, scan_date))
            row['market_mult'] = market_mult
            rows.append(row)
        if (i + 1) % 100 == 0:
            print(f'  已处理 {i + 1}/{len(pool)} 事件, {time.time() - t0:.0f}s')

    if not rows:
        print('无回测样本'); return
    bt = pd.DataFrame(rows)

    # ── norm: 按(扫描时点, 策略)组内百分位 ──
    bt['norm'] = np.nan
    for (m, strat), grp in bt.groupby(['scan_mark', 'strategy']):
        if len(grp) >= 3:
            bt.loc[(bt['scan_mark'] == m) & (bt['strategy'] == strat), 'norm'] = grp['raw'].rank(pct=True) * 100.0
        else:
            bt.loc[(bt['scan_mark'] == m) & (bt['strategy'] == strat), 'norm'] = grp['raw'].clip(0, 100)

    conf_mult = bt['conf'].astype(float) / 100.0
    risk_mult = 1.0 - (bt['rel_risk'].astype(float) / 100.0) * ER20Config.RISK_PEN
    bt['er20_base'] = (bt['norm'] * conf_mult * risk_mult * bt['market_mult']
                       + bt['cf_adj'].fillna(0.0)).round(1)
    decay_mult = bt['decay_factor'].map(lambda x: max(0.60, min(1.00, 1.0 - x)))
    bt['alpha'] = (bt['er20_base'] * decay_mult + bt['alpha_refresh'].fillna(0.0)
                   - bt['eq_penalty'].fillna(0.0)).round(1).clip(0, 100)
    bt['rank_eligible'] = bt['strategy'] != 'D_FALSE_SIGNAL'

    # ── 机构加分：北向 ratio → inst_adj 软加分，重算 er20_base/alpha ──
    if north_map:
        bt['north_ratio'] = bt['ts_code'].map(north_map).fillna(0.0)
        bt['inst_adj'] = bt['north_ratio'].map(inst_adj_score).round(1)
        bt['er20_base_inst'] = (bt['norm'] * conf_mult * risk_mult * bt['market_mult']
                                + bt['cf_adj'].fillna(0.0) + bt['inst_adj']).round(1)
        bt['alpha_inst'] = (bt['er20_base_inst'] * decay_mult + bt['alpha_refresh'].fillna(0.0)
                            - bt['eq_penalty'].fillna(0.0)).round(1).clip(0, 100)
    else:
        bt['north_ratio'] = 0.0
        bt['inst_adj'] = 0.0
        bt['er20_base_inst'] = bt['er20_base']
        bt['alpha_inst'] = bt['alpha']

    # ── 规则变体：同一评分、不同分级门槛与触发限制 ──
    VARIANTS = {
        'base':   {'t1_cap': False, 'gate': {}},
        't1_cap': {'t1_cap': True,  'gate': {}},
        'gate_up': {'t1_cap': False, 'gate': {'test_alpha': 80.0, 'test_ees': 72.0,
                                              'test_ts': 72.0, 'probe_alpha': 72.0}},
        'combo':  {'t1_cap': True,  'gate': {'test_alpha': 80.0, 'test_ees': 72.0,
                                             'test_ts': 72.0, 'probe_alpha': 72.0}},
        'inst':   {'t1_cap': False, 'gate': {}, 'alpha_col': 'alpha_inst'},
        'combo_inst': {'t1_cap': True, 'gate': {'test_alpha': 80.0, 'test_ees': 72.0,
                                                'test_ts': 72.0, 'probe_alpha': 72.0},
                       'alpha_col': 'alpha_inst'},
    }
    for vname, vcfg in VARIANTS.items():
        grades, reasons = [], []
        for _, row in bt.iterrows():
            st, rs = grade_variant(row, vcfg)
            grades.append(st)
            reasons.append(rs)
        bt2 = bt.copy()
        bt2['grade'] = grades
        bt2['grade_reason'] = reasons
        csv = os.path.join(REPORT_DIR, f'er20_v22_backtest_2025H1_{vname}.csv')
        bt2.to_csv(csv, index=False, encoding='utf-8-sig')
        print(f'\n[{vname}] 回测明细: {csv} ({len(bt2)} 样本)')
        summarize(bt2, f'[{vname}] 全样本', None)
        for g in ('grade', 'decay_state', 'ttype'):
            summarize(bt2, f'[{vname}] 按 {g}', g)
        # BUY 组
        buy = bt2[bt2['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])]
        print(f'\n[{vname}] BUY 组 n={len(buy)}')
        summarize(buy, f'[{vname}] BUY', None)
        summarize(bt2[~bt2['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])], f'[{vname}] 非BUY', None)
        # 止损对比（仅 BUY 组）
        if not buy.empty:
            s20 = buy['fwd20'].dropna()
            stp = buy['fwd20_stop'].dropna()
            print(f'[{vname}] BUY 止损对比: 持有T+20 超额均值{s20.mean():+.2f}% 胜率{(s20>0).mean()*100:.0f}%'
                   f' | -8%止损后 超额均值{stp.mean():+.2f}% 胜率{(stp>0).mean()*100:.0f}% 触发率{(buy["stop_day"]>0).mean()*100:.0f}%')

    print(f'\n[总样本] 用时 {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
