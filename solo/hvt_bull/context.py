# -*- coding: utf-8 -*-
"""HVT-BULL 基本面与板块共振模块

- 基本面：fin_ind_2026H1_full.parquet -> FUNDAMENTAL_SCORE 0~100（S/A/B/C）
- 板块：theme_stock_map_v2_*.json（个股->主题）+ report_daily/theme_heat_v23_*.json
        -> SECTOR_STRENGTH 0~100（主题热度 today_heat，缺数据中性50）
- 资金质量：moneyflow parquet（缺失时中性50，评分权重内自动降级）
"""

import os
import glob
import json
import numpy as np
import pandas as pd

_BASE = r'D:\mystock\solo'
_FIN_IND = r'D:\mystock\cache_daily\fin_ind_2026H1_full.parquet'
_THEME_MAP = os.path.join(_BASE, 'report_daily', 'theme_stock_map_latest_v2.json')
_HEAT_DIR = os.path.join(_BASE, 'report_daily')
_HEAT_PREFIX = 'theme_heat_v23_'
_MF_DIR = os.path.join(_BASE, 'theme_alpha_v6', 'cache', 'parquet')

_FIN_IND_CACHE = {'df': None, 'loaded': False}
_HEAT_CACHE = {'map': None, 'loaded': False}


def _load_fin_ind() -> pd.DataFrame:
    if not _FIN_IND_CACHE['loaded']:
        _FIN_IND_CACHE['loaded'] = True
        if os.path.exists(_FIN_IND):
            try:
                _FIN_IND_CACHE['df'] = pd.read_parquet(_FIN_IND)
            except Exception:
                _FIN_IND_CACHE['df'] = None
    return _FIN_IND_CACHE['df']


def fundamental_score(ts_code: str) -> tuple:
    """返回 (score 0~100, grade S/A/B/C)。数据缺失返回 (0, 'C')，调用方按中性处理。"""
    df = _load_fin_ind()
    if df is None or df.empty:
        return 0.0, 'C'
    sub = df[df['ts_code'] == ts_code]
    if sub.empty:
        return 0.0, 'C'
    rec = sub.iloc[-1]

    dt_yoy = float(rec.get('dt_netprofit_yoy') or 0)
    tr_yoy = float(rec.get('tr_yoy') or 0)
    ocf_yoy = float(rec.get('ocf_yoy') or 0) if pd.notna(rec.get('ocf_yoy')) else 0.0
    roe = float(rec.get('roe') or 0) if pd.notna(rec.get('roe')) else 0.0

    cashflow_ok = ocf_yoy > 0
    if dt_yoy > 30 and tr_yoy > 10 and cashflow_ok:
        return 90.0, 'S'
    if dt_yoy > 30 or tr_yoy > 20:
        return 78.0, 'A'
    if dt_yoy > 15:
        return 65.0, 'A'
    if dt_yoy > 0 or tr_yoy > 10:
        return 55.0, 'B'
    return 40.0, 'C'


def load_stock_themes(trade_date: str = None) -> dict:
    """返回 {ts_code: [theme,...]} 个股->主题反查索引。优先精确日期，回退 latest_v2。

    必须读顶层 stocks 字段（逐股 themes），不能反查 themes 列表：后者按主题截断
    （如"消费"只留 300 只），4929 只中有 831 只见于 stocks 而查不到，会被误判无板块。
    """
    candidates = []
    if trade_date:
        candidates.append(rf'D:\mystock\cache_daily\theme_stock_map_v2_{trade_date}.json')
    candidates.append(_THEME_MAP)
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as f:
                    raw = json.load(f)
                stocks = raw.get('stocks')
                if isinstance(stocks, dict) and stocks:
                    out = {c: list(r.get('themes') or [])
                           for c, r in stocks.items()
                           if isinstance(r, dict) and r.get('themes')}
                    if out:
                        return out
            except Exception:
                continue
    return {}


def _load_theme_heat() -> dict:
    """读 report_daily/theme_heat_v23_*.json，转成 {trade_date: {theme: today_heat}}。

    today_heat（V2.3§10）= 0.6·rank(Price) + 0.3·rank(Breadth) + 0.1·rank(Activity)，
    三个分量都是横截面 Percentile Rank，故 today_heat 本身即 0~100 分位口径
    （32 主题实测中位≈41.5），与全链路「50≈中性」约定同量纲，无需再做分位变换。
    旧源 theme_v21_daily.strength_v3 是绝对合成值（中位仅 33.9），已弃用。
    """
    if not _HEAT_CACHE['loaded']:
        _HEAT_CACHE['loaded'] = True
        out = {}
        pat = os.path.join(_HEAT_DIR, f'{_HEAT_PREFIX}*.json')
        for p in sorted(glob.glob(pat)):
            d = os.path.basename(p)[len(_HEAT_PREFIX):-len('.json')]
            try:
                with open(p, encoding='utf-8') as f:
                    raw = json.load(f)
            except Exception:
                continue
            if not isinstance(raw, list):
                continue
            day = {}
            for r in raw:
                if not isinstance(r, dict):
                    continue
                t, h = r.get('theme'), r.get('today_heat')
                if t and h is not None:
                    day[t] = float(h)
            if day:
                out[d] = day
        _HEAT_CACHE['map'] = out
    return _HEAT_CACHE['map'] or {}


def _theme_strength_asof(theme: str, trade_date: str = None) -> float:
    """主题热度 as-of：取不晚于 trade_date 的最近一期；trade_date 为空取最新。

    绝不使用晚于决策日的数据（§37.13 无未来函数）。无数据返回 NaN。
    """
    m = _load_theme_heat()
    if not m:
        return np.nan
    if trade_date:
        days = [d for d in m if d <= str(trade_date)]
        if not days:
            return np.nan
    else:
        days = list(m)
    return float(m[max(days)].get(theme, np.nan))


def sector_resonance(ts_code: str, stock_themes: dict, trade_date: str = None) -> tuple:
    """返回 (sector_strength 0~100, sector_name)。无主题归属/无热度数据时 (50, '')。

    强度口径：theme_heat_v23 的主题热度 today_heat（as-of 决策日），多主题取最强。
    旧版用「命中主题数」造强度（55+8n，恒 63~79），与主题实际强弱无关，已废弃。
    """
    themes = (stock_themes or {}).get(ts_code) or []
    if not themes:
        return 50.0, ''
    best_s, best_t = np.nan, ''
    for t in themes:
        s = _theme_strength_asof(t, trade_date)
        if np.isnan(s):
            continue
        if np.isnan(best_s) or s > best_s:
            best_s, best_t = s, t
    if np.isnan(best_s):
        # 有主题归属但无热度数据 → 强度中性，仍返回主题名
        return 50.0, themes[0]
    return float(best_s), best_t


def money_quality(ts_code: str, trade_date: str, cfg: dict = None) -> float:
    """天量日资金质量 0~100。parquet 缺失返回 50（中性）。"""
    p = os.path.join(_MF_DIR, f'moneyflow_{trade_date}.parquet')
    if not os.path.exists(p):
        return 50.0
    try:
        df = pd.read_parquet(p)
    except Exception:
        return 50.0
    row = df[df['ts_code'] == ts_code]
    if row.empty:
        return 50.0
    r = row.iloc[-1]
    net_mf = float(r.get('net_mf_amount') or 0.0)      # 万元
    # 成交额换算：daily_cache amount 单位千元 -> 万元
    amt_w = 0.0
    try:
        import sqlite3
        with sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db') as conn:
            a = pd.read_sql("SELECT amount FROM daily_cache WHERE ts_code=? AND trade_date=?",
                            conn, params=(ts_code, trade_date))
        if not a.empty:
            amt_w = float(a['amount'].iloc[0]) / 10.0
    except Exception:
        pass
    score = 50.0
    if net_mf > 0:
        score += 15.0
        if amt_w > 0:
            ratio = net_mf / amt_w * 100.0
            cap = float((cfg or {}).get('moneyflow', {}).get('net_mf_strength_cap', 5.0))
            score += min(25.0, ratio / cap * 25.0)
    elif net_mf < 0:
        score -= 15.0
        if amt_w > 0 and (net_mf / amt_w) < -0.03:
            score -= 10.0
    lg = float(r.get('buy_lg_amount') or 0.0) - float(r.get('sell_lg_amount') or 0.0)
    if lg > 0:
        score += 10.0
    return float(max(0.0, min(100.0, score)))
