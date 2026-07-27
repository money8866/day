"""
ELD V2 数据源层

封装 tushare 数据接口，提供缓存优先 + API 回退的数据获取能力。
所有方法支持限流与自动重试。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import tushare as ts

from .cache import EldCache
from .config import CacheConfig, TushareConfig
from .models import (
    CyqData,
    DailyBasicData,
    DailyPriceData,
    FinancialData,
    ForecastData,
    MarketScoreResult,
    MoneyFlowData,
    StockBasic,
)
from .utils import rate_limiter, safe_float, get_last_trade_date


# ──────────────────────────────────────────────
# 日志
# ──────────────────────────────────────────────

logger = logging.getLogger("eld.datasource")


# ──────────────────────────────────────────────
# EldDataSource
# ──────────────────────────────────────────────


class EldDataSource:
    """ELD 数据源层。

    职责：
      - 封装所有 tushare API 调用
      - 缓存优先（先查 SQLite，再调 API）
      - API 限流与自动重试
      - 数据模型转换
    """

    def __init__(
        self,
        token: str,
        cache: EldCache,
        tushare_config: Optional[TushareConfig] = None,
    ) -> None:
        """初始化数据源。

        Args:
            token: Tushare API token。
            cache: 缓存实例。
            tushare_config: Tushare 配置（可选）。
        """
        self._cache = cache
        self._config = tushare_config or TushareConfig(token=token)
        self._retry_count = self._config.retry_count
        self._retry_delay = self._config.retry_delay

        # 初始化 tushare pro（直接传 token，避免 set_token 写文件）
        self._pro = ts.pro_api(token)

    # ─── API 调用包装 ────────────────────────

    def _call_api(
        self, method: str, **kwargs: Any
    ) -> Any:
        """带限流和重试的 tushare API 调用。

        Args:
            method: API 方法名（如 'forecast', 'daily'）。
            **kwargs: 传递给 API 的参数。

        Returns:
            API 返回的 DataFrame，失败时返回 None。
        """
        api_func = getattr(self._pro, method, None)
        if api_func is None:
            logger.error(f"未知的 tushare API 方法: {method}")
            return None

        last_error: Optional[Exception] = None
        for attempt in range(1, self._retry_count + 1):
            try:
                with rate_limiter(self._config.rate_limit_ms):
                    df = api_func(**kwargs)
                if df is not None and len(df) > 0:
                    return df
                # 空结果、非重试场景直接返回
                return df
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "API 调用失败 [%s] (尝试 %d/%d): %s",
                    method, attempt, self._retry_count, exc,
                )
                if attempt < self._retry_count:
                    time.sleep(self._retry_delay * attempt)
        logger.error(
            "API 调用最终失败 [%s] 参数=%s: %s",
            method, kwargs, last_error,
        )
        return None

    def _call_with_retry(
        self, method: str, **kwargs: Any
    ) -> Any:
        """_call_api 的别名，提供更符合直觉的命名。"""
        return self._call_api(method, **kwargs)

    # ─── 业绩预告 ────────────────────────────

    # 全量中报预告 parquet 缓存路径（由外部 multi_factor_picker 维护）
    _FORECAST_PARQUET = Path(
        r"d:\mystock\solo\multi_factor_picker\cache\forecast_vip_20260630.parquet"
    )

    def get_forecast_all(self) -> list[ForecastData]:
        """获取全市场业绩预告数据。

        优先级：
          1. 读取本地 parquet 全量缓存（forecast_vip_20260630.parquet）
          2. SQLite 缓存
          3. 按 ann_date 逐日拉取 API（180天）
        """
        # ── 1. parquet 全量缓存 ──
        if self._FORECAST_PARQUET.exists():
            try:
                df = pd.read_parquet(self._FORECAST_PARQUET)
                results: list[ForecastData] = []
                seen: set[tuple[str, str]] = set()
                for _, row in df.iterrows():
                    ts_code = str(row.get("ts_code", ""))
                    ed = str(row.get("end_date", ""))
                    key = (ts_code, ed)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(ForecastData(
                        ts_code=ts_code,
                        end_date=ed,
                        type=str(row.get("type", "")),
                        p_change_min=safe_float(row.get("p_change_min")),
                        p_change_max=safe_float(row.get("p_change_max")),
                        announce_date=str(row.get("ann_date", "")),
                        fiscal_quarter=ed,
                    ))
                logger.info(
                    "从 parquet 缓存读取业绩预告: %d 条 (%s)",
                    len(results), self._FORECAST_PARQUET.name,
                )
                return results
            except Exception as exc:
                logger.warning("读取 parquet 缓存失败，回退到 API: %s", exc)

        # ── 2. SQLite 缓存 ──
        cached = self._cache.get_forecast_cache()
        if cached is not None:
            logger.info("使用 SQLite 缓存的业绩预告数据 (%d 条)", len(cached))
            return [ForecastData(**item) for item in cached]

        # ── 3. API 逐日扫描 ──
        logger.info("从 API 获取业绩预告数据（逐日扫描180天）...")
        today = datetime.now()
        results = []
        seen = set()

        fields = (
            "ts_code,ann_date,end_date,type,"
            "p_change_min,p_change_max,net_profit_min,"
            "net_profit_max,last_parent_net,summary"
        )

        # 逐日查询，不限定 end_date（在 Python 端过滤）
        for i in range(120):
            d = (today - timedelta(days=i)).strftime("%Y%m%d")
            df = self._call_api("forecast", ann_date=d, fields=fields)
            time.sleep(0.3)
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                ts_code = str(row.get("ts_code", ""))
                ed = str(row.get("end_date", ""))
                key = (ts_code, ed)
                if key in seen:
                    continue
                seen.add(key)
                results.append(ForecastData(
                    ts_code=ts_code,
                    end_date=ed,
                    type=str(row.get("type", "")),
                    p_change_min=safe_float(row.get("p_change_min")),
                    p_change_max=safe_float(row.get("p_change_max")),
                    announce_date=str(row.get("ann_date", "")),
                    fiscal_quarter=ed,
                ))

        logger.info("业绩预告去重后合计: %d 条", len(results))

        # 写入缓存
        if results:
            dict_data = [
                {
                    "ts_code": r.ts_code,
                    "end_date": r.end_date,
                    "type": r.type,
                    "p_change_min": r.p_change_min,
                    "p_change_max": r.p_change_max,
                    "announce_date": r.announce_date,
                    "fiscal_quarter": r.fiscal_quarter,
                }
                for r in results
            ]
            self._cache.set_forecast_cache(dict_data)
            logger.info("已缓存 %d 条预告数据", len(results))
        else:
            logger.warning("API 未返回任何业绩预告数据")

        return results

    # ─── 财务数据 ────────────────────────────

    def get_financial(self, ts_code: str) -> Optional[FinancialData]:
        """获取指定股票的最新财务数据。

        从 fina_indicator（财务指标）和 income（利润表）合并获取。
        """
        # 1. 缓存
        cached = self._cache.get_financial_cache(ts_code)
        if cached is not None and len(cached) > 0:
            return FinancialData(**cached[0])

        # 2. API
        end_date = self._get_latest_financial_end_date()
        logger.debug("获取财务数据: %s (end_date=%s)", ts_code, end_date)

        # 2a. 财务指标
        df_indicator = self._call_api(
            "fina_indicator",
            ts_code=ts_code,
            end_date=end_date,
            fields=(
                "ts_code,end_date,roe,roic,gross_margin,"
                "ocf_to_orp,debt_to_assets,"
                "profit_dedt,eps"
            ),
        )
        # 2b. 利润表（营收、净利润等）
        df_income = self._call_api(
            "income",
            ts_code=ts_code,
            end_date=end_date,
            fields=(
                "ts_code,end_date,revenue,"
                "n_income,"
                "operate_profit"
            ),
        )
        # 2c. 营收同比数据（fina_indicator 也有部分）
        df_fina = self._call_api(
            "fina_indicator",
            ts_code=ts_code,
            end_date=end_date,
            fields=(
                "ts_code,end_date,"
                "or_yoy,"
                "q_deducted_profit_yoy,"
                "q_profit_yoy"
            ),
        )

        if df_indicator is None or len(df_indicator) == 0:
            logger.warning("无财务数据: %s", ts_code)
            return None

        row = df_indicator.iloc[0]
        income_row = df_income.iloc[0] if df_income is not None and len(df_income) > 0 else None
        fina_row = df_fina.iloc[0] if df_fina is not None and len(df_fina) > 0 else None

        revenue = safe_float(income_row.get("revenue")) if income_row is not None else 0.0
        net_profit = safe_float(income_row.get("n_income")) if income_row is not None else 0.0

        revenue_yoy = safe_float(fina_row.get("or_yoy")) if fina_row is not None else 0.0
        deducted_yoy = safe_float(fina_row.get("q_deducted_profit_yoy")) if fina_row is not None else 0.0
        net_profit_yoy = safe_float(fina_row.get("q_profit_yoy")) if fina_row is not None else 0.0

        deducted_profit = safe_float(row.get("profit_dedt"))
        gross_margin = safe_float(row.get("gross_margin"))
        roe = safe_float(row.get("roe"))
        roic = safe_float(row.get("roic"))
        ocf_ratio = safe_float(row.get("ocf_to_orp"))  # 经营现金流/营业收入
        debt_ratio = safe_float(row.get("debt_to_assets"))

        # 主营业务收入占比（近似：营业利润/利润总额，此处简化为营收占比）
        main_biz_ratio = 100.0
        if revenue > 0:
            operate_profit = safe_float(income_row.get("operate_profit")) if income_row is not None else 0.0
            if operate_profit > 0 and net_profit > 0:
                main_biz_ratio = min(100.0, (operate_profit / net_profit) * 100.0) if net_profit > 0 else 100.0

        result = FinancialData(
            ts_code=ts_code,
            end_date=end_date,
            revenue=revenue,
            revenue_yoy=revenue_yoy,
            deducted_profit=deducted_profit,
            deducted_yoy=deducted_yoy,
            net_profit=net_profit,
            net_profit_yoy=net_profit_yoy,
            gross_margin=gross_margin,
            roe=roe,
            roic=roic,
            ocf=0.0,
            ocf_ratio=ocf_ratio,
            debt_ratio=debt_ratio,
            main_biz_ratio=main_biz_ratio,
        )

        # 3. 写缓存
        self._cache.set_financial_cache(ts_code, [result.__dict__])

        return result

    # ─── 日线价格 ────────────────────────────

    def get_daily_data(
        self, ts_code: str, start_date: str, end_date: str
    ) -> list[DailyPriceData]:
        """获取日线价格数据。"""
        cache_key = f"{start_date}_{end_date}"
        cached = self._cache.get_price_cache(ts_code)
        if cached is not None:
            # 尝试从缓存中按 date_range 匹配
            return [DailyPriceData(**item) for item in cached]

        logger.debug("获取日线数据: %s [%s -> %s]", ts_code, start_date, end_date)
        df = self._call_api(
            "daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=(
                "ts_code,trade_date,open,high,low,close,"
                "pre_close,change,pct_chg,vol,amount"
            ),
        )
        results: list[DailyPriceData] = []
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                results.append(DailyPriceData(
                    ts_code=str(row.get("ts_code", ts_code)),
                    trade_date=str(row.get("trade_date", "")),
                    open=safe_float(row.get("open")),
                    high=safe_float(row.get("high")),
                    low=safe_float(row.get("low")),
                    close=safe_float(row.get("close")),
                    pre_close=safe_float(row.get("pre_close")),
                    change=safe_float(row.get("change")),
                    pct_change=safe_float(row.get("pct_chg")),
                    vol=safe_float(row.get("vol")),
                    amount=safe_float(row.get("amount")),
                ))

        if results:
            self._cache.set_price_cache(ts_code, [r.__dict__ for r in results])

        return results

    # ─── 每日指标 ────────────────────────────

    def get_daily_basic(
        self, ts_code: str, days: int = 60
    ) -> list[DailyBasicData]:
        """获取每日指标数据。"""
        end_date = get_last_trade_date()
        start_date = (
            datetime.now() - timedelta(days=days + 10)
        ).strftime("%Y%m%d")

        logger.debug("获取每日指标: %s (%d 天)", ts_code, days)
        df = self._call_api(
            "daily_basic",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=(
                "ts_code,trade_date,turnover_rate,turnover_rate_f,"
                "volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv"
            ),
        )
        results: list[DailyBasicData] = []
        if df is not None and len(df) > 0:
            # 按 trade_date 降序，取最近 days 条
            df_sorted = df.sort_values("trade_date", ascending=False).head(days)
            for _, row in df_sorted.iterrows():
                results.append(DailyBasicData(
                    ts_code=str(row.get("ts_code", ts_code)),
                    trade_date=str(row.get("trade_date", "")),
                    turnover_rate=safe_float(row.get("turnover_rate")),
                    turnover_rate_f=safe_float(row.get("turnover_rate_f")),
                    volume_ratio=safe_float(row.get("volume_ratio")),
                    pe=safe_float(row.get("pe")),
                    pe_ttm=safe_float(row.get("pe_ttm")),
                    pb=safe_float(row.get("pb")),
                    total_mv=safe_float(row.get("total_mv")),
                    circ_mv=safe_float(row.get("circ_mv")),
                ))
        return results

    # ─── 资金流向 ────────────────────────────

    def get_moneyflow(
        self, ts_code: str, days: int = 60
    ) -> list[MoneyFlowData]:
        """获取资金流向数据。"""
        cached = self._cache.get_moneyflow_cache(ts_code)
        if cached is not None:
            return [MoneyFlowData(**item) for item in cached]

        end_date = get_last_trade_date()
        start_date = (
            datetime.now() - timedelta(days=days + 10)
        ).strftime("%Y%m%d")

        logger.debug("获取资金流向: %s (%d 天)", ts_code, days)
        df = self._call_api(
            "moneyflow",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=(
                "ts_code,trade_date,buy_lg_amount,sell_lg_amount,"
                "buy_md_amount,sell_md_amount,"
                "buy_sm_amount,sell_sm_amount"
            ),
        )
        results: list[MoneyFlowData] = []
        if df is not None and len(df) > 0:
            df_sorted = df.sort_values("trade_date", ascending=False).head(days)
            for _, row in df_sorted.iterrows():
                results.append(MoneyFlowData(
                    ts_code=str(row.get("ts_code", ts_code)),
                    trade_date=str(row.get("trade_date", "")),
                    buy_lg_amount=safe_float(row.get("buy_lg_amount")),
                    sell_lg_amount=safe_float(row.get("sell_lg_amount")),
                    buy_md_amount=safe_float(row.get("buy_md_amount")),
                    sell_md_amount=safe_float(row.get("sell_md_amount")),
                    buy_sm_amount=safe_float(row.get("buy_sm_amount")),
                    sell_sm_amount=safe_float(row.get("sell_sm_amount")),
                ))

        if results:
            self._cache.set_moneyflow_cache(ts_code, [r.__dict__ for r in results])

        return results

    # ─── 筹码分布 ────────────────────────────

    def get_cyq(self, ts_code: str) -> Optional[CyqData]:
        """获取筹码分布数据。

        从 cyq_perf（筹码性能）和 cyq_chips（筹码成本分布）获取。
        """
        cached = self._cache.get_cyq_cache(ts_code)
        if cached is not None and len(cached) > 0:
            return CyqData(**cached[0])

        trade_date = get_last_trade_date()
        logger.debug("获取筹码数据: %s (trade_date=%s)", ts_code, trade_date)

        # cyq_perf: 获利盘比例、平均成本等
        perf_df = self._call_api(
            "cyq_perf",
            ts_code=ts_code,
            trade_date=trade_date,
            fields=(
                "ts_code,trade_date,profit_ratio,avg_cost,"
                "cost_concentration,lockup_ratio"
            ),
        )
        # cyq_chips: 成本峰
        chips_df = self._call_api(
            "cyq_chips",
            ts_code=ts_code,
            trade_date=trade_date,
            fields=(
                "ts_code,trade_date,price,percent"
            ),
        )

        if perf_df is None or len(perf_df) == 0:
            logger.warning("无筹码数据: %s", ts_code)
            return None

        row = perf_df.iloc[0]
        peak_price = 0.0
        peak_strength = 0.0
        if chips_df is not None and len(chips_df) > 0:
            # 找到 percent 最大的行作为成本峰
            peak_idx = chips_df["percent"].idxmax()
            peak_row = chips_df.loc[peak_idx]
            peak_price = safe_float(peak_row.get("price"))
            peak_strength = safe_float(peak_row.get("percent"))

        result = CyqData(
            ts_code=ts_code,
            trade_date=str(row.get("trade_date", trade_date)),
            profit_ratio=safe_float(row.get("profit_ratio")),
            avg_cost=safe_float(row.get("avg_cost")),
            cost_concentration=safe_float(row.get("cost_concentration")),
            peak_price=peak_price,
            peak_strength=peak_strength,
            lockup_ratio=safe_float(row.get("lockup_ratio")),
        )

        self._cache.set_cyq_cache(ts_code, [result.__dict__])

        return result

    # ─── 市场数据 ────────────────────────────

    def get_market_data(self) -> MarketScoreResult:
        """获取市场整体评分数据。"""
        cached = self._cache.get_market_cache()
        if cached is not None and len(cached) > 0:
            return MarketScoreResult(**cached[0])

        # 使用 tushare 指数行情评估市场状态
        try:
            # 获取上证指数近期表现
            df_index = self._call_api(
                "daily",
                ts_code="000001.SH",
                start_date=(
                    datetime.now() - timedelta(days=60)
                ).strftime("%Y%m%d"),
                end_date=get_last_trade_date(),
                fields="trade_date,pct_chg,amount,vol",
            )
            if df_index is not None and len(df_index) > 0:
                recent = df_index.head(20)
                avg_change = recent["pct_chg"].mean()
                volatility = recent["pct_chg"].std()

                if avg_change > 0.5 and volatility < 1.5:
                    regime = "bull"
                    multiplier = 1.05
                    score = 75.0
                elif avg_change > 0:
                    regime = "recovery"
                    multiplier = 1.00
                    score = 60.0
                elif avg_change > -0.3:
                    regime = "weak"
                    multiplier = 0.85
                    score = 40.0
                else:
                    regime = "bear"
                    multiplier = 0.65
                    score = 25.0

                risk_appetite = max(0, min(100, 50 + avg_change * 20))
                result = MarketScoreResult(
                    regime=regime,
                    multiplier=multiplier,
                    score=score,
                    risk_appetite=risk_appetite,
                    logic=[f"近20日平均涨跌: {avg_change:.2f}%, 波动率: {volatility:.2f}%"],
                )
            else:
                result = MarketScoreResult()
        except Exception as exc:
            logger.warning("获取市场数据失败: %s", exc)
            result = MarketScoreResult()

        self._cache.set_market_cache([result.__dict__])
        return result

    # ─── 股票基本信息 ────────────────────────

    def get_stock_basic(self, ts_code: str) -> Optional[StockBasic]:
        """获取股票基本信息。"""
        cached = self._cache.get_stock_basic_cache(ts_code)
        if cached is not None and len(cached) > 0:
            return StockBasic(**cached[0])

        df = self._call_api(
            "stock_basic",
            ts_code=ts_code,
            fields="ts_code,name,industry,area,market",
        )
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            result = StockBasic(
                ts_code=str(row.get("ts_code", ts_code)),
                name=str(row.get("name", "")),
                industry=str(row.get("industry", "")),
                area=str(row.get("area", "")),
                market=str(row.get("market", "")),
            )
            self._cache.set_stock_basic_cache(ts_code, [result.__dict__])
            return result

        logger.warning("无股票基本信息: %s", ts_code)
        return None

    # ─── 行业数据 ────────────────────────────

    def get_industry_data(self) -> dict[str, float]:
        """获取行业主题热度评分。

        Returns:
            dict[str, float]: 行业代码 -> 热度评分 (0-100)。
        """
        cached = self._cache.get_industry_cache()
        if cached is not None:
            return {item["industry"]: item["score"] for item in cached}

        # 使用申万行业分类获取各行业近期表现
        # 先获取行业列表
        df_industry = self._call_api(
            "index_classify",
            level="L1",
            src="SW",
            fields="index_code,industry_name",
        )
        result: dict[str, float] = {}

        if df_industry is not None and len(df_industry) > 0:
            end_date = get_last_trade_date()
            start_date = (
                datetime.now() - timedelta(days=20)
            ).strftime("%Y%m%d")

            for _, row in df_industry.iterrows():
                index_code = str(row.get("index_code", ""))
                # 行业指数 daily 行情
                df_idx = self._call_api(
                    "daily",
                    ts_code=index_code,
                    start_date=start_date,
                    end_date=end_date,
                    fields="trade_date,pct_chg",
                )
                if df_idx is not None and len(df_idx) > 0:
                    avg_pct = df_idx["pct_chg"].mean()
                    # 映射到 0-100 分
                    score = min(100, max(0, 50 + avg_pct * 5))
                else:
                    score = 50.0

                industry_code = str(row.get("industry_name", index_code))
                result[industry_code] = score

        # 缓存
        cache_data = [{"industry": k, "score": v} for k, v in result.items()]
        self._cache.set_industry_cache(cache_data)

        return result

    # ─── 公告数据 ────────────────────────────

    def get_announcement(
        self, ts_code: str, announce_date: str
    ) -> dict[str, Any]:
        """获取公告数据。

        Args:
            ts_code: 股票代码。
            announce_date: 公告日期 YYYYMMDD。

        Returns:
            公告信息字典。
        """
        cached = self._cache.get_announcement_cache(ts_code, announce_date)
        if cached is not None and len(cached) > 0:
            return cached[0]

        # 使用 tushare 的 disclos 接口
        df = self._call_api(
            "disclosure",
            ts_code=ts_code,
            start_date=announce_date,
            end_date=announce_date,
            fields="ts_code,ann_date,title,type",
        )
        result: dict[str, Any] = {
            "ts_code": ts_code,
            "ann_date": announce_date,
            "title": "",
            "type": "",
        }
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            result["title"] = str(row.get("title", ""))
            result["type"] = str(row.get("type", ""))

        self._cache.set_announcement_cache(ts_code, announce_date, [result])

        return result

    # ─── 内部辅助 ────────────────────────────

    @staticmethod
    def _get_latest_financial_end_date() -> str:
        """获取最新已结束的财报日期。"""
        now = datetime.now()
        year = now.year
        month = now.month
        if month <= 4:
            return f"{year - 1}1231"
        elif month <= 8:
            return f"{year}0630"
        elif month <= 10:
            return f"{year}0930"
        else:
            return f"{year}0930"

    @property
    def pro(self) -> Any:
        """获取 tushare pro 实例。"""
        return self._pro

    @property
    def cache(self) -> EldCache:
        """获取缓存实例。"""
        return self._cache
