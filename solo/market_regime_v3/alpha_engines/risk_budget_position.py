# -*- coding: utf-8 -*-
"""
Risk Budget Position Engine (V6.1 Module 4)

核心升级：简化组合优化，采用风险预算模型。

Position = Base Position × Market Multiplier × EV Multiplier × Risk Multiplier

其中：
  - Base Position:   基础仓位（由Exposure Model给出）
  - Market Multiplier: 市场状态乘数（由Regime决定）
  - EV Multiplier:    期望收益乘数（由EV Engine决定）
  - Risk Multiplier:  风险乘数（由ATR、波动率、最大回撤决定）

输出：
  每只股票的最终仓位 + 解释说明
"""

import os
import sys
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@dataclass
class RiskBudgetExplanation:
    """仓位解释"""
    base_position_pct: float = 0.0       # 基础仓位%
    market_multiplier: float = 1.0        # 市场乘数
    ev_multiplier: float = 1.0            # EV乘数
    risk_multiplier: float = 1.0          # 风险乘数
    final_position_pct: float = 0.0       # 最终仓位%

    # 明细
    regime_label: str = ''
    theme_label: str = ''
    ev_label: str = ''
    risk_label: str = ''

    # 约束
    max_per_position_pct: float = 15.0    # 单票上限

    # V6.2 Learning Mode
    system_mode: str = 'LIVE'             # LIVE / LEARNING / VALIDATION
    is_learning: bool = False             # 是否为学习仓位

    # V6.2 Learning Position Engine 独立解释字段
    learning_base_pct: float = 5.0        # Base Learning Position (%)
    confidence_adj: float = 1.0           # Confidence Adjustment (A=1.0/B=0.8/C=0.6/D=0.4)
    risk_adj: float = 1.0                 # Risk Adjustment (基于预期回撤)


@dataclass
class PositionResult:
    """仓位结果"""
    ts_code: str
    name: str = ''
    theme: str = ''
    position_pct: float = 0.0        # 最终仓位%
    explanation: RiskBudgetExplanation = field(default_factory=RiskBudgetExplanation)
    signal: str = 'AVOID'
    is_learning: bool = False         # V6.2: 是否为学习仓位


@dataclass
class RiskBudgetResult:
    """风险预算引擎输出"""
    trade_date: str
    positions: Dict[str, PositionResult] = field(default_factory=dict)
    total_exposure: float = 0.0       # 总仓位
    remaining_cash: float = 1.0        # 剩余现金
    asset_count: int = 0
    system_mode: str = 'LIVE'          # V6.2: 系统模式
    learning_count: int = 0            # V6.2: 学习仓位数量


class RiskBudgetPositionEngine:
    """风险预算仓位引擎

    用风险预算模型替代复杂的组合优化。
    """

    def __init__(self, config: dict):
        # 从配置读取单票上限
        rc = config.get('risk_control', {})
        self.max_per_position = rc.get('max_per_position_pct', 0.15)

        # EV乘数映射
        self.ev_multiplier_map = {
            'BUY': 1.0,
            'WAIT': 0.5,
            'AVOID': 0.0,
        }

        # 风险乘数（由ATR%和预期回撤决定）
        self.enabled = config.get('risk_budget_position', {}).get('enabled', True)

        # V6.2 Learning Mode 配置
        lm_cfg = config.get('learning_mode', {})
        self.lm_enabled = lm_cfg.get('enabled', True)
        self.lm_min_samples = lm_cfg.get('min_samples_live', 30)
        self.lm_base_position = lm_cfg.get('base_learning_position', 5)
        self.lm_clamp_min = lm_cfg.get('clamp_min', 3)
        self.lm_clamp_max = lm_cfg.get('clamp_max', 8)
        self.lm_max_total = lm_cfg.get('max_total_learning', 20)
        self.lm_regime_min = lm_cfg.get('regime_min', 'Recovery')
        self.lm_leader_rank_max = lm_cfg.get('leader_rank_max', 100)
        self.lm_smart_money_min = lm_cfg.get('smart_money_min', 60)
        self.lm_conf_adj_map = lm_cfg.get('confidence_adjustment', {
            'A': 1.0, 'B': 0.8, 'C': 0.6, 'D': 0.4,
        })

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def allocate(
        self,
        trade_date: str,
        candidates: List[Dict],
        base_exposure_pct: float,
        regime_name: str,
        ev_results: Dict,
        market_score: float,
        system_mode: str = 'LIVE',              # V6.2: LIVE/LEARNING/VALIDATION
        smart_money_scores: Dict = None,        # V6.2: {ts_code: score}
    ) -> RiskBudgetResult:
        """执行风险预算分配

        Args:
            trade_date: 交易日
            candidates: 候选标的列表（含个股特征）
            base_exposure_pct: 总仓位%（如29%）
            regime_name: 市场状态
            ev_results: EV引擎结果 {ts_code: EVResult}
            market_score: 市场评分
            system_mode: V6.2 系统模式 LIVE/LEARNING/VALIDATION
            smart_money_scores: V6.2 聪明钱评分 {ts_code: score}

        Returns:
            RiskBudgetResult
        """
        result = RiskBudgetResult(trade_date=trade_date, system_mode=system_mode)

        # V6.2: VALIDATION模式 → 全0仓位
        if system_mode == 'VALIDATION':
            for c in candidates:
                code = c.get('ts_code', '')
                pr = PositionResult(
                    ts_code=code, name=c.get('name', ''), theme=c.get('theme', ''),
                    position_pct=0.0, signal='VALIDATION',
                    explanation=RiskBudgetExplanation(
                        system_mode='VALIDATION',
                        regime_label=regime_name,
                        ev_label='回测模式，不建仓',
                        risk_label='',
                    )
                )
                result.positions[code] = pr
            return result

        # 1) Market Multiplier — 由Regime决定
        market_mult = self._get_market_multiplier(regime_name, market_score)

        # V6.2: Learning Mode 市场状态检查
        regime_learning_ok = self._check_regime_learning_ok(regime_name)

        position_results = []
        learning_count = 0
        sm_scores = smart_money_scores or {}

        for c in candidates:
            code = c.get('ts_code', '')
            name = c.get('name', '')
            theme = c.get('theme', '')
            ev = ev_results.get(code)
            leader_rank = c.get('leader_score') or c.get('leader_rank')

            if ev is None or ev.signal.value == 'AVOID':
                # AVOID信号 → 仓位0
                pr = PositionResult(
                    ts_code=code, name=name, theme=theme,
                    position_pct=0.0, signal='AVOID',
                    explanation=RiskBudgetExplanation(
                        system_mode=system_mode,
                        regime_label=regime_name,
                        ev_label='期望收益不足',
                        risk_label='信号不满足要求',
                    )
                )
                position_results.append(pr)
                continue

            # ── V6.2: Learning Mode 检测 ──
            is_learning = False
            if system_mode == 'LEARNING' and self.lm_enabled and regime_learning_ok:
                n_samples = ev.n_samples
                sm_score = sm_scores.get(code, 0)
                if (n_samples < self.lm_min_samples
                        and (leader_rank is None or leader_rank <= self.lm_leader_rank_max)
                        and sm_score >= self.lm_smart_money_min):
                    is_learning = True

            if is_learning:
                # Learning Position: 独立公式，不使用Risk Budget
                base_pct, conf_adj, risk_adj, final_pct = self._calc_learning_position(ev)
                explanation = RiskBudgetExplanation(
                    system_mode='LEARNING',
                    is_learning=True,
                    final_position_pct=final_pct,
                    learning_base_pct=base_pct,
                    confidence_adj=conf_adj,
                    risk_adj=risk_adj,
                    regime_label=regime_name,
                    theme_label=f'{theme}',
                    ev_label=f'EV={ev.expected_value_10d:+.2%} Conf={ev.confidence_level} n={ev.n_samples}',
                    risk_label=f'DD={abs(ev.expected_drawdown):.1%}' if ev.expected_drawdown < 0 else 'DD=5.0%',
                    max_per_position_pct=self.lm_clamp_max,
                )
                pr = PositionResult(
                    ts_code=code, name=name, theme=theme,
                    position_pct=final_pct,
                    signal=ev.signal.value,
                    is_learning=True,
                    explanation=explanation,
                )
                position_results.append(pr)
                learning_count += 1
                continue

            # 2) EV Multiplier
            ev_mult = self._get_ev_multiplier(ev.signal.value, ev.expected_value_10d)

            # 3) Risk Multiplier
            atr_pct = c.get('atr', 0) / max(c.get('ref_price', 1), 0.01)
            expected_dd = abs(ev.expected_drawdown) if ev.expected_drawdown < 0 else 0.05
            risk_mult = self._get_risk_multiplier(atr_pct, expected_dd)

            # 4) 基础仓位 = 总仓位 × leader_allocation
            base_pos = base_exposure_pct * 0.5  # leader allocation ~50% of total

            # 5) 最终仓位
            raw_position = base_pos * market_mult * ev_mult * risk_mult

            # 6) 约束：不超过单票上限
            max_pos = self.max_per_position * 100  # 转换为百分比
            final_position = min(raw_position, max_pos)
            final_position = max(final_position, 0.0)

            # 构建解释
            explanation = RiskBudgetExplanation(
                base_position_pct=round(base_pos, 1),
                market_multiplier=round(market_mult, 2),
                ev_multiplier=round(ev_mult, 2),
                risk_multiplier=round(risk_mult, 2),
                final_position_pct=round(final_position, 1),
                system_mode=system_mode,
                regime_label=regime_name,
                theme_label=f'{theme}',
                ev_label=f'EV={ev.expected_value_10d:+.2%}',
                risk_label=f'ATR={atr_pct:.1%} DD={expected_dd:.1%}',
                max_per_position_pct=round(max_pos, 1),
            )

            pr = PositionResult(
                ts_code=code, name=name, theme=theme,
                position_pct=round(final_position, 1),
                signal=ev.signal.value,
                explanation=explanation,
            )
            position_results.append(pr)

        # 汇总 + V6.2 学习仓位总上限
        # ─────────────────────────────────────
        if learning_count > 0:
            learning_positions = [p for p in position_results if p.is_learning]
            total_learning = sum(p.position_pct for p in learning_positions)
            if total_learning > self.lm_max_total and total_learning > 0:
                scale = self.lm_max_total / total_learning
                # 先缩放+四舍五入到1位小数
                for p in learning_positions:
                    p.position_pct = round(p.position_pct * scale, 1)
                    p.explanation.risk_label += f' (总上限{self.lm_max_total}%等比例)'
                # 尾差修正：调整最后一只学习仓位使总和精确等于上限
                sum_after_round = sum(p.position_pct for p in learning_positions)
                diff = round(self.lm_max_total - sum_after_round, 1)
                if abs(diff) > 0.01 and len(learning_positions) > 0:
                    # 调整最后一只（修正后不低于clamp_min且不高于clamp_max）
                    last = learning_positions[-1]
                    adjusted = round(last.position_pct + diff, 1)
                    adjusted = max(self.lm_clamp_min, min(self.lm_clamp_max, adjusted))
                    last.position_pct = adjusted
                # 同步final_position_pct
                for p in learning_positions:
                    p.explanation.final_position_pct = p.position_pct

        for pr in position_results:
            result.positions[pr.ts_code] = pr

        # 总仓位 = 所有BUY/Wait信号仓位之和
        total_pos = sum(p.position_pct for p in position_results if p.position_pct > 0)
        result.total_exposure = round(total_pos, 1)
        result.remaining_cash = round(100 - total_pos, 1) if total_pos < 100 else 0
        result.asset_count = sum(1 for p in position_results if p.position_pct > 0)
        result.learning_count = learning_count
        result.system_mode = system_mode

        return result

    # ──────────────────────────────────────────────
    # 乘数计算
    # ──────────────────────────────────────────────

    def _check_regime_learning_ok(self, regime: str) -> bool:
        """检查市场状态是否允许学习模式

        LEARNING模式要求 Market Regime >= Recovery
        """
        learning_ok_regimes = ['Recovery', 'Neutral', 'Bull', 'Euphoria']
        return regime in learning_ok_regimes

    def _calc_learning_position(self, ev) -> tuple:
        """独立Learning Position Engine

        Formula:
            Learning_Position = Base_Learning_Position
                              × Confidence_Adjustment
                              × Risk_Adjustment

        Returns:
            (base_pct, confidence_adj, risk_adj, final_clamped_pct)
        """
        base = float(self.lm_base_position)

        # Confidence Adjustment: A=1.0, B=0.8, C=0.6, D=0.4
        conf_level = getattr(ev, 'confidence_level', 'D')
        conf_adj = self.lm_conf_adj_map.get(conf_level, 0.4)

        # Risk Adjustment: 基于预期回撤
        expected_dd = abs(ev.expected_drawdown) if ev.expected_drawdown < 0 else 0.05
        if expected_dd <= 0.03:
            risk_adj = 1.0
        elif expected_dd <= 0.06:
            risk_adj = 0.8
        elif expected_dd <= 0.10:
            risk_adj = 0.6
        else:
            risk_adj = 0.4

        raw = base * conf_adj * risk_adj
        final_pct = max(self.lm_clamp_min, min(self.lm_clamp_max, raw))
        final_pct = round(final_pct, 1)

        return (float(base), float(conf_adj), float(risk_adj), final_pct)

    def _get_market_multiplier(self, regime: str, market_score: float) -> float:
        """市场状态乘数

        Bear:     0.0  (不建仓)
        Recovery: 0.6  (试探性建仓)
        Neutral:  0.8  (正常建仓)
        Bull:     1.0  (积极建仓)
        Euphoria: 0.7  (警惕过热)
        """
        multipliers = {
            'Bear': 0.0,
            'Recovery': 0.6,
            'Neutral': 0.8,
            'Bull': 1.0,
            'Euphoria': 0.7,
        }
        base = multipliers.get(regime, 0.5)
        # 在Regime内部用market_score微调
        score_factor = (market_score - 30) / 40  # 0.0~1.0
        score_factor = max(0.0, min(1.0, score_factor))
        return base + (1.0 - base) * score_factor * 0.3

    def _get_ev_multiplier(self, signal: str, ev: float) -> float:
        """期望收益乘数"""
        if signal == 'BUY':
            # EV越高，乘数越大
            return min(1.2, 0.8 + ev * 5)
        elif signal == 'WAIT':
            return 0.5
        else:
            return 0.0

    def _get_risk_multiplier(self, atr_pct: float, expected_dd: float) -> float:
        """风险乘数

        基于ATR和预期回撤：
          - ATR越小、预期回撤越小 → 乘数越高（接近1.0）
          - ATR越大、预期回撤越大 → 乘数越低（接近0.0）
        """
        # ATR风险: ATR < 3% → 低风险, ATR > 8% → 高风险
        atr_risk = 1.0 - min(1.0, max(0.0, (atr_pct - 0.01) / 0.07))

        # 回撤风险: 预期回撤 < 3% → 低风险, > 10% → 高风险
        dd_risk = 1.0 - min(1.0, max(0.0, (expected_dd - 0.02) / 0.08))

        # 综合风险乘数
        return (atr_risk * 0.5 + dd_risk * 0.5)

    # ──────────────────────────────────────────────
    # 报告生成
    # ──────────────────────────────────────────────

    def format_explanation(self, pr: PositionResult) -> str:
        """生成仓位解释文本"""
        if pr.position_pct <= 0:
            return f"{pr.name}({pr.ts_code}): {pr.signal} — 不建仓"

        exp = pr.explanation
        if pr.is_learning:
            lines = [
                f"{pr.name}({pr.ts_code}): [LEARNING]",
                f"  市场状态: {exp.regime_label}",
                f"  主题: {pr.theme}",
                f"  EV: {exp.ev_label}",
                f"  风险: {exp.risk_label}",
                f"  Learning Base: {exp.learning_base_pct:.0f}%",
                f"  Confidence Adj: {exp.confidence_adj:.1f}",
                f"  Risk Adj: {exp.risk_adj:.1f}",
                f"  Final Position: {exp.final_position_pct:.1f}% (上限{exp.max_per_position_pct:.0f}%)",
            ]
        else:
            lines = [
                f"{pr.name}({pr.ts_code}):",
                f"  市场状态: {exp.regime_label}",
                f"  主题: {pr.theme}",
                f"  EV: {exp.ev_label}",
                f"  风险: {exp.risk_label}",
                f"  仓位计算: {exp.base_position_pct:.0f}% × {exp.market_multiplier:.1f}(市) × {exp.ev_multiplier:.1f}(EV) × {exp.risk_multiplier:.1f}(风) = {exp.final_position_pct:.1f}%",
                f"  最终仓位: {exp.final_position_pct:.1f}% (上限{exp.max_per_position_pct:.0f}%)",
            ]
        return "\n".join(lines)
