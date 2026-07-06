"""低吸入场信号引擎 — 检测4种低吸补涨形态。

替代原来的追涨突破信号，专注于低吸入场点：
1. 缩量回踩MA20 (ma20_pullback)
2. 放量企稳 (volume_stabilize)
3. 二浪低吸 (wave2_dip)
4. 底部反转 (bottom_reversal)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, atr, adx, rsi, macd, macd_hist, bollinger, kdj,
    volume_ratio, natr, max_drawdown, winsorize,
)


@dataclass
class BuySignalResult:
    ts_code: str
    etf_code: str = ""
    signal_type: str = ""
    signal_strength: float = 0.0
    entry_price: float = 0.0
    atr_stop: float = 0.0
    target_price: float = 0.0
    ma20_pullback_score: float = 0.0
    volume_stabilize_score: float = 0.0
    wave2_dip_score: float = 0.0
    bottom_reversal_score: float = 0.0


class BuyEngine:
    """低吸入场信号引擎。

    检测4种低吸形态，输出信号强度和入场/止损/目标价。
    核心理念：不追涨，在回调企稳处低吸。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('buy_signal', {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self,
               stock_data: Dict[str, pd.DataFrame],
               etf_data: Dict[str, pd.DataFrame] = None,
               leader_results: Dict[str, List] = None,
               etf_scores: Dict[str, float] = None) -> Dict[str, BuySignalResult]:
        """检测每只个股的低吸入场信号。"""
        if not stock_data:
            logger.warning("stock_data is empty, returning {}")
            return {}

        etf_data = etf_data or {}
        leader_results = leader_results or {}
        etf_scores = etf_scores or {}

        lookback = self.cfg.get('lookback', 120)
        min_rows = self.cfg.get('min_rows', 60)

        # Build ts_code → etf_code mapping
        ts_to_etf: Dict[str, str] = {}
        for etf_code, leaders in leader_results.items():
            for leader in leaders:
                ts_to_etf[leader.ts_code] = etf_code

        # Per-stock signal computation
        raw_list: List[dict] = []
        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue
            if len(df) < min_rows:
                continue
            try:
                metrics = self._compute_raw_signals(ts_code, df, lookback)
                if metrics is not None:
                    raw_list.append(metrics)
            except Exception as exc:
                logger.debug(f"Error computing buy signals for {ts_code}: {exc}")
                continue

        if not raw_list:
            logger.warning("No buy signals computed, returning {}")
            return {}

        # Cross-sectional normalization
        ma20_raw = np.array([m['ma20_pullback'] for m in raw_list], dtype=np.float64)
        vs_raw = np.array([m['volume_stabilize'] for m in raw_list], dtype=np.float64)
        w2_raw = np.array([m['wave2_dip'] for m in raw_list], dtype=np.float64)
        br_raw = np.array([m['bottom_reversal'] for m in raw_list], dtype=np.float64)

        ma20_s = self._to_score(winsorize(ma20_raw, 0.01))
        vs_s = self._to_score(winsorize(vs_raw, 0.01))
        w2_s = self._to_score(winsorize(w2_raw, 0.01))
        br_s = self._to_score(winsorize(br_raw, 0.01))

        all_scores = np.column_stack([ma20_s, vs_s, w2_s, br_s])
        best_idx = np.argmax(all_scores, axis=1)
        best_values = np.max(all_scores, axis=1)

        signal_names = np.array([
            'ma20_pullback', 'volume_stabilize', 'wave2_dip', 'bottom_reversal',
        ])

        results: Dict[str, BuySignalResult] = {}
        for i, m in enumerate(raw_list):
            b_idx = int(best_idx[i])
            strength = float(best_values[i])
            etf_code = ts_to_etf.get(m['ts_code'], '')

            results[m['ts_code']] = BuySignalResult(
                ts_code=m['ts_code'],
                etf_code=etf_code,
                signal_type=str(signal_names[b_idx]),
                signal_strength=round(strength, 2),
                entry_price=round(float(m['entry_price']), 2),
                atr_stop=round(float(m['atr_stop']), 2),
                target_price=round(float(m['target_price']), 2),
                ma20_pullback_score=round(float(ma20_s[i]), 2),
                volume_stabilize_score=round(float(vs_s[i]), 2),
                wave2_dip_score=round(float(w2_s[i]), 2),
                bottom_reversal_score=round(float(br_s[i]), 2),
            )

        logger.info(f"BuyEngine detected {len(results)} low-absorption buy signals")
        return results

    # ------------------------------------------------------------------
    # 单只个股原始信号计算
    # ------------------------------------------------------------------

    def _compute_raw_signals(self, ts_code: str, df: pd.DataFrame,
                             lookback: int) -> Optional[dict]:
        """对单只个股计算4种低吸入场信号的原始值。"""
        close = np.asarray(df['close'].values, dtype=np.float64)
        high = np.asarray(df['high'].values, dtype=np.float64)
        low = np.asarray(df['low'].values, dtype=np.float64)
        open_ = np.asarray(df.get('open', df['close']).values, dtype=np.float64)
        volume = np.asarray(df['vol'].values, dtype=np.float64)
        n = len(close)

        # 公共指标
        ema5 = ema(close, 5)
        ema10 = ema(close, 10)
        ema20 = ema(close, 20)
        ema60 = ema(close, 60)
        atr14 = atr(high, low, close, 14)
        vr = volume_ratio(volume, 20)
        rsi6 = rsi(close, 6)
        rsi14 = rsi(close, 14)
        macd_h = macd_hist(close)

        entry_price = float(close[-1])
        atr_last = float(atr14[-1]) if n > 0 and np.isfinite(atr14[-1]) else 0.0
        # 低吸策略：止损放宽到3倍ATR（给更多空间），止盈4倍ATR
        atr_stop = entry_price - 3.0 * atr_last if atr_last > 1e-10 else entry_price * 0.92
        target_price = entry_price + 4.0 * atr_last if atr_last > 1e-10 else entry_price * 1.12

        # ---- 1. 缩量回踩MA20 (ma20_pullback) ----
        ma20_pullback_raw = self._calc_ma20_pullback(
            close, ema20, ema60, vr, rsi6, high, n,
        )

        # ---- 2. 放量企稳 (volume_stabilize) ----
        vs_raw = self._calc_volume_stabilize(
            close, open_, vr, macd_h, ema20, ema60, n,
        )

        # ---- 3. 二浪低吸 (wave2_dip) ----
        w2_raw = self._calc_wave2_dip(
            close, high, low, ema20, ema60, vr, rsi6, n,
        )

        # ---- 4. 底部反转 (bottom_reversal) ----
        br_raw = self._calc_bottom_reversal(
            close, open_, high, low, vr, rsi6, rsi14, ema60, n,
        )

        metrics = {
            'ts_code': ts_code,
            'entry_price': entry_price,
            'atr_stop': atr_stop,
            'target_price': target_price,
            'ma20_pullback': ma20_pullback_raw,
            'volume_stabilize': vs_raw,
            'wave2_dip': w2_raw,
            'bottom_reversal': br_raw,
        }
        return metrics

    # ------------------------------------------------------------------
    # 各低吸信号计算
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_ma20_pullback(close: np.ndarray, ema20: np.ndarray,
                            ema60: np.ndarray, vr: np.ndarray,
                            rsi6: np.ndarray, high: np.ndarray,
                            n: int) -> float:
        """缩量回踩MA20信号。

        条件：
        - 中期趋势向上（EMA20 > EMA60）
        - 股价回踩到EMA20附近（±3%）
        - 量比 < 0.9（缩量）
        - RSI6 < 55（未超买）
        - 低于近期高点（不是追涨）
        """
        if n < 30:
            return 0.0

        c = float(close[-1])
        e20 = float(ema20[-1])
        e60 = float(ema60[-1])
        v = float(vr[-1])
        r = float(rsi6[-1]) if np.isfinite(rsi6[-1]) else 50.0

        # 趋势向上
        uptrend = float(e20 > e60 and ema60[-1] > ema60[-max(5, n)])

        # 距EMA20距离（-3%~+3%最佳）
        dist_pct = (c / max(e20, 1e-10) - 1.0) * 100.0
        if -5.0 <= dist_pct <= 5.0:
            near_ema = float(np.exp(-(dist_pct ** 2) / (2.0 * 3.0 ** 2)))
        else:
            near_ema = 0.0

        # 缩量（量比 < 0.9）
        vol_score = float(max(0.0, 1.0 - v / 0.9)) if v < 0.9 else 0.0

        # RSI未超买
        rsi_ok = float(r < 55.0)
        rsi_score = float(np.exp(-((r - 45.0) ** 2) / (2.0 * 15.0 ** 2))) if r < 55.0 else 0.0

        # 低于近期高点
        recent_high = float(np.max(high[-20:])) if n >= 20 else float(high[-1])
        below_high = float(c < recent_high * 0.97)

        raw = uptrend * near_ema * (0.3 + 0.4 * vol_score + 0.3 * rsi_score) * (0.5 + 0.5 * below_high)
        return float(raw)

    @staticmethod
    def _calc_volume_stabilize(close: np.ndarray, open_: np.ndarray,
                               vr: np.ndarray, macd_h: np.ndarray,
                               ema20: np.ndarray, ema60: np.ndarray,
                               n: int) -> float:
        """放量企稳信号。

        条件：
        - 前期下跌或横盘
        - 量比从低位回升到0.8-1.2（温和放量）
        - 收阳线（close > open）
        - MACD柱线收窄或即将翻红
        - 站上或接近EMA20
        """
        if n < 30:
            return 0.0

        c = float(close[-1])
        o = float(open_[-1])
        v = float(vr[-1])
        v_prev = float(np.mean(vr[-6:-1])) if n >= 6 else v
        mh = float(macd_h[-1]) if np.isfinite(macd_h[-1]) else 0.0
        mh_prev = float(macd_h[-3]) if n >= 3 and np.isfinite(macd_h[-3]) else 0.0

        # 量比回升（从低位）
        vol_recover = float(v > v_prev * 0.95 and 0.7 <= v <= 1.3)
        vol_score = float(np.exp(-((v - 1.0) ** 2) / (2.0 * 0.3 ** 2)))

        # 收阳线
        yang_xian = float(c > o)

        # MACD柱线收窄（从负向0靠近）或即将翻红
        macd_narrowing = float(abs(mh) < abs(mh_prev)) if mh_prev != 0 else 0.0
        macd_near_zero = float(abs(mh) < 0.05) if mh != 0 else 0.0

        # 站上或接近EMA20
        e20 = float(ema20[-1])
        dist_ma20 = abs(c / max(e20, 1e-10) - 1.0) * 100.0
        near_ma20 = float(np.exp(-(dist_ma20 ** 2) / (2.0 * 6.0 ** 2)))

        # 前期下跌（5日前收盘 > 当前收盘 * 0.95）
        prev_close = float(close[-6]) if n >= 6 else c
        was_declining = float(prev_close > c * 0.97)

        raw = (vol_recover * 0.2 + vol_score * 0.2) * yang_xian * (
            0.3 + 0.3 * macd_narrowing + 0.2 * macd_near_zero + 0.2 * near_ma20
        ) * (0.5 + 0.5 * was_declining)
        return float(raw)

    @staticmethod
    def _calc_wave2_dip(close: np.ndarray, high: np.ndarray,
                        low: np.ndarray, ema20: np.ndarray,
                        ema60: np.ndarray, vr: np.ndarray,
                        rsi6: np.ndarray, n: int) -> float:
        """二浪低吸信号。

        条件：
        - 第一波上涨 ≥ 20%（30日内）
        - 从高点回调5%-15%
        - 量比0.6-0.9（缩量回调）
        - RSI6 < 50（未超买）
        - 价格在EMA20和EMA60之间
        """
        if n < 50:
            return 0.0

        c = float(close[-1])

        # 第一波上涨
        lookback = min(30, n)
        wave_high = float(np.max(high[-lookback:]))
        wave_low = float(np.min(low[-lookback:]))
        wave_pct = (wave_high / max(wave_low, 1e-10) - 1.0) * 100.0

        if wave_pct < 20.0:
            return 0.0

        # 回调幅度
        pullback_pct = (c / max(wave_high, 1e-10) - 1.0) * 100.0
        if -15.0 <= pullback_pct <= -3.0:
            pullback_score = float(np.exp(-((pullback_pct + 8.0) ** 2) / (2.0 * 4.0 ** 2)))
        else:
            return 0.0

        # 量比
        v = float(vr[-1])
        if 0.5 <= v <= 0.95:
            vol_score = float(np.exp(-((v - 0.75) ** 2) / (2.0 * 0.2 ** 2)))
        else:
            vol_score = 0.0

        # RSI
        r = float(rsi6[-1]) if np.isfinite(rsi6[-1]) else 50.0
        rsi_ok = float(r < 50.0)

        # 价格在EMA20和EMA60之间
        e20 = float(ema20[-1])
        e60 = float(ema60[-1])
        between_ma = float(e60 <= c <= e20 * 1.05) if e20 > e60 else 0.0

        raw = pullback_score * (0.3 + 0.4 * vol_score + 0.3 * rsi_ok) * (0.5 + 0.5 * between_ma)
        return float(raw)

    @staticmethod
    def _calc_bottom_reversal(close: np.ndarray, open_: np.ndarray,
                              high: np.ndarray, low: np.ndarray,
                              vr: np.ndarray, rsi6: np.ndarray,
                              rsi14: np.ndarray, ema60: np.ndarray,
                              n: int) -> float:
        """底部反转信号。

        条件：
        - RSI6曾低于35（超卖）后回升
        - 量比 > 1.0（放量）
        - 收阳线
        - 接近EMA60（底部支撑）
        """
        if n < 40:
            return 0.0

        c = float(close[-1])
        o = float(open_[-1])
        v = float(vr[-1])

        # RSI从超卖回升
        r6 = float(rsi6[-1]) if np.isfinite(rsi6[-1]) else 50.0
        r6_prev = float(rsi6[-5]) if n >= 5 and np.isfinite(rsi6[-5]) else 50.0
        r14 = float(rsi14[-1]) if np.isfinite(rsi14[-1]) else 50.0

        was_oversold = float(r6_prev < 35.0 or r14 < 40.0)
        rsi_recovering = float(r6 > r6_prev)

        # 放量
        vol_surge = float(v > 1.0)
        vol_score = float(min(v / 2.0, 1.0)) if v > 1.0 else 0.0

        # 收阳线
        yang_xian = float(c > o)

        # 接近EMA60
        e60 = float(ema60[-1])
        dist_e60 = abs(c / max(e60, 1e-10) - 1.0) * 100.0
        near_e60 = float(np.exp(-(dist_e60 ** 2) / (2.0 * 8.0 ** 2)))

        # 近期跌幅（20日内跌超10%）
        if n >= 20:
            recent_ret = (c / max(float(close[-20]), 1e-10) - 1.0) * 100.0
            dropped = float(recent_ret < -5.0)
        else:
            dropped = 0.0

        raw = was_oversold * rsi_recovering * vol_surge * yang_xian * (
            0.3 + 0.3 * vol_score + 0.2 * near_e60 + 0.2 * dropped
        )
        return float(raw)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _to_score(arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float64)
        valid = a[np.isfinite(a)]
        if len(valid) == 0:
            return np.full_like(a, 50.0)
        mn, mx = np.nanmin(a), np.nanmax(a)
        if mx <= mn or not np.isfinite(mx - mn):
            return np.full_like(a, 50.0)
        return np.clip((a - mn) / (mx - mn) * 100.0, 0.0, 100.0)
