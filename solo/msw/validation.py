# -*- coding: utf-8 -*-
"""MSW 研究验证（任务书 §11 validation.py：MSW-D）

内容（§9）：
  基线对照    buy_hold / ma_trend / momentum / pure_meanrev / matched_control
  消融实验    trend_filter / fundamental / quality_score / c1_volume_cond /
              c3_breakout / regime_filter / vol_position
  压力测试    成本梯度、参数扰动、剔除 Top5% 收益股票、分年度/市场状态、
              极端盈利依赖、滑点敏感性
所有对照组使用相同交易成本、交易日历与执行约束（§9.3）。
"""
import copy
import numpy as np
import pandas as pd

from msw.backtest import (simulate, equity_metrics, round_metrics, set_trade_calendar)
from msw import load_config

BENCH = '000300.SH'


# ---------------------------------------------------------------- 工具

def _cfg_with(cfg: dict, **overrides) -> dict:
    c = copy.deepcopy(cfg)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c


def _sim(cfg, events, walk, panel, start, end, **kw):
    return simulate(cfg, events, walk['bars'], panel, start, end,
                    meta=walk['meta'], **kw)


def _bundle(res, span):
    em = equity_metrics(res['equity'])
    rm = round_metrics(res['trades'], span_dates=span)
    return {'equity_metrics': em, 'round_metrics': rm, 'result': res}


# ---------------------------------------------------------------- 候选池查询

def pool_at_date(records_by_code: dict, date: str, min_score: float) -> set:
    """某日候选池（趋势通过且质量分达标）"""
    out = set()
    for code, recs in records_by_code.items():
        for r in recs:
            if r['trade_date'] == date and r['trend_pass'] and \
                    (r['msw_quality_score'] or 0) >= min_score:
                out.add(code)
                break
    return out


def first_pool_date(records_by_code: dict, start: str):
    """记录中 >= start 的最早日期（用于 B&H 建仓日）"""
    ds = sorted({r['trade_date'] for recs in records_by_code.values() for r in recs
                 if r['trade_date'] >= start})
    return ds[0] if ds else None


# ---------------------------------------------------------------- 基线

def run_baselines(cfg: dict, walk: dict, panel, data, start: str, end: str,
                  logger=print) -> dict:
    """五类基线 + 指数基准，全部与 MSW 同成本/日历/执行约束"""
    out = {}
    span = [start, end]
    min_score = cfg['quality_score']['min_score']

    # 1) 买入持有：候选池首日等权买入，持有到期末
    d0 = first_pool_date(walk['records'], start)
    if d0:
        pool = pool_at_date(walk['records'], d0, min_score)
        if pool:
            evs = {c: [{'type': 'ENTRY', 'signal_date': d0, 'rule': 'BUY_HOLD',
                        'quality': None, 'rr': None, 'detail': {}}] for c in pool}
            out['buy_hold'] = _bundle(
                _sim(cfg, evs, walk, panel, start, end, use_portfolio_limits=False,
                     portfolio_cfg_over={'mode': 'equal',
                                         'equal_weight_frac': 1.0 / max(len(pool), 1),
                                         'max_positions': len(pool)}), span)
            out['buy_hold']['n_stocks'] = len(pool)
            out['buy_hold']['entry_date'] = d0

    # 2) MA250 趋势
    out['ma_trend'] = _bundle(
        _sim(cfg, walk['baseline']['ma_trend'], walk, panel, start, end,
             use_portfolio_limits=False,
             portfolio_cfg_over={'mode': 'equal', 'equal_weight_frac': 0.05}), span)

    # 3) 动量：每 20 个交易日按 120 日收益选 top N 调仓
    n_top = int(cfg.get('portfolio', {}).get('max_positions', 10))
    mom_events = _momentum_events(walk, panel, start, end, n_top)
    out['momentum'] = _bundle(
        _sim(cfg, mom_events, walk, panel, start, end, use_portfolio_limits=False,
             portfolio_cfg_over={'mode': 'equal',
                                 'equal_weight_frac': 1.0 / max(n_top, 1),
                                 'max_positions': n_top}), span)

    # 4) 单纯均值回归（无趋势过滤）
    out['pure_meanrev'] = _bundle(
        _sim(cfg, walk['baseline']['meanrev'], walk, panel, start, end,
             use_portfolio_limits=False,
             portfolio_cfg_over={'mode': 'equal', 'equal_weight_frac': 0.05}), span)

    # 5) 匹配对照（安慰剂计时）：把每个 MSW 选中股的完整交易序列
    #    （同一信号日/规则/分批决策）移植到其匹配对照股上，用对照股自身的
    #    价格/涨跌停/停牌/ATR 重新模拟。MSW 的超额收益若来自选股，对照组应显著跑输；
    #    若来自计时，两组应接近。注意：不能对对照股"跑同一策略"——对照股正是因
    #    未产生任何入场信号才入选，同策略必然零交易（历史缺陷）。
    ctrl = build_matched_control(cfg, walk, panel, data, start, end, logger=logger)
    if ctrl:
        from msw.backtest import load_bars_for_codes
        ctrl_bars = load_bars_for_codes(data, sorted(ctrl['codes']), cfg, logger=logger)
        placebo = build_placebo_events(ctrl['pairs'], walk['events'])
        placebo = {c: evs for c, evs in placebo.items() if c in ctrl_bars}
        if placebo:
            names, lds, inds = data.basic_maps()
            ctrl_meta = {c: {'name': names.get(c, c),
                             'industry': inds.get(c, '未知'),
                             'list_date': lds.get(c, '')} for c in placebo}
            out['matched_control'] = _bundle(
                _sim(cfg, placebo, {'bars': ctrl_bars, 'meta': ctrl_meta},
                     panel, start, end), span)
            out['matched_control']['n_pairs'] = ctrl['n_pairs']
            out['matched_control']['n_control_events'] = sum(
                len(v) for v in placebo.values())
            out['matched_control']['design'] = 'placebo_timing'

    # 6) 指数买入持有（参考）
    from msw.data_adapter import norm_date
    lo, hi = norm_date(start), norm_date(end)   # 面板/基准索引为 YYYYMMDD 文本
    idx = panel.bench_close
    idx = idx[(idx.index >= lo) & (idx.index <= hi)]
    if len(idx) > 2:
        r = float(idx.iloc[-1] / idx.iloc[0] - 1.0)
        out['index_buy_hold'] = {'total_return': r, 'n_days': len(idx),
                                 'first_date': str(idx.index[0]),
                                 'last_date': str(idx.index[-1])}

    return out


def _momentum_events(walk: dict, panel, start: str, end: str, n_top: int) -> dict:
    """动量基线事件流：每 20 个交易日按 ret_120d 选 top N 调仓"""
    from msw.data_adapter import norm_date
    lo, hi = norm_date(start), norm_date(end)   # 面板日期为 YYYYMMDD 文本
    tds = [d for d in panel.dates if lo <= d <= hi]
    rebal = set(tds[::20])
    # 每只股票在调仓日的 ret_120d
    per_code = {}
    for code, (dates, rets) in walk['ret120'].items():
        m = {d: (rets[i] if i < len(rets) else np.nan) for i, d in enumerate(dates)}
        per_code[code] = m
    events = {}
    held = set()
    for d in tds:
        if d not in rebal:
            continue
        ranked = sorted(((m.get(d, np.nan), c) for c, m in per_code.items()
                         if np.isfinite(m.get(d, np.nan))), reverse=True)
        top = {c for _, c in ranked[:n_top]}
        for c in held - top:
            events.setdefault(c, []).append(
                {'type': 'EXIT', 'signal_date': d, 'reason': 'rebalance',
                 'frac': 1.0, 'rule': 'MOMENTUM', 'entry_price': None, 'entry_date': ''})
        for c in top - held:
            events.setdefault(c, []).append(
                {'type': 'ENTRY', 'signal_date': d, 'rule': 'MOMENTUM',
                 'quality': None, 'rr': None, 'detail': {}})
        held = top
    for c in held:
        events.setdefault(c, []).append(
            {'type': 'EXIT', 'signal_date': tds[-1], 'reason': 'end',
             'frac': 1.0, 'rule': 'MOMENTUM', 'entry_price': None, 'entry_date': ''})
    return events


def build_placebo_events(pairs: dict, events: dict) -> dict:
    """安慰剂计时事件流：{选中股: 对照股} + MSW 事件流 → {对照股: 事件序列}

    事件逐个浅拷贝（signal_date/rule/quality/rr/frac/reason 原样保留），
    成交价、涨跌停、停牌、ATR 仓位全部改由对照股自身数据决定。
    """
    out = {}
    for s, c in pairs.items():
        evs = events.get(s)
        if evs:
            out[c] = [dict(e) for e in evs]
    return out


def build_matched_control(cfg: dict, walk: dict, panel, data, start: str, end: str,
                          logger=print) -> dict | None:
    """匹配对照组：对每个 MSW 选中股票，找同行业、市值/流动性最接近的未选中股票"""
    selected = {c for c, evs in walk['events'].items()
                if any(e['type'] == 'ENTRY' for e in evs)}
    if not selected:
        return None
    d0 = first_pool_date(walk['records'], start) or start
    snap = data.snapshot_fields(d0)
    if snap is None or snap.empty:
        return None
    snap = snap.set_index('ts_code')
    ind_map = {c: walk['meta'].get(c, {}).get('industry', '未知') for c in walk['meta']}
    matched, used = {}, set()
    for s in selected:
        if s not in snap.index:
            continue
        ind = ind_map.get(s, '未知')
        cands = [c for c in snap.index
                 if c not in selected and c not in used and ind_map.get(c, '未知') == ind]
        if not cands:
            continue
        try:
            mv_s = float(np.log(max(snap.loc[s, 'total_mv'], 1.0)))
            am_s = float(np.log(max(snap.loc[s, 'amount'], 1.0)))
        except Exception:
            continue
        best, best_d = None, np.inf
        for c in cands:
            try:
                d = (abs(float(np.log(max(snap.loc[c, 'total_mv'], 1.0))) - mv_s)
                     + abs(float(np.log(max(snap.loc[c, 'amount'], 1.0))) - am_s))
            except Exception:
                continue
            if d < best_d:
                best, best_d = c, d
        if best is not None:
            matched[s] = best
            used.add(best)
    if not matched:
        return None
    logger(f'[matched] {len(matched)} pairs built')
    return {'codes': set(matched.values()), 'pairs': matched, 'n_pairs': len(matched)}


# ---------------------------------------------------------------- 消融

ABLATION_OVERRIDES = {
    'no_trend_filter': {'trend_filter': {'enabled': False}},
    'no_fundamental': {'fundamental': {'enabled': False}},
    'no_quality_score': {'quality_score': {'gate_entries': False}},
    'no_c1_volume_cond': {'signal_c1_pullback': {'require_vol_contract': False}},
    'no_c3_breakout': {'signal_c3_breakout': {'enabled': False}},
    'with_regime_filter': {'market_regime': {'enabled': True}},
}


def run_ablation(cfg: dict, walk_full: dict, panel, data, start: str, end: str,
                 codes: list, logger=print) -> dict:
    """消融实验：在指定股票子集上以改写配置重新行走 + 模拟。

    codes: 消融用股票子集（全量消融成本过高时用流动性子集，需在报告中披露）
    """
    from msw.backtest import walk_universe
    out = {}
    for name, over in ABLATION_OVERRIDES.items():
        w = walk_universe(cfg, data, panel, codes=codes, compute_baselines=False,
                          logger=logger, cfg_overrides=over)
        res = _sim(cfg, w['events'], w, panel, start, end)
        out[name] = {'equity_metrics': equity_metrics(res['equity']),
                     'round_metrics': round_metrics(res['trades'],
                                                    span_dates=[start, end]),
                     'n_walked': w['n_walked']}
        logger(f'[ablation] {name}: rounds={out[name]["round_metrics"].get("n_rounds")} '
               f'net_mean={out[name]["round_metrics"].get("net_mean")}')
    # vol_position 消融：无需重新行走，只改组合配置
    res = _sim(cfg, walk_full['events'], walk_full, panel, start, end,
               portfolio_cfg_over={'mode': 'equal'})
    out['no_vol_position'] = {'equity_metrics': equity_metrics(res['equity']),
                              'round_metrics': round_metrics(res['trades'],
                                                             span_dates=[start, end])}
    return out


# ---------------------------------------------------------------- 压力测试

def cost_sensitivity(cfg: dict, walk: dict, panel, start: str, end: str,
                     bps_list=None) -> list:
    """成本梯度：同一事件流，不同往返成本重新模拟（无需重新行走）"""
    bps_list = bps_list or cfg.get('backtest', {}).get(
        'cost_sensitivity_bp', [20.0, 30.0, 50.0, 80.0])
    out = []
    for bp in bps_list:
        c = _cfg_with(cfg, backtest={'cost_round_trip_bp': float(bp)})
        res = _sim(c, walk['events'], walk, panel, start, end)
        em, rm = equity_metrics(res['equity']), round_metrics(
            res['trades'], span_dates=[start, end])
        out.append({'cost_round_trip_bp': bp,
                    'total_return': em.get('total_return'),
                    'sharpe': em.get('sharpe'),
                    'max_drawdown': em.get('max_drawdown'),
                    'n_rounds': rm.get('n_rounds'),
                    'net_mean': rm.get('net_mean'),
                    'win_rate': rm.get('win_rate'),
                    'expectancy': rm.get('expectancy')})
    return out


def drop_top_stocks(cfg: dict, walk: dict, panel, start: str, end: str,
                    frac: float = 0.05) -> dict:
    """剔除收益贡献最高的 frac 股票后重新模拟（§9.5）"""
    res = _sim(cfg, walk['events'], walk, panel, start, end)
    trades = res['trades']
    if not trades:
        return {}
    by_stock = {}
    for t in trades:
        by_stock.setdefault(t['ts_code'], []).append(t)
    contrib = sorted(((sum(x['net_return'] for x in v), c) for c, v in by_stock.items()),
                     reverse=True)
    k = max(1, int(len(contrib) * frac))
    drop = {c for _, c in contrib[:k]}
    kept_contrib = sum(v for v, _ in contrib[k:])
    total_contrib = sum(v for v, _ in contrib)
    res2 = _sim(cfg, walk['events'], walk, panel, start, end,
                allowed_codes={c for c in walk['events'] if c not in drop})
    em2, rm2 = equity_metrics(res2['equity']), round_metrics(
        res2['trades'], span_dates=[start, end])
    return {
        'n_stocks_total': len(contrib), 'n_dropped': k,
        'dropped_codes': sorted(drop),
        'top_share_of_net': (total_contrib - kept_contrib) / total_contrib
        if total_contrib != 0 else None,
        'kept_equity_metrics': em2, 'kept_round_metrics': rm2,
        'kept_n_rounds': rm2.get('n_rounds'),
    }


# 参数名 → 配置节映射（parameter_stability 用；键名与配置结构解耦，避免静默失效）
PARAM_SECTION = {
    'pullback_min_pct': 'signal_c1_pullback',
    'pullback_max_pct': 'signal_c1_pullback',
    'zigzag_pct': 'swing',
}


def parameter_stability(cfg: dict, panel, data, start: str, end: str,
                        codes: list, logger=print) -> list:
    """参数扰动：关键参数 ±1 档重新行走（子集）

    覆盖必须真正落到配置节上：PARAM_SECTION 未收录的参数名直接跳过并告警，
    绝不像早期版本那样写进不存在的空节名导致扰动静默失效。
    """
    from msw.backtest import walk_universe
    grid = cfg.get('validation', {}).get('param_stability', {})
    variants = []
    for key, vals in grid.items():
        if key == 'cost_round_trip_bp':
            continue                      # 成本敏感性单独测
        section = PARAM_SECTION.get(key)
        if section is None or section not in cfg:
            logger(f'[param] 未映射的参数 {key}（节 {section}），跳过')
            continue
        cur = _dig(cfg, f'{section}.{key}')
        for v in vals:
            if v == cur:
                continue                  # 当前基线值不重复测
            variants.append((section, key, v))
    out = []
    for section, key, v in variants:
        over = {section: {key: v}}
        w = walk_universe(cfg, data, panel, codes=codes, compute_baselines=False,
                          logger=logger, cfg_overrides=over)
        res = _sim(cfg, w['events'], w, panel, start, end)
        em, rm = equity_metrics(res['equity']), round_metrics(
            res['trades'], span_dates=[start, end])
        out.append({'param': f'{section}.{key}', 'value': v,
                    'n_rounds': rm.get('n_rounds'),
                    'net_mean': rm.get('net_mean'),
                    'win_rate': rm.get('win_rate'),
                    'total_return': em.get('total_return'),
                    'max_drawdown': em.get('max_drawdown')})
        logger(f'[param] {section}.{key}={v}: rounds={rm.get("n_rounds")} '
               f'net_mean={rm.get("net_mean")}')
    return out


def _dig(cfg, dotted):
    cur = cfg
    for k in dotted.split('.'):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def regime_and_yearly_slices(cfg: dict, walk: dict, panel, start: str, end: str,
                             res=None) -> dict:
    """分年度 / 分市场状态切片（§9.5）"""
    res = res or _sim(cfg, walk['events'], walk, panel, start, end)
    eq = res['equity']
    out = {'yearly': {}, 'regime': {}}
    # 分年度
    eq = eq.copy()
    eq['year'] = eq['trade_date'].str[:4]
    for y, g in eq.groupby('year'):
        if len(g) < 5:
            continue
        base = g['equity'].iloc[0]
        out['yearly'][y] = {'total_return': float(g['equity'].iloc[-1] / base - 1.0),
                            'max_drawdown': float(g['drawdown'].min()),
                            'avg_positions': float(g['n_positions'].mean()),
                            'n_days': len(g)}
    # 分市场状态：基准指数 vs 其 MA120 + 市场宽度
    bench = panel.bench_close
    ma120 = bench.rolling(120, min_periods=60).mean()
    states = {}
    for d in eq['trade_date']:
        if d not in bench.index:
            continue
        b = float(bench.loc[d])
        m = float(ma120.loc[d]) if d in ma120.index and np.isfinite(ma120.loc[d]) else np.nan
        br = panel.breadth(d)
        if np.isfinite(m) and b > m * 1.02 and (not np.isfinite(br) or br > 0.5):
            st = 'bull'
        elif np.isfinite(m) and b < m * 0.98:
            st = 'bear'
        else:
            st = 'range'
        states[d] = st
    eq['regime'] = eq['trade_date'].map(states)
    for rg, g in eq.groupby('regime'):
        if len(g) < 5:
            continue
        base = g['equity'].iloc[0]
        out['regime'][rg] = {'total_return': float(g['equity'].iloc[-1] / base - 1.0),
                             'max_drawdown': float(g['drawdown'].min()),
                             'n_days': len(g)}
    # 交易按规则/退出原因分布
    tr = res['trades']
    out['by_rule'] = {}
    for t in tr:
        out['by_rule'].setdefault(t['rule'], {'n': 0, 'net_sum': 0.0})
        out['by_rule'][t['rule']]['n'] += 1
        out['by_rule'][t['rule']]['net_sum'] += t['net_return'] or 0.0
    out['by_exit_reason'] = {}
    for t in tr:
        out['by_exit_reason'].setdefault(t['exit_reason'], {'n': 0, 'net_sum': 0.0})
        out['by_exit_reason'][t['exit_reason']]['n'] += 1
        out['by_exit_reason'][t['exit_reason']]['net_sum'] += t['net_return'] or 0.0
    return out
