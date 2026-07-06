"""龙头持续性引擎 — 评估龙头地位的持久性。"""

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
    volume_ratio, slope, hurst_exponent, natr,
)


@dataclass
class PersistenceResult:
    ts_code: str
    etf_code: str
    top3_count_score: float = 0.0
    continuous_top3_score: float = 0.0
    new_high_count_score: float = 0.0
    etf_lead_days_score: float = 0.0
    breakout_count_score: float = 0.0
    pullback_success_score: float = 0.0
    main_up_duration_score: float = 0.0
    persistence_score: float = 0.0


class LeaderPersistenceEngine:
    """龙头持续性引擎。

    对每个强势ETF中的龙头个股，评估其历史持续性（近60天）。
    包含7个维度的持续性评分，加权得到0-100的综合持续得分。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('persistence', {})
        self._log_config()

    def _log_config(self) -> None:
        logger.debug(f"LeaderPersistenceEngine config: "
                     f"lookback={self.cfg.get('lookback_period', 60)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              leader_results: Dict[str, List['LeaderResult']],
              etf_data: Dict[str, pd.DataFrame],
              ) -> Dict[str, PersistenceResult]:
        """评估龙头个股的持续性。

        Parameters
        ----------
        stock_data : dict
            {ts_code: DataFrame}，个股日线数据，含 [trade_date, open, high, low, close, vol]。
        leader_results : dict
            {etf_code: [LeaderResult, ...]}，Module 5输出的龙头评分结果。
        etf_data : dict
            {ts_code: DataFrame}，ETF日线数据。

        Returns
        -------
        dict[str, PersistenceResult]
            {ts_code: PersistenceResult}
        """
        if not leader_results:
            logger.warning("leader_results is empty, returning {}")
            return {}

        lookback = self.cfg.get('lookback_period', 60)
        extra_buffer = 25
        total_needed = lookback + extra_buffer

        all_results: Dict[str, PersistenceResult] = {}

        for etf_code, etf_leaders in leader_results.items():
            if not etf_leaders:
                continue

            etf_df = etf_data.get(etf_code)
            if etf_df is None or len(etf_df) < total_needed:
                logger.debug(f"Skipping ETF {etf_code}: insufficient ETF data")
                continue

            try:
                etf_close = np.asarray(etf_df['close'].values, dtype=np.float64)
                etf_vol = np.asarray(etf_df['vol'].values, dtype=np.float64)
                etf_dates_arr = etf_df['trade_date'].values if 'trade_date' in etf_df.columns else None
            except Exception as exc:
                logger.error(f"Error reading ETF {etf_code} data: {exc}")
                continue

            n_etf = len(etf_close)
            etf_close_tail = etf_close[-total_needed:]
            etf_vol_tail = etf_vol[-total_needed:]

            members = [r.ts_code for r in etf_leaders if r.ts_code]
            if len(members) < 2:
                continue

            # Align each stock to ETF dates
            stock_closes: Dict[str, np.ndarray] = {}
            stock_vols: Dict[str, np.ndarray] = {}
            stock_highs: Dict[str, np.ndarray] = {}
            stock_lows: Dict[str, np.ndarray] = {}

            for ts_code in members:
                sdf = stock_data.get(ts_code)
                if sdf is None or len(sdf) < total_needed:
                    continue
                if 'close' not in sdf.columns:
                    continue

                try:
                    aligned = self._align_to_etf(
                        sdf, etf_dates_arr, etf_close_tail, etf_vol_tail,
                        extra_buffer, lookback,
                    )
                    if aligned is None:
                        continue
                    sc, sv, sh, sl = aligned
                    stock_closes[ts_code] = sc
                    stock_vols[ts_code] = sv
                    stock_highs[ts_code] = sh
                    stock_lows[ts_code] = sl
                except Exception as exc:
                    logger.debug(f"Error aligning {ts_code} to ETF {etf_code}: {exc}")
                    continue

            if len(stock_closes) < 2:
                continue

            # Build cross-sectional ranking matrix for top3 analysis
            rank_matrix, stock_codes_list = self._build_rank_matrix(
                stock_closes, etf_close_tail, extra_buffer, lookback,
            )

            n_stocks_matrix = rank_matrix.shape[1] if rank_matrix is not None else 0

            for idx, ts_code in enumerate(stock_codes_list):
                sc = stock_closes[ts_code]
                sv = stock_vols[ts_code]
                sh = stock_highs[ts_code]

                try:
                    # 1-2: Top3 metrics from ranking matrix
                    if rank_matrix is not None and n_stocks_matrix >= 2:
                        daily_ranks = rank_matrix[:, idx]
                        valid = np.isfinite(daily_ranks)
                        if valid.sum() > 0:
                            top3_count = float(np.sum(daily_ranks[valid] <= 3))
                            total_days = float(valid.sum())
                            top3_count_pct = top3_count / max(total_days, 1)

                            continuous_top3 = self._max_consecutive_leq(daily_ranks[valid], 3)
                            continuous_top3_pct = continuous_top3 / max(total_days, 1)
                        else:
                            top3_count_pct = 0.0
                            continuous_top3_pct = 0.0
                    else:
                        top3_count_pct = 0.0
                        continuous_top3_pct = 0.0

                    # 3. New High Count
                    nh_arr = new_high_count(sc, 60)
                    nh_count = float(nh_arr[-1]) if len(nh_arr) > 0 else 0.0
                    nh_pct = nh_count / max(lookback, 1)

                    # 4. ETF Lead Days (stock return > ETF return)
                    lead_days = self._count_lead_days(sc, etf_close_tail, lookback)
                    lead_pct = lead_days / max(lookback, 1)

                    # 5. Breakout Count
                    breakout_count = self._count_breakouts(sc, sv, lookback)
                    breakout_pct = breakout_count / max(lookback, 1)

                    # 6. Pullback Success
                    pullback_success = self._calc_pullback_success(sc, lookback)

                    # 7. Main Up Duration
                    main_up = self._longest_up_streak(sc, lookback)
                    main_up_pct = main_up / max(lookback, 1)

                    top3_count_raw = top3_count_pct * 100.0
                    continuous_top3_raw = continuous_top3_pct * 100.0
                    nh_raw = nh_pct * 100.0
                    lead_raw = lead_pct * 100.0
                    breakout_raw = breakout_pct * 100.0
                    pullback_raw = pullback_success
                    main_up_raw = main_up_pct * 100.0

                    result = PersistenceResult(
                        ts_code=ts_code,
                        etf_code=etf_code,
                        top3_count_score=round(top3_count_raw, 2),
                        continuous_top3_score=round(continuous_top3_raw, 2),
                        new_high_count_score=round(nh_raw, 2),
                        etf_lead_days_score=round(lead_raw, 2),
                        breakout_count_score=round(breakout_raw, 2),
                        pullback_success_score=round(pullback_raw, 2),
                        main_up_duration_score=round(main_up_raw, 2),
                        persistence_score=0.0,
                    )
                    all_results[ts_code] = result

                except Exception as exc:
                    logger.debug(f"Error computing persistence for {ts_code}: {exc}")
                    continue

        if not all_results:
            logger.warning("No persistence results computed, returning {}")
            return {}

        # Cross-sectional normalization across all stocks
        ts_codes = list(all_results.keys())
        top3_v = np.array([all_results[t].top3_count_score for t in ts_codes], dtype=np.float64)
        ctop3_v = np.array([all_results[t].continuous_top3_score for t in ts_codes], dtype=np.float64)
        nh_v = np.array([all_results[t].new_high_count_score for t in ts_codes], dtype=np.float64)
        lead_v = np.array([all_results[t].etf_lead_days_score for t in ts_codes], dtype=np.float64)
        breakout_v = np.array([all_results[t].breakout_count_score for t in ts_codes], dtype=np.float64)
        pullback_v = np.array([all_results[t].pullback_success_score for t in ts_codes], dtype=np.float64)
        up_v = np.array([all_results[t].main_up_duration_score for t in ts_codes], dtype=np.float64)

        top3_s = self._to_score(winsorize(top3_v, 0.01))
        ctop3_s = self._to_score(winsorize(ctop3_v, 0.01))
        nh_s = self._to_score(winsorize(nh_v, 0.01))
        lead_s = self._to_score(winsorize(lead_v, 0.01))
        breakout_s = self._to_score(winsorize(breakout_v, 0.01))
        pullback_s = self._to_score(winsorize(pullback_v, 0.01))
        up_s = self._to_score(winsorize(up_v, 0.01))

        w_top3 = self.cfg.get('top3_count_weight', 0.20)
        w_ctop3 = self.cfg.get('continuous_top3_weight', 0.15)
        w_nh = self.cfg.get('new_high_count_weight', 0.15)
        w_lead = self.cfg.get('etf_lead_days_weight', 0.15)
        w_breakout = self.cfg.get('breakout_count_weight', 0.10)
        w_pullback = self.cfg.get('pullback_success_weight', 0.15)
        w_up = self.cfg.get('main_up_duration_weight', 0.10)

        final_scores = (
            top3_s * w_top3 +
            ctop3_s * w_ctop3 +
            nh_s * w_nh +
            lead_s * w_lead +
            breakout_s * w_breakout +
            pullback_s * w_pullback +
            up_s * w_up
        )
        final_scores = np.clip(final_scores, 0.0, 100.0)

        for i, ts_code in enumerate(ts_codes):
            all_results[ts_code].top3_count_score = round(float(top3_s[i]), 2)
            all_results[ts_code].continuous_top3_score = round(float(ctop3_s[i]), 2)
            all_results[ts_code].new_high_count_score = round(float(nh_s[i]), 2)
            all_results[ts_code].etf_lead_days_score = round(float(lead_s[i]), 2)
            all_results[ts_code].breakout_count_score = round(float(breakout_s[i]), 2)
            all_results[ts_code].pullback_success_score = round(float(pullback_s[i]), 2)
            all_results[ts_code].main_up_duration_score = round(float(up_s[i]), 2)
            all_results[ts_code].persistence_score = round(float(final_scores[i]), 2)

        logger.info(f"LeaderPersistenceEngine scored {len(all_results)} stocks")
        return all_results

    # ------------------------------------------------------------------
    # 数据对齐
    # ------------------------------------------------------------------

    @staticmethod
    def _align_to_etf(sdf: pd.DataFrame,
                      etf_dates_arr: Optional[np.ndarray],
                      etf_close_tail: np.ndarray,
                      etf_vol_tail: np.ndarray,
                      extra: int,
                      lookback: int,
                      ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """将个股数据对齐到ETF的交易日期。"""
        total = extra + lookback

        if etf_dates_arr is not None and 'trade_date' in sdf.columns:
            sdf_a = sdf[['trade_date', 'close', 'high', 'low', 'vol']].copy()
            sdf_a = sdf_a.set_index('trade_date')

            etf_idx = pd.DataFrame({'trade_date': etf_dates_arr[-total:],
                                    'etf_close': etf_close_tail})
            etf_idx = etf_idx.set_index('trade_date')

            merged = sdf_a.join(etf_idx, how='inner')
            if len(merged) < lookback:
                return None

            sc = np.asarray(merged['close'].values, dtype=np.float64)
            sv = np.asarray(merged['vol'].values, dtype=np.float64)
            sh = np.asarray(merged['high'].values, dtype=np.float64)
            sl = np.asarray(merged['low'].values, dtype=np.float64)
            return sc, sv, sh, sl
        else:
            n_stock = len(sdf)
            if n_stock < total:
                return None
            sc = np.asarray(sdf['close'].values[-total:], dtype=np.float64)
            sv = np.asarray(sdf['vol'].values[-total:], dtype=np.float64)
            sh = np.asarray(sdf['high'].values[-total:], dtype=np.float64)
            sl = np.asarray(sdf['low'].values[-total:], dtype=np.float64)
            return sc, sv, sh, sl

    # ------------------------------------------------------------------
    # 横截面排名矩阵构建
    # ------------------------------------------------------------------

    @staticmethod
    def _build_rank_matrix(stock_closes: Dict[str, np.ndarray],
                           etf_close_tail: np.ndarray,
                           extra: int,
                           lookback: int,
                           ) -> Tuple[Optional[np.ndarray], List[str]]:
        """构建历史排名矩阵 (lookback x n_stocks)。"""
        stock_codes = list(stock_closes.keys())
        n_stocks = len(stock_codes)
        if n_stocks < 2 or extra < 2:
            return None, stock_codes

        rank_matrix = np.full((lookback, n_stocks), np.nan, dtype=np.float64)
        etf_returns = etf_close_tail[extra:] / np.maximum(etf_close_tail[:-extra], 1e-10) - 1.0

        for j, ts_code in enumerate(stock_codes):
            sc = stock_closes[ts_code]
            if len(sc) < extra + lookback:
                continue
            trailing_20 = sc[extra:] / np.maximum(sc[:-extra], 1e-10) - 1.0
            rel_ret = trailing_20 - etf_returns
            n_valid = min(len(rel_ret), lookback)
            if n_valid > 0:
                rank_matrix[-n_valid:, j] = rel_ret[-n_valid:]

        valid_rows = np.isfinite(rank_matrix).all(axis=1)
        if valid_rows.sum() < 5:
            return None, stock_codes

        rank_matrix = rank_matrix[valid_rows]
        if rank_matrix.shape[0] < 2:
            return None, stock_codes

        ranks = np.argsort(np.argsort(-rank_matrix, axis=1), axis=1) + 1
        return ranks, stock_codes

    # ------------------------------------------------------------------
    # 各持续性维度计算（全向量化）
    # ------------------------------------------------------------------

    @staticmethod
    def _count_lead_days(stock_close: np.ndarray,
                         etf_close: np.ndarray,
                         lookback: int) -> int:
        """统计个股日涨幅跑赢ETF的天数。"""
        n = min(len(stock_close), len(etf_close))
        if n < 5:
            return 0

        sc = stock_close[-n:]
        ec = etf_close[-n:]

        sr = np.diff(sc, prepend=sc[0:1]) / np.maximum(np.roll(sc, 1), 1e-10)
        er = np.diff(ec, prepend=ec[0:1]) / np.maximum(np.roll(ec, 1), 1e-10)

        lead = sr[-lookback:] > er[-lookback:]
        return int(np.sum(lead[np.isfinite(lead)]))

    @staticmethod
    def _count_breakouts(stock_close: np.ndarray,
                         stock_vol: np.ndarray,
                         lookback: int) -> int:
        """统计成交量确认的突破次数。"""
        n = len(stock_close)
        if n < 30:
            return 0

        vol_ema_val = ema(stock_vol, 20)
        vol_surge = stock_vol / np.maximum(vol_ema_val, 1e-10) > 1.5

        returns = np.diff(stock_close, prepend=stock_close[0:1]) / np.maximum(
            np.roll(stock_close, 1), 1e-10)

        lookback_window = min(lookback, n)
        surge = vol_surge[-lookback_window:] & (returns[-lookback_window:] > 0.02)

        return int(np.sum(surge[np.isfinite(surge)]))

    @staticmethod
    def _calc_pullback_success(stock_close: np.ndarray,
                               lookback: int) -> float:
        """计算回调修复成功率 (0-100)。"""
        n = len(stock_close)
        if n < 20:
            return 50.0

        close_window = stock_close[-min(lookback + 10, n):]
        m = len(close_window)
        if m < 15:
            return 50.0

        returns = np.diff(close_window, prepend=close_window[0:1]) / np.maximum(
            np.roll(close_window, 1), 1e-10)

        pullback_days = returns < -0.03
        pullback_indices = np.where(pullback_days)[0]

        if len(pullback_indices) == 0:
            return 50.0

        successful = 0
        total = 0
        for idx in pullback_indices:
            if idx < 5:
                continue
            pre_level = close_window[idx - 1]
            recovery_window = close_window[idx + 1:min(idx + 6, m)]
            if len(recovery_window) == 0:
                continue
            total += 1
            if np.any(recovery_window >= pre_level):
                successful += 1

        if total == 0:
            return 50.0

        return float(successful) / max(total, 1) * 100.0

    @staticmethod
    def _longest_up_streak(stock_close: np.ndarray,
                           lookback: int) -> int:
        """计算最长连续上涨天数。"""
        n = len(stock_close)
        if n < 5:
            return 0

        close_window = stock_close[-min(lookback, n):]
        if len(close_window) < 2:
            return 0

        direction = np.diff(close_window) > 0
        return int(LeaderPersistenceEngine._max_consecutive_true(direction))

    @staticmethod
    def _max_consecutive_leq(arr: np.ndarray, threshold: int) -> int:
        """计算数组中连续 <= threshold 的最大长度。"""
        if len(arr) == 0:
            return 0
        cond = arr <= threshold
        s = pd.Series(cond.astype(int))
        groups = (~cond).cumsum()
        streaks = s.groupby(groups).cumcount() + 1
        streaks = streaks.where(cond, 0)
        return int(streaks.max())

    @staticmethod
    def _max_consecutive_true(condition: np.ndarray) -> int:
        """计算布尔数组中连续True的最大长度。"""
        if len(condition) == 0:
            return 0
        s = pd.Series(condition.astype(int))
        groups = (~condition).cumsum()
        streaks = s.groupby(groups).cumcount() + 1
        streaks = streaks.where(condition, 0)
        return int(streaks.max())

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
