# -*- coding: utf-8 -*-
"""测试用的合成行情构造器（不触碰任何真实数据库）

所有用例都基于人造 K 线，避免测试依赖 23GB 的 stock_data.db。
"""
import os
import sys

import numpy as np
import pandas as pd

_SOLO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SOLO not in sys.path:
    sys.path.insert(0, _SOLO)

from hve_v1 import load_config                      # noqa: E402
from hve_v1.indicators import compute_indicators    # noqa: E402
from hve_v1.event import annotate_events            # noqa: E402
from hve_v1.state import walk_stock                 # noqa: E402


def make_df(close, open_=None, high=None, low=None, vol=None, amount=None,
            start='20250106', total_mv=800000.0, adj=1.0) -> pd.DataFrame:
    """构造单股日线 DataFrame（daily_cache 口径，含指标计算所需的最小列集）"""
    close = np.asarray(close, dtype=float)
    n = len(close)
    open_ = np.asarray(open_, dtype=float) if open_ is not None \
        else np.concatenate([[close[0]], close[:-1]])
    high = np.asarray(high, dtype=float) if high is not None \
        else np.maximum(open_, close) * 1.002
    low = np.asarray(low, dtype=float) if low is not None \
        else np.minimum(open_, close) * 0.998
    vol = np.asarray(vol, dtype=float) if vol is not None else np.full(n, 1000.0)
    amount = np.asarray(amount, dtype=float) if amount is not None else vol * close
    pre_close = np.concatenate([[close[0]], close[:-1]])

    dates = pd.bdate_range(start, periods=n).strftime('%Y%m%d')
    return pd.DataFrame({
        'ts_code': '600000.SH',
        'trade_date': dates,
        'open': open_, 'high': high, 'low': low, 'close': close,
        'pre_close': pre_close,
        'pct_chg': (close / pre_close - 1.0) * 100.0,
        'vol': vol, 'amount': amount,
        'total_mv': total_mv,
        'adj_factor': adj,
    })


def analyze(df: pd.DataFrame, code: str = '600000.SH', cfg: dict = None, regime_fn=None):
    """跑完整链路：指标 → 事件标注 → 状态机。返回 (df, events, records)"""
    cfg = cfg or load_config()
    d = compute_indicators(df, cfg)
    d, ev = annotate_events(d, code, cfg)
    recs = walk_stock(d, code, cfg, ev, regime_fn=regime_fn)
    return d, ev, recs


def nan_eq(a, b) -> bool:
    """NaN 感知的相等比较（NaN == NaN 视为 True）"""
    if isinstance(a, float) and isinstance(b, float):
        if np.isnan(a) and np.isnan(b):
            return True
    try:
        return bool(a == b)
    except Exception:
        return False


def recs_equal(xs: list, ys: list) -> bool:
    """逐条逐字段比较 walk_stock 记录（NaN 感知），用于 §44 未来数据变更测试"""
    if len(xs) != len(ys):
        return False
    for x, y in zip(xs, ys):
        if set(x) != set(y):
            return False
        for k in x:
            if not nan_eq(x[k], y[k]):
                return False
    return True


def flat(n: int, price: float = 10.0, vol: float = 1000.0):
    """n 天横盘基线"""
    return [price] * n, [vol] * n


def sig_types(recs):
    return [r['signal_type'] for r in recs]


def by_date(recs, trade_date):
    for r in recs:
        if r['trade_date'] == trade_date:
            return r
    return None
