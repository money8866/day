# -*- coding: utf-8 -*-
"""HVE V1 指标层（严格 PIT：每一天只使用 ≤ 当日的数据）

口径说明（§1.3）：
  - 全部指标用 daily_cache 的**不复权原值**计算（与实盘行情对齐，且 HVE 的所有条件
    都是当日/近期原始价格关系）。
  - 收益统计另见 data.HveData.adjusted_return（复权）。

关于 "前20日均量" 的统一口径（§6 明确要求不含当日）：
  vol_ma20    = volume.shift(1).rolling(20).mean()   ← 前 20 日均量，不含当日
  vr20        = volume / vol_ma20
  该定义同时用于 §14/§21 的 "Volume >= 1.2 × MA20 Volume"，全模块只有这一种口径。
  amount_ma20 同理（不含当日）。

ATR20 采用 Wilder 平滑（教科书 ATR 定义，ewm 只依赖过去，PIT 安全）。
"""
import numpy as np
import pandas as pd

# 规格固定的窗口（任务书 §12/§14/§20 明确写死 MA20 / MA60 / 近3日）
MA_FAST = 20          # §12 close > MA20
MA_SLOW = 60          # §31 指数 MA60 同口径
SLOPE_LAG = 5         # §12 MA20 > MA20[-5]
PRE_HIGH_WINDOW = 20  # §10 distance_to_20d_high
PRE_HIGH_WINDOW_LONG = 60

_NUM = ('open', 'high', 'low', 'close', 'vol', 'amount', 'pre_close', 'adj_factor')


def compute_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """在单股日线时序上追加全部 HVE 指标列（纯函数，返回新 DataFrame）。

    输入要求：daily_cache 原值，按 trade_date 升序，至少含 open/high/low/close/vol/pre_close。
    """
    if df is None or df.empty:
        return df
    d = df.copy().reset_index(drop=True)
    for c in _NUM:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors='coerce')
        else:
            d[c] = np.nan

    close, high, low = d['close'], d['high'], d['low']
    vol, amount, pc = d['vol'], d['amount'], d['pre_close']

    atr_window = int(cfg.get('atr_window', 20))
    bo_days = int(cfg.get('breakout_lookback_high_days', 3))
    decay_win = int(cfg.get('volume_decay_window', 5))

    # ---- 成交量 / 成交额 ----
    d['vol_ma20'] = vol.shift(1).rolling(MA_FAST, min_periods=MA_FAST).mean()
    d['amount_ma20'] = amount.shift(1).rolling(MA_FAST, min_periods=MA_FAST).mean()
    with np.errstate(divide='ignore', invalid='ignore'):
        d['vr20'] = np.where(d['vol_ma20'] > 0, vol / d['vol_ma20'], np.nan)

    # ---- 当日形态 ----
    rng = high - low
    d['clv'] = np.where(rng > 0, (close - low) / rng, np.nan)   # §6: high==low → 缺失
    d['daily_return'] = np.where(pc > 0, close / pc - 1.0, np.nan)

    # ---- 均线 ----
    d['ma20'] = close.rolling(MA_FAST, min_periods=MA_FAST).mean()
    d['ma60'] = close.rolling(MA_SLOW, min_periods=MA_SLOW).mean()
    d['ma20_prev5'] = d['ma20'].shift(SLOPE_LAG)

    # ---- ATR(20)（Wilder）----
    tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    d['tr'] = tr
    d['atr20'] = tr.ewm(alpha=1.0 / atr_window, adjust=False,
                        min_periods=atr_window).mean()

    # ---- 前高（§10：只能使用事件日「之前」的数据）----
    d['high_prev3'] = high.shift(1).rolling(bo_days, min_periods=bo_days).max()
    d['high_prev20'] = high.shift(1).rolling(PRE_HIGH_WINDOW, min_periods=PRE_HIGH_WINDOW).max()
    d['high_prev60'] = high.shift(1).rolling(PRE_HIGH_WINDOW_LONG,
                                             min_periods=PRE_HIGH_WINDOW_LONG).max()

    d.attrs['volume_decay_window'] = decay_win
    return d


def drawdown_since(d: pd.DataFrame, e: int, t: int) -> float:
    """HVE 后最大回撤（§13/§16/§23）＝ 1 − min(close[e+1..t]) / max(close[e..t])；事件当日记 0。

    口径说明：用**收盘价**峰谷（避免 HVE 当日自身的振幅污染），且谷值窗口从 T+1 开始
    ——「HVE 后回撤」度量的是事件之后的回落幅度，不含事件这根 K 线自身的开收盘。
    """
    if t <= e:
        return 0.0
    seg = pd.to_numeric(d['close'].iloc[e:t + 1], errors='coerce')
    post = pd.to_numeric(d['close'].iloc[e + 1:t + 1], errors='coerce')
    hi = float(seg.max())
    lo = float(post.min())
    if not (np.isfinite(hi) and np.isfinite(lo)) or hi <= 0:
        return np.nan
    return 1.0 - lo / hi


def consolidation_high(d: pd.DataFrame, e: int, t: int) -> float:
    """整理区高点（§20）：HVE 后至当前的局部最高价，**不包含当前突破日**。"""
    if t <= e:
        return np.nan
    return float(d['high'].iloc[e:t].max())
