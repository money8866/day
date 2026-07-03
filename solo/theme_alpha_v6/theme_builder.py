#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 主题池构建
从 theme_stock_map_latest.json 加载主题-股票映射
"""
import os, sys, json, warnings
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")
import config


def build_theme_universe():
    """加载主题池，返回 {theme: [ts_code, ...]}"""
    if not os.path.exists(config.THEME_MAP_JSON):
        print(f"[ThemeBuilder] 主题缓存不存在: {config.THEME_MAP_JSON}")
        return {}
    with open(config.THEME_MAP_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    themes_raw = raw.get("themes", {})
    universe = {}
    for tname, stk_list in themes_raw.items():
        codes = []
        for s in stk_list:
            code = s.get("code") if isinstance(s, dict) else str(s)
            if code:
                codes.append(code)
        if len(codes) >= config.MIN_THEME_STOCKS:
            universe[tname] = codes
    print(f"[ThemeBuilder] 加载: {len(universe)} 个主题")
    return universe
