"""TERE V1 主题生命周期状态机 — 动态评分阶段判定.

状态: birth → growth → expansion → main_trend → distribution → death
允许跳级（如 birth → death），不允许逆序。
不使用固定阈值，使用动态评分判定。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from theme_engine.config.settings import load_weights
from theme_engine.models.dataclasses import StageResult

logger = logging.getLogger(__name__)

# 阶段顺序（索引越小越早）
_STAGE_ORDER: List[str] = [
    "birth",
    "growth",
    "expansion",
    "main_trend",
    "distribution",
    "death",
]
_STAGE_ORDER_MAP: Dict[str, int] = {s: i for i, s in enumerate(_STAGE_ORDER)}


class StageStateMachine:
    """主题生命周期状态机.

    使用动态评分对每个阶段计算匹配度（0~100），
    选择最高分阶段，并应用逆序禁止规则。
    通过 load_history / save_history 维护每个主题的阶段持续天数。
    """

    def __init__(self) -> None:
        # theme_code -> StageResult  （内存缓存，生产环境应替换为持久化存储）
        self._history: Dict[str, StageResult] = {}
        self._stage_cfg: Dict[str, Any] = {}

    # ── 历史记录管理 ────────────────────────────────────────

    def load_history(self, theme_code: str) -> Optional[StageResult]:
        """加载主题的历史阶段记录."""
        return self._history.get(theme_code)

    def save_history(self, theme_code: str, result: StageResult) -> None:
        """保存主题的阶段记录."""
        self._history[theme_code] = result

    # ── 核心判定 ────────────────────────────────────────────

    async def analyze(
        self,
        theme_code: str,
        trade_date: str,
        indicators: Dict[str, Any],
    ) -> StageResult:
        """执行阶段判定.

        Args:
            theme_code: 主题代码
            trade_date: 交易日 YYYYMMDD
            indicators: 包含各项评分指标的字典, 至少应含:
                etf_strength, breadth, leader, purity, resonance, flow
                可选: leader_count, etf_change, breadth_change, amount_change

        Returns:
            StageResult 包含判定结果
        """
        await asyncio.sleep(0)

        # 加载阶段配置
        self._load_stage_config()

        # 获取历史记录
        prev = self.load_history(theme_code)
        prev_stage = prev.current_stage if prev else None
        prev_stage_idx = _STAGE_ORDER_MAP.get(prev_stage, -1) if prev_stage else -1

        # 计算各阶段匹配度
        stage_scores: Dict[str, float] = {}
        for stage in _STAGE_ORDER:
            try:
                score_fn = getattr(self, f"_score_{stage}", None)
                if score_fn:
                    stage_scores[stage] = score_fn(indicators)
                else:
                    stage_scores[stage] = 0.0
            except Exception as e:
                logger.warning("阶段 %s 评分异常: %s", stage, e)
                stage_scores[stage] = 0.0

        # 按分数降序排序
        sorted_stages: List[Tuple[str, float]] = sorted(
            stage_scores.items(), key=lambda x: x[1], reverse=True
        )
        best_stage, best_score = sorted_stages[0]
        second_score = sorted_stages[1][1] if len(sorted_stages) > 1 else 0.0

        # 计算置信度 (最高分 - 次高分) / 100
        confidence = (best_score - second_score) / 100.0
        confidence = max(0.0, min(1.0, confidence))

        # 应用逆序禁止规则：不允许跳到更早的阶段
        selected_stage = best_stage
        selected_idx = _STAGE_ORDER_MAP.get(selected_stage, 0)
        if prev_stage_idx >= 0 and selected_idx < prev_stage_idx:
            logger.info(
                "主题 %s 阶段逆序: %s -> %s (禁止), 保持 %s",
                theme_code, prev_stage, selected_stage, prev_stage,
            )
            selected_stage = prev_stage  # type: ignore[assignment]
            confidence = min(confidence, 0.3)  # 逆序保持时降低置信度

        # 预测下一阶段
        next_stage = self._predict_next(selected_stage, indicators)

        # 计算阶段内进度
        progress = self._calc_progress(selected_stage, indicators)

        # 计算阶段持续天数
        days_in_stage = 1
        if prev and prev.current_stage == selected_stage:
            days_in_stage = prev.days_in_stage + 1

        result = StageResult(
            theme_code=theme_code,
            trade_date=trade_date,
            current_stage=selected_stage,
            stage_confidence=round(confidence, 4),
            days_in_stage=days_in_stage,
            stage_progress=round(progress, 4),
            next_stage=next_stage,
            indicators=dict(indicators),
            details={
                "stage_scores": stage_scores,
                "sorted_scores": sorted_stages,
                "prev_stage": prev_stage,
            },
        )

        self.save_history(theme_code, result)
        return result

    # ── 阶段评分函数（每阶段 0~100） ───────────────────────

    def _score_birth(self, indicators: Dict[str, Any]) -> float:
        """birth 阶段匹配度评分.

        特征: 低ETF强度、低扩散度、龙头数<2
        """
        scores: List[float] = []

        # ETF强度越低越匹配 birth
        etf = self._safe_float(indicators, "etf_strength", 50)
        scores.append(self._score_low_is_better(etf, 30, 0))

        # 扩散度越低越匹配
        breadth = self._safe_float(indicators, "breadth", 50)
        scores.append(self._score_low_is_better(breadth, 30, 0))

        # 龙头数 < 2
        leader_count = self._safe_int(indicators, "leader_count", 0)
        scores.append(max(0.0, 100.0 - leader_count * 50.0))

        # 共振低
        resonance = self._safe_float(indicators, "resonance", 50)
        scores.append(self._score_low_is_better(resonance, 30, 0))

        # 资金流低
        flow = self._safe_float(indicators, "flow", 50)
        scores.append(self._score_low_is_better(flow, 20, 0))

        return self._safe_mean(scores)

    def _score_growth(self, indicators: Dict[str, Any]) -> float:
        """growth 阶段匹配度评分.

        特征: ETF强度30~60、扩散度上升、有龙头出现
        """
        scores: List[float] = []

        # ETF强度在30-60范围
        etf = self._safe_float(indicators, "etf_strength", 50)
        scores.append(self._score_range(etf, 30, 60))

        # 扩散度中等偏低但有所上升（若有breadth_change）
        breadth = self._safe_float(indicators, "breadth", 50)
        scores.append(self._score_range(breadth, 20, 50))

        # 扩散度上升趋势加分
        breadth_change = self._safe_float(indicators, "breadth_change", 0)
        scores.append(self._score_higher_is_better(breadth_change, 0, 20, 50))

        # 有龙头出现（leader score 中等）
        leader = self._safe_float(indicators, "leader", 50)
        scores.append(self._score_range(leader, 20, 60))

        # 龙头计数 > 0
        leader_count = self._safe_int(indicators, "leader_count", 0)
        scores.append(self._score_higher_is_better(float(leader_count), 1, 3, 100))

        return self._safe_mean(scores)

    def _score_expansion(self, indicators: Dict[str, Any]) -> float:
        """expansion 阶段匹配度评分.

        特征: ETF强度40~70、扩散度40~80、龙头扩散、成交额放大
        """
        scores: List[float] = []

        # ETF强度中等偏高
        etf = self._safe_float(indicators, "etf_strength", 50)
        scores.append(self._score_range(etf, 40, 70))

        # 扩散度中等
        breadth = self._safe_float(indicators, "breadth", 50)
        scores.append(self._score_range(breadth, 40, 80))

        # 龙头强度较高
        leader = self._safe_float(indicators, "leader", 50)
        scores.append(self._score_range(leader, 40, 80))

        # 龙头数扩散
        leader_count = self._safe_int(indicators, "leader_count", 0)
        scores.append(self._score_higher_is_better(float(leader_count), 3, 6, 100))

        # 成交额放大
        amount_change = self._safe_float(indicators, "amount_change", 0)
        scores.append(self._score_higher_is_better(amount_change, 5, 30, 100))

        # 资金流活跃
        flow = self._safe_float(indicators, "flow", 50)
        scores.append(self._score_range(flow, 30, 80))

        return self._safe_mean(scores)

    def _score_main_trend(self, indicators: Dict[str, Any]) -> float:
        """main_trend 阶段匹配度评分.

        特征: ETF强度>60、扩散度>60、共振>50、龙头稳定
        """
        scores: List[float] = []

        # ETF强度高
        etf = self._safe_float(indicators, "etf_strength", 50)
        scores.append(self._score_higher_is_better(etf, 60, 80, 100))

        # 扩散度高
        breadth = self._safe_float(indicators, "breadth", 50)
        scores.append(self._score_higher_is_better(breadth, 60, 80, 100))

        # 共振强
        resonance = self._safe_float(indicators, "resonance", 50)
        scores.append(self._score_higher_is_better(resonance, 50, 75, 100))

        # 龙头强度高且稳定
        leader = self._safe_float(indicators, "leader", 50)
        scores.append(self._score_higher_is_better(leader, 60, 80, 100))

        # 龙头数充足
        leader_count = self._safe_int(indicators, "leader_count", 0)
        scores.append(self._score_higher_is_better(float(leader_count), 3, 5, 100))

        # 纯度较好
        purity = self._safe_float(indicators, "purity", 50)
        scores.append(self._score_higher_is_better(purity, 40, 70, 100))

        return self._safe_mean(scores)

    def _score_distribution(self, indicators: Dict[str, Any]) -> float:
        """distribution 阶段匹配度评分.

        特征: ETF强度下降(但可能仍在高位)、扩散度下降、龙头数减少、成交额萎缩
        """
        scores: List[float] = []

        # ETF强度可能还在中高位但出现下降趋势
        etf = self._safe_float(indicators, "etf_strength", 50)
        etf_change = self._safe_float(indicators, "etf_change", 0)
        # 高位 + 下降趋势 = 高分配分
        if etf > 40 and etf_change < 0:
            scores.append(min(100.0, (etf / 100.0 * 50.0) + (-etf_change) * 2.0))
        else:
            scores.append(0.0)

        # 扩散度下降
        breadth_change = self._safe_float(indicators, "breadth_change", 0)
        scores.append(self._score_low_is_better(breadth_change, -5, -20))
        # 注意: 这里 low_is_better 用于负值，breadth_change 越负越匹配

        # 龙头数减少
        leader_count = self._safe_int(indicators, "leader_count", 5)
        # 假设历史有3-5个龙头，现在减少
        scores.append(self._score_range(float(leader_count), 1, 3))

        # 龙头强度下降
        leader = self._safe_float(indicators, "leader", 50)
        scores.append(self._score_range(leader, 20, 60))

        # 成交额萎缩
        amount_change = self._safe_float(indicators, "amount_change", 0)
        scores.append(self._score_low_is_better(amount_change, -5, -30))

        # 扩散度可能出现分歧（高 breadth 但为负 change）
        breadth = self._safe_float(indicators, "breadth", 50)
        if breadth > 40 and breadth_change < -2:
            scores.append(80.0)  # 扩散度背离，高分配分
        else:
            scores.append(0.0)

        return self._safe_mean(scores)

    def _score_death(self, indicators: Dict[str, Any]) -> float:
        """death 阶段匹配度评分.

        特征: ETF强度<20、扩散度<20、无龙头、成交额极低
        """
        scores: List[float] = []

        # ETF强度极低
        etf = self._safe_float(indicators, "etf_strength", 50)
        scores.append(self._score_low_is_better(etf, 20, 0))

        # 扩散度极低
        breadth = self._safe_float(indicators, "breadth", 50)
        scores.append(self._score_low_is_better(breadth, 20, 0))

        # 无龙头
        leader_count = self._safe_int(indicators, "leader_count", 5)
        scores.append(max(0.0, 100.0 - leader_count * 50.0))

        # 龙头强度极低
        leader = self._safe_float(indicators, "leader", 50)
        scores.append(self._score_low_is_better(leader, 20, 0))

        # 资金流枯竭
        flow = self._safe_float(indicators, "flow", 50)
        scores.append(self._score_low_is_better(flow, 15, 0))

        # 共振消失
        resonance = self._safe_float(indicators, "resonance", 50)
        scores.append(self._score_low_is_better(resonance, 15, 0))

        return self._safe_mean(scores)

    # ── 辅助方法 ────────────────────────────────────────────

    def _predict_next(self, current_stage: str, indicators: Dict[str, Any]) -> Optional[str]:
        """预测下一个阶段.

        根据当前阶段和指标判断趋势方向:
        - 如果所有指标都向上 → 正向推进
        - 如果指标全面恶化 → 可能跳级到 death
        """
        idx = _STAGE_ORDER_MAP.get(current_stage, -1)
        if idx < 0 or idx >= len(_STAGE_ORDER) - 1:
            return None

        # 计算综合趋势
        etf_change = self._safe_float(indicators, "etf_change", 0)
        breadth_change = self._safe_float(indicators, "breadth_change", 0)
        leader = self._safe_float(indicators, "leader", 50)
        resonance = self._safe_float(indicators, "resonance", 50)

        # 如果多项指标恶化 → 向死亡方向
        deterioration = 0
        if etf_change < -5:
            deterioration += 1
        if breadth_change < -5:
            deterioration += 1
        if leader < 20:
            deterioration += 1
        if resonance < 20:
            deterioration += 1

        if deterioration >= 3:
            # 全面恶化 → 可能跳级到 death
            return "death"
        elif deterioration >= 2 and idx < _STAGE_ORDER_MAP["death"]:
            return "distribution"

        # 正常推进: 返回顺序中的下一个非死亡阶段
        if current_stage == "main_trend":
            # 从 main_trend 可能进入 distribution
            return "distribution"
        elif current_stage == "expansion":
            return "main_trend"
        elif current_stage == "growth":
            return "expansion"
        elif current_stage == "birth":
            return "growth"

        return None

    def _calc_progress(self, stage: str, indicators: Dict[str, Any]) -> float:
        """计算阶段内进度 (0~1).

        当前指标值 / 阶段目标值，取平均。
        """
        target_map: Dict[str, List[Tuple[str, float]]] = {
            "birth":      [("etf_strength", 0.0)],
            "growth":     [("etf_strength", 45.0), ("breadth", 35.0), ("leader", 40.0)],
            "expansion":  [("etf_strength", 55.0), ("breadth", 60.0), ("leader", 60.0)],
            "main_trend": [("etf_strength", 80.0), ("breadth", 80.0), ("resonance", 75.0)],
            "distribution":[("etf_strength", 50.0), ("breadth", 30.0)],
            "death":      [("etf_strength", 0.0), ("breadth", 0.0)],
        }

        targets = target_map.get(stage, [])
        if not targets:
            return 0.0

        progresses: List[float] = []
        for key, target in targets:
            value = self._safe_float(indicators, key, 0)
            if target > 0:
                progresses.append(min(1.0, value / target))
            else:
                # target=0 的情况：值越低进度越高
                progresses.append(max(0.0, 1.0 - value / 30.0))

        return self._safe_mean(progresses) if progresses else 0.0

    def _load_stage_config(self) -> None:
        """从 weights.yaml 加载阶段配置."""
        try:
            cfg = load_weights()
            self._stage_cfg = cfg.get("stage", {})
        except Exception as e:
            logger.warning("加载阶段配置失败: %s", e)
            self._stage_cfg = {}

    # ── 评分工具函数 ────────────────────────────────────────

    @staticmethod
    def _score_higher_is_better(value: float, good: float, great: float, max_val: float) -> float:
        """值越高分越高.

        Args:
            value: 当前值
            good: 达到此值算及格 (60分)
            great: 达到此值算优秀 (90分)
            max_val: 满分值 (100分)

        Returns:
            0~100 的评分
        """
        if value <= good:
            return max(0.0, value / good * 60.0) if good > 0 else 0.0
        elif value <= great:
            return 60.0 + (value - good) / (great - good) * 30.0
        else:
            return min(100.0, 90.0 + (value - great) / (max_val - great) * 10.0)

    @staticmethod
    def _score_low_is_better(value: float, high: float, low: float) -> float:
        """值越低分越高.

        Args:
            value: 当前值
            high: 达到此值算及格 (60分)
            low: 达到此值算优秀 (90分)

        Returns:
            0~100 的评分
        """
        if value >= high:
            return max(0.0, 60.0 - (value - high) / (100 - high) * 60.0)
        elif value >= low:
            return 60.0 + (high - value) / (high - low) * 30.0
        else:
            return min(100.0, 90.0 + (low - value) / low * 10.0)

    @staticmethod
    def _score_range(value: float, low: float, high: float) -> float:
        """值在 [low, high] 范围内得分最高，偏离则降分.

        使用高斯衰减，low~high 范围内给 90~100 分。
        """
        mid = (low + high) / 2.0
        half_range = (high - low) / 2.0
        if half_range <= 0:
            return 50.0
        # 在范围内的得分
        if low <= value <= high:
            return 100.0 - abs(value - mid) / half_range * 10.0
        # 范围外，高斯衰减
        distance = abs(value - mid) - half_range
        return max(0.0, 90.0 * math.exp(-0.5 * (distance / half_range) ** 2))

    @staticmethod
    def _safe_float(d: Dict[str, Any], key: str, default: float = 0.0) -> float:
        """安全获取浮点值."""
        v = d.get(key, default)
        if isinstance(v, (int, float)):
            return float(v)
        return default

    @staticmethod
    def _safe_int(d: Dict[str, Any], key: str, default: int = 0) -> int:
        """安全获取整数值."""
        v = d.get(key, default)
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        return default

    @staticmethod
    def _safe_mean(values: List[float]) -> float:
        """安全计算平均值."""
        if not values:
            return 0.0
        return sum(values) / len(values)
