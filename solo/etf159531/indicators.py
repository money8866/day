"""技术指标：SMA / Wilder RSI / 年化波动率 / 回撤 / 趋势特征。

所有指标均为「因果」计算：t 日数值只依赖 <= t 的数据，窗口不足时返回 NaN，
不使用任何未来数据，也不做前向填充补齐窗口。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, window: int) -> pd.Series:
    """简单移动平均；不足窗口返回 NaN。"""
    return series.astype(float).rolling(window=window, min_periods=window).mean()


def rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑 RSI。

    - 首个有效值位于第 period 个收益率（需要 period 个涨跌幅），前置 NaN。
    - 连续上涨（平均跌幅=0）返回 100；连续下跌（平均涨幅=0）返回 0；
      完全无变化（两者均为 0）返回中性 50。
    """
    s = close.astype(float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    n = len(s)
    out = np.full(n, np.nan, dtype=float)
    if n <= period:
        return pd.Series(out, index=s.index)

    vals_gain = gain.to_numpy()
    vals_loss = loss.to_numpy()

    # 首个均值：前 period 个涨跌幅的简单平均
    avg_gain = np.nanmean(vals_gain[1:period + 1])
    avg_loss = np.nanmean(vals_loss[1:period + 1])

    def _rsi(g: float, l: float) -> float:
        if l == 0 and g == 0:
            return 50.0
        if l == 0:
            return 100.0
        if g == 0:
            return 0.0
        rs = g / l
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        g = vals_gain[i]
        l = vals_loss[i]
        if np.isnan(g) or np.isnan(l):
            out[i] = np.nan
            continue
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[i] = _rsi(avg_gain, avg_loss)

    return pd.Series(out, index=s.index)


def annualized_vol(close_adj: pd.Series, window: int, ann_factor: int = TRADING_DAYS) -> pd.Series:
    """年化波动率：日收益率标准差(ddof=1) × sqrt(252)。"""
    ret = close_adj.astype(float).pct_change()
    return ret.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(ann_factor)


def effective_volatility(vol20: float, vol60: float, floor: float = 0.18) -> float:
    """effective_vol = max(vol20, vol60, floor)，任一分量为 NaN 时用可用的那个。"""
    vals = [v for v in (vol20, vol60) if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if not vals:
        return float("nan")
    return float(max(max(vals), floor))


def drawdown_series(nav: pd.Series) -> pd.Series:
    """相对历史最高值的回撤（负值）。"""
    nav = nav.astype(float)
    peak = nav.cummax()
    return nav / peak - 1.0


def max_drawdown(nav: pd.Series) -> float:
    dd = drawdown_series(nav)
    return float(dd.min()) if len(dd) else 0.0


def cumulative_return(close: pd.Series, window: int) -> pd.Series:
    """最近 window 个交易日的累计收益（含当日）。"""
    return close.astype(float).pct_change(periods=window)


def compute_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """在 ETF 日线表上追加全部技术指标列。

    输入需含 trade_date / open / high / low / close / adj_factor / close_adj。
    指标一律基于复权收盘价 close_adj。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy().sort_values("trade_date").reset_index(drop=True)
    if "close_adj" not in out.columns:
        out["close_adj"] = out["close"].astype(float) * out.get("adj_factor", 1.0)

    close_adj = out["close_adj"].astype(float)

    trend_cfg = cfg.get("trend", {})
    windows = trend_cfg.get("ma_windows", [5, 10, 20, 60, 120, 250])
    for w in windows:
        out[f"ma{w}"] = sma(close_adj, w)

    vol_cfg = cfg.get("volatility", {})
    ann = int(vol_cfg.get("ann_factor", TRADING_DAYS))
    out["vol20"] = annualized_vol(close_adj, 20, ann)
    out["vol60"] = annualized_vol(close_adj, 60, ann)
    out["effective_vol"] = np.maximum(
        np.maximum(out["vol20"], out["vol60"]).astype(float),
        float(vol_cfg.get("floor_effective_vol", 0.18)),
    )

    os_cfg = cfg.get("oversold", {})
    out["rsi6"] = rsi_wilder(close_adj, int(os_cfg.get("rsi_short", 6)))
    out["rsi14"] = rsi_wilder(close_adj, int(os_cfg.get("rsi_long", 14)))

    for w in (20, 60, 120):
        out[f"ret{w}"] = cumulative_return(close_adj, w)
    out["low20"] = close_adj.rolling(20, min_periods=20).min()
    out["low60"] = close_adj.rolling(60, min_periods=60).min()

    for w in (20, 60, 120):
        ma = out[f"ma{w}"]
        out[f"dev{w}"] = close_adj / ma - 1.0

    return out


def last_valid_row(features: pd.DataFrame) -> pd.Series:
    if features is None or features.empty:
        raise ValueError("特征表为空")
    return features.iloc[-1]
