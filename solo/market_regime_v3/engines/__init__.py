# -*- coding: utf-8 -*-
"""Market Regime V3 引擎包"""

import os

_CACHE_DIR = r"D:\mystock\cache_daily"
_REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "report_daily")


def resolve_theme_stock_map_path(trade_date: str = None) -> str:
    """按交易日解析 theme_stock_map 路径

    优先级:
      1. report_daily/theme_stock_map_latest_v2.json   (用户程序生成的最新v2版)
      2. cache_daily/theme_stock_map_v2_{trade_date}.json  (v2精确日期)
      3. cache_daily/theme_stock_map_{trade_date}.json     (v1精确日期)
      4. cache_daily/theme_stock_map_latest.json           (v1最新兜底)

    Returns:
        找到的第一个存在的文件路径；全部不存在时返回 latest v1 路径
    """
    patterns = []
    # 最高优先级：用户程序生成的最新 v2
    patterns.append(os.path.join(_REPORT_DIR, "theme_stock_map_latest_v2.json"))
    # 精确日期 v2
    if trade_date:
        patterns.append(os.path.join(_CACHE_DIR, f"theme_stock_map_v2_{trade_date}.json"))
        patterns.append(os.path.join(_CACHE_DIR, f"theme_stock_map_{trade_date}.json"))
    # 兜底
    patterns.append(os.path.join(_CACHE_DIR, "theme_stock_map_latest.json"))

    for full in patterns:
        if os.path.exists(full):
            return full

    return os.path.join(_CACHE_DIR, "theme_stock_map_latest.json")
