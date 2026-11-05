# -*- coding: utf-8 -*-
"""ZZ2000 三仓信号引擎（底仓 / 均线趋势仓 / 波段交易仓）

设计原则：
- 全部阈值集中在本文件的规则表与 config.py，可复算、可审计。
- 任何输入缺失 -> 相关结论返回 None 并记入 unknowns，绝不推断。
- 避免仅凭单日金叉/大阳线追涨：单日金叉、单日大阳线只压制 BUY，不制造 BUY。
- 本模块只输出「研究目标敞口」，不代表用户实际持仓，也不代替下单。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from . import config as C

U = 'UNKNOWN'


def _f(x) -> Optional[float]:
    """把任意输入安全转成有限浮点数；否则返回 None。"""
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def r5(x: Optional[float]) -> Optional[float]:
    """仓位按 5pp 粒度取整（研究预算口径，非精确下单量）。"""
    return None if x is None else float(round(x / 5.0) * 5.0)


def compute(ctx: dict) -> dict:
    """根据 build_ctx() 组装的上下文计算全部信号。"""
    out = {
        'date': ctx['date'], 'code': ctx['code'], 'ok': False,
        'unknowns': list(ctx.get('integrity_missing', [])),
        'ma': {}, 'sleeves': {'base': None, 'trend': None, 'swing': None},
        'total': None, 'setups': {}, 'gates': {}, 'notes': [],
        'levels': {}, 'triggers': [], 'invalidation': [], 'risks': [], 'watch': [],
    }
    bars: pd.DataFrame = ctx['bars']
    idx: pd.DataFrame = ctx['idx']

    # ---------------- 数据完整性硬门槛 ----------------
    need = C.MIN_BARS + max(C.MA_SLOPE_LOOKBACK.values())
    if bars.empty or len(bars) < need:
        out['unknowns'].append(f'ETF 前复权收盘序列不足 {need} 根（实际 {len(bars)} 根），MA20/60/120 及斜率无法确认')
    elif bars['trade_date'].iloc[-1] != ctx['date']:
        out['unknowns'].append(f'ETF 缺 {ctx["date"]} 当日行情（缓存最新 {bars["trade_date"].iloc[-1]}）')
    q_all = pd.to_numeric(bars.get('qfq_close'), errors='coerce')
    if q_all.isna().any():
        out['unknowns'].append('ETF 前复权因子存在缺失/UNKNOWN，均线与偏离口径未确认')
    if idx.empty or idx['trade_date'].iloc[-1] != ctx['date']:
        out['unknowns'].append(f'中证2000 指数缺 {ctx["date"]} 收盘行情，系统性风险与基准无法确认')
    if not ctx.get('cache_ok'):
        out['unknowns'].append('当日全市场个股日线缓存不完整，市场广度与昨涨停次日表现无法交叉核对')
    if ctx.get('adj_status') not in ('OK', None):
        out['unknowns'].append(f'ETF 复权因子状态 {ctx.get("adj_status")}，复权口径未确认')

    price_broken = bool(out['unknowns'])
    if price_broken:
        out['notes'].append('价格/完整性数据不达标：不给出任何精确仓位或交易价位，动作强制 NO TRADE。')
        out['action'] = 'NO TRADE'
        out['spike_flag'] = None
        out['single_day_cross'] = None
        out['signal_key'] = 'PRICE_INCOMPLETE'
        for m in ctx.get('sentiment_missing', []):
            out['unknowns'].append(m)
        return out

    # 情绪类缺失是「软缺失」：不阻断价格结论，但相关过滤标 UNKNOWN
    for m in ctx.get('sentiment_missing', []):
        out['unknowns'].append(m)

    q = q_all.to_numpy(dtype=float)
    close = float(bars['close'].iloc[-1])
    open_ = _f(bars['open'].iloc[-1])
    high = _f(bars['high'].iloc[-1])
    low = _f(bars['low'].iloc[-1])
    pct = _f(bars['pct_chg'].iloc[-1])
    vol = _f(bars['vol'].iloc[-1])
    amount_qian = _f(bars['amount'].iloc[-1])       # 千元
    vol_arr = pd.to_numeric(bars['vol'], errors='coerce').to_numpy(dtype=float)
    amt_yi = None if amount_qian is None else amount_qian / 100000.0

    # ---------------- 均线 / 斜率 / 偏离 ----------------
    ma_series, ma, slope, dev = {}, {}, {}, {}
    for n in C.MA_WINDOWS:
        s = pd.Series(q).rolling(n).mean()
        ma_series[n] = s
        k = C.MA_SLOPE_LOOKBACK[n]
        v = _f(s.iloc[-1])
        prev = _f(s.iloc[-1 - k])
        ma[n], slope[n] = v, ((v / prev - 1) * 100 if (v and prev) else None)
        dev[n] = ((q[-1] / v - 1) * 100 if v else None)
        out['ma'][n] = {'value': v, 'slope_pct': slope[n], 'dev_pct': dev[n],
                        'slope_lookback': k}
    m20, m60, m120 = (ma[n] for n in (20, 60, 120))
    if m20 > m60 > m120:
        align, align_score = '多头排列', 1.0
    elif m20 < m60 < m120:
        align, align_score = '空头排列', 0.0
    elif m20 > m60:
        align, align_score = '部分转强(MA20>MA60)', 0.6
    elif m20 < m60:
        align, align_score = '部分转弱(MA20<MA60)', 0.2
    else:
        align, align_score = '均线纠缠', 0.4
    out['alignment'] = align

    vol_ma_series = pd.Series(vol_arr).rolling(C.VOL_MA_WINDOW).mean()
    vol_ma20 = _f(vol_ma_series.iloc[-1])
    vol_ratio = (vol / vol_ma20) if (vol is not None and vol_ma20) else None
    out['volume'] = {'vol': vol, 'vol_ma20': vol_ma20, 'vol_ratio': vol_ratio,
                     'amount_yi': amt_yi}

    # ---------------- 单日金叉 / 大阳线追涨抑制 ----------------
    prev_ma20 = _f(ma_series[20].iloc[-2])
    single_day_cross = bool(prev_ma20 and q[-2] <= prev_ma20 and q[-1] > m20)
    spike_flag = bool(pct is not None and pct > C.CHASE_PCT_CHG
                      and dev[20] is not None and dev[20] > C.CHASE_DEV_MA20)
    out['spike_flag'], out['single_day_cross'] = spike_flag, single_day_cross
    if single_day_cross:
        out['notes'].append('今日为单日上穿 MA20（金叉首日），按规则不据此新增敞口，需次日站稳确认。')
    if spike_flag:
        out['notes'].append(f'今日涨幅 {pct:.2f}% 且偏离 MA20 {dev[20]:.2f}%，命中追涨抑制：波段 A/C 不启用。')

    # ---------------- 情绪指标与过滤 ----------------
    sent = ctx['sent']
    yzt = ctx['yzt']
    up_ratio = _f(sent.get('up_ratio'))
    zt = _f(sent.get('zt_count'))
    dtn = _f(sent.get('dt_count'))
    yzt_avg = _f(yzt.get('avg_pct')) if yzt.get('status') == 'OK' else None

    def gate(v, ok_fn):
        return U if v is None else bool(ok_fn(v))

    gates = {
        '全市场上涨占比>=%.0f%%' % (C.SENT_UP_RATIO_MIN * 100): gate(up_ratio, lambda v: v >= C.SENT_UP_RATIO_MIN),
        '涨停家数>=%d' % C.SENT_ZT_MIN: gate(zt, lambda v: v >= C.SENT_ZT_MIN),
        '跌停家数<=%d' % C.SENT_DT_MAX: gate(dtn, lambda v: v <= C.SENT_DT_MAX),
        '昨日涨停股次日平均涨跌幅>%.1f%%' % C.YZT_AVG_MIN: gate(yzt_avg, lambda v: v > C.YZT_AVG_MIN),
    }
    out['gates'] = gates
    unknown_gates = [k for k, v in gates.items() if v == U]
    failed_gates = [k for k, v in gates.items() if v is False]
    for k in unknown_gates:
        out['unknowns'].append(f'情绪过滤「{k}」= UNKNOWN（数据缺失），该项过滤未生效')
    out['gates_unknown'] = unknown_gates
    out['gates_failed'] = failed_gates

    # ---------------- 底仓（MA120 趋势 + 系统性风险） ----------------
    slope_state = ('up' if slope[120] > C.MA120_SLOPE_FLAT_BAND else
                   'down' if slope[120] < -C.MA120_SLOPE_FLAT_BAND else 'flat')
    pos_state = 'above' if q[-1] > m120 else 'below'
    base = C.BASE_TABLE[(pos_state, slope_state)]
    idx_close = pd.to_numeric(idx['close'], errors='coerce').to_numpy(dtype=float)
    idx_ret20 = ((idx_close[-1] / idx_close[-21] - 1) * 100) if len(idx_close) > 21 else None
    sys_hits = []
    if dtn is not None and dtn >= C.SYSRISK_DT_COUNT:
        sys_hits.append(f'跌停 {int(dtn)} 家 >= {C.SYSRISK_DT_COUNT}')
    if up_ratio is not None and up_ratio < C.SYSRISK_UP_RATIO:
        sys_hits.append(f'上涨占比 {up_ratio*100:.1f}% < {C.SYSRISK_UP_RATIO*100:.0f}%')
    if idx_ret20 is not None and idx_ret20 <= C.SYSRISK_RET20:
        sys_hits.append(f'指数20日涨跌幅 {idx_ret20:.2f}% <= {C.SYSRISK_RET20}%')
    out['sysrisk'] = {'hits': sys_hits, 'idx_ret20_pct': idx_ret20,
                      'unknown': (dtn is None or up_ratio is None or idx_ret20 is None)}
    if sys_hits:
        base = min(base, C.SYSRISK_BASE_CAP)
        out['notes'].append('系统性风险触发：' + '；'.join(sys_hits) + f'，底仓压到 {C.SYSRISK_BASE_CAP:.0f}% 以内。')
    if out['sysrisk']['unknown']:
        out['unknowns'].append('系统性风险判定所需指标不全，底仓的「系统性风险过滤」结论无法确认')

    # ---------------- 趋势仓（位置 / 排列 / 斜率） ----------------
    pos_score = np.mean([q[-1] > m20, q[-1] > m60, q[-1] > m120])
    slope_score = np.mean([slope[n] > 0 for n in C.MA_WINDOWS if slope[n] is not None])
    trend_raw = C.BUDGET_TREND * (C.TREND_WEIGHTS['position'] * pos_score
                                 + C.TREND_WEIGHTS['alignment'] * align_score
                                 + C.TREND_WEIGHTS['slope'] * slope_score)
    out['trend_scores'] = {'position': float(pos_score), 'alignment': align_score,
                           'slope': float(slope_score), 'raw': float(trend_raw)}
    trend = r5(trend_raw)

    # ---------------- 波段仓（三个形态） ----------------
    bull = m20 > m60 > m120
    vol_ratio_series = vol_arr / vol_ma_series.to_numpy()
    look = C.PULLBACK_LOOKBACK
    pullback = False
    for i in range(-1 - look, -1):
        vr = vol_ratio_series[i]
        m = _f(ma_series[20].iloc[i])
        if (vr is not None and np.isfinite(vr) and vr <= C.PULLBACK_VOL_RATIO
                and m is not None and q[i] <= m):
            pullback = True
            break
    restand = bool(q[-1] > m20 and q[-1] > q[-2]
                   and vol_ratio is not None and vol_ratio >= 0.90
                   and dev[20] is not None and dev[20] <= C.SWING_EXT_DEV_MA20)
    A = bool(bull and slope[20] is not None and slope[20] > 0
             and pullback and restand and not spike_flag)

    ret5 = (q[-1] / q[-6] - 1) * 100 if len(q) > 6 else None
    sp, st = ctx.get('sent_prev', {}), sent
    prev_dt = _f(sp.get('dt_count'))
    prev_up = _f(sp.get('up_ratio'))
    today_up = up_ratio
    panic_before = bool((prev_dt is not None and prev_dt >= C.PANIC_PREV_DT)
                        or (prev_up is not None and prev_up < 0.25))
    exhaust = bool(dtn is not None and prev_dt is not None and dtn < prev_dt
                   and today_up is not None and today_up > 0.5)
    repair = bool(pct is not None and pct > C.PANIC_MIN_REBOUND
                  and open_ is not None and close > open_
                  and vol_ratio is not None and vol_ratio >= 1.0)
    B = bool(ret5 is not None and ret5 <= C.PANIC_DROP5 and panic_before
             and exhaust and repair and not spike_flag)

    pl = C.PLATFORM_LOOKBACK
    plat = bars['close'].iloc[-1 - pl:-1].astype(float)
    plat_range = (plat.max() / plat.min() - 1) if len(plat) and plat.min() > 0 else None
    prior_spike = bool(np.nanmax(vol_ratio_series[-1 - pl:-1]) >= C.BREAKOUT_VOL_RATIO) if len(vol_ratio_series) > pl else False
    breakout = bool(plat_range is not None and q[-1] > float(plat.max()) * 1.005
                    and vol_ratio is not None and vol_ratio >= C.BREAKOUT_VOL_RATIO
                    and pct is not None and pct > 0)
    Cflag = bool(bull and slope[60] is not None and slope[60] > 0
                 and plat_range is not None and plat_range <= C.PLATFORM_MAX_RANGE
                 and prior_spike and breakout and not spike_flag)

    out['setups'] = {
        'A': {'hit': A, 'budget': C.SWING_A_MAX, 'detail':
              f'多头排列={bull}，MA20斜率={"%.2f%%" % slope[20] if slope[20] is not None else U}，'
              f'近{look}日缩量回踩={pullback}，今日再站稳={restand}'},
        'B': {'hit': B, 'budget': C.SWING_B_MAX, 'detail':
              f'近5日涨跌={"%.2f%%" % ret5 if ret5 is not None else U}，前日恐慌={panic_before}，'
              f'跌停回落/广度回升={exhaust}，放量修复={repair}'},
        'C': {'hit': Cflag, 'budget': C.SWING_C_MAX, 'detail':
              f'平台振幅={"%.2f%%" % (plat_range*100) if plat_range is not None else U}，'
              f'平台内曾放量={prior_spike}，今日二次放量破平台高={breakout}'},
    }
    swing_raw = sum(v['budget'] for v in out['setups'].values() if v['hit'])
    for _ in failed_gates:
        swing_raw *= C.SENT_HAIRCUT
    out['swing_raw'] = swing_raw
    swing = r5(min(swing_raw, C.BUDGET_SWING))
    if not any(v['hit'] for v in out['setups'].values()):
        out['notes'].append('三类波段形态均未触发，波段仓为 0（条件性仓位，不强行配置）。')

    out['sleeves'] = {'base': r5(base), 'trend': trend, 'swing': swing}
    total = r5(min(base + trend + swing, C.MAX_EXPOSURE))
    out['total'] = total

    # ---------------- 关键价位 / 触发 / 失效 ----------------
    out['levels'] = {
        'close': close, 'MA20': m20, 'MA60': m60, 'MA120': m120,
        'today_high': high, 'today_low': low,
        'platform_high': float(plat.max()) if len(plat) else None,
    }
    weak = q[-1] <= m20
    if weak:
        out['triggers'] = [
            f'收盘价重新站上 MA20（{m20:.3f} 元）并连续 2 个交易日站稳（不以单日金叉为准）',
            f'MA20 斜率由负转正（当前 {"%.2f%%" % slope[20] if slope[20] is not None else U}/5日）',
            f'MA60 / MA120 斜率改善（当前 {"%.2f%%" % slope[60] if slope[60] is not None else U}/10日、'
            f'{"%.2f%%" % slope[120] if slope[120] is not None else U}/20日）',
            f'量能确认：量比 >= 1.0 且不出现放量下跌（今日 {amt_yi:.2f} 亿元、量比 '
            f'{"%.2f" % vol_ratio if vol_ratio is not None else U}）' if amt_yi else '量能确认（成交额 UNKNOWN）',
            f'情绪过滤四项全部 PASS：上涨占比 >= {C.SENT_UP_RATIO_MIN*100:.0f}%、涨停 >= {C.SENT_ZT_MIN}、'
            f'跌停 <= {C.SENT_DT_MAX}、昨日涨停股次日平均涨跌幅 > {C.YZT_AVG_MIN:.1f}%',
        ]
        out['invalidation'] = [
            f'收盘价持续低于 MA20（{m20:.3f} 元）且无法收复 → 维持不新增敞口',
            f'跌破今日最低 {low:.3f} 元或再创阶段新低（当前价格偏离 MA120 '
            f'{"%.2f%%" % dev[120] if dev[120] is not None else U}）' if low else '跌破今日最低（UNKNOWN）',
            f'MA120 斜率进一步走弱（当前 {"%.2f%%" % slope[120] if slope[120] is not None else U}/20日）',
            f'系统性风险触发（跌停 >= {C.SYSRISK_DT_COUNT} / 上涨占比 < {C.SYSRISK_UP_RATIO*100:.0f}% / '
            f'指数20日跌幅 <= {C.SYSRISK_RET20}%）或任一情绪过滤转 FAIL / UNKNOWN → 重算',
        ]
    else:
        out['triggers'] = [
            f'收盘价站稳 MA20（{m20:.3f} 元）且 MA20 斜率维持为正',
            f'成交额不出现缩量滞涨（今日 {amt_yi:.2f} 亿元，量比 '
            f'{"%.2f" % vol_ratio if vol_ratio is not None else U}）' if amt_yi else '量能确认（量比 UNKNOWN）',
            f'全市场上涨占比 >= {C.SENT_UP_RATIO_MIN*100:.0f}%、涨停 >= {C.SENT_ZT_MIN}、跌停 <= {C.SENT_DT_MAX}',
            '昨日涨停股次日平均涨跌幅 > %.1f%%（打板赚钱效应不恶化）' % C.YZT_AVG_MIN,
        ]
        out['invalidation'] = [
            f'收盘跌破 MA60（{m60:.3f} 元）或 MA120（{m120:.3f} 元）',
            f'MA120 斜率转负（当前 {"%.2f%%" % slope[120] if slope[120] is not None else U}/20日）',
            f'系统性风险再触发（跌停 >= {C.SYSRISK_DT_COUNT} / 上涨占比 < {C.SYSRISK_UP_RATIO*100:.0f}% / '
            f'指数20日跌幅 <= {C.SYSRISK_RET20}%）',
            '任一情绪过滤转为 UNKNOWN 或关键行情缺失 → 停止新增敞口并重算',
        ]
    out['risks'] = [
        '本报告为研究框架输出，30/40/30 是初始预算假设，未经最优性验证，不代表用户实际持仓，也不代替下单。',
        '中证2000 为小盘宽基，波动与流动性风险高于大盘宽基；ETF 二级市场价格可能偏离基金净值。',
        f'价格口径：收盘价/涨跌幅/成交额用未复权（实际成交价），均线与偏离用前复权；复权因子状态 {ctx.get("adj_status")}，分红记录 {ctx.get("div_status")}。',
        'Tushare 涨跌停池口径与全市场涨跌家数统计口径可能不完全一致（ST、北交所、20% 涨跌幅板差异），两者并列时已分别标注来源。',
        '波段形态为规则化识别，存在形态失败与假突破风险；单日金叉/大阳线不作为加仓依据。',
    ]
    out['watch'] = [
        f'次日能否收在 MA20（{m20:.3f} 元）之上，MA20 斜率是否继续向上',
        '近5日缩量回踩是否延续为「再站稳」（量比是否回到 0.9 以上）',
        f'全市场上涨占比（今日 {"%.1f%%" % (up_ratio*100) if up_ratio is not None else U}）、涨停/跌停家数与炸板率变化',
        f'昨日涨停股次日平均涨跌幅（今日 {"%.2f%%" % yzt_avg if yzt_avg is not None else U}）是否改善',
        'ETF 分红/份额折算公告；若 fund_adj 因子变动需重算前复权序列',
        '数据完整性：当日 ETF / 指数 / 个股日线 / 情绪四项是否全部落库',
    ]

    out['ok'] = True
    out['signal_key'] = '|'.join([
        str(total), str(out['sleeves']['base']), str(out['sleeves']['trend']),
        str(out['sleeves']['swing']), align,
        ''.join('1' if v['hit'] else '0' for v in out['setups'].values()),
        str(spike_flag), str(single_day_cross)])
    return out


def decide(cur: dict, prev: dict | None) -> dict:
    """把当日信号与前一交易日建议比较，产出动作、变化与「信号不变」判定。"""
    d = {'delta': None, 'prev_total': None, 'baseline_origin': None,
         'action': 'NO TRADE', 'signal_unchanged': False, 'action_reason': ''}
    if not cur.get('ok'):
        d['action'] = 'NO TRADE'
        d['action_reason'] = '价格/完整性数据不达标，不产生交易动作（不给出精确仓位或价位）。'
        return d
    if prev is None or not prev.get('ok') or prev.get('total') is None:
        d['action'] = 'NO TRADE'
        d['action_reason'] = ('无前一交易日可比较的建议基线，不推断前值、不生成下单动作；'
                              '本日仅给出研究目标敞口。')
        return d
    p = float(prev['total'])
    t = float(cur['total'])
    d['prev_total'] = p
    d['baseline_origin'] = prev.get('origin', 'live')
    d['delta'] = round(t - p, 1)
    d['signal_unchanged'] = (abs(d['delta']) < C.MIN_REBALANCE_PCT
                             and cur.get('signal_key') == prev.get('signal_key'))
    if cur.get('gates_unknown'):
        d['action'] = 'HOLD' if d['delta'] > 0 else ('REDUCE' if d['delta'] <= -C.ACTION_STEP_PCT else 'HOLD')
        d['action_reason'] = ('情绪过滤存在 UNKNOWN 项，未新增敞口；'
                              '仅允许风险控制方向的减仓，其余维持。')
    elif cur.get('single_day_cross') or cur.get('spike_flag'):
        d['action'] = 'HOLD' if d['delta'] > 0 else ('REDUCE' if d['delta'] <= -C.ACTION_STEP_PCT else 'HOLD')
        d['action_reason'] = '命中单日金叉/追涨抑制，需次日确认，不执行 BUY。'
    elif t <= 0:
        d['action'] = 'NO TRADE'
        d['action_reason'] = '目标敞口为 0。'
    elif d['delta'] >= C.ACTION_STEP_PCT:
        d['action'] = 'BUY'
        d['action_reason'] = f'目标敞口较前一交易日 +{d["delta"]:.0f}pp，且趋势/情绪过滤通过。'
    elif d['delta'] <= -C.ACTION_STEP_PCT:
        d['action'] = 'REDUCE'
        d['action_reason'] = f'目标敞口较前一交易日 {d["delta"]:.0f}pp，按规则降敞口。'
    else:
        d['action'] = 'HOLD'
        d['action_reason'] = '目标敞口变化小于 5pp，维持当前研究建议。'
    return d
