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
