"""Helper utilities for the ETF Resonance system."""

import os
import yaml
import logging
import time
import functools
import numpy as np
import pandas as pd
from typing import Any, Dict, Optional, Callable


def validate_dataframe(df: pd.DataFrame, required_cols: list, name: str = "DataFrame"):
    """Validate that a DataFrame has all required columns."""
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name} missing required columns: {missing}")
    if df.empty:
        raise ValueError(f"{name} is empty")


def safe_div(a: np.ndarray, b: np.ndarray, default: float = 0.0) -> np.ndarray:
    """Safe division, replacing inf/nan with default."""
    result = np.divide(a, b, out=np.full_like(a, default, dtype=np.float64),
                       where=np.abs(b) > 1e-10)
    result[~np.isfinite(result)] = default
    return result


def timeit(func: Callable) -> Callable:
    """Decorator to log function execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        logger = logging.getLogger(__name__)
        logger.debug(f"{func.__name__} took {elapsed:.3f}s")
        return result
    return wrapper


def setup_logger(name: str = "etf_resonance",
                 level: int = logging.INFO,
                 log_file: Optional[str] = None) -> logging.Logger:
    """Configure and return a logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        if log_file:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)

    return logger


class Config:
    """YAML-based configuration manager."""

    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        if name in self._data:
            return DictWrapper(self._data[name])
        raise AttributeError(f"Config has no section '{name}'")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a top-level config section. Use dot notation for nested access."""
        d = self._data
        if "." in key:
            parts = key.split(".")
            for k in parts:
                if isinstance(d, dict):
                    d = d.get(k)
                    if d is None:
                        return default
                else:
                    return default
            return d
        return self._data.get(key, default)

    def as_dict(self) -> dict:
        return self._data


class DictWrapper:
    """Wrapper to allow dict access via attributes."""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        val = self._data.get(name)
        if isinstance(val, dict):
            return DictWrapper(val)
        return val

    def __contains__(self, item):
        return item in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def items(self):
        return self._data.items()

    def __repr__(self):
        return repr(self._data)
