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
      - 缓存优先（先查 SQLite、cache_daily 本地缓存，再调 API）
      - API 限流与自动重试
      - 数据模型转换
    """

    # cache_daily 目录（系统级缓存，由其他模块维护）
    _CACHE_DAILY = Path(r"d:\mystock\cache_daily")

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

        # ── Parquet 文件缓存目录 ──
        self._parquet_cache_dir = Path(cache.config.sqlite_cache_dir) / "parquet"
        self._parquet_cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_enabled = True
        self._cache_expire_hours = cache.config.expire_hours

        # ── lazy-load 缓存（从 cache_daily 读取后常驻内存） ──
        self._stock_basic_df: Optional[pd.DataFrame] = None  # stock_basic.csv
        self._treasure_basic_df: Optional[pd.DataFrame] = None  # treasure_daily_basic_*.parquet
        self._daily_csv_data: Optional[dict[str, list[dict]]] = None  # daily_YYYYMMDD.csv → {ts_code: [records]}
        self._daily_csv_range: tuple[str, str] = ("", "")  # (min_date, max_date) 已加载范围
        self._forecast_cache: Optional[list[ForecastData]] = None  # get_forecast_all 内存缓存

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

    # ─── Parquet 文件缓存（参考 multi_factor_picker 模式） ───

    def _cache_path(self, key: str) -> Path:
        """获取 parquet 缓存文件路径。"""
        return self._parquet_cache_dir / f"{key}.parquet"

    def _load_cache(self, key: str, expire_hours: Optional[int] = None) -> Optional[pd.DataFrame]:
        """加载 parquet 缓存数据。

        参考 multi_factor_picker.data_fetcher.load_cache:
          - 按文件 mtime 判断过期
          - 优先 parquet（快），返回 DataFrame
        """
        if not self._cache_enabled:
            return None
        path = self._cache_path(key)
        if not path.exists():
            return None
        expire = expire_hours if expire_hours is not None else self._cache_expire_hours
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if (datetime.now() - mtime) > timedelta(hours=expire):
                return None
            df = pd.read_parquet(path)
            return df
        except Exception as exc:
            logger.warning("读取 parquet 缓存失败 [%s]: %s", key, exc)
            return None

    def _save_cache(self, key: str, df: pd.DataFrame) -> None:
        """保存 parquet 缓存文件。"""
        if not self._cache_enabled:
            return
        if df is None or len(df) == 0:
            return
        path = self._cache_path(key)
        try:
            df.to_parquet(path, index=False)
        except Exception as exc:
            logger.warning("保存 parquet 缓存失败 [%s]: %s", key, exc)

    def _get_df_cached(self, method: str, cache_key: str,
                       expire_hours: Optional[int] = None,
                       **kwargs: Any) -> pd.DataFrame:
        """统一模式：优先 parquet 缓存 → API 调用 → 保存缓存 → 返回 DataFrame。

        参考 multi_factor_picker.data_fetcher._get_df_cached / _retry_call 模式。
        减少重复的缓存检查 + API 调用样板代码。

        Args:
            method: tushare API 方法名（如 'daily', 'moneyflow'）。
            cache_key: 缓存键（如 'daily_basic_code_000001_SZ_20260701_20260724'）。
            expire_hours: 缓存过期小时数，默认使用配置值。
            **kwargs: 传递给 tushare API 的参数。

        Returns:
            DataFrame，API 失败时返回空 DataFrame。
        """
        # 1. 缓存命中
        expire = expire_hours if expire_hours is not None else self._cache_expire_hours
        cached = self._load_cache(cache_key, expire)
        if cached is not None:
            return cached

        # 2. API 调用
        df = self._call_api(method, **kwargs)

        # 3. 缓存结果
        if df is not None and len(df) > 0:
            self._save_cache(cache_key, df)
            return df

        return pd.DataFrame()

    # ─── cache_daily 本地缓存读取 ─────────────

    def _load_stock_basic_csv(self) -> Optional[pd.DataFrame]:
        """lazy-load stock_basic.csv → DataFrame。"""
        if self._stock_basic_df is not None:
            return self._stock_basic_df
        path = self._CACHE_DAILY / "stock_basic.csv"
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path, dtype={"ts_code": str, "name": str, "industry": str, "list_date": str})
            self._stock_basic_df = df
            logger.info("加载 stock_basic.csv: %d 条记录", len(df))
            return df
        except Exception as exc:
            logger.warning("读取 stock_basic.csv 失败: %s", exc)
            return None

    def _load_treasure_basic(self) -> Optional[pd.DataFrame]:
        """lazy-load 最新的 treasure_daily_basic_*.parquet → DataFrame。"""
        if self._treasure_basic_df is not None:
            return self._treasure_basic_df
        files = sorted(self._CACHE_DAILY.glob("treasure_daily_basic_*.parquet"))
        if not files:
            return None
        latest = files[-1]
        try:
            df = pd.read_parquet(latest)
            self._treasure_basic_df = df
            logger.info("加载 %s: %d 条记录", latest.name, len(df))
            return df
        except Exception as exc:
            logger.warning("读取 %s 失败: %s", latest.name, exc)
            return None

    def _load_daily_csv_range(self, start_date: str, end_date: str) -> None:
        """将 [start_date, end_date] 范围内的 daily_YYYYMMDD.csv 加载到内存（仅一次）。"""
        # 已加载过则直接返回
        if self._daily_csv_data is not None:
            return

        result: dict[str, list[dict]] = {}
        min_date, max_date = "99999999", "00000000"
        for fpath in sorted(self._CACHE_DAILY.glob("daily_????????.csv")):
            fname = fpath.stem  # daily_20260724
            date_str = fname.split("_")[-1]
            if date_str < start_date or date_str > end_date:
                continue
            if date_str < min_date:
                min_date = date_str
            if date_str > max_date:
                max_date = date_str
            try:
                df = pd.read_csv(fpath, dtype={"ts_code": str})
                for _, row in df.iterrows():
                    code = str(row.get("ts_code", ""))
                    if not code:
                        continue
                    rec = {
                        "ts_code": code,
                        "trade_date": date_str,
                        "open": safe_float(row.get("open")),
                        "high": safe_float(row.get("high")),
                        "low": safe_float(row.get("low")),
                        "close": safe_float(row.get("close")),
                        "pre_close": safe_float(row.get("pre_close")),
                        "change": safe_float(row.get("change")),
                        "pct_chg": safe_float(row.get("pct_chg")),
                        "vol": safe_float(row.get("vol")),
                        "amount": safe_float(row.get("amount")),
                    }
                    result.setdefault(code, []).append(rec)
            except Exception as exc:
                logger.warning("读取日线缓存 %s 失败: %s", fpath.name, exc)

        self._daily_csv_data = result
        self._daily_csv_range = (min_date, max_date) if min_date != "99999999" else ("", "")
        logger.info(
            "加载日线缓存 [%s ~ %s]: %d 只股票",
            min_date, max_date, len(result),
        )

    # ─── 业绩预告 ────────────────────────────

    # 全量中报预告 parquet 缓存路径（由外部 multi_factor_picker 维护）
    _FORECAST_PARQUET = Path(
        r"d:\mystock\solo\multi_factor_picker\cache\forecast_vip_20260630.parquet"
    )

    # 当前中报报告期
    _FORECAST_PERIOD = "20260630"

    def get_forecast_all(self) -> list[ForecastData]:
        """获取全市场业绩预告数据。

        参考 multi_factor_picker.data_fetcher.get_forecast_vip 模式：
        使用 forecast_vip 一次性获取全量数据，替代逐日扫描。

        优先级：
          1. 内存缓存（_forecast_cache）
          2. 本地 parquet 缓存（forecast_vip_all.parquet）
          3. multi_factor_picker 共享 parquet 缓存
          4. forecast_vip API（单次调用，替代原先120次循环）
        """
        # ── 0. 内存缓存 ──
        if self._forecast_cache is not None:
            return self._forecast_cache

        trade_date = get_last_trade_date()
        period = self._FORECAST_PERIOD

        # ── 1. 本地 parquet 缓存 ──
        cache_key = f"forecast_vip_{period}_{trade_date}"
        df_cached = self._load_cache(cache_key, expire_hours=24)
        if df_cached is not None and len(df_cached) > 0:
            results = self._df_to_forecast_list(df_cached)
            if results:
                logger.info(
                    "从本地 parquet 缓存读取业绩预告: %d 条",
                    len(results),
                )
                self._forecast_cache = results
                return results

        # ── 2. multi_factor_picker 共享 parquet ──
        if self._FORECAST_PARQUET.exists():
            try:
                df = pd.read_parquet(self._FORECAST_PARQUET)
                results = self._df_to_forecast_list(df)
                if results:
                    logger.info(
                        "从 multi_factor_picker 缓存读取业绩预告: %d 条 (%s)",
                        len(results), self._FORECAST_PARQUET.name,
                    )
                    # 写入本地缓存便于下次快速加载
                    self._save_cache(cache_key, df)
                    self._forecast_cache = results
                    return results
            except Exception as exc:
                logger.warning("读取共享 parquet 缓存失败: %s", exc)

        # ── 3. forecast_vip API（单次调用，替代原先120次循环） ──
        logger.info("从 forecast_vip API 获取全量业绩预告 (period=%s)...", period)
        df = self._call_api(
            "forecast_vip",
            period=period,
            fields=(
                "ts_code,ann_date,end_date,type,period,"
                "p_change_min,p_change_max,net_profit_min,"
                "net_profit_max,last_parent_net,summary"
            ),
        )

        results = self._df_to_forecast_list(df) if df is not None else []

        if results:
            self._save_cache(cache_key, df)
            self._forecast_cache = results
            logger.info("forecast_vip API 获取业绩预告: %d 条", len(results))
        else:
            logger.warning("forecast_vip API 未返回数据")

        return results

    def _df_to_forecast_list(self, df: pd.DataFrame) -> list[ForecastData]:
        """将 forecast_vip DataFrame 转为 ForecastData 列表。"""
        results: list[ForecastData] = []
        seen: set[tuple[str, str]] = set()
        for _, row in df.iterrows():
            ts_code = str(row.get("ts_code", ""))
            if not ts_code:
                continue
            ed = str(row.get("end_date", ""))
            key = (ts_code, ed)
            if key in seen:
                continue
            seen.add(key)
            summary = str(row.get("summary", ""))
            results.append(ForecastData(
                ts_code=ts_code,
                end_date=ed,
                type=str(row.get("type", "")),
                p_change_min=safe_float(row.get("p_change_min")),
                p_change_max=safe_float(row.get("p_change_max")),
                announce_date=str(row.get("ann_date", "")),
                fiscal_quarter=ed,
                summary=summary,
            ))
        return results

    # ─── 财务数据 ────────────────────────────

    def get_financial(self, ts_code: str) -> Optional[FinancialData]:
        """获取指定股票的最新财务数据。

        优先读取 SQLite 缓存，其次 API（统一 _get_df_cached Parquet 缓存）。
        综合 fina_indicator（财务指标）、income（利润表）和同比数据合并。
        """
        # 1. SQLite 缓存
        cached = self._cache.get_financial_cache(ts_code)
        if cached is not None and len(cached) > 0:
            return FinancialData(**cached[0])

        end_date = self._get_latest_financial_end_date()
        end = end_date

        # 2. API（使用 _get_df_cached 统一缓存）
        # 2a. 财务指标（roe, roic, gross_margin 等）
        fina_key = f"fina_indicator_{ts_code}_{end}"
        df_indicator = self._get_df_cached(
            "fina_indicator", fina_key,
            ts_code=ts_code, end_date=end,
            fields=(
                "ts_code,end_date,roe,roic,gross_margin,"
                "ocf_to_orp,debt_to_assets,"
                "profit_dedt,eps"
            ),
        )
        # 2b. 利润表（营收、净利润等）
        income_key = f"income_{ts_code}_{end}"
        df_income = self._get_df_cached(
            "income", income_key,
            ts_code=ts_code, end_date=end,
            fields=(
                "ts_code,end_date,revenue,"
                "n_income,"
                "operate_profit"
            ),
        )
        # 2c. 营收同比数据
        fina_yoy_key = f"fina_indicator_yoy_{ts_code}_{end}"
        df_fina = self._get_df_cached(
            "fina_indicator", fina_yoy_key,
            ts_code=ts_code, end_date=end,
            fields=(
                "ts_code,end_date,"
                "or_yoy,"
                "q_deducted_profit_yoy,"
                "q_profit_yoy"
            ),
        )

        # 如果 fina_indicator 和 income 都为空，尝试从 cache_daily/income_*.parquet 补充
        income_row = None
        net_profit = 0.0
        revenue = 0.0
        operate_profit = 0.0

        if len(df_income) > 0:
            income_row = df_income.iloc[0]
        else:
            # 尝试 local income parquet 补充
            income_path = self._CACHE_DAILY / f"income_{ts_code.replace('.', '_')}.parquet"
            if income_path.exists():
                try:
                    df_inc = pd.read_parquet(income_path)
                    df_inc = df_inc.sort_values("end_date", ascending=False)
                    if len(df_inc) > 0:
                        income_row = df_inc.iloc[0]
                except Exception as exc:
                    logger.warning("读取 income 缓存 %s 失败: %s", income_path.name, exc)

        if income_row is not None:
            revenue = safe_float(income_row.get("revenue", 0))
            net_profit = safe_float(income_row.get("n_income", 0))
            operate_profit = safe_float(income_row.get("operate_profit", 0))

        # 如果 fina_indicator 无数据，但 income 有数据，返回 partial 结果
        if len(df_indicator) == 0 and income_row is None:
            logger.warning("无财务数据: %s", ts_code)
            return None

        # 构建完整财务数据
        fina_row = df_indicator.iloc[0] if len(df_indicator) > 0 else None
        fina_yoy_row = df_fina.iloc[0] if len(df_fina) > 0 else None

        revenue_yoy = safe_float(fina_yoy_row.get("or_yoy")) if fina_yoy_row is not None else 0.0
        deducted_yoy = safe_float(fina_yoy_row.get("q_deducted_profit_yoy")) if fina_yoy_row is not None else 0.0
        net_profit_yoy = safe_float(fina_yoy_row.get("q_profit_yoy")) if fina_yoy_row is not None else 0.0

        deducted_profit = safe_float(fina_row.get("profit_dedt")) if fina_row is not None else 0.0
        gross_margin = safe_float(fina_row.get("gross_margin")) if fina_row is not None else 0.0
        roe = safe_float(fina_row.get("roe")) if fina_row is not None else 0.0
        roic = safe_float(fina_row.get("roic")) if fina_row is not None else 0.0
        ocf_ratio = safe_float(fina_row.get("ocf_to_orp")) if fina_row is not None else 0.0
        debt_ratio = safe_float(fina_row.get("debt_to_assets")) if fina_row is not None else 0.0

        # 主营业务收入占比
        main_biz_ratio = 100.0
        if net_profit > 0 and operate_profit > 0:
            main_biz_ratio = min(100.0, (operate_profit / net_profit) * 100.0)

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

        # SQLite 缓存写回
        self._cache.set_financial_cache(ts_code, [result.__dict__])
        return result

    # ─── 日线价格 ────────────────────────────

    def get_daily_data(
        self, ts_code: str, start_date: str, end_date: str
    ) -> list[DailyPriceData]:
        """获取日线价格数据。

        优先从 cache_daily/daily_YYYYMMDD.csv 读取，其次 SQLite，最后 API。
        """
        # 1. SQLite 缓存
        cached = self._cache.get_price_cache(ts_code)
        if cached is not None:
            return [DailyPriceData(**item) for item in cached]

        # 2. cache_daily / daily_YYYYMMDD.csv
        self._load_daily_csv_range(start_date, end_date)
        if self._daily_csv_data is not None and ts_code in self._daily_csv_data:
            records = self._daily_csv_data[ts_code]
            # 按 date 过滤
            results: list[DailyPriceData] = []
            for rec in records:
                if start_date <= rec["trade_date"] <= end_date:
                    results.append(DailyPriceData(
                        ts_code=ts_code,
                        trade_date=rec["trade_date"],
                        open=rec["open"],
                        high=rec["high"],
                        low=rec["low"],
                        close=rec["close"],
                        pre_close=rec["pre_close"],
                        change=rec["change"],
                        pct_change=rec["pct_chg"],
                        vol=rec["vol"],
                        amount=rec["amount"],
                    ))
            if len(results) >= 10:  # 足够趋势评分使用
                results.sort(key=lambda x: x.trade_date)
                return results
            elif results:
                logger.info(
                    "CSV缓存数据不足 %s: 仅%d条，回退到API获取更多数据",
                    ts_code, len(results),
                )

        # 3. API（使用 _get_df_cached 统一缓存）
        cache_key = f"daily_{ts_code}_{start_date}_{end_date}"
        df = self._get_df_cached(
            "daily", cache_key,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=(
                "ts_code,trade_date,open,high,low,close,"
                "pre_close,change,pct_chg,vol,amount"
            ),
        )
        results = []
        if len(df) > 0:
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

        return results

    # ─── 每日指标 ────────────────────────────

    def get_daily_basic(
        self, ts_code: str, days: int = 60
    ) -> list[DailyBasicData]:
        """获取每日指标数据。

        优先从 cache_daily（treasure_daily_basic + daily_basic_csv）读取，最后 API。
        """
        # 1. cache_daily: treasure_daily_basic_*.parquet（有 total_mv/circ_mv）
        tb_df = self._load_treasure_basic()
        tb_records: list[DailyBasicData] = []
        if tb_df is not None:
            mask = tb_df["ts_code"] == ts_code
            sub = tb_df[mask]
            for _, row in sub.iterrows():
                tb_records.append(DailyBasicData(
                    ts_code=ts_code,
                    trade_date=str(row.get("trade_date", "")),
                    turnover_rate=safe_float(row.get("turnover_rate")),
                    volume_ratio=safe_float(row.get("volume_ratio")),
                    pe=safe_float(row.get("pe")),
                    pe_ttm=safe_float(row.get("pe_ttm")),
                    pb=safe_float(row.get("pb")),
                    total_mv=safe_float(row.get("total_mv")),
                    circ_mv=safe_float(row.get("circ_mv")),
                    turnover_rate_f=0.0,
                ))

        # 2. cache_daily: daily_basic_{ts_code}.csv（多日期、含 turnover_rate/volume_ratio/pe_ttm/pb）
        basic_csv_path = self._CACHE_DAILY / f"daily_basic_{ts_code.replace('.', '_')}.csv"
        if basic_csv_path.exists():
            try:
                df_csv = pd.read_csv(basic_csv_path, dtype={"ts_code": str})
                csv_records: list[DailyBasicData] = []
                for _, row in df_csv.iterrows():
                    csv_records.append(DailyBasicData(
                        ts_code=ts_code,
                        trade_date=str(row.get("trade_date", "")),
                        turnover_rate=safe_float(row.get("turnover_rate")),
                        volume_ratio=safe_float(row.get("volume_ratio")),
                        pe=safe_float(row.get("pe_ttm")),
                        pe_ttm=safe_float(row.get("pe_ttm")),
                        pb=safe_float(row.get("pb")),
                        total_mv=0.0,
                        circ_mv=0.0,
                        turnover_rate_f=0.0,
                    ))

                # 合并：用 treasure 补充 total_mv/circ_mv，用 csv 补充更多日期
                tb_map: dict[str, DailyBasicData] = {r.trade_date: r for r in tb_records}
                merged_map: dict[str, DailyBasicData] = {}
                for r in csv_records:
                    if r.trade_date in tb_map:
                        tb = tb_map[r.trade_date]
                        r.total_mv = tb.total_mv
                        r.circ_mv = tb.circ_mv
                    merged_map[r.trade_date] = r
                # 补充 treasure 中独有日期
                for r in tb_records:
                    if r.trade_date not in merged_map:
                        merged_map[r.trade_date] = r

                merged = sorted(merged_map.values(), key=lambda x: x.trade_date, reverse=True)[:days]
                if merged:
                    return merged
            except Exception as exc:
                logger.warning("读取 daily_basic 缓存 %s 失败: %s", basic_csv_path.name, exc)

        # 3. 仅 treasure 数据（无 daily_basic csv）
        if tb_records:
            tb_sorted = sorted(tb_records, key=lambda x: x.trade_date, reverse=True)[:days]
            return tb_sorted

        # 4. API 兜底
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

        cache_key = f"moneyflow_{ts_code}_{start_date}_{end_date}"
        df = self._get_df_cached(
            "moneyflow", cache_key,
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
        if len(df) > 0:
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

        # cyq_perf: 获利盘比例、平均成本等
        # 注意：Tushare cyq_perf 实际返回字段为：
        #   his_low, his_high, cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
        #   weight_avg (平均成本), winner_rate (获利盘比例 %)
        perf_key = f"cyq_perf_{ts_code}_{trade_date}"
        perf_df = self._get_df_cached(
            "cyq_perf", perf_key, expire_hours=24,
            ts_code=ts_code, trade_date=trade_date,
            fields=(
                "ts_code,trade_date,winner_rate,weight_avg,"
                "cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,"
                "his_low,his_high"
            ),
        )
        # cyq_chips: 成本峰
        chips_key = f"cyq_chips_{ts_code}_{trade_date}"
        chips_df = self._get_df_cached(
            "cyq_chips", chips_key, expire_hours=24,
            ts_code=ts_code, trade_date=trade_date,
            fields=("ts_code,trade_date,price,percent"),
        )

        if len(perf_df) == 0:
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

        # cyq_perf 字段映射
        winner_rate = safe_float(row.get("winner_rate"))  # 0-100 百分比
        weight_avg = safe_float(row.get("weight_avg"))    # 平均成本
        cost_5 = safe_float(row.get("cost_5pct"))
        cost_95 = safe_float(row.get("cost_95pct"))
        cost_50 = safe_float(row.get("cost_50pct"))

        # profit_ratio: winner_rate 是 0-100 百分比，转成 0-1 比率
        profit_ratio = winner_rate / 100.0 if winner_rate > 0 else 0.0

        # avg_cost: weight_avg 就是平均成本
        avg_cost = weight_avg

        # cost_concentration: 用 (cost_95 - cost_5) / cost_50 估算集中度，越小越集中
        cost_concentration = 0.0
        if cost_50 > 0 and cost_95 > 0 and cost_5 > 0:
            cost_concentration = (cost_95 - cost_5) / cost_50
        elif cost_50 > 0 and weight_avg > 0:
            # 用成本峰峰值估算
            if peak_price > 0:
                cost_concentration = abs(peak_price - weight_avg) / weight_avg
            else:
                cost_concentration = 0.3  # 中性假设

        # lockup_ratio: cyq_perf 无锁仓字段，用峰值强度近似
        # 峰值强度高说明筹码锁定好
        lockup_ratio = min(1.0, peak_strength / 10.0) if peak_strength > 0 else 0.0

        result = CyqData(
            ts_code=ts_code,
            trade_date=str(row.get("trade_date", trade_date)),
            profit_ratio=profit_ratio,
            avg_cost=avg_cost,
            cost_concentration=cost_concentration,
            peak_price=peak_price,
            peak_strength=peak_strength,
            lockup_ratio=lockup_ratio,
        )

        return result

    # ─── 市场数据 ────────────────────────────

    def get_market_data(self) -> MarketScoreResult:
        """获取市场整体评分数据。"""
        cached = self._cache.get_market_cache()
        if cached is not None and len(cached) > 0:
            return MarketScoreResult(**cached[0])

        # 使用 tushare 指数行情评估市场状态
        try:
            # 获取上证指数近期表现（使用 index_daily 而非 daily）
            df_index = self._call_api(
                "index_daily",
                ts_code="000001.SH",
                start_date=(
                    datetime.now() - timedelta(days=60)
                ).strftime("%Y%m%d"),
                end_date=get_last_trade_date(),
                fields="trade_date,pct_chg,amount,vol",
            )
            if df_index is not None and len(df_index) > 0:
                # Tushare 返回按 trade_date 升序，取最近20行
                recent = df_index.sort_values("trade_date", ascending=False).head(20)
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
        """获取股票基本信息。

        优先从 cache_daily/stock_basic.csv 读取，其次 SQLite，最后 API。
        """
        # 1. SQLite 缓存
        cached = self._cache.get_stock_basic_cache(ts_code)
        if cached is not None and len(cached) > 0:
            return StockBasic(**cached[0])

        # 2. cache_daily / stock_basic.csv
        df = self._load_stock_basic_csv()
        if df is not None:
            mask = df["ts_code"] == ts_code
            row_df = df[mask]
            if len(row_df) > 0:
                row = row_df.iloc[0]
                result = StockBasic(
                    ts_code=str(row.get("ts_code", ts_code)),
                    name=str(row.get("name", "")),
                    industry=str(row.get("industry", "")),
                    area="",
                    market="",
                )
                self._cache.set_stock_basic_cache(ts_code, [result.__dict__])
                return result

        # 3. API
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

        从 stock_basic.csv 获取行业列表，结合多只个股近期涨跌幅均值估算行业热度。
        免费版 Tushare 无行业指数接口，采用个股聚合方式。

        Returns:
            dict[str, float]: 行业名称 -> 热度评分 (0-100)。
        """
        cached = self._cache.get_industry_cache()
        if cached is not None:
            return {item["industry"]: item["score"] for item in cached}

        result: dict[str, float] = {}

        # 从 stock_basic.csv 获取行业列表
        df_basic = self._load_stock_basic_csv()
        if df_basic is None or len(df_basic) == 0:
            return result

        # 统计各行业股票数量，取TOP30行业做评分
        industry_counts = df_basic["industry"].value_counts()
        top_industries = industry_counts.head(30).index.tolist()

        # 获取最近交易日
        end_date = get_last_trade_date()
        start_date = (datetime.now() - timedelta(days=20)).strftime("%Y%m%d")

        # 对每个行业，采样该行业股票并计算平均涨幅
        for ind_name in top_industries:
            if not ind_name or ind_name == "None":
                continue
            # 取该行业最多5只股票
            sample_codes = df_basic[df_basic["industry"] == ind_name]["ts_code"].head(5).tolist()
            pct_sum = 0.0
            valid_count = 0
            for code in sample_codes:
                daily = self.get_daily_data(code, start_date, end_date)
                if daily and len(daily) >= 2:
                    # 计算区间涨跌幅
                    pct = (daily[-1].close - daily[0].close) / daily[0].close * 100.0
                    pct_sum += pct
                    valid_count += 1
            if valid_count > 0:
                avg_pct = pct_sum / valid_count
                score = min(100.0, max(0.0, 50.0 + avg_pct * 3))
            else:
                score = 50.0
            result[ind_name] = score

        # 缓存
        cache_data = [{"industry": k, "score": v} for k, v in result.items()]
        self._cache.set_industry_cache(cache_data)
        logger.info("行业热度评分: %d 个行业", len(result))

        return result

    def get_industry_rank(self, ts_code: str) -> Optional[int]:
        """获取个股所属行业的排名（按行业热度）。

        Args:
            ts_code: 股票代码

        Returns:
            排名（1=最好），None 表示无法确定。
        """
        # 获取行业热度评分
        industry_scores = self.get_industry_data()
        if not industry_scores:
            return None

        # 获取个股所属行业
        stock_industry = self.get_stock_industry(ts_code)
        if stock_industry is None or stock_industry not in industry_scores:
            return None

        # 按热度排序
        sorted_industries = sorted(industry_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (ind_name, _) in enumerate(sorted_industries, 1):
            if ind_name == stock_industry:
                return rank
        return None

    def get_stock_industry(self, ts_code: str) -> Optional[str]:
        """获取个股所属行业名称。

        Args:
            ts_code: 股票代码

        Returns:
            行业名称，None 表示无法获取。
        """
        sb = self.get_stock_basic(ts_code)
        if sb is not None and sb.industry:
            return sb.industry
        return None

    def get_industry_performance(self) -> Optional[pd.Series]:
        """获取各行业涨跌幅排名（Series）。

        Returns:
            Series: index=行业名称, values=热度评分, None 表示无法获取。
        """
        data = self.get_industry_data()
        if not data:
            return None
        return pd.Series(data).sort_values(ascending=False)

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

    # ─── 基准指数日线（用于趋势评分对比） ────────

    def get_benchmark_daily(self, ts_code: str) -> list[DailyPriceData]:
        """获取基准指数日线（沪深300）。

        基准统一为 000300.SH，不受个股代码影响。
        优先 SQLite 缓存，其次 API（使用 index_daily 接口）。

        Args:
            ts_code: 个股代码（仅用于接口签名统一，实际用大盘基准）
        """
        _ = ts_code  # 统一使用沪深300
        benchmark_code = "000300.SH"
        end = get_last_trade_date()
        start = (datetime.now() - timedelta(days=260)).strftime("%Y%m%d")

        # 1. SQLite 缓存
        cached = self._cache.get_price_cache(f"{benchmark_code}_benchmark")
        if cached is not None:
            return [DailyPriceData(**item) for item in cached]

        # 2. API（指数用 index_daily 而非 daily）
        logger.debug("获取基准指数日线: %s", benchmark_code)
        cache_key = f"index_daily_{benchmark_code}_{start}_{end}"
        df = self._get_df_cached(
            "index_daily", cache_key, expire_hours=24,
            ts_code=benchmark_code,
            start_date=start,
            end_date=end,
            fields="trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
        results: list[DailyPriceData] = []
        if len(df) > 0:
            df_sorted = df.sort_values("trade_date", ascending=True)
            for _, row in df_sorted.iterrows():
                results.append(DailyPriceData(
                    ts_code=benchmark_code,
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
            self._cache.set_price_cache(
                f"{benchmark_code}_benchmark",
                [r.__dict__ for r in results],
            )
        if not results:
            logger.warning("获取基准指数日线失败: %s", benchmark_code)
        return results

    # ─── 行业指数日线（用于趋势评分对比） ─────────

    def get_industry_daily(self, ts_code: str) -> list[DailyPriceData]:
        """获取个股所属行业的行业指数日线。

        优先从 cache_daily，其次 SQLite，最后 API。
        行业指数通过 stock_basic 中的 industry 字段映射到申万行业指数。

        Args:
            ts_code: 个股代码。
        """
        # 获取行业信息
        sb = self.get_stock_basic(ts_code)
        if sb is None or not sb.industry:
            return []

        industry_index_map: dict[str, str] = {
            "半导体": "990001.SI",
            "芯片": "990001.SI",
            "光伏": "990002.SI",
            "新能源": "990003.SI",
            "锂电池": "990003.SI",
            "汽车": "990004.SI",
            "医药生物": "990005.SI",
            "医药": "990005.SI",
            "医疗": "990005.SI",
            "消费": "990006.SI",
            "食品": "990006.SI",
            "金融": "990007.SI",
            "银行": "990007.SI",
            "房地产": "990008.SI",
            "地产": "990008.SI",
            "基建": "990009.SI",
            "国防军工": "990010.SI",
            "军工": "990010.SI",
            "机械设备": "990011.SI",
            "机械": "990011.SI",
            "基础化工": "990012.SI",
            "化工": "990012.SI",
            "有色金属": "990013.SI",
            "计算机": "990014.SI",
            "通信": "990015.SI",
            "电子": "990016.SI",
            "电力设备": "990017.SI",
            "交通运输": "990018.SI",
            "建筑装饰": "990019.SI",
            "建筑材料": "990020.SI",
            "轻工制造": "990021.SI",
            "纺织服饰": "990022.SI",
            "商贸零售": "990023.SI",
            "社会服务": "990024.SI",
            "传媒": "990025.SI",
            "农林牧渔": "990026.SI",
            "公用事业": "990027.SI",
            "环保": "990028.SI",
            "煤炭": "990029.SI",
            "石油石化": "990030.SI",
            "钢铁": "990031.SI",
            "综合": "990032.SI",
        }
        industry_idx = ""
        for keyword, idx_code in industry_index_map.items():
            if keyword in sb.industry:
                industry_idx = idx_code
                break
        if not industry_idx:
            return []

        end = get_last_trade_date()
        start = (datetime.now() - timedelta(days=260)).strftime("%Y%m%d")

        # SQLite 缓存
        cached = self._cache.get_price_cache(f"{industry_idx}_industry")
        if cached is not None:
            return [DailyPriceData(**item) for item in cached]

        # 复用 get_daily_data 的 CSV 缓存
        csv_data = self.get_daily_data(industry_idx, start, end)
        if csv_data and len(csv_data) >= 20:
            self._cache.set_price_cache(
                f"{industry_idx}_industry",
                [r.__dict__ for r in csv_data],
            )
            return csv_data

        # API（指数用 index_daily）
        logger.debug("获取行业指数日线: %s (%s)", industry_idx, sb.industry)
        cache_key = f"index_daily_{industry_idx}_{start}_{end}"
        df = self._get_df_cached(
            "index_daily", cache_key, expire_hours=24,
            ts_code=industry_idx,
            start_date=start,
            end_date=end,
            fields="trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
        results: list[DailyPriceData] = []
        if len(df) > 0:
            df_sorted = df.sort_values("trade_date", ascending=True)
            for _, row in df_sorted.iterrows():
                results.append(DailyPriceData(
                    ts_code=industry_idx,
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
            self._cache.set_price_cache(
                f"{industry_idx}_industry",
                [r.__dict__ for r in results],
            )
        return results

    # ─── 北向资金持仓（机构评分用） ─────────────

    def get_hk_hold(self, ts_code: str) -> list[dict[str, Any]]:
        """获取北向资金（沪深港通）持仓数据。

        优先 SQLite 缓存，其次 API。

        Args:
            ts_code: 个股代码。
        """
        cached = self._cache.get_cached("price_batch_cache", "ts_code", f"hk_{ts_code}")
        if cached is not None:
            return cached

        logger.debug("获取北向持仓: %s", ts_code)
        df = self._call_api(
            "hk_hold",
            ts_code=ts_code,
            fields="ts_code,code,name,vol,ratio,exchange,date",
        )
        results: list[dict[str, Any]] = []
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                results.append({
                    "ts_code": ts_code,
                    "date": str(row.get("date", "")),
                    "vol": safe_float(row.get("vol")),
                    "ratio": safe_float(row.get("ratio")),
                })

        if results:
            self._cache.set_cached(
                "price_batch_cache", "ts_code", f"hk_{ts_code}", results,
            )
        return results

    # ─── 基金持仓数据（机构评分用） ─────────────

    def get_fund_hold(self, ts_code: str) -> list[dict[str, Any]]:
        """获取基金持仓数据。

        优先 SQLite 缓存，其次 API。

        Args:
            ts_code: 个股代码。
        """
        cached = self._cache.get_cached("price_batch_cache", "ts_code", f"fund_{ts_code}")
        if cached is not None:
            return cached

        logger.debug("获取基金持仓: %s", ts_code)
        df = self._call_api(
            "fund_portfolio",
            ts_code=ts_code,
            fields="ts_code,ann_date,stk_name,stk_code,hold_amount,stkvoteratio",
        )
        results: list[dict[str, Any]] = []
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                results.append({
                    "ts_code": ts_code,
                    "ann_date": str(row.get("ann_date", "")),
                    "hold_amount": safe_float(row.get("hold_amount")),
                    "hold_ratio": safe_float(row.get("stkvoteratio")),
                })
            if results:
                self._cache.set_cached(
                    "price_batch_cache", "ts_code", f"fund_{ts_code}", results,
                )
        return results

    # ─── 预期差相关接口 ────────────────────────

    def get_forecast(self, ts_code: str) -> Optional[ForecastData]:
        """获取个股的业绩预告数据（用于预期差评分）。

        从全量预告数据中按 ts_code 筛选。

        Args:
            ts_code: 个股代码。
        """
        all_forecast = self.get_forecast_all()
        for fc in all_forecast:
            if fc.ts_code == ts_code:
                return fc
        return None

    def get_consensus(self, ts_code: str) -> Optional[float]:
        """获取市场一致预期净利润增速。

        无数据时返回 None。

        Args:
            ts_code: 个股代码。
        """
        return None  # Tushare 无一致预期接口

    def get_industry_avg_growth(self, ts_code: str) -> Optional[float]:
        """获取行业平均增速用于预期差估算。

        无数据时返回 None。

        Args:
            ts_code: 个股代码。
        """
        return None

    def get_historical_avg_growth(self, ts_code: str, years: int = 3) -> Optional[float]:
        """获取个股历史平均增速用于预期差估算。

        Args:
            ts_code: 个股代码。
            years: 回溯年数。
        """
        return None

    # ─── 预期差引擎专用：行业财务基准 ─────────────

    def get_financial_quarters(
        self, ts_code: str, num_quarters: int = 4
    ) -> list[FinancialData]:
        """获取指定股票过去 N 个季度的财务数据。

        用于预期差引擎的增长加速度计算。

        Args:
            ts_code: 股票代码。
            num_quarters: 回溯季度数，默认4个季度。

        Returns:
            按 end_date 降序排列的 FinancialData 列表。
        """
        results: list[FinancialData] = []
        now = datetime.now()
        year, month = now.year, now.month

        # 确定最近已结束的财报季度
        if month <= 4:
            base_quarters = [f"{year - 1}1231", f"{year - 1}0930", f"{year - 1}0630", f"{year - 1}0331"]
        elif month <= 8:
            base_quarters = [f"{year}0630", f"{year}0331", f"{year - 1}1231", f"{year - 1}0930"]
        elif month <= 10:
            base_quarters = [f"{year}0930", f"{year}0630", f"{year}0331", f"{year - 1}1231"]
        else:
            base_quarters = [f"{year}0930", f"{year}0630", f"{year}0331", f"{year - 1}1231"]

        quarters = base_quarters[:num_quarters]

        for end_date in quarters:
            # 尝试从 SQLite 缓存读取
            cached = self._cache.get_financial_cache(f"{ts_code}_{end_date}")
            if cached is not None and len(cached) > 0:
                results.append(FinancialData(**cached[0]))
                continue

            # API 获取
            cache_key = f"fina_indicator_q_{ts_code}_{end_date}"
            df = self._get_df_cached(
                "fina_indicator", cache_key,
                ts_code=ts_code, end_date=end_date,
                fields=(
                    "ts_code,end_date,roe,roic,gross_margin,"
                    "ocf_to_orp,debt_to_assets,profit_dedt,eps"
                ),
            )
            if len(df) > 0:
                row = df.iloc[0]
                fin = FinancialData(
                    ts_code=ts_code,
                    end_date=end_date,
                    revenue_yoy=safe_float(row.get("or_yoy")) if "or_yoy" in row.index else 0.0,
                    deducted_yoy=safe_float(row.get("q_deducted_profit_yoy")) if "q_deducted_profit_yoy" in row.index else 0.0,
                    net_profit_yoy=safe_float(row.get("q_profit_yoy")) if "q_profit_yoy" in row.index else 0.0,
                    gross_margin=safe_float(row.get("gross_margin")),
                    roe=safe_float(row.get("roe")),
                    roic=safe_float(row.get("roic")),
                )
                results.append(fin)
                self._cache.set_financial_cache(
                    f"{ts_code}_{end_date}", [fin.__dict__],
                )

        return results

    def get_industry_financial_benchmark(self, ts_code: str) -> dict[str, float]:
        """获取行业财务增长基准（行业平均/中位数增速）。

        通过同行业公司的财务数据计算行业增长基准。
        用于预期差引擎的代理预期模型。

        Args:
            ts_code: 股票代码。

        Returns:
            {
                "industry_median_growth": 0.0,   # 行业中位数净利润增速
                "industry_mean_growth": 0.0,     # 行业平均净利润增速
                "industry_median_revenue": 0.0,  # 行业中位数营收增速
                "industry_mean_revenue": 0.0,    # 行业平均营收增速
                "peer_count": 0,                  # 可比公司数
            }
        """
        result: dict[str, float] = {
            "industry_median_growth": 0.0,
            "industry_mean_growth": 0.0,
            "industry_median_revenue": 0.0,
            "industry_mean_revenue": 0.0,
            "peer_count": 0,
        }

        # 获取股票行业
        sb = self.get_stock_basic(ts_code)
        if sb is None or not sb.industry:
            return result

        # 从 stock_basic 获取同行业股票
        df_basic = self._load_stock_basic_csv()
        if df_basic is None:
            return result

        peers = df_basic[df_basic["industry"] == sb.industry]["ts_code"].head(20).tolist()
        if not peers:
            return result

        # 获取各同行业公司的财务数据
        profit_growths: list[float] = []
        revenue_growths: list[float] = []

        for peer_code in peers:
            if peer_code == ts_code:
                continue
            fin = self.get_financial(peer_code)
            if fin is None:
                continue
            if fin.deducted_yoy and abs(fin.deducted_yoy) < 500:  # 过滤异常值
                profit_growths.append(fin.deducted_yoy)
            if fin.revenue_yoy and abs(fin.revenue_yoy) < 500:
                revenue_growths.append(fin.revenue_yoy)

        if profit_growths:
            import statistics
            result["industry_median_growth"] = round(statistics.median(profit_growths), 2)
            result["industry_mean_growth"] = round(sum(profit_growths) / len(profit_growths), 2)
            result["peer_count"] = len(profit_growths)

        if revenue_growths:
            import statistics
            result["industry_median_revenue"] = round(statistics.median(revenue_growths), 2)
            result["industry_mean_revenue"] = round(sum(revenue_growths) / len(revenue_growths), 2)

        return result

    @property
    def pro(self) -> Any:
        """获取 tushare pro 实例。"""
        return self._pro

    @property
    def cache(self) -> EldCache:
        """获取缓存实例。"""
        return self._cache
