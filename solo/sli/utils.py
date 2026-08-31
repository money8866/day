# -*- coding: utf-8 -*-
"""
SLI 工具函数：限流、日志、token、安全转换、日期
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Generator, Optional

import pandas as pd

from .config import ENV_CANDIDATES, RATE_LIMIT_MS


# ── Token ────────────────────────────────────────────

def load_token() -> str:
    """从环境变量或 config/.env 加载 TUSHARE_TOKEN。"""
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    for p in ENV_CANDIDATES:
        p = os.path.normpath(p)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TUSHARE_TOKEN"):
                        if "=" in line:
                            token = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if token:
                                return token
        except OSError:
            continue
    return ""


# ── 限流器（线程安全，全局单例） ──────────────────────

class _RateLimiter:
    def __init__(self, min_interval_ms: int = RATE_LIMIT_MS) -> None:
        self.min_interval = min_interval_ms / 1000.0
        self._last_call = 0.0
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


_limiter: Optional[_RateLimiter] = None
_limiter_lock = threading.Lock()


@contextmanager
def rate_limit(min_interval_ms: int = RATE_LIMIT_MS) -> Generator[_RateLimiter, None, None]:
    """全局 Tushare API 限流上下文。"""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = _RateLimiter(min_interval_ms)
    with _limiter:
        yield _limiter


# ── 数值工具 ─────────────────────────────────────────

def safe_float(val: Any, default: float = float("nan")) -> float:
    if val is None:
        return default
    try:
        v = float(val)
        return v
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


# ── 日期工具 ─────────────────────────────────────────

def trade_dates_from_cal(cal: pd.DataFrame) -> list[str]:
    """从 trade_cal 中提取交易日（升序）。"""
    df = cal[cal["is_open"] == 1]["cal_date"].astype(str).drop_duplicates().sort_values()
    return df.tolist()


def shift_trade_date(dates: list[str], target: str, back: int) -> Optional[str]:
    """在升序交易日列表中，返回 <= target 且相距 back 个交易日的那一天。"""
    idx = None
    for i, d in enumerate(dates):
        if d <= target:
            idx = i
        else:
            break
    if idx is None:
        return None
    pos = max(0, idx - back)
    return dates[pos]


# ── 日志 ─────────────────────────────────────────────

def setup_logging(log_dir: str, name: str = "sli") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    os.makedirs(log_dir, exist_ok=True)
    fh = logging.FileHandler(
        os.path.join(log_dir, f"sli_{datetime.now():%Y%m%d}.log"),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger
