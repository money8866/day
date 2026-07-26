"""ETF 数据服务.

提供 ETF 历史行情、技术指标计算等功能。
支持从现有系统（ETF历史CSV、Tushare）获取数据。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd

from theme_engine.config.settings import DEFAULT_MA_PERIODS, TUSHARE_TOKEN

logger = logging.getLogger(__name__)


class ETFService:
    """ETF 数据服务.

    提供 ETF 历史行情、技术指标计算等功能。
    支持从现有系统（ETF历史CSV、Tushare）获取数据。
    """

    def __init__(self) -> None:
        self._cache: Dict[str, pd.DataFrame] = {}
        self._flow_cache: Dict[str, dict] = {}

    async def get_etf_daily(
        self, etf_code: str, trade_date: str, days: int = 120
    ) -> pd.DataFrame:
        """获取ETF日线数据.

        Args:
            etf_code: ETF代码 (如 510300.SH)
            trade_date: 交易日 YYYYMMDD
            days: 回溯天数

        Returns:
            DataFrame 包含 open, high, low, close, volume, amount 列
        """
        cache_key = f"{etf_code}_{days}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            # 优先从 CSV 加载
            df = await self._load_from_csv(etf_code, days)
            if df is not None and not df.empty:
                self._cache[cache_key] = df
                return df

            # 从 Tushare 加载
            df = await self._load_from_tushare(etf_code, trade_date, days)
            if df is not None and not df.empty:
                self._cache[cache_key] = df
                return df

            logger.warning("ETF %s 无数据可用", etf_code)
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取ETF %s 日线失败: %s", etf_code, e)
            return pd.DataFrame()

    async def _load_from_csv(self, etf_code: str, days: int) -> Optional[pd.DataFrame]:
        """从 CSV 文件加载 ETF 数据.

        优先级:
        1. D:\\mystock\\cache_daily\\etf_fund\\{ts_code}_{trade_date}.csv (etf_mainline_strategy_tushare 缓存)
        2. D:\\mystock\\solo\\etf_winner_prediction\\data\\{etf_code}.csv (旧目录)
        """
        try:
            import os
            from pathlib import Path

            from theme_engine.config.settings import PROJECT_ROOT

            # 1. 优先读取 etf_mainline_strategy_tushare 的缓存
            etf_cache_dir = PROJECT_ROOT.parent.parent / "cache_daily" / "etf_fund"
            if etf_cache_dir.exists():
                # 查找该 ETF 的所有缓存文件（按日期排序，取最新的）
                prefix = etf_code.replace(".", r"\.")
                pattern = f"{etf_code}_*.csv"
                cache_files = sorted(etf_cache_dir.glob(pattern), reverse=True)
                if cache_files:
                    # 读取最新的缓存文件（包含多日数据）
                    df = pd.read_csv(cache_files[0])
                    if "trade_date" in df.columns:
                        df["trade_date"] = df["trade_date"].astype(str)
                        df = df.sort_values("trade_date", ascending=False).head(days)
                        return df

            # 2. 回退到旧目录
            csv_dir = PROJECT_ROOT.parent / "etf_winner_prediction" / "data"
            csv_path = csv_dir / f"{etf_code.replace('.', '_')}.csv"
            if not csv_path.exists():
                csv_path = csv_dir / f"{etf_code}.csv"
            if not csv_path.exists():
                return None

            df = pd.read_csv(csv_path)
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].astype(str)
                df = df.sort_values("trade_date", ascending=False).head(days)
            return df
        except Exception as e:
            logger.debug("CSV加载ETF %s 失败: %s", etf_code, e)
            return None

    async def _load_from_tushare(
        self, etf_code: str, trade_date: str, days: int
    ) -> Optional[pd.DataFrame]:
        """从 Tushare API 加载 ETF 数据."""
        if not TUSHARE_TOKEN:
            logger.debug("TUSHARE_TOKEN 未配置，跳过 Tushare 加载")
            return None

        try:
            import tushare as ts

            pro = ts.pro_api(TUSHARE_TOKEN)
            start_date = self._calc_start_date(trade_date, days)
            df = pro.fund_daily(
                ts_code=etf_code,
                start_date=start_date,
                end_date=trade_date,
                fields="trade_date,open,high,low,close,vol,amount",
            )
            if df is None or df.empty:
                return None
            df = df.rename(
                columns={
                    "vol": "volume",
                    "trade_date": "trade_date",
                }
            )
            df["trade_date"] = df["trade_date"].astype(str)
            df = df.sort_values("trade_date")
            return df
        except ImportError:
            logger.debug("tushare 未安装")
            return None
        except Exception as e:
            logger.error("Tushare加载ETF %s 失败: %s", etf_code, e)
            return None

    async def get_etf_flow(self, etf_code: str, trade_date: str) -> dict:
        """获取ETF资金流向.

        Returns:
            dict: net_flow, inflow, outflow, amount
        """
        cache_key = f"{etf_code}_{trade_date}"
        if cache_key in self._flow_cache:
            return self._flow_cache[cache_key]

        result: dict = {
            "net_flow": 0.0,
            "inflow": 0.0,
            "outflow": 0.0,
            "amount": 0.0,
        }

        try:
            if TUSHARE_TOKEN:
                import tushare as ts

                pro = ts.pro_api(TUSHARE_TOKEN)
                df = pro.moneyflow(
                    ts_code=etf_code,
                    trade_date=trade_date,
                )
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    result = {
                        "net_flow": float(row.get("net_amount", 0)),
                        "inflow": float(row.get("buy_amount", 0)),
                        "outflow": float(row.get("sell_amount", 0)),
                        "amount": float(row.get("amount", 0)),
                    }
            self._flow_cache[cache_key] = result
        except Exception as e:
            logger.error("获取ETF %s 资金流向失败: %s", etf_code, e)

        return result

    async def calculate_ma(
        self, df: pd.DataFrame, periods: Optional[list] = None
    ) -> pd.DataFrame:
        """计算均线.

        Args:
            df: 包含 close 列的 DataFrame
            periods: 均线周期列表，默认 [5, 10, 20, 60, 120]

        Returns:
            添加了 MA5, MA10, MA20, MA60, MA120 列的 DataFrame
        """
        if df is None or df.empty:
            return df

        if periods is None:
            periods = DEFAULT_MA_PERIODS

        df = df.copy()
        for p in periods:
            if len(df) >= p:
                df[f"MA{p}"] = df["close"].rolling(window=p).mean()
        return df

    async def calculate_macd(
        self,
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> dict:
        """计算MACD.

        Returns:
            dict: {"macd": float, "signal": float, "histogram": float, "macd_direction": str}
        """
        result: dict = {
            "macd": 0.0,
            "signal": 0.0,
            "histogram": 0.0,
            "macd_direction": "flat",
        }

        if df is None or df.empty or len(df) < slow + signal:
            return result

        try:
            close = df["close"].values
            ema_fast = self._ema(close, fast)
            ema_slow = self._ema(close, slow)
            dif = ema_fast - ema_slow
            dea = self._ema(dif, signal)
            bar = 2 * (dif - dea)

            result = {
                "macd": float(dif[-1]),
                "signal": float(dea[-1]),
                "histogram": float(bar[-1]),
                "macd_direction": "up" if bar[-1] > bar[-2] else "down" if len(bar) > 1 else "flat",
            }
        except Exception as e:
            logger.error("MACD计算失败: %s", e)

        return result

    async def calculate_atr(
        self, df: pd.DataFrame, period: int = 14
    ) -> float:
        """计算ATR (Average True Range).

        Returns:
            ATR 值，归一化到 0~100
        """
        if df is None or df.empty or len(df) < period + 1:
            return 0.0

        try:
            high = df["high"].values
            low = df["low"].values
            close = df["close"].values

            tr_list = []
            for i in range(1, len(close)):
                tr = max(
                    high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]),
                )
                tr_list.append(tr)

            if len(tr_list) < period:
                return 0.0

            atr = sum(tr_list[-period:]) / period
            avg_price = float(sum(close[-period:]) / period)
            if avg_price > 0:
                return min(100.0, (atr / avg_price) * 100)
            return 0.0
        except Exception as e:
            logger.error("ATR计算失败: %s", e)
            return 0.0

    async def calculate_rsi(
        self, df: pd.DataFrame, period: int = 14
    ) -> float:
        """计算RSI.

        Returns:
            RSI 值 0~100
        """
        if df is None or df.empty or len(df) < period + 1:
            return 50.0

        try:
            close = df["close"].values
            gains = 0.0
            losses = 0.0

            for i in range(-period, 0):
                diff = close[i] - close[i - 1]
                if diff > 0:
                    gains += diff
                else:
                    losses -= diff

            if losses == 0:
                return 100.0

            avg_gain = gains / period
            avg_loss = losses / period
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
            rsi = 100 - (100 / (1 + rs))
            return round(rsi, 2)
        except Exception as e:
            logger.error("RSI计算失败: %s", e)
            return 50.0

    @staticmethod
    def _ema(values, period: int):
        """计算指数移动平均."""
        if len(values) == 0:
            return values
        multiplier = 2 / (period + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append((v - result[-1]) * multiplier + result[-1])
        return result

    @staticmethod
    def _calc_start_date(trade_date: str, days: int) -> str:
        """计算起始日期 (YYYYMMDD)."""
        try:
            from datetime import datetime, timedelta

            dt = datetime.strptime(trade_date, "%Y%m%d")
            start = dt - timedelta(days=int(days * 1.5))
            return start.strftime("%Y%m%d")
        except ValueError:
            return trade_date

    async def clear_cache(self) -> None:
        """清空缓存."""
        self._cache.clear()
        self._flow_cache.clear()
