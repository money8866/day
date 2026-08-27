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
    next5_score_v22, calc_september_opportunity,
)
from er20_inst import load_northbound_snapshot, inst_adj_score
from bts.data import load_daily, parse_tdx_day_file, load_stock_basic
from bts.indicators import add_ma, add_rsi
import datetime as _dt
import bts.data as _bts_data
import er20_v2 as _er20_v2_mod
import er20_v21 as _er20_v21_mod

SEASONS = {
    '2025H1': {'period': '20250630', 'q1': '20250331', 'lo': '20250701', 'hi': '20251031', 'north': '20250630', 'fwd': 20},
    '2026H1': {'period': '20260630', 'q1': '20260331', 'lo': '20260701', 'hi': '20261231', 'north': None, 'fwd': 6},
}
PERIOD = SEASONS['2025H1']['period']
Q1_PERIOD = SEASONS['2025H1']['q1']
ANN_LO, ANN_HI = SEASONS['2025H1']['lo'], SEASONS['2025H1']['hi']
MARKS = (5, 10)   # 公告后扫描时点（交易日）

# ── tushare 增量日线补丁：本地 TDX 尾端之后的数据，纯内存拼接不落盘 .day ──
_BASE_COLS = ['trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount', 'pre_close', 'pct_chg']
TS_DELTA_PATH = os.path.join(CACHE_DIR, 'ts_daily_delta_20260827.parquet')
TS_IDX_PATH = os.path.join(CACHE_DIR, 'ts_idx_delta_20260827.parquet')
TS_BENCH_CODE = '000001.SH'
_ts_delta = None
_ts_idx_delta = None


UNIFIED_POOL = {
    '20250630': 'fin_ind_2025H1_full.parquet',
    '20260630': 'fin_ind_2026H1_full.parquet',
}


def _tdx_tail_date():
    """本地上证指数最后一根K线日期 = TDX 数据末端"""
    idx = parse_tdx_day_file(os.path.join(TDX_PATH, 'sh', 'lday', 'sh000001.day'))
    if idx is not None and len(idx):
        return str(idx['trade_date'].iloc[-1])
    return ''


def _verify_overlap_unit(pro):
    """重叠日(TDX末端)逐只精确对齐校验 tushare vs 本地 TDX 的单位口径"""
    from bts.data import ts_code_to_tdx_file
    checks, warns = [], []
    for code in ('600519.SH', '000001.SZ', '600829.SH'):
        fp = ts_code_to_tdx_file(code)
        loc = parse_tdx_day_file(fp) if fp else None
        if loc is None or not len(loc):
            continue
        last = str(loc['trade_date'].iloc[-1])
        try:
            df = pro.daily(ts_code=code, start_date=last, end_date=last)
        except Exception as e:
            warns.append(f'{code} API失败({e})')
            continue
        if df is None or df.empty:
            continue
        tr = df.iloc[0]
        lc, lv, la = float(loc.iloc[-1]['close']), float(loc.iloc[-1]['vol']), float(loc.iloc[-1]['amount'])
        tc_ = float(tr['close'])
        rv = float(tr['vol']) / lv if lv else float('nan')
        ra = float(tr['amount']) / la if la else float('nan')
        okc = abs(tc_ - lc) < max(0.02, lc * 0.002)
        oka = abs(ra - 1.0) < 0.02 if np.isfinite(ra) else False
        checks.append(okc and oka)
        print(f'[校验] {code}@{last}: close {lc} vs {tc_} {"✓" if okc else "✗"} '
              f'| vol比={rv:.4f} amount比={ra:.4f}')
    if not checks:
        print('[数据补丁] ⚠ 无可校验样本, 请人工核对单位')
        return False
    if all(checks):
        print('[数据补丁] 单位口径一致(vol=手/百股, amount=千元) ✓')
        return True
    print(f'[数据补丁] ⚠ 口径不一致样本 {checks.count(False)}/{len(checks)}, 请人工核对!')
    return False


def ensure_ts_delta(force=False):
    """tushare 拉取 TDX 尾端之后的全市场日线 + 上证指数增量，缓存 parquet；
    单位与 parse_tdx_day_file 对齐(vol=手/百股, amount=千元)，重叠日实测校验"""
    global _ts_delta, _ts_idx_delta
    if _ts_delta is not None and not force:
        return True
    if (not force) and os.path.exists(TS_DELTA_PATH) and os.path.exists(TS_IDX_PATH):
        try:
            _ts_delta = pd.read_parquet(TS_DELTA_PATH)
            _ts_idx_delta = pd.read_parquet(TS_IDX_PATH)
            print(f'[数据补丁] 缓存命中: 个股 {len(_ts_delta)} 行 / 指数 {len(_ts_idx_delta)} 天')
            return True
        except Exception as e:
            print(f'[数据补丁] 缓存读取失败, 重新拉取: {e}')
    tail = _tdx_tail_date()
    if not tail:
        print('[数据补丁] 本地指数缺失, 跳过补丁')
        return False
    try:
        from zhongbao_hunter import _get_pro
        pro = _get_pro()
    except Exception as e:
        print(f'[数据补丁] tushare 不可用({e}), 仅用 TDX 本地数据')
        return False
    start = (_dt.datetime.strptime(tail, '%Y%m%d') + _dt.timedelta(days=1)).strftime('%Y%m%d')
    end = _dt.datetime.now().strftime('%Y%m%d')
    _verify_overlap_unit(pro)
    # ── 逐自然日全市场日线(非交易日返回空) ──
    frames = []
    cur = _dt.datetime.strptime(start, '%Y%m%d')
    endd = _dt.datetime.strptime(end, '%Y%m%d')
    while cur <= endd:
        d = cur.strftime('%Y%m%d')
        try:
            df = pro.daily(trade_date=d)
            if df is not None and len(df):
                frames.append(df)
                print(f'[数据补丁] {d}: {len(df)} 只')
        except Exception as e:
            print(f'[数据补丁] {d} 拉取失败: {e}')
        cur += _dt.timedelta(days=1)
    delta = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    idf = None
    try:
        idf = pro.index_daily(ts_code=TS_BENCH_CODE, start_date=start, end_date=end)
        idf = idf[idf['trade_date'].astype(str) > tail].reset_index(drop=True)
    except Exception as e:
        print(f'[数据补丁] 指数拉取失败: {e}')
    if delta.empty:
        _ts_delta, _ts_idx_delta = pd.DataFrame(), pd.DataFrame()
        return False
    keep = [c for c in ['ts_code'] + _BASE_COLS if c in delta.columns]
    delta = delta[keep].copy()
    for c in keep[2:]:
        delta[c] = pd.to_numeric(delta[c], errors='coerce')
    # pct_chg 兜底重算(基于 pre_close), 避免 NaN
    if 'pct_chg' in delta.columns:
        pc = (delta['close'] / delta['pre_close'] - 1.0) * 100.0
        delta['pct_chg'] = delta['pct_chg'].where(delta['pct_chg'].notna(), pc)
    delta.to_parquet(TS_DELTA_PATH)
    _ts_delta = delta.reset_index(drop=True)
    if idf is not None and len(idf):
        icols = [c for c in _BASE_COLS if c in idf.columns]
        idf = idf[icols].copy()
        for c in icols[1:]:
            idf[c] = pd.to_numeric(idf[c], errors='coerce')
        idf.to_parquet(TS_IDX_PATH)
        _ts_idx_delta = idf.reset_index(drop=True)
    else:
        _ts_idx_delta = pd.DataFrame()
    print(f'[数据补丁] 完成: 个股 {len(_ts_delta)} 行 ({delta["trade_date"].min()}~{delta["trade_date"].max()})'
          f' / 指数 {len(_ts_idx_delta)} 天 → 已缓存')
    return True


def _merge_ts_delta(base, delta):
    """把 tushare 增量行拼接到 TDX 序列尾部(纯内存); 返回新 DataFrame"""
    if base is None or len(base) == 0:
        return base
    if delta is None or delta.empty:
        return base
    have = set(base['trade_date'].astype(str))
    add = delta.copy()
    add['trade_date'] = add['trade_date'].astype(str)
    add = add[~add['trade_date'].isin(have)]
    cols = _BASE_COLS
    missing = [c for c in cols if c not in base.columns or c not in add.columns]
    if add.empty or missing:
        return base
    part = add[cols].copy()
    for c in cols[1:]:
        part[c] = pd.to_numeric(part[c], errors='coerce')
    out = pd.concat([base.copy(), part], ignore_index=True)
    out['trade_date'] = out['trade_date'].astype(str)
    return out.sort_values('trade_date').reset_index(drop=True)


def install_ts_patches():
    """monkeypatch: 让 market_multiplier(er20_v2.load_bench) 与事件年龄(get_trade_dates)
    的数据面同步吃到增量尾部"""
    idx_full = load_bench_full()

    def patched_get_trade_dates(start_date, end_date):
        if idx_full is None or not len(idx_full):
            return []
        return [d for d in idx_full['trade_date'].tolist()
                if str(start_date) <= str(d) <= str(end_date)]

    def patched_load_bench(scan_date):
        key = str(scan_date)
        if key in _er20_v2_mod._BENCH_CACHE:
            return _er20_v2_mod._BENCH_CACHE[key]
        b = None
        if idx_full is not None and len(idx_full):
            b = idx_full[idx_full['trade_date'] <= key].reset_index(drop=True)
        if b is None or len(b) == 0:
            _er20_v2_mod._BENCH_CACHE[key] = None
            return None
        b = b.copy()
        b['pct_chg'] = b['close'].astype(float).pct_change() * 100.0
        b['date'] = b['trade_date'].astype(str)
        _er20_v2_mod._BENCH_CACHE[key] = b
        return b

    _bts_data.get_trade_dates = patched_get_trade_dates
    _er20_v21_mod.get_trade_dates = patched_get_trade_dates
    _er20_v2_mod.load_bench = patched_load_bench
    n = len(idx_full) if idx_full is not None else 0
    print(f'[数据补丁] monkeypatch 生效: 日历+市场档位基准({n}根, 至{_tdx_tail_date()})')


def load_hist_pool():
    """中报事件池：优先统一 parquet(含预计算 q1/q2)，无则回退 treasure 全历史缓存"""
    unified = os.path.join(CACHE_DIR, UNIFIED_POOL.get(str(PERIOD), ''))
    pool = None
    if str(PERIOD) in UNIFIED_POOL and os.path.exists(unified):
        df = pd.read_parquet(unified)
        df = df.loc[:, ~df.columns.duplicated()].copy()
        df['ann_date'] = df['ann_date'].astype(str)
        df = df[(df['ann_date'] >= ANN_LO) & (df['ann_date'] <= ANN_HI)]
        if df['ts_code'].duplicated().any():
            df = df.sort_values('ann_date').drop_duplicates('ts_code', keep='first')
        pool = df.reset_index(drop=True)
    else:
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
    if pool is None or pool.empty:
        return pd.DataFrame()
    pool = pool.loc[:, ~pool.columns.duplicated()].copy()
    pool['ts_code'] = pool['ts_code'].astype(str)
    basic = load_stock_basic()
    nm = dict(zip(basic['ts_code'], basic['name'])) if basic is not None else {}
    pool['name'] = pool.get('name', pd.Series('', index=pool.index)).fillna('')
    miss_nm = pool['name'].astype(str).str.strip() == ''
    pool.loc[miss_nm, 'name'] = pool.loc[miss_nm, 'ts_code'].map(nm)
    pool = pool[~pool['ts_code'].astype(str).str.endswith('.BJ')]
    pool = pool[~pool['name'].astype(str).str.startswith(('*ST', 'ST'))]
    if 'q2_profit_yoy' not in pool.columns:
        pool['q2_profit_yoy'] = np.nan
    if 'q2_proxy' not in pool.columns:
        pool['q2_proxy'] = False
    need = np.flatnonzero(pool['q2_profit_yoy'].isna().to_numpy())
    q2_col = pool.columns.get_loc('q2_profit_yoy')
    proxy_col = pool.columns.get_loc('q2_proxy')
    for pos in need:
        r = pool.iloc[pos]
        code = r['ts_code']
        q2, pr = _calc_q2_single(str(code), PERIOD, r)
        pool.iat[pos, q2_col] = q2
        pool.iat[pos, proxy_col] = pr
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
    daily = _merge_ts_delta(daily, _ts_delta[_ts_delta['ts_code'] == code] if _ts_delta is not None else None)
    daily = daily.reset_index(drop=True)
    daily = add_ma(daily)
    if 'rsi' not in daily.columns:
        daily = add_rsi(daily)
    if 'pct_chg' not in daily.columns:
        daily['pct_chg'] = daily['close'].pct_change() * 100.0
    daily['pct_chg'] = daily['pct_chg'].fillna(0.0)
    return daily


def load_bench_full():
    idx = None
    for p in (os.path.join(TDX_PATH, 'sh', 'lday', 'sh000001.day'),
              os.path.join(TDX_PATH, 'vipdoc', 'sh', 'lday', 'sh000001.day')):
        idx = parse_tdx_day_file(p)
        if idx is not None and len(idx):
            break
    if idx is None or not len(idx):
        return None
    return _merge_ts_delta(idx, _ts_idx_delta)


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
    sep = calc_september_opportunity(
        r, cfcs, cf_label, eq_label, ab['pre_ret'] * 100.0,
        ab['post_ret'] * 100.0, overheat, rel_risk, decay_state,
        ttype, ts, pqs)

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
        'netprofit_yoy': r.get('netprofit_yoy'),
        **sep,
    }
    return row


def egpt_proxy_scores(row):
    """用扫描日前已知的中报增速和技术结构重建 EGPT 质量/回踩分。"""
    def num(value, default=0.0):
        try:
            value = float(value)
            return default if not np.isfinite(value) else value
        except Exception:
            return default

    netprofit = num(row.get('netprofit_yoy'), 0.0)
    dt = num(row.get('dt_netprofit_yoy'), netprofit)
    quality = 50.0
    quality += min(25.0, max(-25.0, netprofit / 10.0))
    quality += min(20.0, max(-20.0, dt / 10.0))
    if num(row.get('cf_adj'), 0.0) > 0:
        quality += 5.0
    elif num(row.get('cf_adj'), 0.0) < -3:
        quality -= 8.0

    pullback = 0.45 * num(row.get('pqs'), 50.0)
    pullback += 0.25 * num(row.get('volume'), 50.0)
    pullback += 0.20 * num(row.get('trend'), 50.0)
    pullback += 0.10 * num(row.get('ees'), 50.0)
    if row.get('ttype') == 'T2_PULLBACK':
        pullback += 8.0
    elif row.get('ttype') == 'T3_RECLAIM':
        pullback += 5.0
    elif row.get('ttype') == 'T1_BREAKOUT':
        pullback -= 8.0
    return round(max(0.0, min(100.0, quality)), 1), round(max(0.0, min(100.0, pullback)), 1)


def fusion_score_v22(row):
    quality, pullback = egpt_proxy_scores(row)
    er20 = float(row.get('next5_score', 0.0) or 0.0)
    execution = 75.0
    if row.get('ttype') == 'T2_PULLBACK':
        execution += 12.0
    elif row.get('ttype') == 'T3_RECLAIM':
        execution += 6.0
    elif row.get('ttype') == 'T1_BREAKOUT':
        execution -= 15.0
    risk = float(row.get('rel_risk', 100.0) or 100.0)
    execution -= max(0.0, risk - 35.0) * 0.35
    score = 0.50 * er20 + 0.25 * quality + 0.15 * pullback + 0.10 * execution
    return round(max(0.0, min(100.0, score)), 1), quality, pullback, int(row.get('ttype') in ('T2_PULLBACK', 'T3_RECLAIM'))


COST = {
    'order_size': 2_000_000,
    'commission': 0.00025,
    'transfer': 0.00001,
    'stamp_sell': 0.0005,
    'k_impact': 0.8,
    'sigma_default': 0.025,
    'spread_tiers': [(50e6, 0.0020), (100e6, 0.0015), (500e6, 0.0010), (float('inf'), 0.0006)],
}


def entry_costs(daily, s_idx):
    """往返交易成本估计：固定费用 + 成交额分层基础滑点 + 平方根冲击(σ·√p)，全部仅用扫描日已知数据。"""
    amt = pd.to_numeric(pd.Series([daily.iloc[s_idx].get('amount')]), errors='coerce').iloc[0]
    if amt is None or not np.isfinite(float(amt)) or float(amt) <= 0:
        tail = daily['amount'].astype(float).iloc[max(0, s_idx - 60):s_idx]
        adv_yuan = float(tail.median() if tail.notna().any() else 30e3) * 1000.0
    else:
        adv_yuan = float(amt) * 1000.0
    for cap, slip in COST['spread_tiers']:
        if adv_yuan < cap:
            spread = slip
            break
    p = min(COST['order_size'] / max(adv_yuan, 1.0), 0.10)
    rets = daily['close'].astype(float).pct_change().iloc[max(0, s_idx - 20):s_idx].dropna()
    sigma = float(rets.std()) if len(rets) >= 10 else COST['sigma_default']
    impact = COST['k_impact'] * sigma * float(np.sqrt(p))
    buy_frac = COST['commission'] + COST['transfer'] + spread + impact
    sell_frac = COST['commission'] + COST['transfer'] + COST['stamp_sell'] + spread + impact
    return buy_frac, sell_frac, adv_yuan, p


def next5_targets(daily, s_idx, bench_full, scan_date):
    out = {
        'next_open_ret': np.nan, 'close5_ret': np.nan, 'max5_ret': np.nan,
        'max5_excess': np.nan, 'max5_drawdown': np.nan,
        'max5_ret_net': np.nan, 'close5_ret_net': np.nan, 'max5_excess_net': np.nan,
        'cost_rt_pct': np.nan, 'participation': np.nan,
        'entry_date': None, 'entry_price': np.nan, 'entry_executable': 0,
    }
    if s_idx + 1 >= len(daily):
        return out
    base = float(daily.iloc[s_idx]['close'])
    entry = daily.iloc[s_idx + 1]
    entry_price = float(entry['open'])
    out['entry_date'] = str(entry['trade_date'])
    out['entry_price'] = entry_price
    out['entry_executable'] = int(entry_price <= base * 1.08)
    out['next_open_ret'] = round((entry_price / base - 1.0) * 100, 2)
    end = min(s_idx + 5, len(daily) - 1)
    window = daily.iloc[s_idx + 1:end + 1]
    if window.empty:
        return out
    highs = window['high'].astype(float)
    lows = window['low'].astype(float)
    closes = window['close'].astype(float)
    out['max5_ret'] = round((highs.max() / entry_price - 1.0) * 100, 2)
    out['max5_drawdown'] = round((lows.min() / entry_price - 1.0) * 100, 2)
    if len(window) >= 5:
        out['close5_ret'] = round((float(window.iloc[4]['close']) / entry_price - 1.0) * 100, 2)
    # ── 成本净额：买入成本抬高入场价、卖出成本折减退出价 ──
    buy_frac, sell_frac, adv_yuan, part = entry_costs(daily, s_idx)
    out['cost_rt_pct'] = round((buy_frac + sell_frac) * 100, 3)
    out['participation'] = round(min(part, 1.0) * 100, 3)
    gmax = float(highs.max()) / entry_price
    out['max5_ret_net'] = round((gmax * (1.0 - sell_frac) / (1.0 + buy_frac) - 1.0) * 100, 2)
    if len(window) >= 5:
        gc5 = float(window.iloc[4]['close']) / entry_price
        out['close5_ret_net'] = round((gc5 * (1.0 - sell_frac) / (1.0 + buy_frac) - 1.0) * 100, 2)
    bench = bench_full[bench_full['trade_date'].astype(str) == str(scan_date)]
    if not bench.empty:
        bi = bench.index[0]
        if bi + 5 < len(bench_full):
            bbase = float(bench_full.iloc[bi]['close'])
            b5 = (float(bench_full.iloc[bi + 5]['close']) / bbase - 1.0) * 100
            out['max5_excess'] = round(out['max5_ret'] - b5, 2)
            out['max5_excess_net'] = round(out['max5_ret_net'] - b5, 2)
    return out


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
        # 超额收益按各自窗口独立计算: 进行中季节没有完整 fwd20 窗口也不能吞掉 fwd5x/fwd10x
        for h in (5, 10, 20):
            x = out.get(f'fwd{h}')
            if pd.notna(x) and bi + h < len(bench_full):
                bh = (float(bench_full.iloc[bi + h]['close'])
                      / float(bench_full.iloc[bi]['close']) - 1) * 100
                out[f'fwd{h}x'] = round(x - bh, 2)
        if pd.notna(out.get('fwd20_stop')) and bi + 20 < len(bench_full):
            b20 = (float(bench_full.iloc[bi + 20]['close'])
                   / float(bench_full.iloc[bi]['close']) - 1) * 100
            out['fwd20_stop_x'] = round(out['fwd20_stop'] - b20, 2)
    return out


def september_significance(bt):
    from scipy import stats

    targets = ['fwd5x', 'fwd10x', 'fwd20x', 'fwd20_stop_x']
    rows = []
    for target in targets:
        if target not in bt.columns:
            continue
        data = bt[[target, 'sep_label', 'scan_date']].copy()
        data[target] = pd.to_numeric(data[target], errors='coerce')
        data = data.dropna(subset=[target])
        if data.empty:
            continue
        core = data.loc[data['sep_label'].eq('SEPTEMBER_OPPORTUNITY'), target].to_numpy()
        rest = data.loc[~data['sep_label'].eq('SEPTEMBER_OPPORTUNITY'), target].to_numpy()
        if len(core) < 2 or len(rest) < 2:
            continue
        diff = float(core.mean() - rest.mean())
        rng = np.random.default_rng(20260827)
        boots = []
        for _ in range(4000):
            boots.append(rng.choice(core, len(core), replace=True).mean()
                        - rng.choice(rest, len(rest), replace=True).mean())
        lo, hi = np.percentile(boots, [2.5, 97.5])
        t_p = float(stats.ttest_ind(core, rest, equal_var=False, nan_policy='omit').pvalue)
        u_p = float(stats.mannwhitneyu(core, rest, alternative='two-sided').pvalue)
        rows.append({
            'target': target, 'core_n': len(core), 'rest_n': len(rest),
            'core_mean': core.mean(), 'rest_mean': rest.mean(), 'diff': diff,
            'core_median': np.median(core), 'rest_median': np.median(rest),
            'core_win': (core > 0).mean() * 100, 'rest_win': (rest > 0).mean() * 100,
            'welch_p': t_p, 'mw_p': u_p, 'boot_lo': lo, 'boot_hi': hi,
        })

    result = pd.DataFrame(rows)
    print('\\n' + '=' * 64)
    print('C. 九月机会观察池收益显著性检验（SEPTEMBER_OPPORTUNITY vs 其余样本）')
    print('=' * 64)
    if result.empty:
        print('有效收益样本不足')
        return result
    for _, r in result.iterrows():
        sig = '显著' if r['welch_p'] < 0.05 and r['mw_p'] < 0.05 and r['boot_lo'] > 0 else '不显著'
        print(f"{r['target']}: core n={r['core_n']} 均值{r['core_mean']:+.2f}% 中位{r['core_median']:+.2f}% 胜率{r['core_win']:.1f}% | "
              f"rest n={r['rest_n']} 均值{r['rest_mean']:+.2f}% | 差{r['diff']:+.2f}% | "
              f"Welch p={r['welch_p']:.4f} MW p={r['mw_p']:.4f} bootstrap95%[{r['boot_lo']:+.2f},{r['boot_hi']:+.2f}] => {sig}")
    result.to_csv(os.path.join(REPORT_DIR, f'er20_v22_backtest_{PERIOD}_september_significance.csv'), index=False, encoding='utf-8-sig')
    return result


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
    ap.add_argument('--season', choices=list(SEASONS.keys()), default='2025H1')
    ap.add_argument('--prep-data', action='store_true', help='仅拉取并缓存 tushare 增量日线后退出')
    args = ap.parse_args()

    global PERIOD, Q1_PERIOD, ANN_LO, ANN_HI, FWD_MIN
    scfg = SEASONS[args.season]
    PERIOD = scfg['period']
    Q1_PERIOD = scfg['q1']
    ANN_LO, ANN_HI = scfg['lo'], scfg['hi']
    FWD_MIN = int(scfg.get('fwd', 20))

    if not ensure_ts_delta():
        print('[回测] 无增量日线可用, 按本地 TDX 数据继续(末端截断风险)')
    install_ts_patches()
    if args.prep_data:
        try:
            from zhongbao_hunter import _get_pro
            _verify_overlap_unit(_get_pro())
        except Exception:
            pass
        b = load_bench_full()
        tail = str(b['trade_date'].iloc[-1]) if b is not None and len(b) else 'N/A'
        print(f'[预热] 补丁后基准末端: {tail}, 完成')
        return

    t0 = time.time()
    pool = load_hist_pool()
    print(f'[回测] {args.season} 事件池: {len(pool)} 只 (去ST/北交所, ann {ANN_LO}~{ANN_HI})')
    if pool.empty:
        return
    pool = pool.sample(min(args.n, len(pool)), random_state=args.seed)
    print(f'抽样 {len(pool)} 只 (seed={args.seed})')

    bench_full = load_bench_full()
    if bench_full is None:
        print('基准数据缺失'); return

    # ── 北向机构加分（扫描窗口前最近季末快照，无前视；2026H1 暂无快照则跳过） ──
    north_map = {}
    north_date = scfg.get('north')
    if north_date:
        try:
            north_map = load_northbound_snapshot(north_date)
            print(f'[机构加分] 北向快照 {north_date}: {len(north_map)} 只')
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
            if s_idx >= len(daily) or s_idx + FWD_MIN >= len(daily):
                continue
            scan_date = str(daily.iloc[s_idx]['trade_date'])
            if scan_date not in industry_atr_cache:
                industry_atr_cache[scan_date] = _precompute_industry_atr(pool, scan_date)
            industry_atr = industry_atr_cache[scan_date]
            market_mult = market_multiplier(scan_date)[1]
            bench = bench_full[bench_full['trade_date'] <= scan_date].reset_index(drop=True)
            d = daily.iloc[:s_idx + 1].reset_index(drop=True)
            row = score_one(r, d, ann_idx, s_idx, scan_date, bench, industry_atr, market_mult)
            row.update(next5_targets(daily, s_idx, bench_full, scan_date))
            row.update(fwd_returns(daily, s_idx, bench_full, scan_date))
            row['market_mult'] = market_mult
            rows.append(row)
        if (i + 1) % 100 == 0:
            print(f'  已处理 {i + 1}/{len(pool)} 事件, {time.time() - t0:.0f}s')

    if not rows:
        print('无回测样本'); return
    bt = pd.DataFrame(rows)
    for c in ('fwd5', 'fwd10', 'fwd20', 'fwd5x', 'fwd10x', 'fwd20x',
              'fwd20_stop', 'fwd20_stop_x', 'stop_day'):
        if c not in bt.columns:
            bt[c] = np.nan

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
    bt['next5_score'] = bt.apply(next5_score_v22, axis=1)
    september_significance(bt)
    fusion_values = bt.apply(fusion_score_v22, axis=1, result_type='expand')
    fusion_values.columns = ['fusion_score', 'egpt_quality_score', 'egpt_pullback_score', 'fusion_gate']
    bt = pd.concat([bt, fusion_values], axis=1)
    bt['fusion_rank'] = bt.groupby('scan_date')['fusion_score'].rank(method='first', ascending=False).astype(int)
    bt['next5_rank'] = bt.groupby('scan_date')['next5_score'].rank(method='first', ascending=False).astype(int)
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
        'fusion': {'t1_cap': True, 'gate': {'test_alpha': 80.0, 'test_ees': 72.0,
                                            'test_ts': 72.0, 'probe_alpha': 72.0}},
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
        bt2['next5_signal'] = 0
        buy_mask = bt2['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])
        eligible = bt2[buy_mask & bt2['rank_eligible']]
        if vname == 'fusion':
            eligible = eligible[(eligible['fusion_gate'] == 1) & (eligible['fusion_score'] >= V22Config.FUSION['min_score'])]
            rank_col = 'fusion_score'
        else:
            rank_col = 'next5_score'
        for scan_date, grp in eligible.groupby('scan_date'):
            keep = grp.sort_values(rank_col, ascending=False).head(V22Config.NEXT5['limit']).index
            if len(keep) >= 2 and float(grp.loc[keep[0], rank_col]) - float(grp.loc[keep[1], rank_col]) > V22Config.FUSION['second_gap']:
                keep = keep[:1]
            bt2.loc[keep, 'next5_signal'] = 1
        csv = os.path.join(REPORT_DIR, f'er20_v22_backtest_{args.season}_{vname}.csv')
        bt2.to_csv(csv, index=False, encoding='utf-8-sig')
        print(f'\n[{vname}] 回测明细: {csv} ({len(bt2)} 样本)')
        summarize(bt2, f'[{vname}] 全样本', None)
        for g in ('grade', 'decay_state', 'ttype'):
            summarize(bt2, f'[{vname}] 按 {g}', g)
        # BUY 组
        buy = bt2[bt2['grade'].isin(['CORE_BUY', 'TEST_BUY', 'PROBE_BUY'])]
        print(f'\n[{vname}] BUY 组 n={len(buy)}')
        summarize(buy, f'[{vname}] BUY', None)
        summarize(bt2[bt2['next5_signal'].eq(0)], f'[{vname}] 非Top2信号', None,
                  key_cols=('max5_excess', 'max5_ret', 'close5_ret'))
        next5_buy = bt2[bt2['next5_signal'].eq(1)]
        print(f'\n[{vname}] next5 Top2 信号 n={len(next5_buy)}')
        summarize(next5_buy, f'[{vname}] next5 Top2(毛收益)', None,
                  key_cols=('max5_excess', 'max5_ret', 'close5_ret'))
        if not next5_buy.empty:
            summarize(next5_buy, f'[{vname}] next5 Top2(扣成本)', None,
                      key_cols=('max5_excess_net', 'max5_ret_net', 'close5_ret_net'))
            c = next5_buy['cost_rt_pct'].dropna()
            pp = next5_buy['participation'].dropna()
            print(f"[{vname}] Top2 往返成本: 均值{c.mean():.2f}% 中位{c.median():.2f}% "
                  f"最大{c.max():.2f}% 最小{c.min():.2f}% | 成交额参与率均值{pp.mean():.2f}%")
        # 止损对比（仅 BUY 组）
        if not buy.empty:
            s20 = buy['fwd20'].dropna()
            stp = buy['fwd20_stop'].dropna()
            print(f'[{vname}] BUY 止损对比: 持有T+20 超额均值{s20.mean():+.2f}% 胜率{(s20>0).mean()*100:.0f}%'
                   f' | -8%止损后 超额均值{stp.mean():+.2f}% 胜率{(stp>0).mean()*100:.0f}% 触发率{(buy["stop_day"]>0).mean()*100:.0f}%')

    print(f'\n[总样本] 用时 {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
