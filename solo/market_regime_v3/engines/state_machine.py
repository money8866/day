"""State Machine - 市场状态机

基于市场综合评分（0-100），使用模糊边界方法进行市场状态分类。
支持情绪修正覆盖、置信度计算和标签分配。
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)


@dataclass
class MarketRegime:
    """市场状态结果"""
    primary: str                 # 主要状态：Bear / Recovery / Neutral / Bull / Euphoria
    description: str             # 中文描述
    tags: List[str]              # 标签列表，如 ["RiskOn", "Growth", "Technology"]
    score: float                 # 原始市场评分
    confidence: float            # 置信度 0-1，分类的确定程度


class StateMachine:
    """市场状态机

    使用模糊边界方法进行市场状态分类：
    1. 根据市场评分确定初始状态区间
    2. 根据情绪得分进行覆盖修正（极高情绪→亢奋，极低情绪→熊市）
    3. 计算分类置信度（距区间中心越近，置信度越高）
    4. 分配状态标签
    """

    def __init__(self, config: dict):
        """初始化

        Args:
            config: 完整配置字典（从 yaml 加载后的 dict）
        """
        self.cfg = config.get('state_machine', {})
        self.states = self.cfg.get('states', [])
        self.sentiment_override_cfg = self.cfg.get('sentiment_override', {})

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def classify(self,
                 market_score: float,
                 sentiment_score: float,
                 style_dominant: str = "",
                 style_scores: Dict[str, float] = None) -> MarketRegime:
        """对当前市场评分进行分类

        Args:
            market_score:     市场综合评分 0-100
            sentiment_score:  情绪得分 0-1（外部归一化后的值）
            style_dominant:   主导风格名称（如 "Technology"）
            style_scores:     各风格得分字典（预留，暂未使用）

        Returns:
            MarketRegime
        """
        if style_scores is None:
            style_scores = {}

        # 1. 确定初始状态区间
        state, _ = self._find_state(market_score)

        # 2. 情绪修正覆盖
        adjusted_score = market_score

        sentiment_high = self.sentiment_override_cfg.get('sentiment_high_threshold', 0.70)
        sentiment_low = self.sentiment_override_cfg.get('sentiment_low_threshold', 0.25)
        euphoria_push = self.sentiment_override_cfg.get('euphoria_push', 8)
        bear_pull = self.sentiment_override_cfg.get('bear_pull', -8)

        # 极高情绪 + 当前区间在 Bull 及以上 → 推向 Euphoria
        state_name = state['name']
        if sentiment_score > sentiment_high and state_name in ('Bull', 'Euphoria'):
            adjusted_score = market_score + euphoria_push

        # 极低情绪 + 当前区间在 Neutral 及以下 → 拉向 Bear
        if sentiment_score < sentiment_low and state_name in ('Neutral', 'Recovery', 'Bear'):
            adjusted_score = market_score + bear_pull

        # 使用修正后的分数重新确定最终状态
        final_state, final_range = self._find_state(adjusted_score)

        # 3. 计算置信度
        confidence = self._calc_confidence(adjusted_score, final_range)

        # 4. 分配标签
        tags = self._build_tags(final_state, style_dominant, sentiment_score)

        return MarketRegime(
            primary=final_state['name'],
            description=final_state.get('description', ''),
            tags=tags,
            score=market_score,
            confidence=round(confidence, 4),
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_state(self, score: float) -> tuple:
        """根据评分找到所属状态区间

        从高分到低分遍历（Euphoria → Bear），
        第一个 score >= range[0] 的区间即为匹配结果。

        Args:
            score: 市场评分 0-100

        Returns:
            (状态 dict, 区间 [low, high])
        """
        # 按 score_range[0] 降序排列（高分状态优先匹配）
        sorted_states = sorted(self.states,
                               key=lambda s: s['score_range'][0],
                               reverse=True)

        for st in sorted_states:
            r = st['score_range']
            if score >= r[0]:
                return st, r

        # 兜底：返回第一个状态（Bear）
        fallback = self.states[0]
        return fallback, fallback['score_range']

    def _calc_confidence(self, score: float, score_range: list) -> float:
        """计算分类置信度

        基于评分在区间内的位置：
        - 越靠近区间中心 → 置信度越高（趋近 1.0）
        - 越靠近区间边界 → 置信度越低（最低 0.5）

        Args:
            score:      评分（可能是修正后的）
            score_range: 区间 [low, high]

        Returns:
            置信度 0-1
        """
        low, high = score_range[0], score_range[1]
        zone_width = high - low
        if zone_width <= 0:
            return 0.5

        midpoint = (low + high) / 2.0
        distance_from_midpoint = abs(score - midpoint) / zone_width
        confidence = 1.0 - min(0.5, distance_from_midpoint)
        return max(0.0, min(1.0, confidence))

    def _build_tags(self,
                    state: dict,
                    style_dominant: str,
                    sentiment_score: float) -> List[str]:
        """构建状态标签列表

        标签来源：
        1. 状态配置中自带的 tags
        2. 主导风格名称
        3. 情绪阈值触发的 RiskOn / RiskOff

        Args:
            state:           状态 dict
            style_dominant:  主导风格
            sentiment_score: 情绪得分 0-1

        Returns:
            标签列表（已去重）
        """
        tags = list(state.get('tags', []))

        # 添加主导风格标签
        if style_dominant and style_dominant not in tags:
            tags.append(style_dominant)

        # 情绪触发标签
        if sentiment_score > 0.70 and 'RiskOn' not in tags:
            tags.append('RiskOn')
        if sentiment_score < 0.30 and 'RiskOff' not in tags:
            tags.append('RiskOff')

        return tags
