# -*- coding: utf-8 -*-
"""
SLI 数据获取层
封装 Tushare Pro 接口，缓存优先 + API 回退；限流/重试/分页/断点续跑/质量跟踪。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import pandas as pd
import tushare as ts

from .cache import SliCache
from .config import (
    API_RETRY,
    API_RETRY_DELAY,
    CACHE_EXPIRE_HOURS,
    CLS_CACHE_EXPIRE_HOURS,
    FIN_CACHE_EXPIRE_HOURS,
    RATE_LIMIT_MS,
)
from .utils import rate_limit

logger = logging.getLogger("sli.datasource")


class DataSource:
    """SLI 数据源。

    所有方法：先内存缓存 → parquet 缓存 → API，API 结果回写缓存。
    质量统计记录每个接口的调用次数 / 失败次数 / 行数，供数据质量报告使用。
    """

    def __init__(self, token: str, cache: SliCache) -> None:
        self._pro = ts.pro_api(token) if token else ts.pro_api()
        self._cache = cache
        # 质量统计
        self.quality: dict[str, dict[str, Any]] = {}

    # ── 质量跟踪 ──────────────────────────────────────

    def _qlog(self, api: str, rows: int, ok: bool = True, note: str = "") -> None:
        q = self.quality.setdefault(api, {"calls": 0, "fails": 0, "rows": 0, "note": ""})
        q["calls"] += 1
        if ok:
            q["rows"] += int(rows)
        else:
            q["fails"] += 1
            q["note"] = note[:200]

    # ── 基础 API 调用 ─────────────────────────────────

    def call(self, method: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        """限流 + 重试的 API 调用。失败返回 None，不抛异常。"""
        api_func = getattr(self._pro, method, None)
        if api_func is None:
            self._qlog(method, 0, False, f"未知API {method}")
            return None
        last_err: Optional[Exception] = None
        for attempt in range(1, API_RETRY + 1):
            try:
                with rate_limit(RATE_LIMIT_MS):
                    df = api_func(**kwargs)
                if df is not None and len(df) > 0:
                    return df
                # 空结果：合法但不重试
                return df
            except Exception as exc:
                last_err = exc
                logger.warning("API失败[%s] 尝试%d/%d 参数=%s: %s",
                               method, attempt, API_RETRY, str(kwargs)[:120], exc)
                if attempt < API_RETRY:
                    time.sleep(API_RETRY_DELAY * attempt)
        self._qlog(method, 0, False, f"最终失败: {last_err}")
        return None

    def _paginated(self, method: str, key_prefix: str, fields: str,
                   expire_hours: float, page_size: int = 10000, **kwargs: Any) -> pd.DataFrame:
        """全市场分页拉取（offset 分页），缓存优先。"""
        cache_key = f"{key_prefix}_{kwargs.get('period', '')}"
        cached = self._cache.load(cache_key, expire_hours)
        if cached is not None and len(cached) > 0:
            return cached
        parts: list[pd.DataFrame] = []
        offset = 0
        for _ in range(50):  # 防死循环
            kw = dict(kwargs)
            kw["offset"] = offset
            if fields:
                kw["fields"] = fields
            df = self.call(method, **kw)
            if df is None or len(df) == 0:
                break
            parts.append(df)
            if len(df) < page_size:
                break
            offset += len(df)
        if not parts:
            self._qlog(method, 0, True, "无数据")
            return pd.DataFrame()
        out = pd.concat(parts, ignore_index=True).drop_duplicates().reset_index(drop=True)
        self._qlog(method, len(out))
        self._cache.save(cache_key, out)
        return out

    # ── 行业分类 ──────────────────────────────────────

    def get_classify(self, level: str) -> pd.DataFrame:
        """申万行业分类 L1/L2/L3（SW2021）。"""
        cache_key = f"classify_SW2021_{level}"
        cached = self._cache.load(cache_key, CLS_CACHE_EXPIRE_HOURS)
        if cached is not None:
            return cached
        df = self.call("index_classify", src="SW2021", level=level)
        df = df if (df is not None and len(df)) else pd.DataFrame()
        if len(df):
            self._qlog("index_classify", len(df))
            self._cache.save(cache_key, df)
        return df

    def get_all_classify(self) -> pd.DataFrame:
        """合并 L1/L2/L3 分类，返回 L3 级行业表（含父子关系）。"""
        l3 = self.get_classify("L3")
        if l3.empty:
            return l3
        l2 = self.get_classify("L2")
        l1 = self.get_classify("L1")
        col_map = {"industry_code": "code", "industry_name": "name", "index_code": "index_code"}
        if l2.empty or l1.empty:
            return l3.rename(columns=col_map)
        l2m = l2.rename(columns=col_map)[["code", "name", "parent_code"]]
        l1m = l1.rename(columns=col_map)[["code", "name"]]
        l3m = l3.rename(columns=col_map)
        l3m = l3m.merge(l2m, left_on="parent_code", right_on="code", how="left",
                        suffixes=("", "_l2")).drop(columns=["code_l2"])
        l3m = l3m.merge(l1m, left_on="parent_code_l2" if "parent_code_l2" in l3m.columns else "parent_code",
                        right_on="code", how="left", suffixes=("", "_l1"))
        if "parent_code_l2" in l3m.columns:
            l3m["parent_code"] = l3m["parent_code_l2"]
        return l3m

    # ── 行业成分 ──────────────────────────────────────

    def get_members(self, index_codes: list[str], end_date: str) -> pd.DataFrame:
        """按申万三级行业取成分（index_member），含历史进出。

        返回列：index_code, l3_name, con_code, in_date, out_date, is_new
        """
        cache_key = f"members_{end_date}"
        cached = self._cache.load(cache_key, CLS_CACHE_EXPIRE_HOURS)
        if cached is not None:
            return cached
        parts: list[pd.DataFrame] = []
        for code in index_codes:
            df = self.call("index_member", index_code=code,
                           start_date="20000101", end_date=end_date)
            if df is None or len(df) == 0:
                continue
            parts.append(df)
        if not parts:
            return pd.DataFrame()
        out = pd.concat(parts, ignore_index=True)
        self._qlog("index_member", len(out))
        self._cache.save(cache_key, out)
        return out

    # ── 股票基本信息 ──────────────────────────────────

    def get_stock_basic(self) -> pd.DataFrame:
        cache_key = "stock_basic"
        cached = self._cache.load(cache_key, CLS_CACHE_EXPIRE_HOURS)
        if cached is not None:
            return cached
        df = self.call("stock_basic", fields="ts_code,name,industry,market,list_status,list_date")
        df = df if (df is not None and len(df)) else pd.DataFrame()
        if len(df):
            self._qlog("stock_basic", len(df))
            self._cache.save(cache_key, df)
        return df

    # ── 交易日历 ──────────────────────────────────────

    def get_trade_cal(self, start_date: str, end_date: str) -> pd.DataFrame:
        cache_key = f"trade_cal_{start_date}_{end_date}"
        cached = self._cache.load(cache_key, CLS_CACHE_EXPIRE_HOURS)
        if cached is not None:
            return cached
        df = self.call("trade_cal", exchange="SSE", start_date=start_date, end_date=end_date)
        df = df if (df is not None and len(df)) else pd.DataFrame()
        if len(df):
            self._qlog("trade_cal", len(df))
            self._cache.save(cache_key, df)
        return df

    # ── 日行情（按交易日批量） ────────────────────────

    def get_daily_dates(self, trade_dates: list[str]) -> pd.DataFrame:
        """按交易日批量获取全市场日线。返回 trade_date 升序。"""
        parts: list[pd.DataFrame] = []
        for d in trade_dates:
            cache_key = f"daily_{d}"
            cached = self._cache.load(cache_key, CACHE_EXPIRE_HOURS)
            if cached is not None:
                parts.append(cached)
                continue
            df = self.call("daily", trade_date=d,
                           fields="ts_code,trade_date,close,pre_close,pct_chg,vol,amount,high,low")
            if df is None or len(df) == 0:
                continue
            self._qlog("daily", len(df))
            self._cache.save(cache_key, df)
            parts.append(df)
        if not parts:
            return pd.DataFrame()
        out = pd.concat(parts, ignore_index=True)
        return out

    def get_daily_basic_dates(self, trade_dates: list[str]) -> pd.DataFrame:
        """按交易日批量获取 daily_basic（市值/估值/换手）。"""
        parts: list[pd.DataFrame] = []
        for d in trade_dates:
            cache_key = f"daily_basic_{d}"
            cached = self._cache.load(cache_key, CACHE_EXPIRE_HOURS)
            if cached is not None:
                parts.append(cached)
                continue
            df = self.call("daily_basic", trade_date=d,
                           fields="ts_code,trade_date,total_mv,circ_mv,pe_ttm,pb,turnover_rate,volume_ratio")
            if df is None or len(df) == 0:
                continue
            self._qlog("daily_basic", len(df))
            self._cache.save(cache_key, df)
            parts.append(df)
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, ignore_index=True)

    # ── 复权因子（回测收益计算用，防止除权失真） ──────────

    def get_adj_factor_dates(self, trade_dates: list[str]) -> pd.DataFrame:
        """按交易日批量获取全市场复权因子（adj_factor）。"""
        parts: list[pd.DataFrame] = []
        for d in trade_dates:
            cache_key = f"adj_factor_{d}"
            cached = self._cache.load(cache_key, CACHE_EXPIRE_HOURS)
            if cached is not None:
                parts.append(cached)
                continue
            df = self.call("adj_factor", trade_date=d,
                           fields="ts_code,trade_date,adj_factor")
            if df is None or len(df) == 0:
                continue
            self._qlog("adj_factor", len(df))
            self._cache.save(cache_key, df)
            parts.append(df)
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, ignore_index=True)

    # ── 指数日行情（基准：沪深300/中证1000） ────────────

    def get_index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取指数日线（非VIP）。缓存 key index_daily_{code}。"""
        cache_key = f"index_daily_{ts_code}"
        cached = self._cache.load(cache_key, CLS_CACHE_EXPIRE_HOURS)
        if cached is not None:
            return cached
        df = self.call("index_daily", ts_code=ts_code,
                       start_date=start_date, end_date=end_date,
                       fields="ts_code,trade_date,close")
        df = df if (df is not None and len(df)) else pd.DataFrame()
        if len(df):
            self._qlog("index_daily", len(df))
            self._cache.save(cache_key, df)
        return df

    # ── 财务指标（VIP，按报告期全市场） ────────────────

    FINA_FIELDS = (
        "ts_code,end_date,ann_date,update_flag,roe,roic,grossprofit_margin,"
        "netprofit_margin,or_yoy,ocf_to_profit,rd_exp,"
        "q_profit_yoy,dt_netprofit_yoy,netprofit_yoy,roe_dt"
    )

    def get_fina_indicator(self, periods: list[str]) -> pd.DataFrame:
        parts = [self._paginated("fina_indicator_vip", "fina_ind", self.FINA_FIELDS,
                                 FIN_CACHE_EXPIRE_HOURS, period=p)
                 for p in periods]
        parts = [p for p in parts if len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # ── 利润表（营收/毛利/归母净利） ────────────────────

    INCOME_FIELDS = (
        "ts_code,end_date,ann_date,update_flag,revenue,operate_cost,"
        "total_cogs,n_income_attr_p"
    )

    def get_income(self, periods: list[str]) -> pd.DataFrame:
        parts = [self._paginated("income_vip", "income", self.INCOME_FIELDS,
                                 FIN_CACHE_EXPIRE_HOURS, period=p)
                 for p in periods]
        parts = [p for p in parts if len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # ── 资产负债表（总资产） ───────────────────────────

    BALANCE_FIELDS = "ts_code,end_date,ann_date,update_flag,total_assets"

    def get_balance(self, periods: list[str]) -> pd.DataFrame:
        parts = [self._paginated("balancesheet_vip", "balance", self.BALANCE_FIELDS,
                                 FIN_CACHE_EXPIRE_HOURS, period=p)
                 for p in periods]
        parts = [p for p in parts if len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # ── 主营业务构成（产品级，用于行业纯度） ────────────

    MAINBZ_FIELDS = "ts_code,end_date,bz_item,bz_sales,bz_profit,bz_cost"

    def get_mainbz(self, periods: list[str]) -> pd.DataFrame:
        parts: list[pd.DataFrame] = []
        for p in periods:
            df = self._paginated("fina_mainbz_vip", "mainbz", self.MAINBZ_FIELDS,
                                 FIN_CACHE_EXPIRE_HOURS, period=p, type="P")
            if len(df):
                parts.append(df)
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
