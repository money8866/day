# -*- coding: utf-8 -*-
"""
EDB 数据层
================================================================================
全部复用项目既有缓存，禁止直接调 pro.daily：
  ① 日线（含复权/去未来函数截断）  → bts.data.load_daily（DB + TDX 双源）
  ② 股票池/行业                    → cache_daily/stock_basic.csv
  ③ HVT 结构状态（7 态归一化词表）  → sector_0915/data/stock_structure_score.csv
  ④ 基本面（中报/单季同比）        → cache_daily/fin_ind_2026H1_full.parquet
  ⑤ 事件（业绩预告/快报/大股东减持）→ Tushare + 本地 parquet 缓存

无未来函数：行情仅用 trade_date <= scan_date；财报仅用 ann_date <= scan_date；
           事件仅用 ann_date <= scan_date 且落在回看窗口内。
"""
import os
import functools
from typing import Optional

import numpy as np
import pandas as pd

import stock_cache as sc
from bts.data import (load_daily, get_stock_pool, get_name_map,
                      get_trade_dates, last_trade_date_on_or_before, to_ts_code)

from .config import (EDB_CONFIG, STOCK_BASIC_CSV, FIN_IND_PARQUET,
                     HVT_SCORE_CSV, EVENT_CACHE)

__all__ = [
    'load_daily', 'get_stock_pool', 'get_name_map', 'get_trade_dates',
    'last_trade_date_on_or_before', 'to_ts_code',
    'load_industry_map', 'load_hvt_map', 'load_hvt_panel',
    'load_fundamental_map', 'load_event_map', 'events_available',
]


# ═══════════════════════════════════════════════════════════
# 行业
# ═══════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=1)
def load_industry_map() -> dict:
    """ts_code -> industry（严格对齐 stock_basic.csv 行业口径）"""
    if not os.path.exists(STOCK_BASIC_CSV):
        return {}
    sb = pd.read_csv(STOCK_BASIC_CSV, dtype={'ts_code': str}, usecols=['ts_code', 'industry'])
    sb['industry'] = sb['industry'].fillna('').astype(str)
    return dict(zip(sb['ts_code'], sb['industry']))


# ═══════════════════════════════════════════════════════════
# HVT 结构状态（7 态归一化）
# ═══════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=1)
def load_hvt_panel() -> pd.DataFrame:
    """HVT 结构面板全量（一次读入，后续按日期切片）。

    列：trade_date / ts_code / hvt_state / hvt_stage / hvt_quality_score /
        structure_state / structure_quality
    """
    if not os.path.exists(HVT_SCORE_CSV):
        return pd.DataFrame()
    cols = ['trade_date', 'ts_code', 'hvt_state', 'hvt_stage',
            'hvt_quality_score', 'structure_state', 'structure_quality']
    try:
        df = pd.read_csv(HVT_SCORE_CSV, dtype={'trade_date': str, 'ts_code': str},
                         usecols=lambda c: c in cols)
    except Exception:
        return pd.DataFrame()
    if 'trade_date' not in df.columns:
        return pd.DataFrame()
    return df


def _hvt_band(state: str, quality: float) -> str:
    """7 态 → HVT_STRONG / HVT_NORMAL / HVT_WEAK（词表映射见 config 注释）"""
    cfg = EDB_CONFIG
    s = str(state or '')
    if s in cfg['hvt_strong_states']:
        return 'HVT_STRONG' if (quality is not None and quality >= cfg['hvt_strong_quality']) else 'HVT_NORMAL'
    if s in cfg['hvt_normal_states']:
        return 'HVT_NORMAL'
    return 'HVT_WEAK'


def load_hvt_map(scan_date: str) -> dict:
    """{ts_code: {'hvt_state','hvt_band','hvt_quality','hvt_date'}}

    每只股票取 <=scan_date 的最后一个截面（面板为部分覆盖，逐股取最近截面可避免
    因某一交易日截面股票数少而大量缺失）；同时给出 hvt_date 以便判断时效。
    """
    df = load_hvt_panel()
    if df.empty:
        return {}
    sub = df[df['trade_date'] <= str(scan_date)]
    if sub.empty:
        return {}
    sub = sub.sort_values('trade_date').drop_duplicates(subset='ts_code', keep='last')
    out = {}
    for r in sub.itertuples(index=False):
        q = getattr(r, 'hvt_quality_score', np.nan)
        q = float(q) if pd.notna(q) else np.nan
        state = getattr(r, 'hvt_state', '')
        out[r.ts_code] = {
            'hvt_state': str(state),
            'hvt_band': _hvt_band(state, q),
            'hvt_quality': q,
            'hvt_date': str(r.trade_date),
        }
    return out


# ═══════════════════════════════════════════════════════════
# 基本面（中报 + 单季同比）
# ═══════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=1)
def _fin_panel() -> pd.DataFrame:
    if not os.path.exists(FIN_IND_PARQUET):
        return pd.DataFrame()
    keep = ['ts_code', 'ann_date', 'end_date', 'netprofit_yoy', 'dt_netprofit_yoy',
            'or_yoy', 'tr_yoy', 'roe', 'grossprofit_margin', 'debt_to_assets',
            'q1_profit_yoy', 'q2_profit_yoy']
    try:
        df = pd.read_parquet(FIN_IND_PARQUET)
    except Exception:
        return pd.DataFrame()
    cols = [c for c in keep if c in df.columns]
    df = df[cols].copy()
    df['ts_code'] = df['ts_code'].astype(str)
    df['ann_date'] = df['ann_date'].astype(str)
    return df


def load_fundamental_map(scan_date: str) -> dict:
    """{ts_code: {'status','np_yoy','q2_yoy',...}}，仅用 ann_date <= scan_date

    status: FUNDAMENTAL_CONFIRM（改善）/ FUNDAMENTAL_CONFLICT（恶化）/ NEUTRAL / NA
    """
    df = _fin_panel()
    if df.empty:
        return {}
    df = df[df['ann_date'] <= str(scan_date)]
    if df.empty:
        return {}
    df = df.sort_values('ann_date').drop_duplicates(subset='ts_code', keep='last')

    def _v(x):
        return float(x) if pd.notna(x) else np.nan

    out = {}
    for r in df.itertuples(index=False):
        np_yoy = _v(getattr(r, 'dt_netprofit_yoy', np.nan))
        if np.isnan(np_yoy):
            np_yoy = _v(getattr(r, 'netprofit_yoy', np.nan))
        q2 = _v(getattr(r, 'q2_profit_yoy', np.nan))
        q1 = _v(getattr(r, 'q1_profit_yoy', np.nan))
        # 改善：扣非同比正增长且单季不弱化
        improved = (not np.isnan(np_yoy) and np_yoy >= 20) or \
                   (not np.isnan(q2) and q2 >= 30) or \
                   (not np.isnan(q1) and not np.isnan(q2) and q2 > q1 + 10)
        # 恶化：扣非大幅下滑或单季崩塌
        worsened = (not np.isnan(np_yoy) and np_yoy <= -25) or \
                   (not np.isnan(q2) and q2 <= -30)
        if worsened:
            status = 'FUNDAMENTAL_CONFLICT'
        elif improved:
            status = 'FUNDAMENTAL_CONFIRM'
        else:
            status = 'NEUTRAL'
        out[r.ts_code] = {
            'status': status, 'np_yoy': np_yoy, 'q2_yoy': q2, 'q1_yoy': q1,
            'roe': _v(getattr(r, 'roe', np.nan)),
            'debt_to_assets': _v(getattr(r, 'debt_to_assets', np.nan)),
            'ann_date': str(getattr(r, 'ann_date', '')),
        }
    return out


# ═══════════════════════════════════════════════════════════
# 事件污染（业绩预告 / 业绩快报 / 大股东减持）
# ═══════════════════════════════════════════════════════════
_EVENT_STATE = {'available': False, 'note': ''}


def events_available() -> bool:
    return _EVENT_STATE['available']


def _read_event_cache() -> pd.DataFrame:
    if os.path.exists(EVENT_CACHE):
        try:
            df = pd.read_parquet(EVENT_CACHE)
            df['ts_code'] = df['ts_code'].astype(str)
            df['ann_date'] = df['ann_date'].astype(str)
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _fetch_events(trade_dates) -> pd.DataFrame:
    """按交易日拉取事件（Tushare）。权限不足/接口异常时降级为空。"""
    pro = sc._get_pro()
    rows = []
    notes = []
    # ① 业绩预告（forecast 只支持单日 ann_date 或单股）
    hit_forecast = 0
    for d in trade_dates:
        try:
            df = pro.forecast(ann_date=str(d))
            if df is not None and len(df):
                hit_forecast += len(df)
                for r in df.itertuples(index=False):
                    t = str(getattr(r, 'type', '') or '')
                    pmin = getattr(r, 'p_change_min', np.nan)
                    pmax = getattr(r, 'p_change_max', np.nan)
                    et = '业绩预告'
                    if t in ('预增', '扭亏', '略增', '续盈'):
                        et = '业绩预告'
                    if pd.notna(pmax) and float(pmax) >= EDB_CONFIG['event_surge_yoy']:
                        et = '业绩暴增'
                    if t in ('预减', '首亏', '续亏', '略减'):
                        et = '业绩预减'
                    rows.append({'ts_code': str(r.ts_code), 'ann_date': str(r.ann_date),
                                 'event_type': et, 'detail': f'{t} {pmin}~{pmax}%'})
        except Exception as e:
            notes.append(f'forecast:{str(e)[:60]}')
            break
    # ② 业绩快报
    try:
        s, e = str(trade_dates[0]), str(trade_dates[-1])
        df = pro.express(start_date=s, end_date=e)
        if df is not None and len(df):
            for r in df.itertuples(index=False):
                yoy = getattr(r, 'yoy_net_profit', np.nan)
                et = '业绩快报'
                if pd.notna(yoy) and float(yoy) >= EDB_CONFIG['event_surge_yoy']:
                    et = '业绩暴增'
                rows.append({'ts_code': str(r.ts_code), 'ann_date': str(r.ann_date),
                             'event_type': et, 'detail': f'快报 yoy={yoy}'})
    except Exception as ex:
        notes.append(f'express:{str(ex)[:60]}')
    # ③ 大股东增减持（只标记减持）
    try:
        df = pro.stk_holdertrade(start_date=str(trade_dates[0]), end_date=str(trade_dates[-1]))
        if df is not None and len(df):
            df = df[df['in_de'].astype(str).str.upper() == 'DE']
            for r in df.itertuples(index=False):
                cr = getattr(r, 'change_ratio', np.nan)
                if pd.notna(cr) and float(cr) < EDB_CONFIG['event_holdertrade_min_ratio']:
                    continue
                rows.append({'ts_code': str(r.ts_code), 'ann_date': str(r.ann_date),
                             'event_type': '大股东减持',
                             'detail': f"减持{cr}% {getattr(r, 'holder_name', '')}"})
    except Exception as ex:
        notes.append(f'holdertrade:{str(ex)[:60]}')

    if notes:
        _EVENT_STATE['note'] = '；'.join(sorted(set(notes)))
    if not rows:
        return pd.DataFrame(columns=['ts_code', 'ann_date', 'event_type', 'detail'])
    return pd.DataFrame(rows)


def load_event_map(scan_date: str, use_api: bool = True) -> dict:
    """{ts_code: {'event_driven': True, 'types': [...]}}，窗口 = 最近 N 个交易日

    事件数据不可得时返回空 dict，并由 events_available() 暴露降级状态，
    绝不用价格行为臆造「事件驱动」。
    """
    cfg = EDB_CONFIG
    trade_dates = get_trade_dates(_shift_date(scan_date, 60), scan_date)
    win = trade_dates[-cfg['event_lookback_days']:] if trade_dates else []
    if not win:
        _EVENT_STATE['available'] = False
        _EVENT_STATE['note'] = '交易日历为空'
        return {}

    cached = _read_event_cache()
    if not cached.empty:
        cached = cached[cached['ann_date'] <= str(scan_date)]
    # 缓存未覆盖窗口内的任一日 → 需要补拉
    need_fetch = use_api and (cached.empty or
                              not set(win).issubset(set(cached['ann_date'].unique())))
    if need_fetch:
        fresh = _fetch_events(win)
        if not fresh.empty:
            merged = pd.concat([cached, fresh]).drop_duplicates(
                subset=['ts_code', 'ann_date', 'event_type'], keep='last')
            try:
                os.makedirs(os.path.dirname(EVENT_CACHE), exist_ok=True)
                merged.to_parquet(EVENT_CACHE, index=False)
            except Exception:
                pass
            cached = merged

    if cached.empty:
        _EVENT_STATE['available'] = False
        if not _EVENT_STATE['note']:
            _EVENT_STATE['note'] = '无事件数据（接口权限不足或本地缓存缺失）'
        return {}

    _EVENT_STATE['available'] = True
    sub = cached[cached['ann_date'].isin(win)]
    out = {}
    for code, g in sub.groupby('ts_code'):
        types = sorted(set(g['event_type'].astype(str)))
        out[code] = {'event_driven': True, 'types': types,
                     'detail': '；'.join(f"{r.event_type}({r.ann_date})"
                                        for r in g.head(3).itertuples(index=False))}
    return out


def _shift_date(date_str: str, days: int) -> str:
    import datetime
    d = datetime.datetime.strptime(str(date_str), '%Y%m%d') - datetime.timedelta(days=days)
    return d.strftime('%Y%m%d')
