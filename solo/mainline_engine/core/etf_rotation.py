"""ETF 轮动引擎 — 对全市场 ETF 进行 0-100 综合打分。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, rma, atr, adx, rsi, macd, bollinger, kdj,
    rank_score, normalize, zscore, winsorize,
    max_drawdown, sharpe_ratio, calmar_ratio,
    rolling_corr, beta as rolling_beta,
    new_high_count, consecutive_up_days, above_ema_days,
    volume_ratio, slope, hurst_exponent,
)


@dataclass
class ETFScoreResult:
    ts_code: str
    etf_name: str = ""
    theme: str = ""
    trend_score: float = 0.0
    persistence_score: float = 0.0
    rs_score: float = 0.0
    momentum_score: float = 0.0
    volume_score: float = 0.0
    atr_score: float = 0.0
    adx_score: float = 0.0
    breakout_60d: float = 0.0
    breakout_90d: float = 0.0
    breakout_120d: float = 0.0
    relative_rank_score: float = 0.0
    top10_days: int = 0
    rotation_score: float = 0.0


class ETFRotationEngine:
    """ETF 轮动引擎。

    对每只 ETF 计算趋势强度、持续性、相对强弱、动量、成交量、
    ATR、ADX、突破、相对排名等 10+ 维度，加权汇总为 0-100 的轮动得分。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('etf_rotation', {})
        self.etf_themes = config.get('etf_themes', {})
        self._log_config()

    def _log_config(self) -> None:
        logger.debug(f"ETFRotationEngine config: ema_fast={self.cfg.get('ema_fast', 20)}, "
                     f"ema_mid={self.cfg.get('ema_mid', 60)}, "
                     f"ema_slow={self.cfg.get('ema_slow', 120)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              etf_data: Dict[str, pd.DataFrame],
              benchmark_close: Optional[pd.Series] = None,
              ) -> Dict[str, ETFScoreResult]:
        """对所有 ETF 打分。

        Parameters
        ----------
        etf_data : dict
            {ts_code: DataFrame}，每个 DataFrame 必须包含列
            [trade_date, open, high, low, close, vol]。
        benchmark_close : pd.Series, optional
            基准指数（沪深 300）的收盘价序列，用于计算相对强度。

        Returns
        -------
        dict[str, ETFScoreResult]
        """
        if not etf_data:
            logger.warning("etf_data is empty, returning {}")
            return {}

        fast = self.cfg.get('ema_fast', 20)
        mid = self.cfg.get('ema_mid', 60)
        slow = self.cfg.get('ema_slow', 120)
        adx_period = self.cfg.get('adx_period', 14)
        rs_period = self.cfg.get('rs_period', 60)
        top10_period = self.cfg.get('top10_days_period', 20)

        bench_returns_series = None
        if benchmark_close is not None and len(benchmark_close) > 1:
            bc = np.asarray(benchmark_close, dtype=np.float64)
            bench_returns_series = (bc[1:] - bc[:-1]) / np.maximum(bc[:-1], 1e-10)
            bench_returns_series = np.concatenate([[0.0], bench_returns_series])

        # ------ 第一遍：计算每只 ETF 的各项指标（最新值） ------
        metrics_list: List[dict] = []

        for ts_code, df in etf_data.items():
            if df is None or df.empty:
                logger.debug(f"Skipping {ts_code}: empty DataFrame")
                continue

            min_rows = max(slow, 120, top10_period, rs_period) + 5
            if len(df) < min_rows:
                logger.debug(f"Skipping {ts_code}: insufficient rows ({len(df)} < {min_rows})")
                continue

            try:
                metrics = self._compute_etf_metrics(
                    ts_code, df, fast, mid, slow, adx_period, rs_period, top10_period,
                )
                if metrics is not None:
                    metrics_list.append(metrics)
            except Exception as exc:
                logger.error(f"Error computing metrics for {ts_code}: {exc}")
                continue

        if not metrics_list:
            logger.warning("No ETF passed metric computation, returning {}")
            return {}

        # ------ 第二遍：横截面排名 & 最终评分 ------
        rotation_scores_raw = self._compute_rotation_scores(metrics_list, fast, mid, slow)

        # ------ 组装结果 ------
        etf_themes = self.etf_themes
        results: Dict[str, ETFScoreResult] = {}
        rank_lookup = self._percentile_rank(rotation_scores_raw) * 100.0
        for idx, (m, final_score) in enumerate(zip(metrics_list, rotation_scores_raw)):
            ts_code = m['ts_code']
            trend_s = self._score_trend(m, fast, mid, slow)
            persist_s = self._score_persistence(m, fast, mid)
            rs_s = self._score_rs(m)
            mom_s = self._score_momentum(m)
            vol_s = self._score_volume(m)
            atr_s = self._score_atr(m)
            adx_s_val = self._score_adx(m)
            b60 = self._score_breakout(m, 60)
            b90 = self._score_breakout(m, 90)
            b120 = self._score_breakout(m, 120)
            rank_s = round(float(rank_lookup[idx]), 2)

            result = ETFScoreResult(
                ts_code=ts_code,
                etf_name=m.get('etf_name', ''),
                theme=etf_themes.get(ts_code, ''),
                trend_score=trend_s,
                persistence_score=persist_s,
                rs_score=rs_s,
                momentum_score=mom_s,
                volume_score=vol_s,
                atr_score=atr_s,
                adx_score=adx_s_val,
                breakout_60d=b60,
                breakout_90d=b90,
                breakout_120d=b120,
                relative_rank_score=rank_s,
                top10_days=int(m.get('top10_days', 0)),
                rotation_score=round(final_score, 2),
            )
            results[ts_code] = result

        logger.info(f"ETFRotationEngine scored {len(results)} ETFs")
        return results

    # ------------------------------------------------------------------
    # 第一遍：ETF 指标计算（全向量化）
    # ------------------------------------------------------------------

    def _compute_etf_metrics(self, ts_code: str, df: pd.DataFrame,
                             fast: int, mid: int, slow: int,
                             adx_period: int, rs_period: int,
                             top10_period: int,
                             ) -> Optional[dict]:
        close = np.asarray(df['close'].values, dtype=np.float64)
        high = np.asarray(df['high'].values, dtype=np.float64)
        low = np.asarray(df['low'].values, dtype=np.float64)
        vol = np.asarray(df['vol'].values, dtype=np.float64)
        n = len(close)

        ema_f = ema(close, fast)
        ema_m = ema(close, mid)
        ema_s = ema(close, slow)

        # --- 趋势 ---
        alignment = (ema_f > ema_m) & (ema_m > ema_s)
        above_emas = (close > ema_f) & (close > ema_m) & (close > ema_s)
        slope_vals = slope(close, fast)
        adx_vals = adx(high, low, close, adx_period)
        h = hurst_exponent(close[-min(len(close), 240):])

        # --- 持续性 ---
        persist_days = above_ema_days(close, fast)

        ema_aligned_mask = ema_f > ema_m
        ema_aligned_days = self._consecutive_count(ema_aligned_mask)

        # --- 相对强弱 vs 基准 ---
        returns_20 = close[-1] / np.maximum(close[max(-21, -n)], 1e-10) - 1.0 if n >= 21 else np.nan
        returns_60 = close[-1] / np.maximum(close[max(-61, -n)], 1e-10) - 1.0 if n >= 61 else np.nan
        returns_120 = close[-1] / np.maximum(close[max(-121, -n)], 1e-10) - 1.0 if n >= 121 else np.nan

        # --- 动量 ---
        roc_20 = close[-1] / np.maximum(close[max(-21, -n)], 1e-10) - 1.0 if n >= 21 else np.nan
        roc_60 = close[-1] / np.maximum(close[max(-61, -n)], 1e-10) - 1.0 if n >= 61 else np.nan
        roc_20_prev = close[max(-21, -n)] / np.maximum(close[max(-41, -n)], 1e-10) - 1.0 if n >= 41 else np.nan
        momentum_accel = (roc_20 - roc_20_prev) if not np.isnan(roc_20) and not np.isnan(roc_20_prev) else np.nan

        # --- 成交量 ---
        vr = volume_ratio(vol, fast)
        vol_ema_val = ema(vol, fast)

        # --- ATR ---
        atr_vals = atr(high, low, close, adx_period)
        natr_vals = atr_vals / np.maximum(close, 1e-10) * 100.0

        # --- 突破 ---
        high_60 = pd.Series(high).rolling(60, min_periods=1).max().values if n >= 60 else np.full(n, np.nan)
        high_90 = pd.Series(high).rolling(90, min_periods=1).max().values if n >= 90 else np.full(n, np.nan)
        high_120 = pd.Series(high).rolling(120, min_periods=1).max().values if n >= 120 else np.full(n, np.nan)

        pct_60 = (close[-1] / np.maximum(high_60[-1], 1e-10) - 1.0) * 100.0 if high_60[-1] is not None and not np.isnan(high_60[-1]) else np.nan
        pct_90 = (close[-1] / np.maximum(high_90[-1], 1e-10) - 1.0) * 100.0 if high_90[-1] is not None and not np.isnan(high_90[-1]) else np.nan
        pct_120 = (close[-1] / np.maximum(high_120[-1], 1e-10) - 1.0) * 100.0 if high_120[-1] is not None and not np.isnan(high_120[-1]) else np.nan

        # --- 最新有效值提取 ---
        def _last_valid(arr: np.ndarray) -> float:
            arr = arr[np.isfinite(arr)]
            return float(arr[-1]) if len(arr) > 0 else np.nan

        metrics = {
            'ts_code': ts_code,
            'etf_name': str(df.get('etf_name', pd.Series([ts_code])).iloc[0]) if 'etf_name' in df.columns else ts_code,
            # trend
            'trend_alignment': int(alignment[-1]) if n > 0 else 0,
            'trend_above_emas': int(above_emas[-1]) if n > 0 else 0,
            'trend_slope': float(slope_vals[-1]) if n > 0 and np.isfinite(slope_vals[-1]) else 0.0,
            'trend_slope_abs': float(np.abs(slope_vals[-1])) if n > 0 and np.isfinite(slope_vals[-1]) else 0.0,
            'hurst': float(h),
            'adx_val': _last_valid(adx_vals),
            # persistence
            'persist_days': int(persist_days[-1]) if n > 0 else 0,
            'ema_aligned_days': int(ema_aligned_days[-1]) if n > 0 else 0,
            # RS
            'ret_20d': float(returns_20) if np.isfinite(returns_20) else np.nan,
            'ret_60d': float(returns_60) if np.isfinite(returns_60) else np.nan,
            'ret_120d': float(returns_120) if np.isfinite(returns_120) else np.nan,
            # momentum
            'roc_20': float(roc_20) if np.isfinite(roc_20) else np.nan,
            'roc_60': float(roc_60) if np.isfinite(roc_60) else np.nan,
            'momentum_accel': float(momentum_accel) if np.isfinite(momentum_accel) else np.nan,
            # volume
            'volume_ratio': _last_valid(vr),
            'vol_trend': int(vol[-1] > vol_ema_val[-1]) if n > 0 and np.isfinite(vol_ema_val[-1]) else 0,
            # ATR
            'natr': _last_valid(natr_vals),
            # breakout
            'pct_off_60d_high': float(pct_60) if np.isfinite(pct_60) else np.nan,
            'pct_off_90d_high': float(pct_90) if np.isfinite(pct_90) else np.nan,
            'pct_off_120d_high': float(pct_120) if np.isfinite(pct_120) else np.nan,
            # top10 tracked separately after cross-sectional ranking
            'n': n,
        }
        return metrics

    # ------------------------------------------------------------------
    # 第二遍：横截面排名 + 最终加权得分
    # ------------------------------------------------------------------

    def _compute_rotation_scores(self, metrics_list: List[dict],
                                 fast: int, mid: int, slow: int) -> np.ndarray:
        n_etf = len(metrics_list)
        if n_etf == 0:
            return np.array([], dtype=np.float64)

        # 将各项指标提取为向量
        trend_alignment_v = np.array([m['trend_alignment'] for m in metrics_list], dtype=np.float64)
        trend_above_v = np.array([m['trend_above_emas'] for m in metrics_list], dtype=np.float64)
        slope_v = np.array([m['trend_slope_abs'] for m in metrics_list], dtype=np.float64)
        hurst_v = np.array([m['hurst'] for m in metrics_list], dtype=np.float64)
        adx_v = np.array([m['adx_val'] for m in metrics_list], dtype=np.float64)

        persist_days_v = np.array([m['persist_days'] for m in metrics_list], dtype=np.float64)
        aligned_days_v = np.array([m['ema_aligned_days'] for m in metrics_list], dtype=np.float64)

        ret_20_v = np.array([m['ret_20d'] for m in metrics_list], dtype=np.float64)
        ret_60_v = np.array([m['ret_60d'] for m in metrics_list], dtype=np.float64)
        ret_120_v = np.array([m['ret_120d'] for m in metrics_list], dtype=np.float64)

        roc_20_v = np.array([m['roc_20'] for m in metrics_list], dtype=np.float64)
        roc_60_v = np.array([m['roc_60'] for m in metrics_list], dtype=np.float64)
        accel_v = np.array([m['momentum_accel'] for m in metrics_list], dtype=np.float64)

        vr_v = np.array([m['volume_ratio'] for m in metrics_list], dtype=np.float64)
        vol_trend_v = np.array([m['vol_trend'] for m in metrics_list], dtype=np.float64)

        natr_v = np.array([m['natr'] for m in metrics_list], dtype=np.float64)

        b60_v = np.array([m['pct_off_60d_high'] for m in metrics_list], dtype=np.float64)
        b90_v = np.array([m['pct_off_90d_high'] for m in metrics_list], dtype=np.float64)
        b120_v = np.array([m['pct_off_120d_high'] for m in metrics_list], dtype=np.float64)

        # ------ 子分数 0-100 ------
        # 趋势
        trend_alignment_score = np.where(trend_alignment_v > 0.5, 100.0, 0.0) * 0.30
        ema20_above = np.array([float(m.get('ema_f_gt_ema_m', np.nan)) for m in metrics_list], dtype=np.float64)
        ema20_above = np.where(np.isfinite(ema20_above), ema20_above, 0.0)
        ema_alignment_mid = (ema20_above * 100.0) * 0.20

        slope_scaled = np.clip(winsorize(slope_v, 0.01) * 20.0, 0.0, 100.0) * 0.20
        hurst_scaled = np.clip((hurst_v - 0.5) * 200.0, 0.0, 100.0) * 0.15
        adx_scaled = np.clip(adx_v / 50.0 * 100.0, 0.0, 100.0) * 0.15

        trend_score = (trend_alignment_score + ema_alignment_mid + slope_scaled + hurst_scaled + adx_scaled)

        # 持续性
        persist_raw = self._percentile_rank(persist_days_v) * 50.0
        aligned_raw = self._percentile_rank(aligned_days_v) * 50.0
        persistence_score = persist_raw + aligned_raw

        # 相对强度
        ret_20_norm = self._safe_normalize(winsorize(ret_20_v, 0.01)) * 33.33
        ret_60_norm = self._safe_normalize(winsorize(ret_60_v, 0.01)) * 33.33
        ret_120_norm = self._safe_normalize(winsorize(ret_120_v, 0.01)) * 33.34
        rs_score = ret_20_norm + ret_60_norm + ret_120_norm

        # 动量
        roc_20_norm = self._safe_normalize(winsorize(roc_20_v, 0.01)) * 40.0
        roc_60_norm = self._safe_normalize(winsorize(roc_60_v, 0.01)) * 30.0
        accel_norm = self._safe_normalize(winsorize(accel_v, 0.01)) * 30.0
        momentum_score = roc_20_norm + roc_60_norm + accel_norm

        # 成交量
        vr_norm = self._safe_normalize(winsorize(vr_v, 0.01)) * 60.0
        vt_norm = vol_trend_v * 40.0
        volume_score = vr_norm + vt_norm

        # ATR (反向: 越低越好)
        natr_score = np.clip(100.0 - self._safe_normalize(winsorize(natr_v, 0.01)) * 100.0, 0.0, 100.0)

        # ADX
        adx_score_val = adx_scaled

        # 突破
        b60_score = self._safe_normalize(winsorize(b60_v * -1.0, 0.01)) * 100.0  # 接近高点的得分高
        b90_score = self._safe_normalize(winsorize(b90_v * -1.0, 0.01)) * 100.0
        b120_score = self._safe_normalize(winsorize(b120_v * -1.0, 0.01)) * 100.0

        # 相对排名
        combined_raw = (
            trend_score * 0.30 +
            persistence_score * 0.15 +
            rs_score * 0.15 +
            momentum_score * 0.10 +
            volume_score * 0.10 +
            natr_score * 0.05 +
            adx_score_val * 0.05 +
            b60_score * 0.03 +
            b90_score * 0.03 +
            b120_score * 0.02 +
            b120_score * 0.02
        )
        relative_rank_score = self._percentile_rank(combined_raw) * 100.0

        # ------ 权重配置 ------
        w_trend = self.cfg.get('trend_weight', 0.25)
        w_persist = self.cfg.get('persistence_weight', 0.15)
        w_rs = self.cfg.get('relative_strength_weight', 0.15)
        w_mom = self.cfg.get('relative_momentum_weight', 0.15)
        w_vol = self.cfg.get('volume_trend_weight', 0.10)
        w_atr = self.cfg.get('atr_weight', 0.05)
        w_adx = self.cfg.get('adx_weight', 0.05)
        w_b60 = self.cfg.get('breakout_60d', 0.03)
        w_b90 = self.cfg.get('breakout_90d', 0.03)
        w_b120 = self.cfg.get('breakout_120d', 0.02)
        w_rank = self.cfg.get('relative_etf_rank_weight', 0.02)

        final_score = (
            trend_score * w_trend +
            persistence_score * w_persist +
            rs_score * w_rs +
            momentum_score * w_mom +
            volume_score * w_vol +
            natr_score * w_atr +
            adx_score_val * w_adx +
            b60_score * w_b60 +
            b90_score * w_b90 +
            b120_score * w_b120 +
            relative_rank_score * w_rank
        )
        final_score = np.clip(final_score, 0.0, 100.0)

        # 更新 top10_days（追踪连续处于前10的天数）
        # 用 cross-sectional 排名来近似
        rank_order = np.argsort(final_score)[::-1]
        top10_mask = np.zeros(n_etf, dtype=bool)
        top10_mask[rank_order[:min(10, n_etf)]] = True
        for i, m in enumerate(metrics_list):
            m['top10_days'] = 1 if top10_mask[i] else 0

        return final_score

    # ------------------------------------------------------------------
    # 子分数方法（供组装结果使用）
    # ------------------------------------------------------------------

    def _score_trend(self, m: dict, fast: int, mid: int, slow: int) -> float:
        s = 0.0
        s += m['trend_alignment'] * 30.0
        s += m['trend_above_emas'] * 20.0
        s += np.clip(np.abs(m['trend_slope']) * 5.0, 0.0, 20.0)
        s += np.clip((m['hurst'] - 0.5) * 100.0, 0.0, 15.0)
        s += 15.0 if m['adx_val'] > 25 else (7.5 if m['adx_val'] > 20 else 0.0)
        return round(np.clip(s, 0.0, 100.0), 2)

    def _score_persistence(self, m: dict, fast: int, mid: int) -> float:
        days = m['persist_days']
        aligned = m['ema_aligned_days']
        s = np.clip(days / 10.0 * 50.0, 0.0, 50.0) + np.clip(aligned / 10.0 * 50.0, 0.0, 50.0)
        return round(np.clip(s, 0.0, 100.0), 2)

    def _score_rs(self, m: dict) -> float:
        r20 = m['ret_20d']
        r60 = m['ret_60d']
        r120 = m['ret_120d']
        s = 0.0
        if np.isfinite(r20):
            s += np.clip((r20 * 100.0 + 10.0) * 3.33, 0.0, 33.33)
        if np.isfinite(r60):
            s += np.clip((r60 * 100.0 + 10.0) * 3.33, 0.0, 33.33)
        if np.isfinite(r120):
            s += np.clip((r120 * 100.0 + 10.0) * 3.34, 0.0, 33.34)
        return round(s, 2)

    def _score_momentum(self, m: dict) -> float:
        roc20 = m['roc_20']
        roc60 = m['roc_60']
        accel = m['momentum_accel']
        s = 0.0
        if np.isfinite(roc20):
            s += np.clip((roc20 * 100.0 + 5.0) * 6.67, 0.0, 40.0)
        if np.isfinite(roc60):
            s += np.clip((roc60 * 100.0 + 5.0) * 5.0, 0.0, 30.0)
        if np.isfinite(accel):
            s += np.clip((accel * 100.0 + 5.0) * 5.0, 0.0, 30.0)
        return round(s, 2)

    def _score_volume(self, m: dict) -> float:
        vr = m['volume_ratio']
        vt = m['vol_trend']
        s = 0.0
        if np.isfinite(vr):
            s += np.clip(vr * 30.0, 0.0, 60.0)
        s += vt * 40.0
        return round(np.clip(s, 0.0, 100.0), 2)

    def _score_atr(self, m: dict) -> float:
        natr_val = m['natr']
        if not np.isfinite(natr_val):
            return 50.0
        return round(np.clip(100.0 - natr_val * 10.0, 0.0, 100.0), 2)

    def _score_adx(self, m: dict) -> float:
        adx_val = m['adx_val']
        if not np.isfinite(adx_val):
            return 0.0
        return round(np.clip(adx_val / 50.0 * 100.0, 0.0, 100.0), 2)

    def _score_breakout(self, m: dict, period: int) -> float:
        key = f'pct_off_{period}d_high'
        val = m.get(key, np.nan)
        if not np.isfinite(val):
            return 0.0
        return round(np.clip(100.0 + val * 5.0, 0.0, 100.0), 2)

    def _score_relative_rank(self, m: dict, all_scores: np.ndarray) -> float:
        if len(all_scores) == 0:
            return 50.0
        # 找到该 ETF 在全体中的位置
        idx = len(all_scores) - 1  # fallback
        for i, score in enumerate(all_scores):
            ts_code = m['ts_code']
            if ts_code == list(self._find_key_for_value(ts_code)):  # simplified
                idx = i
                break
        rank = float(np.mean(all_scores < all_scores[idx])) * 100.0
        return round(rank, 2)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _consecutive_count(condition: np.ndarray) -> np.ndarray:
        s = pd.Series(condition.astype(int))
        groups = (~condition).cumsum()
        result = s.groupby(groups).cumcount() + 1
        return result.where(condition, 0).values.astype(np.int32)

    @staticmethod
    def _safe_normalize(arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float64)
        valid = a[np.isfinite(a)]
        if len(valid) == 0:
            return np.full_like(a, 0.5)
        mn, mx = np.nanmin(a), np.nanmax(a)
        if mx <= mn or not np.isfinite(mx - mn):
            return np.full_like(a, 0.5)
        return (a - mn) / (mx - mn)

    @staticmethod
    def _percentile_rank(arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float64)
        valid_mask = np.isfinite(a)
        if valid_mask.sum() == 0:
            return np.full_like(a, 0.5)
        valid = a[valid_mask]
        sorted_valid = np.sort(valid)
        ranks = np.searchsorted(sorted_valid, a).astype(np.float64)
        ranks = ranks / max(len(sorted_valid), 1)
        ranks[~valid_mask] = 0.5
        return ranks
