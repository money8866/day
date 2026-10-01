# -*- coding: utf-8 -*-
"""HVE V1 可交易性标记（§46）

涨停股票**可以**产生 HVE EVENT（研究口径不因此改变），
但「如果当日无法合理成交，不默认产生实际 BUY」——
本模块只给出 tradability_flag，是否过滤由上层决定（signal 与 executable 字段分离）。
"""
import numpy as np
import pandas as pd

LIQUID = 'LIQUID'
LIMIT_UP_RISK = 'LIMIT_UP_RISK'
ONE_PRICE_BOARD = 'ONE_PRICE_BOARD'


def tradability_flag(d: pd.DataFrame, i: int, ts_code: str) -> str:
    """§46 tradability_flag：LIQUID / LIMIT_UP_RISK / ONE_PRICE_BOARD"""
    from hvt_bull.engine import _is_limit_up_close

    try:
        close = float(pd.to_numeric(d['close'].iloc[i], errors='coerce'))
        pre_close = float(pd.to_numeric(d['pre_close'].iloc[i], errors='coerce'))
        high = float(pd.to_numeric(d['high'].iloc[i], errors='coerce'))
        low = float(pd.to_numeric(d['low'].iloc[i], errors='coerce'))
    except Exception:
        return LIQUID

    if not np.isfinite(close) or not np.isfinite(pre_close):
        return LIQUID
    is_lu = _is_limit_up_close(close, pre_close, ts_code)
    if is_lu and np.isfinite(high) and np.isfinite(low) and high == low:
        return ONE_PRICE_BOARD
    if is_lu:
        return LIMIT_UP_RISK
    return LIQUID
