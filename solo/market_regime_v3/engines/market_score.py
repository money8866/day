"""Market Score Engine - 市场综合评分引擎

将指数强度、市场宽度、情绪、主题共振、风险偏好五个维度的得分
按配置权重加权汇总，得到 0-100 的市场综合评分。
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List

import yaml

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)


@dataclass
class MarketScoreResult:
    """市场评分结果"""
    score: float                         # 综合评分 0-100
    index_strength_score: float          # 指数强度得分
    breadth_score: float                 # 市场宽度得分
    sentiment_score: float               # 情绪得分
    theme_resonance_score: float         # 主题共振得分
    risk_appetite_score: float           # 风险偏好得分
    contributions: Dict[str, float]      # 各组件加权贡献
    explain: Dict[str, str]             # 各组件解释文本


class MarketScoreEngine:
    """市场综合评分引擎

    接收五个维度的 0-100 评分，按配置权重加权汇总为综合评分。
    每个维度的权重从 config['market_score']['weights'] 读取。
    """

    def __init__(self, config: dict):
        """初始化

        Args:
            config: 完整配置字典（从 yaml 加载后的 dict）
        """
        self.cfg = config.get('market_score', {})
        self.weights = self.cfg.get('weights', {})

    def evaluate(self,
                 index_strength: float,
                 breadth: float,
                 sentiment: float,
                 theme_resonance: float,
                 risk_appetite: float) -> MarketScoreResult:
        """综合评估五个维度，计算市场综合评分

        Args:
            index_strength:  指数强度得分 0-100
            breadth:         市场宽度得分 0-100
            sentiment:       情绪得分 0-100
            theme_resonance: 主题共振得分 0-100
            risk_appetite:   风险偏好得分 0-100

        Returns:
            MarketScoreResult
        """
        # 获取各维度权重（带默认值）
        w_idx = self.weights.get('index_strength', 0.35)
        w_breadth = self.weights.get('breadth', 0.30)
        w_sentiment = self.weights.get('sentiment', 0.15)
        w_theme = self.weights.get('theme_resonance', 0.10)
        w_risk = self.weights.get('risk_appetite', 0.10)

        # 计算加权贡献
        contrib_idx = index_strength * w_idx
        contrib_breadth = breadth * w_breadth
        contrib_sentiment = sentiment * w_sentiment
        contrib_theme = theme_resonance * w_theme
        contrib_risk = risk_appetite * w_risk

        # 综合评分
        total_score = contrib_idx + contrib_breadth + contrib_sentiment + contrib_theme + contrib_risk

        # 构建贡献字典
        contributions = {
            'index_strength': contrib_idx,
            'breadth': contrib_breadth,
            'sentiment': contrib_sentiment,
            'theme_resonance': contrib_theme,
            'risk_appetite': contrib_risk,
        }

        # 构建解释文本
        explain = {
            'index_strength': f"指数强度得分 {index_strength:.1f}分 × 权重{w_idx:.2f} = 贡献{contrib_idx:.1f}分",
            'breadth': f"市场宽度得分 {breadth:.1f}分 × 权重{w_breadth:.2f} = 贡献{contrib_breadth:.1f}分",
            'sentiment': f"情绪得分 {sentiment:.1f}分 × 权重{w_sentiment:.2f} = 贡献{contrib_sentiment:.1f}分",
            'theme_resonance': f"主题共振得分 {theme_resonance:.1f}分 × 权重{w_theme:.2f} = 贡献{contrib_theme:.1f}分",
            'risk_appetite': f"风险偏好得分 {risk_appetite:.1f}分 × 权重{w_risk:.2f} = 贡献{contrib_risk:.1f}分",
        }

        return MarketScoreResult(
            score=round(total_score, 2),
            index_strength_score=index_strength,
            breadth_score=breadth,
            sentiment_score=sentiment,
            theme_resonance_score=theme_resonance,
            risk_appetite_score=risk_appetite,
            contributions=contributions,
            explain=explain,
        )
