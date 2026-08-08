#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
同花顺扶摇 MCP 客户端 (fuyao-a-share)
通过 HTTP JSON-RPC 调用 MCP 工具，作为新浪 API 的补充数据源

端点: https://fuyao.aicubes.cn/mcp/a-share
协议: MCP JSON-RPC 2.0 over HTTP

使用方式:
    from fuyao_mcp_client import FuyaoMCPClient
    client = FuyaoMCPClient()
    zt_pool = client.get_limit_up_pool()
    hot_stocks = client.get_hot_stock_list()
"""

import json
import time
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

import requests

logger = logging.getLogger(__name__)

FUYAO_MCP_URL = "https://fuyao.aicubes.cn/mcp/a-share"
FUYAO_API_KEY = "sk-fuyao-KyUTn-mAykSTOaW87kgYghnZ2e2gJc0H"
REQUEST_TIMEOUT = 15  # 秒


class FuyaoMCPClient:
    """同花顺扶摇 MCP 客户端，封装涨停池、热股榜、飙升榜、龙虎榜等工具"""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-api-key": FUYAO_API_KEY,
        })
        self._request_id = 0
        self._initialized = False
        self._last_error = None
        self._error_count = 0
        self._max_errors = 5  # 连续失败N次后暂停

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _call(self, method: str, params: dict = None) -> Optional[dict]:
        """底层 JSON-RPC 调用"""
        if self._error_count >= self._max_errors:
            return None

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._next_id(),
        }

        try:
            resp = self._session.post(
                FUYAO_MCP_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                err_msg = data["error"].get("message", str(data["error"]))
                logger.warning(f"fuyao MCP error: {err_msg}")
                self._error_count += 1
                self._last_error = err_msg
                return None

            self._error_count = 0  # 成功则重置
            return data.get("result", {})

        except requests.exceptions.Timeout:
            logger.warning("fuyao MCP timeout")
            self._error_count += 1
            self._last_error = "timeout"
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"fuyao MCP request error: {e}")
            self._error_count += 1
            self._last_error = str(e)
            return None
        except json.JSONDecodeError:
            logger.warning("fuyao MCP invalid JSON response")
            self._error_count += 1
            self._last_error = "invalid json"
            return None

    def _ensure_initialized(self) -> bool:
        """确保 MCP 会话已初始化"""
        if self._initialized:
            return True

        result = self._call("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "fuyao-realtime-monitor",
                "version": "1.0"
            },
        })

        if result is not None:
            self._initialized = True
            logger.info("fuyao MCP initialized")
            return True
        return False

    def _call_tool(self, tool_name: str, args: dict = None) -> Optional[dict]:
        """调用 MCP 工具"""
        if not self._ensure_initialized():
            return None

        result = self._call("tools/call", {
            "name": tool_name,
            "arguments": args or {},
        })

        if result is None:
            return None

        # 解析 content
        content = result.get("content", [])
        if content and len(content) > 0:
            text = content[0].get("text", "")
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logger.warning(f"fuyao tool {tool_name}: invalid JSON in content")
                    return None

        return result

    # ──────────── 公开工具方法 ────────────

    def get_limit_up_pool(self, size: int = 20,
                          sort_field: str = "continue_day_cnt",
                          sort_dir: str = "desc") -> Optional[dict]:
        """
        涨停池 (服务端30秒缓存，盘中可放心轮询)
        返回: {code, data: {item: [{thscode, ticker, name, continue_day_cnt, ...}]}}
        """
        return self._call_tool("get_a_share_special_data_limit_up_pool", {
            "size": size,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
        })

    def get_hot_stock_list(self, period: str = "day") -> Optional[dict]:
        """
        热股榜
        period: day(24小时榜) / hour(小时榜)
        返回: {code, data: {item: [{thscode, ticker, name, rank, heat, rank_change, rank_trend}]}}
        """
        return self._call_tool("get_a_share_special_data_hot_stock_list", {
            "period": period,
        })

    def get_skyrocket_list(self, period: str = "day") -> Optional[dict]:
        """
        飙升榜
        period: day(日榜) / hour(小时榜)
        返回: {code, data: {item: [{thscode, ticker, name, rank, heat, rank_change, rank_trend}]}}
        """
        return self._call_tool("get_a_share_special_data_skyrocket_list", {
            "period": period,
        })

    def get_dragon_tiger_list(self, board_type: str = "all",
                              date: str = None) -> Optional[dict]:
        """
        龙虎榜
        board_type: all(全部) / org(机构榜) / hot_money(游资榜)
        date: YYYY-MM-DD, 缺省取最近交易日
        """
        args = {"board_type": board_type}
        if date:
            args["date"] = date
        return self._call_tool("get_a_share_special_data_dragon_tiger_list", args)

    def get_snapshot(self, thscodes: str) -> Optional[dict]:
        """
        行情快照 (批量)
        thscodes: 逗号分隔的 thscode 列表, 如 "600519.SH,000001.SZ"
        """
        return self._call_tool("get_a_share_prices_snapshot", {
            "thscodes": thscodes,
        })

    @property
    def is_healthy(self) -> bool:
        return self._error_count < self._max_errors

    @property
    def status(self) -> str:
        if self._error_count == 0:
            return "正常"
        if self._error_count < self._max_errors:
            return f"警告({self._error_count}次失败)"
        return f"暂停(连续{self._error_count}次失败: {self._last_error})"


# ──────────── 便捷函数 ────────────

def format_limit_up_summary(data: dict, top_n: int = 10) -> str:
    """格式化涨停池摘要"""
    if not data or data.get("code") != 0:
        return "涨停池数据获取失败"

    items = data.get("data", {}).get("item", [])
    if not items:
        return "今日无涨停股"

    lines = [f"📈 涨停池 Top{min(top_n, len(items))} (连板排序):"]
    for i, item in enumerate(items[:top_n], 1):
        name = item.get("name", "?")
        ticker = item.get("ticker", "?")
        days = item.get("continue_day_cnt", 0)
        lines.append(f"  {i:2}. {name}({ticker}) {days}连板")

    return "\n".join(lines)


def format_hot_stock_summary(data: dict, top_n: int = 10) -> str:
    """格式化热股榜摘要"""
    if not data or data.get("code") != 0:
        return "热股榜数据获取失败"

    items = data.get("data", {}).get("item", [])
    if not items:
        return "热股榜无数据"

    lines = [f"🔥 热股榜 Top{min(top_n, len(items))}:"]
    for item in items[:top_n]:
        name = item.get("name", "?")
        rank = item.get("rank", 0)
        trend = item.get("rank_trend", "flat")
        trend_emoji = {"up": "↑", "down": "↓", "flat": "→"}.get(trend, "")
        change = item.get("rank_change", 0)
        chg_str = f"+{change}" if change > 0 else str(change) if change < 0 else ""
        lines.append(f"  {rank:2}. {name} {trend_emoji}{chg_str}")

    return "\n".join(lines)


# ──────────── 自测 ────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("=" * 60)
    print("同花顺扶摇 MCP 客户端自测")
    print("=" * 60)

    client = FuyaoMCPClient()

    # 1. 涨停池
    t0 = time.time()
    zt = client.get_limit_up_pool(size=10)
    t1 = time.time()
    print(f"\n[涨停池] 耗时 {t1-t0:.2f}s, 状态: {client.status}")
    if zt:
        items = zt.get("data", {}).get("item", [])
        print(f"  返回 {len(items)} 只涨停股")
        if items:
            for item in items[:5]:
                print(f"  {item.get('name')}({item.get('ticker')}) {item.get('continue_day_cnt', 0)}连板")

    # 2. 热股榜
    t0 = time.time()
    hot = client.get_hot_stock_list()
    t1 = time.time()
    print(f"\n[热股榜] 耗时 {t1-t0:.2f}s, 状态: {client.status}")
    if hot:
        items = hot.get("data", {}).get("item", [])
        print(f"  返回 {len(items)} 只热股")
        if items:
            for item in items[:5]:
                print(f"  {item.get('rank')}. {item.get('name')} {item.get('rank_trend')}")

    # 3. 飙升榜
    t0 = time.time()
    sky = client.get_skyrocket_list()
    t1 = time.time()
    print(f"\n[飙升榜] 耗时 {t1-t0:.2f}s, 状态: {client.status}")
    if sky:
        items = sky.get("data", {}).get("item", [])
        print(f"  返回 {len(items)} 只飙升股")

    print(f"\n{'=' * 60}")
    print(f"客户端状态: {client.status}")
    print(f"{'=' * 60}")