"""TERE V1 综合评分计算器.

接收全部8个层级的因子结果，
按 weights.yaml 中的 layer_weights 加权计算总分，
生成 ThemeDailyScore 排行榜。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from theme_engine.config.settings import get_factor_weights, get_layer_weight, load_weights
from theme_engine.models.dataclasses import (
    ExplainItem,
    FactorResult,
    StageResult,
    ThemeDailyScore,
)

logger = logging.getLogger(__name__)

# 层级名称与 ThemeDailyScore 字段的映射
_LAYER_FIELD_MAP: Dict[str, str] = {
    "etf_strength": "etf_strength",
    "breadth": "breadth_score",
    "leader": "leader_strength",
    "purity": "purity_score",
    "resonance": "resonance_score",
    "flow": "flow_score",
    "rotation": "rotation_prob",
    "stage": "stage",
}

# 层级的中文描述（用于生成解释文本）
_LAYER_CN_NAMES: Dict[str, str] = {
    "etf_strength": "ETF强度",
    "breadth": "扩散度",
    "leader": "龙头强度",
    "purity": "主题纯度",
    "resonance": "共振强度",
    "flow": "资金流",
    "rotation": "轮动概率",
    "stage": "生命周期阶段",
}


class ScoreCalculator:
    """综合评分计算器.

    接收全部8个层级的因子结果（作为 FactorResult 字典），
    按 layer_weights 加权得到 total_score，
    生成可解释的 ExplainItem 列表和总结文本。
    """

    def __init__(self) -> None:
        self._layer_weights: Dict[str, float] = {}

    # ── 核心方法 ────────────────────────────────────────────

    def calculate(
        self,
        theme_code: str,
        theme_name: str,
        factor_results: Dict[str, FactorResult],
        **kwargs: Any,
    ) -> ThemeDailyScore:
        """计算单个主题的综合评分.

        Args:
            theme_code: 主题代码
            theme_name: 主题名称
            factor_results: 因子名称 -> FactorResult 的字典，
                至少应包含 etf_strength, breadth, leader, purity,
                resonance, flow 等层级的因子结果。
            **kwargs: 额外信息，可包含:
                stage: StageResult 对象
                signal: SignalResult 对象或信号字符串
                rotation: RotationResult 对象或 rotation_prob 浮点数
                top_leaders: 龙头股票列表
                top_stocks: 强势个股列表
                main_etf: 主 ETF 代码
                backup_etf: 备选 ETF 代码

        Returns:
            ThemeDailyScore 包含完整评分和解释
        """
        self._load_layer_weights()

        # 从 factor_results 中提取各层级分数
        layer_scores: Dict[str, float] = {}
        for layer_name in _LAYER_FIELD_MAP:
            layer_scores[layer_name] = self._extract_layer_score(
                layer_name, factor_results
            )

        # 加权计算总分
        total_score = self._calc_total_score(layer_scores)

        # 提取各层级分数到指定字段
        etf_strength = layer_scores.get("etf_strength", 0.0)
        breadth_score = layer_scores.get("breadth", 0.0)
        leader_strength = layer_scores.get("leader", 0.0)
        purity_score = layer_scores.get("purity", 0.0)
        resonance_score = layer_scores.get("resonance", 0.0)
        flow_score = layer_scores.get("flow", 0.0)

        # 处理轮动概率
        rotation_prob = layer_scores.get("rotation", 0.0)
        if "rotation" in kwargs:
            rot = kwargs["rotation"]
            if isinstance(rot, (int, float)):
                rotation_prob = float(rot)
            elif hasattr(rot, "rotation_score"):
                rotation_prob = getattr(rot, "rotation_score", rotation_prob)

        # 处理阶段信息
        stage_str = ""
        if "stage" in kwargs:
            stg = kwargs["stage"]
            if isinstance(stg, str):
                stage_str = stg
            elif isinstance(stg, StageResult):
                stage_str = stg.current_stage
            elif isinstance(stg, dict):
                stage_str = stg.get("current_stage", "")

        # 处理信号
        signal_str = "WATCH"
        if "signal" in kwargs:
            sig = kwargs["signal"]
            if isinstance(sig, str):
                signal_str = sig
            elif hasattr(sig, "signal"):
                signal_str = getattr(sig, "signal", "WATCH")

        # 构建解释列表
        explanations = self._build_explanations(
            etf_strength=etf_strength,
            breadth_score=breadth_score,
            leader_strength=leader_strength,
            purity_score=purity_score,
            resonance_score=resonance_score,
            flow_score=flow_score,
            stage=stage_str,
            signal=signal_str,
        )

        # 提取龙头和个股
        top_leaders: List[str] = list(kwargs.get("top_leaders", []))
        top_stocks: List[str] = list(kwargs.get("top_stocks", []))
        main_etf: str = str(kwargs.get("main_etf", ""))
        backup_etf: Optional[str] = kwargs.get("backup_etf", None)
        if backup_etf is not None:
            backup_etf = str(backup_etf)

        trade_date: str = str(kwargs.get("trade_date", ""))
        created_at: str = datetime.now().isoformat()

        result = ThemeDailyScore(
            rank=0,
            theme_code=theme_code,
            theme_name=theme_name,
            total_score=round(total_score, 2),
            etf_strength=round(etf_strength, 2),
            breadth_score=round(breadth_score, 2),
            leader_strength=round(leader_strength, 2),
            purity_score=round(purity_score, 2),
            resonance_score=round(resonance_score, 2),
            flow_score=round(flow_score, 2),
            stage=stage_str,
            rotation_prob=round(rotation_prob, 2),
            signal=signal_str,
            top_leaders=top_leaders,
            top_stocks=top_stocks,
            main_etf=main_etf,
            backup_etf=backup_etf,
            explanations=explanations,
            summary="",
            trade_date=trade_date,
            created_at=created_at,
        )

        return result

    def rank(
        self,
        themes: List[ThemeDailyScore],
    ) -> List[ThemeDailyScore]:
        """对主题列表按 total_score 降序排列并设置 rank.

        Args:
            themes: 待排名的 ThemeDailyScore 列表

        Returns:
            排序后的列表（直接修改原列表并返回）
        """
        # 按 total_score 降序排列
        sorted_themes = sorted(themes, key=lambda x: x.total_score, reverse=True)

        # 设置 rank 并生成总结
        for i, t in enumerate(sorted_themes):
            t.rank = i + 1

        # 生成全局总结
        summary = self._build_summary(sorted_themes)
        for t in sorted_themes:
            t.summary = summary

        return sorted_themes

    # ── 解释生成 ────────────────────────────────────────────

    def _build_explanations(
        self,
        etf_strength: float,
        breadth_score: float,
        leader_strength: float,
        purity_score: float,
        resonance_score: float,
        flow_score: float,
        stage: str,
        signal: str,
    ) -> List[ExplainItem]:
        """生成可解释 AI 条目列表.

        每个 ExplainItem 包含:
        - reason: 自然语言描述该层级贡献/影响
        - score: 该层级评分
        - weight: 该层级权重
        """
        explanations: List[ExplainItem] = []

        # ETF强度
        explanations.append(
            ExplainItem(
                reason=self._describe_layer("etf_strength", etf_strength),
                score=round(etf_strength, 2),
                weight=self._layer_weights.get("etf_strength", 0),
            )
        )

        # 扩散度
        explanations.append(
            ExplainItem(
                reason=self._describe_layer("breadth", breadth_score),
                score=round(breadth_score, 2),
                weight=self._layer_weights.get("breadth", 0),
            )
        )

        # 龙头强度
        explanations.append(
            ExplainItem(
                reason=self._describe_layer("leader", leader_strength),
                score=round(leader_strength, 2),
                weight=self._layer_weights.get("leader", 0),
            )
        )

        # 主题纯度
        explanations.append(
            ExplainItem(
                reason=self._describe_layer("purity", purity_score),
                score=round(purity_score, 2),
                weight=self._layer_weights.get("purity", 0),
            )
        )

        # 共振强度
        explanations.append(
            ExplainItem(
                reason=self._describe_layer("resonance", resonance_score),
                score=round(resonance_score, 2),
                weight=self._layer_weights.get("resonance", 0),
            )
        )

        # 资金流
        explanations.append(
            ExplainItem(
                reason=self._describe_layer("flow", flow_score),
                score=round(flow_score, 2),
                weight=self._layer_weights.get("flow", 0),
            )
        )

        # 阶段
        stage_desc = f"处于{self._stage_cn(stage)}阶段"
        explanations.append(
            ExplainItem(
                reason=stage_desc,
                score=0.0,
                weight=0.0,
            )
        )

        # 信号
        signal_desc = self._describe_signal(signal)
        if signal_desc:
            explanations.append(
                ExplainItem(
                    reason=signal_desc,
                    score=0.0,
                    weight=0.0,
                )
            )

        return explanations

    def _build_summary(self, themes: List[ThemeDailyScore]) -> str:
        """生成一句话市场总结.

        描述今日最强主线及其核心特征。
        """
        if not themes:
            return "当日无活跃主题"

        top = themes[0]
        top_stage_cn = self._stage_cn(top.stage)

        # 构建总结
        parts: List[str] = [f"今日最强主线为{top.theme_name}"]

        if top.resonance_score > 0:
            parts.append(f"共振强度{top.resonance_score:.0f}")
        if top_stage_cn:
            parts.append(f"处于{top_stage_cn}阶段")
        if top.rotation_prob > 50:
            parts.append(f"主线延续概率{top.rotation_prob:.0f}%")
        if top.total_score > 0:
            parts.append(f"综合评分{top.total_score:.1f}")

        # 如有第二、三名，简要提及
        if len(themes) > 1 and themes[1].total_score > 50:
            parts.append(f"次强{themes[1].theme_name}({themes[1].total_score:.1f}分)")
        if len(themes) > 2 and themes[2].total_score > 50:
            parts.append(f"第三{themes[2].theme_name}({themes[2].total_score:.1f}分)")

        return "，".join(parts)

    # ── 内部工具 ────────────────────────────────────────────

    def _extract_layer_score(
        self,
        layer_name: str,
        factor_results: Dict[str, FactorResult],
    ) -> float:
        """从 factor_results 中提取指定层级的分数.

        策略:
        1. 尝试用层级名称直接匹配（如 etf_strength）
        2. 如果该层级有多个子因子，取加权平均
        """
        # 直接匹配
        if layer_name in factor_results:
            return factor_results[layer_name].score

        # 尝试匹配子因子模式（如 etf_strength_trend, etf_strength_momentum）
        layer_prefix = layer_name + "_"
        related: List[FactorResult] = []
        for f_name, f_result in factor_results.items():
            if f_name.startswith(layer_prefix):
                related.append(f_result)

        if related:
            # 加权平均
            total_weight = sum(r.weight for r in related if r.weight > 0)
            if total_weight > 0:
                return sum(r.score * r.weight for r in related) / total_weight
            return sum(r.score for r in related) / len(related)

        # 尝试从 details 中提取
        for f_result in factor_results.values():
            if layer_name in f_result.details:
                val = f_result.details[layer_name]
                if isinstance(val, (int, float)):
                    return float(val)

        logger.debug("层级 %s 未在 factor_results 中找到匹配", layer_name)
        return 0.0

    def _calc_total_score(self, layer_scores: Dict[str, float]) -> float:
        """按 layer_weights 加权计算总分 (0~100)."""
        total_weight = sum(self._layer_weights.values())
        if total_weight <= 0:
            return 0.0

        weighted_sum = 0.0
        for layer_name, weight in self._layer_weights.items():
            score = layer_scores.get(layer_name, 0.0)
            weighted_sum += score * weight

        return weighted_sum / total_weight

    def _load_layer_weights(self) -> None:
        """从 weights.yaml 加载 layer_weights."""
        try:
            cfg = load_weights()
            raw = cfg.get("layer_weights", {})
            self._layer_weights = {k: float(v) for k, v in raw.items()}
        except Exception as e:
            logger.warning("加载 layer_weights 失败: %s", e)
            self._layer_weights = {
                "etf_strength": 30,
                "breadth": 20,
                "leader": 20,
                "purity": 10,
                "resonance": 10,
                "flow": 5,
                "rotation": 5,
            }

    def _describe_layer(self, layer_name: str, score: float) -> str:
        """生成某个层级的自然语言描述."""
        cn_name = _LAYER_CN_NAMES.get(layer_name, layer_name)
        weight = self._layer_weights.get(layer_name, 0)

        if score >= 80:
            level = "强势"
        elif score >= 60:
            level = "良好"
        elif score >= 40:
            level = "一般"
        elif score >= 20:
            level = "较弱"
        else:
            level = "极弱"

        return f"{cn_name}{level} ({score:.0f}分, 权重{weight:.0f}%)"

    @staticmethod
    def _stage_cn(stage: str) -> str:
        """将阶段英文名转为中文."""
        mapping = {
            "birth": "萌芽",
            "growth": "成长",
            "expansion": "扩散",
            "main_trend": "主升浪",
            "distribution": "派发",
            "death": "消亡",
        }
        return mapping.get(stage, stage)

    @staticmethod
    def _describe_signal(signal: str) -> str:
        """生成交易信号描述."""
        desc_map: Dict[str, str] = {
            "STRONG_BUY": "强烈买入信号",
            "BUY": "买入信号",
            "WATCH": "观察中",
            "REDUCE": "减仓信号",
            "EXIT": "离场信号",
        }
        return desc_map.get(signal, "")
