"""
通用工具模块
============
提供日志配置、交易日历获取、异常处理装饰器等底层工具函数。

依赖:
    pip install akshare tushare pandas python-dateutil
"""

from __future__ import annotations

import os
import sys
import time
import logging
import logging.handlers
import functools
from datetime import datetime, timedelta, date
from typing import Any, Callable, Dict, List, Optional, Union, cast

import pandas as pd

# --------------------------------------------------------------------------- #
# 1. 日志配置
# --------------------------------------------------------------------------- #

_LOGGER_CACHE: Dict[str, logging.Logger] = {}


def setup_logger(
    name: str = "thematic_investment",
    log_dir: str = "logs",
    log_file: str = "thematic_investment.log",
    level: Union[str, int] = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
    fmt: Optional[str] = None,
    console_output: bool = True,
) -> logging.Logger:
    """
    配置并返回一个带文件滚动 + 控制台输出的 Logger。

    :param name:            Logger 名称
    :param log_dir:         日志目录
    :param log_file:        日志文件名
    :param level:           日志级别 (字符串或 int)
    :param max_bytes:       单文件最大字节
    :param backup_count:    历史文件保留数
    :param fmt:             自定义格式字符串
    :param console_output:  是否输出到 stdout
    """
    if name in _LOGGER_CACHE:
        return _LOGGER_CACHE[name]

    # 解析 level
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # 创建日志目录
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    logger_: logging.Logger = logging.getLogger(name)
    logger_.setLevel(level)
    logger_.propagate = False

    # 防止重复添加 Handler
    if logger_.handlers:
        _LOGGER_CACHE[name] = logger_
        return logger_

    default_fmt: str = (
        "%(asctime)s - %(name)s - %(levelname)s "
        "- %(filename)s:%(lineno)d - %(message)s"
    )
    formatter = logging.Formatter(fmt or default_fmt)

    # 1) 文件 Handler - 按大小滚动
    file_path: str = os.path.join(log_dir, log_file)
    file_handler = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger_.addHandler(file_handler)

    # 2) 控制台 Handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger_.addHandler(console_handler)

    _LOGGER_CACHE[name] = logger_
    return logger_


# 模块默认 logger
logger: logging.Logger = setup_logger()


# --------------------------------------------------------------------------- #
# 2. 异常处理装饰器
# --------------------------------------------------------------------------- #

def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    logger_: Optional[logging.Logger] = None,
) -> Callable:
    """
    失败重试装饰器。

    :param max_attempts: 最大重试次数
    :param delay:        首次失败后等待秒数
    :param backoff:      每次失败后 delay 的倍数 (指数退避)
    :param exceptions:   需要捕获的异常元组
    :param logger_:      可选自定义 logger
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            _log = logger_ or logger
            attempt: int = 1
            current_delay: float = delay
            last_exception: Optional[Exception] = None

            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    _log.warning(
                        "[%s] 第 %d/%d 次调用失败: %s - 等待 %.1fs 后重试",
                        func.__name__, attempt, max_attempts, exc, current_delay,
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff
                    attempt += 1

            _log.error("[%s] 重试 %d 次后仍失败，最终异常: %s",
                       func.__name__, max_attempts, last_exception)
            raise last_exception  # type: ignore[misc]

        return wrapper
    return decorator


def handle_exception(
    default_return: Any = None,
    reraise: bool = False,
    logger_: Optional[logging.Logger] = None,
) -> Callable:
    """
    通用异常处理装饰器，记录异常并返回默认值或重新抛出。

    :param default_return: 捕获异常后的返回值
    :param reraise:        记录后是否继续抛出
    :param logger_:        可选自定义 logger
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            _log = logger_ or logger
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                _log.exception("[%s] 发生未预期异常: %s", func.__name__, exc)
                if reraise:
                    raise
                return default_return
        return wrapper
    return decorator


def timing(logger_: Optional[logging.Logger] = None) -> Callable:
    """函数耗时统计装饰器。"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            start: float = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed: float = time.perf_counter() - start
                (logger_ or logger).info(
                    "[%s] 执行耗时: %.3fs", func.__name__, elapsed,
                )
        return wrapper
    return decorator


# --------------------------------------------------------------------------- #
# 3. 交易日历
# --------------------------------------------------------------------------- #

class TradeCalendar:
    """
    A 股交易日历。

    优先级: tushare (trade_cal) -> akshare (stock_zh_a_spot_em 辅助)
    内部使用内存缓存，避免重复请求。
    """

    _cache: Dict[str, pd.DataFrame] = {}     # key: source_name
    _trade_dates_cache: Optional[List[date]] = None

    # --------------------------------------------------------------- public

    @classmethod
    def get_trade_dates(
        cls,
        start_date: Union[str, date] = "2015-01-01",
        end_date: Optional[Union[str, date]] = None,
        source: str = "tushare",
    ) -> List[date]:
        """
        获取 [start_date, end_date] 区间内的 A 股交易日列表 (date 对象)。

        :param source: "tushare" | "akshare"
        """
        if end_date is None:
            end_date = date.today()

        start_: date = (
            datetime.strptime(start_date, "%Y-%m-%d").date()
            if isinstance(start_date, str) else start_date
        )
        end_: date = (
            datetime.strptime(end_date, "%Y-%m-%d").date()
            if isinstance(end_date, str) else end_date
        )

        cache_key: str = f"{source}_{start_}_{end_}"
        if cache_key in cls._cache:
            df: pd.DataFrame = cls._cache[cache_key]
        else:
            if source == "tushare":
                df = cls._fetch_from_tushare(start_, end_)
            else:
                df = cls._fetch_from_akshare(start_, end_)
            cls._cache[cache_key] = df

        # 数据列兼容: tushare 返回 cal_date + is_open, akshare 返回不同格式
        dates: List[date] = cls._parse_dates(df, source)
        return [d for d in dates if start_ <= d <= end_]

    # ------------------------------------------------------------ helpers

    @classmethod
    def is_trade_day(cls, target_date: Union[str, date],
                     source: str = "akshare") -> bool:
        """判断某天是否为交易日。"""
        target: date = (
            datetime.strptime(target_date, "%Y-%m-%d").date()
            if isinstance(target_date, str) else target_date
        )
        # 向前后扩展 1 周以确保覆盖目标日期
        start: date = target - timedelta(days=7)
        end: date = target + timedelta(days=7)
        dates: List[date] = cls.get_trade_dates(start, end, source=source)
        return target in dates

    @classmethod
    def next_trade_day(cls, current: Union[str, date],
                       source: str = "akshare") -> date:
        """获取下一交易日。"""
        cur: date = (
            datetime.strptime(current, "%Y-%m-%d").date()
            if isinstance(current, str) else current
        )
        start: date = cur + timedelta(days=1)
        dates: List[date] = cls.get_trade_dates(
            start, cur + timedelta(days=14), source=source,
        )
        if not dates:
            raise RuntimeError(f"无法在 {cur} 之后找到交易日")
        return dates[0]

    @classmethod
    def prev_trade_day(cls, current: Union[str, date],
                       source: str = "akshare") -> date:
        """获取上一交易日。"""
        cur: date = (
            datetime.strptime(current, "%Y-%m-%d").date()
            if isinstance(current, str) else current
        )
        end: date = cur - timedelta(days=1)
        dates: List[date] = cls.get_trade_dates(
            cur - timedelta(days=14), end, source=source,
        )
        if not dates:
            raise RuntimeError(f"无法在 {cur} 之前找到交易日")
        return dates[-1]

    # ---------------------------------------------------------- fetchers

    @staticmethod
    @retry(max_attempts=3, delay=2.0, backoff=2.0)
    def _fetch_from_tushare(start_date: date, end_date: date) -> pd.DataFrame:
        """从 tushare trade_cal 接口获取。需要 tushare token。"""
        import tushare as ts
        from modules.db_connector import CONFIG

        token: Optional[str] = (
            CONFIG.get("data_sources", {})
                  .get("tushare_pro", {})
                  .get("token")
        )
        if not token or (isinstance(token, str) and token.startswith("${")):
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法使用 tushare 日历")

        ts.set_token(token)
        pro = ts.pro_api()

        cal: pd.DataFrame = pro.trade_cal(
            exchange="SSE",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            is_open="1",
        )
        return cal

    @staticmethod
    @retry(max_attempts=3, delay=2.0, backoff=2.0)
    def _fetch_from_akshare(start_date: date, end_date: date) -> pd.DataFrame:
        """
        akshare 没有直接的交易日历，这里使用 tool_trade_date_hist_sina()
        作为替代接口。
        """
        import akshare as ak

        # akshare 提供新浪财经的 A 股交易日历
        cal: pd.DataFrame = ak.tool_trade_date_hist_sina()
        cal.columns = [str(c).strip() for c in cal.columns]
        # 常见列名为 "trade_date" 或首列
        if "trade_date" not in cal.columns:
            cal = cal.rename(columns={cal.columns[0]: "trade_date"})
        cal["trade_date"] = pd.to_datetime(cal["trade_date"]).dt.date
        return cal

    @staticmethod
    def _parse_dates(df: pd.DataFrame, source: str) -> List[date]:
        """将不同来源的 DataFrame 解析为 date 列表。"""
        if df.empty:
            return []

        if source == "tushare":
            # cal_date 是 YYYYMMDD 字符串
            dates_series = pd.to_datetime(df["cal_date"].astype(str),
                                          format="%Y%m%d")
            return [d.date() for d in dates_series]

        # akshare
        if "trade_date" in df.columns:
            col = df["trade_date"]
            # 若已是 date，则直接取
            if isinstance(col.iloc[0], date):
                return list(col)
            return [d.date() for d in pd.to_datetime(col)]

        raise ValueError(f"无法解析数据源 {source} 的交易日历结构")


# --------------------------------------------------------------------------- #
# 4. 常用工具函数
# --------------------------------------------------------------------------- #

def today_str(fmt: str = "%Y-%m-%d") -> str:
    """获取今日日期字符串。"""
    return date.today().strftime(fmt)


def ensure_dir(path: str) -> None:
    """确保目录存在，不存在则创建。"""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        logger.debug("创建目录: %s", path)


def to_datetime(value: Union[str, datetime, date]) -> datetime:
    """统一转换为 datetime 对象。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    raise ValueError(f"无法解析日期字符串: {value}")


def chunk_list(items: List[Any], size: int) -> List[List[Any]]:
    """将列表按 size 切片。"""
    if size <= 0:
        raise ValueError("size 必须为正整数")
    return [items[i:i + size] for i in range(0, len(items), size)]


# --------------------------------------------------------------------------- #
# 5. 简单的单元自测
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logger.info("=== utils.py 自测开始 ===")

    # 日志
    logger.debug("debug message")
    logger.info("info message")

    # 交易日历
    try:
        dates: List[date] = TradeCalendar.get_trade_dates(
            "2026-06-01", "2026-06-15", source="akshare",
        )
        logger.info("样本交易日数量: %d", len(dates))
        if dates:
            logger.info("首个交易日: %s", dates[0])
    except Exception as exc:
        logger.warning("交易日历获取失败 (可能无网络): %s", exc)

    # retry 装饰器
    counter: Dict[str, int] = {"n": 0}

    @retry(max_attempts=3, delay=0.1, backoff=1.5)
    def flaky() -> str:
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError(f"临时故障 (第 {counter['n']} 次)")
        return "success"

    logger.info("retry 测试: %s", flaky())

    logger.info("=== utils.py 自测结束 ===")
