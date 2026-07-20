"""
Chip Alpha Engine V5 — Institutional Trend Intelligence Engine
==============================================================
Architecture: Point72 / Millennium / Citadel / Two Sigma style

Raw Factors → Feature Processing → Alpha Engine → Risk Engine
                                                          ↓
                              Decision Engine ← Transition Engine ← Trend State Machine

Key principles:
- Alpha and Risk are fully decoupled
- All scores are interpretable via dimension contributions
- State machine transitions are probability-based
- Correlation penalty prevents double-counting
- Backtest API for factor validation
"""
from __future__ import annotations

import os
import json
import math
import time
import warnings
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import deque

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ============================================================
# Configuration (all weights here, fully configurable)
# ============================================================
@dataclass
class AlphaConfig:
    # Structure
    structure_pressure_decay: float = 0.60
    structure_resilience: float = 0.20
    structure_concentration: float = 0.20
    peak_migration_bonus_threshold: float = 90.0
    peak_migration_bonus: float = 3.0

    # Flow
    flow_cre: float = 0.45
    flow_absorption: float = 0.25
    flow_clv: float = 0.15
    flow_volume_quality: float = 0.15

    # Momentum
    momentum_center_velocity: float = 0.30
    momentum_chip_momentum: float = 0.30
    momentum_winning_expansion: float = 0.25
    momentum_acceleration: float = 0.15

    # Composite
    composite_structure: float = 0.45
    composite_flow: float = 0.30
    composite_momentum: float = 0.25


@dataclass
class RiskConfig:
    momentum_exhaustion: float = 0.20
    profit_crowding: float = 0.20
    distribution: float = 0.20
    structure_breakdown: float = 0.20
    volatility_expansion: float = 0.10
    liquidity_risk: float = 0.10


@dataclass
class FeatureConfig:
    zscore_clip: float = 3.0  # ±3σ winsorize
    correlation_threshold: float = 0.80
    missing_fill_value: float = 50.0  # neutral score


# ============================================================
# Feature Processing
# ============================================================
class FeatureProcessor:
    """
    Institutional-grade feature processing pipeline:
    1. Z-Score standardization
    2. Winsorize (clip at ±3σ)
    3. Missing value imputation
    4. Correlation detection with penalty
    """

    def __init__(self, config: Optional[FeatureConfig] = None):
        self.config = config or FeatureConfig()

    @staticmethod
    def zscore(series: np.ndarray) -> np.ndarray:
        """Z-Score with zero-variance guard."""
        std = np.nanstd(series)
        if std < 1e-8:
            return np.zeros_like(series)
        return (series - np.nanmean(series)) / std

    @staticmethod
    def winsorize(series: np.ndarray, clip: float = 3.0) -> np.ndarray:
        """Clip extreme values at ±clip σ."""
        mean = np.nanmean(series)
        std = np.nanstd(series)
        if std < 1e-8:
            return series
        lower = mean - clip * std
        upper = mean + clip * std
        return np.clip(series, lower, upper)

    @staticmethod
    def fill_missing(series: np.ndarray, fill_value: float = 50.0) -> np.ndarray:
        """Replace NaN/inf with fill_value."""
        s = np.where(np.isfinite(series), series, fill_value)
        return s

    def process(self, scores: np.ndarray) -> np.ndarray:
        """Full pipeline: winsorize → zscore → fill → rescale to 0~100."""
        s = self.winsorize(scores, self.config.zscore_clip)
        s = self.zscore(s)
        s = self.fill_missing(s, 0.0)
        # Rescale z-score to 0~100 (z=0 → 50, z=±3 → 100/0)
        s = np.clip(s * 50 / 3 + 50, 0, 100)
        return s

    @staticmethod
    def correlation_matrix(factor_scores: Dict[str, np.ndarray]) -> pd.DataFrame:
        """Compute Pearson correlation matrix across factors."""
        names = list(factor_scores.keys())
        arr = np.column_stack([factor_scores[n] for n in names])
        df = pd.DataFrame(arr, columns=names)
        return df.corr()

    @staticmethod
    def correlation_penalty(corr_matrix: pd.DataFrame, threshold: float = 0.80) -> Dict[str, float]:
        """
        For each factor pair with |corr| > threshold, reduce weight.
        Returns penalty factors (0~1 multiplier) per factor.
        """
        n = len(corr_matrix)
        factors = list(corr_matrix.columns)
        penalties = {f: 1.0 for f in factors}
        for i in range(n):
            for j in range(i + 1, n):
                corr_val = abs(corr_matrix.iloc[i, j])
                if corr_val > threshold and not pd.isna(corr_val):
                    f1, f2 = factors[i], factors[j]
                    # Each gets penalty proportional to excess correlation
                    penalty = threshold / corr_val
                    penalties[f1] = min(penalties[f1], penalty)
                    penalties[f2] = min(penalties[f2], penalty)
        return penalties


# ============================================================
# Alpha Engine V5
# ============================================================
class AlphaEngineV5:
    """
    Three-dimension alpha:
      Structure:  trend foundation (is the structure healthy?)
      Flow:       capital flow (is money still coming in?)
      Momentum:   trend acceleration (is the trend speeding up?)
    """

    def __init__(self, config: Optional[AlphaConfig] = None):
        self.cfg = config or AlphaConfig()

    def compute(self, factor_scores: Dict[str, float]) -> Dict[str, Any]:
        """Compute all alpha dimensions from a single stock's factor scores."""
        # --- Structure ---
        pd_score = factor_scores.get('PressureDecay', 50)
        res_score = factor_scores.get('Resilience', 50)
        conc_score = factor_scores.get('Concentration', 50)
        pm_score = factor_scores.get('PeakMigration', 50)

        structure = (
            self.cfg.structure_pressure_decay * pd_score +
            self.cfg.structure_resilience * res_score +
            self.cfg.structure_concentration * conc_score
        )
        # Peak Migration bonus
        if pm_score >= self.cfg.peak_migration_bonus_threshold:
            structure = min(structure + self.cfg.peak_migration_bonus, 100)

        # --- Flow ---
        cre_score = factor_scores.get('CRE', 50)
        ab_score = factor_scores.get('Absorption', 50)
        clv_score = factor_scores.get('CLV', ab_score)  # CLV from absorption
        vq_score = factor_scores.get('VolumeQuality', 50)

        flow = (
            self.cfg.flow_cre * cre_score +
            self.cfg.flow_absorption * ab_score +
            self.cfg.flow_clv * clv_score +
            self.cfg.flow_volume_quality * vq_score
        )

        # --- Momentum ---
        cv_score = factor_scores.get('CenterVelocity', 50)
        cm_score = factor_scores.get('ChipMomentum', 50)
        we_score = factor_scores.get('WinningExpansion', 50)
        accel_score = factor_scores.get('Acceleration', 50)

        momentum = (
            self.cfg.momentum_center_velocity * cv_score +
            self.cfg.momentum_chip_momentum * cm_score +
            self.cfg.momentum_winning_expansion * we_score +
            self.cfg.momentum_acceleration * accel_score
        )

        # --- Composite ---
        composite = (
            self.cfg.composite_structure * structure +
            self.cfg.composite_flow * flow +
            self.cfg.composite_momentum * momentum
        )

        return {
            'Structure': round(structure, 1),
            'Flow': round(flow, 1),
            'Momentum': round(momentum, 1),
            'Composite': round(composite, 1),
            'Grade': self._grade(composite),
            '_factor_scores': factor_scores,
        }

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 85: return 'AA'
        if score >= 75: return 'A'
        if score >= 65: return 'B+'
        if score >= 55: return 'B'
        if score >= 45: return 'B-'
        if score >= 35: return 'C'
        return 'D'

    @staticmethod
    def structure_conclusion(score: float) -> str:
        if score >= 85: return "Excellent — healthy chip structure"
        if score >= 70: return "Good — structure intact"
        if score >= 55: return "Fair — minor structural weakness"
        return "Poor — structure damaged"

    @staticmethod
    def flow_conclusion(score: float) -> str:
        if score >= 75: return "Strong — capital actively flowing in"
        if score >= 60: return "Positive — steady capital inflow"
        if score >= 45: return "Neutral — capital flow weakening"
        return "Weak — capital outflow risk"

    @staticmethod
    def momentum_conclusion(score: float) -> str:
        if score >= 75: return "Strong — trend accelerating"
        if score >= 60: return "Positive — trend intact"
        if score >= 45: return "Neutral — momentum fading"
        return "Weak — trend reversing"


# ============================================================
# Risk Engine (fully decoupled from Alpha)
# ============================================================
class RiskEngine:
    """
    Six-dimensional risk assessment. Completely independent of Alpha.
    Higher score = higher risk (worse).
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.cfg = config or RiskConfig()

    def compute(self, factor_scores: Dict[str, float],
                chip_history: Optional[Dict] = None,
                prices: Optional[List[float]] = None,
                vols: Optional[List[float]] = None) -> Dict[str, Any]:
        """Compute all 6 risk dimensions."""
        r = {}

        # 1. Momentum Exhaustion
        r['MomentumExhaustion'] = self._momentum_exhaustion(factor_scores)

        # 2. Profit Crowding
        r['ProfitCrowding'] = self._profit_crowding(factor_scores)

        # 3. Distribution
        r['Distribution'] = self._distribution_signal(factor_scores)

        # 4. Structure Breakdown
        r['StructureBreakdown'] = self._structure_breakdown(factor_scores)

        # 5. Volatility Expansion
        r['VolatilityExpansion'] = self._volatility_expansion(factor_scores, vols)

        # 6. Liquidity Risk
        r['LiquidityRisk'] = self._liquidity_risk(factor_scores)

        # Composite risk
        composite = (
            self.cfg.momentum_exhaustion * r['MomentumExhaustion'] +
            self.cfg.profit_crowding * r['ProfitCrowding'] +
            self.cfg.distribution * r['Distribution'] +
            self.cfg.structure_breakdown * r['StructureBreakdown'] +
            self.cfg.volatility_expansion * r['VolatilityExpansion'] +
            self.cfg.liquidity_risk * r['LiquidityRisk']
        )

        return {
            'dimensions': r,
            'Composite': round(composite, 1),
            'Level': self._level(composite),
        }

    @staticmethod
    def _momentum_exhaustion(fs: Dict) -> float:
        """Momentum slowing down / acceleration turning negative."""
        accel = fs.get('Acceleration', fs.get('ChipMomentum_Accel', 0))
        cm = fs.get('ChipMomentum', 50)
        we = fs.get('WinningExpansion', 50)
        # Low momentum + negative acceleration = high risk
        base = max(0, 50 - cm) * 0.8  # momentum below 50 = risk
        if accel < 0:
            base += min(50, abs(accel) * 10)
        # Winning expansion slowing also adds risk
        if we < 50:
            base += (50 - we) * 0.3
        return min(base, 100)

    @staticmethod
    def _profit_crowding(fs: Dict) -> float:
        """Too many profit-taking chips = risk."""
        we = fs.get('WinningExpansion', 50)
        # Very high winning expansion near peak = crowding risk
        if we > 85:
            return (we - 85) * 3 + 30  # 30~75
        if we > 70:
            return 20 + (we - 70) * 0.5  # 20~27.5
        # Low winning = no crowding
        return max(0, 50 - we) * 0.3

    @staticmethod
    def _distribution_signal(fs: Dict) -> float:
        """Distribution signals from absorption + CLV."""
        ab = fs.get('Absorption', 50)
        clv_val = fs.get('CLV', 0)
        cv = fs.get('CenterVelocity', 50)
        risk = 0
        # Low CLV + high volume = distribution
        if ab < 40:
            risk += (50 - ab) * 0.7
        # Center velocity declining
        if cv < 40:
            risk += (40 - cv) * 0.5
        risk = min(risk, 100)
        return risk

    @staticmethod
    def _structure_breakdown(fs: Dict) -> float:
        """Structure deteriorating."""
        pd_sc = fs.get('PressureDecay', 50)
        res = fs.get('Resilience', 50)
        conc = fs.get('Concentration', 50)
        # Pressure increasing = structure worsening
        risk = 0
        if pd_sc < 40:
            risk += (40 - pd_sc) * 0.6
        if res < 40:
            risk += (40 - res) * 0.4
        if conc < 30 or conc > 95:  # too dispersed or too concentrated
            risk += 15
        return min(risk * 1.5, 100)

    @staticmethod
    def _volatility_expansion(fs: Dict, vols: Optional[List] = None) -> float:
        """Volatility expanding = risk."""
        vol_ratio = fs.get('VolumeQuality_ratio', 1.0)
        if vol_ratio > 2.0:
            return min(50 + (vol_ratio - 2.0) * 25, 100)
        if vol_ratio > 1.5:
            return 30 + (vol_ratio - 1.5) * 40
        if vol_ratio < 0.5:
            return 40 + (0.5 - vol_ratio) * 40
        return 20

    @staticmethod
    def _liquidity_risk(fs: Dict) -> float:
        """Volume shrinking / turnover declining."""
        cre = fs.get('CRE', 50)
        turnovers = fs.get('turnover_rates', [])
        if isinstance(turnovers, (list, np.ndarray)) and len(turnovers) >= 5:
            recent = np.mean(turnovers[-5:])
            prior = np.mean(turnovers[-10:-5]) if len(turnovers) >= 10 else recent
            if prior > 1e-8:
                ratio = recent / prior
                if ratio < 0.5:
                    return 70 + (0.5 - ratio) * 60
                if ratio < 0.7:
                    return 40 + (0.7 - ratio) * 100
        if cre < 30:
            return (30 - cre) * 1.5
        return max(0, 50 - cre) * 0.4

    @staticmethod
    def _level(score: float) -> str:
        if score <= 20: return "Very Low"
        if score <= 40: return "Low"
        if score <= 60: return "Medium"
        if score <= 80: return "High"
        return "Extreme"


# ============================================================
# Trend State Machine (8-state lifecycle)
# ============================================================
TREND_STATES = [
    'Birth', 'Early', 'Expansion', 'Acceleration',
    'Climax', 'Distribution', 'Breakdown', 'Recovery'
]


class TrendStateMachine:
    """
    8-state trend lifecycle with typical profiles for each state.
    """

    # Typical profiles: (structure_min, flow_min, momentum_min,
    #                    structure_max, flow_max, momentum_max, risk_range)
    STATE_PROFILES = {
        'Birth':       (40, 30, 20, 65, 55, 40, (10, 40)),
        'Early':       (55, 40, 40, 80, 65, 65, (10, 35)),
        'Expansion':   (65, 55, 55, 90, 80, 80, (15, 45)),
        'Acceleration':(70, 65, 70, 95, 90, 95, (20, 50)),
        'Climax':      (80, 70, 80, 100, 95, 100, (30, 70)),
        'Distribution':(40, 30, 20, 70, 55, 50, (40, 80)),
        'Breakdown':   (20, 10, 10, 50, 35, 30, (60, 100)),
        'Recovery':    (35, 25, 15, 60, 50, 40, (20, 50)),
    }

    STATE_CYCLE = {
        'Birth': 'Early',
        'Early': 'Expansion',
        'Expansion': 'Acceleration',
        'Acceleration': 'Climax',
        'Climax': 'Distribution',
        'Distribution': 'Breakdown',
        'Breakdown': 'Recovery',
        'Recovery': 'Birth',
    }

    def __init__(self):
        pass

    def classify(self, alpha: Dict) -> str:
        """Classify current trend stage from alpha dimensions."""
        s = alpha['Structure']
        f = alpha['Flow']
        m = alpha['Momentum']
        best_state = 'Early'
        best_dist = float('inf')

        for state, (smin, fmin, mmax_rev, smax, fmax, mmax, _) in self.STATE_PROFILES.items():
            # Check compatibility
            if not (smin <= s <= smax):
                continue
            if not (fmin <= f <= fmax):
                continue
            if not (mmax_rev <= m <= mmax):
                continue
            # Distance to state centroid
            centroid_s = (smin + smax) / 2
            centroid_f = (fmin + fmax) / 2
            centroid_m = (mmax_rev + mmax) / 2
            dist = math.sqrt(
                ((s - centroid_s) / 25) ** 2 +
                ((f - centroid_f) / 25) ** 2 +
                ((m - centroid_m) / 25) ** 2
            )
            if dist < best_dist:
                best_dist = dist
                best_state = state

        # Fallback: closest by Manhattan distance
        if best_dist == float('inf'):
            for state, (smin, fmin, mmax_rev, smax, fmax, mmax, _) in self.STATE_PROFILES.items():
                centroid_s = (smin + smax) / 2
                centroid_f = (fmin + fmax) / 2
                centroid_m = (mmax_rev + mmax) / 2
                dist = abs(s - centroid_s) + abs(f - centroid_f) + abs(m - centroid_m)
                if dist < best_dist:
                    best_dist = dist
                    best_state = state

        return best_state

    @staticmethod
    def state_description(state: str) -> str:
        desc = {
            'Birth': 'Trend is just born. Structure forming, capital starting to accumulate.',
            'Early': 'Trend is early. Structure solidifying, capital flowing in steadily.',
            'Expansion': 'Trend expanding. Structure strong, capital accelerating, momentum building.',
            'Acceleration': 'Trend accelerating rapidly. All dimensions aligned, aggressive positioning.',
            'Climax': 'Trend climaxing. Structure peak, momentum extreme, crowding risk rising.',
            'Distribution': 'Distribution phase. Structure weakening, capital flowing out, momentum fading.',
            'Breakdown': 'Trend broken. Structure damaged, capital fleeing, momentum negative.',
            'Recovery': 'Recovering from breakdown. Structure stabilizing, capital cautiously returning.',
        }
        return desc.get(state, 'Unknown stage')

    @staticmethod
    def state_strategy(state: str) -> str:
        strat = {
            'Birth': 'Monitor. Small position if structure confirms.',
            'Early': 'Accumulate on pullbacks. Core position building.',
            'Expansion': 'Hold core. Add on dips with volume confirmation.',
            'Acceleration': 'Hold. Tighten stops. Consider partial take-profit.',
            'Climax': 'Reduce position. Prepare for reversal.',
            'Distribution': 'Exit or hedge. Do not add.',
            'Breakdown': 'Exit completely. Do not catch falling knife.',
            'Recovery': 'Watch for re-confirmation. Small试探 position.',
        }
        return strat.get(state, 'Wait.')


# ============================================================
# Transition Engine
# ============================================================
class TransitionEngine:
    """
    Predict next state(s) based on current state + alpha/risk deltas.
    Uses rule-based probability calibrated from state profiles.
    """

    # Transition probability templates
    # From state → [(to_state, base_prob), ...]
    TRANSITION_TEMPLATES = {
        'Birth': [
            ('Early', 0.55), ('Failure', 0.30), ('Distribution', 0.15),
        ],
        'Early': [
            ('Expansion', 0.50), ('Failure', 0.25), ('Distribution', 0.15),
            ('Acceleration', 0.10),
        ],
        'Expansion': [
            ('Acceleration', 0.55), ('Climax', 0.20), ('Distribution', 0.15),
            ('Breakdown', 0.05), ('Early', 0.05),
        ],
        'Acceleration': [
            ('Climax', 0.50), ('Acceleration', 0.25), ('Expansion', 0.10),
            ('Distribution', 0.10), ('Breakdown', 0.05),
        ],
        'Climax': [
            ('Distribution', 0.55), ('Climax', 0.20), ('Breakdown', 0.15),
            ('Acceleration', 0.10),
        ],
        'Distribution': [
            ('Breakdown', 0.45), ('Distribution', 0.25), ('Recovery', 0.15),
            ('Climax', 0.10), ('Early', 0.05),
        ],
        'Breakdown': [
            ('Breakdown', 0.35), ('Recovery', 0.30), ('Birth', 0.20),
            ('Distribution', 0.15),
        ],
        'Recovery': [
            ('Early', 0.40), ('Recovery', 0.25), ('Birth', 0.20),
            ('Breakdown', 0.15),
        ],
    }

    def __init__(self):
        self.templates = self.TRANSITION_TEMPLATES

    def predict(self, current_state: str, alpha: Dict,
                risk: Dict, alpha_delta: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Predict next state probabilities, adjusted by current alpha/risk deltas.
        Returns dict with current_state, transitions [(state, prob), ...],
        and primary_next_state.
        """
        transitions = list(self.templates.get(current_state, [('Early', 1.0)]))
        # Adjust probabilities based on alpha momentum & risk
        momentum = alpha.get('Momentum', 50)
        risk_composite = risk.get('Composite', 50)
        structure = alpha.get('Structure', 50)
        flow = alpha.get('Flow', 50)

        adjustments = []
        for state, base_prob in transitions:
            adj = base_prob
            # State-specific adjustments
            if state in ('Expansion', 'Acceleration'):
                if momentum > 65:
                    adj += 0.08
                if flow > 65:
                    adj += 0.05
                if risk_composite > 60:
                    adj -= 0.10
            elif state in ('Distribution', 'Breakdown'):
                if risk_composite > 60:
                    adj += 0.10
                if momentum < 45:
                    adj += 0.08
                if structure < 50:
                    adj += 0.05
            elif state in ('Early', 'Recovery', 'Birth'):
                if structure > 60 and momentum < 55:
                    adj += 0.06
                if risk_composite < 30:
                    adj += 0.04
            elif state == 'Climax':
                if momentum > 80 and flow > 80:
                    adj += 0.10
                if risk_composite > 50:
                    adj += 0.05
            elif state == 'Failure':
                if momentum < 40 or structure < 45:
                    adj += 0.08

            adjustments.append((state, max(0, adj)))

        # Renormalize
        total = sum(a for _, a in adjustments)
        if total > 0:
            adjustments = [(s, a / total) for s, a in adjustments]

        # Sort by prob descending, take top 3
        adjustments.sort(key=lambda x: -x[1])

        return {
            'current_state': current_state,
            'transitions': [(s, round(p, 3)) for s, p in adjustments[:3]],
            'primary_next': adjustments[0][0] if adjustments else current_state,
            'primary_prob': round(adjustments[0][1], 3) if adjustments else 0,
        }


# ============================================================
# Decision Engine
# ============================================================
ACTIONS = [
    'Strong Buy', 'Buy', 'Buy on Pullback', 'Hold',
    'Reduce', 'Take Profit', 'Avoid'
]


class DecisionEngine:
    """
    Generate trading action + confidence from Alpha, Risk, Trend State, and Transition.
    """

    def __init__(self):
        pass

    def decide(self, alpha: Dict, risk: Dict, trend_state: str,
               transition: Dict) -> Dict[str, Any]:
        """
        Map alpha/risk/state/transition → action + confidence.
        """

        # 注入的因子评分（用于 BuyQualityScore）
        factor_scores = alpha.get('_factor_scores', {})
        flow_score = alpha.get('Flow', 50)
        momentum_score = alpha.get('Momentum', 50)
        cre_score = factor_scores.get('CRE', 50)

        composite = alpha.get('Composite', 50)
        risk_score = risk.get('Composite', 50)
        s = alpha.get('Structure', 50)
        f = alpha.get('Flow', 50)
        m = alpha.get('Momentum', 50)

        # Base decision by state
        state_action_map = {
            'Birth': 'Buy on Pullback',
            'Early': 'Buy on Pullback',
            'Expansion': 'Buy',
            'Acceleration': 'Hold',
            'Climax': 'Reduce',
            'Distribution': 'Reduce',
            'Breakdown': 'Avoid',
            'Recovery': 'Buy on Pullback',
        }
        action = state_action_map.get(trend_state, 'Hold')
        confidence = 50.0

        # --- Confidence calculation ---
        # Alpha base
        conf_alpha = max(0, min(100, (composite - 30) * 1.5))
        # Risk penalty
        risk_penalty = risk_score * 0.5
        # Transition confidence
        trans_prob = transition.get('primary_prob', 0.5)
        trans_bonus = trans_prob * 30

        confidence = conf_alpha - risk_penalty + trans_bonus
        confidence = max(10, min(100, confidence))

        # --- Action adjustments based on risk ---
        if risk_score > 70:
            action = 'Avoid'
            confidence = min(confidence, 30)
        elif risk_score > 55:
            if action in ('Buy', 'Strong Buy', 'Buy on Pullback'):
                action = 'Buy on Pullback'
            confidence *= 0.7

        # --- Structure override ---
        if s < 40 and action not in ('Avoid', 'Take Profit'):
            action = 'Reduce'
            confidence *= 0.5

        # --- Momentum override ---
        if m < 35 and action == 'Strong Buy':
            action = 'Buy on Pullback'

        # ============================================================
        # BuyQualityScore 门控（Expansion / Recovery / Birth）
        # 将阶段决定的 Buy 拆分为三层：
        #   Strong Buy : BuyQualityScore >= 70 且 Alpha=A
        #   Buy        : BuyQualityScore >= 60
        #   Buy on Pullback : 其余
        # ============================================================
        bqs = 50.0
        if action in ('Buy', 'Strong Buy', 'Buy on Pullback'):
            bqs = (
                flow_score * 0.35 +
                momentum_score * 0.25 +
                (100 - risk_score) * 0.20 +
                cre_score * 0.20
            )
            bqs = max(0, min(100, bqs))

            alpha_grade = alpha.get('Grade', 'C')
            if bqs >= 70 and alpha_grade in ('A', 'B+'):
                action = 'Strong Buy'
            elif bqs >= 60:
                action = 'Buy'
            else:
                action = 'Buy on Pullback'

        combined = f"{action}, Confidence: {confidence:.0f}%"

        return {
            'action': action,
            'confidence': round(confidence, 1),
            'combined': combined,
            'buy_quality_score': round(bqs, 1),
        }


# ============================================================
# Trend Invalidator
# ============================================================
class TrendInvalidator:
    """
    Auto-detect conditions that would invalidate the current trend thesis.
    """

    def generate(self, alpha: Dict, risk: Dict, trend_state: str,
                 factor_scores: Dict, current_price: float,
                 center_price: float) -> List[str]:
        invalidators = []

        # 1. Price below chip center
        if center_price > 0 and current_price < center_price:
            invalidators.append(f"跌破筹码质心 ({center_price:.2f})")

        # 2. Pressure increasing
        pd_score = factor_scores.get('PressureDecay', 50)
        if pd_score < 40:
            invalidators.append("上方压力回升，阻力区筹码占比增加")

        # 3. CRE deteriorating
        cre_score = factor_scores.get('CRE', 50)
        if cre_score < 35:
            invalidators.append("CRE持续恶化，轮换效率丧失")

        # 4. Risk > 60
        risk_score = risk.get('Composite', 50)
        if risk_score > 60:
            invalidators.append(f"风险分{risk_score:.0f}，超过60警戒线")

        # 5. Momentum < 40
        m = alpha.get('Momentum', 50)
        if m < 40:
            invalidators.append("动量跌破40，动能衰退")

        # 6. Structure breakdown
        s = alpha.get('Structure', 50)
        if s < 35:
            invalidators.append("结构分跌破35，筹码结构受损")

        # 7. State-specific
        if trend_state in ('Climax', 'Distribution') and risk.get('Composite', 50) > 50:
            invalidators.append("处于派发/高潮阶段且风险偏高，趋势随时反转")

        if not invalidators:
            invalidators.append("当前无趋势失效信号")

        return invalidators


# ============================================================
# Summary Generator
# ============================================================
class SummaryGenerator:
    """
    Auto-generate institutional-style natural language summary.
    """

    def generate(self, alpha: Dict, risk: Dict, trend_state: str,
                 transition: Dict, decision: Dict) -> str:
        s = alpha.get('Structure', 50)
        f = alpha.get('Flow', 50)
        m = alpha.get('Momentum', 50)
        composite = alpha.get('Composite', 50)
        risk_level = risk.get('Level', 'Medium')
        risk_score = risk.get('Composite', 50)
        next_state = transition.get('primary_next', 'Unknown')
        next_prob = transition.get('primary_prob', 0.5)
        action = decision.get('action', 'Hold')

        parts = []

        # Structure
        if s >= 80:
            parts.append("筹码结构持续优化，")
        elif s >= 60:
            parts.append("筹码结构处于健康水平，")
        elif s >= 40:
            parts.append("筹码结构有轻度弱化迹象，")
        else:
            parts.append("筹码结构明显受损，")

        # Flow
        if f >= 70:
            parts.append("资金承接能力强劲，机构持续流入。")
        elif f >= 55:
            parts.append("资金承接能力中性偏强，仍有资金关注。")
        elif f >= 40:
            parts.append("资金承接趋弱，需警惕资金流出。")
        else:
            parts.append("资金面疲软，缺乏增量资金。")

        # Momentum
        if m >= 70:
            parts.append("趋势动能充沛，处于加速上行阶段。")
        elif m >= 55:
            parts.append("趋势动能向好，但未形成加速共振。")
        elif m >= 40:
            parts.append("趋势动能偏弱，短线需要进一步确认。")
        else:
            parts.append("趋势动能衰竭，方向可能反转。")

        # Risk
        if risk_score <= 20:
            parts.append("整体风险极低，安全边际充足。")
        elif risk_score <= 40:
            parts.append(f"当前风险等级{risk_level}，整体可控。")
        elif risk_score <= 60:
            parts.append(f"风险等级为{risk_level}，需密切关注。")
        else:
            parts.append(f"风险等级{risk_level}，建议谨慎或回避。")

        # Transition
        parts.append(f"当前处于{trend_state}阶段，")
        if next_prob >= 0.5:
            parts.append(f"下一阶段最可能进入{next_state}（概率{next_prob*100:.0f}%）。")
        else:
            parts.append(f"下一阶段有{next_prob*100:.0f}%概率进入{next_state}，但不确定性较高。")

        # Action
        if action in ('Strong Buy', 'Buy'):
            parts.append(f"操作建议：{action}。")
        elif action == 'Buy on Pullback':
            parts.append("操作建议：等待缩量回踩筹码质心附近低吸，不宜追高。")
        elif action == 'Hold':
            parts.append("操作建议：持有为主，不新增仓位。")
        elif action == 'Reduce':
            parts.append("操作建议：逐步减仓，控制风险敞口。")
        else:
            parts.append(f"操作建议：{action}。")

        return ''.join(parts)


# ============================================================
# Backtest Engine
# ============================================================
class BacktestEngine:
    """
    Institutional-grade factor backtesting API.

    Standard workflow:
        bt = BacktestEngine()
        # 1. Prepare panel data: columns=[factor1, factor2, ..., fwd_5d, fwd_10d, fwd_20d]
        bt.load_panel(panel_df, factor_cols=['Structure','Flow','Momentum','Composite'],
                      return_cols=['fwd_5d','fwd_10d','fwd_20d'])
        # 2. Run full analysis
        results = bt.full_analysis()
    """

    def __init__(self):
        self.panel: Optional[pd.DataFrame] = None
        self.factor_cols: List[str] = []
        self.return_cols: List[str] = []
        self.results: Dict = {}

    def load_panel(self, panel_df: pd.DataFrame,
                   factor_cols: Optional[List[str]] = None,
                   return_cols: Optional[List[str]] = None):
        """Load cross-sectional panel dataset."""
        self.panel = panel_df.copy()
        if factor_cols:
            self.factor_cols = [c for c in factor_cols if c in self.panel.columns]
        if return_cols:
            self.return_cols = [c for c in return_cols if c in self.panel.columns]
        if not self.factor_cols:
            self.factor_cols = [c for c in self.panel.columns
                                if c.endswith('_Score') or c in (
                'Structure', 'Flow', 'Momentum', 'Composite',
                'PressureDecay', 'CRE', 'ChipMomentum', 'CenterVelocity',
                'WinningExpansion', 'Absorption', 'Resilience', 'Concentration',
                'VolumeQuality',
            )]
        if not self.return_cols:
            self.return_cols = [c for c in self.panel.columns
                                if 'fwd_' in c or 'return' in c.lower()]

    # -------------------------------------------------------
    # 1. Factor stratification (Quintile / Decile)
    # -------------------------------------------------------
    def _stratify(self, series: pd.Series, q: int = 10) -> pd.Series:
        """Stratify into q groups. Handles duplicate edges."""
        try:
            return pd.qcut(series, q=q, labels=False, duplicates='drop')
        except ValueError:
            # Fallback: rank-based bins
            ranks = series.rank(method='first')
            return (ranks // (len(ranks) // q)).clip(0, q - 1).astype(int)

    def decile_analysis(self, factor_col: Optional[str] = None,
                        return_col: Optional[str] = None,
                        q: int = 10) -> Dict:
        """
        Decile/Quintile portfolio analysis.

        Returns:
            {
                'group_returns': DataFrame with mean, std, sharpe, win_rate per group,
                'long_short_spread': return of top - bottom,
                'long_short_sharpe': Sharpe-like ratio,
                'monotonicity': rank correlation between group order and return,
                'top_win_rate': win rate of top group,
            }
        """
        fc = factor_col or (self.factor_cols[0] if self.factor_cols else None)
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        if fc is None or rc is None:
            raise ValueError("factor_col and return_col required")
        df = self.panel[[fc, rc]].dropna().copy()
        if len(df) < q * 2:
            return {'error': 'insufficient data'}

        df['group'] = self._stratify(df[fc], q)
        grp = df.groupby('group')[rc]

        group_returns = grp.agg(['mean', 'std', 'count'])
        # Win rate per group
        win_rate = grp.apply(lambda x: (x > 0).mean())
        group_returns['win_rate'] = win_rate
        # Sharpe-like (annualized approximation using daily factor)
        group_returns['sharpe'] = np.where(
            group_returns['std'] > 1e-8,
            group_returns['mean'] / group_returns['std'] * np.sqrt(252), 0
        )

        top_grp = group_returns.index.max()
        bot_grp = group_returns.index.min()
        ls_spread = group_returns.loc[top_grp, 'mean'] - group_returns.loc[bot_grp, 'mean']
        ls_std = np.sqrt(
            group_returns.loc[top_grp, 'std'] ** 2 +
            group_returns.loc[bot_grp, 'std'] ** 2
        ) if top_grp != bot_grp else 0
        ls_sharpe = ls_spread / ls_std * np.sqrt(252) if ls_std > 1e-8 else 0

        # Monotonicity: Spearman correlation between group label and mean return
        from scipy.stats import spearmanr
        mono_rho, _ = spearmanr(group_returns.index, group_returns['mean'])

        return {
            'factor': fc,
            'return_col': rc,
            'q': q,
            'group_returns': group_returns,
            'long_short_spread': round(ls_spread, 6),
            'long_short_sharpe': round(ls_sharpe, 4),
            'monotonicity': round(mono_rho, 4),
            'top_win_rate': round(group_returns.loc[top_grp, 'win_rate'], 4),
        }

    # -------------------------------------------------------
    # 2. IC / Rank IC (per return horizon)
    # -------------------------------------------------------
    def ic_analysis(self, return_col: Optional[str] = None,
                    factor_cols: Optional[List[str]] = None) -> Dict:
        """
        Cross-sectional IC / Rank IC for each factor vs a given return horizon.

        Returns: {factor_name: {'PearsonIC': ..., 'RankIC': ..., 'p_value': ...}}
        """
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        if rc is None:
            raise ValueError("return_col required")
        fcs = factor_cols or self.factor_cols
        results = {}
        from scipy.stats import pearsonr, spearmanr

        df = self.panel[fcs + [rc]].dropna()
        if len(df) < 10:
            return {'error': 'insufficient data (need >= 10 observations)'}

        for col in fcs:
            mask = df[col].notna() & df[rc].notna()
            if mask.sum() < 10:
                results[col] = {'PearsonIC': 0, 'RankIC': 0, 'p_value': 1.0, 'n': mask.sum()}
                continue
            try:
                p_ic, p_val = pearsonr(df.loc[mask, col], df.loc[mask, rc])
                r_ic, _ = spearmanr(df.loc[mask, col], df.loc[mask, rc])
                results[col] = {
                    'PearsonIC': round(p_ic, 4),
                    'RankIC': round(r_ic, 4),
                    'p_value': round(p_val, 4),
                    'n': mask.sum(),
                }
            except Exception:
                results[col] = {'PearsonIC': 0, 'RankIC': 0, 'p_value': 1.0, 'n': 0}
        return results

    def ic_time_series(self, date_col: str = 'date',
                       factor_cols: Optional[List[str]] = None,
                       return_col: Optional[str] = None) -> pd.DataFrame:
        """
        Compute IC per time period (time-series of IC).
        panel must have date_col to group by.
        Returns: DataFrame(index=date, columns=factor_cols, values=PearsonIC)
        """
        if date_col not in self.panel.columns:
            return pd.DataFrame()
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        fcs = factor_cols or self.factor_cols
        dates = self.panel[date_col].unique()
        records = []
        from scipy.stats import pearsonr

        for dt in sorted(dates):
            sub = self.panel[self.panel[date_col] == dt][fcs + [rc]].dropna()
            if len(sub) < 10:
                continue
            row = {'date': dt}
            for col in fcs:
                try:
                    ic, _ = pearsonr(sub[col], sub[rc])
                    row[col] = round(ic, 4)
                except Exception:
                    row[col] = 0
            records.append(row)

        return pd.DataFrame(records).set_index('date') if records else pd.DataFrame()

    # -------------------------------------------------------
    # 3. Long-Short portfolio returns
    # -------------------------------------------------------
    def long_short_analysis(self, factor_col: Optional[str] = None,
                            return_col: Optional[str] = None,
                            top_pct: float = 0.2,
                            bottom_pct: float = 0.2) -> Dict:
        """
        Long top_pct percentile, short bottom_pct percentile.
        Returns long/short/spread returns, max_dd, win_rate.
        """
        fc = factor_col or (self.factor_cols[0] if self.factor_cols else None)
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        df = self.panel[[fc, rc]].dropna().copy()
        if len(df) < 20:
            return {'error': 'insufficient data'}

        threshold_top = df[fc].quantile(1 - top_pct)
        threshold_bot = df[fc].quantile(bottom_pct)

        long = df.loc[df[fc] >= threshold_top, rc]
        short = df.loc[df[fc] <= threshold_bot, rc]

        if len(long) < 1 or len(short) < 1:
            return {'error': 'not enough stocks in long/short tails'}

        l_mean = long.mean()
        s_mean = short.mean()
        spread = l_mean - s_mean

        # Combined long-short returns (for max_dd estimation)
        all_returns = np.concatenate([long.values, -short.values])
        cumulative = np.cumsum(all_returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = cumulative - running_max
        max_dd = abs(drawdown.min())

        return {
            'factor': fc,
            'return_col': rc,
            'long_n': len(long),
            'short_n': len(short),
            'long_return': round(l_mean, 6),
            'short_return': round(s_mean, 6),
            'long_short_spread': round(spread, 6),
            'max_drawdown': round(max_dd, 6),
            'win_rate_long': round((long > 0).mean(), 4),
            'win_rate_short': round((short > 0).mean(), 4),
        }

    # -------------------------------------------------------
    # 4. Alpha / Risk matrix return analysis
    # -------------------------------------------------------
    def alpha_risk_matrix(self, alpha_col: str = 'Composite',
                          risk_col: str = 'risk_score',
                          return_col: Optional[str] = None,
                          alpha_bins: int = 3,
                          risk_bins: int = 3) -> Dict:
        """
        2D matrix: Alpha quintile x Risk quintile → mean forward return.
        High Alpha + Low Risk should have highest returns.
        """
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        if alpha_col not in self.panel or risk_col not in self.panel:
            return {'error': f'columns {alpha_col}/{risk_col} not found'}

        df = self.panel[[alpha_col, risk_col, rc]].dropna().copy()
        if len(df) < 20:
            return {'error': 'insufficient data'}

        df['alpha_group'] = self._stratify(df[alpha_col], alpha_bins)
        df['risk_group'] = self._stratify(df[risk_col], risk_bins)

        matrix = df.pivot_table(
            values=rc, index='alpha_group', columns='risk_group',
            aggfunc='mean'
        )
        counts = df.pivot_table(
            values=rc, index='alpha_group', columns='risk_group',
            aggfunc='count'
        )

        best_cell = matrix.max().max() if not matrix.empty else 0
        worst_cell = matrix.min().min() if not matrix.empty else 0

        return {
            'alpha_col': alpha_col,
            'risk_col': risk_col,
            'return_col': rc,
            'return_matrix': matrix,
            'count_matrix': counts,
            'best_cell_return': round(best_cell, 6),
            'worst_cell_return': round(worst_cell, 6),
            'high_alpha_low_risk_return': round(
                matrix.loc[matrix.index.max(), matrix.columns.min()], 6
            ) if matrix.index.max() in matrix.index and matrix.columns.min() in matrix.columns else None,
            'low_alpha_high_risk_return': round(
                matrix.loc[matrix.index.min(), matrix.columns.max()], 6
            ) if matrix.index.min() in matrix.index and matrix.columns.max() in matrix.columns else None,
        }

    # -------------------------------------------------------
    # 5. Market regime stability
    # -------------------------------------------------------
    def regime_stability(self, regime_col: str = 'market_regime',
                         factor_cols: Optional[List[str]] = None,
                         return_col: Optional[str] = None) -> Dict:
        """
        Evaluate factor IC stability across different market regimes.
        regime_col: column in panel identifying bull/sideways/bear regimes.
        """
        if regime_col not in self.panel.columns:
            return {'error': f'{regime_col} not in panel'}
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        fcs = factor_cols or self.factor_cols
        regimes = self.panel[regime_col].unique()
        results = {}

        for regime in sorted(regimes):
            sub = self.panel[self.panel[regime_col] == regime]
            ics = self.ic_analysis(return_col=rc, factor_cols=fcs)
            results[str(regime)] = {
                'count': len(sub),
                'ic_analysis': ics,
            }

        # Cross-regime IC stability (std of IC across regimes)
        stability = {}
        for fc in fcs:
            ic_vals = []
            for r in results:
                ic_val = results[r]['ic_analysis'].get(fc, {}).get('PearsonIC', 0)
                if isinstance(ic_val, (int, float)):
                    ic_vals.append(ic_val)
            stability[fc] = {
                'mean_ic': round(np.mean(ic_vals), 4) if ic_vals else 0,
                'std_ic': round(np.std(ic_vals), 4) if ic_vals else 0,
                'ic_stability_ratio': round(
                    np.mean(ic_vals) / (np.std(ic_vals) + 1e-8), 2
                ) if ic_vals else 0,
            }

        return {
            'regime_col': regime_col,
            'by_regime': results,
            'stability': stability,
        }

    # -------------------------------------------------------
    # 6. Factor contribution & weight sensitivity
    # -------------------------------------------------------
    def factor_importance(self, return_col: Optional[str] = None,
                          factor_cols: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Factor importance ranking via univariate IC.
        Returns DataFrame sorted by |PearsonIC| descending.
        """
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        fcs = factor_cols or self.factor_cols
        from scipy.stats import pearsonr

        rows = []
        for col in fcs:
            mask = self.panel[col].notna() & self.panel[rc].notna()
            if mask.sum() < 10:
                continue
            try:
                ic, pv = pearsonr(self.panel.loc[mask, col], self.panel.loc[mask, rc])
                rows.append({
                    'factor': col,
                    'PearsonIC': round(ic, 4),
                    'p_value': round(pv, 4),
                    'n': mask.sum(),
                    'abs_IC': round(abs(ic), 4),
                })
            except Exception:
                continue

        df = pd.DataFrame(rows).sort_values('abs_IC', ascending=False)
        return df

    def weight_sensitivity(self, alpha_col: str = 'Composite',
                           return_col: Optional[str] = None,
                           weight_range: Tuple[float, float, int] = (0.2, 0.8, 7)) -> Dict:
        """
        Sensitivity analysis: vary weight of alpha_col and measure IC.
        Simulates re-weighting a composite score.
        """
        rc = return_col or (self.return_cols[0] if self.return_cols else None)
        start, end, steps = weight_range

        # Use all factor cols as the "other" components
        other_cols = [c for c in self.factor_cols if c != alpha_col]
        if not other_cols or alpha_col not in self.panel.columns:
            return {'error': f'{alpha_col} or other factors not available'}

        weights = np.linspace(start, end, steps)
        ics = []
        from scipy.stats import pearsonr

        for w in weights:
            # Composite = w * alpha_col + (1-w) * mean(other_cols)
            other_mean = self.panel[other_cols].mean(axis=1)
            composite = w * self.panel[alpha_col] + (1 - w) * other_mean
            mask = composite.notna() & self.panel[rc].notna()
            if mask.sum() < 10:
                continue
            ic, _ = pearsonr(composite[mask], self.panel.loc[mask, rc])
            ics.append({'weight': round(w, 2), 'IC': round(ic, 4)})

        df = pd.DataFrame(ics)
        max_row = df.loc[df['IC'].idxmax()] if not df.empty else {}
        return {
            'factor': alpha_col,
            'return_col': rc,
            'sensitivity': df,
            'optimal_weight': max_row.get('weight', None),
            'optimal_ic': max_row.get('IC', None),
        }

    # -------------------------------------------------------
    # 7. Full analysis suite
    # -------------------------------------------------------
    def full_analysis(self, factor_cols: Optional[List[str]] = None,
                      return_cols: Optional[List[str]] = None) -> Dict:
        """
        Run all analyses and return consolidated results.
        """
        fcs = factor_cols or self.factor_cols
        rcs = return_cols or self.return_cols
        results = {}

        # IC analysis for each return horizon
        results['ic'] = {}
        for rc in rcs:
            results['ic'][rc] = self.ic_analysis(return_col=rc, factor_cols=fcs)

        # Factor importance
        for rc in rcs:
            results[f'factor_importance_{rc}'] = self.factor_importance(
                return_col=rc, factor_cols=fcs
            )

        # Decile analysis for each factor vs each return
        results['decile'] = {}
        for fc in fcs:
            for rc in rcs:
                key = f'{fc}_vs_{rc}'
                results['decile'][key] = self.decile_analysis(
                    factor_col=fc, return_col=rc, q=10
                )

        # Long-short for composite
        if 'Composite' in self.panel.columns:
            results['long_short'] = {}
            for rc in rcs:
                results['long_short'][rc] = self.long_short_analysis(
                    factor_col='Composite', return_col=rc
                )

        self.results = results
        return results

    # -------------------------------------------------------
    # 8. Report
    # -------------------------------------------------------
    def format_report(self) -> str:
        """Generate a concise backtest report."""
        lines = []
        lines.append("")
        lines.append("=" * 55)
        lines.append("  Backtest Engine Report")
        lines.append("=" * 55)

        if not self.results:
            lines.append("  No results. Run full_analysis() first.")
            return '\n'.join(lines)

        # IC summary
        ic_data = self.results.get('ic', {})
        if ic_data:
            lines.append("\n  ── IC / Rank IC ──")
            for rc, factors in ic_data.items():
                lines.append(f"\n  Forward: {rc}")
                if isinstance(factors, dict) and 'error' not in factors:
                    for fname, vals in factors.items():
                        if isinstance(vals, dict) and 'PearsonIC' in vals:
                            lines.append(f"    {fname:<20s}  IC={vals['PearsonIC']:.4f}  "
                                         f"RankIC={vals['RankIC']:.4f}  p={vals['p_value']:.4f}")
                else:
                    lines.append(f"    {factors}")

        # Factor importance
        for key in self.results:
            if key.startswith('factor_importance_'):
                rc = key.replace('factor_importance_', '')
                fi_df = self.results[key]
                if isinstance(fi_df, pd.DataFrame) and not fi_df.empty:
                    lines.append(f"\n  ── Factor Importance ({rc}) ──")
                    for _, row in fi_df.iterrows():
                        lines.append(f"    {row['factor']:<20s}  IC={row['PearsonIC']:.4f}  "
                                     f"p={row['p_value']:.4f}  n={row['n']}")

        # Decile summary
        decile_data = self.results.get('decile', {})
        if decile_data:
            lines.append("\n  ── Decile Analysis (Top decile key metrics) ──")
            for key, val in decile_data.items():
                if isinstance(val, dict) and 'long_short_spread' in val:
                    lines.append(f"    {key:<30s}  LS={val['long_short_spread']:.4f}  "
                                 f"Sharpe={val['long_short_sharpe']:.2f}  "
                                 f"Mono={val['monotonicity']:.2f}  "
                                 f"TopWR={val['top_win_rate']:.0%}")

        # Long-short summary
        ls_data = self.results.get('long_short', {})
        if ls_data:
            lines.append("\n  ── Long-Short Portfolio ──")
            for rc, val in ls_data.items():
                if isinstance(val, dict) and 'long_short_spread' in val:
                    lines.append(f"    Composite vs {rc:<8s}  "
                                 f"Spread={val['long_short_spread']:.4f}  "
                                 f"LongWR={val['win_rate_long']:.0%}  "
                                 f"ShortWR={val['win_rate_short']:.0%}  "
                                 f"MaxDD={val['max_drawdown']:.4f}")

        lines.append("\n" + "=" * 55)
        return '\n'.join(lines)


# ============================================================
# Chip Alpha V5 Engine — Main Orchestrator
# ============================================================
class ChipAlphaV5Engine:
    """
    Institutional Trend Intelligence Engine.
    Orchestrates: Feature Processing → Alpha → Risk → State → Transition → Decision
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get('TUSHARE_TOKEN', '')
        self.fp = FeatureProcessor()
        self.alpha_engine = AlphaEngineV5()
        self.risk_engine = RiskEngine()
        self.state_machine = TrendStateMachine()
        self.transition_engine = TransitionEngine()
        self.decision_engine = DecisionEngine()
        self.invalidator = TrendInvalidator()
        self.summarizer = SummaryGenerator()
        self.bt = BacktestEngine()

        # V2 engine for data fetching
        self._v2 = None

    def _get_v2_engine(self):
        if self._v2 is not None:
            return self._v2
        from chip_alpha_engine_v2 import ChipAlphaEngineV2
        self._v2 = ChipAlphaEngineV2(token=self.token)
        return self._v2

    @staticmethod
    def _get_turnover_rates(v2_result: Dict) -> List[float]:
        """Extract turnover rate history from V2 result's CRE details if available."""
        cre = v2_result.get('Factors', {}).get('CRE', {})
        details = cre.get('details', {})
        if 'turnovers' in details:
            return details['turnovers']
        if 'accum_turnover_trend' in details:
            return details['accum_turnover_trend']
        return []

    def analyze(self, ts_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        """
        Full V5 analysis pipeline for a single stock.
        1. Fetch data + compute V2 factors
        2. Feature processing
        3. Alpha Engine V5
        4. Risk Engine
        5. Trend State Machine
        6. Transition Engine
        7. Decision Engine + Invalidator + Summary
        """
        v2 = self._get_v2_engine()
        v2_result = v2.analyze(ts_code, lookback_days=lookback_days)
        return self._build_v5(v2_result, v2)

    def analyze_from_v2(self, v2_result: Dict) -> Dict[str, Any]:
        """Build V5 profile from an existing V2 result."""
        return self._build_v5(v2_result, None)

    def _build_v5(self, v2_result: Dict, v2_engine) -> Dict[str, Any]:
        """Core pipeline: V2 result → V5 profile."""
        f = v2_result.get('Factors', {})
        ts_code = v2_result.get('ts_code', '')
        price = v2_result.get('current_price', 0)
        center = v2_result.get('chip_center', 0)

        # --- Extract factor scores ---
        ab_dict = f.get('Absorption', {})
        cv_dict = f.get('CenterVelocity', {})
        vol_ratio = ab_dict.get('vol_ratio', 1.0)
        clv_val = ab_dict.get('avg_clv', 0)
        accel_val = cv_dict.get('acceleration', 0)

        # VolumeQuality: log-scaled, capped at vol_ratio 2.0
        vq_raw = math.log(max(0.3, vol_ratio)) / math.log(2.0) * 50 + 50
        volume_quality = max(0, min(100, vq_raw))

        # Acceleration score (z-score based)
        accel_score = max(0, min(100, accel_val * 200 + 50))

        fs = {
            'PressureDecay': f.get('PressureDecay', {}).get('score', 50),
            'Resilience': f.get('Resilience', {}).get('score', 50),
            'Concentration': f.get('Concentration', {}).get('score', 50),
            'PeakMigration': f.get('PeakMigration', {}).get('score', 50),
            'CRE': f.get('CRE', {}).get('score', 50),
            'Absorption': ab_dict.get('score', 50),
            'CLV': clv_val * 100,  # 0~1 → 0~100
            'VolumeQuality': volume_quality,
            'VolumeQuality_ratio': vol_ratio,
            'CenterVelocity': cv_dict.get('score', 50),
            'ChipMomentum': f.get('ChipMomentum', {}).get('score', 50),
            'WinningExpansion': f.get('WinningExpansion', {}).get('score', 50),
            'Acceleration': accel_score,
            'turnover_rates': self._get_turnover_rates(v2_result),
        }

        # --- Alpha ---
        alpha = self.alpha_engine.compute(fs)

        # --- Risk ---
        risk = self.risk_engine.compute(fs)

        # --- Trend State ---
        current_state = self.state_machine.classify(alpha)

        # --- Transition ---
        transition = self.transition_engine.predict(current_state, alpha, risk)

        # --- Decision ---
        decision = self.decision_engine.decide(alpha, risk, current_state, transition)

        # --- Invalidator ---
        invalidators = self.invalidator.generate(alpha, risk, current_state, fs, price, center)

        # --- Summary ---
        summary = self.summarizer.generate(alpha, risk, current_state, transition, decision)

        return {
            'ts_code': ts_code,
            'end_date': v2_result.get('end_date', ''),
            'lookback_days': v2_result.get('lookback_days', 20),
            'current_price': price,
            'chip_center': center,
            'raw_factors': fs,
            'v2_grade': v2_result.get('Grade', 'C'),
            'v2_score': v2_result.get('ChipTrendScore', 50),
            'alpha': alpha,
            'risk': risk,
            'trend': {
                'current_state': current_state,
                'description': TrendStateMachine.state_description(current_state),
                'strategy': TrendStateMachine.state_strategy(current_state),
                'transition': transition,
            },
            'decision': {
                'action': decision['action'],
                'confidence': decision['confidence'],
                'combined': decision['combined'],
                'invalidators': invalidators,
                'buy_quality_score': decision.get('buy_quality_score', 50),
            },
            'summary': summary,
        }

    # ================================================================
    # Output formatting
    # ================================================================
    def format_report(self, result: Dict) -> str:
        """Generate terminal report (V5 format)."""
        lines = []
        lines.append("")

        def bar(score: float, width: int = 12) -> str:
            filled = max(1, int(score / 100 * width))
            return "█" * filled + "░" * (width - filled)

        def grade_score(score: float) -> str:
            if score >= 85: return "AA"
            if score >= 75: return "A"
            if score >= 65: return "B+"
            if score >= 55: return "B"
            if score >= 45: return "B-"
            if score >= 35: return "C"
            return "D"

        # Header
        lines.append("═" * 55)
        lines.append(f"  Chip Alpha Profile — {result['ts_code']}")
        lines.append(f"  {result['end_date']}  |  Lookback: {result['lookback_days']}d"
                     f"  |  Price: {result['current_price']:.2f}")
        lines.append("═" * 55)

        # Alpha dimensions
        a = result['alpha']
        lines.append(f"\n  Structure    {a['Structure']:5.1f}  {bar(a['Structure'])}  "
                     f"{AlphaEngineV5.structure_conclusion(a['Structure'])}")
        lines.append(f"  Flow         {a['Flow']:5.1f}  {bar(a['Flow'])}  "
                     f"{AlphaEngineV5.flow_conclusion(a['Flow'])}")
        lines.append(f"  Momentum     {a['Momentum']:5.1f}  {bar(a['Momentum'])}  "
                     f"{AlphaEngineV5.momentum_conclusion(a['Momentum'])}")
        lines.append("─" * 55)
        lines.append(f"  Composite    {a['Composite']:5.1f}  Grade {a['Grade']}  "
                     f"{bar(a['Composite'])}")
        lines.append("─" * 55)

        # Risk
        r = result['risk']
        r_bar = bar(r['Composite'])
        lines.append(f"  Risk         {r['Composite']:5.1f}  {r_bar}  {r['Level']}")
        # Show risk dimensions
        rd = r.get('dimensions', {})
        risk_dims = [
            ('Exhaust', rd.get('MomentumExhaustion', 0)),
            ('Crowding', rd.get('ProfitCrowding', 0)),
            ('Distrib', rd.get('Distribution', 0)),
            ('StructBrk', rd.get('StructureBreakdown', 0)),
            ('Vol', rd.get('VolatilityExpansion', 0)),
            ('Liq', rd.get('LiquidityRisk', 0)),
        ]
        dim_str = '  '.join(f'{name}={val:.0f}' for name, val in risk_dims)
        lines.append(f"  Risk Dims:   {dim_str}")
        lines.append("─" * 55)

        # Trend State
        t = result['trend']
        lines.append(f"  Trend State  {t['current_state']}")
        lines.append(f"  Strategy     {t['strategy']}")

        # Transition
        tr = t.get('transition', {})
        transitions = tr.get('transitions', [])
        if transitions:
            trans_parts = '  '.join(f"{st} {p*100:.0f}%" for st, p in transitions)
            lines.append(f"  Transition   {trans_parts}")

        # Decision
        d = result['decision']
        lines.append("─" * 55)
        lines.append(f"  Action       {d['action']}")
        lines.append(f"  Confidence   {d['confidence']:.0f}%")
        lines.append("")

        # Invalidators
        inv = d.get('invalidators', [])
        lines.append("─" * 55)
        lines.append("  Trend Invalidator")
        for iv in inv:
            lines.append(f"    • {iv}")

        # Summary
        lines.append("─" * 55)
        lines.append("  Institution Summary")
        lines.append(f"    {result['summary']}")
        lines.append("═" * 55)

        return '\n'.join(lines)

    def to_json(self, result: Dict) -> str:
        """Serialize to JSON."""
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)


# ============================================================
# Decision Report — 买入决策建议书
# ============================================================
def generate_buy_decision_report(v5_result: Dict) -> str:
    """
    从 V5 分析结果生成完整的买入决策分析报告（终端输出）
    包含：Alpha三维度解读、风险评估、趋势生命周期、关键价位、失效条件、决策结论
    """
    a = v5_result.get('alpha', {})
    r = v5_result.get('risk', {})
    t = v5_result.get('trend', {})
    d = v5_result.get('decision', {})
    tr = t.get('transition', {})
    rd = r.get('dimensions', {})
    price = v5_result.get('current_price', 0)
    center = v5_result.get('chip_center', 0)
    ts_code = v5_result.get('ts_code', '')

    lines = []
    lines.append("")
    lines.append("═" * 60)
    lines.append(f"  买入决策建议书 — {ts_code}")
    lines.append(f"  Chip Alpha Trend Intelligence  |  {v5_result.get('end_date', '')}")
    lines.append("═" * 60)

    # 1. Alpha 三维度解读
    s_val, f_val, m_val = a.get('Structure', 50), a.get('Flow', 50), a.get('Momentum', 50)
    c_val = a.get('Composite', 50)
    lines.append("\n【Alpha 三维度诊断】")
    # Structure
    if s_val >= 85:
        lines.append(f"  结构分 {s_val:.0f}/100 — 筹码结构优秀，压力区筹码持续衰减，")
        lines.append(f"  质心稳健，属于机构重仓健康形态，长期底仓逻辑扎实。")
    elif s_val >= 70:
        lines.append(f"  结构分 {s_val:.0f}/100 — 筹码结构健康，压力区可控，")
        lines.append(f"  质心稳定，短期无结构性风险。")
    elif s_val >= 55:
        lines.append(f"  结构分 {s_val:.0f}/100 — 筹码结构一般，存在轻度上方压力，")
        lines.append(f"  需关注压力衰减速率和质心变化方向。")
    else:
        lines.append(f"  结构分 {s_val:.0f}/100 — 筹码结构偏弱，上方压力明显，")
        lines.append(f"  质心可能下移，需要更多时间换手消化。")
    # Flow
    if f_val >= 70:
        lines.append(f"  资金分 {f_val:.0f}/100 — 资金承接强劲，CRE处于高效换手水平，")
        lines.append(f"  吸筹质量好，机构资金持续流入迹象明显。")
    elif f_val >= 55:
        lines.append(f"  资金分 {f_val:.0f}/100 — 资金承接中性偏强，CRE处于温和换手水平，")
        lines.append(f"  吸筹质量良好，未见机构系统性流出迹象。")
    elif f_val >= 40:
        lines.append(f"  资金分 {f_val:.0f}/100 — 资金承接趋弱，成交缩量或放量滞涨，")
        lines.append(f"  需警惕资金面进一步恶化。")
    else:
        lines.append(f"  资金分 {f_val:.0f}/100 — 资金面疲软，缺乏增量资金，")
        lines.append(f"  短期反弹难度较大。")
    # Momentum
    if m_val >= 70:
        lines.append(f"  动量分 {m_val:.0f}/100 — 趋势动能充沛，处于加速上行通道中，")
        lines.append(f"  顺应趋势持仓为主，不宜逆势做空。")
    elif m_val >= 55:
        lines.append(f"  动量分 {m_val:.0f}/100 — 趋势动能正面但未形成加速共振，")
        lines.append(f"  处于温和上行通道中，短线爆发力尚需量能配合确认。")
    elif m_val >= 40:
        lines.append(f"  动量分 {m_val:.0f}/100 — 趋势动能偏弱，方向可能不明或震荡，")
        lines.append(f"  短线需等待进一步信号确认方向。")
    else:
        lines.append(f"  动量分 {m_val:.0f}/100 — 趋势动能衰竭，方向可能反转，")
        lines.append(f"  不宜追涨，等待企稳信号。")

    # 2. 复合评分
    grade = a.get('Grade', 'C')
    if grade in ('AA', 'A'):
        verb = '属于全市场前25%的高质量筹码形态标的'
    elif grade in ('B+', 'B'):
        verb = '处于市场中上水平，具备一定跟踪价值'
    elif grade in ('B-',):
        verb = '处于市场中等水平，需结合其他维度判断'
    else:
        verb = '处于市场偏弱水平，谨慎对待'
    lines.append(f"\n【复合Alpha】{c_val:.0f}/100 ({grade}级) — {verb}。")

    # 3. 风险
    risk_score = r.get('Composite', 50)
    risk_level = r.get('Level', 'Medium')
    risk_dims_desc = {
        'MomentumExhaustion': '动量衰竭',
        'ProfitCrowding': '获利拥挤',
        'Distribution': '派发信号',
        'StructureBreakdown': '结构破裂',
        'VolatilityExpansion': '波动放大',
        'LiquidityRisk': '流动性风险',
    }
    high_risk_dims = []
    for dim_key, dim_name in risk_dims_desc.items():
        dim_val = rd.get(dim_key, 0)
        if dim_val >= 50:
            high_risk_dims.append(f"{dim_name}({dim_val:.0f})")
    lines.append(f"\n【风险评估】{risk_score:.0f}/100 ({risk_level})")
    if high_risk_dims:
        lines.append(f"  主要风险来源：{'、'.join(high_risk_dims)}")
    else:
        lines.append(f"  六维度风险均处于低位，未发现系统性风险隐患。")
    if risk_score <= 20:
        lines.append(f"  整体风险极低，安全边际充足，仓位可适当积极。")
    elif risk_score <= 40:
        lines.append(f"  整体风险可控，不存在重大回撤隐患。")
    elif risk_score <= 60:
        lines.append(f"  风险中等，需密切关注高风险维度变化。")
    else:
        lines.append(f"  风险偏高，建议降低仓位或对冲保护。")

    # 4. 生命周期
    current_state = t.get('current_state', 'Unknown')
    next_state = tr.get('primary_next', '')
    next_prob = tr.get('primary_prob', 0.5)
    strategy = t.get('strategy', '')

    state_desc_map = {
        'Birth': '趋势初生，结构正在形成，资金刚刚开始积累',
        'Early': '趋势早期，结构在巩固中，资金稳定流入',
        'Expansion': '趋势扩张期，结构强健，资金加速，动量蓄力',
        'Acceleration': '趋势加速期，各维度共振，趋势最为强劲',
        'Climax': '趋势高潮期，结构见顶，动量极端，拥挤风险上升',
        'Distribution': '派发阶段，结构走弱，资金流出，动量衰减',
        'Breakdown': '趋势破裂，结构受损，资金逃离，动量转负',
        'Recovery': '趋势修复中，结构企稳，资金谨慎回流',
    }
    state_desc = state_desc_map.get(current_state, '')
    lines.append(f"\n【趋势生命周期】{current_state}")
    if state_desc:
        lines.append(f"  {state_desc}。")
    if next_state and next_prob:
        if next_prob >= 0.5:
            lines.append(f"  下一阶段大概率进入 {next_state}（概率{next_prob*100:.0f}%），趋势方向明确。")
        else:
            lines.append(f"  下一阶段有{next_prob*100:.0f}%概率进入{next_state}，但不确定性较高。")
    if strategy:
        lines.append(f"  策略指导：{strategy}")

    # 5. 关键价位
    if price and center:
        dist = (price - center) / center * 100
        lines.append(f"\n【关键价位】")
        lines.append(f"  现价 {price:.2f} | 筹码质心 {center:.2f} | 偏离 {dist:+.1f}%")
        if dist < -5:
            lines.append(f"  现价大幅低于质心，属于折价区间，是理想的中线低吸区域。")
            lines.append(f"  当前即为低吸窗口，若后续缩量企稳可逐步建仓。")
        elif dist < 0:
            lines.append(f"  现价略低于质心（{dist:+.1f}%），属于小幅折价区间，安全边际尚可。")
            lines.append(f"  当前即为低吸窗口，无需等待回踩。")
        elif dist < 5:
            lines.append(f"  现价略高于质心，成本支撑有效。")
            lines.append(f"  若回踩质心不破，是较好的加仓点位。")
        elif dist < 15:
            lines.append(f"  现价高于质心一定幅度，短期追高需谨慎。")
            lines.append(f"  建议等待回踩MA20或质心附近再介入。")
        else:
            lines.append(f"  现价大幅高于质心，短期已透支，追高风险较大。")
            lines.append(f"  等待充分回调再考虑。")

    # 6. 失效条件
    invalidators = d.get('invalidators', [])
    lines.append(f"\n【趋势失效警戒】")
    if invalidators and '无趋势失效信号' not in invalidators:
        for inv in invalidators:
            lines.append(f"  ⚠ {inv}")
    else:
        lines.append(f"  ✓ 当前无趋势失效信号")

    # 7. 决策结论
    action = d.get('action', 'Hold')
    confidence = d.get('confidence', 50)
    lines.append(f"\n【决策结论】{action} | 信心度 {confidence:.0f}%")
    lines.append(f"  {'━' * 30}")

    if action in ('Strong Buy', 'Buy'):
        if s_val >= 80 and risk_score <= 30:
            pos = '5%~8%（高仓位）'
        elif s_val >= 60 and risk_score <= 40:
            pos = '3%~5%（中等偏高仓位）'
        else:
            pos = '1%~3%（中等偏低仓位）'
        lines.append(f"  建议仓位：{pos}")
        if price and center:
            if dist < 0:
                # 现价已低于质心→当前就是低吸区间
                stop_loss = price * 0.95
                lines.append(f"  入场策略：现价{price:.2f}已低于质心{center:.2f}（{dist:.1f}%），即为低吸窗口")
                lines.append(f"  止损设置：跌破 {stop_loss:.2f}（现价下方5%）止损")
            elif dist < 5:
                stop_loss = price * 0.95
                lines.append(f"  入场策略：现价附近或回踩质心{center:.2f}时分批建仓")
                lines.append(f"  止损设置：跌破 {stop_loss:.2f}（现价下方5%）止损")
            else:
                stop_loss = center * 0.97
                lines.append(f"  入场策略：耐心等待回踩筹码质心 {center:.2f} 附近低吸，不追高")
                lines.append(f"  止损设置：跌破 {stop_loss:.2f}（质心下方3%）止损")
        else:
            lines.append(f"  入场策略：逢低分批建仓")
        lines.append(f"  目标位：先看 Acceleration 阶段量能确认后的趋势延续")
        lines.append(f"  持仓周期：中线（2~4周），直至出现 Climax/Distribution 信号")
    elif action == 'Buy on Pullback':
        lines.append(f"  建议等待缩量回踩筹码质心附近低吸，不追高。")
        lines.append(f"  若放量站上质心并伴随 MACD 翻红，可视为入场确认信号。")
    elif action == 'Hold':
        lines.append(f"  持有为主，不建议新增仓位。")
        if next_state == 'Acceleration':
            lines.append(f"  若后续量能配合突破关键阻力，可追加仓位。")
        else:
            lines.append(f"  等待趋势进一步确认后再考虑加仓。")
    elif action == 'Reduce':
        lines.append(f"  建议逐步减仓，控制风险敞口。")
        lines.append(f"  若出现 Distribution/Breakdown 信号，应果断离场。")
    elif action == 'Take Profit':
        lines.append(f"  建议分批止盈，锁定利润。")
        lines.append(f"  保留底仓观察后续趋势演化。")
    else:
        lines.append(f"  当前不宜参与，等待更明确的趋势信号。")

    lines.append("")
    lines.append("═" * 60)
    return '\n'.join(lines)


# ============================================================
# Opportunity Score — 交易时机评分
# ============================================================
def calc_opportunity_score(v5_result: Dict) -> Dict:
    """
    计算 Opportunity Score (OS)，用于"当前最值得交易的股票"排序。

    OS = 0.40×Alpha + 0.25×(100−Risk) + 0.20×TransitionScore + 0.15×ActionScore

    Alpha：趋势质量（Composite）。
    RiskScore：风险越低越好，使用 100−Risk。
    TransitionScore：向更强阶段转移加分，向衰退阶段减分。
    ActionScore：Strong Buy > Buy > Buy on Pullback > Hold > Take Profit > Reduce > Avoid。

    返回：
        {'score': float(0~100), 'alpha_part', 'risk_part', 'trans_part', 'action_part',
         'transition_score', 'action_score', 'details': '因子分解说明'}
    """
    a = v5_result.get('alpha', {})
    r = v5_result.get('risk', {})
    t = v5_result.get('trend', {})
    d = v5_result.get('decision', {})

    # ----- 1. Alpha (0~100) -----
    alpha_val = min(100, max(0, a.get('Composite', 50)))

    # ----- 2. Risk part: 100 - Risk (0~100) -----
    risk_val = min(100, max(0, r.get('Composite', 50)))
    risk_part = 100 - risk_val

    # ----- 3. TransitionScore (0~100) -----
    # 基于当前状态 + 下一阶段转移概率
    current_state = t.get('current_state', 'Unknown')
    trans_list = t.get('transition', {}).get('transitions', [])
    # 状态质量分（越高越好）
    state_quality = {
        'Recovery': 35,
        'Birth': 40,
        'Early': 50,
        'Expansion': 70,
        'Acceleration': 85,
        'Climax': 55,
        'Distribution': 25,
        'Breakdown': 10,
    }
    # 下一阶段加分 / 减分
    transition_bonus = {
        'Acceleration': 0.30,
        'Expansion': 0.20,
        'Early': 0.10,
        'Recovery': 0.10,
        'Climax': -0.05,
        'Distribution': -0.20,
        'Breakdown': -0.30,
    }

    base_quality = state_quality.get(current_state, 50)
    bonus = 0.0
    total_prob = 0.0
    for trans in trans_list:
        if isinstance(trans, tuple):
            ns, prob = trans[0], trans[1]
        else:
            ns = trans.get('state', '')
            prob = trans.get('probability', 0)
        if ns in transition_bonus:
            bonus += transition_bonus[ns] * prob
        total_prob += prob

    # 将 base_quality + bonus 映射到 0~100
    transition_score = max(0, min(100, base_quality + bonus * 100))
    # 如果转移概率总和过低，降低分数
    if total_prob < 0.5:
        transition_score *= 0.8

    # ----- 4. ActionScore (0~100) -----
    action_map = {
        'Strong Buy': 100,
        'Buy': 80,
        'Buy on Pullback': 65,
        'Hold': 50,
        'Take Profit': 35,
        'Reduce': 20,
        'Avoid': 0,
    }
    action = d.get('action', 'Hold')
    action_score = action_map.get(action, 50)

    # ----- 5. 加权合成 -----
    w_alpha, w_risk, w_trans, w_action = 0.40, 0.25, 0.20, 0.15
    os_raw = (
        w_alpha * alpha_val +
        w_risk * risk_part +
        w_trans * transition_score +
        w_action * action_score
    )
    os_score = round(max(0, min(100, os_raw)), 1)

    # ----- 6. 因子分解说明 -----
    details = (
        f"OS={os_score} = "
        f"Alpha({alpha_val:.0f})×{w_alpha:.0%} + "
        f"非风险({risk_part:.0f})×{w_risk:.0%} + "
        f"转移({transition_score:.0f})×{w_trans:.0%} + "
        f"操作({action_score:.0f})×{w_action:.0%}"
    )

    return {
        'score': os_score,
        'alpha_part': round(w_alpha * alpha_val, 1),
        'risk_part': round(w_risk * risk_part, 1),
        'trans_part': round(w_trans * transition_score, 1),
        'action_part': round(w_action * action_score, 1),
        'transition_score_raw': round(transition_score, 1),
        'action_score_raw': action_score,
        'details': details,
    }


# ============================================================
# CLI Entry
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Chip Alpha Engine V5')
    parser.add_argument('ts_code', nargs='?', help='Stock code, e.g. 000989.SZ')
    parser.add_argument('--days', type=int, default=20, help='Lookback days')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv("d:/mystock/config/.env")

    engine = ChipAlphaV5Engine()
    if args.ts_code:
        result = engine.analyze(args.ts_code, lookback_days=args.days)
        if args.json:
            print(engine.to_json(result))
        else:
            print(engine.format_report(result))
    else:
        print("Usage: python chip_alpha_v5.py <ts_code> [--days 20] [--json]")


if __name__ == '__main__':
    main()
