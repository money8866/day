# -*- coding: utf-8 -*-
"""
交易风格引擎 - Trading Style Engine V3
根据市场状态(Regime)和热度(Heat Level)确定当前推荐交易风格。
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# 热度等级映射：HeatResult 输出带空格的等级 → config matrix 中的带下划线的 key
HEAT_LEVEL_MAP = {
    "Extreme Hot": "Extreme_Hot",
    "Very Hot": "Very_Hot",
    "Hot": "Hot",
    "Warm": "Warm",
    "Normal": "Normal",
    "Cool": "Cool",
    "Cold": "Cold",
    "Ice": "Ice",
}

# 默认兜底
_DEFAULT_REGIME = "Bull"
_DEFAULT_HEAT = "Normal"


@dataclass
class TradingStyleResult:
    """交易风格推荐结果"""
    style_name: str  # e.g. "pullback_buy"
    style_label: str  # e.g. "回调低吸"
    style_description: str  # e.g. "震荡/弱势市场低吸龙头"
    regime_name: str
    heat_level: str  # e.g. "Cold", "Hot", etc.
    explain: Dict[str, str] = field(default_factory=dict)


class TradingStyleEngine:
    """交易风格引擎

    根据市场状态(Regime)和热度等级(Heat Level)，通过配置矩阵
    查找当前推荐的交易风格。
    """

    def __init__(self, config: dict):
        """
        Args:
            config: 完整配置字典，读取 config['trading_style']
        """
        self.cfg = config['trading_style']

    def evaluate(self, regime_name: str, heat_level: str) -> TradingStyleResult:
        """根据市场状态和热度等级，评估推荐交易风格

        Args:
            regime_name: 市场状态名称，如 "Bull", "Bear" 等
            heat_level: 热度等级，如 "Extreme Hot", "Cold" 等（与 HeatResult.level 一致）

        Returns:
            TradingStyleResult
        """
        # 1. 热度等级映射（去除空格，匹配 matrix 中的 key）
        mapped_heat = HEAT_LEVEL_MAP.get(heat_level, heat_level)

        # 2. 从 matrix 中查找风格名称
        matrix = self.cfg.get('matrix', {})
        style_name = None

        # 先按传入的 regime_name 查找
        regime_matrix = matrix.get(regime_name)
        if regime_matrix is not None:
            style_name = regime_matrix.get(mapped_heat)

        # 如果没找到，使用默认兜底
        if style_name is None:
            fallback_regime = matrix.get(_DEFAULT_REGIME, {})
            style_name = fallback_regime.get(_DEFAULT_HEAT, "swing_trade")

        # 3. 查找风格定义
        style_def = None
        styles = self.cfg.get('styles', [])
        for s in styles:
            if s.get('name') == style_name:
                style_def = s
                break

        if style_def is None:
            # 兜底：返回一个安全的中性风格
            style_def = {"name": "swing_trade", "label": "波段操作", "description": "中性市场高抛低吸"}

        # 4. 构造 explain 说明
        explain = {
            "regime": f"市场状态: {regime_name}",
            "heat_level": f"热度等级: {heat_level}",
            "mapped_heat": f"矩阵映射: {mapped_heat}",
            "style_found": f"匹配风格: {style_name}",
        }

        return TradingStyleResult(
            style_name=style_def['name'],
            style_label=style_def['label'],
            style_description=style_def['description'],
            regime_name=regime_name,
            heat_level=heat_level,
            explain=explain,
        )
