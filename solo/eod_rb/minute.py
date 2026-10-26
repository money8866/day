# -*- coding: utf-8 -*-
"""
EOD-RB 分钟级数据模块。

数据源：本机通达信 .lc5（5分钟线，mootdx.reader.Reader 本地读取，不复权）。
实际覆盖约 2026-02-24 ~ 2026-05-22（约 60 个交易日）。此为「第三层尾盘再启动」的唯一合规数据源；
缺失时严禁用日线伪造尾盘信号，函数返回 None 并在上层输出 NO TRADE / 数据不足。
"""
import os
from typing import Optional

import pandas as pd

from .config import TDX_PATH

_MINUTE_CACHE = {}  # ts_code -> DataFrame|None，进程内缓存（含负缓存）


def _reader():
    from mootdx.reader import Reader
    return Reader.factory(market='std', tdxdir=TDX_PATH)


def ts_to_tdx_sym(ts_code: str) -> Optional[str]:
    """600000.SH -> sh600000 ；000001.SZ -> sz000001"""
    try:
        code, suffix = ts_code.split('.')
    except (ValueError, AttributeError):
        return None
    if suffix == 'SH':
        return 'sh' + code
    if suffix == 'SZ':
        return 'sz' + code
    return None


def load_minute(ts_code: str) -> Optional[pd.DataFrame]:
    """加载单股全部5分钟K线（本地文件，DatetimeIndex）。带进程内缓存（含负缓存）。

    返回列：open/high/low/close/amount(元)/volume(股)，并派生 day/hhmm。
    无本地文件或读取失败 → None。
    """
    if ts_code in _MINUTE_CACHE:
        return _MINUTE_CACHE[ts_code]
    out = _load_minute_uncached(ts_code)
    _MINUTE_CACHE[ts_code] = out
    return out


def _load_minute_uncached(ts_code: str) -> Optional[pd.DataFrame]:
    sym = ts_to_tdx_sym(ts_code)
    if not sym:
        return None
    try:
        df = _reader().fzline(symbol=sym)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    df = df.reset_index()
    if 'date' not in df.columns:
        return None
    df['day'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df['hhmm'] = pd.to_datetime(df['date']).dt.strftime('%H:%M')
    for c in ('open', 'high', 'low', 'close', 'amount', 'volume'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.sort_values('date').reset_index(drop=True)


def build_tail_anchor(ts_code: str, trade_date: str) -> Optional[dict]:
    """从 T 日5分钟K线重建尾盘锚点（严格只用 <=14:55 的当日分钟数据）。

    返回 dict：
      vwap_until(hhmm)  —— 截至某时点的当日累计 VWAP（成交额/成交量）
      price_1430 / price_1455 —— 14:30 / 14:55（或之前最近一根）K线收盘价
      range_1400_1430 —— (low, high) 14:00~14:30 整理区间
      tail_volume —— (14:30, 14:55] 区间成交量（股）
      day_high / day_low / day_close —— 当日（截至14:55）
      bars —— 当日分钟内 bar 列表（用于下一根可成交价）
    """
    m = load_minute(ts_code)
    if m is None:
        return None
    day = m[m['day'] == str(trade_date)]
    if day.empty:
        return None
    day = day[day['hhmm'] <= '14:56'].sort_values('hhmm')  # 只保留到 14:55 收盘
    if day.empty:
        return None

    cum_vol = day['volume'].fillna(0).cumsum()
    cum_amt = day['amount'].fillna(0).cumsum()
    vwap = (cum_amt / cum_vol.replace(0, 1)).where(cum_vol > 0)

    def at_or_before(hhmm):
        sub = day[day['hhmm'] <= hhmm]
        return None if sub.empty else float(sub.iloc[-1]['close'])

    p1430 = at_or_before('14:30')
    p1455 = day.iloc[-1]['close'] if day['hhmm'].max() <= '14:55' else at_or_before('14:55')
    if p1430 is None or p1455 is None or p1430 <= 0:
        return None

    rng = day[(day['hhmm'] > '14:00') & (day['hhmm'] <= '14:30')]
    range_1400_1430 = (float(rng['low'].min()), float(rng['high'].max())) if not rng.empty else (p1430, p1430)

    tail = day[(day['hhmm'] > '14:30') & (day['hhmm'] <= '14:55')]
    tail_vol = float(tail['volume'].fillna(0).sum())

    vwap_at_1455 = float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else None

    # 信号触发后「下一根可成交价」：15:00 的 bar 收盘价（若有），否则 14:55 收盘
    all_day = m[m['day'] == str(trade_date)].sort_values('hhmm')
    nxt = all_day[all_day['hhmm'] > '14:55']
    exec_next = float(nxt.iloc[0]['close']) if not nxt.empty else float(p1455)

    return {
        "trade_date": str(trade_date),
        "price_1430": p1430,
        "price_1455": float(p1455),
        "vwap_1455": vwap_at_1455,
        "range_1400_1430": range_1400_1430,
        "tail_volume": tail_vol,
        "tail_window_return": float(p1455) / p1430 - 1,
        "day_high": float(day['high'].max()),
        "day_low": float(day['low'].min()),
        "day_close_1455": float(p1455),
        "exec_next": exec_next,
        "bars": day,
    }


def tail_volume_benchmark(ts_code: str, trade_date: str, lookback: int = 5) -> Optional[float]:
    """过去 N 个交易日相同时间窗口 (14:30,14:55] 平均成交量（股）。用相同窗口比较，避免归一化偏差。"""
    m = load_minute(ts_code)
    if m is None:
        return None
    days = sorted(m['day'].unique())
    if str(trade_date) not in days:
        return None
    idx = list(days).index(str(trade_date))
    prev = [d for d in days[max(0, idx - lookback):idx]]
    vals = []
    for d in prev:
        dd = m[(m['day'] == d) & (m['hhmm'] > '14:30') & (m['hhmm'] <= '14:55')]
        if not dd.empty:
            vals.append(float(dd['volume'].fillna(0).sum()))
    if not vals:
        return None
    return float(sum(vals) / len(vals))