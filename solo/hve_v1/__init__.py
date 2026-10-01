# -*- coding: utf-8 -*-
"""HVE V1（High Volume Event）高量事件驱动策略模块

设计原则（任务书 §54）：
  - HVE 是事件，不是评分
  - HVE 是起点，不是买点（HVE 当日永不产生 BUY）
  - BUY 只发生在 Re-expansion（HVE_BULL）或 Breakout（HVE_2ND）
  - 缩量只是状态，不等于卖压下降（统一称 Volume Contraction）
  - 结构破坏直接 FAIL

数据源：仅复用现有 Tushare 缓存 D:\\mystock\\cache_daily\\stock_data.db（§二/§三）
不修改任何既有模块（§一）
"""
import json
import os

__version__ = '1.0.0'
STRATEGY_ID = 'hve_v1'

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hve_config.json')


def load_config(path: str = CONFIG_PATH) -> dict:
    """读取集中配置（§41）。全部参数均为 V1_INITIAL_HYPOTHESIS。"""
    with open(path, encoding='utf-8') as f:
        return json.load(f)
