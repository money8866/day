# -*- coding: utf-8 -*-
"""
MCP 客户端封装 - 通过 run_mcp 工具调用数据源

提供统一的 call_mcp 接口，支持本地 mock 模式。
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional


# ── 本地 Mock 数据（无网络/无权限时使用） ──
_MOCK_MODE = False
_MOCK_DATA_CACHE: Dict[str, Any] = {}


def set_mock_mode(enabled: bool = True):
    """设置 mock 模式。"""
    global _MOCK_MODE
    _MOCK_MODE = enabled


def call_mcp(server: str, tool: str, args: dict) -> Optional[dict]:
    """调用 MCP 工具。

    实际实现依赖运行环境中的 run_mcp 工具。
    在没有 MCP 服务时返回 None。
    """
    if _MOCK_MODE:
        return _mock_call(server, tool, args)

    # 实际运行时由 Trae 环境注入 run_mcp
    try:
        # 尝试使用全局 run_mcp
        from .. import run_mcp as _run_mcp
        return _run_mcp(server, tool, args)
    except (ImportError, AttributeError):
        pass

    # 备选：尝试直接调用（在 Trae Exec 环境中）
    try:
        return _call_via_exec(server, tool, args)
    except Exception:
        return None


def _call_via_exec(server: str, tool: str, args: dict) -> Optional[dict]:
    """通过 Exec 环境调用 MCP。"""
    import asyncio
    import json as _json

    # 此函数会在 Trae Exec 环境中被覆盖
    return None


def _mock_call(server: str, tool: str, args: dict) -> Optional[dict]:
    """Mock 数据返回。"""
    cache_key = f"{server}:{tool}:{json.dumps(args, sort_keys=True)}"

    if cache_key in _MOCK_DATA_CACHE:
        return _MOCK_DATA_CACHE[cache_key]

    # 生成模拟K线数据
    if tool == "get_a_share_prices_historical":
        return _generate_mock_klines(args)

    return None


def _generate_mock_klines(args: dict) -> dict:
    """生成模拟K线。"""
    import numpy as np

    thscode = args.get("thscode", "000001.SZ")
    start = args.get("start", 0)
    end = args.get("end", 0)

    # 转换时间戳为日期
    from datetime import datetime, timedelta

    if start > 1e12:
        start = start / 1000
    if end > 1e12:
        end = end / 1000

    start_date = datetime.fromtimestamp(start)
    end_date = datetime.fromtimestamp(end)

    days = (end_date - start_date).days
    n = max(60, min(days, 280))

    # 生成模拟数据
    np.random.seed(hash(thscode) % (2**31))
    base_price = 20 + np.random.random() * 80
    returns = np.random.normal(0.0005, 0.02, n)
    prices = [base_price]
    for r in returns[1:]:
        prices.append(prices[-1] * (1 + r))

    klines = []
    for i in range(n):
        dt = start_date + timedelta(days=i)
        price = prices[i]
        daily_range = price * 0.02
        klines.append({
            "timestamp": int(dt.timestamp() * 1000),
            "trade_date": dt.strftime("%Y%m%d"),
            "open": round(price - daily_range / 2, 2),
            "high": round(price + daily_range, 2),
            "low": round(price - daily_range, 2),
            "close": round(price, 2),
            "volume": int(np.random.uniform(1e6, 1e7)),
            "amount": int(np.random.uniform(1e8, 1e9)),
        })

    result = {"data": klines}
    _MOCK_DATA_CACHE[f"mock_klines_{thscode}"] = result
    return result
