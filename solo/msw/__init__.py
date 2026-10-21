# -*- coding: utf-8 -*-
"""MSW 1.0（Multi-Swing Wave）长期趋势下的多波段交易系统

设计原则（任务书 §三）：
  - MSW-A 筛选 / MSW-B 评分 / MSW-C 信号 / MSW-D 验证 四层分离，不融合成黑箱总分
  - 历史波段统计只用「已确认」转折点；实盘信号只用当时已知数据（无未来函数）
  - 仅复用现有 Tushare 缓存 D:\\mystock\\cache_daily\\stock_data.db，零网络、零写入
  - 不修改任何既有策略模块（隔离原则 §2.3）

数据源：D:\\mystock\\cache_daily\\stock_data.db（只读）
运行环境：Python 3.14.4 + pandas 3.0.2 + numpy 2.4.4（本机项目环境）
"""
import json
import os

__version__ = '1.0.0'
STRATEGY_ID = 'msw'

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'msw_config.json')
PREREG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiment_preregistration.json')


def load_config(path: str = CONFIG_PATH) -> dict:
    """读取集中配置。全部参数均为 V1_INITIAL_HYPOTHESIS（任务书 §41 禁止赛前调参）。"""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_prereg(path: str = PREREG_PATH) -> dict:
    """读取预注册实验设计（§9.1：看测试集结果前冻结）。"""
    with open(path, encoding='utf-8') as f:
        return json.load(f)
