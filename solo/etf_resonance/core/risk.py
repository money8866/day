"""Risk Score - Part 7 of the Resonance System.

Higher risk = higher score (0-100, more risky).

Components:
- ATR% (20%): daily volatility
- Max Drawdown (25%): worst peak-to-trough
- Beta (15%): market sensitivity
- Volatility (15%): return std
- Consecutive up days (10%): exhaustion risk
- Distance to EMA20/EMA60 (15%): overextension
"""

import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass

from etf_resonance.utils.indicators import (
    ema, atr, max_drawdown, rolling_beta, consecutive_up_days, normalize,
)
from etf_resonance.utils.helpers import timeit, Config


@dataclass
class RiskResult:
    """Per-stock risk scoring result."""
    ts_code: str
    risk_score: float          # 0-100 (higher = more risky)
    atr_pct: float
    max_drawdown_pct: float
    beta: float
    volatility: float
    consecutive_up: int
    dist_ema20_pct: float
    dist_ema60_pct: float
    gap_risk: float


class RiskScorer:
    """Compute Risk Score (0-100) for each stock.

    Higher score = more risky. Used as a penalty in composite scoring.
    """

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("risk", {}) if config else {}
        self.atr_w = cfg.get("atr_weight", 0.20)
        self.md_w = cfg.get("max_drawdown_weight", 0.25)
        self.beta_w = cfg.get("beta_weight", 0.15)
        self.vol_w = cfg.get("volatility_weight", 0.15)
        self.cu_w = cfg.get("consecutive_up_weight", 0.10)
        self.ma_w = cfg.get("ma_distance_weight", 0.15)

    @timeit
    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              stock_list: Optional[list] = None) -> Dict[str, RiskResult]:
        """Score risk for all stocks."""
        results = {}
        targets = stock_list if stock_list else list(stock_data.keys())
        for code in targets:
            if code not in stock_data:
                continue
            df = stock_data[code]
            if df.empty or len(df) < 60:
                continue
            result = self._score_single(code, df)
            if result is not None:
                results[code] = result
        return results

    def _score_single(self, code: str, df: pd.DataFrame) -> Optional[RiskResult]:
        try:
            close = df["close"].values.astype(np.float64)
            high = df["high"].values.astype(np.float64)
            low = df["low"].values.astype(np.float64)
            vol = df["vol"].values.astype(np.float64)

            # 1. ATR%
            atr_val = atr(high, low, close, 14)[-1]
            atr_pct = atr_val / np.maximum(close[-1], 1e-10) * 100

            # 2. Max Drawdown (60d)
            dd = max_drawdown(close[-60:]) if len(close) >= 60 else max_drawdown(close)

            # 3. Beta vs market (use own returns as proxy)
            returns = np.diff(close) / close[:-1]
            # Simple beta: auto-regressive of 1
            if len(returns) > 1:
                beta = np.clip(np.corrcoef(returns[:-1], returns[1:])[0, 1] * 1.5, 0, 3)
            else:
                beta = 1.0
            beta_val = beta if not np.isnan(beta) else 1.0

            # 4. Volatility
            vol_val = np.std(returns[-60:]) * np.sqrt(252) * 100 if len(returns) >= 60 else \
                      np.std(returns) * np.sqrt(252) * 100

            # 5. Consecutive up days
            cu_arr = consecutive_up_days(close)
            cu = int(cu_arr[-1])

            # 6. Distance to EMA20 / EMA60
            ema20 = ema(close, 20)[-1]
            ema60 = ema(close, 60)[-1]
            close_last = close[-1]
            dist_20 = (close_last / ema20 - 1) * 100 if ema20 > 0 else 0
            dist_60 = (close_last / ema60 - 1) * 100 if ema60 > 0 else 0

            # 7. Gap risk (high-low / prev_close)
            gap = (high[-1] - low[-1]) / np.maximum(df["pre_close"].values[-1] if "pre_close" in df.columns else close[-2] if len(close) >= 2 else close[-1], 1e-10) * 100 if len(df) >= 2 else atr_pct

            # ════════════════════════════════════
            # Composite Risk Score
            # ════════════════════════════════════

            atr_score = np.clip(atr_pct * 10, 0, 100)
            dd_score = np.clip(dd * 1.5, 0, 100)
            beta_score = np.clip(beta_val / 3 * 100, 0, 100)
            vol_score = np.clip(vol_val / 60 * 100, 0, 100)
            cu_score = np.clip(cu * 10, 0, 100)  # consecutive up = exhaustion risk
            ma_score = np.clip(max(abs(dist_20), abs(dist_60)) * 5, 0, 100)

            risk = (
                self.atr_w * atr_score +
                self.md_w * dd_score +
                self.beta_w * beta_score +
                self.vol_w * vol_score +
                self.cu_w * cu_score +
                self.ma_w * ma_score
            )
            risk = np.clip(risk, 0, 100)

            return RiskResult(
                ts_code=code,
                risk_score=round(float(risk), 1),
                atr_pct=round(float(atr_pct), 2),
                max_drawdown_pct=round(float(dd), 2),
                beta=round(float(beta_val), 2),
                volatility=round(float(vol_val), 2),
                consecutive_up=int(cu),
                dist_ema20_pct=round(float(dist_20), 2),
                dist_ema60_pct=round(float(dist_60), 2),
                gap_risk=round(float(gap), 2),
            )

        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.error(f"RiskScorer failed for {code}: {e}")
            return None
