# -*- coding: utf-8 -*-
"""热点主题扩池：V2.4 主题热度 -> 主题成员股票集合。

供 HVT-BULL(daily) 与 W7(w7_second_wave_engine) 共用的股票池准入扩展入口：
在「sli_v2 细分龙头 Top5」之外，把当期热点主题（theme_heat_v24_{date}.json，
指定窗口热度 >= heat_min）的全部成员并入准入池，解决「非龙头但属强势主题」的漏选
（案例：诺唯赞 688105.SH 20260922 天量、属创新药热点，但不在细分龙头 Top5 被过滤）。

口径（与 hvt_bull/context.py 对齐）：
- 热度 as-of：取不晚于 trade_date 的最近一期 theme_heat_v24_*.json，绝不用未来数据（§37.13）。
- 成员映射：theme_stock_map_v2_{date}.json 的 stocks 字段逐股 themes 反查（含全部成员层级）。
- 任一数据源缺失 -> 返回 None，调用方维持原过滤行为（不扩池、不误杀）。
"""

import glob
import json
import os

_BASE = r'D:\mystock\solo'
_HEAT_DIR = os.path.join(_BASE, 'report_daily')
_HEAT_PREFIX = 'theme_heat_v24_'
_MAP_DIR = r'D:\mystock\cache_daily'
_MAP_PREFIX = 'theme_stock_map_v2_'
_MAP_LATEST = os.path.join(_BASE, 'report_daily', 'theme_stock_map_latest_v2.json')
_WINDOW_FIELD = {'today': 'today_heat', 'week': 'week_heat', 'month': 'month_heat'}


def _heat_path_asof(trade_date: str):
    """取不晚于 trade_date 的最近一期 heat JSON 路径；无则 None。

    只认纯 8 位日期（排除 theme_heat_v24_hc_*.json 人工复核层）。
    """
    best = None
    for p in glob.glob(os.path.join(_HEAT_DIR, f'{_HEAT_PREFIX}*.json')):
        d = os.path.basename(p)[len(_HEAT_PREFIX):-len('.json')]
        if not (len(d) == 8 and d.isdigit()):
            continue
        if trade_date and d > str(trade_date):
            continue
        if best is None or d > best[0]:
            best = (d, p)
    return best[1] if best else None


def _map_path_asof(trade_date: str):
    """取不晚于 trade_date 的最近一期映射 JSON 路径；无则回退 latest 快照。"""
    best = None
    if trade_date:
        for p in glob.glob(os.path.join(_MAP_DIR, f'{_MAP_PREFIX}*.json')):
            d = os.path.basename(p)[len(_MAP_PREFIX):-len('.json')]
            if not (len(d) == 8 and d.isdigit()):
                continue
            if d > str(trade_date):
                continue
            if best is None or d > best[0]:
                best = (d, p)
    if best:
        return best[1]
    return _MAP_LATEST if os.path.exists(_MAP_LATEST) else None


def hot_theme_heat(trade_date: str, window: str = 'month', heat_min: float = 80.0):
    """返回 as-of trade_date 热度 >= heat_min 的 {主题名: 热度}；数据缺失返回 None。"""
    field = _WINDOW_FIELD.get(str(window).lower(), 'month_heat')
    path = _heat_path_asof(trade_date)
    if not path:
        return None
    try:
        with open(path, encoding='utf-8') as f:
            rows = json.load(f)
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = r.get('theme')
        try:
            h = float(r.get(field))
        except (TypeError, ValueError):
            continue
        if t and h >= float(heat_min):
            out[str(t)] = h
    return out or None


def hot_themes(trade_date: str, window: str = 'month', heat_min: float = 80.0):
    """返回 as-of trade_date 热度 >= heat_min 的主题名集合；数据缺失返回 None。"""
    m = hot_theme_heat(trade_date, window=window, heat_min=heat_min)
    return set(m) if m else None


def hot_theme_codes(trade_date: str, window: str = 'month', heat_min: float = 80.0):
    """热点主题成员股票集合（全部成员层级）；热度或映射缺失返回 None（调用方不扩池）。"""
    themes = hot_themes(trade_date, window=window, heat_min=heat_min)
    if not themes:
        return None
    path = _map_path_asof(trade_date)
    if not path:
        return None
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return None
    stocks = (raw or {}).get('stocks') or {}
    out = {str(c).strip() for c, r in stocks.items()
           if isinstance(r, dict) and (set(r.get('themes') or []) & themes)}
    return out or None


def stock_hot_themes(trade_date: str, window: str = 'month', heat_min: float = 80.0):
    """每股归属的热点主题明细：{ts_code: [(主题名, 热度), ...]}（按热度降序）。

    只保留「归属的热点主题」；热度或映射缺失返回 None。供报告「热点主题新增跟踪」节
    标注每只标的的来源主题与热度（HVT-BULL / W7 共用）。
    """
    heat = hot_theme_heat(trade_date, window=window, heat_min=heat_min)
    if not heat:
        return None
    path = _map_path_asof(trade_date)
    if not path:
        return None
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return None
    stocks = (raw or {}).get('stocks') or {}
    out = {}
    for code, info in stocks.items():
        if not isinstance(info, dict):
            continue
        hits = [(t, float(heat[t])) for t in (info.get('themes') or []) if t in heat]
        if hits:
            hits.sort(key=lambda x: -x[1])
            out[str(code).strip()] = hits
    return out or None
