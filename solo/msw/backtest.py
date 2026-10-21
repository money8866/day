# -*- coding: utf-8 -*-
"""MSW 回测引擎（任务书 §11 backtest.py）

两阶段架构：
  阶段一 walk_universe()  逐股行走（StockWalker）→ 事件流 + 日记录 + 基线信号
  阶段二 simulate()       事件驱动组合模拟（T+1 / 涨跌停 / 停牌 / 滑点 / 成本 / 仓位）

严格执行约束（§8.1）：
  - 信号日 T 收盘确认，T+1 起可成交，成交价 = 次日开盘价 ± 滑点
  - T+1：当日买入不可当日卖出
  - 涨停不可买、跌停不可卖（主板±10% / 双创±20% / ST±5% / 北交所±30%）
  - 停牌/无成交：挂起等待，超时按未成交处理并记录原因
  - 股票池按历史日期还原（保留退市股），不用今日名单覆盖历史
  - 理论价 / 模拟成交价 / 滑点 / 未成交原因全记录
"""
import copy
from bisect import bisect_left, bisect_right

import numpy as np
import pandas as pd

from msw.data_adapter import MswData, universe_mask
from msw.feature_engine import compute_features, MarketPanel
from msw.swing_analyzer import SwingHistory
from msw.signal_engine import StockWalker, RULE_PRIORITY
from msw.portfolio_engine import Portfolio, board_limit_pct

RULE_RANK = {r: i for i, r in enumerate(RULE_PRIORITY)}

_TD_SRC = []          # 全局交易日历（升序）


def set_trade_calendar(dates):
    global _TD_SRC
    _TD_SRC = sorted(str(d) for d in dates)


def _next_td(date: str) -> str:
    i = bisect_right(_TD_SRC, date)
    return _TD_SRC[i] if i < len(_TD_SRC) else date


# ================================================================ 阶段一：行走

def apply_cfg_overrides(cfg: dict, cfg_overrides: dict, logger=print) -> dict:
    """把 {节: {字段: 值}} 覆盖应用到配置的深拷贝上。

    节不存在时告警（历史上的坑：参数名与配置结构不匹配时覆盖被写进空节名，
    扰动静默失效，全部实验跑出同一结果）。
    """
    walk_cfg = copy.deepcopy(cfg)
    for k, v in (cfg_overrides or {}).items():
        if isinstance(v, dict) and isinstance(walk_cfg.get(k), dict):
            walk_cfg[k].update(v)
        else:
            if k not in walk_cfg:
                logger(f'[walk] 警告：cfg_overrides 节 {k!r} 不在配置中，'
                       f'该覆盖可能无效（键名与配置结构不匹配）')
            walk_cfg[k] = v
    return walk_cfg


def walk_universe(cfg: dict, data: MswData, panel: MarketPanel,
                  codes=None, compute_baselines=True, logger=print,
                  cfg_overrides=None, start_idx=250):
    """逐股加载 → 特征 → 波段 → 行走，汇总全部股票的事件与记录。

    返回 dict:
      events   : {ts_code: [event, ...]}   MSW 事件流
      records  : {ts_code: [record, ...]}   日记录（仅候选池/信号日，控制体积）
      bars     : {ts_code: {...}}           含事件股票的日期/开/收/前收/ATR%/行业
      ret120   : {ts_code: (dates, ret120)} 全股票（动量基线用）
      meta     : {ts_code: {'name','industry','list_date'}}
      baseline : {'ma_trend': {code: events}, 'meanrev': {code: events}}
      n_walked : int
    """
    walk_cfg = apply_cfg_overrides(cfg, cfg_overrides, logger=logger)

    names, lds, inds = data.basic_maps()
    if codes is None:
        codes = data.all_codes()
    out = {'events': {}, 'records': {}, 'bars': {}, 'ret120': {},
           'meta': {}, 'baseline': {'ma_trend': {}, 'meanrev': {}}, 'n_walked': 0}
    for i, code in enumerate(codes):
        try:
            df = data.load_stock(code, '2023-01-03', '2026-10-20')
            if df is None or len(df) < 300:
                continue
            name = names.get(code, code)
            list_date = lds.get(code, '')
            industry = inds.get(code, '未知')
            feats = compute_features(df, walk_cfg)
            um = universe_mask(feats, code, name, list_date, walk_cfg)
            if not um.any():
                continue
            sh = SwingHistory(feats, walk_cfg)
            walker = StockWalker(code, feats, sh, walk_cfg, panel=panel, name=name,
                                 industry=industry,
                                 fina_series=data.fundamentals_series(code),
                                 start_idx=start_idx, uni_mask=um)
            recs, evs = walker.run()
        except Exception as e:
            logger(f'[walk] {code} failed: {type(e).__name__} {e}')
            continue
        out['n_walked'] += 1
        out['meta'][code] = {'name': name, 'industry': industry, 'list_date': list_date}
        if evs:
            out['events'][code] = evs
            out['bars'][code] = {
                'dates': feats['trade_date'].astype(str).to_numpy(),
                'open': feats['hfq_open'].to_numpy(dtype=float),
                'open_raw': feats['open'].to_numpy(dtype=float),
                'close': feats['hfq_close'].to_numpy(dtype=float),
                'pre_close': feats['pre_close'].to_numpy(dtype=float),
                'atr_pct': feats['atr_pct'].to_numpy(dtype=float),
                'name': name, 'industry': industry,
            }
        # 日记录：仅保留候选池（趋势通过且质量分达标）或信号日（控制体积）
        keep = [r for r in recs
                if (r['trend_pass'] and r['msw_quality_score'] is not None
                    and r['msw_quality_score'] >= walk_cfg['quality_score']['min_score'])
                or r['current_signal'] != 'WATCH']
        if keep:
            out['records'][code] = keep
        out['ret120'][code] = (feats['trade_date'].astype(str).to_numpy(),
                               feats['ret_120d'].to_numpy(dtype=float))
        if compute_baselines:
            try:
                out['baseline']['ma_trend'][code] = _baseline_ma_trend(feats, walk_cfg)
                out['baseline']['meanrev'][code] = _baseline_meanrev(feats, walk_cfg)
            except Exception:
                pass
        if (i + 1) % 500 == 0:
            logger(f'[walk] {i + 1}/{len(codes)} done, event stocks={len(out["events"])}')
        # 内存控制：逐股处理，立即从数据层缓存中驱逐（全市场 5000+ 股不可全缓存）
        # 注意：缓存键必须与 load_stock 调用时传入的原始字符串一致（带横杠）
        data._stock_cache.pop((code, '2023-01-03', '2026-10-20'), None)
    return out


def load_bars_for_codes(data, codes, cfg, logger=print,
                        start='2023-01-03', end='2026-10-20') -> dict:
    """只加载指定股票的价格栏（不行走、不判池）——匹配对照安慰剂计时用。

    返回与 walk_universe['bars'] 同构的 {code: bars}（不含 name/industry）。
    """
    out = {}
    for i, code in enumerate(codes):
        try:
            df = data.load_stock(code, start, end)
            if df is None or len(df) < 300:
                continue
            feats = compute_features(df, cfg)
            out[code] = {
                'dates': feats['trade_date'].astype(str).to_numpy(),
                'open': feats['hfq_open'].to_numpy(dtype=float),
                'open_raw': feats['open'].to_numpy(dtype=float),
                'close': feats['hfq_close'].to_numpy(dtype=float),
                'pre_close': feats['pre_close'].to_numpy(dtype=float),
                'atr_pct': feats['atr_pct'].to_numpy(dtype=float),
            }
        except Exception as e:
            logger(f'[bars] {code} failed: {type(e).__name__} {e}')
        data._stock_cache.pop((code, start, end), None)
        if (i + 1) % 500 == 0:
            logger(f'[bars] {i + 1}/{len(codes)} loaded')
    return out


def _baseline_ma_trend(feats, cfg):
    """基线2：MA250 趋势（close > MA250 且斜率>0 买入，跌破退出）"""
    close = feats['hfq_close'].to_numpy(dtype=float)
    ma = feats['ma250'].to_numpy(dtype=float)
    slope = feats['ma250_slope20'].to_numpy(dtype=float)
    dates = feats['trade_date'].astype(str).to_numpy()
    events, holding = [], False
    for t in range(250, len(feats)):
        if not (np.isfinite(close[t]) and np.isfinite(ma[t])):
            continue
        if not holding and close[t] > ma[t] and np.isfinite(slope[t]) and slope[t] > 0:
            events.append({'type': 'ENTRY', 'signal_date': dates[t], 'rule': 'MA_TREND',
                           'quality': None, 'rr': None, 'detail': {}})
            holding = True
        elif holding and close[t] < ma[t]:
            events.append({'type': 'EXIT', 'signal_date': dates[t], 'reason': 'below_ma250',
                           'frac': 1.0, 'rule': 'MA_TREND', 'entry_price': None,
                           'entry_date': ''})
            holding = False
    return events


def _baseline_meanrev(feats, cfg):
    """基线4：单纯均值回归（RSI6<=30 买，+5% 或 10 日或 RSI6>=60 卖；无趋势过滤）"""
    close = feats['hfq_close'].to_numpy(dtype=float)
    rsi = feats['rsi6'].to_numpy(dtype=float)
    dates = feats['trade_date'].astype(str).to_numpy()
    events, entry_bar = [], None
    for t in range(250, len(feats)):
        if entry_bar is None:
            if np.isfinite(rsi[t]) and rsi[t] <= 30:
                events.append({'type': 'ENTRY', 'signal_date': dates[t], 'rule': 'MEANREV',
                               'quality': None, 'rr': None, 'detail': {}})
                entry_bar = t
        else:
            if (np.isfinite(close[t]) and np.isfinite(close[entry_bar])
                    and close[t] >= close[entry_bar] * 1.05) or t - entry_bar >= 10 \
                    or (np.isfinite(rsi[t]) and rsi[t] >= 60):
                events.append({'type': 'EXIT', 'signal_date': dates[t],
                               'reason': 'meanrev_exit', 'frac': 1.0, 'rule': 'MEANREV',
                               'entry_price': None, 'entry_date': ''})
                entry_bar = None
    return events


# ================================================================ 阶段二：模拟

def simulate(cfg: dict, events_by_code: dict, bars: dict, panel: MarketPanel,
             start_date: str, end_date: str, meta: dict = None,
             allowed_codes: set = None, use_portfolio_limits: bool = True,
             portfolio_cfg_over: dict = None, initial_cash: float = 1_000_000.0,
             force_exit_end: bool = True, entry_filter=None):
    """事件驱动组合模拟。

    events_by_code : {code: [event...]}（signal_date 为信号日 T）
    meta           : {code: {'name','industry'}}
    entry_filter   : 可选 fn(code, event) -> bool，额外入场过滤（匹配对照组用）
    返回 {'trades','equity','unfilled','portfolio'}
    """
    meta = meta or {}
    from msw.data_adapter import norm_date
    start_date, end_date = norm_date(start_date), norm_date(end_date)
    pcfg = dict(cfg.get('portfolio', {}))
    if portfolio_cfg_over:
        pcfg.update(portfolio_cfg_over)
    if not use_portfolio_limits:
        pcfg.update({'max_positions': 10 ** 9, 'max_single_frac': 1.0,
                     'max_sector_frac': 1.0})
    sim_cfg = dict(cfg)
    sim_cfg['portfolio'] = pcfg
    pf = Portfolio(sim_cfg, initial_cash=initial_cash)

    td = [d for d in panel.dates if start_date <= d <= end_date]
    close_mat = panel.close
    code_cols = {c: j for j, c in enumerate(panel.codes)}
    col_dates = list(close_mat.index)
    slip = float(cfg.get('backtest', {}).get('slippage_bp', 5.0)) / 10000.0

    # 事件按信号日分组
    ev_by_date = {}
    for code, evs in events_by_code.items():
        if code not in bars:
            continue
        if allowed_codes is not None and code not in allowed_codes:
            continue
        for e in evs:
            if e['type'] not in ('ENTRY', 'REDUCE', 'EXIT'):
                continue
            ev_by_date.setdefault(e['signal_date'], []).append((code, e))

    trades, open_rounds, unfilled, pending = [], {}, [], {}
    eq_rows = []

    def _dset(code):
        b = bars[code]
        if '_dset' not in b:
            b['_dset'] = set(b['dates'])
        return b['_dset']

    def _imap(code):
        b = bars[code]
        if '_imap' not in b:
            b['_imap'] = {d: i for i, d in enumerate(b['dates'])}
        return b['_imap']

    def open_px(code, date):
        i = _imap(code).get(date)
        return float(bars[code]['open'][i]) if i is not None else None

    def open_px_raw(code, date):
        """不复权开盘价（涨跌停判定必须用原始价，与 pre_close 同基准）"""
        i = _imap(code).get(date)
        return float(bars[code]['open_raw'][i]) if i is not None else None

    def prev_close_px(code, date):
        i = _imap(code).get(date)
        if i is None or i == 0:
            return None
        return float(bars[code]['pre_close'][i])

    def close_px(code, date):
        j = code_cols.get(code)
        if j is not None:
            k = bisect_left(col_dates, date)
            if k < len(col_dates) and col_dates[k] == date:
                v = close_mat.iat[k, j]
                if np.isfinite(v):
                    return float(v)
        i = _imap(code).get(date)
        return float(bars[code]['close'][i]) if i is not None else None

    for date in td:
        # ---------- 1) 到期卖单（REDUCE/EXIT） ----------
        for code in [c for c, p in pending.items()
                     if p['kind'] == 'sell' and p['due'] == date]:
            p = pending[code]
            if date not in _dset(code):
                p['waited'] += 1
                if p['waited'] > 40:
                    unfilled.append({'code': code, 'date': date, 'kind': 'sell',
                                     'reason': 'suspended_too_long',
                                     'event': p['event']['type']})
                    pending.pop(code, None)
                else:
                    p['due'] = _next_td(date)
                continue
            op_raw, pc = open_px_raw(code, date), prev_close_px(code, date)
            lim = board_limit_pct(code, meta.get(code, {}).get('name', ''))
            if op_raw is not None and pc and pc > 0 and op_raw <= pc * (1.0 - lim) + 1e-9:
                p['waited'] += 1                      # 跌停开盘：不可卖出，顺延
                p['due'] = _next_td(date)
                continue
            op = open_px(code, date)
            if op is None or not np.isfinite(op) or op <= 0:
                p['waited'] += 1
                p['due'] = _next_td(date)
                continue
            fill_px = op * (1.0 - slip)
            e = p['event']
            rec = pf.sell(code, fill_px, frac=e.get('frac', 1.0), date=date)
            pending.pop(code, None)
            if rec is None:
                continue
            rnd = open_rounds.get(code)
            if rnd is not None:
                rnd['exits'].append({'date': date, 'price': fill_px, 'theo_price': op,
                                     'frac': e.get('frac', 1.0),
                                     'reason': e.get('reason', ''), 'cost': rec['cost']})
                if e['type'] == 'EXIT' or code not in pf.positions:
                    _close_round(rnd, trades)
                    open_rounds.pop(code, None)

        # ---------- 2) 到期买单（ENTRY） ----------
        due_buys = [(c, p) for c, p in pending.items()
                    if p['kind'] == 'buy' and p['due'] == date]
        due_buys.sort(key=lambda cp: (
            -(cp[1]['event'].get('quality') or 0),
            RULE_RANK.get(cp[1]['event'].get('rule', ''), 9),
            cp[1]['event']['signal_date']))
        for code, p in due_buys:
            if date not in _dset(code):
                p['waited'] += 1
                if p['waited'] > 20:
                    unfilled.append({'code': code, 'date': date, 'kind': 'buy',
                                     'reason': 'suspended_too_long', 'event': 'ENTRY'})
                    pending.pop(code, None)
                else:
                    p['due'] = _next_td(date)
                continue
            op_raw, pc = open_px_raw(code, date), prev_close_px(code, date)
            lim = board_limit_pct(code, meta.get(code, {}).get('name', ''))
            if op_raw is not None and pc and pc > 0 and op_raw >= pc * (1.0 + lim) - 1e-9:
                unfilled.append({'code': code, 'date': date, 'kind': 'buy',
                                 'reason': 'limit_up_open', 'event': 'ENTRY'})
                pending.pop(code, None)
                continue
            op = open_px(code, date)
            if op is None or not np.isfinite(op) or op <= 0:
                p['waited'] += 1
                p['due'] = _next_td(date)
                continue
            industry = meta.get(code, {}).get('industry', '未知')
            w = pf.target_weight(p.get('atr_pct') or 0.03, industry, {})
            if w <= 0:
                p['waited'] += 1
                if p['waited'] > 5:
                    reason = ('no_slot' if len(pf.positions) >= pf.max_positions
                              else 'sector_cap')
                    unfilled.append({'code': code, 'date': date, 'kind': 'buy',
                                     'reason': reason, 'event': 'ENTRY'})
                    pending.pop(code, None)
                else:
                    p['due'] = _next_td(date)
                continue
            eq = pf.equity({})
            value = min(eq * w, pf.cash / (1.0 + pf._cost_rate()))
            fill_px = op * (1.0 + slip)
            rec = pf.buy(code, fill_px, value, industry, p.get('atr_pct') or 0.03, date)
            if rec is None:
                unfilled.append({'code': code, 'date': date, 'kind': 'buy',
                                 'reason': 'no_cash', 'event': 'ENTRY'})
                pending.pop(code, None)
                continue
            e = p['event']
            open_rounds[code] = {
                'ts_code': code, 'name': meta.get(code, {}).get('name', code),
                'industry': industry, 'rule': e.get('rule', ''),
                'quality': e.get('quality'), 'rr': e.get('rr'),
                'entry_date': date, 'entry_price': fill_px, 'entry_theo': op,
                'entry_cost': rec['cost'], 'shares': rec['shares'],
                'exits': [], 'high': fill_px, 'low': fill_px,
            }
            pending.pop(code, None)

        # ---------- 3) 今日新信号入队 ----------
        for code, e in ev_by_date.get(date, []):
            if e['type'] == 'ENTRY':
                if code in open_rounds:
                    continue                       # 单股一仓：忽略重复买入
                if any(p['kind'] == 'buy' for c, p in pending.items() if c == code):
                    continue                       # 已有挂单
                if entry_filter is not None and not entry_filter(code, e):
                    continue
                i = _imap(code).get(date)
                atr_pct = float(bars[code]['atr_pct'][i]) if i is not None else 0.03
                if not np.isfinite(atr_pct) or atr_pct <= 0:
                    atr_pct = 0.03
                pending[code] = {'event': e, 'due': _next_td(date), 'kind': 'buy',
                                 'waited': 0, 'atr_pct': atr_pct}
            else:
                rnd = open_rounds.get(code)
                if rnd is None:
                    continue                       # 无持仓的卖出信号：忽略
                due = _next_td(date)
                if rnd['entry_date'] >= due:
                    due = _next_td(rnd['entry_date'])   # T+1：买入当日不可卖出
                pending[code] = {'event': e, 'due': due, 'kind': 'sell', 'waited': 0}

        # ---------- 4) 盯市 ----------
        close_map = {}
        for code in pf.positions:
            px = close_px(code, date)
            if px is not None:
                close_map[code] = px
                rnd = open_rounds.get(code)
                if rnd is not None:
                    rnd['high'] = max(rnd['high'], px)
                    rnd['low'] = min(rnd['low'], px)
        eq = pf.equity(close_map)
        eq_rows.append({'trade_date': date, 'cash': pf.cash,
                        'invested': eq - pf.cash, 'equity': eq,
                        'n_positions': len(pf.positions)})

    # ---------- 期末强制平仓 ----------
    if force_exit_end and pf.positions:
        last = td[-1]
        for code in list(pf.positions.keys()):
            px = close_px(code, last)
            if px is None:
                continue
            pf.sell(code, px * (1.0 - slip), frac=1.0, date=last)
            rnd = open_rounds.get(code)
            if rnd is not None:
                rnd['exits'].append({'date': last, 'price': px * (1.0 - slip),
                                     'theo_price': px, 'frac': 1.0,
                                     'reason': 'end_of_backtest', 'cost': 0.0})
                _close_round(rnd, trades)
                open_rounds.pop(code, None)

    equity = pd.DataFrame(eq_rows)
    if not equity.empty:
        equity['peak'] = equity['equity'].cummax()
        equity['drawdown'] = equity['equity'] / equity['peak'] - 1.0
    return {'trades': trades, 'equity': equity, 'unfilled': unfilled, 'portfolio': pf}


def _close_round(rnd, trades):
    """结算一轮完整交易（含分批止盈；股数按剩余份额顺序结算）

    成本会计口径（与组合权益曲线严格一致，均为现金口径）：
      entry_out  = 入场成交额 + 入场佣金      （实际现金流出）
      cash_in    = 出场成交额 - 出场佣金      （实际现金流入）
      net_return = cash_in / entry_out - 1   全成本净收益（佣金+滑点）
      gross_return = 理论价（theo，T+1 开盘、无滑点无佣金）计算的纯价格收益
      total_cost = 入场佣金 + 出场佣金；cost_ratio = total_cost / entry_out
    """
    remaining = rnd['shares']
    proceeds, proceeds_theo, exit_costs = 0.0, 0.0, 0.0
    for x in rnd['exits']:
        sh = remaining * x['frac']
        proceeds += sh * x['price']
        theo = x.get('theo_price')
        proceeds_theo += sh * (theo if (theo is not None and theo > 0) else x['price'])
        exit_costs += x['cost']
        remaining -= sh
    entry_value = rnd['shares'] * rnd['entry_price']
    if entry_value <= 0:
        return
    entry_theo = rnd.get('entry_theo')
    entry_value_theo = rnd['shares'] * (entry_theo if (entry_theo and entry_theo > 0)
                                        else rnd['entry_price'])
    entry_cost = rnd.get('entry_cost') or 0.0
    entry_out = entry_value + entry_cost
    cash_in = proceeds - exit_costs
    gross = proceeds_theo / entry_value_theo - 1.0
    net = cash_in / entry_out - 1.0
    total_cost = entry_cost + exit_costs
    ed = rnd['entry_date']
    xd = rnd['exits'][-1]['date'] if rnd['exits'] else ed
    hold_days = bisect_left(_TD_SRC, xd) - bisect_left(_TD_SRC, ed)
    trades.append({
        'ts_code': rnd['ts_code'], 'stock_name': rnd['name'],
        'industry': rnd['industry'], 'rule': rnd['rule'],
        'quality': _r(rnd['quality']), 'rr': _r(rnd['rr']),
        'entry_date': ed, 'entry_price': _r(rnd['entry_price']),
        'entry_theo': _r(rnd['entry_theo']),
        'entry_value': _r(entry_value, 2),
        'exit_date': xd,
        'exit_reason': rnd['exits'][-1]['reason'] if rnd['exits'] else '',
        'n_exits': len(rnd['exits']),
        'gross_return': _r(gross), 'net_return': _r(net),
        'gross_profit': _r(proceeds_theo - entry_value_theo, 2),
        'total_cost': _r(total_cost, 2),
        'cost_ratio': _r(total_cost / entry_out),
        'mfe': _r(rnd['high'] / rnd['entry_price'] - 1.0),
        'mae': _r(rnd['low'] / rnd['entry_price'] - 1.0),
        'hold_days': hold_days,
    })


def _r(x, nd=6):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


# ================================================================ 指标

def equity_metrics(equity: pd.DataFrame, periods_per_year: int = 252) -> dict:
    """组合层指标：年化收益 / 波动 / Sharpe / Calmar / 最大回撤 / 资金利用率"""
    if equity is None or equity.empty:
        return {}
    eq = equity['equity'].to_numpy(dtype=float)
    dates = equity['trade_date'].tolist()
    rets = eq[1:] / eq[:-1] - 1.0
    years = _span_years(dates)
    out = {
        'n_days': len(eq),
        'total_return': float(eq[-1] / eq[0] - 1.0) if eq[0] > 0 else None,
        'max_drawdown': float(equity['drawdown'].min()),
        'avg_positions': float(equity['n_positions'].mean()),
        'zero_position_ratio': float((equity['n_positions'] == 0).mean()),
    }
    if len(eq) > 2 and np.isfinite(rets).all():
        mu, sd = float(np.mean(rets)), float(np.std(rets, ddof=1))
        out['ann_return'] = float((eq[-1] / eq[0]) ** (1.0 / years) - 1.0) if years > 0 else None
        out['ann_vol'] = sd * np.sqrt(periods_per_year)
        out['sharpe'] = (mu / sd * np.sqrt(periods_per_year)) if sd > 0 else None
        out['calmar'] = (out['ann_return'] / abs(out['max_drawdown'])
                         if out.get('ann_return') and out['max_drawdown'] < 0 else None)
    eq_inv = equity['invested'].to_numpy(dtype=float)
    out['capital_utilization'] = float(np.mean(eq_inv / np.maximum(eq, 1e-9)))
    return out


def round_metrics(trades: list, span_dates=None) -> dict:
    """每轮交易指标（§10.2）"""
    if not trades:
        return {'n_rounds': 0}
    nets = np.array([t['net_return'] for t in trades if t['net_return'] is not None], dtype=float)
    gross = np.array([t['gross_return'] for t in trades if t['gross_return'] is not None], dtype=float)
    costs = np.array([t['total_cost'] for t in trades if t['total_cost'] is not None], dtype=float)
    gp_cur = np.array([t['gross_profit'] for t in trades if t.get('gross_profit') is not None], dtype=float)
    holds = np.array([t['hold_days'] for t in trades if t['hold_days'] is not None], dtype=float)
    n = len(nets)
    if n == 0:
        return {'n_rounds': 0}
    wins, losses = nets[nets > 0], nets[nets <= 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(-losses.sum()) if len(losses) else 0.0
    span = _span_years(span_dates) if span_dates else 1.0
    return {
        'n_rounds': n,
        'win_rate': float(len(wins) / n),
        'net_mean': float(nets.mean()),
        'net_median': float(np.median(nets)),
        'net_p10': float(np.percentile(nets, 10)),
        'net_p90': float(np.percentile(nets, 90)),
        'avg_win': float(wins.mean()) if len(wins) else None,
        'avg_loss': float(losses.mean()) if len(losses) else None,
        'profit_factor': (gp / gl) if gl > 0 else None,
        'gross_total': float(gross.sum()),
        'cost_total': float(costs.sum()),
        'cost_share_of_gross': (float(costs.sum() / gp_cur.sum())
                                if gp_cur.sum() > 0 else None),
        'expectancy': float(nets.mean()),
        'avg_hold_days': float(holds.mean()) if len(holds) else None,
        'rounds_per_year': float(n / max(span, 1e-6)),
    }


def _span_years(dates) -> float:
    from msw.data_adapter import norm_date
    if len(dates) < 2:
        return 1.0
    d0 = pd.to_datetime(norm_date(dates[0]), format='%Y%m%d')
    d1 = pd.to_datetime(norm_date(dates[-1]), format='%Y%m%d')
    return max((d1 - d0).days / 365.25, 1e-6)
