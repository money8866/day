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
        # 精确速率控制: 500 次/分钟 = 每 120ms 一次
        self._min_interval = 0.12
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
                              fields='ts_code,ann_date,end_date,type,period,profit_change,profit_ratio')

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
                                  fields='ts_code,trade_date,close,total_mv,circ_mv,pe,pe_ttm,pb,volume_ratio')
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
                                    max_workers: int = 10) -> Dict[str, Dict]:
        """
        批量获取单只股票的四类财务数据（income + balance + forecast + cashflow）

        每只股票内部串行请求四类数据，股票之间并发
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}

        def fetch_all(code: str) -> tuple:
            income_df = self.get_income(code, start_year=start_year)
            balance_df = self.get_balance_sheet(code, start_year=start_year)
            forecast_df = self.get_forecast(code)
            cashflow_df = self.get_cashflow(code, start_year=start_year)
            return (code, {
                'income': income_df if income_df is not None else pd.DataFrame(),
                'balance': balance_df if balance_df is not None else pd.DataFrame(),
                'forecast': forecast_df if forecast_df is not None else pd.DataFrame(),
                'cashflow': cashflow_df if cashflow_df is not None else pd.DataFrame(),
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

        return results
