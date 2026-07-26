"""Market Data Fetcher — 获取市场整体数据.

提供指数行情、全市场宽度、情绪指标等，
供 Market Regime Engine 使用。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Tushare 限速
_TUSHARE_LOCK = threading.Lock()
_LAST_TUSHARE_CALL: float = 0.0

# 指数列表
INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
}

# 项目根目录 (用于CSV缓存)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _rate_limit():
    """Tushare 限速: 至少间隔 120ms."""
    global _LAST_TUSHARE_CALL
    with _TUSHARE_LOCK:
        elapsed = time.time() - _LAST_TUSHARE_CALL
        if elapsed < 0.12:
            time.sleep(0.12 - elapsed)
        _LAST_TUSHARE_CALL = time.time()


def _calc_start_date(trade_date: str, days: int) -> str:
    """计算回溯起始日期."""
    dt = datetime.strptime(trade_date, "%Y%m%d")
    start = dt - timedelta(days=int(days * 1.4))
    return start.strftime("%Y%m%d")


def _load_token() -> str:
    """加载 Tushare Token，优先级: 环境变量 > config/.env."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        return token
    # 搜索 config/.env
    search_dirs = [
        Path(__file__).resolve().parent.parent.parent,  # theme_engine/
        Path(__file__).resolve().parent.parent.parent.parent,  # solo/
        Path(r"D:\mystock"),  # 外部配置
    ]
    searched = []
    for base in search_dirs:
        for env_rel in ["config/.env", ".env"]:
            env_path = base / env_rel
            searched.append(str(env_path))
            if env_path.exists():
                try:
                    with open(env_path, encoding="utf-8") as f:
                        for line in f:
                            if line.strip().startswith("TUSHARE_TOKEN"):
                                token = line.split("=", 1)[1].strip().strip("\"' ")
                                if token:
                                    os.environ["TUSHARE_TOKEN"] = token
                                    return token
                except Exception:
                    continue
    logger.warning("TUSHARE_TOKEN 未配置 (已搜索: %s)", "; ".join(searched))
    return ""


def _get_pro():
    """获取 tushare pro 对象."""
    token = _load_token()
    if not token:
        return None
    import tushare as ts
    return ts.pro_api(token)


class MarketDataFetcher:
    """市场数据获取器.

    提供:
    - 指数行情
    - 全市场宽度
    - 情绪指标
    - 成交额分析
    - 资金流
    - 风险偏好
    """

    def __init__(self) -> None:
        self._cache: Dict[str, pd.DataFrame] = {}
        self._daily_basic_cache: Dict[str, pd.DataFrame] = {}
        self._pro = None

    def _get_pro(self):
        if self._pro is None:
            self._pro = _get_pro()
        return self._pro

    async def get_index_daily(
        self, index_code: str, trade_date: str, days: int = 120
    ) -> pd.DataFrame:
        """获取指数日线数据."""
        cache_key = f"index_{index_code}_{days}_{trade_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        df = None
        # 1. 尝试从ETF缓存目录读取 (部分指数有缓存)
        try:
            etf_dir = Path(os.getenv("CACHE_DIR", r"D:\mystock\cache_daily")) / "etf_fund"
            if etf_dir.exists():
                pattern = f"{index_code}_*.csv"
                files = sorted(etf_dir.glob(pattern), reverse=True)
                if files:
                    df = pd.read_csv(files[0])
                    if "trade_date" in df.columns:
                        df["trade_date"] = df["trade_date"].astype(str)
                        df = df.sort_values("trade_date", ascending=False).head(days)
        except Exception:
            pass

        # 2. 从 Tushare 获取
        if df is None or df.empty:
            pro = self._get_pro()
            if pro:
                try:
                    _rate_limit()
                    start_date = _calc_start_date(trade_date, days)
                    raw = pro.index_daily(
                        ts_code=index_code,
                        start_date=start_date,
                        end_date=trade_date,
                    )
                    if raw is not None and not raw.empty:
                        df = raw.copy()
                        if "trade_date" in df.columns:
                            df["trade_date"] = df["trade_date"].astype(str)
                            df = df.sort_values("trade_date")
                except Exception as e:
                    logger.debug("Tushare获取指数 %s 失败: %s", index_code, e)

        if df is None or df.empty:
            logger.warning("指数 %s 无数据", index_code)
            self._cache[cache_key] = pd.DataFrame()
            return self._cache[cache_key]

        self._cache[cache_key] = df
        return df

    async def get_all_indices(
        self, trade_date: str, days: int = 120
    ) -> Dict[str, pd.DataFrame]:
        """获取所有6个指数行情."""
        result = {}
        for code in INDEX_CODES:
            df = await self.get_index_daily(code, trade_date, days)
            if df is not None and not df.empty:
                result[code] = df
        return result

    async def get_market_breadth(
        self, trade_date: str
    ) -> Dict[str, Any]:
        """获取市场宽度数据.

        使用 daily_basic 接口获取全市场涨跌统计.
        """
        pro = self._get_pro()
        if not pro:
            return {"up_count": 0, "down_count": 0, "up_ratio": 0.5,
                    "new_high_20d": 0, "new_low_20d": 0, "consecutive_up_count": 0}

        try:
            _rate_limit()
            # pro.daily() 无ts_code返回全市场日线
            df = pro.daily(trade_date=trade_date)
            if df is None or df.empty:
                return self._default_breadth()

            # 涨跌统计
            pct = df.get("pct_chg", df.get("change", None))
            if pct is None:
                return self._default_breadth()
            total = len(df)
            up_count = int((pct > 0).sum())
            down_count = int((pct < 0).sum())
            up_ratio = up_count / total if total > 0 else 0.5

            # 涨幅>5%和跌幅>5%作为暴涨暴跌近似
            sorted_pct = pct.sort_values(ascending=False)
            n_top = max(1, int(total * 0.2))
            new_high_20d = int((sorted_pct.head(n_top) > 5).sum())
            new_low_20d = int((sorted_pct.tail(n_top) < -5).sum())

            return {
                "up_count": up_count,
                "down_count": down_count,
                "up_ratio": round(up_ratio, 4),
                "new_high_20d": new_high_20d,
                "new_low_20d": new_low_20d,
                "consecutive_up_count": 0,
            }

        except Exception as e:
            logger.debug("获取市场宽度失败: %s", e)
            return self._default_breadth()

    def _default_breadth(self) -> Dict[str, Any]:
        return {"up_count": 0, "down_count": 0, "up_ratio": 0.5,
                "new_high_20d": 0, "new_low_20d": 0, "consecutive_up_count": 0}

    async def get_market_sentiment(
        self, trade_date: str
    ) -> Dict[str, Any]:
        """获取市场情绪.

        使用 limit_list 接口获取涨停/跌停数据.
        """
        pro = self._get_pro()
        if not pro:
            return {"limit_up_count": 0, "limit_down_count": 0, "break_rate": 0.3,
                    "consecutive_limit_height": 0, "yest_limit_up_perf": 0.0}

        try:
            # limit_list 接口可能不可用, 从 daily 数据推断涨停数量
            _rate_limit()
            df = pro.daily(trade_date=trade_date)
            limit_up = 0
            limit_down = 0
            if df is not None and not df.empty and "pct_chg" in df.columns:
                # 涨停近似: pct_chg >= 9.8% (实际涨停线9.96%±0.5%)
                limit_up = int((df["pct_chg"] >= 9.8).sum())
                limit_down = int((df["pct_chg"] <= -9.8).sum())

            # 炸板率: 无法精确获取，用固定值
            break_rate = 0.3
            consecutive_height = 0

            # 昨日涨停表现: 取 pct_chg > 9.5% 的股票均值
            yest_perf = 0.0
            if df is not None and not df.empty and "pct_chg" in df.columns:
                limit_up_stocks = df[df["pct_chg"] >= 9.5]
                if len(limit_up_stocks) > 0:
                    yest_perf = float(limit_up_stocks["pct_chg"].mean())

            return {
                "limit_up_count": limit_up,
                "limit_down_count": limit_down,
                "break_rate": round(break_rate, 4),
                "consecutive_limit_height": consecutive_height,
                "yest_limit_up_perf": round(yest_perf, 2),
            }

        except Exception as e:
            logger.debug("获取市场情绪失败: %s", e)
            return {"limit_up_count": 0, "limit_down_count": 0, "break_rate": 0.3,
                    "consecutive_limit_height": 0, "yest_limit_up_perf": 0.0}

    async def get_market_amount(
        self, trade_date: str
    ) -> Dict[str, Any]:
        """获取市场成交额.

        使用 daily_basic 汇总全市场成交额.
        """
        pro = self._get_pro()
        if not pro:
            return {"total_amount": 0.0, "amount_ma20": 0.0, "amount_change_pct": 0.0}

        try:
            # 当日成交额
            _rate_limit()
            today_df = pro.daily_basic(trade_date=trade_date)
            total_amount = 0.0
            if today_df is not None and not today_df.empty and "amount" in today_df.columns:
                total_amount = float(today_df["amount"].sum())

            # 近20日成交额均值
            dt = datetime.strptime(trade_date, "%Y%m%d")
            start_20 = (dt - timedelta(days=40)).strftime("%Y%m%d")
            _rate_limit()
            hist = pro.daily_basic(start_date=start_20, end_date=trade_date)
            amount_ma20 = total_amount
            if hist is not None and not hist.empty and "amount" in hist.columns:
                # 按 trade_date 分组求合计
                by_date = hist.groupby("trade_date")["amount"].sum()
                if len(by_date) >= 5:
                    amount_ma20 = float(by_date.tail(20).mean())

            amount_change_pct = (total_amount / amount_ma20 - 1) * 100 if amount_ma20 > 0 else 0

            return {
                "total_amount": round(total_amount / 1e8, 2),  # 转换为亿元
                "amount_ma20": round(amount_ma20 / 1e8, 2),
                "amount_change_pct": round(amount_change_pct, 2),
            }

        except Exception as e:
            logger.debug("获取市场成交额失败: %s", e)
            return {"total_amount": 0.0, "amount_ma20": 0.0, "amount_change_pct": 0.0}

    async def get_money_flow(
        self, trade_date: str
    ) -> Dict[str, float]:
        """获取资金流数据.

        使用 moneyflow 接口获取主力资金流向.
        """
        pro = self._get_pro()
        if not pro:
            return {"etf_net_inflow": 0.0, "main_net_inflow": 0.0}

        try:
            _rate_limit()
            df = pro.moneyflow(trade_date=trade_date)
            if df is None or df.empty:
                return {"etf_net_inflow": 0.0, "main_net_inflow": 0.0}

            # 主力净流入 (Tushare moneyflow: net_mf_amount)
            main_inflow = float(df["net_mf_amount"].sum()) if "net_mf_amount" in df.columns else 0.0

            # ETF净流入简化: 使用 buy_elg_amount - sell_elg_amount (特大单净额)
            etf_inflow = 0.0
            if "buy_elg_amount" in df.columns and "sell_elg_amount" in df.columns:
                etf_inflow = float((df["buy_elg_amount"] - df["sell_elg_amount"]).sum())

            return {
                "etf_net_inflow": round(etf_inflow / 1e8, 2),
                "main_net_inflow": round(main_inflow / 1e8, 2),
            }

        except Exception as e:
            logger.debug("获取资金流失败: %s", e)
            return {"etf_net_inflow": 0.0, "main_net_inflow": 0.0}

    async def get_etf_performance(
        self, etf_codes: List[str], trade_date: str, days: int = 60
    ) -> Dict[str, float]:
        """获取ETF最近20日收益表现."""
        if not etf_codes:
            return {}

        pro = self._get_pro()
        if not pro:
            return {code: 0.0 for code in etf_codes}

        result = {}
        for code in etf_codes:
            try:
                _rate_limit()
                start_date = _calc_start_date(trade_date, 30)  # 30天取20个交易日
                df = pro.fund_daily(
                    ts_code=code,
                    start_date=start_date,
                    end_date=trade_date,
                    fields="trade_date,close",
                )
                if df is not None and len(df) >= 20:
                    closes = df.sort_values("trade_date")["close"].values
                    perf_20d = (closes[-1] / closes[-20] - 1) * 100
                    result[code] = round(perf_20d, 2)
                else:
                    result[code] = 0.0
            except Exception as e:
                logger.debug("ETF %s 表现获取失败: %s", code, e)
                result[code] = 0.0

        return result

    async def clear_cache(self) -> None:
        self._cache.clear()
        self._daily_basic_cache.clear()
