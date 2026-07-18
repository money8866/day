"""
Theme Persistence Score Engine
主题持续性评分引擎 — 预测主题未来20-60日能否持续跑赢市场

6模块架构:
  Trend Stability      25%  趋势稳定性
  Breadth Expansion    25%  广度扩张
  Leader Persistence   20%  龙头持续性
  Capital Consistency  15%  资金一致性
  Catalyst Duration    15%  催化剂持续
  Crowding Penalty     -    拥挤度惩罚
"""
from .persistence_engine import ThemePersistenceEngine

__all__ = ["ThemePersistenceEngine"]
