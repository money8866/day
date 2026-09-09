# -*- coding: utf-8 -*-
"""TE + T20_ROCKET 多源候选 → 个股量价二次筛选与最高胜率交易排序 V1.0

输入: report_daily/hvt_bull_{date}.json
  - te_buy_pool                    → SOURCE = TE_BUY
  - events 前20 中 state=T20_ROCKET_WATCH → SOURCE = T20_ROCKET
  - 同码同日出现 → SOURCE = BOTH

三大原则:
  1) TE_BUY 不是自动 BUY，必须重新验证量价与可执行性
  2) T20_ROCKET 不是短线 BUY，ES/XP 仅作参考因子
  3) 量价二筛决定"现在该不该买"；宁可 PRIMARY BUY = 0，不为凑数制造 BUY

输出: 终端精简汇总 + report_daily/te_rocket_filter_{date}.md

用法: python -m hvt_bull.te_rocket_filter --date 20260908
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.data_loader import HvtDataLoader
from hvt_bull.future_expansion import _atr14

REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')

VPQ_STATES = ('HEALTHY_ADVANCE', 'HEALTHY_PULLBACK', 'ACCUMULATION',
              'NEUTRAL', 'WEAK_VOLUME', 'DISTRIBUTION', 'PANIC_SELLING')
PATTERNS = ('HEALTHY_PULLBACK', 'CONFIRMED_BREAKOUT', 'HIGH_EXTENSION', 'DISTRIBUTION_RISK')
DECISIONS = ('PRIMARY BUY', 'CONDITIONAL BUY', 'WAIT_RETEST', 'WAIT_BREAKOUT',
             'WATCH_ROCKET', 'WATCH_HIGH_EXTENSION', 'AVOID')
READINESS = ('EARLY', 'PRE_BREAKOUT', 'BREAKOUT_READY', 'EXPANSION', 'EXTENDED', 'FAILED')
_READINESS_SCORE = {'BREAKOUT_READY': 90, 'PRE_BREAKOUT': 80, 'EXPANSION': 70,
                    'EARLY': 55, 'EXTENDED': 35, 'FAILED': 10}


def _f(v, default=np.nan):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- 候选构建

def build_candidates(report: dict) -> list:
    """从报告 JSON 抽取 TE_BUY + T20_ROCKET 两类候选，同码合并为 BOTH。"""
    cands = {}
    for p in (report.get('te_buy_pool') or []):
        code = p.get('ts_code')
        if not code:
            continue
        cands[code] = {
            'ts_code': code, 'name': p.get('name'), 'source': 'TE_BUY',
            't0_date': p.get('t0_date'), 'sector': p.get('sector_name'),
            'state_orig': p.get('state'),
            'es': _f(p.get('execution_score')), 'xp': None,
            'te': {'entry_trigger': _f(p.get('entry_trigger')),
                   'buy_zone_low': _f(p.get('buy_zone_low')),
                   'buy_zone_high': _f(p.get('buy_zone_high')),
                   'stop_loss': _f(p.get('stop_loss')), 'target1': _f(p.get('target1')),
                   'next_day_action': p.get('next_day_action'),
                   'execution_reason': p.get('execution_reason'),
                   'reentry_streak': p.get('reentry_streak'),
                   'te_decision_point': p.get('te_decision_point')},
        }
    for ev in (report.get('events') or []):
        if ev.get('state') != 'T20_ROCKET_WATCH':
            continue
        code = ev.get('ts_code')
        if not code:
            continue
        t20 = {'es': _f(ev.get('execution_score') if ev.get('execution_score') is not None
                        else ev.get('es')),
               'xp': _f(ev.get('expansion_score') if ev.get('expansion_score') is not None
                        else ev.get('xp'))}
        if code in cands:
            cands[code]['source'] = 'BOTH'
            cands[code]['xp'] = t20['xp']
            cands[code]['t20'] = t20
        else:
            cands[code] = {
                'ts_code': code, 'name': ev.get('name'), 'source': 'T20_ROCKET',
                't0_date': ev.get('t0_date'), 'sector': ev.get('sector_name'),
                'state_orig': ev.get('state'), 'es': t20['es'], 'xp': t20['xp'],
                'te': None, 't20': t20,
            }
    return list(cands.values())


# ---------------------------------------------------------------- 量价特征

def compute_features(df: pd.DataFrame) -> dict:
    """基于决策日截面的量价特征（独立计算，不复用原模型结论）。"""
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    v = df['vol']
    close = _f(c.iloc[-1])
    chg = _f(df['pct_chg'].iloc[-1]) if 'pct_chg' in df else (close / _f(c.iloc[-2], close) - 1) * 100

    v5 = _f(v.iloc[-6:-1].mean()) if len(v) >= 6 else _f(v.tail(5).mean())
    v20 = _f(v.tail(20).mean())
    vol_ratio = close and v5 and _f(v.iloc[-1]) / v5 if v5 > 0 else np.nan

    upper = (max(_f(h.iloc[-1]), close) - max(_f(o.iloc[-1]), close)) / close * 100 if close else np.nan
    body_low = min(_f(o.iloc[-1]), close)
    lower = (body_low - _f(l.iloc[-1])) / close * 100 if close else np.nan
    hl = _f(h.iloc[-1]) - _f(l.iloc[-1])
    close_pos = (close - _f(l.iloc[-1])) / hl if hl > 0 else 0.5

    ma20 = _f(c.rolling(20, min_periods=10).mean().iloc[-1])
    ma20_prev = _f(c.rolling(20, min_periods=10).mean().iloc[-6]) if len(c) >= 26 else ma20
    ma60 = _f(c.rolling(60, min_periods=30).mean().iloc[-1])
    atr = _atr14(df)

    r5 = (close / _f(c.iloc[-6], close) - 1) * 100
    r10 = (close / _f(c.iloc[-11], close) - 1) * 100
    r20 = (close / _f(c.iloc[-21], close) - 1) * 100
    r60 = (close / _f(c.iloc[-61], close) - 1) * 100

    high20 = _f(h.tail(20).max())
    high20_prev = _f(h.iloc[-21:-1].max()) if len(h) >= 21 else high20
    low10 = _f(l.tail(10).min())
    low60 = _f(l.tail(60).min())

    dev20_atr = (close - ma20) / atr if atr and atr > 0 else np.nan
    ma20_slope = ma20 - ma20_prev

    max_drop20 = min((_f(df['pct_chg'].iloc[i], 0) for i in range(max(0, len(df) - 20), len(df))), default=0)

    return dict(close=close, chg=chg, vol=vol_ratio, vol_trend=(v5 / v20 if v20 > 0 else np.nan),
                upper_shadow=upper, lower_shadow=lower, close_pos=close_pos,
                ma20=ma20, ma60=ma60, ma20_slope=ma20_slope, atr=atr,
                dev20_atr=dev20_atr, r5=r5, r10=r10, r20=r20, r60=r60,
                high20=high20, high20_prev=high20_prev, low10=low10, low60=low60,
                dist_high20=(close / high20 - 1) * 100 if high20 else np.nan,
                dist_high20_prev=(close / high20_prev - 1) * 100 if high20_prev else np.nan,
                dist_low10=(close / low10 - 1) * 100 if low10 else np.nan,
                max_drop20=max_drop20)


# ---------------------------------------------------------------- VPQ 状态

def score_vpq(feat: dict):
    """VPQ Score 0~100 与七类状态判定。"""
    chg, vr = _f(feat['chg']), _f(feat['vol'])
    cs, us = _f(feat['close_pos']), _f(feat['upper_shadow'])
    dev, ma20, ma60 = _f(feat['dev20_atr']), _f(feat['ma20']), _f(feat['ma60'])
    r5, r20, close = _f(feat['r5']), _f(feat['r20']), _f(feat['close'])

    if chg > 0:
        if 1.0 <= vr <= 2.5:
            s_vp = 30
        elif vr > 2.5:
            s_vp = 18
        else:
            s_vp = 15
    else:
        s_vp = 24 if vr < 0.9 else 5

    s_st = (12 if close > ma20 else 0) + (8 if ma20 > ma60 else 0) + (5 if close > ma60 else 0)

    s_mo = 0
    if 0 < r5 <= 15:
        s_mo += 8
    if 0 < r20 <= 30:
        s_mo += 7
    if r5 < 0 and r20 < 0:
        s_mo = 0
    if dev > 2.5:
        s_mo = max(0, s_mo - 10)

    s_k = (8 if cs > 0.6 else 4 if cs > 0.4 else 0)
    s_k += 7 if us < 1.5 else (3 if us < 3 else 0)

    md = _f(feat['max_drop20'])
    s_dd = 10 if md > -4 else (5 if md > -7 else 0)

    score = _clip(s_vp + s_st + s_mo + s_k + s_dd, 0, 100)

    above20 = close > ma20 * 0.995
    if chg <= -5 and vr >= 1.8:
        state = 'PANIC_SELLING'
    elif (not above20) and chg <= -4 and vr >= 1.5:
        state = 'PANIC_SELLING'
    elif (vr >= 1.5 and abs(chg) <= 1.0 and us >= 2.0) \
            or (vr >= 1.8 and chg <= -2.0) \
            or (dev >= 2.0 and us >= 2.5 and vr >= 1.3):
        state = 'DISTRIBUTION'
    elif vr <= 0.55 and abs(chg) <= 1.0:
        state = 'WEAK_VOLUME'
    elif chg < 0 and vr <= 0.85 and above20 and feat['ma20_slope'] >= 0:
        state = 'HEALTHY_PULLBACK'
    elif (chg < 0 and vr <= 0.85) or (abs(chg) <= 1.0 and vr <= 0.9 and feat['ma20_slope'] >= 0):
        state = 'ACCUMULATION'
    elif chg >= 1.5 and 0.9 <= vr <= 3.0 and dev <= 2.5:
        state = 'HEALTHY_ADVANCE'
    else:
        state = 'NEUTRAL'
    return score, state


def classify_pattern(feat: dict, state: str):
    """四种量价形态识别，返回形态名或 None。"""
    vr, chg = _f(feat['vol']), _f(feat['chg'])
    dev, r20 = _f(feat['dev20_atr']), _f(feat['r20'])
    if state in ('DISTRIBUTION', 'PANIC_SELLING'):
        return 'DISTRIBUTION_RISK'
    if feat['dist_high20_prev'] >= -0.5 and vr >= 1.5 and chg >= 2:
        return 'CONFIRMED_BREAKOUT'
    if dev >= 2.5 or r20 >= 30:
        return 'HIGH_EXTENSION'
    if state == 'HEALTHY_PULLBACK' and dev <= 1.2:
        return 'HEALTHY_PULLBACK'
    return None


# ---------------------------------------------------------------- T1/T3

_T1_ADJ = {'HEALTHY_ADVANCE': 12, 'HEALTHY_PULLBACK': 8, 'ACCUMULATION': 5, 'NEUTRAL': 0,
           'WEAK_VOLUME': -8, 'DISTRIBUTION': -15, 'PANIC_SELLING': -25}
_T3_ADJ = {'HEALTHY_ADVANCE': 10, 'HEALTHY_PULLBACK': 7, 'ACCUMULATION': 6, 'NEUTRAL': 0,
           'WEAK_VOLUME': -5, 'DISTRIBUTION': -12, 'PANIC_SELLING': -18}


def estimate_t1_t3(feat: dict, state: str):
    """独立估计 T+1 / T+3 延续概率（0~100，仅量价与结构，不含 ES/XP）。"""
    t1 = 50 + _T1_ADJ.get(state, 0)
    t3 = 55 + _T3_ADJ.get(state, 0)
    if _f(feat['close']) > _f(feat['ma20']):
        t1 += 5
        t3 += 5
    if _f(feat['ma20']) > _f(feat['ma60']):
        t1 += 4
        t3 += 4
    if _f(feat['dev20_atr']) > 2.5:
        t1 -= 10
        t3 -= 10
    if feat['dist_high20_prev'] >= -1:
        t1 += 5
    if _f(feat['r20']) >= 30:
        t3 -= 5
    return _clip(t1, 5, 95), _clip(t3, 5, 95)


# ---------------------------------------------------------------- 执行层

def _exec_te(te: dict, feat: dict):
    """TE_BUY 源执行层：使用 te_buy_pool 的触发/买区/止损/目标重新验证。"""
    close = _f(feat['close'])
    trig = _f(te.get('entry_trigger'))
    zl, zh = _f(te.get('buy_zone_low')), _f(te.get('buy_zone_high'))
    stop, target = _f(te.get('stop_loss')), _f(te.get('target1'))
    if not trig:
        trig = zh if zh else _f(feat['close'])
    atr = _f(feat.get('atr')) or close * 0.02
    if not target or target <= 0:
        target = max(close, trig) + 2 * atr

    confirmed = close >= trig
    touched = _f(_feat_high(feat)) >= trig
    confirmation = 'CONFIRMED' if confirmed else ('TOUCHED' if touched else 'PENDING')

    if zh and zl and zl <= close <= zh:
        entry_state = 'READY'
    elif close > (zh if zh else trig) and close <= trig * 1.05:
        entry_state = 'NEAR'
    elif close > trig * 1.05:
        entry_state = 'CHASE'
    else:
        entry_state = 'READY'

    stop_dist = (close - stop) / close * 100 if stop and close else np.nan
    risk = _risk_level(stop_dist, feat)

    entry_price = close if entry_state == 'READY' else trig
    rr = (target - entry_price) / (entry_price - stop) \
        if target and stop and entry_price > stop else np.nan
    return dict(trigger=trig, stop=stop, target1=target, buy_zone=(zl, zh),
                confirmation=confirmation, entry_state=entry_state,
                stop_dist=stop_dist, rr=rr, risk=risk, entry_price=entry_price)


def _feat_high(feat: dict):
    """今日盘中高点近似：用 close_pos 反推不可行，直接由调用方传入 df 时补充。

    为保持特征字典单一来源，此处以 close*(1+0.01) 作为保守近似不可取——
    故在 compute_features 中已存入 today_high。"""
    return feat.get('today_high', feat['close'])


def _risk_level(stop_dist, feat: dict):
    dev = _f(feat['dev20_atr'], 0)
    sd = _f(stop_dist, 99)
    if sd < 2 or dev > 3:
        return 'HIGH'
    if sd < 3.5 or dev > 2.5:
        return 'ELEVATED'
    return 'ACCEPTABLE'


def _exec_t20(feat: dict):
    """T20_ROCKET 源执行层：触发=前20日高，结构止损+量度目标自算。"""
    close = _f(feat['close'])
    trig = _f(feat['high20_prev'])
    atr = _f(feat['atr']) or close * 0.02
    stop = _f(feat['low10']) * 0.99
    if stop and close and close / stop - 1 > 0.08:
        stop = _f(feat['ma20']) * 0.985
    target = max(close, trig) + 2 * atr

    confirmed = close >= trig * 0.995
    touched = _f(feat.get('today_high', close)) >= trig
    confirmation = 'CONFIRMED' if confirmed else ('TOUCHED' if touched else 'PENDING')

    dist = feat['dist_high20_prev']
    dev = _f(feat['dev20_atr'])
    if dev >= 3:
        entry_state = 'CHASE'
    elif confirmed or dist >= -1:
        entry_state = 'READY'
    elif dist >= -3:
        entry_state = 'NEAR'
    else:
        entry_state = 'FAR'

    entry_price = close if confirmed else trig
    stop_dist = (close - stop) / close * 100 if close and stop else np.nan
    rr = (target - entry_price) / (entry_price - stop) \
        if entry_price and stop and entry_price > stop else np.nan
    risk = _risk_level(stop_dist, feat)
    return dict(trigger=trig, stop=stop, target1=target, buy_zone=(None, None),
                confirmation=confirmation, entry_state=entry_state,
                stop_dist=stop_dist, rr=rr, risk=risk, entry_price=entry_price)


# ---------------------------------------------------------------- Readiness

def compute_readiness(feat: dict):
    """ROCKET_READINESS 六档（仅 T20_ROCKET / BOTH 源有意义）。

    已突破（距前20日高 ≥ -0.5%）按扩张度分流：dev≥1.5ATR → EXPANSION，刚破未扩张 → BREAKOUT_READY；
    未破贴新高（≥-1%）且量能蓄势 → BREAKOUT_READY；贴近未破（≥-5%）→ PRE_BREAKOUT。"""
    close, ma20, ma60 = _f(feat['close']), _f(feat['ma20']), _f(feat['ma60'])
    dev, r20 = _f(feat['dev20_atr']), _f(feat['r20'])
    if close < ma60 * 0.97 or (ma60 and close < _f(feat['low60']) * 1.02):
        return 'FAILED'
    if dev >= 2.5 or r20 >= 35:
        return 'EXTENDED'
    dist = feat['dist_high20_prev']
    if dist >= -0.5:
        return 'EXPANSION' if dev >= 1.5 else 'BREAKOUT_READY'
    if dist >= -1 and _f(feat['vol']) >= 1.2:
        return 'BREAKOUT_READY'
    if dist >= -5:
        return 'PRE_BREAKOUT'
    if dev >= 1.5:
        return 'EXPANSION'
    return 'EARLY'


# ---------------------------------------------------------------- 决策矩阵

def decide(row: dict):
    """七类决策矩阵。TE 源可到 PRIMARY BUY；T20 源最高 CONDITIONAL BUY。

    扩张型风险（dev>3ATR / HIGH_EXTENSION）→ WATCH_HIGH_EXTENSION 而非 AVOID；
    NEAR + CONFIRMED 表示已突破但脱离买区 → WAIT_RETEST 等回踩。"""
    st, pat = row['vpq_state'], row['pattern']
    ex, src = row['exec'], row['source']
    dev = _f((row.get('feat') or {}).get('dev20_atr'), 0)
    if row.get('no_data'):
        return 'AVOID', '数据缺失，无法验证'

    if src in ('TE_BUY', 'BOTH'):
        if st == 'PANIC_SELLING':
            return 'AVOID', '恐慌抛售'
        if ex['entry_state'] == 'CHASE':
            return ('WATCH_HIGH_EXTENSION', '追高禁止，等回踩') \
                if pat == 'HIGH_EXTENSION' or dev > 3 else ('AVOID', '追高禁止')
        if st == 'DISTRIBUTION':
            return 'AVOID', '放量滞涨，派发嫌疑'
        if pat == 'HIGH_EXTENSION' or dev > 3:
            return 'WATCH_HIGH_EXTENSION', '高位扩张，不追，等回踩或放量突破确认'
        if ex['risk'] == 'HIGH':
            return 'AVOID', '风险过高（止损过近或波动失控）'
        gates_ok = (ex['confirmation'] == 'CONFIRMED' or
                    (ex['confirmation'] == 'TOUCHED' and row['feat']['close_pos'] >= 0.5)) \
            and ex['entry_state'] == 'READY' and ex['risk'] == 'ACCEPTABLE' \
            and (np.isfinite(ex['rr']) and ex['rr'] >= 2)
        if st in ('HEALTHY_ADVANCE',) and pat == 'CONFIRMED_BREAKOUT' and gates_ok:
            return 'PRIMARY BUY', '放量突破确认+买区内+风险可控+R/R达标'
        if gates_ok and st in ('HEALTHY_ADVANCE', 'HEALTHY_PULLBACK', 'ACCUMULATION') \
                and (pat in ('CONFIRMED_BREAKOUT', 'HEALTHY_PULLBACK') or row['t1'] >= 60):
            return 'PRIMARY BUY', '量价健康+可执行门槛全过'
        if ex['entry_state'] == 'NEAR' and ex['confirmation'] == 'CONFIRMED':
            return 'WAIT_RETEST', '已突破触发位但脱离买区，等回踩买区确认'
        if ex['entry_state'] == 'READY' and ex['risk'] != 'HIGH' \
                and (np.isfinite(ex['rr']) and ex['rr'] >= 1.8) \
                and st in ('HEALTHY_ADVANCE', 'HEALTHY_PULLBACK', 'ACCUMULATION', 'NEUTRAL'):
            return 'CONDITIONAL BUY', '基本可执行，需次日确认（竞价/盘中不破止损）'
        if pat == 'HEALTHY_PULLBACK' or st == 'ACCUMULATION':
            return 'WAIT_RETEST', '缩量回踩/蓄势，等待企稳或触发位确认'
        if ex['entry_state'] == 'NEAR':
            return 'WAIT_BREAKOUT', '贴近触发位，等待放量突破'
        if st == 'WEAK_VOLUME':
            return 'WAIT_RETEST', '量能不足，等待放量'
        return 'WAIT_RETEST', '未满足买入门槛，观察'

    # T20_ROCKET 源
    rd = row.get('readiness')
    if rd == 'FAILED' or st == 'PANIC_SELLING':
        return 'AVOID', '火箭逻辑失败/恐慌抛售'
    if rd == 'EXTENDED' or pat == 'HIGH_EXTENSION':
        return 'WATCH_HIGH_EXTENSION', '扩张过度，宁可错过也不追'
    if st == 'DISTRIBUTION':
        return 'AVOID', '放量滞涨，派发嫌疑'
    if rd == 'BREAKOUT_READY' and _f(row['feat']['vol']) >= 1.5 and _f(row['feat']['chg']) >= 2:
        if np.isfinite(_f(ex.get('rr'), np.nan)) and ex['rr'] >= 1.5:
            return 'CONDITIONAL BUY', '贴新高+放量启动+赔率达标（R/R≥1.5），确认后可短线参与'
        return 'WATCH_ROCKET', '放量启动但赔率不足（R/R<1.5），等回踩低吸'
    return 'WATCH_ROCKET', f"火箭观察（{rd}），等突破确认或回踩低吸"


def grade(row: dict):
    """FINAL EXECUTION 四级分级。"""
    d = row['decision']
    ex = row['exec']
    if d == 'PRIMARY BUY' and ex['confirmation'] in ('CONFIRMED', 'TOUCHED') \
            and ex['entry_state'] == 'READY' and ex['risk'] == 'ACCEPTABLE' \
            and np.isfinite(ex['rr']) and ex['rr'] >= 2:
        return 'A', '明日直接执行'
    if d in ('CONDITIONAL BUY', 'WAIT_RETEST', 'WAIT_BREAKOUT'):
        return 'B', '确认后执行'
    if d in ('WATCH_ROCKET', 'WATCH_HIGH_EXTENSION'):
        return 'C', '重点观察（T20_ROCKET）'
    return 'D', '禁止交易'


# ---------------------------------------------------------------- 排名

def rank_rows(rows: list):
    """三大排名分离：QualityRank / ShortTradeRank / RocketRank。"""
    for r in rows:
        feat, ex = r['feat'], r['exec']
        st_score = 0.35 * r['t1'] + 0.15 * r['t3'] + 0.20 * r['vpq']
        st_score += {'READY': 15, 'NEAR': 8}.get(ex['entry_state'], 0)
        st_score += {'CONFIRMED': 10, 'TOUCHED': 5}.get(ex['confirmation'], 0)
        if r['vpq_state'] in ('DISTRIBUTION', 'PANIC_SELLING') or r['pattern'] == 'HIGH_EXTENSION':
            st_score -= 10
        if ex['entry_state'] == 'CHASE':
            st_score -= 15
        r['short_trade_score'] = st_score

        struct = (40 if _f(feat['close']) > _f(feat['ma20']) else 0) \
            + (30 if _f(feat['ma20']) > _f(feat['ma60']) else 0) \
            + (30 if _f(feat['r20']) > 0 else 0)
        q = 0.15 * (r['es'] or 0) + 0.15 * (r['xp'] or 0) + 0.35 * r['vpq'] + 0.20 * struct + 0.15 * r['t3']
        if r['vpq_state'] in ('DISTRIBUTION', 'PANIC_SELLING'):
            q -= 15
        r['quality_score'] = q

        rd = r.get('readiness')
        rk = _READINESS_SCORE.get(rd, 55) + 0.2 * (r['xp'] or 0) + 0.2 * r['t3']
        if rd in ('EXTENDED', 'FAILED') or r['vpq_state'] in ('DISTRIBUTION', 'PANIC_SELLING'):
            rk -= 15
        r['rocket_score'] = rk

    quality_order = sorted(rows, key=lambda r: -r['quality_score'])
    trade_order = sorted(rows, key=lambda r: -r['short_trade_score'])
    rocket_order = sorted(rows, key=lambda r: -r['rocket_score'])
    for i, r in enumerate(quality_order):
        r['quality_rank'] = i + 1
    for i, r in enumerate(trade_order):
        r['short_trade_rank'] = i + 1
    for i, r in enumerate(rocket_order):
        r['rocket_rank'] = i + 1
    return quality_order, trade_order, rocket_order


# ---------------------------------------------------------------- 主流程

def _load_df(loader: HvtDataLoader, cand: dict, trade_date: str) -> pd.DataFrame:
    end = datetime.strptime(trade_date, '%Y%m%d')
    start = (end - timedelta(days=500)).strftime('%Y%m%d')
    return loader.load(cand['ts_code'], start, trade_date)


def run_filter(trade_date: str, verbose: bool = True) -> dict:
    """执行量价二筛，写 MD 报告并返回结果 dict。"""
    path = os.path.join(REPORT_DIR, f'hvt_bull_{trade_date}.json')
    with open(path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    cands = build_candidates(report)
    loader = HvtDataLoader()
    rows = []
    for cand in cands:
        df = _load_df(loader, cand, trade_date)
        row = dict(cand)
        row['te'] = cand.get('te')
        if df is None or len(df) < 30:
            row.update(no_data=True, feat={}, vpq=None, vpq_state='NEUTRAL', pattern=None,
                       t1=0, t3=0, exec=None, readiness=None, decision='AVOID',
                       why='数据缺失', grade='D', grade_note='禁止交易')
            rows.append(row)
            continue
        df = df.tail(130).reset_index(drop=True)
        feat = compute_features(df)
        feat['today_high'] = _f(df['high'].iloc[-1])
        vpq, state = score_vpq(feat)
        pattern = classify_pattern(feat, state)
        t1, t3 = estimate_t1_t3(feat, state)
        ex = _exec_te(cand['te'], feat) if cand['te'] else _exec_t20(feat)
        readiness = compute_readiness(feat) if cand['source'] in ('T20_ROCKET', 'BOTH') else None
        row.update(no_data=False, feat=feat, vpq=vpq, vpq_state=state, pattern=pattern,
                   t1=t1, t3=t3, exec=ex, readiness=readiness)
        row['decision'], row['why'] = decide(row)
        row['grade'], row['grade_note'] = grade(row)
        rows.append(row)

    quality_order, trade_order, rocket_order = rank_rows(rows)
    trade_order = [r for r in trade_order if not r.get('no_data')] + \
                  [r for r in trade_order if r.get('no_data')]

    summary = {
        'trade_date': trade_date,
        'n_cand': len(rows),
        'by_source': {s: sum(1 for r in rows if r['source'] == s) for s in ('TE_BUY', 'T20_ROCKET', 'BOTH')},
        'by_decision': {d: sum(1 for r in rows if r['decision'] == d) for d in DECISIONS},
        'by_grade': {g: sum(1 for r in rows if r.get('grade') == g) for g in 'ABCD'},
        'top3': [r['ts_code'] for r in trade_order[:3]],
    }
    md = render_markdown(rows, summary, trade_order)
    md_path = os.path.join(REPORT_DIR, f'te_rocket_filter_{trade_date}.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)

    if verbose:
        render_console(rows, summary, trade_order, md_path)
    return dict(rows=rows, summary=summary, md_path=md_path,
                quality_order=quality_order, trade_order=trade_order, rocket_order=rocket_order)


# ---------------------------------------------------------------- 输出

def _pct(v, nd=1):
    return '—' if v is None or not np.isfinite(_f(v, np.nan)) else f'{_f(v):.{nd}f}%'


def render_markdown(rows, summary, trade_order) -> str:
    d = summary['trade_date']
    L = [f"# TE + T20_ROCKET 多源候选 → 量价二次筛选与最高胜率交易排序（{d}）", '']
    L.append('> 原则：TE_BUY 不是自动 BUY；T20_ROCKET 不是短线 BUY（ES/XP 仅作参考）；量价二筛决定"现在该不该买"。'
             '宁可 PRIMARY BUY = 0，不为凑数制造 BUY。', )
    L.append('')

    L += ['## 一、多源候选 → 量价二筛汇总', '',
          f"- 候选总数 **{summary['n_cand']}**：TE_BUY {summary['by_source']['TE_BUY']} 只 / "
          f"T20_ROCKET {summary['by_source']['T20_ROCKET']} 只 / BOTH {summary['by_source']['BOTH']} 只",
          '- 决策分布：' + ' / '.join(f"{k} **{v}**" for k, v in summary['by_decision'].items()),
          '- FINAL EXECUTION：' + ' / '.join(f"{g}级 **{summary['by_grade'][g]}**" for g in 'ABCD'), '',
          '| 代码 | 名称 | SOURCE | VPQ | 状态 | 形态 | T1 | T3 | Conf | Entry | Risk | R/R | Readiness | 决策 |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in sorted(rows, key=lambda x: x['ts_code']):
        ex = r['exec'] or {}
        L.append('| {ts} | {name} | {src} | {vpq} | {st} | {pat} | {t1} | {t3} | {cf} | {en} | {rk} | {rr} | {rd} | **{dec}** |'.format(
            ts=r['ts_code'], name=r.get('name') or '—', src=r['source'],
            vpq=r['vpq'] if r['vpq'] is not None else '—', st=r['vpq_state'], pat=r['pattern'] or '—',
            t1=f"{r['t1']:.0f}" if not r.get('no_data') else '—',
            t3=f"{r['t3']:.0f}" if not r.get('no_data') else '—',
            cf=ex.get('confirmation', '—'), en=ex.get('entry_state', '—'),
            rk=ex.get('risk', '—'), rr=_pct(ex.get('rr')) if np.isfinite(_f(ex.get('rr'), np.nan)) else '—',
            rd=r.get('readiness') or '—', dec=r['decision']))
    L.append('')

    L += ['## 二、TOP TRADE（按 ShortTradeRank：未来 1~3 天最值得交易）', '',
          '| 排名 | 代码 | 名称 | SOURCE | 评分 | VPQ | T1 | T3 | 决策 | 一句话理由 |',
          '|---|---|---|---|---|---|---|---|---|---|']
    for i, r in enumerate(trade_order, 1):
        L.append(f"| {i} | {r['ts_code']} | {r.get('name') or '—'} | {r['source']} | "
                 f"{r['short_trade_score']:.1f} | {r['vpq_state']} | {r['t1']:.0f} | {r['t3']:.0f} | "
                 f"**{r['decision']}** | {r['why']} |")
    L.append('')

    L += ['## 三、次日最高胜率 TOP3', '']
    for i, r in enumerate(trade_order[:3], 1):
        ex, ft = r['exec'] or {}, r['feat']
        L.append(f"### TOP{i} · {r['ts_code']} {r.get('name') or ''}（{r['source']}）")
        L.append(f"- **类型**：{r['vpq_state']}" + (f" / {r['pattern']}" if r['pattern'] else '')
                 + (f" / Readiness={r['readiness']}" if r.get('readiness') else ''))
        L.append(f"- **T1 延续**：{r['t1']:.0f}　**VPQ**：{r['vpq']:.0f}（{r['vpq_state']}）")
        if ft:
            L.append(f"- **结构**：距前20日高 {r['feat']['dist_high20_prev']:.1f}%，"
                     f"偏离MA20 {_pct(_f(ft['dev20_atr']) * 100 / 1 if False else _f(ft['dev20_atr']) * 1, 2)} ATR，"
                     f"量比 {_pct(_f(ft['vol']), 2).replace('%', '')}（现价/5日均量）")
        if ex:
            L.append(f"- **Confirmation**：{ex['confirmation']}　**Entry**：{ex['entry_state']}"
                     f"（触发位 {_pct(ex['trigger'], 2).replace('%', '')}）")
            L.append(f"- **STOP**：{_pct(ex['stop'], 2).replace('%', '')}（距离 {_pct(ex.get('stop_dist'), 1)}）"
                     f"　**R/R**：{_pct(ex['rr'], 2).replace('%', '')}　**Risk**：{ex['risk']}")
        L.append(f"- **决策**：{r['decision']} —— {r['why']}")
        L.append(f"- **为什么排第{i}**：ShortTradeRank 第 {r['short_trade_rank']}"
                 f"（T1 {r['t1']:.0f} / 量价状态 {r['vpq_state']} / 入场 {ex.get('entry_state', '—')}"
                 f" / 确认 {ex.get('confirmation', '—')}）")
        L.append('')

    L += ['## 四、原模型 vs 二筛变化表', '',
          '| 代码 | 名称 | SOURCE | 原结论 | 二筛结论 | 变化 |',
          '|---|---|---|---|---|---|']
    for r in sorted(rows, key=lambda x: x['ts_code']):
        orig = (r.get('te') or {}).get('next_day_action') if r['source'] in ('TE_BUY', 'BOTH') else None
        if not orig:
            orig = r.get('state_orig') or '—'
        lvl = {'PRIMARY BUY': 3, 'CONDITIONAL BUY': 2, 'WAIT_RETEST': 1, 'WAIT_BREAKOUT': 1,
               'WATCH_ROCKET': 1, 'WATCH_HIGH_EXTENSION': 0, 'AVOID': -1}
        cur = lvl.get(r['decision'], 0)
        old = 2 if r['source'] in ('TE_BUY', 'BOTH') else 1
        chg = '↑ 升级' if cur > old else ('↓ 降级' if cur < old else '→ 维持')
        L.append(f"| {r['ts_code']} | {r.get('name') or '—'} | {r['source']} | {orig} | "
                 f"**{r['decision']}** | {chg} |")
    L.append('')

    L += ['## 五、FINAL EXECUTION', '']
    for g, title in (('A', 'A级 —— 明日直接执行（Confirmation=TRUE / Entry=READY / Risk=ACCEPTABLE / R/R≥2）'),
                     ('B', 'B级 —— 确认后执行'),
                     ('C', 'C级 —— T20_ROCKET 重点观察'),
                     ('D', 'D级 —— 禁止交易')):
        sel = [r for r in rows if r.get('grade') == g]
        L.append(f"### {title}（{len(sel)} 只）")
        if not sel:
            L.append('- 无')
        for r in sel:
            ex = r['exec'] or {}
            L.append(f"- **{r['ts_code']} {r.get('name') or ''}**（{r['source']}）：{r['decision']} —— {r['why']}"
                     + (f"｜触发 {_f(ex.get('trigger'), 0):.2f}｜止损 {_f(ex.get('stop'), 0):.2f}"
                        f"｜R/R {_f(ex.get('rr'), 0):.2f}" if ex else ''))
        L.append('')

    L += ['---', '',
          f"*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　"
          f"数据：{summary['n_cand']} 只候选，量价二筛 V1.0*"]
    return '\n'.join(L)


def render_console(rows, summary, trade_order, md_path):
    d = summary['trade_date']
    print(f"\n===== 量价二筛 V1.0（{d}） =====")
    print(f"候选 {summary['n_cand']} 只：TE_BUY {summary['by_source']['TE_BUY']}"
          f" / T20_ROCKET {summary['by_source']['T20_ROCKET']}"
          f" / BOTH {summary['by_source']['BOTH']}")
    print('决策分布：' + ' / '.join(f"{k} {v}" for k, v in summary['by_decision'].items() if v))
    print('FINAL EXECUTION：' + ' / '.join(f"{g}级 {summary['by_grade'][g]}" for g in 'ABCD'))
    print('\nTOP TRADE（ShortTradeRank）:')
    for i, r in enumerate(trade_order, 1):
        ex = r['exec'] or {}
        rr = f"{ex['rr']:.2f}" if ex and np.isfinite(_f(ex.get('rr'), np.nan)) else '—'
        print(f"  {i}. {r['ts_code']} {r.get('name') or ''} [{r['source']}] "
              f"T1={r['t1']:.0f} VPQ={r['vpq'] if r['vpq'] is not None else '—'} {r['vpq_state']} "
              f"Entry={ex.get('entry_state', '—')} Conf={ex.get('confirmation', '—')} "
              f"R/R={rr} → {r['decision']}（{r['why']}）")
    print(f"\n完整报告: {md_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    run_filter(args.date)


if __name__ == '__main__':
    main()
