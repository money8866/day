# -*- coding: utf-8 -*-
"""
REVERSAL-IMPULSE-BASE-100 V1.0
A股长期下跌反转后二波启动选股引擎

核心形态：
  长期下跌 → 第一波反转拉升 → 高位强势整理(POST_IMPULSE_BASE)
  → 突破第一波高点 → 第一次健康回踩 → 二波启动

目标周期：3~5 个交易日
"""
from .config import RIB_CONFIG
from .state_machine import StateMachine, RIBState
from .engine import RIBEngine
from .scoring import FinalScorer
from .detectors import (
    DowntrendDetector,
    ImpulseDetector,
    ImpulsePeakDetector,
    PostImpulseBaseDetector,
    PreBreakoutDetector,
    SecondLegBreakoutDetector,
    FirstPullbackDetector,
    ReAccelerationDetector,
)
from .filters import MarketFilter, ThemeFilter, RiskRewardEngine

__all__ = [
    "RIB_CONFIG",
    "StateMachine",
    "RIBState",
    "RIBEngine",
    "FinalScorer",
    "DowntrendDetector",
    "ImpulseDetector",
    "ImpulsePeakDetector",
    "PostImpulseBaseDetector",
    "PreBreakoutDetector",
    "SecondLegBreakoutDetector",
    "FirstPullbackDetector",
    "ReAccelerationDetector",
    "MarketFilter",
    "ThemeFilter",
    "RiskRewardEngine",
]
