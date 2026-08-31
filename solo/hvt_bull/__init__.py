# -*- coding: utf-8 -*-
"""HVT-BULL V1.0 历史天量换手牛股识别 + 二次启动预测引擎

结构：
  天量换手(HVT) -> 价格强度 -> 趋势/涨幅结构 -> 缩量锁筹 -> 二次放量突破 -> PRIMARY_BUY

数据源：
  - stk_factor_pro (SQLite, 2025起全市场含 turnover_rate/amount/total_mv)
  - daily_cache    (SQLite, 2021起全市场 OHLCV/amount)
  - fin_ind_2026H1_full.parquet (基本面)
  - theme_stock_map (板块/主题共振)
  - moneyflow parquet (可选)
"""

from .engine import HvtBullEngine
from .daily import run_daily
from .backtest import run_backtest

__all__ = ['HvtBullEngine', 'run_daily', 'run_backtest']
