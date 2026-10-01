# -*- coding: utf-8 -*-
"""HVE V1 市场环境（§31/§32）

§31 只生成三态：MARKET_OK / MARKET_NEUTRAL / MARKET_RISK
     至少参考：指数 MA20、指数 MA60、市场 Breadth、成交额。
§32 MARKET_RISK 时**不删除信号**，改 signal = CONDITIONAL（避免污染 HVE 自身 Alpha 研究）。

实现裁决（计划 §7.2）：V1 用 index_daily_cache + daily_cache 截面轻量自算，
不调用 market_regime_v3（7 层 Pipeline，开销大且会反向耦合）。
升级接口：把 MarketEnv 换成任意实现同一 regime(trade_date) -> str 的对象即可（MARKET_PROVIDER）。
"""
import numpy as np
import pandas as pd

MARKET_OK = 'MARKET_OK'
MARKET_NEUTRAL = 'MARKET_NEUTRAL'
MARKET_RISK = 'MARKET_RISK'

# 升级钩子：设为 callable(cfg) -> object with .regime(trade_date)，日后可切到 market_regime_v3
MARKET_PROVIDER = None


class MarketEnv:
    """轻量三态市场环境（PIT：只用 <= trade_date 的指数/截面数据）"""

    def __init__(self, data, cfg: dict):
        self.data = data
        self.cfg = cfg.get('market', {}) or {}
        self._series = None

    def _close_series(self):
        if self._series is None:
            code = self.cfg.get('index', '000300.SH')
            df = self.data.index_daily(code, end_date='29991231')
            self._series = df.set_index('trade_date')['close'] if df is not None and not df.empty \
                else pd.Series(dtype=float)
        return self._series

    def regime(self, trade_date: str) -> str:
        s = self._close_series()
        if s.empty:
            return MARKET_NEUTRAL
        hist = s[s.index <= str(trade_date)]
        fast = int(self.cfg.get('ma_fast', 20))
        slow = int(self.cfg.get('ma_slow', 60))
        if len(hist) < slow:
            return MARKET_NEUTRAL
        close = float(hist.iloc[-1])
        ma_f = float(hist.tail(fast).mean())
        ma_s = float(hist.tail(slow).mean())

        breadth_ok = breadth_risk = False
        if self.cfg.get('use_breadth', True):
            b = self.data.breadth(trade_date)
            ur = b.get('up_ratio', np.nan)
            if np.isfinite(ur) and b.get('n', 0) > 0:
                breadth_ok = ur >= float(self.cfg.get('breadth_ok_min', 0.45))
                breadth_risk = ur < float(self.cfg.get('breadth_risk_max', 0.30))

        if close > ma_f and ma_f > ma_s and breadth_ok:
            return MARKET_OK
        if close < ma_s or breadth_risk:
            return MARKET_RISK
        return MARKET_NEUTRAL

    def describe(self, trade_date: str) -> dict:
        s = self._close_series()
        out = {'regime': self.regime(trade_date)}
        if not s.empty:
            hist = s[s.index <= str(trade_date)]
            fast = int(self.cfg.get('ma_fast', 20))
            slow = int(self.cfg.get('ma_slow', 60))
            if len(hist) >= slow:
                out['index'] = self.cfg.get('index', '000300.SH')
                out['close'] = float(hist.iloc[-1])
                out['ma_fast'] = float(hist.tail(fast).mean())
                out['ma_slow'] = float(hist.tail(slow).mean())
        b = self.data.breadth(trade_date)
        out['breadth_up_ratio'] = b.get('up_ratio')
        out['breadth_n'] = b.get('n')
        return out


def apply_regime(signal: str, regime: str) -> str:
    """§32 市场环境与信号关系：RISK 不删信号，改 CONDITIONAL"""
    if signal == 'BUY' and regime == MARKET_RISK:
        return 'CONDITIONAL'
    return signal
