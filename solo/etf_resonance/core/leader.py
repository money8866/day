"""Leader Score - Part 4 of the Resonance System.

Computes how much each constituent stock leads its ETF.

Components:
- Relative Strength (25%): stock_return vs ETF_return
- Relative Momentum (15%): acceleration comparison
- Breakout Strength (15%): new-high proximity vs ETF
- Trend Stability (10%): consistency of outperformance
- Correlation (10%): stock-ETF return correlation
- Beta (10%): stock-ETF beta
- Liquidity (5%): volume ranking within ETF constituents
- Institution Score (10%): institutional quality proxy

LeaderScore: 0-100
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from etf_resonance.utils.indicators import (
    ema, slope, atr, rolling_corr, rolling_beta,
    sharpe_ratio, calmar_ratio, max_drawdown,
    rank_score, normalize,
)
from etf_resonance.utils.helpers import safe_div, timeit, Config


@dataclass
class LeaderResult:
    """Per-stock leader scoring result."""
    ts_code: str
    name: str
    etf_code: str
    leader_score: float        # 0-100 composite
    relative_strength: float
    relative_momentum: float
    relative_volume: float
    relative_high: float
    breakout_strength: float
    trend_stability: float
    correlation: float
    beta: float
    sharpe: float
    calmar: float
    drawdown: float
    liquidity_score: float
    institution_score: float
    rank_in_etf: int


class LeaderScorer:
    """Compute Leader Score (0-100) for each stock within its ETF."""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("leader", {}) if config else {}
        self.rs_w = cfg.get("relative_strength_weight", 0.25)
        self.rm_w = cfg.get("relative_momentum_weight", 0.15)
        self.bo_w = cfg.get("breakout_weight", 0.15)
        self.ts_w = cfg.get("trend_stability_weight", 0.10)
        self.corr_w = cfg.get("correlation_weight", 0.10)
        self.beta_w = cfg.get("beta_weight", 0.10)
        self.liq_w = cfg.get("liquidity_weight", 0.05)
        self.inst_w = cfg.get("institution_weight", 0.10)
        self.period = cfg.get("relative_period", 60)

    @timeit
    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              etf_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]],
              etf_trend_scores: Dict[str, float]
              ) -> Dict[str, List[LeaderResult]]:
        """Score all stocks within all qualifying ETFs.

        Returns:
            Dict of {etf_code: [LeaderResult, ...]}
        """
        results: Dict[str, List[LeaderResult]] = {}

        for etf_code, stock_codes in constituents.items():
            if etf_code not in etf_data:
                continue
            etf_df = etf_data[etf_code]
            etf_close = etf_df["close"].values.astype(np.float64)

            stock_results = []
            for stock_code in stock_codes:
                if stock_code not in stock_data:
                    continue
                stock_df = stock_data[stock_code]
                if stock_df.empty or len(stock_df) < 60:
                    continue

                result = self._score_single(
                    stock_code, stock_df, etf_close, etf_code
                )
                if result is not None:
                    stock_results.append(result)

            if stock_results:
                # Assign rank within ETF
                stock_results.sort(key=lambda x: -x.leader_score)
                for i, r in enumerate(stock_results):
                    r.rank_in_etf = i + 1
                results[etf_code] = stock_results

        return results

    def _score_single(self, stock_code: str, stock_df: pd.DataFrame,
                      etf_close: np.ndarray, etf_code: str) -> Optional[LeaderResult]:
        """Score a single stock against its ETF."""
        try:
            P = self.period
            close = stock_df["close"].values.astype(np.float64)
            high = stock_df["high"].values.astype(np.float64)
            low = stock_df["low"].values.astype(np.float64)
            vol = stock_df["vol"].values.astype(np.float64)

            # Align lengths
            min_len = min(len(close), len(etf_close))
            close = close[-min_len:]
            etf_c = etf_close[-min_len:]
            high = high[-min_len:]
            low = low[-min_len:]
            vol = vol[-min_len:]

            lookback = min(P, len(close))
            if lookback < 20:
                return None

            c = close[-lookback:]
            ec = etf_c[-lookback:]

            # 1. Relative Strength: stock_ret - etf_ret
            stock_ret = (c[-1] / c[0] - 1) * 100
            etf_ret = (ec[-1] / ec[0] - 1) * 100
            relative_strength = stock_ret - etf_ret

            # 2. Relative Momentum: recent performance gap
            sr_20 = (close[-1] / close[-min(20, len(close))] - 1) * 100 if len(close) >= 20 else 0
            er_20 = (etf_c[-1] / etf_c[-min(20, len(etf_c))] - 1) * 100 if len(etf_c) >= 20 else 0
            relative_momentum = sr_20 - er_20

            # 3. Relative Volume
            stock_vol_ma = np.mean(vol[-lookback:])
            etf_vol = np.mean(vol[-lookback:])  # This should be ETF volume
            relative_volume = safe_div(np.array([stock_vol_ma]),
                                       np.array([etf_vol]))[0]

            # 4. Relative High (distance from 60d high)
            stock_hh = np.max(close[-60:]) if len(close) >= 60 else np.max(close)
            etf_hh = np.max(etf_c[-60:]) if len(etf_c) >= 60 else np.max(etf_c)
            stock_hh_dist = (close[-1] / stock_hh - 1) * 100
            etf_hh_dist = (etf_c[-1] / etf_hh - 1) * 100
            relative_high = etf_hh_dist - stock_hh_dist  # positive = stock closer to high

            # 5. Breakout Strength
            stock_rank = rank_score(close, P)[-1]
            etf_rank = rank_score(etf_c, P)[-1]
            breakout_strength = stock_rank - etf_rank

            # 6. Trend Stability (std of relative returns - lower = more stable)
            stock_daily_ret = np.diff(c) / c[:-1]
            etf_daily_ret = np.diff(ec) / ec[:-1]
            rel_ret = stock_daily_ret - etf_daily_ret
            trend_stability = max(0, 100 - np.std(rel_ret) * 1000)

            # 7. Correlation
            corr = rolling_corr(stock_daily_ret, etf_daily_ret, min(20, len(stock_daily_ret)))
            correlation = float(corr[-1]) if not np.isnan(corr[-1]) else 0

            # 8. Beta
            beta = rolling_beta(stock_daily_ret, etf_daily_ret, min(60, len(stock_daily_ret)))
            beta_val = float(beta[-1]) if not np.isnan(beta[-1]) else 1.0

            # 9. Sharpe & Calmar
            sharpe = sharpe_ratio(stock_daily_ret)
            calmar = calmar_ratio(close)
            drawdown = max_drawdown(close)

            # 10. Liquidity Score (volume percentile within its own history)
            vol_rank = rank_score(vol, 60)[-1]
            liquidity_score = vol_rank

            # 11. Institution Score (proxy: stable vol + moderate size + low drawdown)
            inst_stability = max(0, 100 - np.std(vol[-20:]) / np.maximum(np.mean(vol[-20:]), 1) * 200)
            inst_dd = max(0, 100 - drawdown * 2)
            institution_score = 0.5 * inst_stability + 0.5 * inst_dd

            # ════════════════════════════════════
            # Composite Leader Score
            # ════════════════════════════════════

            norm_rs = np.clip((relative_strength + 50) / 100 * 100, 0, 100)
            norm_rm = np.clip((relative_momentum + 30) / 60 * 100, 0, 100)
            norm_bo = np.clip(breakout_strength + 50, 0, 100)
            norm_ts = trend_stability
            norm_corr = np.clip((correlation + 1) / 2 * 100, 0, 100)
            norm_beta = np.clip((2 - abs(beta_val - 1)) / 2 * 100, 0, 100)
            norm_liq = liquidity_score
            norm_inst = institution_score

            leader_score = (
                self.rs_w * norm_rs +
                self.rm_w * norm_rm +
                self.bo_w * norm_bo +
                self.ts_w * norm_ts +
                self.corr_w * norm_corr +
                self.beta_w * norm_beta +
                self.liq_w * norm_liq +
                self.inst_w * norm_inst
            )
            leader_score = np.clip(leader_score, 0, 100)

            name_col = "name" if "name" in stock_df.columns else "ts_code"
            stock_name = stock_df[name_col].iloc[-1] if name_col in stock_df.columns and len(stock_df) > 0 else stock_code

            return LeaderResult(
                ts_code=stock_code,
                name=str(stock_name),
                etf_code=etf_code,
                leader_score=round(float(leader_score), 1),
                relative_strength=round(float(relative_strength), 2),
                relative_momentum=round(float(relative_momentum), 2),
                relative_volume=round(float(relative_volume), 2),
                relative_high=round(float(relative_high), 2),
                breakout_strength=round(float(breakout_strength), 1),
                trend_stability=round(float(trend_stability), 1),
                correlation=round(float(correlation), 3),
                beta=round(float(beta_val), 2),
                sharpe=round(float(sharpe), 3),
                calmar=round(float(calmar), 2),
                drawdown=round(float(drawdown), 2),
                liquidity_score=round(float(liquidity_score), 1),
                institution_score=round(float(institution_score), 1),
                rank_in_etf=0,
            )

        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.error(f"LeaderScorer failed for {stock_code}: {e}")
            return None
