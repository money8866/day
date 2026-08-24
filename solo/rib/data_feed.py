# -*- coding: utf-8 -*-
"""
数据获取层 - 从 MCP 数据源获取 A 股数据

支持：
  - 历史K线（前复权）
  - 实时行情快照
  - 股票筛选
  - 财务数据
  - 通过 UDC 缓存避免重复请求
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from .cache import UDCache


class DataFeed:
    """A 股数据获取器。"""

    def __init__(self, cache: Optional[UDCache] = None):
        self._cache = cache or UDCache()

    # ─────────────────────────────────────────
    # 历史K线
    # ─────────────────────────────────────────
    def get_daily_kline(
        self,
        thscode: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        count: int = 280,
        adjust: str = "forward",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取日线K线数据。

        Args:
            thscode: 股票代码，如 '600519.SH'
            start_date: 起始日期 'YYYYMMDD'，默认往前推 count 个交易日
            end_date: 结束日期 'YYYYMMDD'，默认今天
            count: 当无 start_date 时的回看条数
            adjust: 复权方式 forward/none/backward
            use_cache: 是否使用缓存

        Returns:
            DataFrame 包含 trade_date, open, high, low, close, vol, amount
        """
        if end_date is None:
            end_dt = datetime.now()
            end_date = end_dt.strftime("%Y%m%d")
        else:
            end_dt = datetime.strptime(end_date, "%Y%m%d")

        if start_date is None:
            start_dt = end_dt - timedelta(days=count * 2)  # 大约2倍交易日
            start_date = start_dt.strftime("%Y%m%d")

        cache_key = f"{thscode}_{start_date}_{end_date}_{adjust}"
        trade_date = end_date

        if use_cache:
            cached = self._cache.get_price(thscode, trade_date)
            if cached and isinstance(cached, dict) and cached.get("klines"):
                df = pd.DataFrame(cached["klines"])
                if len(df) >= 60:
                    return df

        # 从 MCP 获取
        try:
            start_ts = int(time.mktime(time.strptime(start_date, "%Y%m%d"))) * 1000
            end_ts = int(time.mktime(time.strptime(end_date, "%Y%m%d"))) * 1000

            from ._mcp_client import call_mcp

            result = call_mcp(
                "mcp_fuyao-a-share",
                "get_a_share_prices_historical",
                {
                    "thscode": thscode,
                    "start": start_ts,
                    "end": end_ts,
                    "interval": "1d",
                    "adjust": adjust,
                    "offset": 0,
                },
            )

            if result and "data" in result:
                klines = result["data"]
                df = self._parse_klines(klines)
                if len(df) >= 1:
                    self._cache.set_price(
                        thscode, trade_date, {"klines": df.to_dict("records")}
                    )
                return df
        except Exception:
            pass

        # 返回空 DataFrame
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "vol", "amount"])

    def _parse_klines(self, klines: list) -> pd.DataFrame:
        """解析 K 线数据为 DataFrame。"""
        records = []
        for item in klines:
            try:
                ts = item.get("timestamp", item.get("time", 0))
                if isinstance(ts, (int, float)) and ts > 0:
                    dt = datetime.fromtimestamp(ts / 1000) if ts > 1e12 else datetime.fromtimestamp(ts)
                    trade_date = dt.strftime("%Y%m%d")
                else:
                    trade_date = str(item.get("trade_date", item.get("date", "")))

                records.append({
                    "trade_date": trade_date,
                    "open": float(item.get("open", item.get("o", 0))),
                    "high": float(item.get("high", item.get("h", 0))),
                    "low": float(item.get("low", item.get("l", 0))),
                    "close": float(item.get("close", item.get("c", 0))),
                    "vol": float(item.get("volume", item.get("vol", item.get("v", 0)))),
                    "amount": float(item.get("amount", item.get("amt", 0))),
                })
            except (ValueError, TypeError):
                continue

        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    # ─────────────────────────────────────────
    # 实时行情快照
    # ─────────────────────────────────────────
    def get_snapshot(self, thscodes: Optional[str] = None) -> pd.DataFrame:
        """获取实时行情快照。"""
        try:
            from ._mcp_client import call_mcp

            params = {}
            if thscodes:
                params["thscodes"] = thscodes

            result = call_mcp(
                "mcp_fuyao-a-share",
                "get_a_share_prices_snapshot",
                params,
            )

            if result and "data" in result:
                records = []
                for item in result["data"]:
                    records.append({
                        "thscode": item.get("thscode", ""),
                        "name": item.get("name", item.get("stock_name", "")),
                        "price": float(item.get("price", item.get("close", 0))),
                        "change_pct": float(item.get("change_pct", item.get("pct_chg", 0))),
                        "volume": float(item.get("volume", 0)),
                        "amount": float(item.get("amount", 0)),
                        "turnover_rate": float(item.get("turnover_rate", 0)),
                    })
                return pd.DataFrame(records)
        except Exception:
            pass

        return pd.DataFrame(columns=["thscode", "name", "price", "change_pct", "volume", "amount"])

    # ─────────────────────────────────────────
    # 股票筛选
    # ─────────────────────────────────────────
    def screener(self, query: str) -> List[Dict]:
        """条件选股。"""
        try:
            from ._mcp_client import call_mcp

            result = call_mcp(
                "mcp_plugin_full-link-stock-analysis_mx-ds-mcp",
                "mx_stocks_screener",
                {"query": query},
            )

            if result and "data" in result:
                return result["data"]
        except Exception:
            pass

        return []

    # ─────────────────────────────────────────
    # 市场指数（用于市场环境判断）
    # ─────────────────────────────────────────
    def get_index_data(self, index_code: str = "000001.SH", days: int = 60) -> pd.DataFrame:
        """获取指数K线。"""
        try:
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=days * 2)
            start_ts = int(time.mktime(start_dt.timetuple())) * 1000
            end_ts = int(time.mktime(end_dt.timetuple())) * 1000

            from ._mcp_client import call_mcp

            result = call_mcp(
                "mcp_fuyao-a-share",
                "get_a_share_prices_historical",
                {
                    "thscode": index_code,
                    "start": start_ts,
                    "end": end_ts,
                    "interval": "1d",
                    "adjust": "none",
                },
            )

            if result and "data" in result:
                return self._parse_klines(result["data"])
        except Exception:
            pass

        return pd.DataFrame()


# ────────────────────────────────────────────
# 简易 MCP 客户端（通过 run_mcp 工具调用）
# ────────────────────────────────────────────
_MCP_CACHE: Dict = {}


def call_mcp(server: str, tool: str, args: dict) -> Optional[dict]:
    """调用 MCP 工具。

    此函数将由主程序注入实际实现。在回测或离线模式下，
    可通过 mock 方式替换。
    """
    from .. import run_mcp
    try:
        return run_mcp(server, tool, args)
    except Exception:
        return None
