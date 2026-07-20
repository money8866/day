"""
数据获取模块 - Tushare API封装与缓存管理
"""
import os
import json
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable
import pandas as pd
import loguru

logger = loguru.logger


def get_cache_dir(config: Dict) -> Path:
    """获取缓存目录"""
    cache_dir = Path(config.get('cache', {}).get('dir', 'cache'))
    if not cache_dir.is_absolute():
        cache_dir = Path(__file__).parent / cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cache_path(cache_dir: Path, key: str) -> Path:
    """获取缓存文件路径"""
    return cache_dir / f"{key}.parquet"


def load_cache(cache_dir: Path, key: str, expire_hours: int = 24) -> Optional[pd.DataFrame]:
    """加载缓存数据

    优先读取parquet(快)，其次csv(兼容旧缓存)
    """
    cache_path = get_cache_path(cache_dir, key)  # .parquet
    csv_path = cache_path.with_suffix('.csv')

    # 检查 parquet
    if cache_path.exists():
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=expire_hours):
            return None
        try:
            df = pd.read_parquet(cache_path)
            return df
        except Exception:
            pass

    # 回退: 检查 csv(旧缓存)
    if csv_path.exists():
        mtime = datetime.fromtimestamp(csv_path.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=expire_hours):
            return None
        try:
            df = pd.read_csv(csv_path)
            # CSV读回可能丢失类型信息: 日期列变int64, 代码列变其他类型
            # 需转回来避免 .str accessor 报错
            for col in df.columns:
                if col in ['ts_code', 'ann_date', 'f_ann_date', 'end_date',
                           'report_date', 'end_date', 'update_flag',
                           'industry', 'industry_class', 'list_date']:
                    df[col] = df[col].astype(str).str.replace(r'\.0$', '', regex=True)
                    # NaN会变成字符串' nan '，需要处理
                    df.loc[df[col].str.lower().isin(['nan', 'nat', 'none']), col] = ''
                elif 'date' in col.lower():
                    df[col] = df[col].apply(lambda x: str(int(x)) if pd.notna(x) and str(x).replace('.','').replace('-','').isdigit() else str(x) if pd.notna(x) else '')
            # 升级为parquet加速后续读取
            try:
                df.to_parquet(cache_path, index=False)
            except Exception:
                pass
            return df
        except Exception:
            return None

    return None


def save_cache(df: pd.DataFrame, cache_dir: Path, key: str) -> None:
    """保存数据到缓存"""
    cache_path = get_cache_path(cache_dir, key)
    try:
        df.to_parquet(cache_path, index=False)
    except Exception:
        # 回退到 CSV
        csv_path = cache_path.with_suffix('.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    logger.debug(f"缓存已保存: {key}")


class DataFetcher:
    """Tushare数据获取器"""

    def __init__(self, token: str, config: Dict):
        import tushare as ts
        import threading
        self.pro = ts.pro_api(token)
        self.config = config
        self.cache_config = config.get('cache', {})
        self.cache_enabled = self.cache_config.get('enabled', True)
        self.cache_dir = get_cache_dir(config)
        self.expire_hours = self.cache_config.get('expire_hours', 24)
        self.max_retry = config.get('tushare', {}).get('max_retry', 3)
        self.retry_delay = config.get('tushare', {}).get('retry_delay', 5)
        # 精确速率控制: 300 次/分钟 = 每 220ms 一次 (留 80ms 安全裕度)
        self._min_interval = 0.22
        self._last_request_time = time.time()
        self._lock = threading.Lock()  # 多线程共享速率锁

    def _rate_limit(self) -> None:
        """线程安全的频率控制（严格 500次/分钟）"""
        with self._lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.time()

    def _retry_call(self, func: Callable, *args, **kwargs) -> Any:
        """带重试的API调用（遇到频率超限会多等一会儿）"""
        last_error = None
        for attempt in range(self.max_retry):
            try:
                self._rate_limit()
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                last_error = e
                msg = str(e)
                # 识别频率超限错误，额外等待以恢复
                if '频率' in msg or '频率超限' in msg or 'frequency' in msg.lower():
                    wait = 3.0 + attempt * 2.0
                    logger.warning(f"频率超限, 等待 {wait:.0f}s 后重试({attempt+1}/{self.max_retry})")
                    time.sleep(wait)
                else:
                    logger.warning(f"API调用失败(尝试{attempt+1}/{self.max_retry}): {e}")
                    time.sleep(1.0)

        logger.error(f"API调用最终失败: {last_error}")
        raise last_error

    def get_stock_list(self, list_status: str = "L") -> pd.DataFrame:
        """
        获取股票列表

        Args:
            list_status: L=上市, D=退市, P=暂停

        Returns:
            股票列表DataFrame
        """
        cache_key = f"stock_list_{list_status}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        df = self._retry_call(self.pro.stock_basic, exchange='', list_status=list_status,
                              fields='ts_code,symbol,name,area,industry,list_date,list_status')

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df

    def get_daily(self, trade_date: str) -> pd.DataFrame:
        """
        获取日线行情，如该日期无数据则向前查找最近有数据的交易日
        """
        from datetime import datetime, timedelta
        date_obj = datetime.strptime(trade_date, '%Y%m%d')

        for offset in range(10):  # 最多向前找10天
            check_date = (date_obj - timedelta(days=offset)).strftime('%Y%m%d')
            cache_key = f"daily_{check_date}"
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None and len(cached) > 0:
                    return cached

            df = self._retry_call(self.pro.daily, trade_date=check_date)
            if df is not None and len(df) > 0:
                if self.cache_enabled:
                    save_cache(df, self.cache_dir, cache_key)
                return df
            time.sleep(0.15)

        return pd.DataFrame()

    def get_daily_history(self, end_date: str, days: int = 120) -> pd.DataFrame:
        """
        获取历史日线行情（多日合并）

        Args:
            end_date: 截止日期 YYYYMMDD
            days: 需要的交易日天数（默认120天）

        Returns:
            多日合并的日线 DataFrame, 含 trade_date 字段
        """
        from datetime import datetime, timedelta
        import time

        date_obj = datetime.strptime(end_date, '%Y%m%d')
        all_data = []
        collected = 0
        offset = 0
        max_lookback = days * 3  # 最多向前查找天数, 留足周末和假期

        while collected < days and offset < max_lookback:
            check_date = (date_obj - timedelta(days=offset)).strftime('%Y%m%d')
            cache_key = f"daily_{check_date}"

            df = None
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None and len(cached) > 0:
                    df = cached

            if df is None:
                df = self._retry_call(self.pro.daily, trade_date=check_date)
                if df is not None and len(df) > 0 and self.cache_enabled:
                    save_cache(df, self.cache_dir, cache_key)
                time.sleep(0.12)  # 限速

            if df is not None and len(df) > 0:
                all_data.append(df)
                collected += 1

            offset += 1

        if len(all_data) > 0:
            result = pd.concat(all_data, ignore_index=True)
            return result
        return pd.DataFrame()

    def get_income(self, ts_code: str, start_year: str = None, end_year: str = None) -> pd.DataFrame:
        """
        获取利润表数据(使用真实的Tushare字段名)
        """
        cache_key = f"income_{ts_code}_{start_year}_{end_year}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        # 使用更全的字段(包含 rd_exp, n_income, total_cogs 等关键财务指标)
        fields = 'ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,end_type,basic_eps,diluted_eps,total_revenue,revenue,n_income,n_income_attr_p,rd_exp,total_profit,total_cogs,operate_profit,oper_exp'
        df = self._retry_call(self.pro.income, ts_code=ts_code, start_year=start_year, end_year=end_year, fields=fields)

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df if df is not None else pd.DataFrame()

    def get_balance_sheet(self, ts_code: str, start_year: str = None, end_year: str = None) -> pd.DataFrame:
        """
        获取资产负债表(使用真实的Tushare字段名)
        """
        cache_key = f"balance_{ts_code}_{start_year}_{end_year}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        # 关键字段: inventories(存货)、fix_assets(固定资产)、total_hldr_eqy_exc_min_int(股东权益)、total_assets
        # 新增需求链指标: contract_liability(合同负债)、advance_payment(预付款)
        fields = 'ts_code,ann_date,f_ann_date,end_date,report_type,total_assets,total_current_assets,inventories,fix_assets,total_liability,total_hldr_eqy_exc_min_int,total_hldr_eqy_inc_min_int,contract_liability,advance_payment,operating_liability,operating_asset'
        df = self._retry_call(self.pro.balancesheet, ts_code=ts_code, start_year=start_year, end_year=end_year, fields=fields)

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df if df is not None else pd.DataFrame()

    def get_cashflow(self, ts_code: str, start_year: str = None, end_year: str = None) -> pd.DataFrame:
        """
        获取现金流量表

        Args:
            ts_code: 股票代码
            start_year: 开始年份 YYYY
            end_year: 结束年份 YYYY

        Returns:
            现金流量表DataFrame
        """
        cache_key = f"cashflow_{ts_code}_{start_year}_{end_year}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        df = self._retry_call(self.pro.cashflow, ts_code=ts_code, start_year=start_year, end_year=end_year,
                              fields='ts_code,ann_date,f_ann_date,end_date,report_type,net_operate_cash_flow,net_invest_cash_flow,payment_for_assets,cap_expend_ra')

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df if df is not None else pd.DataFrame()

    def get_forecast(self, ts_code: str) -> pd.DataFrame:
        """
        获取业绩预告

        Args:
            ts_code: 股票代码

        Returns:
            业绩预告DataFrame
        """
        cache_key = f"forecast_{ts_code}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        df = self._retry_call(self.pro.forecast, ts_code=ts_code,
                              fields='ts_code,ann_date,end_date,type,period,profit_change,profit_ratio,'
                                     'p_change_min,p_change_max,net_profit_min,net_profit_max,'
                                     'last_parent_net,summary,update_flag')

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df if df is not None else pd.DataFrame()

    def get_forecast_vip(self, period: str) -> pd.DataFrame:
        """
        获取全量业绩预告 (v3.3 升级: 用 forecast_vip 替代逐只 forecast)

        Args:
            period: 报告期, 如 '20260630' 表示2026年中报

        Returns:
            全量业绩预告 DataFrame, 包含字段:
            ts_code, ann_date, end_date, type, period,
            profit_change, p_change_min, p_change_max,
            net_profit_min, net_profit_max, last_parent_net,
            summary, update_flag
        """
        cache_key = f"forecast_vip_{period}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        df = self._retry_call(self.pro.forecast_vip, period=period,
                              fields='ts_code,ann_date,end_date,type,period,profit_change,'
                                     'p_change_min,p_change_max,net_profit_min,net_profit_max,'
                                     'last_parent_net,summary,update_flag')

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df if df is not None else pd.DataFrame()

    def get_express_vip(self, period: str) -> pd.DataFrame:
        """
        获取全量业绩快报 (v3.3 新增: 业绩快报是比财报更及时的数据源)

        Args:
            period: 报告期, 如 '20260630' 表示2026年中报

        Returns:
            全量业绩快报 DataFrame, 包含字段:
            ts_code, ann_date, end_date, revenue, operate_profit,
            total_profit, n_income, total_assets, diluted_eps,
            reason, yoy_net_profit, yoy_eps, yoy_revenue, perf_summary
        """
        cache_key = f"express_vip_{period}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        df = self._retry_call(self.pro.express_vip, period=period,
                              fields='ts_code,ann_date,end_date,revenue,operate_profit,'
                                     'total_profit,n_income,total_assets,diluted_eps,'
                                     'reason,yoy_net_profit,yoy_eps,yoy_revenue,perf_summary')

        if self.cache_enabled and df is not None and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)

        return df if df is not None else pd.DataFrame()

    def get_moneyflow(self, trade_date: str) -> pd.DataFrame:
        """
        获取资金流向，如该日期无数据则向前查找最近有数据的交易日
        """
        from datetime import datetime, timedelta
        date_obj = datetime.strptime(trade_date, '%Y%m%d')

        for offset in range(10):
            check_date = (date_obj - timedelta(days=offset)).strftime('%Y%m%d')
            cache_key = f"moneyflow_{check_date}"
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None and len(cached) > 0:
                    return cached

            df = self._retry_call(self.pro.moneyflow, trade_date=check_date)
            if df is not None and len(df) > 0:
                if self.cache_enabled:
                    save_cache(df, self.cache_dir, cache_key)
                return df
            time.sleep(0.15)

        return pd.DataFrame()

    def get_north_hold(self, trade_date: str) -> pd.DataFrame:
        """
        获取北向资金持股数据（使用 pro.hk_hold 接口）
        
        Returns: DataFrame with columns: ts_code, trade_date, hold_ratio(%)
        """
        from datetime import datetime, timedelta
        date_obj = datetime.strptime(trade_date, '%Y%m%d')

        for offset in range(120):
            check_date = (date_obj - timedelta(days=offset)).strftime('%Y%m%d')
            cache_key = f"north_hold_{check_date}"
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None and len(cached) > 0:
                    return cached

            try:
                df = self._retry_call(self.pro.hk_hold, trade_date=check_date,
                                      fields='ts_code,trade_date,ratio')
                if df is not None and len(df) > 0:
                    df = df.rename(columns={'ratio': 'hold_ratio'})
                    df = df[df['ts_code'].str.endswith(('.SH', '.SZ'))]
                    if len(df) > 0:
                        if self.cache_enabled:
                            save_cache(df, self.cache_dir, cache_key)
                        return df
            except Exception as e:
                pass
            time.sleep(0.15)

        return pd.DataFrame()

    def get_holder_trade(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取股东增减持数据（含社保等机构）
        
        Returns: DataFrame with holder trade information
        """
        cache_key = f"holder_trade_{self._safe_name(ts_code)}_{start_date}_{end_date}"
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None and len(cached) > 0:
                return cached

        try:
            df = self._retry_call(self.pro.stk_holdertrade, ts_code=ts_code,
                                  start_date=start_date, end_date=end_date)
            if df is not None and len(df) > 0:
                if self.cache_enabled:
                    save_cache(df, self.cache_dir, cache_key)
                return df
        except Exception:
            pass

        return pd.DataFrame()

    def get_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """
        获取每日基本面数据（含总市值/流通市值），如该日期无数据则向前查找

        Returns: DataFrame with columns: ts_code, trade_date, close, total_mv, circ_mv, pe, pb
        """
        from datetime import datetime, timedelta
        date_obj = datetime.strptime(trade_date, '%Y%m%d')

        for offset in range(10):
            check_date = (date_obj - timedelta(days=offset)).strftime('%Y%m%d')
            cache_key = f"daily_basic_{check_date}"
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None and len(cached) > 0:
                    return cached

            df = self._retry_call(self.pro.daily_basic, trade_date=check_date,
                                  fields='ts_code,trade_date,close,total_mv,circ_mv,turnover_rate,volume_ratio,pe,pe_ttm,pb')
            if df is not None and len(df) > 0:
                if self.cache_enabled:
                    save_cache(df, self.cache_dir, cache_key)
                return df
            time.sleep(0.15)

        return pd.DataFrame()

    def get_stk_industry(self, ts_code: str = None) -> pd.DataFrame:
        """
        获取行业分类

        Args:
            ts_code: 股票代码(可选,不传则获取全市场)

        Returns:
            行业分类DataFrame
        """
        if ts_code:
            cache_key = f"industry_{ts_code}"
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None:
                    return cached

            df = self._retry_call(self.pro.stk_industry, ts_code=ts_code)
            if self.cache_enabled and df is not None and len(df) > 0:
                save_cache(df, self.cache_dir, cache_key)
            return df if df is not None else pd.DataFrame()
        else:
            # 获取所有行业分类
            cache_key = "industry_all"
            if self.cache_enabled:
                cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
                if cached is not None:
                    return cached

            # 批量获取(需要遍历)
            all_industries = []
            stocks = self.get_stock_list()
            for _, row in stocks.iterrows():
                try:
                    df = self.pro.stk_industry(ts_code=row['ts_code'])
                    if df is not None and len(df) > 0:
                        df['ts_code'] = row['ts_code']
                        all_industries.append(df)
                except:
                    pass

            if all_industries:
                result = pd.concat(all_industries, ignore_index=True)
            else:
                result = pd.DataFrame()

            if self.cache_enabled and len(result) > 0:
                save_cache(result, self.cache_dir, cache_key)
            return result

    def get_last_trade_date(self) -> str:
        """获取最近可用交易日

        逻辑:
        - 如果当前时间 < 16:00, 今天数据尚未收盘, 直接使用 Tushare 返回的前一交易日(pretrade_date)
        - 如果当前时间 >= 16:00, 使用今天(若今天是交易日)或最近的上一交易日
        """
        now = datetime.now()
        today = now.strftime('%Y%m%d')

        # 获取今天及最近7天的日历数据, 包含 pretrade_date
        start = (now - timedelta(days=7)).strftime('%Y%m%d')
        try:
            df = self._retry_call(self.pro.trade_cal, exchange='SSE', start_date=start, end_date=today)
            if df is not None and len(df) > 0:
                # 找到今天的记录
                today_row = df[df['cal_date'] == today]
                if len(today_row) > 0:
                    today_is_open = today_row.iloc[0]['is_open'] == 1
                    pretrade_date = today_row.iloc[0]['pretrade_date']

                    if now.hour < 16:
                        # 收盘前: 无论今天是否开盘, 都用前一交易日
                        logger.info(f"当前时间 {now.strftime('%H:%M')} < 16:00, 使用前一交易日: {pretrade_date}")
                        return str(pretrade_date)
                    elif today_is_open:
                        # 收盘后且今天是交易日: 用今天
                        logger.info(f"当前时间 {now.strftime('%H:%M')} >= 16:00, 今天是交易日, 使用: {today}")
                        return today
                    else:
                        # 收盘后但今天非交易日: 用前一交易日
                        logger.info(f"今天非交易日({now.strftime('%H:%M')}), 使用前一交易日: {pretrade_date}")
                        return str(pretrade_date)

                # 没找到今天记录, 在返回范围内找最近的交易日
                trading_days = df[df['is_open'] == 1]
                if len(trading_days) > 0:
                    latest = sorted(trading_days['cal_date'].tolist())[-1]
                    if now.hour < 16 and latest == today:
                        # 跳过今天(未收盘), 用前一个
                        filtered = [d for d in sorted(trading_days['cal_date'].tolist()) if d != today]
                        if filtered:
                            return str(filtered[-1])
                    return str(latest)
        except Exception as e:
            logger.warning(f"交易日历查询异常: {e}")

        # 兜底: 直接返回今天
        logger.warning(f"交易日历查询失败, 回退使用: {today}")
        return today

    def batch_get_financial_data(self, ts_codes: List[str], data_type: str = 'income',
                                    max_workers: int = 8) -> Dict[str, pd.DataFrame]:
        """
        并发批量获取财务数据

        Args:
            ts_codes: 股票代码列表
            data_type: 数据类型 income/balance/cashflow
            max_workers: 并发线程数

        Returns:
            dict {ts_code: DataFrame}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}

        def fetch_single(code: str) -> tuple:
            try:
                if data_type == 'income':
                    df = self.get_income(code)
                elif data_type == 'balance':
                    df = self.get_balance_sheet(code)
                else:
                    df = self.get_cashflow(code)
                return (code, df)
            except Exception as e:
                return (code, pd.DataFrame())

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_single, code): code for code in ts_codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    code, df = future.result()
                    results[code] = df
                except Exception:
                    results[code] = pd.DataFrame()

        return results

    def get_stock_financial_batch(self, ts_codes: List[str], start_year: str,
                                    max_workers: int = 10,
                                    forecast_vip_df: pd.DataFrame = None,
                                    express_vip_df: pd.DataFrame = None) -> Dict[str, Dict]:
        """
        批量获取单只股票的三类财务数据(income + balance + cashflow)

        v3.3 升级: 业绩预告/快报改用全量VIP接口,移除逐只 forecast 调用
        每只股票内部串行请求三类数据,股票之间并发

        Args:
            forecast_vip_df: 全量业绩预告 DataFrame(来自 get_forecast_vip)
            express_vip_df: 全量业绩快报 DataFrame(来自 get_express_vip)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}

        def fetch_all(code: str) -> tuple:
            income_df = self.get_income(code, start_year=start_year)
            balance_df = self.get_balance_sheet(code, start_year=start_year)
            cashflow_df = self.get_cashflow(code, start_year=start_year)
            mainbz_list = self.get_fina_mainbz(code)
            return (code, {
                'income': income_df if income_df is not None else pd.DataFrame(),
                'balance': balance_df if balance_df is not None else pd.DataFrame(),
                'cashflow': cashflow_df if cashflow_df is not None else pd.DataFrame(),
                'mainbz': mainbz_list if mainbz_list else [],
                'years_available': 3
            })

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_all, code): code for code in ts_codes}
            done_count = 0
            total = len(ts_codes)
            for future in as_completed(futures):
                done_count += 1
                try:
                    code, data = future.result()
                    results[code] = data
                except Exception:
                    pass

        # v3.3: 将全量VIP数据按ts_code分发到各股票
        if forecast_vip_df is not None and len(forecast_vip_df) > 0:
            for code in results:
                per_stock = forecast_vip_df[forecast_vip_df['ts_code'] == code]
                results[code]['forecast'] = per_stock if len(per_stock) > 0 else pd.DataFrame()

        if express_vip_df is not None and len(express_vip_df) > 0:
            for code in results:
                per_stock = express_vip_df[express_vip_df['ts_code'] == code]
                results[code]['express'] = per_stock if len(per_stock) > 0 else pd.DataFrame()

        return results

    def get_report_rc_batch(self, stock_list: List[str] = None,
                            force_refresh: bool = False,
                            cache_days: int = 7) -> Dict[str, Dict]:
        """
        获取卖方盈利预测数据（report_rc 接口），批量构建每只股票的一致性指标。

        策略（增量刷新模式）:
        - 按 ts_code 逐只拉取全量历史研报（不再按 ann_date 扫日期）
        - 每只股票缓存到 cache/report_rc_{ts_code}.parquet，有效期 cache_days 天
        - 空结果（0条研报）也会缓存（zero-row DataFrame），避免重复 API 调用
        - 汇总层缓存在 report_rc_all_{YYYYMMDD}.parquet，当天命中直接返回
        - 增量模式：只刷新超过 cache_days 未查过 / 之前无数据但可能新获得分析师覆盖的股票
        - force_refresh=True 时强制全量重建（忽略所有缓存）

        参数:
            stock_list: 需要拉取研报的股票列表
            force_refresh: True 时忽略所有缓存，重新拉取 stock_list 全部股票
            cache_days: 单股缓存有效天数（默认7天，即每周仅需刷新一次）

        返回 Dict:
            { ts_code -> {
                analyst_count / avg_eps_current_year / avg_eps_next_year /
                avg_np_current_year / avg_np_next_year / np_growth_current /
                eps_growth_next / buy_ratio / rating_sentiment /
                analyst_revision_30d / latest_report_date
            } }
        """
        import json as _json
        from datetime import datetime as dt2, timedelta

        today = dt2.now()
        today_str = today.strftime('%Y%m%d')
        cache_dir = self.cache_dir
        meta_path = cache_dir / 'report_rc_meta.json' if hasattr(cache_dir, '__truediv__') else None

        # 读取/维护元信息文件：记录每只股票 last_query / analyst_count / has_data
        meta = {}
        if meta_path is None:
            try:
                meta_path = Path(str(cache_dir)) / 'report_rc_meta.json'
            except Exception:
                meta_path = None
        try:
            if meta_path and os.path.exists(str(meta_path)):
                with open(str(meta_path), 'r', encoding='utf-8') as fh:
                    meta = _json.load(fh)
        except Exception:
            meta = {}

        # ── 阶段0：初始化股票代码列表 ──
        all_stock_codes = stock_list if stock_list else []

        # ── 阶段1：汇总层缓存命中（需覆盖 70% 以上请求股票）──
        if not force_refresh and self.cache_enabled:
            summary_cache_key = f"report_rc_all_{today_str}"
            cached = load_cache(cache_dir, summary_cache_key, self.expire_hours)
            if cached is not None and len(cached) > 0:
                cached_codes = set(cached['ts_code'].unique())
                requested = set(all_stock_codes)
                coverage = len(cached_codes & requested) / len(requested) if requested else 0
                if coverage >= 0.7:
                    logger.info(f"卖方盈利预测汇总缓存命中: {len(cached_codes)} 只 (覆盖 {coverage*100:.0f}%)")
                    return self._build_report_rc_consensus(cached)
                else:
                    logger.info(f"卖方盈利预测汇总缓存覆盖不足({coverage*100:.0f}%)，转入增量刷新")
            for d in range(1, 8):
                past = (today - timedelta(days=d)).strftime('%Y%m%d')
                cached2 = load_cache(cache_dir, f"report_rc_all_{past}", self.expire_hours + 24 * d)
                if cached2 is not None and len(cached2) > 0:
                    cached2_codes = set(cached2['ts_code'].unique())
                    requested = set(all_stock_codes)
                    coverage2 = len(cached2_codes & requested) / len(requested) if requested else 0
                    if coverage2 >= 0.7:
                        logger.info(f"卖方盈利预测汇总缓存回退({past}): {len(cached2_codes)} 只 (覆盖 {coverage2*100:.0f}%)")
                        return self._build_report_rc_consensus(cached2)

        # ── 阶段2：逐只识别（增量刷新 + 空结果缓存）──
        existing = {}       # ts_code -> DataFrame（有效缓存命中）
        no_data_stocks = set()  # 已确认无研报且在有效期内的股票
        to_query = []

        if force_refresh:
            to_query = list(all_stock_codes)
            logger.info(f"卖方盈利预测[FORCE]: 强制全量刷新 {len(to_query)} 只")
        else:
            for sc in all_stock_codes:
                safe_name = sc.replace('.', '_')
                key = f"report_rc_{safe_name}"
                # 先查单股 parquet 缓存（7天过期）
                single_cache = load_cache(cache_dir, key, 24 * cache_days)
                if single_cache is not None and len(single_cache) > 0:
                    existing[sc] = single_cache
                    continue
                # 空结果缓存（zero-row parquet 也可被 load_cache 读取）
                empty_cache = load_cache(cache_dir, f"report_rc_empty_{safe_name}", 24 * cache_days)
                if empty_cache is not None:
                    no_data_stocks.add(sc)
                    continue
                to_query.append(sc)

            logger.info(f"卖方盈利预测[增量]: 已有缓存 {len(existing)} 只, 空缓存跳过 {len(no_data_stocks)} 只, 需API {len(to_query)} 只")

        # ── 阶段3：增量拉取（限速 220ms / 次，300次/分钟上限）──
        fetched_frames = []
        newly_empty = []
        if to_query:
            batch_size = 100
            total_batches = (len(to_query) + batch_size - 1) // batch_size
            t0 = time.time()
            for batch_idx in range(total_batches):
                batch = to_query[batch_idx * batch_size:(batch_idx + 1) * batch_size]
                batch_t0 = time.time()
                for ts_code in batch:
                    try:
                        df = self._retry_call(self.pro.report_rc, ts_code=ts_code)
                        if df is not None and len(df) > 0:
                            fetched_frames.append(df)
                            # 缓存单股结果
                            if self.cache_enabled:
                                safe_name = ts_code.replace('.', '_')
                                save_cache(df, cache_dir, f"report_rc_{safe_name}")
                            meta[ts_code] = {
                                'last_query': today_str,
                                'has_data': True,
                                'report_count': int(len(df)),
                            }
                        else:
                            # 无研报记录：缓存空结果标记
                            newly_empty.append(ts_code)
                            if self.cache_enabled:
                                safe_name = ts_code.replace('.', '_')
                                # 写一个只有列名的零行 DataFrame 作为空结果标记
                                empty_df = pd.DataFrame(columns=['ts_code', 'report_date', 'org_name', 'quarter'])
                                save_cache(empty_df, cache_dir, f"report_rc_empty_{safe_name}")
                            meta[ts_code] = {
                                'last_query': today_str,
                                'has_data': False,
                                'report_count': 0,
                            }
                    except Exception:
                        pass

                # 批次进度日志 + ETA
                if batch_idx % 1 == 0 or batch_idx == total_batches - 1:
                    elapsed = time.time() - t0
                    done = (batch_idx + 1) * batch_size
                    rate = done / elapsed if elapsed > 0 else 0
                    remain_sec = (len(to_query) - done) / rate if rate > 0 else 0
                    eta_min = remain_sec / 60.0
                    fetched_hits = sum(1 for d in fetched_frames)
                    logger.info(f"  [{batch_idx+1}/{total_batches}] 已查询 {done}/{len(to_query)}, "
                                f"有研报 {fetched_hits} 只, 速率 {rate:.0f} 股/秒, "
                                f"预计剩余 {eta_min:.1f} 分钟")

            # 写入元信息
            try:
                if meta_path and self.cache_enabled:
                    os.makedirs(os.path.dirname(str(meta_path)), exist_ok=True)
                    with open(str(meta_path), 'w', encoding='utf-8') as fh:
                        _json.dump(meta, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass

        # ── 合并：已有缓存 + 刚拉到 ──
        raw_frames = list(existing.values()) + fetched_frames
        if not raw_frames:
            logger.warning("卖方盈利预测: 所有股票均无研报数据")
            return {}

        raw_df = pd.concat(raw_frames, ignore_index=True)
        raw_df = raw_df.drop_duplicates(subset=['ts_code', 'report_date', 'org_name', 'quarter'])

        summary_cache_key = f"report_rc_all_{today_str}"
        if self.cache_enabled:
            save_cache(raw_df, cache_dir, summary_cache_key)

        logger.info(f"卖方盈利预测: 共 {len(raw_df)} 条研报记录, {raw_df['ts_code'].nunique()} 只有效股票")
        return self._build_report_rc_consensus(raw_df)

    def _build_report_rc_consensus(self, df: pd.DataFrame) -> Dict[str, Dict]:
        """统一口径转换层：把原始 report_rc DataFrame 变成 ts_code -> 一致性指标 dict。"""
        if df is None or len(df) == 0:
            return {}

        from datetime import datetime as dt2, timedelta
        cur_year = dt2.now().year
        cur_q4 = f"{cur_year}Q4"
        next_q4 = f"{cur_year + 1}Q4"
        # 近30天的 cutoff（用 report_date 替代可能缺失的 ann_date）
        cutoff_30d = (dt2.now() - timedelta(days=30)).strftime('%Y%m%d')
        # 只取近2年内的研报（避免极旧数据干扰）
        cutoff_2y = (dt2.now() - timedelta(days=730)).strftime('%Y%m%d')

        # 统一日期字段：优先 ann_date，回退 report_date
        if 'ann_date' in df.columns:
            df['_date_for_filter'] = df['ann_date'].astype(str)
        elif 'report_date' in df.columns:
            df['_date_for_filter'] = df['report_date'].astype(str)
        else:
            df['_date_for_filter'] = ''

        # 过滤2年旧数据
        df = df[df['_date_for_filter'] >= cutoff_2y].copy()

        rating_map = {'买入': 3, '增持': 2, '推荐': 2, '中性': 1, '持有': 1, '减持': 0, '卖出': 0, '无': 0}
        df['_rating_score'] = df['rating'].map(lambda x: rating_map.get(x, 0) if pd.notna(x) else 0)
        df['_is_buy'] = df['rating'].isin(['买入', '增持', '推荐']).astype(int)

        result = {}
        for ts_code, g in df.groupby('ts_code'):
            analyst_count = g['org_name'].nunique()
            g_cur = g[g['quarter'] == cur_q4]
            g_next = g[g['quarter'] == next_q4]

            avg_eps_cur = g_cur['eps'].dropna().mean() if len(g_cur['eps'].dropna()) > 0 else 0.0
            avg_eps_next = g_next['eps'].dropna().mean() if len(g_next['eps'].dropna()) > 0 else 0.0
            avg_np_cur = g_cur['np'].dropna().mean() if len(g_cur['np'].dropna()) > 0 else 0.0
            avg_np_next = g_next['np'].dropna().mean() if len(g_next['np'].dropna()) > 0 else 0.0

            np_growth = (avg_np_next / avg_np_cur - 1.0) if avg_np_cur > 0 else 0.0
            eps_growth = (avg_eps_next / avg_eps_cur - 1.0) if avg_eps_cur > 0 else 0.0

            buy_ratio = g['_is_buy'].mean() if len(g) > 0 else 0.0
            rating_sentiment = g['_rating_score'].mean() if len(g) > 0 else 0.0

            g_recent = g[g['_date_for_filter'] >= cutoff_30d]
            g_older = g[g['_date_for_filter'] < cutoff_30d]
            revision = 0.0
            if len(g_recent) > 0 and len(g_older) > 0:
                recent_np = g_recent['np'].dropna().mean()
                older_np = g_older['np'].dropna().mean()
                if older_np and older_np > 0:
                    revision = (recent_np - older_np) / older_np

            latest_date = g['_date_for_filter'].max()
            result[ts_code] = {
                'analyst_count': int(analyst_count),
                'avg_eps_current_year': float(avg_eps_cur) if pd.notna(avg_eps_cur) else 0.0,
                'avg_eps_next_year': float(avg_eps_next) if pd.notna(avg_eps_next) else 0.0,
                'avg_np_current_year': float(avg_np_cur) if pd.notna(avg_np_cur) else 0.0,
                'avg_np_next_year': float(avg_np_next) if pd.notna(avg_np_next) else 0.0,
                'np_growth_current': float(np_growth) if pd.notna(np_growth) else 0.0,
                'eps_growth_next': float(eps_growth) if pd.notna(eps_growth) else 0.0,
                'buy_ratio': float(buy_ratio) if pd.notna(buy_ratio) else 0.0,
                'rating_sentiment': float(rating_sentiment) if pd.notna(rating_sentiment) else 0.0,
                'analyst_revision_30d': float(revision) if pd.notna(revision) else 0.0,
                'latest_report_date': str(latest_date) if pd.notna(latest_date) else "",
            }
        return result

    # ============================================================
    # BullScore v2 新增接口（均带24小时缓存）
    # ============================================================

    def _load_dict_cache(self, key: str, expire_hours: int = 24) -> Optional[Dict]:
        """读取 dict 缓存（JSON 格式，24小时有效）"""
        cache_path = self.cache_dir / f"{key}.json"
        if not cache_path.exists():
            return None
        try:
            mtime = cache_path.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600
            if age_hours > expire_hours:
                return None  # 过期
            import json as _json
            with open(cache_path, 'r', encoding='utf-8') as f:
                return _json.load(f)
        except Exception:
            return None

    def _save_dict_cache(self, key: str, data: Dict) -> None:
        """保存 dict 缓存（JSON 格式）"""
        try:
            import json as _json
            cache_path = self.cache_dir / f"{key}.json"
            with open(cache_path, 'w', encoding='utf-8') as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _safe_name(self, ts_code: str) -> str:
        """生成安全的文件名后缀"""
        return ts_code.replace('.', '_').replace('-', '_')

    def get_moneyflow_ndays(self, ts_code: str, n_days: int = 20,
                            end_date: str = None) -> Dict[str, float]:
        """
        获取近N日主力资金净流入汇总（24小时缓存）

        Returns:
            { net_mf_amount_sum(万元), net_inflow_ratio(%), avg_daily_net }
        """
        from datetime import datetime as dt2, timedelta
        if end_date is None:
            end_date = self.get_last_trade_date()
        cache_key = f"mf_ndays_{self._safe_name(ts_code)}_{n_days}_{end_date}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        start_date = (dt2.strptime(end_date, '%Y%m%d') - timedelta(days=n_days + 10)).strftime('%Y%m%d')

        try:
            df = self._retry_call(self.pro.moneyflow, ts_code=ts_code,
                                  start_date=start_date, end_date=end_date)
            if df is None or len(df) == 0:
                result = {'net_mf_amount_sum': 0.0, 'net_inflow_ratio': 0.0, 'avg_daily_net': 0.0}
            else:
                df = df.sort_values('trade_date')
                df = df.tail(n_days)
                net_sum = float(df['net_mf_amount'].sum())
                db = self._retry_call(self.pro.daily_basic, ts_code=ts_code,
                                      start_date=end_date, end_date=end_date)
                circ_mv = float(db.iloc[0]['circ_mv']) if db is not None and len(db) > 0 else 0.0
                ratio = (net_sum / circ_mv * 100) if circ_mv > 0 else 0.0
                avg = net_sum / len(df) if len(df) > 0 else 0.0
                result = {
                    'net_mf_amount_sum': net_sum,
                    'net_inflow_ratio': ratio,
                    'avg_daily_net': avg,
                }
        except Exception:
            result = {'net_mf_amount_sum': 0.0, 'net_inflow_ratio': 0.0, 'avg_daily_net': 0.0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_stk_holdernumber(self, ts_code: str, n_periods: int = 3) -> Dict[str, float]:
        """
        股东人数变化（近N期，24小时缓存）

        Returns:
            { holder_num_latest, holder_num_change_ratio(缩减=正), holder_num_trend }
        """
        cache_key = f"holder_num_{self._safe_name(ts_code)}_{n_periods}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.stk_holdernumber, ts_code=ts_code, limit=n_periods)
            if df is None or len(df) < 2:
                result = {'holder_num_latest': 0.0, 'holder_num_change_ratio': 0.0, 'holder_num_trend': 0.0}
            else:
                df = df.sort_values('end_date')
                latest = float(df.iloc[-1]['holder_num'])
                oldest = float(df.iloc[0]['holder_num'])
                change_ratio = (oldest - latest) / oldest if oldest > 0 else 0.0
                if len(df) >= 2:
                    changes = [(float(df.iloc[i]['holder_num']) - float(df.iloc[i+1]['holder_num'])) /
                               float(df.iloc[i+1]['holder_num']) if float(df.iloc[i+1]['holder_num']) > 0 else 0.0
                               for i in range(len(df)-1)]
                    trend = sum(changes) / len(changes)
                else:
                    trend = 0.0
                result = {
                    'holder_num_latest': latest,
                    'holder_num_change_ratio': change_ratio,
                    'holder_num_trend': trend,
                }
        except Exception:
            result = {'holder_num_latest': 0.0, 'holder_num_change_ratio': 0.0, 'holder_num_trend': 0.0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_stk_holdertrade(self, ts_code: str, n_days: int = 90) -> Dict[str, float]:
        """
        股东增减持（近N日，24小时缓存）

        Returns:
            { holder_trade_vol(万股), holder_trade_ratio(%流通), net_buy }
        """
        from datetime import datetime as dt2, timedelta
        end_date = self.get_last_trade_date()
        start_date = (dt2.strptime(end_date, '%Y%m%d') - timedelta(days=n_days)).strftime('%Y%m%d')
        cache_key = f"holder_trade_{self._safe_name(ts_code)}_{n_days}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.stk_holdertrade, ts_code=ts_code,
                                  start_date=start_date, end_date=end_date)
            if df is None or len(df) == 0:
                result = {'holder_trade_vol': 0.0, 'holder_trade_ratio': 0.0, 'net_buy': 0}
            else:
                vol = float(df['vol'].sum())
                buy_vol = float(df[df['vol'] > 0]['vol'].sum())
                sell_vol = float(df[df['vol'] < 0]['vol'].sum())
                net_buy = 1 if buy_vol > abs(sell_vol) else -1
                db = self._retry_call(self.pro.daily_basic, ts_code=ts_code,
                                      start_date=end_date, end_date=end_date)
                circ_mv = 0.0
                if db is not None and len(db) > 0:
                    close = float(db.iloc[0]['close']) if pd.notna(db.iloc[0]['close']) else 0.0
                    total_mv = float(db.iloc[0]['total_mv']) if pd.notna(db.iloc[0]['total_mv']) else 0.0
                    if close > 0:
                        circ_mv = total_mv / close * 10000
                ratio = (buy_vol + sell_vol) / circ_mv * 100 if circ_mv > 0 else 0.0
                result = {
                    'holder_trade_vol': vol,
                    'holder_trade_ratio': ratio,
                    'net_buy': net_buy,
                }
        except Exception:
            result = {'holder_trade_vol': 0.0, 'holder_trade_ratio': 0.0, 'net_buy': 0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_repurchase(self, ts_code: str, n_days: int = 365) -> Dict[str, float]:
        """
        股票回购信息（近N日，24小时缓存）

        Returns:
            { repurchase_amount(万元), repurchase_ratio(%股本), has_repurchase }
        """
        from datetime import datetime as dt2, timedelta
        end_date = self.get_last_trade_date()
        start_date = (dt2.strptime(end_date, '%Y%m%d') - timedelta(days=n_days)).strftime('%Y%m%d')
        cache_key = f"repurchase_{self._safe_name(ts_code)}_{n_days}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.repurchase, ts_code=ts_code,
                                  start_date=start_date, end_date=end_date)
            if df is None or len(df) == 0:
                result = {'repurchase_amount': 0.0, 'repurchase_ratio': 0.0, 'has_repurchase': 0}
            else:
                amount = float(df['amount'].sum())
                db = self._retry_call(self.pro.daily_basic, ts_code=ts_code,
                                      start_date=end_date, end_date=end_date)
                total_mv = float(db.iloc[0]['total_mv']) if db is not None and len(db) > 0 and pd.notna(db.iloc[0]['total_mv']) else 0.0
                ratio = amount / total_mv * 100 if total_mv > 0 else 0.0
                result = {
                    'repurchase_amount': amount,
                    'repurchase_ratio': ratio,
                    'has_repurchase': 1,
                }
        except Exception:
            result = {'repurchase_amount': 0.0, 'repurchase_ratio': 0.0, 'has_repurchase': 0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_fund_portfolio(self, ts_code: str, n_periods: int = 2) -> Dict[str, float]:
        """
        公募基金持仓变化（近N期季报，24小时缓存）

        Returns:
            { fund_holding_ratio, fund_ratio_change, fund_count }
            fund_count — 最新一期持有该股票的基金数量（覆盖广度）
        """
        cache_key = f"fund_portfolio_{self._safe_name(ts_code)}_{n_periods}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.fund_portfolio, ts_code=ts_code, limit=n_periods)
            if df is None or len(df) == 0:
                result = {'fund_holding_ratio': 0.0, 'fund_ratio_change': 0.0, 'fund_count': 0}
            else:
                df = df.sort_values('end_date')
                latest_ratio = float(df.iloc[-1]['amount']) if 'amount' in df.columns and pd.notna(df.iloc[-1]['amount']) else 0.0
                change = 0.0
                if len(df) >= 2:
                    prev = float(df.iloc[0]['amount']) if pd.notna(df.iloc[0]['amount']) else 0.0
                    change = latest_ratio - prev
                # 统计最新一期持有基金数量（覆盖广度）
                latest_end = df['end_date'].iloc[-1]
                latest_period = df[df['end_date'] == latest_end]
                fund_count = latest_period['fund_code'].nunique() if 'fund_code' in latest_period.columns else 0
                result = {
                    'fund_holding_ratio': latest_ratio,
                    'fund_ratio_change': change,
                    'fund_count': fund_count,
                }
        except Exception:
            result = {'fund_holding_ratio': 0.0, 'fund_ratio_change': 0.0, 'fund_count': 0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_pledge_stat(self, ts_code: str) -> Dict[str, float]:
        """
        股权质押统计（24小时缓存）

        Returns:
            { pledge_ratio(%), pledge_risk_score }
        """
        cache_key = f"pledge_{self._safe_name(ts_code)}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.pledge_stat, ts_code=ts_code)
            if df is None or len(df) == 0:
                result = {'pledge_ratio': 0.0, 'pledge_risk_score': 100.0}
            else:
                df = df.sort_values('end_date')
                latest = df.iloc[-1]
                ratio = float(latest.get('pledge_ratio', 0.0)) if pd.notna(latest.get('pledge_ratio')) else 0.0
                if ratio < 0.20:
                    risk_score = 100.0
                elif ratio < 0.35:
                    risk_score = 80.0
                elif ratio < 0.50:
                    risk_score = 50.0
                else:
                    risk_score = 10.0
                result = {'pledge_ratio': ratio, 'pledge_risk_score': risk_score}
        except Exception:
            result = {'pledge_ratio': 0.0, 'pledge_risk_score': 100.0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_share_float(self, ts_code: str, n_days: int = 60) -> Dict[str, float]:
        """
        限售股解禁压力（未来N日，24小时缓存）

        Returns:
            { float_ratio(%), unlock_ratio(%), unlock_risk_score }
        """
        from datetime import datetime as dt2, timedelta
        end_date = self.get_last_trade_date()
        future_date = (dt2.strptime(end_date, '%Y%m%d') + timedelta(days=n_days)).strftime('%Y%m%d')
        cache_key = f"share_float_{self._safe_name(ts_code)}_{n_days}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.share_float, ts_code=ts_code,
                                  start_date=end_date, end_date=future_date)
            if df is None or len(df) == 0:
                result = {'float_ratio': 0.0, 'unlock_ratio': 0.0, 'unlock_risk_score': 100.0}
            else:
                float_share = float(df['float_share'].sum()) if 'float_share' in df.columns else 0.0
                total_share = float(df['total_share'].iloc[0]) if 'total_share' in df.columns and len(df) > 0 else 0.0
                unlock_ratio = float_share / total_share * 100 if total_share > 0 else 0.0
                if unlock_ratio < 5:
                    risk_score = 100.0
                elif unlock_ratio < 10:
                    risk_score = 80.0
                elif unlock_ratio < 20:
                    risk_score = 50.0
                else:
                    risk_score = 20.0
                result = {'float_ratio': float_share, 'unlock_ratio': unlock_ratio, 'unlock_risk_score': risk_score}
        except Exception:
            result = {'float_ratio': 0.0, 'unlock_ratio': 0.0, 'unlock_risk_score': 100.0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_fina_audit(self, ts_code: str) -> Dict[str, Any]:
        """
        审计意见（最新一期，24小时缓存）

        Returns:
            { audit_opinion, audit_risk_score }
        """
        cache_key = f"audit_{self._safe_name(ts_code)}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.fina_audit, ts_code=ts_code)
            if df is None or len(df) == 0:
                result = {'audit_opinion': '', 'audit_risk_score': 100.0}
            else:
                df = df.sort_values('end_date', ascending=False)
                opinion = str(df.iloc[0]['audit_result']) if 'audit_result' in df.columns and pd.notna(df.iloc[0]['audit_result']) else ''
                if '标准无保留' in opinion or '无保留意见' in opinion:
                    risk_score = 100.0
                elif '保留意见' in opinion:
                    risk_score = 50.0
                elif '无法表示' in opinion or '否定意见' in opinion:
                    risk_score = 10.0
                else:
                    risk_score = 80.0
                result = {'audit_opinion': opinion, 'audit_risk_score': risk_score}
        except Exception:
            result = {'audit_opinion': '', 'audit_risk_score': 100.0}

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_fina_mainbz(self, ts_code: str) -> List[Dict[str, Any]]:
        """
        主营业务构成（最新一期，24小时缓存）

        Returns:
            [{ bz_item, bz_ratio }, ...]
        """
        cache_key = f"mainbz_{self._safe_name(ts_code)}"

        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(self.pro.fina_mainbz, ts_code=ts_code)
            if df is None or len(df) == 0:
                result = []
            else:
                df = df.sort_values('end_date', ascending=False)
                latest_date = df.iloc[0]['end_date']
                df = df[df['end_date'] == latest_date].copy()
                df['bz_ratio'] = pd.to_numeric(df['bz_ratio'], errors='coerce').fillna(0.0)
                result = df[['bz_item', 'bz_ratio']].to_dict('records')
        except Exception:
            result = []

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_chip_margin_batch(self, ts_code: str) -> Dict[str, Any]:
        """
        一次性获取单只股票的筹码面+估值安全所需数据（合并调用，减少延迟）
        内部8个接口均带缓存（24小时有效）

        Returns:
            合并后的 dict
        """
        import concurrent.futures

        results = {}

        def safe_call(func, key):
            try:
                return (key, func())
            except Exception:
                return (key, {})

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures_list = [
                executor.submit(safe_call, lambda: self.get_moneyflow_ndays(ts_code, 20), 'moneyflow'),
                executor.submit(safe_call, lambda: self.get_stk_holdernumber(ts_code, 3), 'holdernumber'),
                executor.submit(safe_call, lambda: self.get_stk_holdertrade(ts_code, 90), 'holdertrade'),
                executor.submit(safe_call, lambda: self.get_repurchase(ts_code, 365), 'repurchase'),
                executor.submit(safe_call, lambda: self.get_fund_portfolio(ts_code, 2), 'fund_portfolio'),
                executor.submit(safe_call, lambda: self.get_pledge_stat(ts_code), 'pledge'),
                executor.submit(safe_call, lambda: self.get_share_float(ts_code, 60), 'share_float'),
                executor.submit(safe_call, lambda: self.get_fina_audit(ts_code), 'audit'),
            ]
            for f in concurrent.futures.as_completed(futures_list):
                key, val = f.result()
                results[key] = val

        return results

    # ============================================================
    # 按 ts_code 查询的接口（评分器复用，与按 trade_date 的切片缓存正交）
    # ============================================================
    def get_hk_hold_by_code(self, ts_code: str) -> Dict[str, float]:
        """
        按股票代码查询北向持股时间序列最新值

        注：tushare 的 pro.hk_hold(trade_date=...) 返回港股通南向数据，
        北向资金持股（沪深股通）需用 pro.hk_hold(ts_code=...) 按股票查询。

        Returns:
            { ratio: 最新持股比例(%), trade_date: 最新日期, vol: 持股量 }
        """
        cache_key = f"hk_hold_code_{self._safe_name(ts_code)}"
        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None:
                return cached

        result = {'ratio': 0.0, 'trade_date': '', 'vol': 0.0}
        try:
            df = self._retry_call(
                self.pro.hk_hold,
                ts_code=ts_code,
                fields='ts_code,trade_date,vol,ratio',
            )
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date', ascending=False)
                latest = df.iloc[0]
                ratio = float(latest['ratio']) if pd.notna(latest.get('ratio')) else 0.0
                vol = float(latest['vol']) if pd.notna(latest.get('vol')) else 0.0
                td = str(latest.get('trade_date', ''))
                # 沪深股通代码仅 .SH/.SZ（排除港股代码 .HK）
                if ts_code.endswith(('.SH', '.SZ')):
                    result = {'ratio': ratio, 'trade_date': td, 'vol': vol}
        except Exception:
            pass

        if self.cache_enabled:
            self._save_dict_cache(cache_key, result)
        return result

    def get_billboard_list(self, ts_code: str,
                           start_date: str = None,
                           end_date: str = None) -> pd.DataFrame:
        """
        按股票代码查询龙虎榜上榜记录

        注：Tushare 的 top_list 接口必须传 trade_date（单日查询），
        本方法仅查询 end_date 当天的上榜记录。如需近60天上榜次数，
        请用 get_billboard_counts_batch。

        Args:
            ts_code: 股票代码
            start_date: 起始日期 YYYYMMDD（保留兼容，内部忽略）
            end_date: 结束日期 YYYYMMDD（默认今天，仅查当日）

        Returns:
            龙虎榜 DataFrame（当日上榜记录）
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        cache_key = f"billboard_{self._safe_name(ts_code)}_{end_date}"

        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(
                self.pro.top_list,
                trade_date=end_date,
                ts_code=ts_code,
            )
        except Exception:
            df = None

        if df is None:
            df = pd.DataFrame()

        if self.cache_enabled and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)
        return df

    def get_billboard_counts_batch(self, start_date: str = None,
                                   end_date: str = None) -> Dict[str, int]:
        """
        批量获取近一段时间的龙虎榜上榜次数统计

        通过循环调用 top_list(trade_date=...) 获取每个交易日的龙虎榜，
        统计每只股票的上榜次数。结果缓存24小时。

        Args:
            start_date: 起始日期 YYYYMMDD（默认近60天）
            end_date: 结束日期 YYYYMMDD（默认今天）

        Returns:
            {ts_code: 上榜次数} 字典
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')

        cache_key = f"billboard_counts_{start_date}_{end_date}"
        if self.cache_enabled:
            cached = self._load_dict_cache(cache_key, self.expire_hours)
            if cached is not None and isinstance(cached, dict):
                return cached

        # 获取交易日列表
        try:
            cal_df = self._retry_call(
                self.pro.trade_cal,
                exchange='SSE',
                start_date=start_date,
                end_date=end_date,
            )
            if cal_df is None or len(cal_df) == 0:
                return {}
            trade_dates = cal_df[cal_df['is_open'] == 1]['cal_date'].tolist()
        except Exception:
            return {}

        counts: Dict[str, int] = {}
        for td in trade_dates:
            day_cache_key = f"top_list_day_{td}"
            day_df = None
            if self.cache_enabled:
                day_df = load_cache(self.cache_dir, day_cache_key, self.expire_hours)
            if day_df is None:
                try:
                    day_df = self._retry_call(self.pro.top_list, trade_date=td)
                except Exception:
                    day_df = None
                if self.cache_enabled and day_df is not None and len(day_df) > 0:
                    save_cache(day_df, self.cache_dir, day_cache_key)
            if day_df is not None and len(day_df) > 0 and 'ts_code' in day_df.columns:
                for code in day_df['ts_code'].unique():
                    counts[code] = counts.get(code, 0) + 1

        if self.cache_enabled and counts:
            self._save_dict_cache(cache_key, counts)
        return counts

    def get_daily_by_code(self, ts_code: str,
                          start_date: str = None,
                          end_date: str = None,
                          fields: str = None) -> pd.DataFrame:
        """
        按股票代码查询日线行情（时间序列）

        Args:
            ts_code: 股票代码
            start_date: 起始日期 YYYYMMDD（默认近365天）
            end_date: 结束日期 YYYYMMDD（默认今天）
            fields: 字段，默认全部

        Returns:
            日线 DataFrame
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
        if fields is None:
            fields = 'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount'
        cache_key = f"daily_code_{self._safe_name(ts_code)}_{start_date}_{end_date}"

        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached

        try:
            df = self._retry_call(
                self.pro.daily,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields=fields,
            )
        except Exception:
            df = None

        if df is None:
            df = pd.DataFrame()

        if self.cache_enabled and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)
        return df

    # ─── 以下为 bull_scorer_v2 等评分器复用的原始 DataFrame 接口 ───

    def _get_df_cached(self, cache_key: str, api_func, **kwargs) -> pd.DataFrame:
        """通用：调用 API 获取原始 DataFrame 并缓存（parquet）"""
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached
        try:
            df = self._retry_call(api_func, **kwargs)
        except Exception:
            df = None
        if df is None:
            df = pd.DataFrame()
        if self.cache_enabled and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)
        return df

    def get_daily_basic_by_code(self, ts_code: str,
                                start_date: str = None,
                                end_date: str = None) -> pd.DataFrame:
        """按股票代码查询 daily_basic（pe/pb/总市值/流通市值/换手率等）"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        cache_key = f"daily_basic_code_{self._safe_name(ts_code)}_{start_date}_{end_date}"
        return self._get_df_cached(
            cache_key, self.pro.daily_basic,
            ts_code=ts_code, start_date=start_date, end_date=end_date,
        )

    def get_moneyflow_by_code(self, ts_code: str,
                              start_date: str = None,
                              end_date: str = None) -> pd.DataFrame:
        """按股票代码查询 moneyflow 资金流向（原始 DataFrame）"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        cache_key = f"moneyflow_code_{self._safe_name(ts_code)}_{start_date}_{end_date}"
        return self._get_df_cached(
            cache_key, self.pro.moneyflow,
            ts_code=ts_code, start_date=start_date, end_date=end_date,
        )

    def get_stk_holdernumber_raw(self, ts_code: str, limit: int = 3) -> pd.DataFrame:
        """按股票代码查询股东人数（原始 DataFrame）"""
        cache_key = f"holdernumber_raw_{self._safe_name(ts_code)}_{limit}"
        return self._get_df_cached(
            cache_key, self.pro.stk_holdernumber,
            ts_code=ts_code, limit=limit,
        )

    def get_fund_portfolio_raw(self, ts_code: str, limit: int = 2) -> pd.DataFrame:
        """按股票代码查询公募基金持仓（原始 DataFrame）"""
        cache_key = f"fund_portfolio_raw_{self._safe_name(ts_code)}_{limit}"
        return self._get_df_cached(
            cache_key, self.pro.fund_portfolio,
            ts_code=ts_code, limit=limit,
        )

    def get_stk_holdertrade_raw(self, ts_code: str,
                                start_date: str = None,
                                end_date: str = None) -> pd.DataFrame:
        """按股票代码查询股东增减持（原始 DataFrame）"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        cache_key = f"holdertrade_raw_{self._safe_name(ts_code)}_{start_date}_{end_date}"
        return self._get_df_cached(
            cache_key, self.pro.stk_holdertrade,
            ts_code=ts_code, start_date=start_date, end_date=end_date,
        )

    def get_pledge_stat_raw(self, ts_code: str) -> pd.DataFrame:
        """按股票代码查询质押状态（原始 DataFrame）"""
        cache_key = f"pledge_raw_{self._safe_name(ts_code)}"
        return self._get_df_cached(
            cache_key, self.pro.pledge_stat, ts_code=ts_code,
        )

    def get_share_float_raw(self, ts_code: str) -> pd.DataFrame:
        """按股票代码查询限售股解禁（原始 DataFrame）"""
        cache_key = f"share_float_raw_{self._safe_name(ts_code)}"
        return self._get_df_cached(
            cache_key, self.pro.share_float, ts_code=ts_code,
        )

    def get_fina_mainbz_raw(self, ts_code: str, period: str = None) -> pd.DataFrame:
        """按股票代码查询主营业务构成（原始 DataFrame）"""
        cache_key = f"mainbz_raw_{self._safe_name(ts_code)}_{period or 'latest'}"
        kwargs = {'ts_code': ts_code}
        if period:
            kwargs['period'] = period
        return self._get_df_cached(
            cache_key, self.pro.fina_mainbz, **kwargs,
        )

    # ─── 以下为统一缓存补充接口（按 trade_date 维度） ───

    def get_trade_cal(self, start_date: str = None, end_date: str = None,
                      is_open: str = '1') -> pd.DataFrame:
        """交易日历（按日期范围缓存）"""
        if end_date is None:
            end_date = (datetime.now() + timedelta(days=365)).strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365 * 3)).strftime('%Y%m%d')
        cache_key = f"trade_cal_{start_date}_{end_date}_{is_open}"
        return self._get_df_cached(
            cache_key, self.pro.trade_cal,
            start_date=start_date, end_date=end_date, is_open=is_open,
        )

    def get_limit_list_ths(self, trade_date: str) -> pd.DataFrame:
        """同花顺涨停板（按日期缓存）"""
        cache_key = f"limit_list_ths_{trade_date}"
        return self._get_df_cached(
            cache_key, self.pro.limit_list_ths, trade_date=trade_date,
        )

    def get_limit_list_d(self, trade_date: str) -> pd.DataFrame:
        """涨停板（按日期缓存）"""
        cache_key = f"limit_list_d_{trade_date}"
        return self._get_df_cached(
            cache_key, self.pro.limit_list_d, trade_date=trade_date,
        )

    def get_limit_step(self, trade_date: str) -> pd.DataFrame:
        """炸板信息（按日期缓存）"""
        cache_key = f"limit_step_{trade_date}"
        return self._get_df_cached(
            cache_key, self.pro.limit_step, trade_date=trade_date,
        )

    def get_top_list(self, trade_date: str) -> pd.DataFrame:
        """龙虎榜（按日期缓存）"""
        cache_key = f"top_list_{trade_date}"
        return self._get_df_cached(
            cache_key, self.pro.top_list, trade_date=trade_date,
        )

    def get_top_inst(self, trade_date: str) -> pd.DataFrame:
        """龙虎榜机构明细（按日期缓存）"""
        cache_key = f"top_inst_{trade_date}"
        return self._get_df_cached(
            cache_key, self.pro.top_inst, trade_date=trade_date,
        )

    def get_stk_factor_pro(self, trade_date: str, ts_code: str = None) -> pd.DataFrame:
        """专业版技术指标（按日期缓存，可选按股票过滤）"""
        cache_key = f"stk_factor_pro_{trade_date}"
        kwargs = {'trade_date': trade_date}
        if ts_code:
            kwargs['ts_code'] = ts_code
            cache_key = f"stk_factor_pro_{trade_date}_{self._safe_name(ts_code)}"
        return self._get_df_cached(
            cache_key, self.pro.stk_factor_pro, **kwargs,
        )

    # ─── 按 ts_code 维度 ───

    def get_index_daily(self, ts_code: str, start_date: str = None,
                        end_date: str = None) -> pd.DataFrame:
        """指数日线（按指数代码缓存）"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
        cache_key = f"index_daily_{self._safe_name(ts_code)}_{start_date}_{end_date}"
        return self._get_df_cached(
            cache_key, self.pro.index_daily,
            ts_code=ts_code, start_date=start_date, end_date=end_date,
        )

    def get_fund_daily(self, ts_code: str, start_date: str = None,
                       end_date: str = None) -> pd.DataFrame:
        """基金日线（按基金代码缓存）"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
        cache_key = f"fund_daily_{self._safe_name(ts_code)}_{start_date}_{end_date}"
        return self._get_df_cached(
            cache_key, self.pro.fund_daily,
            ts_code=ts_code, start_date=start_date, end_date=end_date,
        )

    def get_etf_cons(self, ts_code: str) -> pd.DataFrame:
        """ETF成份股（深市 etf_sz_cons / 沪市 etf_sh_cons）"""
        cache_key = f"etf_cons_{self._safe_name(ts_code)}"
        if ts_code.endswith('.SH'):
            api_func = self.pro.etf_sh_cons
        else:
            api_func = self.pro.etf_sz_cons
        return self._get_df_cached(cache_key, api_func, ts_code=ts_code)

    def get_namechange(self, ts_code: str) -> pd.DataFrame:
        """股票改名记录（按股票代码缓存）"""
        cache_key = f"namechange_{self._safe_name(ts_code)}"
        return self._get_df_cached(
            cache_key, self.pro.namechange, ts_code=ts_code,
        )

    # ─── 东财板块接口 ───

    def get_dc_index(self) -> pd.DataFrame:
        """东财板块指数列表（长期缓存）"""
        cache_key = "dc_index_all"
        # 板块列表较稳定，使用 168h TTL
        if self.cache_enabled:
            cached = load_cache(self.cache_dir, cache_key, self.expire_hours)
            if cached is not None:
                return cached
        try:
            df = self._retry_call(self.pro.dc_index)
        except Exception:
            df = None
        if df is None:
            df = pd.DataFrame()
        if self.cache_enabled and len(df) > 0:
            save_cache(df, self.cache_dir, cache_key)
        return df

    def get_dc_member(self, dc_id: str) -> pd.DataFrame:
        """东财板块成员（按板块ID缓存）"""
        cache_key = f"dc_member_{dc_id}"
        return self._get_df_cached(
            cache_key, self.pro.dc_member, dc_id=dc_id,
        )

    def get_sw_industry_map(self) -> pd.DataFrame:
        """申万行业分类映射（全市场成份股 -> L1/L2/L3 行业名）。

        调用 pro.index_member_all(is_new='Y')，一次性获取全部申万行业成份股。
        返回字段: ts_code, l1_name, l2_name, l3_name, in_date, out_date, is_new
        缓存有效期 168h（7天），成份股变动不频繁。
        """
        cache_key = "sw_industry_map"
        cached = load_cache(self.cache_dir, cache_key, 168)
        if cached is not None:
            return cached
        try:
            df = self._retry_call(self.pro.index_member_all, is_new='Y')
        except Exception:
            df = None
        if df is None or len(df) == 0:
            return pd.DataFrame()
        save_cache(df, self.cache_dir, cache_key)
        return df
