"""
ELD V2 工具函数

通用工具函数集合：评分映射、日期校验、阈值评分、日志、限流等。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Generator, Optional

from .constants import STAR_THRESHOLDS, StarRating


# ──────────────────────────────────────────────
# 评分映射
# ──────────────────────────────────────────────


def get_star_rating(score: float) -> StarRating:
    """将数值分数映射为星级评分。"""
    for threshold, star in STAR_THRESHOLDS:
        if score >= threshold:
            return star
    return StarRating.ZERO


# ──────────────────────────────────────────────
# 日期工具
# ──────────────────────────────────────────────

_DATE_REGEX = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")


def validate_date(date_str: str) -> bool:
    """验证日期字符串是否为有效格式（YYYYMMDD 或 YYYY-MM-DD）。

    Args:
        date_str: 日期字符串。

    Returns:
        是否为有效日期。
    """
    if not _DATE_REGEX.match(date_str):
        return False
    normalized = date_str.replace("-", "")
    try:
        datetime.strptime(normalized, "%Y%m%d")
        return True
    except ValueError:
        return False


def get_last_trade_date() -> str:
    """获取最近一个交易日，格式 YYYYMMDD。

    优先从 TUSHARE_TOKEN 环境变量取 token，
    尝试调用 tushare 交易日历，失败则回退到最近工作日。
    """
    try:
        import tushare as ts

        token = os.getenv("TUSHARE_TOKEN", "")
        pro = ts.pro_api(token) if token else ts.pro_api()
        df = pro.trade_cal(
            start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if df is not None and not df.empty:
            cal = df[df["is_open"] == 1]
            if not cal.empty:
                return cal["cal_date"].iloc[-1]
    except Exception:
        pass

    # 回退：取最近一个工作日（跳过周末）
    today = datetime.now()
    offset = 0
    while offset < 7:
        d = today - timedelta(days=offset)
        if d.weekday() < 5:
            return d.strftime("%Y%m%d")
        offset += 1
    return today.strftime("%Y%m%d")


# ──────────────────────────────────────────────
# 数值工具
# ──────────────────────────────────────────────


def safe_float(val: Any, default: float = 0.0) -> float:
    """安全地将输入转换为 float。

    Args:
        val: 输入值。
        default: 转换失败时的默认值。

    Returns:
        float 值。
    """
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ──────────────────────────────────────────────
# 阈值评分
# ──────────────────────────────────────────────


def threshold_score(
    value: float, thresholds: list[tuple[float, float]]
) -> float:
    """根据阈值区间对数值进行评分。

    Args:
        value: 待评分数值。
        thresholds: 阈值列表，每项为 (lower_bound, score)。
          从高到低排列，匹配首个 value >= lower_bound 的区间。

    Returns:
        对应的分数。
    """
    for lower_bound, score in thresholds:
        if value >= lower_bound:
            return score
    return 0.0


# ──────────────────────────────────────────────
# 日志工具
# ──────────────────────────────────────────────


def setup_logging(level: str, log_file: Optional[str] = None) -> logging.Logger:
    """配置 ELD 系统日志。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
        log_file: 可选的日志文件路径。

    Returns:
        配置好的 Logger 实例。
    """
    logger = logging.getLogger("eld")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler（可选）
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# ──────────────────────────────────────────────
# Tushare API 限流器
# ──────────────────────────────────────────────


class _RateLimiter:
    """Tushare API 调用频率限制器（线程安全，支持上下文管理器）。"""

    def __init__(self, min_interval_ms: int = 120) -> None:
        self.min_interval = min_interval_ms / 1000.0
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def __enter__(self) -> "_RateLimiter":
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    @contextmanager
    def acquire(self) -> Generator[None, None, None]:
        """获取 API 调用许可，必要时等待限流间隔。"""
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()
        yield


# 全局限流器实例
_global_rate_limiter: Optional[_RateLimiter] = None
_rate_limiter_lock = threading.Lock()


@contextmanager
def rate_limiter(min_interval_ms: int = 120) -> Generator[_RateLimiter, None, None]:
    """获取或创建全局 tushare API 限流器上下文管理器。

    Args:
        min_interval_ms: 最小调用间隔（毫秒）。默认 120ms ≈ 8次/秒。

    Usage:
        with rate_limiter():
            df = pro.forecast(...)
    """
    global _global_rate_limiter
    if _global_rate_limiter is None:
        with _rate_limiter_lock:
            if _global_rate_limiter is None:
                _global_rate_limiter = _RateLimiter(min_interval_ms)
    with _global_rate_limiter:
        yield _global_rate_limiter


# ──────────────────────────────────────────────
# 序列化工具
# ──────────────────────────────────────────────


class EldJsonEncoder(json.JSONEncoder):
    """支持 dataclass 和自定义类型的 JSON 编码器。"""

    def default(self, o: Any) -> Any:
        if hasattr(o, "to_dict"):
            return o.to_dict()
        if isinstance(o, Enum):
            return o.value
        if hasattr(o, "_asdict"):
            return o._asdict()
        return super().default(o)


def eld_json_dumps(obj: Any, **kwargs: Any) -> str:
    """使用 EldJsonEncoder 的 JSON 序列化。"""
    return json.dumps(obj, cls=EldJsonEncoder, ensure_ascii=False, **kwargs)


# ──────────────────────────────────────────────
# CSV 工具
# ──────────────────────────────────────────────


def dicts_to_csv(data: list[dict[str, Any]], fieldnames: list[str]) -> str:
    """将字典列表转为 CSV 字符串。

    Args:
        data: 字典列表。
        fieldnames: 列顺序。

    Returns:
        CSV 格式字符串。
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    return output.getvalue()


def csv_to_dicts(csv_str: str) -> list[dict[str, str]]:
    """将 CSV 字符串解析为字典列表。

    Args:
        csv_str: CSV 格式字符串。

    Returns:
        字典列表。
    """
    reader = csv.DictReader(io.StringIO(csv_str))
    return list(reader)


# ──────────────────────────────────────────────
# 集合工具
# ──────────────────────────────────────────────


def chunks(lst: list[Any], size: int) -> Generator[list[Any], None, None]:
    """将列表分割为指定大小的块。"""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
