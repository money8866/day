"""Stock Feature Engine —— 个股行为特征提取（第一层）。

输入：data_layer.Slice（严格 as-of 的 T x N 面板）
输出：FeatureSet（截面特征 + 时序特征 + 8 类异动事件矩阵 + 事件明细）

全部指标向量化计算；事件只作为行为特征，绝不等同于资金净流入或买入信号。
事件的"后续表现"字段只在后续日期已 <= asof 时填写，不使用未来数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .common import get_logger, finite
from .data_layer import Slice

log = get_logger("feature_engine")

EVENT_TYPES = (
    "VOL_UP", "VOL_DOWN", "BREAKOUT", "PULLBACK", "REBOUND",
    "RELATIVE_STRENGTH", "LIMIT_UP", "LIMIT_DOWN",
)


@dataclass
class FeatureSet:
    s: Slice
    latest: dict[str, np.ndarray] = field(default_factory=dict)
    series: dict[str, np.ndarray] = field(default_factory=dict)
    events: dict[str, np.ndarray] = field(default_factory=dict)   # type -> T x N int8
    event_rows: list[dict[str, Any]] = field(default_factory=list)
    trend_state: np.ndarray | None = None                         # T x N float32
    meta: dict[str, Any] = field(default_factory=dict)

    def l(self, key: str) -> np.ndarray:
        return self.latest.get(key, np.zeros(self.s.N, dtype=np.float64))

    def series_of(self, key: str) -> np.ndarray:
        return self.series.get(key, np.zeros((self.s.T, self.s.N), dtype=np.float32))


def _df(a: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(np.asarray(a, dtype=np.float64))


def _roll(a: pd.DataFrame, w: int, how: str = "mean", mp: int | None = None) -> pd.DataFrame:
    mp = mp if mp is not None else max(2, int(w * 0.6))
    r = a.rolling(w, min_periods=mp)
    return getattr(r, how)()


def compute_features(s: Slice, cfg: dict[str, Any]) -> FeatureSet:
    fcfg = cfg["features"]
    ucfg = cfg["universe"]
    fs = FeatureSet(s=s)
    T, N = s.T, s.N

    R = _df(s.ret)
    C = _df(s.close).ffill()
    H = _df(s.high).replace(0.0, np.nan).ffill()
    L = _df(s.low).replace(0.0, np.nan).ffill()
    A = _df(s.amount)
    TOR = _df(s.turnover)
    valid = s.valid.astype(bool)
    R = R.where(valid, np.nan)
    Rf = R.fillna(0.0)

    logret = np.log1p(Rf.clip(lower=-0.9, upper=3.0))
    cum = logret.cumsum(axis=0)
    mkt = pd.Series(np.asarray(s.market_ret, dtype=np.float64))
    cum_mkt = np.log1p(mkt.clip(lower=-0.9, upper=3.0)).cumsum()
    cum_ind = pd.DataFrame(np.asarray(s.industry_ret, dtype=np.float64)).clip(lower=-0.9, upper=3.0)
    cum_ind = np.log1p(cum_ind).cumsum(axis=0)

    def ret_w(w: int) -> np.ndarray:
        if T < 2:
            return np.zeros(N)
        k = min(w, T - 1)
        d = cum.iloc[-1].to_numpy() - cum.iloc[-1 - k].to_numpy()
        return np.expm1(d)

    def excess_w(w: int, bench: pd.Series | pd.DataFrame) -> np.ndarray:
        k = min(w, T - 1)
        if isinstance(bench, pd.Series):
            b = bench.iloc[-1] - bench.iloc[-1 - k]
            return ret_w(w) - np.expm1(b)
        b = bench.iloc[-1].to_numpy() - bench.iloc[-1 - k].to_numpy()
        return ret_w(w) - np.expm1(b)

    # ---------------- 1. 收益与相对强度 ----------------
    for w in fcfg.get("return_windows", [5, 10, 20, 60]):
        fs.latest[f"ret_{w}"] = ret_w(w)
        fs.latest[f"excess_mkt_{w}"] = excess_w(w, cum_mkt)
        fs.latest[f"excess_ind_{w}"] = excess_w(w, cum_ind)
    fs.latest["rs5_change"] = fs.latest["excess_mkt_5"] - _shift_last(cum, cum_mkt, 5, 5)
    fs.latest["rs20_change"] = _rs_change(cum, cum_mkt, 20, 5)

    resid = s.resid_mkt if s.resid_mkt is not None else None
    if resid is not None:
        rr = pd.DataFrame(np.asarray(resid, dtype=np.float64)).where(valid, 0.0).fillna(0.0)
        for w in (5, 10, 20):
            k = min(w, T)
            fs.latest[f"resid_cum_{w}"] = rr.iloc[-k:].sum(axis=0).to_numpy()
            fs.series[f"resid_roll_{w}"] = rr.rolling(max(3, w // 2), min_periods=2).sum().to_numpy(dtype=np.float32)
    else:
        for w in (5, 10, 20):
            fs.latest[f"resid_cum_{w}"] = np.zeros(N)

    roll_max20 = _roll(C, 20, "max")
    dd = (C / roll_max20 - 1.0).where(np.isfinite(roll_max20))
    fs.latest["max_drawdown_20"] = np.nan_to_num(_roll(dd, 20, "min").iloc[-1].to_numpy(), nan=0.0)
    trough = _roll(C, 20, "min")
    with np.errstate(divide="ignore", invalid="ignore"):
        recovery = C.iloc[-1].to_numpy() / np.where(trough.iloc[-1].to_numpy() > 0,
                                                    trough.iloc[-1].to_numpy(), np.nan) - 1.0
    fs.latest["drawdown_repair"] = np.nan_to_num(recovery, nan=0.0, posinf=0.0, neginf=0.0)

    # ---------------- 2. 成交行为 ----------------
    amt20 = _roll(A, 20, "mean")
    for w in fcfg.get("amount_windows", [5, 10, 20]):
        fs.latest[f"amt_mean_{w}"] = np.nan_to_num(_roll(A, w, "mean").iloc[-1].to_numpy(), nan=0.0)
    amt_ratio = (A / amt20.shift(1).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    fs.series["amount_ratio"] = amt_ratio.fillna(1.0).clip(lower=0, upper=10).to_numpy(dtype=np.float32)
    fs.latest["amount_ratio"] = np.nan_to_num(amt_ratio.iloc[-1].to_numpy(), nan=1.0)
    tor5, tor20 = _roll(TOR, 5, "mean"), _roll(TOR, 20, "mean")
    fs.latest["turnover_change"] = np.nan_to_num(
        (tor5.iloc[-1].to_numpy() / np.where(tor20.iloc[-1].to_numpy() > 0,
                                             tor20.iloc[-1].to_numpy(), np.nan) - 1.0),
        nan=0.0, posinf=0.0, neginf=0.0)

    vu = float(fcfg.get("vol_up_multiple", 1.8))
    vd = float(fcfg.get("vol_down_multiple", 1.8))
    vs = float(fcfg.get("vol_shrink_multiple", 0.7))
    amt_base = amt20.shift(1)
    up_strong = (R > 0.02) & (A > vu * amt_base)
    dn_strong = (R < -0.02) & (A > vd * amt_base)
    shrink = A < vs * amt_base
    recent20 = slice(max(0, T - 20), T)
    fs.latest["vol_up_count_20"] = up_strong.iloc[recent20].sum(axis=0).to_numpy()
    fs.latest["vol_down_count_20"] = dn_strong.iloc[recent20].sum(axis=0).to_numpy()
    ma20 = _roll(C, 20, "mean")
    pullback_cond = (R < 0) & shrink & (C > ma20 * 0.97)
    fs.latest["shrink_pullback_count_20"] = pullback_cond.iloc[recent20].sum(axis=0).to_numpy()
    prev_high20 = _roll(C.shift(1), int(fcfg.get("breakout_lookback", 20)), "max")
    breakout_cond = (C > prev_high20) & (A > 1.5 * amt_base)
    fs.latest["vol_breakout_count_20"] = breakout_cond.iloc[recent20].sum(axis=0).to_numpy()

    growing = (A > A.shift(1)).fillna(False)
    streak = np.zeros(N, dtype=np.float64)
    g = growing.to_numpy()
    for i in range(T - 1, max(-1, T - 11), -1):
        streak = np.where(g[i], streak + 1, streak) if i == T - 1 else streak
    # 从最后一天往前数连续放量天数
    streak = np.zeros(N, dtype=np.float64)
    for i in range(T - 1, -1, -1):
        streak = np.where(g[i] & (streak == (T - 2 - i)), streak + 1, streak)
        if not g[i].any():
            break
    fs.latest["amount_growth_streak"] = streak
    up_amt = A.where(R > 0).iloc[recent20].mean(axis=0).to_numpy()
    dn_amt = A.where(R < 0).iloc[recent20].mean(axis=0).to_numpy()
    fs.latest["up_down_amount_ratio"] = np.nan_to_num(
        up_amt / np.where(dn_amt > 0, dn_amt, np.nan), nan=1.0, posinf=3.0)

    # ---------------- 3. 趋势行为 ----------------
    mas = {}
    for w in fcfg.get("ma_windows", [5, 10, 20, 60]):
        mas[w] = _roll(C, w, "mean")
        fs.series[f"ma{w}"] = mas[w].to_numpy(dtype=np.float32)
    stack = ((mas[5] > mas[10]).astype(float) + (mas[10] > mas[20]).astype(float)
             + (mas[20] > mas[60]).astype(float)) / 3.0
    fs.trend_state = np.nan_to_num(stack.to_numpy(), nan=0.0).astype(np.float32)
    fs.latest["ma_stack"] = fs.trend_state[-1].astype(np.float64)
    fs.latest["above_ma20"] = (C.iloc[-1].to_numpy() > np.nan_to_num(mas[20].iloc[-1].to_numpy(), nan=1e18)).astype(float)
    for w in (20, 60):
        m = mas[w]
        slope = (m / m.shift(5) - 1.0) / 5.0
        fs.latest[f"ma{w}_slope"] = np.nan_to_num(slope.iloc[-1].to_numpy(), nan=0.0)
    fs.series["ma20_slope"] = np.nan_to_num(((mas[20] / mas[20].shift(5) - 1) / 5).to_numpy(),
                                            nan=0.0).astype(np.float32)

    fs.latest["platform_breakout_count_20"] = breakout_cond.iloc[recent20].sum(axis=0).to_numpy()
    pb_ok = pd.DataFrame(False, index=R.index, columns=R.columns)
    # 突破后 5 日内出现缩量回踩且仍未跌破 MA20，视为回踩成功
    for lag in range(1, int(fcfg.get("pullback_window", 5)) + 1):
        pb_ok |= (breakout_cond.shift(lag).fillna(False)
                  & pullback_cond.fillna(False) & (C > ma20).fillna(False))
    fs.latest["pullback_success_20"] = pb_ok.iloc[recent20].sum(axis=0).to_numpy()
    low5 = _roll(L, 5, "min")
    bottom_up = (low5 > low5.shift(5)).fillna(False)
    fs.latest["bottom_rising_count_20"] = bottom_up.iloc[recent20].sum(axis=0).to_numpy()
    high10 = _roll(H, 10, "max")
    fs.latest["high_low_rising_degree"] = np.nan_to_num(
        high10.iloc[-1].to_numpy() / np.where(high10.iloc[-11].to_numpy() > 0,
                                              high10.iloc[-11].to_numpy(), np.nan) - 1.0,
        nan=0.0, posinf=0.0) if T > 11 else np.zeros(N)
    fs.latest["pullback_depth"] = np.nan_to_num(
        (roll_max20.iloc[-1].to_numpy() - _roll(L, 5, "min").iloc[-1].to_numpy())
        / np.where(roll_max20.iloc[-1].to_numpy() > 0, roll_max20.iloc[-1].to_numpy(), np.nan),
        nan=0.0, posinf=0.0)
    low20 = _roll(L, 20, "min")
    pos_since_low = np.zeros(N)
    lv = L.to_numpy()
    for j in range(N):
        seg = lv[-20:, j]
        if np.isfinite(seg).any():
            k = int(np.nanargmin(seg))
            pos_since_low[j] = 20 - k
    fs.latest["restart_speed"] = np.nan_to_num(
        (C.iloc[-1].to_numpy() / np.where(low20.iloc[-1].to_numpy() > 0,
                                          low20.iloc[-1].to_numpy(), np.nan) - 1.0)
        / np.maximum(pos_since_low, 1.0), nan=0.0, posinf=0.0)

    # ---------------- 4. 异动事件 ----------------
    ev: dict[str, np.ndarray] = {}
    strength_clip = fcfg.get("event_strength_clip", [0.0, 3.0])
    lo, hi = float(strength_clip[0]), float(strength_clip[1])
    pct = np.asarray(s.pct_chg, dtype=np.float64) / 100.0
    is_gem = np.asarray([c.startswith(("30", "68")) for c in s.codes])
    is_st = np.asarray(["ST" in str(n).upper() or "退" in str(n) for n in s.names])
    lim = np.where(is_st, ucfg.get("limit_up_st", 0.048),
                   np.where(is_gem, ucfg.get("limit_up_gem_star", 0.198),
                            ucfg.get("limit_up_main", 0.098)))
    ratio_series = np.asarray(fs.series["amount_ratio"], dtype=np.float64)

    rebound_base = C.rolling(5, min_periods=2).max() / C.shift(1) - 1.0
    rel_strength = (mkt.to_numpy()[:, None] < -0.005) & (R.to_numpy() > mkt.to_numpy()[:, None] + 0.02)
    raw_events = {
        "VOL_UP": ((R > 0.02) & (A > vu * amt_base)).to_numpy(),
        "VOL_DOWN": ((R < -0.02) & (A > vd * amt_base)).to_numpy(),
        "BREAKOUT": breakout_cond.to_numpy(),
        "PULLBACK": pullback_cond.to_numpy(),
        "REBOUND": ((R > 0.03) & (A > 1.2 * amt_base) & (rebound_base > 0.03)).to_numpy(),
        "RELATIVE_STRENGTH": rel_strength,
        "LIMIT_UP": pct >= lim[None, :],
        "LIMIT_DOWN": pct <= -lim[None, :],
    }
    for k, m in raw_events.items():
        ev[k] = np.nan_to_num(m, nan=0.0).astype(np.int8)
    fs.events = ev

    # 事件明细：只落近 20 个交易日、且属于当日或近期可用于同步匹配的记录
    rows: list[dict[str, Any]] = []
    day_from = max(0, T - 20)
    fwd_cache: dict[int, np.ndarray] = {}
    for h in (1, 3, 5):
        fwd_cache[h] = np.full((T, N), np.nan, dtype=np.float32)
        if T > h:
            # 未来 h 日累计收益，只在 t+h <= T-1 时有值（绝不越过 asof）
            c = cum
            fwd_cache[h][:T - h] = np.expm1(c.shift(-h) - c).to_numpy(dtype=np.float32)[:T - h]
    for ti in range(day_from, T):
        for et in EVENT_TYPES:
            js = np.flatnonzero(ev[et][ti])
            for j in js:
                rows.append({
                    "trade_date": s.dates[ti], "ts_code": s.codes[j],
                    "event_type": et,
                    "strength": round(float(np.clip(max(ratio_series[ti, j], abs(s.ret[ti, j]) * 10), lo, hi)), 4),
                    "amount_ratio": round(float(ratio_series[ti, j]), 4),
                    "pct_chg": round(float(s.pct_chg[ti, j]), 4),
                    "fwd_ret_1": _f(fwd_cache[1][ti, j]),
                    "fwd_ret_3": _f(fwd_cache[3][ti, j]),
                    "fwd_ret_5": _f(fwd_cache[5][ti, j]),
                })
    fs.event_rows = rows
    fs.meta = {
        "asof": s.asof, "T": T, "N": N,
        "event_types": list(EVENT_TYPES),
        "event_rows": len(rows),
        "events_by_type": {k: int(v.sum()) for k, v in ev.items()},
        "feature_count": len(fs.latest),
        "no_future_data": True,
    }
    log.info("特征完成: %d 个截面指标, %d 条近20日事件", len(fs.latest), len(rows))
    return fs


def _f(x: float) -> float | None:
    v = float(x)
    return round(v, 6) if np.isfinite(v) else None


def _rs_change(cum: pd.DataFrame, cum_mkt: pd.Series, w: int, lag: int) -> np.ndarray:
    T = len(cum)
    if T <= w + lag:
        return np.zeros(cum.shape[1])
    k = min(w, T - 1)
    now = np.expm1(cum.iloc[-1].to_numpy() - cum.iloc[-1 - k].to_numpy()) - \
          np.expm1(cum_mkt.iloc[-1] - cum_mkt.iloc[-1 - k])
    prev = np.expm1(cum.iloc[-1 - lag].to_numpy() - cum.iloc[-1 - lag - k].to_numpy()) - \
           np.expm1(cum_mkt.iloc[-1 - lag] - cum_mkt.iloc[-1 - lag - k])
    return np.nan_to_num(now - prev, nan=0.0)


def _shift_last(cum: pd.DataFrame, cum_mkt: pd.Series, w: int, lag: int) -> np.ndarray:
    """lag 日前的 w 窗口超额收益（用于 RS 变化率）。"""
    T = len(cum)
    if T <= w + lag:
        return np.zeros(cum.shape[1])
    k = min(w, T - 1 - lag)
    return np.nan_to_num(
        np.expm1(cum.iloc[-1 - lag].to_numpy() - cum.iloc[-1 - lag - k].to_numpy())
        - np.expm1(cum_mkt.iloc[-1 - lag] - cum_mkt.iloc[-1 - lag - k]), nan=0.0)
