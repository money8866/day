"""股票数据服务.

提供个股日线、技术指标、资金流向等数据。
支持批量获取。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from theme_engine.config.settings import DEFAULT_MA_PERIODS, TUSHARE_TOKEN

logger = logging.getLogger(__name__)


class StockService:
    """股票数据服务.

    提供个股日线、技术指标、资金流向等数据。
    支持批量获取。
    """

    def __init__(self) -> None:
        self._daily_cache: Dict[str, pd.DataFrame] = {}
        self._ma_status_cache: Dict[str, dict] = {}

    async def get_stocks_daily(
        self, codes: List[str], trade_date: str, days: int = 120
    ) -> Dict[str, pd.DataFrame]:
        """批量获取个股日线.

        Args:
            codes: 股票代码列表
            trade_date: 交易日 YYYYMMDD
            days: 回溯天数

        Returns:
            {code: DataFrame} 字典
        """
        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                df = await self._get_single_stock_daily(code, trade_date, days)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as e:
                logger.error("获取个股 %s 日线失败: %s", code, e)
        return result

    async def _get_single_stock_daily(
        self, code: str, trade_date: str, days: int
    ) -> Optional[pd.DataFrame]:
        """获取单只个股日线."""
        cache_key = f"{code}_{days}"
        if cache_key in self._daily_cache:
            return self._daily_cache[cache_key]

        try:
            # 1. 本地缓存/CSV加载（多日数据）
            df = await self._load_from_cache(code, days)
            if df is not None and not df.empty:
                self._daily_cache[cache_key] = df
                return df

            # 2. 批量日线文件 daily_{trade_date}.csv（全部股票单日快照）
            df = await self._load_from_bulk_daily(code, trade_date)
            if df is not None and not df.empty:
                self._daily_cache[cache_key] = df
                return df

            # 3. 从 Tushare 加载
            df = await self._load_from_tushare(code, trade_date, days)
            if df is not None and not df.empty:
                self._daily_cache[cache_key] = df
                return df

            logger.debug("个股 %s 无数据可用", code)
            return None
        except Exception as e:
            logger.error("获取个股 %s 日线异常: %s", code, e)
            return None

    async def _load_from_cache(self, code: str, days: int) -> Optional[pd.DataFrame]:
        """从本地缓存加载.

        优先级:
        1. D:\\mystock\\cache_daily\\etf_cons\\stock_{code}_{trade_date}.csv (etf alpha ranking)
        2. D:\\mystock\\cache_daily\\{code_safe}_daily.csv (个股日线缓存)
        3. D:\\mystock\\solo\\theme_engine\\data\\stock_cache\\{code}.csv
        """
        try:
            if "stock_cache" in sys.modules or True:
                from pathlib import Path

                from theme_engine.config.settings import PROJECT_ROOT

                safe_code = code.replace(".", "_")

                # 1. etf_cons 缓存
                etf_cons_dir = PROJECT_ROOT.parent.parent / "cache_daily" / "etf_cons"
                if etf_cons_dir.exists():
                    for f in sorted(etf_cons_dir.glob(f"stock_{safe_code}_*.csv"), reverse=True):
                        df = pd.read_csv(f)
                        if "trade_date" in df.columns:
                            df["trade_date"] = df["trade_date"].astype(str)
                            df = df.sort_values("trade_date", ascending=False).head(days)
                            return df

                # 2. cache_daily 个股日线缓存 {code_safe}_daily.csv
                cache_daily_dir = PROJECT_ROOT.parent.parent / "cache_daily"
                daily_file = cache_daily_dir / f"{safe_code}_daily.csv"
                if daily_file.exists():
                    df = pd.read_csv(daily_file)
                    if "trade_date" in df.columns:
                        df["trade_date"] = df["trade_date"].astype(str)
                        df = df.sort_values("trade_date", ascending=False).head(days)
                        return df

                # 3. stock_cache 目录
                cache_dir = PROJECT_ROOT / "data" / "stock_cache"
                if not cache_dir.exists():
                    return None

                csv_path = cache_dir / f"{code}.csv"
                if csv_path.exists():
                    df = pd.read_csv(csv_path)
                    if "trade_date" in df.columns:
                        df["trade_date"] = df["trade_date"].astype(str)
                        df = df.sort_values("trade_date", ascending=False).head(days)
                        return df
        except Exception as e:
            logger.debug("本地缓存加载 %s 失败: %s", code, e)
        return None

    async def _load_from_bulk_daily(
        self, code: str, trade_date: str,
    ) -> Optional[pd.DataFrame]:
        """从批量日线文件 daily_{trade_date}.csv 加载.

        文件格式: ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        包含当日全部 A 股的单日快照。
        """
        try:
            from pathlib import Path

            from theme_engine.config.settings import PROJECT_ROOT

            bulk_path = (
                PROJECT_ROOT.parent.parent / "cache_daily" / f"daily_{trade_date}.csv"
            )
            if not bulk_path.exists():
                return None

            df = pd.read_csv(bulk_path)
            row = df[df["ts_code"] == code]
            if row.empty:
                return None

            # 返回与 get_stocks_daily 兼容的格式（多行DataFrame）
            result = pd.DataFrame({
                "trade_date": [str(trade_date)],
                "open": [row.iloc[0].get("open", 0)],
                "high": [row.iloc[0].get("high", 0)],
                "low": [row.iloc[0].get("low", 0)],
                "close": [row.iloc[0].get("close", 0)],
                "volume": [row.iloc[0].get("vol", 0)],
                "amount": [row.iloc[0].get("amount", 0)],
                "pct_chg": [row.iloc[0].get("pct_chg", 0)],
            })
            return result
        except Exception as e:
            logger.debug("批量日线加载 %s 失败: %s", code, e)
            return None

    async def _load_from_tushare(
        self, code: str, trade_date: str, days: int
    ) -> Optional[pd.DataFrame]:
        """从 Tushare API 加载."""
        if not TUSHARE_TOKEN:
            return None

        try:
            import tushare as ts

            pro = ts.pro_api(TUSHARE_TOKEN)
            start_date = self._calc_start_date(trade_date, days)
            df = pro.daily(
                ts_code=code,
                start_date=start_date,
                end_date=trade_date,
                fields="trade_date,open,high,low,close,vol,amount",
            )
            if df is None or df.empty:
                return None
            df = df.rename(columns={"vol": "volume"})
            df["trade_date"] = df["trade_date"].astype(str)
            df = df.sort_values("trade_date")
            return df
        except ImportError:
            logger.debug("tushare 未安装")
            return None
        except Exception as e:
            logger.error("Tushare加载 %s 失败: %s", code, e)
            return None

    async def get_stock_ma_status(self, code: str, trade_date: str) -> dict:
        """检查个股均线状态: 是否站上MA5/10/20/60/120.

        Returns:
            dict: {
                "above_ma5": bool, "above_ma10": bool, ...
                "ma_count": int, "ma_score": float (0~100)
            }
        """
        cache_key = f"{code}_{trade_date}"
        if cache_key in self._ma_status_cache:
            return self._ma_status_cache[cache_key]

        result: dict = {
            "above_ma5": False,
            "above_ma10": False,
            "above_ma20": False,
            "above_ma60": False,
            "above_ma120": False,
            "ma_count": 0,
            "ma_score": 0.0,
        }

        try:
            df = await self._get_single_stock_daily(code, trade_date, 180)
            if df is None or df.empty:
                return result

            close = df["close"].values
            if len(close) < 5:
                return result

            latest_close = close[-1]

            for period in DEFAULT_MA_PERIODS:
                if len(close) >= period:
                    ma = sum(close[-period:]) / period
                    key = f"above_ma{period}"
                    result[key] = bool(latest_close >= ma)

            result["ma_count"] = sum(
                1 for p in DEFAULT_MA_PERIODS if result.get(f"above_ma{p}", False)
            )
            result["ma_score"] = (result["ma_count"] / len(DEFAULT_MA_PERIODS)) * 100.0

            self._ma_status_cache[cache_key] = result
        except Exception as e:
            logger.error("检查均线状态 %s 失败: %s", code, e)

        return result

    async def get_limit_up_data(self, trade_date: str) -> pd.DataFrame:
        """获取涨停数据.

        Returns:
            DataFrame 包含涨停股票的 code, name, limit_up_time, close 等
        """
        try:
            # 优先从本地文件获取
            df = await self._load_limit_up_from_file(trade_date)
            if df is not None and not df.empty:
                return df

            # 从 Tushare 获取
            if TUSHARE_TOKEN:
                import tushare as ts

                pro = ts.pro_api(TUSHARE_TOKEN)
                df = pro.limit_list(
                    trade_date=trade_date,
                    limit_type="U",
                    fields="ts_code,name,limit_amount,limit_time,open,high,low,close",
                )
                if df is not None and not df.empty:
                    df = df.rename(columns={"ts_code": "code"})
                    df["trade_date"] = trade_date
                    return df
        except ImportError:
            logger.debug("tushare 未安装")
        except Exception as e:
            logger.error("获取涨停数据失败: %s", e)

        return pd.DataFrame()

    async def _load_limit_up_from_file(self, trade_date: str) -> Optional[pd.DataFrame]:
        """从本地文件加载涨停数据."""
        try:
            from pathlib import Path

            from theme_engine.config.settings import PROJECT_ROOT

            file_path = (
                PROJECT_ROOT.parent / "report_daily" / f"limit_up_{trade_date}.csv"
            )
            if file_path.exists():
                df = pd.read_csv(file_path)
                if "trade_date" not in df.columns:
                    df["trade_date"] = trade_date
                return df
        except Exception as e:
            logger.debug("本地涨停文件加载失败: %s", e)
        return None

    async def enrich_stocks(
        self,
        stocks: List[Dict[str, Any]],
        trade_date: str,
    ) -> List[Dict[str, Any]]:
        """富化成分股数据: 添加 pct_chg, amount, 均线状态等.

        从本地缓存批量读取日线数据，计算行情指标。
        缺少缓存数据的股票使用默认值（不影响整体评分）。
        """
        if not stocks:
            return stocks

        # 批量获取日线数据
        codes = [s.get("code", "") for s in stocks if s.get("code")]
        daily_map = await self.get_stocks_daily(codes, trade_date, days=60)

        # 获取涨停数据
        limit_up_df = await self.get_limit_up_data(trade_date)
        limit_up_codes: set = set()
        if limit_up_df is not None and not limit_up_df.empty:
            col = "code" if "code" in limit_up_df.columns else "ts_code"
            limit_up_codes = set(limit_up_df[col].astype(str).values)

        enriched: List[Dict[str, Any]] = []
        for s in stocks:
            code = s.get("code", "")
            es = dict(s)  # 复制原始数据

            df = daily_map.get(code)
            if df is not None and not df.empty and "close" in df.columns:
                closes = df["close"].values
                amounts = df.get("amount", df.get("vol", [0])).values
                highs = df["high"].values if "high" in df.columns else closes
                lows = df["low"].values if "low" in df.columns else closes

                n = len(closes)

                # pct_chg: 优先使用文件中的 pct_chg 字段，否则从收盘价计算
                if "pct_chg" in df.columns:
                    pct_chg = float(df["pct_chg"].iloc[0])
                else:
                    pct_chg = ((closes[-1] / closes[-2]) - 1) * 100 if n >= 2 else 0.0

                # amount: 成交额 (优先 amount 列, 回退 vol)
                if "amount" in df.columns:
                    amount = float(df["amount"].iloc[0])
                else:
                    amounts = df.get("amount", df.get("vol", [0])).values
                    amount = float(amounts[-1]) if len(amounts) > 0 else 0.0

                # limit_up: 是否涨停 (简单判断: 沪/深主板涨10%, 双创涨20%)
                if code.endswith(".SH") or code.endswith(".SZ"):
                    # 从名称判断: 688/300开头为双创
                    num = code.split(".")[0]
                    if num.startswith("688") or num.startswith("300"):
                        limit_up_th = 19.5  # 20% 涨停 (留容差)
                    else:
                        limit_up_th = 9.8  # 10% 涨停
                else:
                    limit_up_th = 9.8
                is_limit_up = pct_chg >= limit_up_th or code in limit_up_codes

                # new_high_20d: 20日新高
                if n >= 20:
                    new_high_20d = closes[-1] >= max(closes[-20:])
                else:
                    new_high_20d = closes[-1] >= max(closes)

                # MA 均线状态
                ma_periods = [5, 10, 20, 60, 120]
                for p in ma_periods:
                    if n >= p:
                        ma = sum(closes[-p:]) / p
                        es[f"above_ma{p}"] = bool(closes[-1] >= ma)
                    else:
                        es[f"above_ma{p}"] = False

                # alpha: 简化计算 (当日涨跌幅 - 沪深300涨跌幅)
                es["alpha"] = pct_chg  # 简化
                es["relative_alpha"] = pct_chg  # 简化

                es["pct_chg"] = round(pct_chg, 2)
                es["amount"] = round(amount, 2)
                es["limit_up"] = is_limit_up
                es["new_high_20d"] = new_high_20d

                # leader 因子需要的字段
                es["total_mv"] = s.get("total_mv", 0)
                es["volume_ratio"] = 1.0
                es["money_flow"] = 0.0
                es["institution_holding"] = 0.0
                es["macd"] = 0.0
                es["ma_trend"] = 0.0
                es["relative_strength"] = 0.0
            else:
                # 无数据, 使用默认值
                es["pct_chg"] = 0.0
                es["amount"] = 0.0
                es["limit_up"] = False
                es["new_high_20d"] = False
                for p in [5, 10, 20, 60, 120]:
                    es[f"above_ma{p}"] = False
                es["alpha"] = 0.0
                es["relative_alpha"] = 0.0
                es["total_mv"] = 0
                es["volume_ratio"] = 1.0
                es["money_flow"] = 0.0
                es["institution_holding"] = 0.0
                es["macd"] = 0.0
                es["ma_trend"] = 0.0
                es["relative_strength"] = 0.0

            enriched.append(es)

        return enriched

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
        self._daily_cache.clear()
        self._ma_status_cache.clear()
