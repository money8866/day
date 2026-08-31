# -*- coding: utf-8 -*-
"""HVT-BULL 基本面与板块共振模块

- 基本面：fin_ind_2026H1_full.parquet -> FUNDAMENTAL_SCORE 0~100（S/A/B/C）
- 板块：theme_stock_map_latest_v2.json -> SECTOR_STRENGTH 0~100
- 资金质量：moneyflow parquet（缺失时中性50，评分权重内自动降级）
"""

import os
import json
import numpy as np
import pandas as pd

_BASE = r'D:\mystock\solo'
_FIN_IND = r'D:\mystock\cache_daily\fin_ind_2026H1_full.parquet'
_THEME_MAP = os.path.join(_BASE, 'report_daily', 'theme_stock_map_latest_v2.json')
_MF_DIR = os.path.join(_BASE, 'theme_alpha_v6', 'cache', 'parquet')

_FIN_IND_CACHE = {'df': None, 'loaded': False}


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


def load_theme_map(trade_date: str = None) -> dict:
    """返回 {theme_name: [ts_code,...]}。优先精确日期，回退 latest_v2。"""
    candidates = []
    if trade_date:
        candidates.append(rf'D:\mystock\cache_daily\theme_stock_map_v2_{trade_date}.json')
    candidates.append(_THEME_MAP)
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as f:
                    raw = json.load(f)
                themes = raw.get('themes', raw)
                if isinstance(themes, dict):
                    return {k: [it.get('code') for it in v if isinstance(it, dict)]
                            for k, v in themes.items()}
            except Exception:
                continue
    return {}


def sector_resonance(ts_code: str, theme_map: dict, trade_date: str = None) -> tuple:
    """返回 (sector_strength 0~100, sector_name)。无映射时 (50, '')."""
    if not theme_map:
        return 50.0, ''
    hit = [t for t, codes in theme_map.items() if ts_code in codes]
    if not hit:
        return 50.0, ''
    # 简化：命中主题数即广度，主主题取第一个
    strength = min(100.0, 55.0 + 8.0 * len(hit))
    return strength, hit[0]


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
    # 成交额换算：stk_factor_pro amount 单位千元 -> 万元
    amt_w = 0.0
    try:
        import sqlite3
        with sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db') as conn:
            a = pd.read_sql("SELECT amount FROM stk_factor_pro WHERE ts_code=? AND trade_date=?",
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
