"""pytest 公共夹具：合成行情（仅用于测试，不能冒充真实历史数据）。"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settings import load_config  # noqa: E402


@pytest.fixture(scope="session")
def base_cfg() -> dict:
    return load_config(str(ROOT / "config.yaml"))


@pytest.fixture()
def cfg(base_cfg) -> dict:
    c = copy.deepcopy(base_cfg)
    c["report"] = dict(c.get("report", {}), html=False)
    return c


def make_ohlcv(n: int = 400, seed: int = 7, start: str = "20230102",
               price: float = 1.0, mu: float = 0.0004,
               sigma: float = 0.012) -> pd.DataFrame:
    """构造带 OHLCV / 复权因子的合成日线（仅测试用）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n).strftime("%Y%m%d")
    rets = rng.normal(mu, sigma, n)
    close = price * np.cumprod(1.0 + rets)
    pre_close = np.r_[price, close[:-1]]
    open_ = pre_close * (1.0 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0, 0.003, n)))
    vol = rng.integers(100_000, 1_000_000, n).astype(float)
    df = pd.DataFrame({
        "ts_code": "159531.SZ",
        "trade_date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "pre_close": pre_close,
        "pct_chg": (close / pre_close - 1.0) * 100.0,
        "vol": vol,
        "amount": vol * close * 0.01,
        "adj_factor": 1.0,
    })
    df["close_adj"] = df["close"] * df["adj_factor"]
    return df


def make_features(n: int = 60, close: np.ndarray | None = None,
                  ma5: np.ndarray | None = None, ma10: np.ndarray | None = None,
                  ma20: np.ndarray | None = None, rsi6: np.ndarray | None = None,
                  dev20: np.ndarray | None = None, vol20: float = 0.2) -> pd.DataFrame:
    """直接构造特征表，用于隔离测试状态机。"""
    close = np.asarray(close, dtype=float) if close is not None else np.full(n, 1.0)
    df = pd.DataFrame({
        "trade_date": pd.bdate_range("2024-01-02", periods=n).strftime("%Y%m%d"),
        "close": close,
        "close_adj": close,
        "open": close,
        "high": close,
        "low": close,
        "pre_close": np.r_[close[0], close[:-1]],
        "adj_factor": 1.0,
        "ma5": np.asarray(ma5, dtype=float) if ma5 is not None else close,
        "ma10": np.asarray(ma10, dtype=float) if ma10 is not None else close,
        "ma20": np.asarray(ma20, dtype=float) if ma20 is not None else close,
        "ma60": close,
        "ma120": close,
        "ma250": close,
        "rsi6": np.asarray(rsi6, dtype=float) if rsi6 is not None else np.full(n, 50.0),
        "rsi14": np.full(n, 50.0),
        "dev20": np.asarray(dev20, dtype=float) if dev20 is not None else np.zeros(n),
        "vol20": np.full(n, vol20),
        "vol60": np.full(n, vol20),
        "effective_vol": np.full(n, max(vol20, 0.18)),
    })
    for k in ("ret20", "ret60", "ret120"):
        df[k] = 0.0
    df["low20"] = close
    df["low60"] = close
    return df
