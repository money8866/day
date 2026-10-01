# -*- coding: utf-8 -*-
"""HVT-BULL V3.5 Trade Execution 历史回测（规格§33/§34，无未来函数）

对每个 HVT 事件在"决策时点"回放 daily.run_daily 完整决策管线：
  V3.0 双评分 → V3.1 Future Expansion → build_trade_plan → V3.5 Trade Execution
决策时点选取（与 backtest.py V3.0 决策时点副本模式一致，并补齐回踩确认时点）：
  - 突破发生在 t0 后 40 个交易日内（daily lookback 窗口）→ 决策点 = 突破日收盘（BREAKOUT）
  - 突破后至 t0+40 内首个回踩确认日（pb_verdict=GOOD）→ 追加决策点 = 该日收盘（PULLBACK，
    实盘 daily 每日重跑在该日输出 PULLBACK_BUY；回测此前缺失该时点）
  - 40 日内无突破 → 决策日 = t0（T0，TE 输出 BREAKOUT_WAIT/WAIT/SKIP 是诚实结果）
统计各执行池 T+N 收益 / MFE / MAE / 胜率 / 盈亏比，对比：
  HVT 全池 vs BUY 池（action ∈ BUY/BUY_ON_CONFIRM）
            vs BUY 可执行池（剔除次日高开越过 NO_CHASE_LEVEL 的不可成交样本）
            vs BUY 每日Top-N池（按执行分排序、每日最多 max_buy_candidates 只，§26）

无未来函数保证（§37.13/37.14）：
  1) 全跟踪探针在副本上运行、仅用于定位决策日，事件对象保持 T0 干净状态；
     决策输入全部截断至决策日：update_tracking(end_idx=d+1)、expansion_score(asof_idx=d)、
     compute_future_expansion/compute_trade_execution 传 df.iloc[:d+1]（两者内部消费 iloc[-1]）
  2) RS 图按决策日全市场截面计算（rs5/10/20/60/120，复用 daily._build_rs_maps 口径）
  3) 上下文字段 as-of：基本面 ann_date<=决策日、板块 主题景气度/映射(<=决策日快照)、资金流(t0日)
  4) 前瞻收益/MFE/MAE 严格使用决策日之后数据；基准 ActualEntry = T+1 开盘
  5) 无分钟数据 → intraday_available 恒 False（INTRADAY_CONFIRMATION_UNAVAILABLE），不伪造 VWAP 确认
"""

import os
import sys
import copy
import json
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.data_loader import HvtDataLoader
from hvt_bull.engine import HvtBullEngine
from hvt_bull import context as hvt_context
from hvt_bull.backtest import (
    _load_config, _universe_codes_period, _fundamental_asof, _sector_asof, _bp, _stat,
)
from hvt_bull.daily import _build_rs_maps, _load_calibration, _fe_num
from hvt_bull.future_expansion import compute_future_expansion
from hvt_bull.trade_execution import compute_trade_execution, TE_JSON_FIELDS

TE_HORIZONS = (1, 3, 5, 10, 20, 60, 120)
TE_MFE_HORIZONS = (10, 20, 60, 120)
TE_GROUP_HORIZONS = (10, 20, 60)
BUY_ACTIONS = ('BUY', 'BUY_ON_CONFIRM')
# daily.run_daily 的 lookback_days=40：事件 t0 距决策日超过 40 个交易日时 daily 不再覆盖
TE_DECISION_MAX_LAG = 40

TE_CSV_COLUMNS = (
    'ts_code', 'name', 'signal_date', 'decision_date', 'decision_lag', 'decision_point',
    'v3_state', 'hvt_grade', 'signal_tier', 'entry_score', 'expansion_score', 'tail_score',
    'hard_veto', 'platform_breakout', 'locked_chip_dt',
    'fundamental_score', 'sector_strength', 'money_quality_score',
    'fe_score', 'fe20', 'fe60', 'fe120', 'lifecycle', 'trend_gain',
    'expansion_type', 'continuation_score', 'extension_risk',
    'buyability', 'execution_score', 'execution_state', 'next_day_action',
    'primary_horizon', 'stock_type', 'confirmation_level',
    'entry_trigger', 'buy_zone_low', 'buy_zone_high', 'invalidation', 'no_chase_level',
    'position_size', 'initial_position', 'execution_reason', 'why_not_buy',
    'gap_pct', 'actual_entry', 'gap_no_chase',
    'pb_verdict', 'pb_shrink_ratio', 'pb_low_vs_t0high', 'pb_cur_vs_t0high', 'reentry_streak',
    'r1', 'r3', 'r5', 'r10', 'r20', 'r60', 'r120',
    'er10', 'er20', 'er60', 'er120',
    'max_gain', 'max_dd',
    'mfe10', 'mfe20', 'mfe60', 'mfe120', 'mae10', 'mae20', 'mae60', 'mae120',
)


def _pool_stats(sub: pd.DataFrame, horizons) -> dict:
    """池统计：决策日收盘收益(r{h}) + T+1开盘执行收益(er{h}) + MFE/MAE 均值"""
    if sub is None or sub.empty:
        return {'n': 0}
    out = {'n': int(len(sub))}
    out.update(_stat(sub, horizons))
    for h in horizons:
        col = f'er{h}'
        if col in sub.columns:
            bp = _bp(pd.to_numeric(sub[col], errors='coerce'))
            if bp.get('n'):
                out[col] = bp
    for h in TE_MFE_HORIZONS:
        for pre in ('mfe', 'mae'):
            col = f'{pre}{h}'
            if col in sub.columns:
                s = pd.to_numeric(sub[col], errors='coerce').dropna()
                if s.size:
                    out[f'{pre}{h}_avg'] = round(float(s.mean()), 2)
    return out


def _compare(hvt: dict, buy: dict) -> dict:
    out = {}
    for key in ('r10_win', 'r20_win', 'r60_win', 'r10_avg', 'r20_avg', 'r60_avg', 'max_dd_avg'):
        if key in hvt and key in buy:
            out[key] = {'hvt': hvt[key], 'buy': buy[key], 'delta': round(buy[key] - hvt[key], 2)}
    for h in (10, 20, 60):
        ha, ba = hvt.get(f'er{h}'), buy.get(f'er{h}')
        if isinstance(ha, dict) and isinstance(ba, dict) and ha.get('n') and ba.get('n'):
            out[f'er{h}'] = {
                'hvt': {'win': ha.get('win'), 'avg': ha.get('avg'), 'pf': ha.get('pf')},
                'buy': {'win': ba.get('win'), 'avg': ba.get('avg'), 'pf': ba.get('pf')},
            }
    return out


def _proof(hvt: dict, buy: dict, label: str) -> dict:
    """§34 验收：BUY池相对HVT池的风险调整后改善证明"""
    def _pf(d, h):
        b = d.get(f'er{h}')
        return b.get('pf') if isinstance(b, dict) else None

    out = {'buy_pool': label}
    for key in ('r20_win', 'r20_avg', 'r60_win', 'r60_avg'):
        hv, bv = hvt.get(key), buy.get(key)
        if hv is not None and bv is not None:
            out[key] = {'hvt': hv, 'buy': bv, 'delta': round(bv - hv, 2)}
    for h in (20, 60):
        hp, bp_ = _pf(hvt, h), _pf(buy, h)
        if hp is not None and bp_ is not None:
            out[f'er{h}_pf'] = {'hvt': hp, 'buy': bp_, 'delta': round(bp_ - hp, 2)}
    hd, bd = hvt.get('max_dd_avg'), buy.get('max_dd_avg')
    if hd is not None and bd is not None:
        out['max_dd_avg'] = {'hvt': hd, 'buy': bd, 'delta': round(bd - hd, 2)}
    win_up = bool(out.get('r20_win') and out['r20_win']['delta'] > 0)
    pf20 = out.get('er20_pf') or {}
    pf_up = bool(pf20 and (pf20.get('delta') or 0) > 0)
    dd = out.get('max_dd_avg')
    dd_down = bool(dd and dd['delta'] > 0)  # max_dd 为负值，delta>0 即回撤更浅
    out['win_rate_improved'] = win_up
    out['pf_improved'] = pf_up
    out['drawdown_reduced'] = dd_down
    flags = int(win_up) + int(pf_up)
    out['conclusion'] = 'PASS' if flags == 2 else ('PARTIAL' if flags == 1 else 'FAIL')
    return out


def _pullback_decision_points(engine, df: pd.DataFrame, ev, b_idx: int, idx: int):
    """突破后全部回踩确认日：首个 = PULLBACK 决策点，后续 = 再入决策点（PULLBACK_RE）。

    实盘 daily 每天重跑，回踩确认日及之后的 GOOD 再入日都会再次覆盖该事件并可能
    输出 PULLBACK_BUY（如陆家嘴 20260828 即为再入日信号）；回测此前只采样首个
    回踩日，再入日信号的历史表现缺失。此处从突破日次日起逐日用干净副本截断重放
    update_tracking，收集窗口内全部 pb_verdict=GOOD 的交易日；
    判定完全复用 engine._eval_pullback，不自行重实现条件。
    决策日上限 = t0 + TE_DECISION_MAX_LAG（daily lookback 窗口，超出后实盘不再覆盖）。
    """
    n = len(df)
    j_end = min(n - 1, idx + TE_DECISION_MAX_LAG)
    good = []
    for j in range(b_idx + 1, j_end + 1):
        ev_j = copy.copy(ev)
        engine.update_tracking(df, ev_j, end_idx=j + 1)
        if getattr(ev_j, 'pb_verdict', 'NA') == 'GOOD':
            good.append(j)
    if not good:
        return []
    return [(good[0], 'PULLBACK')] + [(j, 'PULLBACK_RE') for j in good[1:]]


def _te_decision_record(engine, loader, cfg, te_cfg, calib, code, df, ev,
                        idx: int, d_idx: int, decision_point: str, dates: list,
                        closes, highs, lows, opn, rs_cache: dict) -> dict:
    """单个决策时点的完整决策记录（与 daily.run_daily 顺序一致，输入全部截至 d_idx）。

    ev 必须是 update_tracking 之前的干净 T0 状态（§37.13 无未来函数）：
    突破/回踩等跟踪字段在本函数内以 end_idx=d_idx+1 截断重放得出。
    T0 时点（d_idx=idx）时 update_tracking 因 end<=idx+1 直接返回，
    ev_dt 保持干净副本（无突破字段），TE 诚实输出 BREAKOUT_WAIT/WAIT/SKIP。
    """
    ev_dt = copy.copy(ev)
    if engine.distribution_risk(ev_dt):
        ev_dt.state = 'DISTRIBUTION'
    engine.update_tracking(df, ev_dt, end_idx=d_idx + 1)
    ev_dt.fundamental_score, ev_dt.fundamental_grade, _fin_known = _fundamental_asof(code, dates[d_idx])
    if not np.isfinite(ev_dt.fundamental_score):
        ev_dt.fundamental_score, ev_dt.fundamental_grade = 50.0, 'B'
    ev_dt.sector_strength, ev_dt.sector_name, _ = _sector_asof(code, dates[d_idx])
    if not np.isfinite(ev_dt.sector_strength):
        ev_dt.sector_strength = 50.0
    ev_dt.money_quality_score = hvt_context.money_quality(code, ev_dt.t0_date, cfg)
    rsm = rs_cache.get(dates[d_idx])
    if rsm is None:
        rsm = _build_rs_maps(loader, dates[d_idx])
        rs_cache[dates[d_idx]] = rsm
    if rsm:
        engine.compute_rs(ev_dt, rsm, dates[d_idx])
    engine.entry_score(ev_dt)
    engine.expansion_score(df, ev_dt, asof_idx=d_idx)
    engine.hard_veto(ev_dt)
    engine.apply_tail_calibration(ev_dt, calib)
    engine.classify_v3(ev_dt)
    ev_dt.signal_tier = ev_dt.signal_tier or 'T3'
    # V3.1 FE（截断 df：compute_future_expansion 内部消费 iloc[-1]）
    rs_row = {}
    for hk, hv in (rsm.get(dates[d_idx]) or {}).items():
        try:
            rs_row[hk] = float(hv.get(code, np.nan))
        except Exception:
            continue
    df_trunc = df.iloc[:d_idx + 1]
    try:
        fe = compute_future_expansion(df_trunc, ev_dt, rs_row)
    except Exception:
        fe = {}
    ev_dt.fe_score = _fe_num(fe.get('fe_score'))
    ev_dt.fe10 = _fe_num(fe.get('fe10'))
    ev_dt.fe20 = _fe_num(fe.get('fe20'))
    ev_dt.fe60 = _fe_num(fe.get('fe60'))
    ev_dt.fe120 = _fe_num(fe.get('fe120'))
    ev_dt.lifecycle = str(fe.get('lifecycle') or '')
    ev_dt.trend_gain = _fe_num(fe.get('trend_gain'))
    ev_dt.base_price = _fe_num(fe.get('base_price'))
    ev_dt.base_date = str(fe.get('base_date') or '')
    ev_dt.base_method = str(fe.get('base_method') or '')
    ev_dt.continuation_score = _fe_num(fe.get('continuation_score'))
    ev_dt.extension_risk = _fe_num(fe.get('extension_risk'))
    ev_dt.expansion_type = str(fe.get('expansion_type') or '')
    ev_dt.fe_mode = str(fe.get('mode') or '')
    ev_dt.fe_parts = dict(fe.get('fe_parts') or {})
    ev_dt.why_space = list(fe.get('why_space') or [])
    ev_dt.why_risk = list(fe.get('why_risk') or [])
    engine.build_trade_plan(ev_dt)
    # V3.5 TE（截断 df：compute_trade_execution 内部消费 iloc[-1]）
    te = {}
    try:
        te = compute_trade_execution(df_trunc, ev_dt, te_cfg)
    except Exception as _te_err:
        if os.environ.get('HVT_TE_DEBUG'):
            print(f"[TE-BT] {code} TE 异常: {_te_err}")

    rec = {
        'ts_code': code,
        'name': ev_dt.name,
        'signal_date': dates[idx],
        'decision_date': dates[d_idx],
        'decision_lag': int(d_idx - idx),
        'decision_point': decision_point,
        'v3_state': ev_dt.state,
        'hvt_grade': getattr(ev_dt, 'hvt_grade', ''),
        'signal_tier': getattr(ev_dt, 'signal_tier', ''),
        'entry_score': round(float(ev_dt.entry_score), 1),
        'expansion_score': round(float(ev_dt.expansion_score), 1),
        'tail_score': round(float(ev_dt.tail_score), 1),
        'hard_veto': '|'.join(ev_dt.hard_veto or []),
        'platform_breakout': bool(ev_dt.platform_breakout),
        'locked_chip_dt': bool(getattr(ev_dt, 'locked_chip', False)),
        'pb_verdict': getattr(ev_dt, 'pb_verdict', 'NA'),
        'pb_shrink_ratio': getattr(ev_dt, 'pb_shrink_ratio', None),
        'pb_low_vs_t0high': getattr(ev_dt, 'pb_low_vs_t0high', None),
        'pb_cur_vs_t0high': getattr(ev_dt, 'pb_cur_vs_t0high', None),
        'fundamental_score': round(float(ev_dt.fundamental_score), 1),
        'sector_strength': round(float(ev_dt.sector_strength), 1),
        'money_quality_score': round(float(ev_dt.money_quality_score), 1),
        'fe_score': round(ev_dt.fe_score, 1),
        'fe20': round(ev_dt.fe20, 1),
        'fe60': round(ev_dt.fe60, 1),
        'fe120': round(ev_dt.fe120, 1),
        'lifecycle': ev_dt.lifecycle,
        'trend_gain': round(ev_dt.trend_gain, 1),
        'expansion_type': ev_dt.expansion_type,
        'continuation_score': round(ev_dt.continuation_score, 1),
        'extension_risk': round(ev_dt.extension_risk, 1),
    }
    for kk in TE_JSON_FIELDS:
        vv = te.get(kk)
        if isinstance(vv, (list, tuple)):
            vv = '; '.join(str(x) for x in vv)
        elif isinstance(vv, dict):
            vv = json.dumps(vv, ensure_ascii=False)
        rec[kk] = vv

    # ---- 前瞻收益 / MFE / MAE（严格使用决策日之后数据；ActualEntry=T+1开盘） ----
    n = len(closes)
    actual_entry = np.nan
    if d_idx + 1 < n and np.isfinite(opn[d_idx + 1]) and opn[d_idx + 1] > 0:
        actual_entry = float(opn[d_idx + 1])
    rec['actual_entry'] = round(actual_entry, 2) if np.isfinite(actual_entry) else None
    rec['gap_pct'] = round((actual_entry / closes[d_idx] - 1.0) * 100.0, 2) \
        if np.isfinite(actual_entry) else None
    ncl = te.get('no_chase_level')
    rec['gap_no_chase'] = bool(
        np.isfinite(actual_entry) and ncl is not None
        and np.isfinite(float(ncl)) and actual_entry > float(ncl)
    )
    for h in TE_HORIZONS:
        j = d_idx + h
        if j < n:
            rec[f'r{h}'] = round((closes[j] / closes[d_idx] - 1.0) * 100.0, 2)
            if np.isfinite(actual_entry):
                rec[f'er{h}'] = round((closes[j] / actual_entry - 1.0) * 100.0, 2)
    w = closes[d_idx + 1:min(n, d_idx + 21)]
    if len(w):
        rec['max_gain'] = round(float(np.max(w) / closes[d_idx] - 1.0) * 100.0, 2)
        rec['max_dd'] = round(float(np.min(w) / closes[d_idx] - 1.0) * 100.0, 2)
    for h in TE_MFE_HORIZONS:
        e = min(n, d_idx + 1 + h)
        if e <= d_idx + 1:
            continue
        hi = highs[d_idx + 1:e]
        lo = lows[d_idx + 1:e]
        if np.isfinite(actual_entry) and np.isfinite(hi).any() and np.isfinite(lo).any():
            rec[f'mfe{h}'] = round(float(np.nanmax(hi) / actual_entry - 1.0) * 100.0, 2)
            rec[f'mae{h}'] = round(float(np.nanmin(lo) / actual_entry - 1.0) * 100.0, 2)
    return rec


def _streak_bucket(df_ev: pd.DataFrame) -> pd.Series:
    """reentry_streak → 分层桶：first(首日入池)/re1(连续第2日)/re2plus(连续第3日及以后)。"""
    n = pd.to_numeric(df_ev['reentry_streak'], errors='coerce').fillna(0)
    return pd.Series(np.where(n == 0, 'first', np.where(n == 1, 're1', 're2plus')),
                     index=df_ev.index)


def _cand_top_mask(df_ev: pd.DataFrame, buy_mask: pd.Series, max_buy: int) -> pd.Series:
    """每日 cand 的 Top-max_buy 掩码，cand 口径与 daily._te_buy_pool_filter 返回的 cand 完全一致。

    cand 定义：next_day_action ∈ BUY_ACTIONS 且 execution_state ∈ (READY_BUY, PULLBACK_BUY)，
    再按 (execution_score, buyability) 降序取每个 decision_date 的前 max_buy 只。
    该掩码同时是「回测 streak 递推源」与「R1/R2 审计样本源」，两处必须同源（否则口径漂移）。
    """
    cand_mask = buy_mask & df_ev['execution_state'].fillna('').astype(str) \
        .isin(('READY_BUY', 'PULLBACK_BUY'))
    sub = df_ev.loc[cand_mask].copy()
    if sub.empty:
        return pd.Series(False, index=df_ev.index)
    sub['_es'] = pd.to_numeric(sub['execution_score'], errors='coerce').fillna(-1.0)
    sub['_bb'] = pd.to_numeric(sub['buyability'], errors='coerce').fillna(-1.0)
    idx = (sub.sort_values(['decision_date', '_es', '_bb'], ascending=[True, False, False])
           .groupby('decision_date').head(max_buy).index)
    return pd.Series(df_ev.index.isin(idx), index=df_ev.index)


def _compute_streak(df_ev: pd.DataFrame, pool_mask: pd.Series, det_dates) -> pd.Series:
    """按 pool_mask 递推 reentry_streak：连续处于每日池中的决策日数（首次入池=0，中断归零）。

    与 daily 从昨日报告 JSON 读取 buy_pool 判定的 count 语义一致：同一 (股票, 决策日) 在池中
    则取「前一交易日同股票」的值 +1，否则置 0。返回按 df_ev 索引标签对齐的 Series。
    """
    all_dts = sorted(det_dates)
    prev_dt = {all_dts[i]: all_dts[i - 1] for i in range(1, len(all_dts))}
    d = df_ev.sort_values(['ts_code', 'decision_date'])
    by_key, vals = {}, []
    for r in d.itertuples():
        if bool(pool_mask.loc[r.Index]):
            p = prev_dt.get(r.decision_date)
            by_key[(r.ts_code, r.decision_date)] = by_key.get((r.ts_code, p), -1) + 1
        else:
            by_key[(r.ts_code, r.decision_date)] = 0
        vals.append(by_key[(r.ts_code, r.decision_date)])
    return pd.Series(vals, index=d.index)


def _reentry_buy_audit(df_ev: pd.DataFrame, max_buy: int,
                       bucket: pd.Series, cand_top: pd.Series) -> dict:
    """V3.6 再入规则（R1/R2）可复核化：按 daily._te_buy_pool_filter 的 cand 口径重建样本并统计。

    背景：daily._te_buy_pool_filter 的注释声称「R1 回测 pf 0.48~0.61 / R2 回测 pf 0.76」，
    但该结论此前只存在于注释里——回测脚本原先只把 buy_by_streak 打印到控制台、不落盘，
    也从未按 (streak, decision_point, next_day_action) 三元组交叉，导致规则无法复核。
    本函数重建与 _te_buy_pool_filter 完全一致的样本口径，使 R1/R2 可复现、可复核。
    口径：next_day_action ∈ BUY_ACTIONS 且 execution_state ∈ (READY_BUY, PULLBACK_BUY)，
          先按 (execution_score, buyability) 取每个 decision_date 的 Top-max_buy，再在桶内统计。
          样本源 cand_top 由调用方经 _cand_top_mask 生成（与 streak 递推源同源，避免口径漂移）。
    说明：只做统计与落盘，不改变任何信号或状态。
    """
    _dpc = df_ev['decision_point'].fillna('').astype(str)
    _actc = df_ev['next_day_action'].fillna('').astype(str)
    _streak_c = pd.to_numeric(df_ev['reentry_streak'], errors='coerce').fillna(0)

    drop_r1 = cand_top & (_streak_c >= 1) & (_actc == 'BUY_ON_CONFIRM')
    drop_r2 = cand_top & (~drop_r1) & (_streak_c == 0) & (_dpc == 'PULLBACK_RE') \
        & (_actc == 'BUY_ON_CONFIRM')
    keep_msk = cand_top & (~drop_r1) & (~drop_r2)

    _tri = df_ev.loc[cand_top].copy()
    if not _tri.empty:
        _tri['_bucket'] = bucket.reindex(_tri.index)
        _tri['_dp'] = _dpc.reindex(_tri.index)
        _tri['_act'] = _actc.reindex(_tri.index)
        triple = {f'{b}|{d or "-"}|{a or "-"}': _pool_stats(sub, TE_HORIZONS)
                  for (b, d, a), sub in _tri.groupby(['_bucket', '_dp', '_act'], dropna=False)}
    else:
        triple = {}

    def _er20_brief(p: dict) -> dict:
        e = (p or {}).get('er20') or {}
        return {'n': (p or {}).get('n', 0), 'win': e.get('win'),
                'avg': e.get('avg'), 'pf': e.get('pf')}

    _rule_pools = {
        'cand_top': _pool_stats(df_ev[cand_top], TE_HORIZONS),
        'kept': _pool_stats(df_ev[keep_msk], TE_HORIZONS),
        'dropped_r1': _pool_stats(df_ev[drop_r1], TE_HORIZONS),
        'dropped_r2': _pool_stats(df_ev[drop_r2], TE_HORIZONS),
    }
    return {
        'definition': ('样本 = 与 daily._te_buy_pool_filter 的 cand 同口径：'
                       'next_day_action ∈ BUY_ACTIONS 且 execution_state ∈ (READY_BUY, PULLBACK_BUY)，'
                       '先按 (execution_score, buyability) 取每个 decision_date 的 Top-max_buy（不补位）'),
        'rule_r1': 'reentry_streak >= 1 且 next_day_action == BUY_ON_CONFIRM → 剔除',
        'rule_r2': ('reentry_streak == 0 且 decision_point == PULLBACK_RE 且 '
                    'next_day_action == BUY_ON_CONFIRM → 剔除（R1 优先判定）'),
        'max_buy': int(max_buy),
        'n_cand_top': int(cand_top.sum()),
        'pools': _rule_pools,
        'rule_check_er20': {k: _er20_brief(v) for k, v in _rule_pools.items()},
        'triple': dict(sorted(triple.items())),
        'triple_key': 'reentry_bucket|decision_point|next_day_action',
    }


def _print_reentry_audit(audit: dict) -> None:
    print(f"[TE-BT] V3.6 再入规则复核（cand_top n={audit.get('n_cand_top')}，er20 = 盈均/亏均）：")
    for kn, p in audit.get('rule_check_er20', {}).items():
        print(f"  {kn:<12} n={p['n']:<5} win={p['win']}% avg={p['avg']}% pf={p['pf']}")
    print(f"[TE-BT] 三元组分组（{audit.get('triple_key')}）：")
    for kt, p in audit.get('triple', {}).items():
        er20 = p.get('er20') or {}
        print(f"  {kt:<40} n={p.get('n', 0):<5} er20win={er20.get('win')}% "
              f"er20avg={er20.get('avg')}% pf={er20.get('pf')}")


def run_te_backtest(start: str = None, end: str = None, cfg: dict = None,
                    sample_step: int = 1, max_events: int = None, out_dir: str = None) -> dict:
    """Trade Execution 历史回测主入口。"""
    if cfg is None:
        cfg = _load_config()
    bt = cfg.get('backtest', {})
    start = start or bt.get('start', '20250101')
    end = end or bt.get('end', '20260828')
    te_cfg = cfg.get('trade_execution') or {}
    max_buy = int(te_cfg.get('max_buy_candidates', 3))
    calib = _load_calibration(cfg)

    loader = HvtDataLoader()
    engine = HvtBullEngine(cfg)

    det_dates = loader.trade_dates(start, end)
    if not det_dates:
        print('[TE-BT] 回测区间无交易日')
        return {}
    if sample_step > 1:
        det_dates = det_dates[::sample_step]
    det_set = set(det_dates)

    codes = _universe_codes_period(loader, det_dates, cfg)
    print(f"[TE-BT] 区间 {start}~{end}，检测日 {len(det_dates)} 个，股票 {len(codes)} 只")

    import stock_cache as sc
    sb = sc.load_stock_basic()
    name_map = dict(zip(sb['ts_code'], sb.get('name', pd.Series(dtype=str)))) \
        if sb is not None and not sb.empty else {}

    anchor_date = str(cfg.get('hvt', {}).get('anchor_date', '') or '')
    hist_start = anchor_date or (pd.to_datetime(start) - pd.Timedelta(days=550)).strftime('%Y%m%d')
    fut_end = (pd.to_datetime(end) + pd.Timedelta(days=190)).strftime('%Y%m%d')  # 覆盖 T+120

    rs_cache = {}
    events = []
    for k, code in enumerate(codes):
        df = loader.load(code, hist_start, fut_end)
        if df is None or len(df) < 260:
            continue
        df = df.reset_index(drop=True)
        dates = df['trade_date'].tolist()
        date_idx = {d: i for i, d in enumerate(dates)}
        closes = df['close'].to_numpy(dtype=float)
        highs = df['high'].to_numpy(dtype=float)
        lows = df['low'].to_numpy(dtype=float)
        opn = df['open'].to_numpy(dtype=float)
        last_ev_idx = -10 ** 9

        for idx in range(30, len(df)):
            if dates[idx] not in det_set:
                continue
            if idx - last_ev_idx < 10:  # 事件冷却：10 个交易日内不重复计数
                continue
            ev = engine.detect_hvt(df, idx)
            if ev is None:
                continue
            ev.name = name_map.get(code, '')
            engine.evaluate_event(df, ev)
            if not engine.price_strength_ok(ev) and not ev.platform_breakout:
                continue
            last_ev_idx = idx

            # 探针在副本上定位决策时点；事件对象保持 T0 干净状态（§37.13 无未来函数）
            ev_probe = copy.copy(ev)
            engine.update_tracking(df, ev_probe, end_idx=len(df))
            b_idx = date_idx.get(ev_probe.breakout_date) if ev_probe.breakout_date else None
            if b_idx is not None and 0 <= b_idx - idx <= TE_DECISION_MAX_LAG:
                points = [(b_idx, 'BREAKOUT')]
                points.extend(_pullback_decision_points(engine, df, ev, b_idx, idx))
            else:
                points = [(idx, 'T0')]
            for d_idx, decision_point in points:
                events.append(_te_decision_record(
                    engine, loader, cfg, te_cfg, calib, code, df, ev,
                    idx, d_idx, decision_point, dates,
                    closes, highs, lows, opn, rs_cache))

        loader._cache.pop((code, hist_start, fut_end), None)  # 控制内存
        if (k + 1) % 300 == 0:
            print(f"[TE-BT] 股票 {k + 1}/{len(codes)}，累计事件 {len(events)}，RS缓存 {len(rs_cache)} 日")
        if max_events and len(events) >= max_events:
            print(f"[TE-BT] 达到事件上限 {max_events}，提前结束")
            break

    df_ev = pd.DataFrame(events)
    out_dir = out_dir or cfg.get('report', {}).get('output_dir',
                                                   os.path.join(BASE_DIR, 'report_daily'))
    os.makedirs(out_dir, exist_ok=True)
    tag = f'{start}_{end}'
    if df_ev.empty:
        out = {'start': start, 'end': end, 'n_events': 0}
        with open(os.path.join(out_dir, f'te_backtest_{tag}_summary.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        print('[TE-BT] 无事件')
        return out

    act = df_ev['next_day_action'].fillna('').astype(str)
    est = df_ev['execution_state'].fillna('').astype(str)
    buy_mask = act.isin(BUY_ACTIONS)
    exec_mask = buy_mask & (~df_ev['gap_no_chase'].fillna(False).astype(bool))
    # 每日Top-N池（§26：按执行分排序、每日最多 max_buy_candidates 只，模拟真实横截面约束）
    # 分组键 = decision_date（实盘 daily 报告按决策日横截面取 Top-N；原 signal_date 分组
    # 无法反映不同 t0 事件在同一决策日的真实竞争，属于采样语义偏差，已修正）
    sub_buy = df_ev.loc[buy_mask].copy()
    if not sub_buy.empty:
        sub_buy['_es'] = pd.to_numeric(sub_buy['execution_score'], errors='coerce').fillna(-1.0)
        sub_buy['_bb'] = pd.to_numeric(sub_buy['buyability'], errors='coerce').fillna(-1.0)
        top_idx = (sub_buy.sort_values(['decision_date', '_es', '_bb'], ascending=[True, False, False])
                   .groupby('decision_date').head(max_buy).index)
        # 与 else 分支保持同为 Series（按 df_ev.index 对齐），下方 L481 需按索引标签取用；
        # index.isin() 返回的是裸 ndarray，直接用 .loc 会 AttributeError
        top_mask = pd.Series(df_ev.index.isin(top_idx), index=df_ev.index)
    else:
        top_mask = pd.Series(False, index=df_ev.index)

    pools = {
        'hvt_all': _pool_stats(df_ev, TE_HORIZONS),
        'buy_pool': _pool_stats(df_ev[buy_mask], TE_HORIZONS),
        'buy_executable': _pool_stats(df_ev[exec_mask], TE_HORIZONS),
        'buy_daily_top': _pool_stats(df_ev[top_mask], TE_HORIZONS),
        'wait_confirm': _pool_stats(df_ev[est == 'WAIT_CONFIRM'], TE_GROUP_HORIZONS),
        'breakout_wait': _pool_stats(df_ev[est == 'BREAKOUT_WAIT'], TE_GROUP_HORIZONS),
        'no_chase': _pool_stats(df_ev[est == 'NO_CHASE'], TE_GROUP_HORIZONS),
        'skip': _pool_stats(df_ev[est == 'SKIP'], TE_GROUP_HORIZONS),
    }
    by_state = {s: _pool_stats(df_ev[est == s], TE_GROUP_HORIZONS)
                for s in sorted(set(est)) if s}
    stype = df_ev['stock_type'].fillna('').astype(str)
    by_type = {s: _pool_stats(df_ev[stype == s], TE_GROUP_HORIZONS)
               for s in sorted(set(stype)) if s}
    hor = df_ev['primary_horizon'].fillna('').astype(str)
    by_horizon = {s: _pool_stats(df_ev[hor == s], TE_GROUP_HORIZONS)
                  for s in sorted(set(hor)) if s}
    es_num = pd.to_numeric(df_ev['execution_score'], errors='coerce')
    buckets = {}
    for lab, mask in (
        ('>=85', es_num >= 85), ('75-85', (es_num >= 75) & (es_num < 85)),
        ('65-75', (es_num >= 65) & (es_num < 75)), ('50-65', (es_num >= 50) & (es_num < 65)),
        ('<50', es_num < 50), ('NA', es_num.isna()),
    ):
        buckets[lab] = _pool_stats(df_ev[mask.fillna(False)], TE_GROUP_HORIZONS)

    proof_pool = pools['buy_executable'] if pools['buy_executable'].get('n', 0) >= 15 \
        else pools['buy_pool']
    proof_label = 'buy_executable' if proof_pool is pools['buy_executable'] else 'buy_pool'
    proof = _proof(pools['hvt_all'], proof_pool, proof_label)
    verdict = {
        'buy_pool_vs_hvt': _compare(pools['hvt_all'], pools['buy_pool']),
        'buy_executable_vs_hvt': _compare(pools['hvt_all'], pools['buy_executable']),
        'buy_daily_top_vs_hvt': _compare(pools['hvt_all'], pools['buy_daily_top']),
    }

    # 按决策时点拆分：首入(BREAKOUT/PULLBACK) vs 再入(PULLBACK_RE) 对照，及 BUY 池分层
    dp = df_ev['decision_point'].fillna('').astype(str)
    by_dp = {s: _pool_stats(df_ev[dp == s], TE_GROUP_HORIZONS)
             for s in sorted(set(dp)) if s}
    dp_set = set(dp)
    buy_by_dp = {s: _pool_stats(df_ev[(dp == s) & buy_mask], TE_HORIZONS)
                 for s in ('BREAKOUT', 'PULLBACK', 'PULLBACK_RE', 'T0') if s in dp_set}

    # reentry_streak：连续处于每日 cand 池的决策日数（cand 口径 = daily._te_buy_pool_filter 的
    # cand，即 READY_BUY/PULLBACK_BUY 且 action ∈ BUY_ACTIONS 的 Top-max_buy），与实盘 daily
    # 从昨日报告 JSON 读取 buy_pool 判定再入的口径一致：同一事件在相邻检测日均为池成员则 +1，
    # 中断（非 BUY / 被挤出 Top-max_buy / 超 40 日决策窗）即归零重置。
    # 注：递推源必须是 cand_top 而非仅按 buy_mask 取的 Top-N，否则与 daily 候选定义漂移。
    all_dts = sorted(det_dates)
    cand_top = _cand_top_mask(df_ev, buy_mask, max_buy)
    _streak = _compute_streak(df_ev, cand_top, all_dts)
    df_ev = df_ev.sort_values(['ts_code', 'decision_date'])
    df_ev['reentry_streak'] = _streak.reindex(df_ev.index)
    _bucket = _streak_bucket(df_ev)
    # buy_mask 建于排序前（L417-425）；此处 df_ev 已按 ts_code/decision_date 重排，
    # 显式按索引标签对齐，消除 pandas 的隐式 reindex 告警（语义不变）
    buy_mask = buy_mask.reindex(df_ev.index) if isinstance(buy_mask, pd.Series) else \
        pd.Series(buy_mask, index=df_ev.index)
    cand_top = cand_top.reindex(df_ev.index)
    buy_by_streak = {b: _pool_stats(df_ev[(_bucket == b) & buy_mask], TE_HORIZONS)
                     for b in ('first', 're1', 're2plus')}

    # V3.6 再入规则（R1/R2）复核：口径实现见 _reentry_buy_audit（与 daily._te_buy_pool_filter 一致）
    reentry_audit = _reentry_buy_audit(df_ev, max_buy, _bucket, cand_top)

    out = {
        'start': start, 'end': end, 'sample_step': sample_step,
        'n_universe': len(codes), 'n_events': int(len(df_ev)),
        'n_dates_rs_cache': len(rs_cache),
        'max_buy_candidates': max_buy,
        'buy_daily_top_groupby': 'decision_date',
        'decision_max_lag': TE_DECISION_MAX_LAG,
        'entry_actual': 'ActualEntry=T+1开盘（无分钟数据，INTRADAY_CONFIRMATION_UNAVAILABLE）',
        'mfe_mae_base': 'MFE/MAE 基准 = ActualEntry(T+1开盘)，窗口 T+1~T+h',
        'action_dist': {k2: int(v) for k2, v in act.value_counts().items()},
        'state_dist': {k2: int(v) for k2, v in est.value_counts().items()},
        'pools': pools,
        'by_execution_state': by_state,
        'by_stock_type': by_type,
        'by_primary_horizon': by_horizon,
        'score_buckets': buckets,
        'proof_vs_hvt': proof,
        'verdict': verdict,
        'decision_point_dist': {k2: int(v) for k2, v in dp.value_counts().items()},
        'by_decision_point': by_dp,
        'buy_pool_by_decision_point': buy_by_dp,
        'buy_pool_by_streak': buy_by_streak,
        'reentry_rule_audit': reentry_audit,
    }
    with open(os.path.join(out_dir, f'te_backtest_{tag}_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    cols = [c for c in TE_CSV_COLUMNS if c in df_ev.columns]
    extra = [c for c in df_ev.columns if c not in cols]
    df_ev[cols + extra].to_csv(os.path.join(out_dir, f'te_backtest_events_{tag}.csv'),
                               index=False, encoding='utf-8-sig')

    print(f"[TE-BT] 完成: 事件 {len(df_ev)}，BUY候选 {int(buy_mask.sum())}，RS缓存 {len(rs_cache)} 日")
    for nm in ('hvt_all', 'buy_pool', 'buy_executable', 'buy_daily_top'):
        p = pools[nm]
        er20 = p.get('er20') or {}
        print(f"  {nm:<15} n={p.get('n', 0):<5} r20win={p.get('r20_win')}% "
              f"r20avg={p.get('r20_avg')}% r60win={p.get('r60_win')}% "
              f"er20win={er20.get('win')}% pf={er20.get('pf')}")
    for dpn in ('BREAKOUT', 'PULLBACK', 'PULLBACK_RE', 'T0'):
        p = buy_by_dp.get(dpn) or {}
        er20 = p.get('er20') or {}
        print(f"  buy@{dpn:<12} n={p.get('n', 0):<5} r20win={p.get('r20_win')}% "
              f"r20avg={p.get('r20_avg')}% er20win={er20.get('win')}% pf={er20.get('pf')}")
    for bn in ('first', 're1', 're2plus'):
        p = buy_by_streak.get(bn) or {}
        er20 = p.get('er20') or {}
        print(f"  buy@streak={bn:<8} n={p.get('n', 0):<5} r20win={p.get('r20_win')}% "
              f"r20avg={p.get('r20_avg')}% er20win={er20.get('win')}% pf={er20.get('pf')}")
    _print_reentry_audit(reentry_audit)
    print(f"  proof({proof_label}): {json.dumps(proof, ensure_ascii=False)}")
    return out


def _is_numeric_col(c: str) -> bool:
    """是否为收益/评分数值列（用于 CSV 重放时强制数值化；'-' 占位会让整列退化为 object）。"""
    for pre in ('er', 'mfe', 'mae', 'r'):
        if c.startswith(pre) and c[len(pre):].isdigit():
            return True
    return c in ('max_gain', 'max_dd', 'execution_score', 'buyability', 'reentry_streak')


def run_from_csv(csv_path: str = None, out_dir: str = None, cfg: dict = None,
                 max_buy: int = None, tag: str = None) -> dict:
    """从已落盘的 te_backtest_events_{tag}.csv 重放 V3.6 再入规则审计（不重跑价格循环）。

    CSV 已含 reentry_streak / decision_point / next_day_action / execution_state /
    execution_score / buyability 与全部收益列，故可在秒级重建 reentry_rule_audit 与
    buy_pool_by_streak，并写回 te_backtest_{tag}_summary.json（复用 run_te_backtest 的同一
    审计函数 _reentry_buy_audit，两处口径严格一致）。
    """
    if cfg is None:
        cfg = _load_config()
    te_cfg = cfg.get('trade_execution') or {}
    if max_buy is None:
        max_buy = int(te_cfg.get('max_buy_candidates', 3))
    out_dir = out_dir or cfg.get('report', {}).get('output_dir',
                                                   os.path.join(BASE_DIR, 'report_daily'))
    csv_path = csv_path or os.path.join(out_dir, 'te_backtest_events_20250101_20260828.csv')
    if not os.path.exists(csv_path):
        print(f'[TE-BT] 找不到重放输入：{csv_path}')
        return {}
    if tag is None:
        base = os.path.basename(csv_path)
        pre, suf = 'te_backtest_events_', '.csv'
        tag = base[len(pre):-len(suf)] if base.startswith(pre) and base.endswith(suf) else 'replay'

    # keep_default_na=False：保留字面量 'NA'/'N/A' 占位字符串（pb_verdict/retest_pass 等）。
    # 否则 pandas 会把它们解析成 NaN，回写时变成空单元格，造成不可逆的数据损坏。
    df_ev = pd.read_csv(csv_path, low_memory=False, keep_default_na=False,
                        dtype={'ts_code': str, 'name': str,
                               'signal_date': str, 'decision_date': str})
    if 'reentry_streak' not in df_ev.columns:
        print('[TE-BT] CSV 缺 reentry_streak 列，无法重放（请先跑一次完整回测生成该列）')
        return {}
    # CSV 中的 '-' 占位会让整列退化为 object；收益/评分列统一强制数值化，保证统计口径与回测一致
    for c in df_ev.columns:
        if _is_numeric_col(c):
            df_ev[c] = pd.to_numeric(df_ev[c], errors='coerce')

    act = df_ev['next_day_action'].fillna('').astype(str)
    buy_mask = act.isin(BUY_ACTIONS)
    # 用与实盘 cand 一致的口径重建 cand_top 与 reentry_streak（不信任 CSV 里旧口径的 streak 列）：
    # 交易日历取 CSV 决策日区间，与回测 det_dates（step=1）一致，保证 prev_trade_date 映射相同。
    det_dates = HvtDataLoader().trade_dates(str(df_ev['decision_date'].min()),
                                            str(df_ev['decision_date'].max()))
    cand_top = _cand_top_mask(df_ev, buy_mask, max_buy)
    _new_streak = _compute_streak(df_ev, cand_top, det_dates).reindex(df_ev.index)
    n_stale = int((pd.to_numeric(df_ev['reentry_streak'], errors='coerce').fillna(0)
                   != _new_streak).sum())
    df_ev['reentry_streak'] = _new_streak
    bucket = _streak_bucket(df_ev)
    reentry_audit = _reentry_buy_audit(df_ev, max_buy, bucket, cand_top)
    buy_by_streak = {b: _pool_stats(df_ev[(bucket == b) & buy_mask], TE_HORIZONS)
                     for b in ('first', 're1', 're2plus')}

    summary_path = os.path.join(out_dir, f'te_backtest_{tag}_summary.json')
    out = {}
    if os.path.exists(summary_path):
        with open(summary_path, encoding='utf-8') as f:
            out = json.load(f)
    out['buy_pool_by_streak'] = buy_by_streak
    out['reentry_rule_audit'] = reentry_audit
    out['reentry_audit_source'] = f'replay:{os.path.basename(csv_path)}'
    out['reentry_streak_definition'] = ('连续处于每日 cand 池（execution_state ∈ '
                                        'READY_BUY/PULLBACK_BUY 且 action ∈ BUY_ACTIONS 的 '
                                        'Top-max_buy）的决策日数，与 daily._te_buy_pool_filter '
                                        '的 cand 同源；重放时按此口径重算，不采用 CSV 旧列')
    out['reentry_streak_rebuilt'] = n_stale
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    if n_stale:  # 同步修正 CSV 的 streak 列，避免台账里留着旧口径的陈旧值
        df_ev.to_csv(csv_path, index=False, encoding='utf-8-sig')

    print(f'[TE-BT] CSV 重放：{os.path.basename(csv_path)} → 事件 {len(df_ev)}，'
          f'BUY候选 {int(buy_mask.sum())}，streak 重算修正 {n_stale} 行，'
          f'写回 {os.path.basename(summary_path)}')
    for bn in ('first', 're1', 're2plus'):
        p = buy_by_streak.get(bn) or {}
        er20 = p.get('er20') or {}
        print(f"  buy@streak={bn:<8} n={p.get('n', 0):<5} r20win={p.get('r20_win')}% "
              f"r20avg={p.get('r20_avg')}% er20win={er20.get('win')}% pf={er20.get('pf')}")
    _print_reentry_audit(reentry_audit)
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=None)
    ap.add_argument('--step', type=int, default=1)
    ap.add_argument('--max-events', type=int, default=None)
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--max-buy', type=int, default=None)
    ap.add_argument('--from-csv', nargs='?', const='', default=None,
                    help='重放模式：读取已落盘的 te_backtest_events_{tag}.csv 重建再入规则审计'
                         '（可跟 CSV 路径，省略则用 out-dir 下的默认文件）')
    ap.add_argument('--tag', default=None, help='重放模式写回的 summary tag')
    args = ap.parse_args()
    if args.from_csv is not None:
        run_from_csv(csv_path=(args.from_csv or None), out_dir=args.out_dir,
                     max_buy=args.max_buy, tag=args.tag)
    else:
        run_te_backtest(start=args.start, end=args.end, sample_step=args.step,
                        max_events=args.max_events, out_dir=args.out_dir)
