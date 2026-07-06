"""风险引擎 — 计算个股的多维风险评分。"""

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
    volume_ratio, slope, natr,
)


@dataclass
class RiskResult:
    ts_code: str
    atr_score: float = 0.0
    drawdown_score: float = 0.0
    gap_risk_score: float = 0.0
    beta_score: float = 0.0
    volatility_score: float = 0.0
    etf_drawdown_score: float = 0.0
    market_risk_score: float = 0.0
    risk_score: float = 0.0
    risk_inverted: float = 0.0


class RiskEngine:
    """风险引擎。

    对每只个股计算 7 个维度的风险评分（0-100，越高越风险），
    加权得到综合风险得分 risk_score，
    并输出 risk_inverted = 100 - risk_score 供复合评分使用。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('risk', {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              etf_data: Dict[str, pd.DataFrame] = None,
              market_index_data: pd.DataFrame = None,
              risk_free_rate: float = 0.02) -> Dict[str, RiskResult]:
        """计算每只个股的多维风险评分。

        Parameters
        ----------
        stock_data : dict
            {ts_code: DataFrame}，个股日线数据，需含列
            [trade_date, open, high, low, close, vol]。
        etf_data : dict, optional
            {ts_code: DataFrame}，ETF 日线数据，用于计算 ETF 回撤风险。
        market_index_data : pd.DataFrame, optional
            市场指数（沪深300）日线数据，用于计算 Beta 和市场风险。
        risk_free_rate : float
            无风险利率，默认 0.02。

        Returns
        -------
        dict[str, RiskResult]
            {ts_code: RiskResult}
        """
        if not stock_data:
            logger.warning("stock_data is empty, returning {}")
            return {}

        etf_data = etf_data or {}
        lookback = self.cfg.get('lookback', 60)
        min_rows = lookback + 10
        annual_factor = np.sqrt(252.0)

        metrics_list: List[dict] = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue
            if len(df) < min_rows:
                logger.debug(f"Skipping {ts_code}: insufficient rows ({len(df)} < {min_rows})")
                continue

            try:
                metrics = self._compute_risk_metrics(
                    ts_code, df, etf_data, market_index_data,
                    lookback, annual_factor, risk_free_rate,
                )
                if metrics is not None:
                    metrics_list.append(metrics)
            except Exception as exc:
                logger.debug(f"Error computing risk for {ts_code}: {exc}")
                continue

        if not metrics_list:
            logger.warning("No risk metrics computed, returning {}")
            return {}

        # Cross-sectional normalization across all stocks
        n = len(metrics_list)
        atr_v = np.array([m['atr_raw'] for m in metrics_list], dtype=np.float64)
        dd_v = np.array([m['dd_raw'] for m in metrics_list], dtype=np.float64)
        gap_v = np.array([m['gap_raw'] for m in metrics_list], dtype=np.float64)
        beta_v = np.array([m['beta_raw'] for m in metrics_list], dtype=np.float64)
        vol_v = np.array([m['vol_raw'] for m in metrics_list], dtype=np.float64)
        etf_dd_v = np.array([m['etf_dd_raw'] for m in metrics_list], dtype=np.float64)
        market_v = np.array([m['market_raw'] for m in metrics_list], dtype=np.float64)

        atr_s = self._to_score(winsorize(atr_v, 0.01))
        dd_s = self._to_score(winsorize(dd_v, 0.01))
        gap_s = self._to_score(winsorize(gap_v, 0.01))
        beta_s = self._to_score(winsorize(beta_v, 0.01))
        vol_s = self._to_score(winsorize(vol_v, 0.01))
        etf_dd_s = self._to_score(winsorize(etf_dd_v, 0.01))
        market_s = self._to_score(winsorize(market_v, 0.01))

        w_atr = self.cfg.get('atr_weight', 0.15)
        w_dd = self.cfg.get('drawdown_weight', 0.20)
        w_gap = self.cfg.get('gap_risk_weight', 0.10)
        w_beta = self.cfg.get('beta_weight', 0.15)
        w_vol = self.cfg.get('volatility_weight', 0.15)
        w_etf_dd = self.cfg.get('etf_drawdown_weight', 0.15)
        w_market = self.cfg.get('market_risk_weight', 0.10)

        final_risk = (
            atr_s * w_atr +
            dd_s * w_dd +
            gap_s * w_gap +
            beta_s * w_beta +
            vol_s * w_vol +
            etf_dd_s * w_etf_dd +
            market_s * w_market
        )
        final_risk = np.clip(final_risk, 0.0, 100.0)
        final_inverted = 100.0 - final_risk

        results: Dict[str, RiskResult] = {}
        for m, ars, dds, gps, bts, vls, eds, mks, rs, ri in zip(
            metrics_list,
            atr_s, dd_s, gap_s, beta_s, vol_s, etf_dd_s, market_s,
            final_risk, final_inverted,
        ):
            results[m['ts_code']] = RiskResult(
                ts_code=m['ts_code'],
                atr_score=round(float(ars), 2),
                drawdown_score=round(float(dds), 2),
                gap_risk_score=round(float(gps), 2),
                beta_score=round(float(bts), 2),
                volatility_score=round(float(vls), 2),
                etf_drawdown_score=round(float(eds), 2),
                market_risk_score=round(float(mks), 2),
                risk_score=round(float(rs), 2),
                risk_inverted=round(float(ri), 2),
            )

        logger.info(f"RiskEngine scored {len(results)} stocks")
        return results

    # ------------------------------------------------------------------
    # 单只个股风险指标计算（全向量化）
    # ------------------------------------------------------------------

    def _compute_risk_metrics(self,
                              ts_code: str,
                              df: pd.DataFrame,
                              etf_data: Dict[str, pd.DataFrame],
                              market_index: Optional[pd.DataFrame],
                              lookback: int,
                              annual_factor: float,
                              risk_free_rate: float) -> Optional[dict]:
        close = np.asarray(df['close'].values, dtype=np.float64)
        high = np.asarray(df['high'].values, dtype=np.float64)
        low = np.asarray(df['low'].values, dtype=np.float64)
        vol = np.asarray(df['vol'].values, dtype=np.float64)
        n = len(close)

        # 1. ATR Risk: normalized ATR as % of close
        atr_vals = atr(high, low, close, 14)
        atr_pct = atr_vals / np.maximum(close, 1e-10) * 100.0
        atr_raw = float(atr_pct[-1]) if n > 0 and np.isfinite(atr_pct[-1]) else 0.0

        # 2. Drawdown Risk: max drawdown over lookback window
        close_tail = close[-min(lookback, n):]
        dd_val = max_drawdown(close_tail)
        dd_raw = abs(dd_val) * 100.0

        # 3. Gap Risk: frequency and magnitude of price gaps
        gap_raw = self._calc_gap_risk(
            open_px=np.asarray(df['open'].values, dtype=np.float64),
            close=close, lookback=lookback, n=n,
        )

        # 4. Beta Risk: beta to market index
        beta_raw = self._calc_beta_risk(close, market_index, n, risk_free_rate)

        # 5. Volatility Risk: 20-day annualized volatility
        returns = np.diff(close, prepend=close[0:1]) / np.maximum(np.roll(close, 1), 1e-10)
        returns[0] = 0.0
        if n >= 20:
            vol_20 = float(np.nanstd(returns[-20:]) * annual_factor * 100.0)
            vol_raw = vol_20 if np.isfinite(vol_20) else 0.0
        else:
            vol_raw = 0.0

        # 6. ETF Drawdown Risk: worst drawdown across all related ETFs
        etf_dd_raw = self._calc_etf_drawdown_risk(etf_data, lookback)

        # 7. Market Risk: VIX-like measure from index
        market_raw = self._calc_market_risk(market_index)

        metrics = {
            'ts_code': ts_code,
            'atr_raw': atr_raw,
            'dd_raw': dd_raw,
            'gap_raw': gap_raw,
            'beta_raw': beta_raw,
            'vol_raw': vol_raw,
            'etf_dd_raw': etf_dd_raw,
            'market_raw': market_raw,
        }
        return metrics

    # ------------------------------------------------------------------
    # 各子维度风险计算
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_gap_risk(open_px: np.ndarray, close: np.ndarray,
                       lookback: int, n: int) -> float:
        """计算跳空风险：跳空频率 × 平均跳空幅度。"""
        if n < 10:
            return 0.0

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]

        gap_pct = open_px / np.maximum(prev_close, 1e-10) - 1.0
        gap_magnitude = np.abs(gap_pct)

        window = min(lookback, n)
        recent_gaps = gap_magnitude[-window:]
        valid = np.isfinite(recent_gaps)
        valid_count = int(valid.sum())
        if valid_count < 5:
            return 0.0

        gap_freq = valid_count / window
        gap_avg_mag = float(np.mean(recent_gaps[valid])) * 100.0

        return gap_freq * gap_avg_mag * 10.0

    @staticmethod
    def _calc_beta_risk(close: np.ndarray,
                        market_index: Optional[pd.DataFrame],
                        n: int, risk_free_rate: float) -> float:
        """计算 Beta 风险（相对市场指数）。"""
        del risk_free_rate
        if market_index is None or len(market_index) < 5:
            return 1.0

        mkt_close = np.asarray(market_index['close'].values, dtype=np.float64)
        min_len = min(n, len(mkt_close))
        if min_len < 20:
            return 1.0

        sc = close[-min_len:]
        mc = mkt_close[-min_len:]

        sr = np.diff(sc, prepend=sc[0:1]) / np.maximum(np.roll(sc, 1), 1e-10)
        mr = np.diff(mc, prepend=mc[0:1]) / np.maximum(np.roll(mc, 1), 1e-10)
        sr[0] = 0.0
        mr[0] = 0.0

        beta_val = rolling_beta(sr, mr, 60)
        beta_last = float(beta_val[-1]) if len(beta_val) > 0 and np.isfinite(beta_val[-1]) else 1.0

        return max(beta_last, 0.0)

    @staticmethod
    def _calc_etf_drawdown_risk(etf_data: Dict[str, pd.DataFrame],
                                 lookback: int) -> float:
        """计算关联 ETF 的最大回撤风险（取最差 ETF 的回撤）。"""
        if not etf_data:
            return 0.0

        worst_dd = 0.0
        for edf in etf_data.values():
            if edf is None or edf.empty or 'close' not in edf.columns:
                continue
            ec = np.asarray(edf['close'].values, dtype=np.float64)
            if len(ec) < 20:
                continue
            ec_tail = ec[-min(lookback, len(ec)):]
            dd = max_drawdown(ec_tail)
            worst_dd = min(worst_dd, dd)

        return abs(worst_dd) * 100.0

    @staticmethod
    def _calc_market_risk(market_index: Optional[pd.DataFrame]) -> float:
        """计算市场整体风险：用指数 NATR 作为类 VIX 指标。"""
        if market_index is None or 'close' not in market_index.columns:
            return 0.0

        mkt_close = np.asarray(market_index['close'].values, dtype=np.float64)
        if len(mkt_close) < 20:
            return 0.0

        mkt_high = np.asarray(market_index['high'].values, dtype=np.float64)
        mkt_low = np.asarray(market_index['low'].values, dtype=np.float64)

        natr_vals = natr(mkt_high, mkt_low, mkt_close, 14)
        natr_last = float(natr_vals[-1]) if len(natr_vals) > 0 and np.isfinite(natr_vals[-1]) else 0.0

        return natr_last * 10.0

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
