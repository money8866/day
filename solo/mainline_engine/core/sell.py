"""卖出信号引擎 — 检测6种卖出形态。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, atr, adx, rsi, bollinger, kdj,
    rank_score, normalize, zscore, winsorize,
    max_drawdown, rolling_corr, beta as rolling_beta,
    volume_ratio, slope, natr, new_high_count,
    consecutive_up_days, above_ema_days, future_return,
)
from mainline_engine.core.buy import BuySignalResult


@dataclass
class SellSignalResult:
    ts_code: str
    etf_code: str = ""
    signal_type: str = ""
    signal_strength: float = 0.0
    exit_price: float = 0.0
    reason: str = ""
    ema20_break_score: float = 0.0
    atr_stop_score: float = 0.0
    etf_weaken_score: float = 0.0
    theme_cool_score: float = 0.0
    leader_change_score: float = 0.0
    volume_stagnation_score: float = 0.0


@dataclass
class ThemeResult:
    """主题数据简化结构，由 theme_rotation.py 输出。"""
    theme: str = ""
    heat_score: float = 50.0
    composite_score: float = 50.0


class SellEngine:
    """卖出信号引擎。

    对已有买入信号的个股检测6种卖出形态（EMA20跌破、ATR止损、ETF走弱、
    主题降温、龙头切换、放量滞涨），输出0-100信号强度。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('sell_signal', {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self,
               stock_data: Dict[str, pd.DataFrame],
               buy_results: Dict[str, BuySignalResult],
               etf_data: Dict[str, pd.DataFrame],
               etf_scores: Dict[str, float],
               theme_data: Dict[str, ThemeResult],
               leader_results: Dict[str, List]) -> Dict[str, SellSignalResult]:
        """检测每只有买入信号的个股的卖出信号。

        Parameters
        ----------
        stock_data : dict
            {ts_code: DataFrame}，需含列 [trade_date, open, high, low, close, vol]。
        buy_results : dict
            {ts_code: BuySignalResult}，买入信号结果。
        etf_data : dict
            {ts_code: DataFrame}，ETF日线数据。
        etf_scores : dict
            {etf_code: float}，ETF趋势得分。
        theme_data : dict
            {theme_name: ThemeResult}，主题热度数据。
        leader_results : dict
            {etf_code: [LeaderResult, ...]}，龙头评分结果。

        Returns
        -------
        dict[str, SellSignalResult]
            {ts_code: SellSignalResult}
        """
        if not stock_data or not buy_results:
            logger.warning("stock_data or buy_results is empty, returning {}")
            return {}

        etf_data = etf_data or {}
        etf_scores = etf_scores or {}
        theme_data = theme_data or {}
        leader_results = leader_results or {}

        min_rows = self.cfg.get('min_rows', 20)

        # Build ETF → ts_code list from leader_results
        etf_to_codes: Dict[str, List[str]] = {}
        for etf_code, leaders in leader_results.items():
            codes = [r.ts_code for r in leaders]
            etf_to_codes[etf_code] = codes

        # Build ts_code → leader score map
        ts_leader_score: Dict[str, float] = {}
        for etf_code, leaders in leader_results.items():
            for r in leaders:
                score = getattr(r, 'leader_score', getattr(r, 'composite_score', 50.0))
                ts_leader_score[r.ts_code] = float(score)

        # Build ts_code → etf_code from buy_results
        ts_to_etf: Dict[str, str] = {}
        for ts_code, br in buy_results.items():
            if br.etf_code:
                ts_to_etf[ts_code] = br.etf_code

        # Per-stock raw metric computation
        raw_list: List[dict] = []
        for ts_code, br in buy_results.items():
            df = stock_data.get(ts_code)
            if df is None or df.empty or len(df) < min_rows:
                continue
            try:
                metrics = self._compute_raw_signals(
                    ts_code=ts_code,
                    df=df,
                    buy_result=br,
                    etf_data=etf_data,
                    etf_scores=etf_scores,
                    theme_data=theme_data,
                    etf_to_codes=etf_to_codes,
                    ts_leader_score=ts_leader_score,
                    ts_to_etf=ts_to_etf,
                )
                if metrics is not None:
                    raw_list.append(metrics)
            except Exception as exc:
                logger.debug(f"Error computing sell signals for {ts_code}: {exc}")
                continue

        if not raw_list:
            logger.warning("No sell signals computed, returning {}")
            return {}

        # Cross-sectional normalization across all stocks with buy signals
        n = len(raw_list)
        ema20_brk_raw = np.array([m['ema20_break'] for m in raw_list], dtype=np.float64)
        atr_raw = np.array([m['atr_stop'] for m in raw_list], dtype=np.float64)
        etf_wk_raw = np.array([m['etf_weaken'] for m in raw_list], dtype=np.float64)
        theme_c_raw = np.array([m['theme_cool'] for m in raw_list], dtype=np.float64)
        leader_ch_raw = np.array([m['leader_change'] for m in raw_list], dtype=np.float64)
        vol_stag_raw = np.array([m['volume_stagnation'] for m in raw_list], dtype=np.float64)

        ema20_brk_s = self._to_score(winsorize(ema20_brk_raw, 0.01))
        atr_s = self._to_score(winsorize(atr_raw, 0.01))
        etf_wk_s = self._to_score(winsorize(etf_wk_raw, 0.01))
        theme_c_s = self._to_score(winsorize(theme_c_raw, 0.01))
        leader_ch_s = self._to_score(winsorize(leader_ch_raw, 0.01))
        vol_stag_s = self._to_score(winsorize(vol_stag_raw, 0.01))

        all_scores = np.column_stack([
            ema20_brk_s, atr_s, etf_wk_s, theme_c_s, leader_ch_s, vol_stag_s,
        ])
        best_idx = np.argmax(all_scores, axis=1)
        best_values = np.max(all_scores, axis=1)

        signal_names = np.array([
            'ema20_break', 'atr_stop', 'etf_weaken',
            'theme_cool', 'leader_change', 'volume_stagnation',
        ])

        signal_reasons = [
            'Price closed below EMA20',
            'Price hit ATR stop loss',
            'ETF trend weakened',
            'Theme heat cooled',
            'Leader position changed',
            'Volume stagnation with price flat/declining',
        ]

        results: Dict[str, SellSignalResult] = {}
        for i, m in enumerate(raw_list):
            b_idx = int(best_idx[i])
            strength = float(best_values[i])
            etf_code = m.get('etf_code', '')

            results[m['ts_code']] = SellSignalResult(
                ts_code=m['ts_code'],
                etf_code=etf_code,
                signal_type=str(signal_names[b_idx]),
                signal_strength=round(strength, 2),
                exit_price=round(float(m['exit_price']), 2),
                reason=signal_reasons[b_idx],
                ema20_break_score=round(float(ema20_brk_s[i]), 2),
                atr_stop_score=round(float(atr_s[i]), 2),
                etf_weaken_score=round(float(etf_wk_s[i]), 2),
                theme_cool_score=round(float(theme_c_s[i]), 2),
                leader_change_score=round(float(leader_ch_s[i]), 2),
                volume_stagnation_score=round(float(vol_stag_s[i]), 2),
            )

        logger.info(f"SellEngine detected {len(results)} sell signals")
        return results

    # ------------------------------------------------------------------
    # 单只个股原始信号计算（全向量化）
    # ------------------------------------------------------------------

    def _compute_raw_signals(self,
                             ts_code: str,
                             df: pd.DataFrame,
                             buy_result: BuySignalResult,
                             etf_data: Dict[str, pd.DataFrame],
                             etf_scores: Dict[str, float],
                             theme_data: Dict[str, ThemeResult],
                             etf_to_codes: Dict[str, List[str]],
                             ts_leader_score: Dict[str, float],
                             ts_to_etf: Dict[str, str]) -> Optional[dict]:
        """对单只个股计算6种卖出信号的原始值。"""
        close = np.asarray(df['close'].values, dtype=np.float64)
        high = np.asarray(df['high'].values, dtype=np.float64)
        low = np.asarray(df['low'].values, dtype=np.float64)
        volume = np.asarray(df['vol'].values, dtype=np.float64)
        n = len(close)

        etf_code = ts_to_etf.get(ts_code, buy_result.etf_code)

        # Common indicators
        ema20 = ema(close, 20)
        atr14 = atr(high, low, close, 14)
        vr = volume_ratio(volume, 20)

        exit_price = float(close[-1])

        # ---- 1. EMA20 Break ----
        ema20_break_raw = self._calc_ema20_break(close, ema20, n)

        # ---- 2. ATR Stop Loss ----
        atr_stop_raw = self._calc_atr_stop(close, atr14, buy_result, n)

        # ---- 3. ETF Weakens ----
        etf_weaken_raw = self._calc_etf_weaken(etf_code, etf_data, etf_scores)

        # ---- 4. Theme Cools ----
        theme_cool_raw = self._calc_theme_cool(etf_code, theme_data, etf_scores)

        # ---- 5. Leader Changes ----
        leader_change_raw = self._calc_leader_change(
            ts_code, etf_code, etf_to_codes, ts_leader_score,
        )

        # ---- 6. Volume Stagnation ----
        vol_stagnation_raw = self._calc_volume_stagnation(close, volume, vr, n)

        metrics = {
            'ts_code': ts_code,
            'etf_code': etf_code,
            'exit_price': exit_price,
            'ema20_break': ema20_break_raw,
            'atr_stop': atr_stop_raw,
            'etf_weaken': etf_weaken_raw,
            'theme_cool': theme_cool_raw,
            'leader_change': leader_change_raw,
            'volume_stagnation': vol_stagnation_raw,
        }
        return metrics

    # ------------------------------------------------------------------
    # 各卖出信号计算
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_ema20_break(close: np.ndarray, ema20: np.ndarray,
                          n: int) -> float:
        """EMA20跌破信号。

        Price closed below EMA20 by > threshold %.
        Higher score = further below EMA20.
        """
        if n < 20:
            return 0.0

        c = float(close[-1])
        e = float(ema20[-1])
        if e <= 0:
            return 0.0

        # How far below EMA20 as %
        below_pct = (e - c) / e * 100.0

        # Threshold from config (default 0.5%)
        threshold = 0.5
        if below_pct <= threshold:
            return 0.0

        # Score increases with distance below EMA20, saturating at ~10%
        raw = (below_pct - threshold) / 10.0
        return float(min(raw, 1.0))

    @staticmethod
    def _calc_atr_stop(close: np.ndarray, atr14: np.ndarray,
                       buy_result: BuySignalResult, n: int) -> float:
        """ATR止损信号。

        Price below entry_price - 2 * ATR.
        Higher score = further below stop level.
        """
        if n < 14:
            return 0.0

        entry_price = buy_result.entry_price
        if entry_price <= 0:
            return 0.0

        c = float(close[-1])
        atr_last = float(atr14[-1]) if np.isfinite(atr14[-1]) else 0.0
        if atr_last <= 0:
            return 0.0

        stop_level = entry_price - 2.0 * atr_last

        # How far below stop as % of ATR
        if c >= stop_level:
            return 0.0

        below_pct = (stop_level - c) / max(atr_last, 1e-10)
        raw = min(below_pct / 3.0, 1.0)  # 3 ATRs below stop = max score
        return float(raw)

    @staticmethod
    def _calc_etf_weaken(etf_code: str,
                         etf_data: Dict[str, pd.DataFrame],
                         etf_scores: Dict[str, float]) -> float:
        """ETF走弱信号。

        ETF trend score drops below threshold.
        ETF breaks below its own EMA20.
        """
        score = 0.0

        # ETF score check
        if etf_code and etf_code in etf_scores:
            etf_score = etf_scores[etf_code]
            threshold = 40.0
            if etf_score < threshold:
                score = max(score, (threshold - etf_score) / threshold)

        # ETF EMA20 break check
        if etf_code and etf_code in etf_data:
            edf = etf_data[etf_code]
            if edf is not None and not edf.empty and 'close' in edf.columns:
                ec = np.asarray(edf['close'].values, dtype=np.float64)
                if len(ec) >= 20:
                    eema20 = ema(ec, 20)
                    if float(ec[-1]) < float(eema20[-1]):
                        below_pct = (float(eema20[-1]) - float(ec[-1])) / max(float(eema20[-1]), 1e-10) * 100.0
                        score = max(score, min(below_pct / 5.0, 1.0))

        return float(score)

    @staticmethod
    def _calc_theme_cool(etf_code: str,
                         theme_data: Dict[str, ThemeResult],
                         etf_scores: Dict[str, float]) -> float:
        """主题降温信号。

        Theme heat drops below threshold.
        Theme composite score declining.
        """
        score = 0.0

        if not theme_data:
            return 0.0

        # Try to find theme by checking all themes
        for theme_name, tr in theme_data.items():
            heat = tr.heat_score
            composite = tr.composite_score

            # Theme heat check
            heat_threshold = 30.0
            if heat < heat_threshold:
                score = max(score, (heat_threshold - heat) / heat_threshold)

            # Composite score check
            comp_threshold = 30.0
            if composite < comp_threshold:
                score = max(score, (comp_threshold - composite) / comp_threshold)

        return float(min(score, 1.0))

    @staticmethod
    def _calc_leader_change(ts_code: str,
                            etf_code: str,
                            etf_to_codes: Dict[str, List[str]],
                            ts_leader_score: Dict[str, float]) -> float:
        """龙头切换信号。

        Another stock in same ETF has higher leader score.
        Previous leader breaking down (score < threshold).
        """
        if not etf_code or etf_code not in etf_to_codes:
            return 0.0

        peer_codes = etf_to_codes.get(etf_code, [])
        if len(peer_codes) < 2:
            return 0.0

        my_score = ts_leader_score.get(ts_code, 50.0)

        # Check if any peer has significantly higher leader score
        max_peer_score = 0.0
        for peer in peer_codes:
            if peer == ts_code:
                continue
            peer_s = ts_leader_score.get(peer, 0.0)
            max_peer_score = max(max_peer_score, peer_s)

        score = 0.0
        if max_peer_score > my_score:
            # Score based on how much higher the peer score is
            ratio = (max_peer_score - my_score) / max(max_peer_score, 1e-10)
            score = max(score, min(ratio * 2.0, 1.0))

        # Check if own score has dropped significantly
        leader_threshold = 30.0
        if my_score < leader_threshold:
            score = max(score, (leader_threshold - my_score) / leader_threshold)

        return float(min(score, 1.0))

    @staticmethod
    def _calc_volume_stagnation(close: np.ndarray, volume: np.ndarray,
                                 vr: np.ndarray, n: int) -> float:
        """连续放量滞涨信号。

        Price flat/declining with above average volume.
        Multiple days of stagnation.
        """
        if n < 10:
            return 0.0

        # Price change over last 5 days
        days = min(5, n)
        price_change = (float(close[-1]) - float(close[-days])) / max(float(close[-days]), 1e-10) * 100.0

        # Price is flat or declining
        if price_change > 2.0:
            return 0.0

        # Volume above average
        vol_above = float(vr[-1] > 1.1)

        # Consecutive days with above-average volume
        vol_above_days = int(np.sum(vr[-days:] > 1.05)) if n >= days else 0

        # Check for bearish volume pattern
        vol_trend = 0.0
        if n >= 10:
            recent_vol = np.mean(volume[-5:]) if n >= 5 else 0.0
            prev_vol = np.mean(volume[-10:-5]) if n >= 10 else 0.0
            if prev_vol > 0:
                vol_trend = float(min(recent_vol / prev_vol - 1.0, 0.5) / 0.5)

        # Stagnation score
        stagn_days_ratio = vol_above_days / max(days, 1)
        price_decline = max(-price_change, 0.0) / 5.0  # 5% decline = max

        raw = (0.3 * vol_above + 0.3 * stagn_days_ratio + 0.2 * vol_trend + 0.2 * min(price_decline, 1.0))
        return float(min(raw, 1.0))

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
