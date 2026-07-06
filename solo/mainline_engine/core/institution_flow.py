"""机构资金流引擎 — 对个股/ETF 的资金面进行 0-100 综合打分。"""

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
class CapitalScoreResult:
    ts_code: str
    etf_code: str = ""
    continuous_inflow_score: float = 0.0
    institution_participation: float = 0.0
    capital_persistence: float = 0.0
    volume_amplification: float = 0.0
    capital_concentration: float = 0.0
    capital_acceleration: float = 0.0
    capital_score: float = 0.0


class InstitutionFlowEngine:
    """机构资金流引擎。

    计算连续净流入、机构参与度、资金持续性、成交额放大、
    资金集中度、主力加速度等 6 个维度，加权得到 0-100 资金得分。
    """

    # 资金流列名的可能别名
    _NET_MF_COLS = ['net_mf_vol', 'net_amount', 'net_mf_amount', 'net_inflow']
    _BUY_LG_COLS = ['buy_lg_vol', 'buy_lg_amount', 'buy_large']
    _SELL_LG_COLS = ['sell_lg_vol', 'sell_lg_amount', 'sell_large']
    _BUY_ELG_COLS = ['buy_elg_vol', 'buy_elg_amount', 'buy_exlarge']
    _SELL_ELG_COLS = ['sell_elg_vol', 'sell_elg_amount', 'sell_exlarge']
    _VOL_COLS = ['vol', 'volume', 'amount', 'turnover']

    def __init__(self, config: dict):
        self.cfg = config.get('institution_flow', {})
        self._log_config()

    def _log_config(self) -> None:
        logger.debug(f"InstitutionFlowEngine config: period={self.cfg.get('moneyflow_period', 20)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              moneyflow_data: Dict[str, pd.DataFrame],
              north_flow: Optional[pd.DataFrame] = None,
              margin_data: Optional[Dict[str, pd.DataFrame]] = None,
              billboard_data: Optional[pd.DataFrame] = None,
              fund_portfolio: Optional[Dict[str, pd.DataFrame]] = None,
              ) -> Dict[str, CapitalScoreResult]:
        """对所有标的进行资金面打分。

        Parameters
        ----------
        moneyflow_data : dict
            {ts_code: DataFrame}，每个 DataFrame 至少包含资金流字段。
            支持列名:
            - net_mf_vol / net_amount / net_inflow : 净流入
            - buy_lg_vol / sell_lg_vol : 大单买卖
            - buy_elg_vol / sell_elg_vol : 特大单买卖
            - vol / volume / amount : 成交额
        north_flow : pd.DataFrame, optional
            北向资金日频数据，列 ['trade_date', 'net_amount', ...]。
        margin_data : dict, optional
            {ts_code: DataFrame} 融资融券数据。
        billboard_data : pd.DataFrame, optional
            龙虎榜数据。
        fund_portfolio : dict, optional
            {ts_code: DataFrame} 基金持仓数据。

        Returns
        -------
        dict[str, CapitalScoreResult]
        """
        if not moneyflow_data:
            logger.warning("moneyflow_data is empty, returning {}")
            return {}

        period = self.cfg.get('moneyflow_period', 20)
        results: Dict[str, CapitalScoreResult] = {}
        metrics_list: List[dict] = []

        for ts_code, df in moneyflow_data.items():
            if df is None or df.empty:
                logger.debug(f"Skipping {ts_code}: empty DataFrame")
                continue
            if len(df) < period + 5:
                logger.debug(f"Skipping {ts_code}: insufficient rows ({len(df)} < {period + 5})")
                continue

            try:
                metrics = self._compute_capital_metrics(ts_code, df, period)
                if metrics is not None:
                    metrics_list.append(metrics)
            except Exception as exc:
                logger.error(f"Error computing capital metrics for {ts_code}: {exc}")
                continue

        if not metrics_list:
            logger.warning("No stock passed capital metric computation, returning {}")
            return {}

        # 横截面归一化
        n = len(metrics_list)
        inflow_v = np.array([m['continuous_inflow'] for m in metrics_list], dtype=np.float64)
        participation_v = np.array([m['institution_participation'] for m in metrics_list], dtype=np.float64)
        persistence_v = np.array([m['capital_persistence'] for m in metrics_list], dtype=np.float64)
        vol_amp_v = np.array([m['volume_amplification'] for m in metrics_list], dtype=np.float64)
        concentration_v = np.array([m['capital_concentration'] for m in metrics_list], dtype=np.float64)
        acceleration_v = np.array([m['capital_acceleration'] for m in metrics_list], dtype=np.float64)

        # winsorize + normalize -> 0-100 sub-score
        inflow_s = self._to_score(winsorize(inflow_v, 0.01))
        participation_s = self._to_score(winsorize(participation_v, 0.01))
        persistence_s = self._to_score(winsorize(persistence_v, 0.01))
        vol_amp_s = self._to_score(winsorize(vol_amp_v, 0.01))
        concentration_s = self._to_score(winsorize(concentration_v, 0.01))
        acceleration_s = self._to_score(winsorize(acceleration_v, 0.01))

        w_inflow = self.cfg.get('continuous_inflow_weight', 0.25)
        w_part = self.cfg.get('institution_participation_weight', 0.20)
        w_persist = self.cfg.get('capital_persistence_weight', 0.15)
        w_vol = self.cfg.get('volume_amplification_weight', 0.15)
        w_conc = self.cfg.get('capital_concentration_weight', 0.15)
        w_accel = self.cfg.get('capital_acceleration_weight', 0.10)

        final_scores = (
            inflow_s * w_inflow +
            participation_s * w_part +
            persistence_s * w_persist +
            vol_amp_s * w_vol +
            concentration_s * w_conc +
            acceleration_s * w_accel
        )
        final_scores = np.clip(final_scores, 0.0, 100.0)

        for m, fs, i_s, p_s, pe_s, v_s, c_s, a_s in zip(
            metrics_list, final_scores,
            inflow_s, participation_s, persistence_s,
            vol_amp_s, concentration_s, acceleration_s,
        ):
            result = CapitalScoreResult(
                ts_code=m['ts_code'],
                etf_code=m.get('etf_code', ''),
                continuous_inflow_score=round(float(i_s), 2),
                institution_participation=round(float(p_s), 2),
                capital_persistence=round(float(pe_s), 2),
                volume_amplification=round(float(v_s), 2),
                capital_concentration=round(float(c_s), 2),
                capital_acceleration=round(float(a_s), 2),
                capital_score=round(float(fs), 2),
            )
            results[m['ts_code']] = result

        logger.info(f"InstitutionFlowEngine scored {len(results)} targets")
        return results

    # ------------------------------------------------------------------
    # 资金流指标计算（全向量化）
    # ------------------------------------------------------------------

    def _compute_capital_metrics(self, ts_code: str, df: pd.DataFrame,
                                 period: int) -> Optional[dict]:
        """对单个标的计算所有资金流指标。"""
        close_series = self._safe_col(df, 'close')
        if close_series is None:
            return None

        close = np.asarray(close_series, dtype=np.float64)
        net_mf_raw = self._pick_col(df, self._NET_MF_COLS)
        buy_lg_raw = self._pick_col(df, self._BUY_LG_COLS)
        sell_lg_raw = self._pick_col(df, self._SELL_LG_COLS)
        buy_elg_raw = self._pick_col(df, self._BUY_ELG_COLS)
        sell_elg_raw = self._pick_col(df, self._SELL_ELG_COLS)
        vol_raw = self._pick_col(df, self._VOL_COLS)

        # 转换为 ndarray
        net_mf = np.asarray(net_mf_raw, dtype=np.float64) if net_mf_raw is not None else None
        buy_lg = np.asarray(buy_lg_raw, dtype=np.float64) if buy_lg_raw is not None else None
        sell_lg = np.asarray(sell_lg_raw, dtype=np.float64) if sell_lg_raw is not None else None
        buy_elg = np.asarray(buy_elg_raw, dtype=np.float64) if buy_elg_raw is not None else None
        sell_elg = np.asarray(sell_elg_raw, dtype=np.float64) if sell_elg_raw is not None else None
        vol_raw_arr = np.asarray(vol_raw, dtype=np.float64) if vol_raw is not None else None

        n = len(close)

        # ------ 1. 连续净流入 ------
        if net_mf is not None:
            net_arr = np.where(np.isfinite(net_mf), net_mf, 0.0)
            net_sum = pd.Series(net_arr).rolling(period, min_periods=1).sum().values
            continuous_inflow = net_sum[-1] / np.maximum(close[-1], 1e-10)
        elif buy_lg is not None and sell_lg is not None:
            buy_arr = np.where(np.isfinite(buy_lg), buy_lg, 0.0)
            sell_arr = np.where(np.isfinite(sell_lg), sell_lg, 0.0)
            net_arr = buy_arr - sell_arr
            net_sum = pd.Series(net_arr).rolling(period, min_periods=1).sum().values
            continuous_inflow = net_sum[-1] / np.maximum(close[-1], 1e-10)
        else:
            continuous_inflow = 0.0

        # ------ 2. 机构参与度 (大单+特大单) / 总成交额 ------
        if (buy_lg is not None and sell_lg is not None) and vol_raw_arr is not None:
            buy_arr = np.where(np.isfinite(buy_lg), buy_lg, 0.0)
            sell_arr = np.where(np.isfinite(sell_lg), sell_lg, 0.0)
            vol_arr = np.where(np.isfinite(vol_raw_arr) & (vol_raw_arr > 0), vol_raw_arr, np.nan)

            lg_total = buy_arr + sell_arr
            if buy_elg is not None and sell_elg is not None:
                elg_buy = np.where(np.isfinite(buy_elg), buy_elg, 0.0)
                elg_sell = np.where(np.isfinite(sell_elg), sell_elg, 0.0)
                lg_total = lg_total + elg_buy + elg_sell

            participation_ratio = lg_total / np.maximum(vol_arr, 1e-10)
            participation_ratio = np.where(
                np.isfinite(participation_ratio),
                participation_ratio, 0.0,
            )
            participation_ratio = pd.Series(participation_ratio).rolling(
                period, min_periods=1
            ).mean().values
            institution_participation = float(participation_ratio[-1]) if n > 0 else 0.0
        else:
            institution_participation = 0.0

        # ------ 3. 资金持续性（连续净流入天数） ------
        if net_mf is not None:
            pos_flow = net_mf > 0
            pos_flow = pos_flow[np.isfinite(pos_flow)]
            capital_persistence = float(self._consecutive_count_at_end(pos_flow))
        elif buy_lg is not None and sell_lg is not None:
            pos_flow = (buy_lg - sell_lg) > 0
            pos_flow = pos_flow[np.isfinite(pos_flow)]
            capital_persistence = float(self._consecutive_count_at_end(pos_flow))
        else:
            capital_persistence = 0.0

        # ------ 4. 成交额放大 ------
        if vol_raw_arr is not None:
            vol_ema_val = ema(vol_raw_arr, period)
            vol_ratio_arr = vol_raw_arr / np.maximum(vol_ema_val, 1e-10)
            vol_ratio_arr = np.where(np.isfinite(vol_ratio_arr), vol_ratio_arr, 1.0)
            volume_amplification = float(vol_ratio_arr[-1] - 1.0) if n > 0 else 0.0
        else:
            volume_amplification = 0.0

        # ------ 5. 资金集中度 (大单净流入 / 总净流入) ------
        if (buy_lg is not None and sell_lg is not None) and net_mf is not None:
            net_lg = np.where(np.isfinite(buy_lg), buy_lg, 0.0) - np.where(np.isfinite(sell_lg), sell_lg, 0.0)
            net_total = np.where(np.isfinite(net_mf), net_mf, 0.0)
            lg_net_sum = pd.Series(net_lg).rolling(period, min_periods=1).sum().values
            total_net_sum = pd.Series(net_total).rolling(period, min_periods=1).sum().values
            concentration = lg_net_sum[-1] / np.maximum(np.abs(total_net_sum[-1]), 1e-10)
            capital_concentration = float(concentration)
        elif buy_lg is not None and sell_lg is not None:
            net_lg = np.where(np.isfinite(buy_lg), buy_lg, 0.0) - np.where(np.isfinite(sell_lg), sell_lg, 0.0)
            lg_net_sum = pd.Series(net_lg).rolling(period, min_periods=1).sum().values
            lg_abs_sum = pd.Series(np.abs(net_lg)).rolling(period, min_periods=1).sum().values
            concentration = lg_net_sum[-1] / np.maximum(lg_abs_sum[-1], 1e-10)
            capital_concentration = float(concentration)
        else:
            capital_concentration = 0.0

        # ------ 6. 主力资金加速度 (净流入的变化率) ------
        if net_mf is not None:
            net_arr = np.where(np.isfinite(net_mf), net_mf, 0.0)
            net_sma = sma(net_arr, period // 2)
            net_sma_prev = np.roll(net_sma, period // 4)
            net_sma_prev[:period // 4] = np.nan
            accel = (net_sma - net_sma_prev) / np.maximum(np.abs(net_sma_prev), 1e-10)
            accel = np.where(np.isfinite(accel), accel, 0.0)
            capital_acceleration = float(accel[-1]) if n > 0 and np.isfinite(accel[-1]) else 0.0
        elif buy_lg is not None and sell_lg is not None:
            net_lg = np.where(np.isfinite(buy_lg), buy_lg, 0.0) - np.where(np.isfinite(sell_lg), sell_lg, 0.0)
            net_sma = sma(net_lg, period // 2)
            net_sma_prev = np.roll(net_sma, period // 4)
            net_sma_prev[:period // 4] = np.nan
            accel = (net_sma - net_sma_prev) / np.maximum(np.abs(net_sma_prev), 1e-10)
            accel = np.where(np.isfinite(accel), accel, 0.0)
            capital_acceleration = float(accel[-1]) if n > 0 and np.isfinite(accel[-1]) else 0.0
        else:
            capital_acceleration = 0.0

        metrics = {
            'ts_code': ts_code,
            'continuous_inflow': continuous_inflow,
            'institution_participation': institution_participation,
            'capital_persistence': capital_persistence,
            'volume_amplification': volume_amplification,
            'capital_concentration': capital_concentration,
            'capital_acceleration': capital_acceleration,
        }
        return metrics

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_col(df: pd.DataFrame, col: str) -> Optional[pd.Series]:
        if col in df.columns:
            return df[col]
        return None

    @staticmethod
    def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[pd.Series]:
        """从多个候选列名中选取第一个存在的列。"""
        for col in candidates:
            if col in df.columns:
                return df[col]
        return None

    @staticmethod
    def _to_score(arr: np.ndarray) -> np.ndarray:
        """Min-Max 归一化到 [0, 100]。"""
        a = np.asarray(arr, dtype=np.float64)
        valid = a[np.isfinite(a)]
        if len(valid) == 0:
            return np.full_like(a, 50.0)
        mn, mx = np.nanmin(a), np.nanmax(a)
        if mx <= mn or not np.isfinite(mx - mn):
            return np.full_like(a, 50.0)
        return np.clip((a - mn) / (mx - mn) * 100.0, 0.0, 100.0)

    @staticmethod
    def _consecutive_count_at_end(condition: np.ndarray) -> int:
        """计算数组尾部连续 True 的个数（全向量化）。"""
        if len(condition) == 0:
            return 0
        false_positions = np.where(~condition)[0]
        if len(false_positions) == 0:
            return len(condition)
        last_false = int(false_positions[-1])
        return len(condition) - last_false - 1
