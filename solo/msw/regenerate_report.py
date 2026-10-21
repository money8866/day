# -*- coding: utf-8 -*-
"""用行走缓存 + 既有汇总数据重新生成增强版验证报告（免重跑 3 小时全流程）。

输入（msw/out/）：
  walk_cache_full.pkl              全市场行走缓存（--cache-walk 产出）
  msw_run_summary.json             判定/基线/IS/OOS 汇总
  msw_ablation_results.csv         消融结果
  msw_parameter_stability.csv      参数扰动结果
  msw_cost_sensitivity.csv         成本敏感性
输出：重写 msw_validation_report.md（Q1/Q2/Q3 为数据驱动结论）

用法：python msw/regenerate_report.py
"""
import json
import os
import pickle
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msw import load_config
from msw.data_adapter import MswData
from msw.feature_engine import MarketPanel
from msw.backtest import (simulate, set_trade_calendar, equity_metrics,
                          round_metrics)
from msw import validation as V
from msw import report as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')

NOTES = [
    'ST 剔除基于当前名称（无 PIT 名称历史），回测早期 ST 样本无法按当时状态剔除',
    '财务分层使用 fina_indicator_cache 现有 8 字段近似，非完整财报',
    '行业相对强度用个股 industry 字段等权中位数代理',
    '消融/参数扰动在流动性前 1200 只子集上运行（全量成本过高），结论外推需谨慎',
    'ZigZag 阈值 = max(10%, 1.5×ATR%)；转折点必须事后确认才进入统计',
    'matched_control 为安慰剂计时设计：同一交易时点移植到行业+市值+流动性最接近的'
    '未选中对照股，成交价/涨跌停/停牌/ATR 仓位用对照股自身数据重模拟',
]


def main():
    cfg = load_config()
    start = cfg['backtest']['start_date']
    end = cfg['backtest']['end_date']
    is_start, is_end = cfg['backtest']['is_range']
    oos_start, oos_end = cfg['backtest']['oos_range']

    data = MswData()
    panel = MarketPanel(data, '2023-01-03', end, cfg)
    set_trade_calendar(panel.dates)
    print(f'面板就绪：{len(panel.codes)} 只 × {len(panel.dates)} 日')

    with open(os.path.join(OUT, 'walk_cache_full.pkl'), 'rb') as f:
        walk = pickle.load(f)['walk']
    print(f'行走缓存：{walk["n_walked"]} 只，{len(walk["events"])} 只产生事件')

    msw_res = simulate(cfg, walk['events'], walk['bars'], panel, start, end,
                       meta=walk['meta'])

    def _slice(a, b):
        r = simulate(cfg, walk['events'], walk['bars'], panel, a, b,
                     meta=walk['meta'])
        return {'equity_metrics': equity_metrics(r['equity']),
                'round_metrics': round_metrics(r['trades'], span_dates=[a, b])}

    is_res = _slice(is_start, is_end)
    oos_res = _slice(oos_start, oos_end)

    with open(os.path.join(OUT, 'msw_run_summary.json'), encoding='utf-8') as f:
        summary = json.load(f)
    baselines = {}
    for k, v in summary['baselines'].items():
        eq = v.get('equity') or {}
        if 'total_return' not in eq and 'total_return' in v:
            eq = {'total_return': v.get('total_return')}
        baselines[k] = {'equity_metrics': eq, 'round_metrics': v.get('rounds')}

    abl = pd.read_csv(os.path.join(OUT, 'msw_ablation_results.csv'))
    ablations = {}
    for _, r in abl.iterrows():
        ablations[str(r['ablation'])] = {
            'equity_metrics': {'total_return': _n(r['total_return']),
                               'max_drawdown': _n(r['max_drawdown']),
                               'sharpe': _n(r['sharpe'])},
            'round_metrics': {'n_rounds': _n(r['n_rounds']),
                              'net_mean': _n(r['net_mean']),
                              'win_rate': _n(r['win_rate']),
                              'profit_factor': _n(r['profit_factor'])},
        }
    stability = _clean(pd.read_csv(
        os.path.join(OUT, 'msw_parameter_stability.csv')).to_dict('records'))
    cost_sens = _clean(pd.read_csv(
        os.path.join(OUT, 'msw_cost_sensitivity.csv')).to_dict('records'))

    slices = V.regime_and_yearly_slices(cfg, walk, panel, start, end, res=msw_res)
    drop_top = V.drop_top_stocks(cfg, walk, panel, start, end,
                                 frac=cfg['validation']['drop_top_stocks_frac'])

    p = R.write_validation_report(OUT, msw_res, baselines, ablations, stability,
                                  cost_sens, drop_top, slices, cfg, start, end,
                                  summary['verdict'], summary['reasons'], NOTES,
                                  walk=walk, is_res=is_res, oos_res=oos_res)
    print('报告已生成:', p)
    print(f'判定: {summary["verdict"]}')


def _n(x):
    """NaN → None（pandas 读空单元格）"""
    try:
        if x is None or (isinstance(x, float) and x != x):
            return None
    except Exception:
        pass
    return x


def _clean(rows):
    return [{k: _n(v) for k, v in r.items()} for r in rows]


if __name__ == '__main__':
    main()
