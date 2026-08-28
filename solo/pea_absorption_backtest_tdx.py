# -*- coding: utf-8 -*-
"""
PEA-Absorption TDX 回测 — 独立策略验证
======================================
范式: Price-Event Absorption（价格-事件吸收, pea_absorption.py 全链）
样本: 2025 中报(end_date=20250630) 已披露事件(ann 2025-07~10), 随机抽样 N=400(seed=42)
扫描: 公告后第 T+5 / T+10 交易日(双时点) 按 PEA 全链评分
      防未来函数: 每个扫描时点 daily 切片到扫描日(≤扫描日), 未来行情仅用于收益统计
未来: 扫描日后 T+5/10/15/20/30 收益 + 上证(000001)同期超额
硬规则回测落实(与策略引擎一致):
      T+15 持有(close15/mix15) / -8% 收盘止损(fwd15_stop/fwd20_stop/spike/mix)
      T3_RECLAIM 硬屏蔽(grade_pea) / PRICED_IN 硬排除(grade_pea) / 次日开盘>前收×1.08 放弃(entry_executable)
分层: grade / Alpha分位 / Event / Decay / Trigger / 扫描时点
数据: 行情=TDX via bts.data + tushare 增量补丁; 财务=fin_ind unified parquet, 缺失回退 treasure 缓存
      主题加分在回测中禁用(当前主题快照有前视偏差, theme_adj=0)
用法: python -X utf8 pea_absorption_backtest_tdx.py [--n 400] [--seed 42] [--season 2025H1]
输出: report_daily/pea_absorption_backtest_2025H1_{variant}.csv + pea_absorption_grid_2025H1.csv
锚点: er20 基线 close15 Top2 净超额 +0.40%
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

from pea_absorption import (
    PeaConfig, _valid, classify_event, fundamental_quality, calc_rqs,
    expect_gap_score, overheat_penalty, calc_ars, trend_structure,
    volume_structure, pullback_quality, calc_tqs, risk_score,
    market_multiplier, theme_score, relative_risk_score,
    earnings_quality_context, cashflow_context_engine, data_confidence,
    calc_event_age, price_absorption, calc_absorption_state, refresh_score,
    trigger_score, calc_ees, _load_industry_map, grade_pea,
)
from bts.data import load_daily, parse_tdx_day_file, load_stock_basic
from bts.indicators import add_ma, add_rsi
import datetime as _dt
import bts.data as _bts_data
import pea_absorption as _pea_mod

SEASONS = {
    '2025H1': {'period': '20250630', 'q1': '20250331', 'lo': '20250701', 'hi': '20251031', 'fwd': 20},
    '2026H1': {'period': '20260630', 'q1': '20260331', 'lo': '20260701', 'hi': '20261231', 'fwd': 6},
}
# close-based 前瞻窗口族: 5/10 为短线锚, 15/20/30 为中线网格(close15=T+15 持有锚点)
FWD_HORIZONS = (5, 10, 15, 20, 30)
# 持有期×规模效应网格的 TopN 候选(2=基线规模, 其余为 PEA 策略备选规模)
GRID_TOPN = (2, 3, 5, 8)
# 冲高兑现(spike cashout)参数: 限价挂单模型, 挂单价触及即成交(不按盘中最高价计, 保守口径)
SPIKE_TARGET = 0.05   # 挂单兑现价 = 入场价 × (1+5%), 当日最高≥挂单价视为成交
SPIKE_STOP = 0.08     # 收盘价较入场价浮亏 -8% 则当日收盘止损离场(与 PeaConfig.EXEC.stop 一致)
SPIKE_WINDOW = 5      # 冲高兑现挂单有效期(T+1..T+5), 期内未触及按末日收盘离场
MIX_HOLD = 15         # 混合兑现: +5% 兑现半仓后, 余仓持有至 T+15 收盘(与 PeaConfig.EXEC.max_hold 一致)
TOPN_SIGNAL = 2       # 每扫描日 TopN 信号数
SECOND_GAP = 8.0      # Top1 领先 Top2 超过该分差 → 收缩为只取 Top1
STOP_PCT = 0.08       # 止损线(收盘价口径), 对齐 PeaConfig.EXEC.stop
PERIOD = SEASONS['2025H1']['period']
Q1_PERIOD = SEASONS['2025H1']['q1']
ANN_LO, ANN_HI = SEASONS['2025H1']['lo'], SEASONS['2025H1']['hi']
FWD_MIN = 20
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
    """monkeypatch: 让事件龄(pea_absorption.get_trade_dates)与 bts.data 日历
    的数据面同步吃到增量尾部"""
    idx_full = load_bench_full()

    def patched_get_trade_dates(start_date, end_date):
        if idx_full is None or not len(idx_full):
            return []
        return [d for d in idx_full['trade_date'].tolist()
                if str(start_date) <= str(d) <= str(end_date)]

    _bts_data.get_trade_dates = patched_get_trade_dates
    _pea_mod.get_trade_dates = patched_get_trade_dates
    n = len(idx_full) if idx_full is not None else 0
    print(f'[数据补丁] monkeypatch 生效: 交易日历({n}根, 至{_tdx_tail_date()})')


def _calc_q2_single(ts_code, period, row):
    """单只股票 Q2 累计增速补算（tushare fina_indicator）; 返回 (netprofit_yoy, proxy_flag)"""
    try:
        from zhongbao_hunter import _get_pro
        pro = _get_pro()
        df = pro.fina_indicator(ts_code=str(ts_code), period=str(period))
        if df is not None and len(df):
            v = df.iloc[0].get('netprofit_yoy', np.nan)
            if v is not None and pd.notna(v):
                return float(v), False
    except Exception:
        pass
    return np.nan, False


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
        q2, pr = _calc_q2_single(str(r['ts_code']), PERIOD, r)
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
    # PEA 技术函数依赖列兜底(ma5/ma10/ma20/ma60/vol_ma5/vol_ma10/vol_ma20/rsi14)
    for col, win in (('ma5', 5), ('ma10', 10), ('ma20', 20), ('ma60', 60)):
        if col not in daily.columns:
            daily[col] = daily['close'].astype(float).rolling(win).mean()
    for col, win in (('vol_ma5', 5), ('vol_ma10', 10), ('vol_ma20', 20)):
        if col not in daily.columns:
            daily[col] = pd.to_numeric(daily['vol'], errors='coerce').rolling(win).mean()
    if 'rsi14' not in daily.columns and 'rsi' in daily.columns:
        daily['rsi14'] = daily['rsi']
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
    idx = _merge_ts_delta(idx, _ts_idx_delta)
    idx = idx.reset_index(drop=True)
    # PEA 市场状态/吸收度量/相对风险依赖列兜底（与 load_daily_full 同思路）
    if 'trade_date' not in idx.columns and 'date' in idx.columns:
        idx = idx.rename(columns={'date': 'trade_date'})
    if 'pct_chg' not in idx.columns:
        idx['pct_chg'] = idx['close'].astype(float).pct_change() * 100.0
    idx['pct_chg'] = idx['pct_chg'].fillna(0.0)
    for col, win in (('ma20', 20), ('ma60', 60)):
        if col not in idx.columns:
            idx[col] = idx['close'].astype(float).rolling(win).mean()
    return idx


# ============================================================
# 评分（PEA 全链，扫描日切片防未来）
# ============================================================
def score_one(r, daily, ann_idx, s_idx, scan_date, bench, ind_map, mkt_mult):
    """扫描日 s_idx 处按 PEA 全链评分（daily 已切片 ≤s_idx，未来行情仅用于收益统计）"""
    code = str(r['ts_code'])
    code6 = code.split('.')[0]
    ann_date = str(r.get('ann_date', '') or '')[:8]

    etype, side, edesc = classify_event(r)
    fq = fundamental_quality(r)
    industry = ind_map.get(code6, str(r.get('industry', '') or ''))
    rqs = calc_rqs(r, industry)
    gap_s = expect_gap_score(r)
    ars = calc_ars(daily, ann_idx, s_idx)
    oh = overheat_penalty(daily)
    tqs = calc_tqs(daily)
    risk = risk_score(daily)

    event_age = calc_event_age(ann_date, scan_date)
    pa = price_absorption(daily, ann_idx, s_idx, bench)
    state, decay, _, _detail = calc_absorption_state(pa, event_age)
    refresh, refresh_conds = refresh_score(daily, pa, state)
    trig_type, ts, _trig_detail = trigger_score(daily)
    pqs = pullback_quality(daily)
    ees = calc_ees(daily, ts, pqs, oh)

    conf = data_confidence(r, ann_date)
    # 预期季口径对照列(ann_ok=1 → conf 满血 +25): 变体 ann25 系专用
    conf_full = data_confidence(r, ann_date, expected_year=str(ann_date)[:4])
    cf_key, cf_base, cf_adj, cf_desc = cashflow_context_engine(r, industry)
    cfcs = cf_base + cf_adj
    eq_state = earnings_quality_context(r)
    eq_penalty = PeaConfig.EQ_PENALTY.get(eq_state, 0.0)

    # 相对风险（20 日量比 vs 基准，与 scan_pea 口径一致）
    vol_ratio20 = np.nan
    if len(bench) >= 20 and 'vol' in bench.columns:
        sv = float(pd.to_numeric(daily['vol'], errors='coerce').iloc[-20:].mean())
        bv = float(pd.to_numeric(bench['vol'], errors='coerce').iloc[-20:].mean())
        if _valid(sv) and _valid(bv) and bv > 0:
            vol_ratio20 = sv / bv
    rel_risk = relative_risk_score(vol_ratio20 if _valid(vol_ratio20) else np.nan)

    # raw（组内归一在 main 层按 scan_mark×side 完成；回测禁用主题分 theme_adj=0）
    w = PeaConfig.W_A if side == 'A' else PeaConfig.W_B
    parts = ({'fq': fq, 'gap': gap_s, 'ars': ars, 'pqs': pqs, 'trend': tqs, 'risk': 100 - risk}
             if side == 'A' else
             {'rqs': rqs, 'fq': fq, 'gap': gap_s, 'ars': ars, 'tqs': tqs, 'risk': 100 - risk})
    raw = sum(float(parts[k]) * w[k] for k in w)

    return {
        'ts_code': code, 'code6': code6, 'name': str(r.get('name', '') or ''),
        'ann_date': ann_date, 'end_date': str(r.get('end_date', '') or ''),
        'scan_mark': s_idx - ann_idx + 1, 'scan_date': scan_date,
        'etype': etype, 'side': side, 'edesc': edesc, 'industry': industry,
        'fq': fq, 'rqs': rqs, 'gap_s': gap_s, 'ars': ars, 'pqs': pqs, 'tqs': tqs,
        'risk': risk, 'rel_risk': rel_risk, 'vol_ratio20': vol_ratio20,
        'overheat': oh, 'event_age': event_age,
        'pre_ret': pa.get('pre_ret', np.nan), 'gap_ann': pa.get('gap_ann', np.nan),
        'post_ret': pa.get('post_ret', np.nan), 'rel_str': pa.get('rel_str', np.nan),
        'pre_priced': pa.get('pre_priced', False),
        'absorption_state': state, 'decay': decay,
        'refresh': refresh, 'refresh_conds': '|'.join(refresh_conds),
        'trigger_type': trig_type, 'ts': ts, 'ees': ees,
        'conf': conf, 'conf_full': conf_full, 'cfcs': cfcs, 'cf_key': cf_key, 'cf_desc': cf_desc,
        'eq_state': eq_state, 'eq_penalty': eq_penalty,
        'raw': raw, 'side_key': side,
        'close': float(daily['close'].iloc[-1]),
    }


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
    """T+1 开盘入场后的短窗口目标统计 + 冲高兑现(spike) + 混合兑现(mix) 逐日模拟"""
    out = {
        'next_open_ret': np.nan, 'close5_ret': np.nan, 'max5_ret': np.nan,
        'max5_excess': np.nan, 'max5_drawdown': np.nan,
        'max5_ret_net': np.nan, 'close5_ret_net': np.nan, 'max5_excess_net': np.nan,
        'cost_rt_pct': np.nan, 'participation': np.nan,
        'entry_date': None, 'entry_price': np.nan, 'entry_executable': 0,
        'spike_exit_ret': np.nan, 'spike_exit_net': np.nan,
        'spike_excess': np.nan, 'spike_excess_net': np.nan,
        'spike_exit_day': np.nan, 'spike_exit_type': 'no_entry',
        'mix_legA_ret': np.nan, 'mix_legA_day': np.nan, 'mix_legA_type': 'no_entry',
        'mix_legB_ret': np.nan, 'mix_legB_day': np.nan, 'mix_legB_type': 'no_entry',
        'mix_exit_ret': np.nan, 'mix_exit_net': np.nan,
        'mix_excess': np.nan, 'mix_excess_net': np.nan,
    }
    if s_idx + 1 >= len(daily):
        return out
    base = float(daily.iloc[s_idx]['close'])
    entry = daily.iloc[s_idx + 1]
    entry_price = float(entry['open'])
    out['entry_date'] = str(entry['trade_date'])
    out['entry_price'] = entry_price
    out['entry_executable'] = int(entry_price <= base * PeaConfig.EXEC['gap_cap'])
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
    # ── 冲高兑现(spike cashout): T+1 开盘入场, 挂单价+5%冲高兑现(限价单触及即成交),
    #    收盘浮亏-8%止损, T+5 未触发按收盘清仓. 同日先判冲高后判止损 ──
    exit_price, exit_day, exit_type = None, None, None
    if int(out['entry_executable']) == 1:
        tgt = entry_price * (1.0 + SPIKE_TARGET)
        stp = entry_price * (1.0 - SPIKE_STOP)
        last_day = len(window)
        for d in range(len(window)):
            row_d = window.iloc[d]
            o, h, c = float(row_d['open']), float(row_d['high']), float(row_d['close'])
            if o >= tgt:
                exit_price, exit_day, exit_type = o, d + 1, 'spike'
                break
            if h >= tgt:
                exit_price, exit_day, exit_type = tgt, d + 1, 'spike'
                break
            if c <= stp:
                exit_price, exit_day, exit_type = c, d + 1, 'stop'
                break
            if d == last_day - 1:
                exit_price, exit_day, exit_type = c, d + 1, 'expire'
        if exit_price is not None:
            out['spike_exit_ret'] = round((exit_price / entry_price - 1.0) * 100, 2)
            out['spike_exit_day'] = exit_day
            out['spike_exit_type'] = exit_type
    if pd.notna(out.get('spike_exit_ret')):
        ge = exit_price / entry_price
        out['spike_exit_net'] = round((ge * (1.0 - sell_frac) / (1.0 + buy_frac) - 1.0) * 100, 2)
    # ── 混合兑现(mix cashout): +5% 触及兑现半仓(限价单 T+1..T+5 有效),
    #    余仓持有至 T+15 收盘; 全程收盘浮亏-8% 整体止损. 同日先冲高后止损 ──
    a_price = a_day = a_type = None
    b_price = b_day = b_type = None
    if int(out['entry_executable']) == 1:
        tgt_m = entry_price * (1.0 + SPIKE_TARGET)
        stp_m = entry_price * (1.0 - SPIKE_STOP)
        wm = daily.iloc[s_idx + 1: min(s_idx + MIX_HOLD, len(daily) - 1) + 1]
        last_no = len(wm)
        for d in range(last_no):
            row_d = wm.iloc[d]
            o, h, c = float(row_d['open']), float(row_d['high']), float(row_d['close'])
            no = d + 1
            if a_price is None and no <= SPIKE_WINDOW:
                if o >= tgt_m:
                    a_price, a_day, a_type = o, no, 'spike'
                elif h >= tgt_m:
                    a_price, a_day, a_type = tgt_m, no, 'spike'
                elif no == min(SPIKE_WINDOW, last_no) and c > stp_m:
                    a_price, a_day, a_type = c, no, 'expire'
            if b_price is None and c <= stp_m:
                b_price, b_day, b_type = c, no, 'stop'
            if no == last_no and b_price is None:
                b_price, b_day, b_type = c, no, 'expire'
            if a_price is None and c <= stp_m:
                a_price, a_day, a_type = c, no, 'stop'
        if a_price is None and last_no > 0:
            c_last = float(wm.iloc[-1]['close'])
            a_price, a_day, a_type = c_last, last_no, 'expire'
    if a_price is not None and b_price is not None:
        ra = (a_price / entry_price - 1.0) * 100
        rb = (b_price / entry_price - 1.0) * 100
        out['mix_legA_ret'] = round(ra, 2)
        out['mix_legA_day'] = a_day
        out['mix_legA_type'] = a_type
        out['mix_legB_ret'] = round(rb, 2)
        out['mix_legB_day'] = b_day
        out['mix_legB_type'] = b_type
        out['mix_exit_ret'] = round(0.5 * ra + 0.5 * rb, 2)
        na = (a_price * (1.0 - sell_frac) / (entry_price * (1.0 + buy_frac)) - 1.0) * 100
        nb = (b_price * (1.0 - sell_frac) / (entry_price * (1.0 + buy_frac)) - 1.0) * 100
        out['mix_exit_net'] = round(0.5 * na + 0.5 * nb, 2)
    bench = bench_full[bench_full['trade_date'].astype(str) == str(scan_date)]
    if not bench.empty:
        bi = bench.index[0]
        if bi + 5 < len(bench_full):
            bbase = float(bench_full.iloc[bi]['close'])
            b5 = (float(bench_full.iloc[bi + 5]['close']) / bbase - 1.0) * 100
            out['max5_excess'] = round(out['max5_ret'] - b5, 2)
            out['max5_excess_net'] = round(out['max5_ret_net'] - b5, 2)
        # 冲高兑现超额: 按实际退出日对齐基准(持有天数逐笔一致)
        if pd.notna(out.get('spike_exit_ret')):
            bd = bi + int(out['spike_exit_day'])
            if bd < len(bench_full):
                b_exit = (float(bench_full.iloc[bd]['close'])
                          / float(bench_full.iloc[bi]['close']) - 1.0) * 100
                out['spike_excess'] = round(out['spike_exit_ret'] - b_exit, 2)
                out['spike_excess_net'] = round(out['spike_exit_net'] - b_exit, 2)
        # 混合兑现超额: 两腿各自按退出日对齐基准后加权(半仓半仓)
        if pd.notna(out.get('mix_exit_ret')):
            ba_ = bi + int(out['mix_legA_day'])
            bb_ = bi + int(out['mix_legB_day'])
            if ba_ < len(bench_full) and bb_ < len(bench_full):
                bxa = float(bench_full.iloc[ba_]['close']) / float(bench_full.iloc[bi]['close']) - 1.0
                bxb = float(bench_full.iloc[bb_]['close']) / float(bench_full.iloc[bi]['close']) - 1.0
                b_mix = (0.5 * bxa + 0.5 * bxb) * 100
                out['mix_excess'] = round(out['mix_exit_ret'] - b_mix, 2)
                out['mix_excess_net'] = round(out['mix_exit_net'] - b_mix, 2)
    return out


def fwd_returns(daily, s_idx, bench_full, scan_date):
    """close-based 前瞻窗口 + 止损模拟(fwd15_stop=T+15 持有锚 / fwd20_stop=基线口径)"""
    out = {}
    base = float(daily.iloc[s_idx]['close'])
    for h in FWD_HORIZONS:
        if s_idx + h < len(daily):
            out[f'fwd{h}'] = round((float(daily.iloc[s_idx + h]['close']) / base - 1) * 100, 2)
        else:
            out[f'fwd{h}'] = np.nan
    # ── -8% 收盘止损模拟：窗口内收盘价较扫描日收盘回撤≥8% 则按 -8% 止损 ──
    stop_price = base * (1.0 - STOP_PCT)
    for hold, skey in ((15, 'fwd15_stop'), (20, 'fwd20_stop')):
        stop_day = None
        for h in range(1, hold + 1):
            if s_idx + h >= len(daily):
                break
            if float(daily.iloc[s_idx + h]['close']) <= stop_price:
                stop_day = h
                break
        if stop_day is not None:
            out[skey] = round(-STOP_PCT * 100, 2)
            out[f'{skey}_day'] = stop_day
        elif pd.notna(out.get(f'fwd{hold}')):
            out[skey] = out[f'fwd{hold}']
            out[f'{skey}_day'] = 0
        else:
            out[skey] = np.nan
            out[f'{skey}_day'] = np.nan
    b = bench_full[bench_full['trade_date'].astype(str) == str(scan_date)]
    if not b.empty:
        bi = b.index[0]
        # 超额收益按各自窗口独立计算: 窗口不完整也不能吞掉短窗口超额
        for h in FWD_HORIZONS:
            x = out.get(f'fwd{h}')
            if pd.notna(x) and bi + h < len(bench_full):
                bh = (float(bench_full.iloc[bi + h]['close'])
                      / float(bench_full.iloc[bi]['close']) - 1) * 100
                out[f'fwd{h}x'] = round(x - bh, 2)
        for hold, skey in ((15, 'fwd15_stop'), (20, 'fwd20_stop')):
            if pd.notna(out.get(skey)) and bi + hold < len(bench_full):
                bh = (float(bench_full.iloc[bi + hold]['close'])
                      / float(bench_full.iloc[bi]['close']) - 1) * 100
                out[f'{skey}_x'] = round(out[skey] - bh, 2)
    return out


# ============================================================
# 汇总 / 网格 / 变体分级
# ============================================================
# PEA 锚点窗口: fwd15x/close15 = T+15 持有锚, fwd15_stop_x = T+15 + -8% 收盘止损
KEY_COLS = ('fwd15x', 'fwd15_stop_x', 'fwd20x', 'fwd20_stop_x')


def summarize(bt, tag, group_col, key_cols=KEY_COLS):
    print(f'\n── {tag} ──')
    if group_col is None:
        sub = bt
        parts = [f'n={len(sub)}']
        for k in key_cols:
            if k not in sub.columns:
                continue
            s = sub[k].dropna()
            if s.empty:
                continue
            parts.append(f'{k}:均值{s.mean():+.2f}% 胜率{(s > 0).mean() * 100:.0f}% 中位{s.median():+.2f}%')
        print(' | '.join(parts))
        return
    for gname, sub in bt.groupby(group_col, dropna=False):
        parts = [f'{str(gname)[:16]} n={len(sub)}']
        for k in key_cols:
            if k not in sub.columns:
                continue
            s = sub[k].dropna()
            if s.empty:
                continue
            label = k.replace('fwd', '').replace('_x', '')
            parts.append(f'{label} {s.mean():+.2f}%/{((s > 0).mean() * 100):.0f}%')
        print(' | '.join(parts))


def holding_grid(bt2, vname, rank_col, eligible):
    """持有期×规模效应网格(PEA-Absorption 验证).
    对 N∈GRID_TOPN 按现行 TopN 规则(含 second_gap=8.0 分差收缩)从 eligible 选信号,
    统计各窗口超额收益/胜率/选股增量. 复用同一轮评分, 零重复扫描.
    窗口族: close5/10/15/20/30 + spike5 + mix15 + stop15/stop20(-8%收盘止损模拟).
    stop 窗口对齐 PeaConfig.EXEC.max_hold=15 锚点; PEA 无 D 类伪信号分层, 池=全样本."""
    rows = []
    if eligible.empty:
        return rows
    gap = SECOND_GAP
    for n in GRID_TOPN:
        sigcol = f'topN{n}_signal'
        bt2[sigcol] = 0
        for scan_date, grp in eligible.groupby('scan_date'):
            keep = grp.sort_values(rank_col, ascending=False).head(n).index
            if len(keep) >= 2 and float(grp.loc[keep[0], rank_col]) - \
                    float(grp.loc[keep[1], rank_col]) > gap:
                keep = keep[:1]
            bt2.loc[keep, sigcol] = 1
    pool_elig = bt2[bt2['rank_eligible']]
    costs = bt2['cost_rt_pct'].dropna()
    avg_cost = float(costs.mean()) if len(costs) else 0.60
    for n in GRID_TOPN:
        sel = bt2[bt2[f'topN{n}_signal'].eq(1)]
        if sel.empty:
            continue
        # close-based 窗口
        for h in FWD_HORIZONS:
            col = f'fwd{h}x'
            x = sel[col].dropna()
            p = pool_elig[col].dropna()
            mean_x = float(x.mean()) if len(x) else np.nan
            lift = (mean_x - float(p.mean())) if (len(x) and len(p)) else np.nan
            rows.append({
                'variant': vname, 'topn': n, 'window': f'close{h}',
                'n_signals': len(sel), 'n_valid': len(x),
                'coverage': round(len(x) / len(sel), 3) if len(sel) else np.nan,
                'excess_mean': round(mean_x, 2),
                'excess_median': round(float(x.median()), 2) if len(x) else np.nan,
                'win_rate': round(float((x > 0).mean()) * 100, 1) if len(x) else np.nan,
                'pool_mean': round(float(p.mean()), 2) if len(p) else np.nan,
                'lift_vs_pool': round(lift, 2) if pd.notna(lift) else np.nan,
                'net_mean_est': round(mean_x - avg_cost, 2) if pd.notna(mean_x) else np.nan,
                'avg_cost_assumed': avg_cost,
            })
        # 止损模拟窗口: -8% 收盘止损 + T+15/T+20 持有(与 EXEC.max_hold 对齐)
        for skey, wname in (('fwd15_stop_x', 'stop15'), ('fwd20_stop_x', 'stop20')):
            x = sel[skey].dropna()
            p = pool_elig[skey].dropna()
            mean_x = float(x.mean()) if len(x) else np.nan
            lift = (mean_x - float(p.mean())) if (len(x) and len(p)) else np.nan
            rows.append({
                'variant': vname, 'topn': n, 'window': wname,
                'n_signals': len(sel), 'n_valid': len(x),
                'coverage': round(len(x) / len(sel), 3) if len(sel) else np.nan,
                'excess_mean': round(mean_x, 2),
                'excess_median': round(float(x.median()), 2) if len(x) else np.nan,
                'win_rate': round(float((x > 0).mean()) * 100, 1) if len(x) else np.nan,
                'pool_mean': round(float(p.mean()), 2) if len(p) else np.nan,
                'lift_vs_pool': round(lift, 2) if pd.notna(lift) else np.nan,
                'net_mean_est': round(mean_x - avg_cost, 2) if pd.notna(mean_x) else np.nan,
                'avg_cost_assumed': avg_cost,
            })
        # spike cashout 维度: net_mean_est 用真实成本净超额(spike_excess_net)
        x = sel['spike_excess'].dropna()
        xn = sel['spike_excess_net'].dropna()
        p = pool_elig['spike_excess'].dropna()
        mean_x = float(x.mean()) if len(x) else np.nan
        mean_xn = float(xn.mean()) if len(xn) else np.nan
        lift = (mean_x - float(p.mean())) if (len(x) and len(p)) else np.nan
        rows.append({
            'variant': vname, 'topn': n, 'window': 'spike5',
            'n_signals': len(sel), 'n_valid': len(x),
            'coverage': round(len(x) / len(sel), 3) if len(sel) else np.nan,
            'excess_mean': round(mean_x, 2),
            'excess_median': round(float(x.median()), 2) if len(x) else np.nan,
            'win_rate': round(float((x > 0).mean()) * 100, 1) if len(x) else np.nan,
            'pool_mean': round(float(p.mean()), 2) if len(p) else np.nan,
            'lift_vs_pool': round(lift, 2) if pd.notna(lift) else np.nan,
            'net_mean_est': round(mean_xn, 2) if pd.notna(mean_xn) else np.nan,
            'avg_cost_assumed': avg_cost,
        })
        # mix cashout 维度: 半仓+5%兑现/余仓T+15, net_mean_est 用真实成本净超额(mix_excess_net)
        x = sel['mix_excess'].dropna()
        xn = sel['mix_excess_net'].dropna()
        p = pool_elig['mix_excess'].dropna()
        mean_x = float(x.mean()) if len(x) else np.nan
        mean_xn = float(xn.mean()) if len(xn) else np.nan
        lift = (mean_x - float(p.mean())) if (len(x) and len(p)) else np.nan
        rows.append({
            'variant': vname, 'topn': n, 'window': 'mix15',
            'n_signals': len(sel), 'n_valid': len(x),
            'coverage': round(len(x) / len(sel), 3) if len(sel) else np.nan,
            'excess_mean': round(mean_x, 2),
            'excess_median': round(float(x.median()), 2) if len(x) else np.nan,
            'win_rate': round(float((x > 0).mean()) * 100, 1) if len(x) else np.nan,
            'pool_mean': round(float(p.mean()), 2) if len(p) else np.nan,
            'lift_vs_pool': round(lift, 2) if pd.notna(lift) else np.nan,
            'net_mean_est': round(mean_xn, 2) if pd.notna(mean_xn) else np.nan,
            'avg_cost_assumed': avg_cost,
        })
    return rows


def grade_variant(row, vcfg):
    """按变体覆盖 PeaConfig.GATE 后分级(try/finally 恢复).
    注: PEA 的 T1 追高控制在 grade_pea 第⑦步内嵌(不可门控), 故变体族不含 t1_cap.
    expected_year=True → 采用预期季口径列(alpha_full/conf_full, conf 满血 +25):
      2025H1 默认口径 conf 封顶 60 使 72/80 门限不可达, 预期季口径让门限体系按原设计运作."""
    g = PeaConfig.GATE
    saved = {}
    for k, v in vcfg.get('gate', {}).items():
        saved[k] = g[k]
        g[k] = v
    full = bool(vcfg.get('expected_year'))
    try:
        st, rs = grade_pea(row['alpha_full'] if full else row['alpha'],
                           row['ees'], row['ts'], row['risk'],
                           row['conf_full'] if full else row['conf'],
                           row['fq'], row['overheat'], row['trigger_type'],
                           row['absorption_state'], row['side'], row['rqs'], row['event_age'])
    finally:
        g.update(saved)
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
    os.makedirs(REPORT_DIR, exist_ok=True)

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
    ind_map = _load_industry_map()
    print(f'[行业映射] {len(ind_map)} 只')

    rows = []
    daily_cache = {}
    mkt_mult_cache = {}
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
            # 同一扫描日的基准切片与市场乘数只算一次
            if scan_date not in mkt_mult_cache:
                bslice = bench_full[bench_full['trade_date'] <= scan_date].reset_index(drop=True)
                mkt_mult_cache[scan_date] = (market_multiplier(bslice), bslice)
            mkt_mult, bench = mkt_mult_cache[scan_date]
            d = daily.iloc[:s_idx + 1].reset_index(drop=True)
            row = score_one(r, d, ann_idx, s_idx, scan_date, bench, ind_map, mkt_mult)
            row.update(next5_targets(daily, s_idx, bench_full, scan_date))
            row.update(fwd_returns(daily, s_idx, bench_full, scan_date))
            row['market_mult'] = mkt_mult
            rows.append(row)
        if (i + 1) % 100 == 0:
            print(f'  已处理 {i + 1}/{len(pool)} 事件, {time.time() - t0:.0f}s')

    if not rows:
        print('无回测样本'); return
    bt = pd.DataFrame(rows)
    for c in (tuple(f'fwd{h}' for h in FWD_HORIZONS)
              + tuple(f'fwd{h}x' for h in FWD_HORIZONS)
              + ('fwd15_stop', 'fwd15_stop_x', 'fwd15_stop_day',
                 'fwd20_stop', 'fwd20_stop_x', 'fwd20_stop_day')):
        if c not in bt.columns:
            bt[c] = np.nan

    # ── norm: 按(扫描时点, 事件侧 A/B)组内百分位(≥3 启用, 否则 clip) ──
    bt['norm'] = np.nan
    for (m, sd), grp in bt.groupby(['scan_mark', 'side']):
        mask = (bt['scan_mark'] == m) & (bt['side'] == sd)
        if len(grp) >= 3:
            bt.loc[mask, 'norm'] = grp['raw'].rank(pct=True) * 100.0
        else:
            bt.loc[mask, 'norm'] = grp['raw'].clip(0, 100)

    # ── alpha 链(与 calc_pea_score 严格一致; 回测禁用主题分 theme_adj=0) ──
    conf_mult = bt['conf'].astype(float) / 100.0
    risk_mult = 1.0 - (bt['rel_risk'].astype(float) / 100.0) * PeaConfig.RISK_PEN
    bt['pea_base'] = (bt['norm'] * conf_mult * risk_mult * bt['market_mult']).round(1)
    decay_mult = bt['decay'].map(
        lambda x: max(0.60, min(1.00, 1.0 - float(x))) if pd.notna(x) else 1.0)
    bt['alpha'] = (bt['pea_base'] * decay_mult + bt['refresh'].fillna(0.0)
                   - bt['eq_penalty'].fillna(0.0)).round(1).clip(0, 100)
    # 预期季口径(ann_ok=1, conf 满血): ann25 变体专用——门限按原设计运作
    bt['pea_base_full'] = (bt['norm'] * bt['conf_full'].astype(float) / 100.0
                           * risk_mult * bt['market_mult']).round(1)
    bt['alpha_full'] = (bt['pea_base_full'] * decay_mult + bt['refresh'].fillna(0.0)
                        - bt['eq_penalty'].fillna(0.0)).round(1).clip(0, 100)
    # PEA 无 D 类伪信号分层, 池 = 全样本
    bt['rank_eligible'] = True

    # ── 规则变体: 同一评分、不同分级门槛 ──
    # er20 的 t1_cap 对 PEA 是空操作(grade_pea 第⑦步已内嵌 T1 硬顶).
    # 2025H1 默认口径 conf 封顶 60 → 72/80 门限不可达(冒烟已证变体空转):
    # ann25 系列切换预期季口径(alpha_full/conf_full), 门限按原设计运作.
    grid_rows = []
    GATE_UP = {'test_alpha': 85.0, 'test_ees': 78.0, 'test_ts': 78.0,
               'probe_alpha': 78.0, 'probe_ees': 65.0}
    GATE_LO = {'test_alpha': 70.0, 'test_ees': 60.0, 'test_ts': 60.0,
               'probe_alpha': 58.0, 'probe_ees': 48.0}
    VARIANTS = {
        'base':          {'gate': {}},
        'ann25':         {'gate': {}, 'expected_year': True},
        'ann25_gate':    {'gate': dict(GATE_UP), 'expected_year': True},
        'ann25_gate_lo': {'gate': dict(GATE_LO), 'expected_year': True},
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
        buy_mask = bt2['grade'].isin(['CORE', 'TEST', 'PROBE'])
        eligible = bt2[buy_mask & bt2['rank_eligible']]
        rank_col = 'alpha'
        for scan_date, grp in eligible.groupby('scan_date'):
            keep = grp.sort_values(rank_col, ascending=False).head(TOPN_SIGNAL).index
            if len(keep) >= 2 and float(grp.loc[keep[0], rank_col]) - \
                    float(grp.loc[keep[1], rank_col]) > SECOND_GAP:
                keep = keep[:1]
            bt2.loc[keep, 'next5_signal'] = 1
        csv = os.path.join(REPORT_DIR, f'pea_absorption_backtest_{args.season}_{vname}.csv')
        bt2.to_csv(csv, index=False, encoding='utf-8-sig')
        print(f'\n[{vname}] 回测明细: {csv} ({len(bt2)} 样本)')
        summarize(bt2, f'[{vname}] 全样本', None)
        for g in ('grade', 'absorption_state', 'trigger_type', 'scan_mark'):
            summarize(bt2, f'[{vname}] 按 {g}', g)
        buy = bt2[buy_mask]
        print(f'\n[{vname}] BUY 组(CORE/TEST/PROBE) n={len(buy)}')
        summarize(buy, f'[{vname}] BUY', None)
        summarize(bt2[bt2['next5_signal'].eq(0)], f'[{vname}] 非Top2信号', None,
                  key_cols=('max5_excess', 'max5_ret', 'close5_ret', 'spike_excess'))
        top2 = bt2[bt2['next5_signal'].eq(1)]
        print(f'\n[{vname}] Top{TOPN_SIGNAL} 信号 n={len(top2)}')
        summarize(top2, f'[{vname}] Top{TOPN_SIGNAL}(毛收益)', None,
                  key_cols=('fwd15x', 'fwd15_stop_x', 'max5_excess', 'spike_excess', 'mix_excess'))
        if not top2.empty:
            summarize(top2, f'[{vname}] Top{TOPN_SIGNAL}(扣成本)', None,
                      key_cols=('fwd15_stop_x', 'spike_excess_net', 'mix_excess_net'))
            c = top2['cost_rt_pct'].dropna()
            pp = top2['participation'].dropna()
            print(f"[{vname}] Top{TOPN_SIGNAL} 往返成本: 均值{c.mean():.2f}% 中位{c.median():.2f}% "
                  f"最大{c.max():.2f}% 最小{c.min():.2f}% | 成交额参与率均值{pp.mean():.2f}%")
            tc = top2['spike_exit_type'].value_counts()
            sd = top2.loc[top2['spike_exit_type'].eq('spike'), 'spike_exit_day']
            print(f"[{vname}] Top{TOPN_SIGNAL} 冲高兑现分布: {' '.join(f'{k}={v}' for k, v in tc.items())}"
                  + (f" | spike成交日均值 T+{sd.mean():.1f}" if len(sd) else ''))
            ta = top2['mix_legA_type'].value_counts()
            tb = top2['mix_legB_type'].value_counts()
            print(f"[{vname}] Top{TOPN_SIGNAL} 混合兑现: A腿 {' '.join(f'{k}={v}' for k, v in ta.items())}"
                  f" | B腿 {' '.join(f'{k}={v}' for k, v in tb.items())}")
        # 止损对比(仅 BUY 组, T+15 持有锚点)
        if not buy.empty:
            s15 = buy['fwd15x'].dropna()
            stp = buy['fwd15_stop'].dropna()
            sday = buy['fwd15_stop_day'].dropna()
            print(f'[{vname}] BUY 止损对比: 持有T+15 超额均值{s15.mean():+.2f}% 胜率{(s15 > 0).mean() * 100:.0f}%'
                  f' | -8%收盘止损后 均值{stp.mean():+.2f}% 胜率{(stp > 0).mean() * 100:.0f}%'
                  f' 触发率{(sday > 0).mean() * 100:.0f}%')
        grid_rows.extend(holding_grid(bt2, vname, rank_col, eligible))

    # ── 持有期×规模效应网格落盘 ──
    if grid_rows:
        gdf = pd.DataFrame(grid_rows)
        grid_csv = os.path.join(REPORT_DIR, f'pea_absorption_grid_{args.season}.csv')
        gdf.to_csv(grid_csv, index=False, encoding='utf-8-sig')
        print(f'\n[网格] 持有期×规模效应: {grid_csv} ({len(gdf)} 组合)')
        for w in ('close5', 'close15', 'close20', 'spike5', 'mix15', 'stop15'):
            piv = gdf[gdf['window'] == w].pivot_table(
                index='variant', columns='topn', values='excess_mean', dropna=False)
            if not piv.empty:
                print(f'\n[{w} 超额均值% (TopN×变体)]')
                print(piv.round(2).to_string())

    print(f'\n[总样本] 用时 {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
