# -*- coding: utf-8 -*-
"""回测执行约束测试：T+1、涨跌停、停牌顺延、成本会计、单股一仓、期末平仓。

成本口径（修复后）：
  成交价 = T+1 开盘 × (1 ± 滑点)          —— 滑点只在价格上建模一次
  佣金   = 成交额 × 往返成本/2            —— 单边 = 15bp
  全成本往返 = 30bp 佣金 + 10bp 滑点 = 40bp
"""
import numpy as np
import pandas as pd
import pytest

from msw import load_config
from msw.backtest import simulate, set_trade_calendar, _close_round, equity_metrics
from msw.portfolio_engine import board_limit_pct

CODE = '000001.SZ'
DATES = pd.bdate_range('2024-01-02', periods=8).strftime('%Y%m%d').tolist()


class SimPanel:
    """simulate 所需的最小面板替身"""

    def __init__(self, dates, codes, close_df):
        self.dates = list(dates)
        self.codes = list(codes)
        self.close = close_df

    def breadth(self, date):
        return 1.0


def _panel(close_by_code=None):
    close_by_code = close_by_code or {CODE: np.full(len(DATES), 10.0)}
    df = pd.DataFrame({c: close_by_code[c] for c in close_by_code},
                      index=DATES)
    return SimPanel(DATES, list(close_by_code.keys()), df)


def _bars(dates, open_px, open_raw=None, pre_close=None, close=None,
          atr_pct=None, name='测试股', industry='测试'):
    n = len(dates)
    return {
        'dates': np.array(dates),
        'open': np.array(open_px, dtype=float),
        'open_raw': np.array(open_raw if open_raw is not None else open_px,
                             dtype=float),
        'close': np.array(close if close is not None else open_px, dtype=float),
        'pre_close': np.array(pre_close if pre_close is not None
                              else [open_px[0]] + list(open_px[:-1]), dtype=float),
        'atr_pct': np.array(atr_pct if atr_pct is not None
                            else np.full(n, 0.03), dtype=float),
        'name': name, 'industry': industry,
    }


def _ev(kind, signal_date, **kw):
    e = {'type': kind, 'signal_date': signal_date}
    e.update(kw)
    return e


def _run(cfg, events, bars, panel, meta=None, **kw):
    set_trade_calendar(DATES)
    return simulate(cfg, {CODE: events}, bars, panel,
                    DATES[0], DATES[-1],
                    meta=meta or {CODE: {'name': '测试股', 'industry': '测试'}},
                    **kw)


# ---------------------------------------------------------------- T+1

def test_t_plus_1_next_open_fill(cfg):
    """信号日 T 收盘确认，T+1 开盘成交；买入当日不可卖出"""
    bars = {CODE: _bars(DATES, [10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0),
           _ev('EXIT', DATES[2], frac=1.0, reason='tp2', rule='C1',
              entry_price=None, entry_date='')]
    res = _run(cfg, evs, bars, _panel())
    assert len(res['trades']) == 1
    t = res['trades'][0]
    slip = cfg['backtest']['slippage_bp'] / 1e4
    assert t['entry_date'] == DATES[1]                    # T+1
    assert t['entry_price'] == pytest.approx(10.0 * (1 + slip))
    assert t['exit_date'] == DATES[3]                     # 卖出信号 T+2 → T+3
    assert t['exit_reason'] == 'tp2'


def test_no_same_day_sell_after_buy(cfg):
    """T+1：买入成交当日（D1）的卖出信号只能次日（D2）成交"""
    bars = {CODE: _bars(DATES, [10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0),
           _ev('EXIT', DATES[1], frac=1.0, reason='x', rule='C1',
              entry_price=None, entry_date='')]
    res = _run(cfg, evs, bars, _panel())
    assert len(res['trades']) == 1
    assert res['trades'][0]['exit_date'] == DATES[2]


def test_exit_signal_before_entry_ignored(cfg):
    bars = {CODE: _bars(DATES, [10.0] * len(DATES))}
    evs = [_ev('EXIT', DATES[0], frac=1.0, reason='x', rule='C1',
               entry_price=None, entry_date=''),
           _ev('ENTRY', DATES[1], rule='C1', quality=60.0, rr=2.0)]
    res = _run(cfg, evs, bars, _panel())
    assert len(res['trades']) == 1                       # 仅期末强平一轮
    assert res['trades'][0]['exit_reason'] == 'end_of_backtest'


# ---------------------------------------------------------------- 涨跌停

def test_limit_up_open_no_buy(cfg):
    """涨停开盘不可买（主板 10%，原始价与 pre_close 同基准）"""
    op = [10.0] * len(DATES)
    raw = [10.0] * len(DATES)
    raw[1] = 11.0                                        # D1 涨停开盘
    bars = {CODE: _bars(DATES, op, open_raw=raw, pre_close=[10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0)]
    res = _run(cfg, evs, bars, _panel())
    assert not res['trades']
    assert any(u['reason'] == 'limit_up_open' for u in res['unfilled'])


def test_limit_down_open_no_sell(cfg):
    """跌停开盘不可卖，顺延至可卖日"""
    op = [10.0] * len(DATES)
    raw = [10.0] * len(DATES)
    raw[3] = 9.0                                         # D3 跌停开盘
    bars = {CODE: _bars(DATES, op, open_raw=raw, pre_close=[10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0),
           _ev('EXIT', DATES[2], frac=1.0, reason='x', rule='C1',
              entry_price=None, entry_date='')]
    res = _run(cfg, evs, bars, _panel())
    assert len(res['trades']) == 1
    assert res['trades'][0]['exit_date'] == DATES[4]     # 跳过 D3 跌停


# ---------------------------------------------------------------- 停牌

def test_suspension_postpones_fill(cfg):
    """停牌（当日无 K 线）→ 挂单顺延至恢复当日"""
    dates = [d for i, d in enumerate(DATES) if i != 1]   # D1 停牌
    bars = {CODE: _bars(dates, [10.0] * len(dates))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0)]
    res = _run(cfg, evs, bars, _panel())
    assert len(res['trades']) == 1
    assert res['trades'][0]['entry_date'] == DATES[2]    # 顺延到 D2


def test_suspension_too_long_unfilled(cfg):
    """停牌超时（>20 日）按未成交处理"""
    dates = DATES[:1] + DATES[25:]                       # D1..D24 停牌
    dates = DATES[:1] + [d for d in pd.bdate_range('2024-02-01', periods=30)
                         .strftime('%Y%m%d')]
    panel_dates = DATES[:1] + list(pd.bdate_range('2024-01-03', periods=31)
                                   .strftime('%Y%m%d'))
    bars = {CODE: _bars(dates, [10.0] * len(dates))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0)]
    set_trade_calendar(panel_dates)
    res = simulate(cfg, {CODE: evs}, bars,
                   SimPanel(panel_dates, [CODE],
                            pd.DataFrame({CODE: np.full(len(panel_dates), 10.0)},
                                         index=panel_dates)),
                   panel_dates[0], panel_dates[-1],
                   meta={CODE: {'name': '测试股', 'industry': '测试'}})
    assert not res['trades']
    assert any(u['reason'] == 'suspended_too_long' for u in res['unfilled'])


# ---------------------------------------------------------------- 成本会计

def test_round_trip_cost_accounting(cfg):
    """全成本口径：同价买卖 → 净收益 ≈ -(30bp 佣金 + 2×5bp 滑点) = -40bp"""
    bars = {CODE: _bars(DATES, [10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0),
           _ev('EXIT', DATES[2], frac=1.0, reason='x', rule='C1',
              entry_price=None, entry_date='')]
    res = _run(cfg, evs, bars, _panel())
    t = res['trades'][0]
    slip = cfg['backtest']['slippage_bp'] / 1e4
    commission = cfg['backtest']['cost_round_trip_bp'] / 1e4
    expected = (1 - slip) * (1 - commission / 2) / (
        (1 + slip) * (1 + commission / 2)) - 1.0
    # trades 记录保留 6 位小数，容差相应放宽
    assert t['net_return'] == pytest.approx(expected, abs=1e-5)
    assert t['net_return'] == pytest.approx(-0.004, abs=2e-4)   # ≈ -40bp
    # 毛收益 = 理论价收益（同价 → 0）
    assert t['gross_return'] == pytest.approx(0.0, abs=1e-6)
    # 成本占比 ≈ 30bp / 1.0015
    assert t['cost_ratio'] == pytest.approx(commission / (1 + commission / 2),
                                            rel=2e-3)


def test_cost_rate_excludes_slippage(cfg):
    """_cost_rate 只含佣金（滑点已在成交价建模），单边 = 15bp"""
    from msw.portfolio_engine import Portfolio
    pf = Portfolio(cfg, initial_cash=1e6)
    assert pf._cost_rate() == pytest.approx(
        cfg['backtest']['cost_round_trip_bp'] / 2.0 / 1e4)


def test_close_round_cost_consistency():
    """_close_round 与组合现金流一致：net = 现金流入/现金流出 - 1"""
    rnd = {'ts_code': CODE, 'name': 'x', 'industry': 'y', 'rule': 'C1',
           'quality': 60.0, 'rr': 2.0,
           'shares': 1000.0, 'entry_price': 10.05, 'entry_theo': 10.0,
           'entry_cost': 150.0, 'entry_date': DATES[1],
           'exits': [{'date': DATES[3], 'price': 9.95, 'theo_price': 10.0,
                      'frac': 1.0, 'reason': 'x', 'cost': 150.0}],
           'high': 10.05, 'low': 10.05}
    trades = []
    set_trade_calendar(DATES)
    _close_round(rnd, trades)
    t = trades[0]
    entry_out = 1000.0 * 10.05 + 150.0
    cash_in = 1000.0 * 9.95 - 150.0
    assert t['net_return'] == pytest.approx(cash_in / entry_out - 1.0, abs=1e-5)
    assert t['gross_return'] == pytest.approx(0.0, abs=1e-6)   # theo 同价
    assert t['total_cost'] == pytest.approx(300.0)
    assert t['cost_ratio'] == pytest.approx(300.0 / entry_out, abs=1e-5)


def test_partial_exit_settlement():
    """分批止盈按剩余份额顺序结算（REDUCE 50% + EXIT 全部剩余）"""
    rnd = {'ts_code': CODE, 'name': 'x', 'industry': 'y', 'rule': 'C1',
           'quality': 60.0, 'rr': 2.0,
           'shares': 1000.0, 'entry_price': 10.0, 'entry_theo': 10.0,
           'entry_cost': 150.0, 'entry_date': DATES[1],
           'exits': [{'date': DATES[2], 'price': 10.5, 'theo_price': 10.5,
                      'frac': 0.5, 'reason': 'tp1', 'cost': 78.75},
                     {'date': DATES[3], 'price': 11.0, 'theo_price': 11.0,
                      'frac': 1.0, 'reason': 'tp2', 'cost': 82.5}],
           'high': 11.0, 'low': 10.0}
    trades = []
    set_trade_calendar(DATES)
    _close_round(rnd, trades)
    t = trades[0]
    assert t['n_exits'] == 2
    proceeds = 500.0 * 10.5 + 500.0 * 11.0
    entry_out = 1000.0 * 10.0 + 150.0
    assert t['net_return'] == pytest.approx(
        (proceeds - 78.75 - 82.5) / entry_out - 1.0, abs=1e-5)
    assert t['gross_return'] == pytest.approx(proceeds / 10_000.0 - 1.0, abs=1e-6)
    assert t['exit_date'] == DATES[3]


# ---------------------------------------------------------------- 仓位/去重

def test_single_position_per_stock(cfg):
    """单股一仓：持仓期间的重复 ENTRY 被忽略"""
    bars = {CODE: _bars(DATES, [10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0),
           _ev('ENTRY', DATES[1], rule='C1', quality=70.0, rr=2.0),
           _ev('ENTRY', DATES[2], rule='C3', quality=65.0, rr=2.0)]
    res = _run(cfg, evs, bars, _panel())
    assert len(res['trades']) == 1
    assert res['trades'][0]['rule'] == 'C1'              # 首信号优先


def test_end_of_backtest_force_exit(cfg):
    """期末强制平仓， reason=end_of_backtest"""
    bars = {CODE: _bars(DATES, [10.0] * len(DATES), close=[12.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0)]
    res = _run(cfg, evs, bars, _panel({CODE: np.full(len(DATES), 12.0)}))
    assert len(res['trades']) == 1
    t = res['trades'][0]
    assert t['exit_reason'] == 'end_of_backtest'
    assert t['exit_date'] == DATES[-1]


def test_equity_curve_marks_to_market(cfg):
    bars = {CODE: _bars(DATES, [10.0] * len(DATES))}
    evs = [_ev('ENTRY', DATES[0], rule='C1', quality=60.0, rr=2.0)]
    res = _run(cfg, evs, bars, _panel({CODE: np.full(len(DATES), 11.0)}))
    eq = res['equity']
    assert len(eq) == len(DATES)
    assert eq['n_positions'].iloc[0] == 0                 # 信号当日未成交
    assert eq['n_positions'].iloc[1] == 1                 # T+1 成交后持仓
    m = equity_metrics(eq)
    assert m['n_days'] == len(DATES)
    assert m['total_return'] > 0                          # 10 买 11 平，扣成本后仍为正
    assert 'max_drawdown' in m and m['max_drawdown'] <= 0


# ---------------------------------------------------------------- 板块阈值

def test_board_limit_pct():
    assert board_limit_pct('600000.SH') == 0.10
    assert board_limit_pct('000001.SZ') == 0.10
    assert board_limit_pct('002001.SZ') == 0.10
    assert board_limit_pct('300001.SZ') == 0.20
    assert board_limit_pct('688001.SH') == 0.20
    assert board_limit_pct('830001.BJ') == 0.30
    assert board_limit_pct('000002.SZ', 'ST测试') == 0.05
    # 双创 ST 仍为 20%（创业板/科创板 ST 不降板），与实盘一致
    assert board_limit_pct('300002.SZ', '*ST测试') == 0.20
    assert board_limit_pct('688002.SH', 'ST 测试') == 0.20
