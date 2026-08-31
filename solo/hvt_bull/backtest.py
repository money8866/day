# -*- coding: utf-8 -*-
"""HVT-BULL 历史回测

事件研究法：
  1) 按股票遍历（每只股票只加载一次），在其时间轴上扫描检测日，检测 HVT 事件（无前视）
  2) 对每个事件计算 T+1/3/5/10/20 收益、最大收益、最大回撤
  3) 分组统计：全部HVT / 价格强度达标 / 锁筹 / 锁筹+突破 / HVT等级
  4) 对比三种买点：T0当日买入 vs 缩量锁筹后T+5买入 vs 二次突破日买入

数据约束：
  - 2025-01-01 起 stk_factor_pro 全市场覆盖（含 turnover_rate），主回测用该区间
  - 事件去重：同一股票 10 个交易日内只保留一个事件（防止连续天量重复计数）
"""

import os
import sys
import copy
import json
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.data_loader import HvtDataLoader
from hvt_bull.engine import HvtBullEngine
from hvt_bull import context as hvt_context

import yaml


_FIN_ASOF = None
_THEME_CACHE = {}


def _safe_float(value, default=np.nan):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _fundamental_asof(ts_code: str, trade_date: str) -> tuple:
    global _FIN_ASOF
    if _FIN_ASOF is None:
        path = hvt_context._FIN_IND
        if not os.path.exists(path):
            _FIN_ASOF = pd.DataFrame()
        else:
            try:
                _FIN_ASOF = pd.read_parquet(path)
                _FIN_ASOF['ann_date'] = _FIN_ASOF['ann_date'].astype(str)
            except Exception:
                _FIN_ASOF = pd.DataFrame()
    if _FIN_ASOF.empty:
        return np.nan, 'NA', False
    sub = _FIN_ASOF[(_FIN_ASOF['ts_code'] == ts_code) &
                    (_FIN_ASOF['ann_date'] <= str(trade_date))]
    if sub.empty:
        return np.nan, 'NA', False
    rec = sub.sort_values(['ann_date', 'end_date']).iloc[-1]
    dt_yoy = _safe_float(rec.get('dt_netprofit_yoy'), 0.0)
    tr_yoy = _safe_float(rec.get('tr_yoy'), 0.0)
    ocf_yoy = _safe_float(rec.get('ocf_yoy'), 0.0)
    if dt_yoy > 30 and tr_yoy > 10 and ocf_yoy > 0:
        return 90.0, 'S', True
    if dt_yoy > 30 or tr_yoy > 20:
        return 78.0, 'A', True
    if dt_yoy > 15:
        return 65.0, 'A', True
    if dt_yoy > 0 or tr_yoy > 10:
        return 55.0, 'B', True
    return 40.0, 'C', True


def _sector_asof(ts_code: str, trade_date: str) -> tuple:
    if trade_date not in _THEME_CACHE:
        path = rf'D:\mystock\cache_daily\theme_stock_map_v2_{trade_date}.json'
        _THEME_CACHE[trade_date] = hvt_context.load_theme_map(trade_date) if os.path.exists(path) else {}
    theme_map = _THEME_CACHE[trade_date]
    if not theme_map:
        return np.nan, '', False
    strength, name = hvt_context.sector_resonance(ts_code, theme_map, trade_date)
    return float(strength), name, True


def _first_pullback(dates, closes, highs, lows, break_idx, breakout_level, max_days=10):
    end = min(len(closes), break_idx + max_days + 1)
    pull_idx = None
    for j in range(break_idx + 1, end):
        if lows[j] <= breakout_level * 1.01:
            pull_idx = j
            break
    if pull_idx is None:
        return {'first_pullback_date': '', 'first_pullback_depth': np.nan,
                'first_pullback_hold': np.nan}
    depth = (lows[pull_idx] / breakout_level - 1.0) * 100.0
    hold = False
    check_end = min(len(closes), pull_idx + 4)
    if check_end > pull_idx:
        hold = bool(np.nanmax(closes[pull_idx:check_end]) >= breakout_level)
    return {'first_pullback_date': str(dates[pull_idx]),
            'first_pullback_depth': float(depth),
            'first_pullback_hold': bool(hold)}


def _quantile_labels(series: pd.Series, labels=('低', '中', '高')) -> pd.Series:
    values = pd.to_numeric(series, errors='coerce')
    ranks = values.rank(method='first', pct=True)
    return pd.cut(ranks, bins=[-np.inf, 1 / 3, 2 / 3, np.inf], labels=labels)


def _breakout_stat(df: pd.DataFrame, horizons) -> dict:
    if df is None or df.empty:
        return {'n': 0}
    out = {'n': int(len(df))}
    for h in horizons:
        col = f'r_break_{h}'
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors='coerce').dropna()
        if s.size:
            out[f'{col}_win'] = round(float((s > 0).mean() * 100), 1)
            out[f'{col}_avg'] = round(float(s.mean()), 2)
            out[f'{col}_median'] = round(float(s.median()), 2)
            wins = s[s > 0]
            losses = s[s <= 0]
            if wins.size and losses.size and losses.mean() != 0:
                out[f'{col}_pf'] = round(float(wins.mean() / abs(losses.mean())), 2)
    if 'breakout_max_dd' in df.columns:
        s = pd.to_numeric(df['breakout_max_dd'], errors='coerce').dropna()
        if s.size:
            out['breakout_max_dd_avg'] = round(float(s.mean()), 2)
    return out


def _group_stats(df: pd.DataFrame, group_col: str, horizons, breakout=False) -> dict:
    result = {}
    if df.empty or group_col not in df.columns:
        return result
    for group, sub in df.groupby(group_col, dropna=False, observed=False):
        key = str(group) if pd.notna(group) else '缺失'
        result[key] = _breakout_stat(sub, horizons) if breakout else _stat(sub, horizons)
    return result


def _breakout_attribution(df_ev: pd.DataFrame, horizons) -> dict:
    if df_ev.empty:
        return {'n': 0, 'groups': {}, 'post_breakout_pullback': {}}
    data = df_ev.copy()
    realtime = data[(data['breakout_realtime'] == True) &
                    (data['breakout'] == True) &
                    (data['false_breakout'] != True)].copy()
    if realtime.empty:
        return {'n': 0, 'groups': {}, 'post_breakout_pullback': {}}
    for col in ('breakout_volume_ratio', 'breakout_pct_above_t0_high',
                't0_to_breakout_days', 'breakout_sector_strength',
                'breakout_fundamental_score'):
        realtime[col] = pd.to_numeric(realtime[col], errors='coerce')
    realtime['放量倍数'] = _quantile_labels(realtime['breakout_volume_ratio'])
    realtime['突破幅度'] = _quantile_labels(realtime['breakout_pct_above_t0_high'])
    realtime['距T0间隔'] = _quantile_labels(realtime['t0_to_breakout_days'])
    realtime['板块强度'] = _quantile_labels(realtime['breakout_sector_strength'])
    realtime['业绩预期'] = _quantile_labels(realtime['breakout_fundamental_score'])
    high = (realtime['放量倍数'] == '高').astype(int)
    high += (realtime['突破幅度'] == '高').astype(int)
    high += (realtime['距T0间隔'] == '低').astype(int)
    high += (realtime['板块强度'] == '高').astype(int)
    high += (realtime['业绩预期'] == '高').astype(int)
    complete_context = realtime['breakout_sector_known'] & realtime['breakout_fundamental_known']
    realtime['实时共振'] = pd.Series(pd.NA, index=realtime.index, dtype='object')
    realtime.loc[complete_context, '实时共振'] = pd.cut(
        high[complete_context], [-1, 1, 3, 5], labels=['弱共振', '中共振', '强共振']
    ).astype(str)
    groups = {}
    for col in ('放量倍数', '突破幅度', '距T0间隔', '板块强度', '业绩预期', '实时共振'):
        groups[col] = _group_stats(realtime, col, horizons, breakout=True)
    pull = realtime[realtime['first_pullback_hold'].notna()].copy()
    groups['首次回踩守住'] = _group_stats(pull, 'first_pullback_hold', horizons, breakout=True)
    pull['回踩深度分组'] = _quantile_labels(pull['first_pullback_depth'], ('浅', '中', '深'))
    groups['回踩深度'] = _group_stats(pull, '回踩深度分组', horizons, breakout=True)
    robust = []
    for name, sub in realtime.dropna(subset=['实时共振']).groupby('实时共振', observed=False):
        stat = _breakout_stat(sub, horizons)
        if stat.get('n', 0) >= 30 and stat.get('r_break_20_win', 0) >= 75:
            robust.append({'group': str(name), **stat})
    return {'n': int(len(realtime)), 'groups': groups,
            'candidate_high_win': robust,
            'coverage': {'sector_known': int(realtime['breakout_sector_known'].sum()),
                         'fundamental_known': int(realtime['breakout_fundamental_known'].sum())}}


# ==================== V3.0：右尾捕获统计 ====================

_RS_CLOSE_CACHE = {}   # date -> Series(close, index=ts_code)
_RS_MAP_CACHE = {}     # date -> {'rs5': Series(pct), 'rs10':..., 'rs20':...}
_MKT_R20_CACHE = {}    # date -> (th_10, th_05) 全市场T+20涨幅分位阈值


def _rs_series(loader, trade_date: str) -> pd.Series:
    """全市场单日收盘截面（缓存）"""
    if trade_date not in _RS_CLOSE_CACHE:
        cs = loader.query_cross_section(trade_date, fields=('ts_code', 'close'))
        if cs is None or cs.empty:
            _RS_CLOSE_CACHE[trade_date] = pd.Series(dtype=float)
        else:
            _RS_CLOSE_CACHE[trade_date] = pd.to_numeric(
                cs.set_index('ts_code')['close'], errors='coerce').dropna()
    return _RS_CLOSE_CACHE[trade_date]


def _rs_map(loader, trade_date: str, cal: list, cal_idx: dict):
    """指定交易日全市场 RS5/RS10/RS20 百分位图（缓存）"""
    if trade_date in _RS_MAP_CACHE:
        return _RS_MAP_CACHE[trade_date]
    pos = cal_idx.get(trade_date)
    if pos is None or pos < 20:
        _RS_MAP_CACHE[trade_date] = None
        return None
    c0 = _rs_series(loader, trade_date)
    if c0.empty:
        _RS_MAP_CACHE[trade_date] = None
        return None
    m = {}
    for h in (5, 10, 20):
        c1 = _rs_series(loader, cal[pos - h])
        if c1.empty:
            _RS_MAP_CACHE[trade_date] = None
            return None
        both = c0.div(c1.reindex(c0.index)).sub(1.0)
        both = both.replace([np.inf, -np.inf], np.nan).dropna()
        m[f'rs{h}'] = both.rank(pct=True) * 100.0
    _RS_MAP_CACHE[trade_date] = m
    return m


def _market_r20_th(loader, trade_date: str, cal: list, cal_idx: dict):
    """全市场自 trade_date 起 T+20 涨幅的 90%/95% 分位阈值（Top10/Top5 捕获基准）"""
    if trade_date in _MKT_R20_CACHE:
        return _MKT_R20_CACHE[trade_date]
    pos = cal_idx.get(trade_date)
    if pos is None or pos + 20 >= len(cal):
        _MKT_R20_CACHE[trade_date] = (np.nan, np.nan)
        return _MKT_R20_CACHE[trade_date]
    c0 = _rs_series(loader, trade_date)
    c20 = _rs_series(loader, cal[pos + 20])
    if c0.empty or c20.empty:
        _MKT_R20_CACHE[trade_date] = (np.nan, np.nan)
        return _MKT_R20_CACHE[trade_date]
    r = c0.div(c20.reindex(c0.index)).sub(1.0).replace([np.inf, -np.inf], np.nan).dropna() * 100.0
    if len(r) < 100:
        _MKT_R20_CACHE[trade_date] = (np.nan, np.nan)
        return _MKT_R20_CACHE[trade_date]
    _MKT_R20_CACHE[trade_date] = (float(r.quantile(0.90)), float(r.quantile(0.95)))
    return _MKT_R20_CACHE[trade_date]


def _tail_stat(df: pd.DataFrame, col: str = 'r_break_20') -> dict:
    """T+20 右尾分布统计（V3§十七）"""
    s = pd.to_numeric(df[col], errors='coerce').dropna() if col in df.columns else pd.Series(dtype=float)
    if s.empty:
        return {'n': 0}
    out = {'n': int(len(s))}
    out['mean'] = round(float(s.mean()), 2)
    out['median'] = round(float(s.median()), 2)
    out['p75'] = round(float(s.quantile(0.75)), 2)
    out['p90'] = round(float(s.quantile(0.90)), 2)
    out['max'] = round(float(s.max()), 2)
    for th in (10, 20, 30, 50):
        out[f'ge{th}'] = round(float((s >= th).mean() * 100), 1)
    out['win'] = round(float((s > 0).mean() * 100), 1)
    k10 = max(1, int(len(s) * 0.10))
    k5 = max(1, int(len(s) * 0.05))
    out['top10_avg'] = round(float(s.nlargest(k10).mean()), 2)
    out['top5_avg'] = round(float(s.nlargest(k5).mean()), 2)
    return out


def _v3_analysis(df_ev: pd.DataFrame, cfg: dict, cal: list, cal_idx: dict,
                 loader) -> dict:
    """V3 归因：校准表 + 门控消融 + 赢家贡献 + Top10/Top5 捕获率"""
    need = [c for c in ('breakout_realtime', 'breakout', 'false_breakout') if c in df_ev.columns]
    x = df_ev
    if need:
        m = (df_ev['breakout_realtime'].eq(True)) if 'breakout_realtime' in df_ev.columns else pd.Series(True, index=df_ev.index)
        if 'breakout' in df_ev.columns:
            m &= df_ev['breakout'].eq(True)
        if 'false_breakout' in df_ev.columns:
            m &= df_ev['false_breakout'].ne(True)
        x = df_ev[m].copy()
    if x.empty:
        return {'base': {'n': 0}}
    out = {}
    out['base'] = _tail_stat(x)

    # 状态组
    if 'v3_state' in x.columns:
        for st in ('PRIMARY_BUY', 'T20_ROCKET_WATCH', 'BREAKOUT_READY'):
            sub = x[x['v3_state'].eq(st)]
            if not sub.empty:
                out[f'group_{st}'] = _tail_stat(sub)

    # 校准表：按 expansion_score 分档（供 daily 运行时映射概率）
    if 'expansion_score' in x.columns:
        es = pd.to_numeric(x['expansion_score'], errors='coerce')
        edges = [0, 50, 60, 70, 80, 85, 90, 101]
        bands = []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            sub = x[(es >= lo) & (es < hi)]
            if sub.empty:
                continue
            st = _tail_stat(sub)
            bands.append({
                'lo': lo, 'hi': None if hi >= 101 else hi, 'n': st['n'],
                'p10': st['ge10'], 'p20': st['ge20'], 'p30': st['ge30'],
                'p50': st['ge50'], 'mean': st['mean'], 'median': st['median'],
                'p90': st['p90'], 'top10_avg': st['top10_avg'], 'top5_avg': st['top5_avg'],
            })
        out['calibration'] = {'bands': bands}

    # 门控消融：每个门控高分段 vs BASE（V3§十八）
    ab = {}
    def _gate(name, mask):
        sub = x[mask.reindex(x.index, fill_value=False)]
        if not sub.empty:
            ab[name] = _tail_stat(sub)
    if 'close_pos_grade' in x.columns:
        _gate('close_pos_AplusA', x['close_pos_grade'].isin(['A+', 'A']))
    if 'volume_grade' in x.columns:
        _gate('volume_AplusA', x['volume_grade'].isin(['A+', 'A']))
    if 'rs20' in x.columns:
        _gate('rs20_ge70', pd.to_numeric(x['rs20'], errors='coerce') >= 70)
    if 'exp_subs' in x.columns:
        def _sub_le(subname, thr):
            def f(row):
                try:
                    d = json.loads(row) if isinstance(row, str) else {}
                    return d.get(subname, 0) >= thr
                except Exception:
                    return False
            return x['exp_subs'].apply(f)
        _gate('compression_ge10', _sub_le('压缩结构', 10))
        _gate('supply_abs_ge12', _sub_le('供给吸收', 12))
    if 'breakout_fundamental_known' in x.columns:
        _gate('fundamental_known', x['breakout_fundamental_known'].eq(True))
    out['ablation'] = ab

    # 赢家贡献（V3§十九）
    s = pd.to_numeric(x['r_break_20'], errors='coerce').dropna()
    if len(s) >= 20:
        tot = float(s.sum())
        if tot > 0:
            k10 = int(len(s) * 0.10) or 1
            k5 = int(len(s) * 0.05) or 1
            top10_contrib = float(s.nlargest(k10).sum()) / tot * 100.0
            top5_contrib = float(s.nlargest(k5).sum()) / tot * 100.0
            out['winner'] = {
                'n': int(len(s)),
                'top10_contrib_pct': round(top10_contrib, 1),
                'top5_contrib_pct': round(top5_contrib, 1),
                'strategy_type': 'RIGHT_TAIL' if top10_contrib >= 60 else 'BROAD',
            }

    # Top10/Top5 捕获率（V3§十七）：相对全市场同日起点 T+20 涨幅
    if 'breakout_date' in x.columns and 'r_break_20' in x.columns:
        caps = {'top10': [], 'top5': []}
        for _, row in x.iterrows():
            r20 = pd.to_numeric(pd.Series([row['r_break_20']]), errors='coerce').iloc[0]
            if not np.isfinite(r20):
                continue
            th10, th5 = _market_r20_th(loader, str(row['breakout_date']), cal, cal_idx)
            if np.isfinite(th10):
                caps['top10'].append(1 if r20 >= th10 else 0)
                caps['top5'].append(1 if np.isfinite(th5) and r20 >= th5 else 0)
        if caps['top10']:
            out['capture'] = {
                'n': len(caps['top10']),
                'top10_capture_pct': round(float(np.mean(caps['top10'])) * 100.0, 1),
                'top5_capture_pct': round(float(np.mean(caps['top5'])) * 100.0, 1),
            }
    return out


def _load_config():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _universe_codes_period(loader: HvtDataLoader, det_dates: list, cfg: dict):
    """区间股票池：在若干采样日的横截面上应用过滤条件后取并集。"""
    import stock_cache as sc
    uni_cfg = cfg.get('universe', {})
    sb = sc.load_stock_basic()
    if sb is None or sb.empty:
        return []
    names = dict(zip(sb['ts_code'], sb.get('name', pd.Series(dtype=str))))
    min_mv = float(uni_cfg.get('min_market_cap', 30.0)) * 10000.0  # 万元

    sample_dates = det_dates[::60] if len(det_dates) > 60 else det_dates
    if det_dates[-1] not in sample_dates:
        sample_dates = list(sample_dates) + [det_dates[-1]]

    codes = set()
    for td in sample_dates:
        cs = loader.query_cross_section(td, fields=('ts_code', 'total_mv'))
        if cs is None or cs.empty:
            continue
        for _, r in cs.iterrows():
            code = r['ts_code']
            if not (code.endswith('.SH') or code.endswith('.SZ')):
                continue
            if code.startswith(('8', '4', '9')):
                continue
            name = str(names.get(code, ''))
            if uni_cfg.get('exclude_st', True) and ('ST' in name.upper() or '退' in name):
                continue
            total_mv = r.get('total_mv', np.nan)
            if np.isfinite(total_mv) and float(total_mv) < min_mv:
                continue
            codes.add(code)
    return sorted(codes)


def run_backtest(start: str = None, end: str = None, cfg: dict = None,
                 sample_step: int = 1, max_events: int = None, out_dir: str = None) -> dict:
    """事件回测主入口。

    Args:
        start/end: YYYYMMDD，默认取 config.backtest
        sample_step: 每 N 个检测日采样一次（1=每天）
        max_events: 限制事件总数（调试用）
    """
    if cfg is None:
        cfg = _load_config()
    bt = cfg.get('backtest', {})
    start = start or bt.get('start', '20250101')
    end = end or bt.get('end', '20260828')
    horizons = bt.get('horizons', [1, 3, 5, 10, 20])

    loader = HvtDataLoader()
    engine = HvtBullEngine(cfg)

    det_dates = loader.trade_dates(start, end)
    if not det_dates:
        print('[HVT-BT] 回测区间无交易日')
        return {}
    if sample_step > 1:
        det_dates = det_dates[::sample_step]
    det_set = set(det_dates)

    codes = _universe_codes_period(loader, det_dates, cfg)
    print(f"[HVT-BT] 区间 {start}~{end}，检测日 {len(det_dates)} 个，股票 {len(codes)} 只")

    anchor_date = str(cfg.get('hvt', {}).get('anchor_date', '') or '')
    # 数据加载起点强制锚定天量锚点日（默认 20240801），不再按 550 天动态回退
    hist_start = anchor_date or (pd.to_datetime(start) - pd.Timedelta(days=550)).strftime('%Y%m%d')
    fut_end = (pd.to_datetime(end) + pd.Timedelta(days=80)).strftime('%Y%m%d')

    cal_all = loader.trade_dates(hist_start, fut_end)
    cal_idx_all = {d: i for i, d in enumerate(cal_all)}

    events = []
    for k, code in enumerate(codes):
        df = loader.load(code, hist_start, fut_end)
        if df is None or len(df) < 260:
            continue
        dates = df['trade_date'].tolist()
        date_idx = {d: i for i, d in enumerate(dates)}
        closes = df['close'].to_numpy(dtype=float)
        highs = df['high'].to_numpy(dtype=float)
        lows = df['low'].to_numpy(dtype=float)
        vols = df['vol'].to_numpy(dtype=float)
        turnovers = df['turnover_rate'].to_numpy(dtype=float)
        last_ev_idx = -10 ** 9

        for idx in range(30, len(df)):
            if dates[idx] not in det_set:
                continue
            if idx - last_ev_idx < 10:  # 事件冷却：10 个交易日内不重复计数
                continue
            ev = engine.detect_hvt(df, idx)
            if ev is None:
                continue
            engine.evaluate_event(df, ev)
            if not engine.price_strength_ok(ev) and not ev.platform_breakout:
                continue
            last_ev_idx = idx

            # 完整跟踪（结局判定：锁筹/突破/出货/失败）
            engine.update_tracking(df, ev, end_idx=len(df))

            fwd = {}
            for h in horizons:
                j = idx + h
                fwd[f'r{h}'] = float(closes[j] / closes[idx] - 1.0) * 100.0 if j < len(closes) else np.nan
            w = closes[idx + 1:min(len(closes), idx + 21)]
            if len(w):
                fwd['max_gain'] = float(np.max(w) / closes[idx] - 1.0) * 100.0
                fwd['max_dd'] = float(np.min(w) / closes[idx] - 1.0) * 100.0
            # 锁筹后买点：T+5 收盘买入，T+20 收益
            if idx + 5 < len(closes) and idx + 20 < len(closes):
                fwd['r_lock5_20'] = float(closes[idx + 20] / closes[idx + 5] - 1.0) * 100.0
            # 二次突破买点：突破日收盘买入，其后 10/20 日收益
            if ev.breakout_date and ev.breakout_date in date_idx:
                b_idx = date_idx[ev.breakout_date]
                breakout_level = ev.t0_high * float(cfg.get('breakout', {}).get('confirm_ratio', 1.01))
                fwd['breakout_idx'] = b_idx
                fwd['breakout_date'] = ev.breakout_date
                fwd['breakout_realtime'] = bool(
                    ev.locked_chip and not ev.false_breakout
                )
                fwd['breakout_pct_above_t0_high'] = float(closes[b_idx] / ev.t0_high - 1.0) * 100.0
                fwd['t0_to_breakout_days'] = int(b_idx - idx)
                fwd['breakout_volume_ratio'] = float(
                    vols[b_idx] / np.nanmean(vols[max(0, b_idx - 20):b_idx])
                ) if b_idx > 0 and np.nanmean(vols[max(0, b_idx - 20):b_idx]) > 0 else np.nan
                sector_score, sector_name, sector_known = _sector_asof(code, ev.breakout_date)
                fund_score, fund_grade, fund_known = _fundamental_asof(code, ev.breakout_date)
                fwd['breakout_sector_strength'] = sector_score
                fwd['breakout_sector_name'] = sector_name
                fwd['breakout_sector_known'] = sector_known
                fwd['breakout_fundamental_score'] = fund_score
                fwd['breakout_fundamental_grade'] = fund_grade
                fwd['breakout_fundamental_known'] = fund_known
                fwd.update(_first_pullback(dates, closes, highs, lows, b_idx, breakout_level))
                if b_idx + 5 < len(closes):
                    fwd['r_break_5'] = float(closes[b_idx + 5] / closes[b_idx] - 1.0) * 100.0
                if b_idx + 10 < len(closes):
                    fwd['r_break_10'] = float(closes[b_idx + 10] / closes[b_idx] - 1.0) * 100.0
                if b_idx + 20 < len(closes):
                    fwd['r_break_20'] = float(closes[b_idx + 20] / closes[b_idx] - 1.0) * 100.0
                post = closes[b_idx + 1:min(len(closes), b_idx + 21)]
                if len(post):
                    fwd['breakout_max_dd'] = float(np.min(post) / closes[b_idx] - 1.0) * 100.0
                # ---- V3.0：突破日时点双评分（无前视，只用截至 b_idx 的数据） ----
                # 决策时点副本：update_tracking 用 end_idx=b_idx+1（截至突破日），
                # 使 locked/回撤/state 均为决策时信息，避免把突破后的失败结局泄漏进评分。
                if fwd['breakout_realtime']:
                    ev_dt = copy.copy(ev)
                    engine.update_tracking(df, ev_dt, end_idx=b_idx + 1)
                    rsm = _rs_map(loader, ev.breakout_date, cal_all, cal_idx_all)
                    if rsm:
                        engine.compute_rs(ev_dt, {ev.breakout_date: rsm}, ev.breakout_date)
                    engine.entry_score(ev_dt)
                    engine.expansion_score(df, ev_dt, asof_idx=b_idx)
                    engine.hard_veto(ev_dt)
                    engine.classify_v3(ev_dt)
                    fwd['entry_score'] = ev_dt.entry_score
                    fwd['expansion_score'] = ev_dt.expansion_score
                    fwd['r120'] = ev_dt.r120
                    fwd['r250'] = ev_dt.r250
                    fwd['tail_score'] = ev_dt.tail_score
                    fwd['tail_calibrated'] = ev_dt.tail_calibrated
                    fwd['rs5'] = round(ev_dt.rs5, 1) if np.isfinite(ev_dt.rs5) else np.nan
                    fwd['rs10'] = round(ev_dt.rs10, 1) if np.isfinite(ev_dt.rs10) else np.nan
                    fwd['rs20'] = round(ev_dt.rs20, 1) if np.isfinite(ev_dt.rs20) else np.nan
                    fwd['rs_accel'] = round(ev_dt.rs_accel, 1) if np.isfinite(ev_dt.rs_accel) else np.nan
                    fwd['close_pos_grade'] = ev_dt.close_pos_grade
                    fwd['volume_grade'] = ev_dt.volume_grade
                    fwd['hard_veto'] = '|'.join(ev_dt.hard_veto)
                    fwd['entry_subs'] = json.dumps(ev_dt.entry_subs, ensure_ascii=False)
                    fwd['exp_subs'] = json.dumps(ev_dt.exp_subs, ensure_ascii=False)
                    fwd['v3_state'] = ev_dt.state
                    # 决策时点的锁筹/回撤（供消融与复算，区别于全跟踪结局口径）
                    fwd['dt_locked'] = ev_dt.locked_chip
                    fwd['dt_post_dd'] = round(ev_dt.post_max_drawdown, 2)
                    fwd['dt_vol5_ratio'] = round(ev_dt.vol_5d_ratio, 2)
            fwd['locked'] = ev.locked_chip
            fwd['breakout'] = bool(ev.breakout_date)
            fwd['false_breakout'] = ev.false_breakout
            fwd['post_dd'] = ev.post_max_drawdown
            fwd['vol5_ratio'] = ev.vol_5d_ratio
            fwd['final_state'] = ev.state

            rec = ev.to_dict()
            rec.update(fwd)
            events.append(rec)

        loader._cache.pop((code, hist_start, fut_end), None)  # 控制内存
        if (k + 1) % 300 == 0:
            print(f"[HVT-BT] 股票 {k + 1}/{len(codes)}，累计事件 {len(events)}")
        if max_events and len(events) >= max_events:
            print(f"[HVT-BT] 达到事件上限 {max_events}，提前结束")
            break

    df_ev = pd.DataFrame(events)
    stats = {}
    if not df_ev.empty:
        stats['all'] = _stat(df_ev, horizons)
        stats['price_strong'] = _stat(df_ev[df_ev['t0_pct_chg'] >= 5], horizons)
        if 'locked' in df_ev.columns:
            stats['locked'] = _stat(df_ev[df_ev['locked'] == True], horizons)
            lk_brk = df_ev[(df_ev['locked'] == True) & (df_ev['breakout'] == True) & (df_ev['false_breakout'] != True)]
            stats['locked_breakout'] = _stat(lk_brk, horizons)
            stats['locked_only'] = _stat(df_ev[(df_ev['locked'] == True) & (df_ev['breakout'] != True)], horizons)
        stats['grade_A'] = _stat(df_ev[df_ev['hvt_grade'] == 'A'], horizons)
        stats['grade_B'] = _stat(df_ev[df_ev['hvt_grade'] == 'B'], horizons)
        stats['grade_C'] = _stat(df_ev[df_ev['hvt_grade'] == 'C'], horizons)
        if 'final_state' in df_ev.columns:
            stats['state_dist'] = {str(k2): int(v) for k2, v in df_ev['final_state'].value_counts().items()}

        # 三种买点对比（规格§27）
        bp = {}
        bp['t0_buy'] = _stat(df_ev, horizons)
        if 'r_lock5_20' in df_ev.columns:
            bp['t5_buy_all'] = _bp(df_ev['r_lock5_20'])
            if 'locked' in df_ev.columns:
                bp['t5_buy_locked'] = _bp(df_ev.loc[df_ev['locked'] == True, 'r_lock5_20'].dropna())
        brk_mask = (df_ev['breakout'] == True) & (df_ev['false_breakout'] != True)
        for key in ('r_break_10', 'r_break_20'):
            if key in df_ev.columns:
                bp[key] = _bp(df_ev.loc[brk_mask, key].dropna())
        stats['buy_points'] = bp
        stats['breakout_attribution'] = _breakout_attribution(df_ev, horizons)

        # ---- V3.0：右尾捕获分析 + 校准表 ----
        if cfg.get('v3', {}).get('enabled', True):
            stats['v3'] = _v3_analysis(df_ev, cfg, cal_all, cal_idx_all, loader)
            calib = stats['v3'].get('calibration')
            if calib:
                calib_path = cfg['v3'].get('calib_file')
                if calib_path:
                    os.makedirs(os.path.dirname(calib_path), exist_ok=True)
                    with open(calib_path, 'w', encoding='utf-8') as f:
                        json.dump({'bands': calib['bands'],
                                   'generated_at': end}, f, ensure_ascii=False, indent=2)

    out_dir = out_dir or cfg.get('report', {}).get('output_dir', os.path.join(BASE_DIR, 'report_daily'))
    os.makedirs(out_dir, exist_ok=True)
    out = {
        'start': start, 'end': end, 'n_events': len(events),
        'stats': stats,
    }
    with open(os.path.join(out_dir, f'hvt_bull_backtest_{start}_{end}.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    if not df_ev.empty:
        df_ev.to_csv(os.path.join(out_dir, f'hvt_bull_backtest_events_{start}_{end}.csv'),
                     index=False, encoding='utf-8-sig')
    print(f"[HVT-BT] 完成: 事件 {len(events)}")
    for k, v in stats.items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)[:300]}")
    return out


def _bp(s: pd.Series) -> dict:
    """买点统计：胜率/平均/中位/盈亏比"""
    s = s.dropna()
    if not s.size:
        return {'n': 0}
    wins = s[s > 0]
    losses = s[s <= 0]
    pf = float(wins.mean() / abs(losses.mean())) if wins.size and losses.size and losses.mean() != 0 else None
    return {
        'n': int(s.size),
        'win': round(float((s > 0).mean() * 100), 1),
        'avg': round(float(s.mean()), 2),
        'median': round(float(s.median()), 2),
        'pf': round(pf, 2) if pf is not None else None,
    }


def _stat(df: pd.DataFrame, horizons) -> dict:
    if df is None or df.empty:
        return {'n': 0}
    out = {'n': int(len(df))}
    for h in horizons:
        col = f'r{h}'
        if col in df.columns:
            s = df[col].dropna()
            if s.size:
                out[f'{col}_win'] = round(float((s > 0).mean() * 100), 1)
                out[f'{col}_avg'] = round(float(s.mean()), 2)
                out[f'{col}_median'] = round(float(s.median()), 2)
    for col in ('max_gain', 'max_dd'):
        if col in df.columns:
            s = df[col].dropna()
            if s.size:
                out[f'{col}_avg'] = round(float(s.mean()), 2)
    if 'post_dd' in df.columns:
        s = df['post_dd'].dropna()
        if s.size:
            out['post_dd_avg'] = round(float(s.mean()), 2)
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=None)
    ap.add_argument('--step', type=int, default=1)
    ap.add_argument('--max-events', type=int, default=None)
    args = ap.parse_args()
    run_backtest(start=args.start, end=args.end, sample_step=args.step, max_events=args.max_events)
