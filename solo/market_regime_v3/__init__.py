# Market Regime Engine V3
# 机构级市场状态判断、热度评估、风格轮动、仓位管理系统

from .main import MarketRegimeV3
from .engines.market_score import MarketScoreResult
from .engines.state_machine import MarketRegime
from .engines.heat_engine import HeatResult

__version__ = "3.0.0"
