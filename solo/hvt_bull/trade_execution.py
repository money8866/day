# -*- coding: utf-8 -*-
"""HVT-BULL V3.5 Trade Execution 增量层

定位（规格§0/§35）：
  HVT = 事件发现 → Future Expansion = 未来扩张 → Candidate Pool → Trade Execution = 明天是否值得成交
  核心区分：FE（未来空间）≠ BUYABILITY（当前交易条件）≠ EXECUTION（明日执行决策）。

约束：
  - 只读增量：只读取 V3.0 双评分 / V3.1 FE / V3.4 回踩 / build_trade_plan 价格体系，
    不修改任何原有状态、评分、排序、否决与候选池；可整体关闭（enabled=false 输出与 V3.1 完全一致）。
  - 价格全部由现有规则导出（t0_high × confirm_ratio / stop_loss / atr14），禁止虚构。
  - 无分钟数据：INTRADAY_CONFIRMATION_UNAVAILABLE，VWAP 分量给中性分，不伪造盘中确认（§37.11/12）。

回答五个问题（§35）：
  1. 明天买不买     → EXECUTION_STATE / NEXT_DAY_ACTION
  2. 买哪一只       → EXECUTION_SCORE 排序，BUY 候选 <= max_buy_candidates(3)
  3. 什么价格买     → ENTRY_TRIGGER / BUY_ZONE / INVALIDATION / NO_CHASE_LEVEL
  4. 什么情况不买   → WHY_NOT_BUY / OPEN_PLAYBOOK / 硬风控覆盖
  5. 按 T20/60/120 管 → PRIMARY_HORIZON
"""

import math

import numpy as np

from hvt_bull.future_expansion import _atr14

# 关闭增量层时需从 JSON 中过滤的全部新增字段（与 models.py V3.5 字段一一对应）
TE_JSON_FIELDS = (
    'buyability', 'buyability_parts', 'execution_score', 'execution_state',
    'next_day_action', 'entry_trigger', 'buy_zone_low', 'buy_zone_high',
    'invalidation', 'no_chase_level', 'position_size', 'initial_position',
    'primary_horizon', 'stock_type', 'confirmation_level', 'execution_reason',
    'why_not_buy', 'open_playbook', 'intraday_available',
)

_TE_FLOATS = ('buyability', 'execution_score', 'entry_trigger', 'buy_zone_low',
              'buy_zone_high', 'invalidation', 'no_chase_level')
_TE_STRS = ('execution_state', 'next_day_action', 'position_size', 'initial_position',
            'primary_horizon', 'stock_type', 'confirmation_level', 'execution_reason')


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _stock_type(ev, fe20, fe60, cont, ext):
    """§22 四类：不惩罚过去涨幅，惩罚未来继续上涨的条件恶化。"""
    tg = _f(ev.trend_gain)
    if ev.expansion_type == 'DISTRIBUTION_RISK':
        return 'LATE_STAGE'
    if tg >= 150 and cont < 60 and ext >= 65:
        return 'LATE_STAGE'
    if tg >= 150 and cont >= 70 and ext < 70:
        return 'EXTENDED_CONTINUATION'
    if str(ev.lifecycle or '') == 'EARLY':
        return 'NEW_TREND'
    if cont >= 65 and (fe20 >= 65 or fe60 >= 65):
        return 'RE_ACCELERATION'
    return 'NEW_TREND'


def _primary_horizon(ev, stock_type, fe20, fe60, fe120):
    """§23：Lifecycle 决定主战场周期；LATE_STAGE 不进 PRIMARY。"""
    if stock_type == 'LATE_STAGE':
        return ''
    lc = str(ev.lifecycle or '')
    if lc == 'EARLY':
        return 'T120' if fe120 >= fe60 else 'T60'
    if lc == 'DEVELOPING':
        return 'T60' if fe60 >= fe20 else 'T20'
    return 'T20' if fe20 >= fe60 else 'T60'   # MATURE / EXTENDED / RE_ACCEL


def _breakout_readiness(ev, has_breakout, close, days_since_break, t0_high):
    """§12：突破确认度 0~100。"""
    if ev.false_breakout:
        return 25.0
    if has_breakout:
        base = 70.0
        if _f(ev.breakout_close_pos) >= 0.75 and _f(ev.breakout_turnover_ratio) >= 1.3:
            base = 92.0
        elif _f(ev.breakout_close_pos) >= 0.60:
            base = 78.0
        if close < t0_high:                       # 重新跌回平台高点下方
            base -= 25.0
        elif days_since_break >= 5 and close >= t0_high * 1.02:
            base = min(96.0, base + 3.0)          # 突破后站稳多日
        return _clamp(base)
    if ev.state == 'BREAKOUT_READY':
        return 62.0
    if ev.locked_chip:
        return 58.0
    if ev.state in ('LOCKED', 'LOCKING'):
        return 50.0
    return 40.0


def _price_location(close, trigger, atr, stock_type, cont):
    """§13：价格越接近有效突破位、风险越明确，越适合执行；
    强延续股（EXTENDED/RE_ACCEL 且 Cont≥70）容忍度加倍——不简单认为涨得多就差。"""
    if trigger <= 0 or atr <= 0:
        return 50.0
    tol = 1.0
    if stock_type in ('EXTENDED_CONTINUATION', 'RE_ACCELERATION') and cont >= 70:
        tol = 2.0
    if close <= trigger:                          # 触发价下方（未突破 / 回踩到位）
        gap_atr = (trigger - close) / atr
        if gap_atr <= 0.5:
            return 88.0
        if gap_atr <= 1.5:
            return 74.0
        return 52.0
    dist_pct = (close / trigger - 1.0) * 100.0 / tol
    if dist_pct <= 1.0:
        return 85.0
    if dist_pct <= 3.0:
        return 65.0
    if dist_pct <= 6.0:
        return 40.0
    return 20.0


def _pullback_quality(ev):
    """§14：复用 V3.4 回踩判定（GOOD=缩量≤0.8+守住T0_High+收复突破收盘），不另起炉灶。"""
    v = getattr(ev, 'pb_verdict', 'NA') or 'NA'
    if v == 'GOOD':
        return 95.0 if _f(ev.pb_shrink_ratio, 1.0) <= 0.60 else 90.0
    if v == 'NEAR':
        return 68.0
    if v == 'POOR':
        return 28.0
    return 50.0


def _volume_quality(ev, has_breakout, df):
    """§15：健康放量（突破放量/回踩缩量）加分；异常爆炸/冲高回落减分。"""
    if has_breakout:
        br = _f(ev.breakout_turnover_ratio, np.nan) if ev.breakout_turnover_ratio else np.nan
        if not math.isfinite(br):
            base = 55.0
        elif br > 5.0:
            base = 35.0                            # 成交量异常爆炸（§9）
        elif br >= 1.3:
            base = 85.0                            # 健康突破放量
        elif br >= 1.0:
            base = 62.0
        else:
            base = 45.0                            # 突破无量
        if math.isfinite(_f(ev.pb_shrink_ratio, np.nan)) and _f(ev.pb_shrink_ratio, 1.0) < 0.8:
            base = min(95.0, base + 8.0)           # 回踩缩量
    else:
        vr = _f(ev.vol_5d_ratio, 1.0)
        base = 80.0 if vr <= 0.70 else (70.0 if vr <= 1.0 else 50.0)   # 锁筹缩量健康
    try:                                           # 当日冲高回落（长上影风险放量）
        hi = float(df['high'].iloc[-1])
        cl = float(df['close'].iloc[-1])
        if hi > 0 and cl < hi * 0.96:
            base -= 12.0
    except Exception:
        pass
    return _clamp(base, 10.0, 100.0)


def _risk_reward(ev):
    if ev.entry > 0 and ev.stop_loss > 0 and ev.target1 > ev.entry:
        risk = ev.entry - ev.stop_loss
        rr = (ev.target1 - ev.entry) / risk if risk > 0 else 0.0
        if rr >= 3.0:
            return 90.0
        if rr >= 2.0:
            return 75.0
        if rr >= 1.5:
            return 60.0
        return 40.0
    return 50.0


def _fe_riskadj(horizon, fe20, fe60, fe120, fe_score, cont, ext, expansion_type):
    """§24：Future Expansion RiskAdj。第一版无 T*_RA，用 FE×(Continuation/ExtRisk) 调制。"""
    fe = {'T20': fe20, 'T60': fe60, 'T120': fe120}.get(horizon)
    if not math.isfinite(_f(fe, np.nan)):
        fe = fe_score
    adj = _f(fe)
    if adj <= 0:
        return 0.0
    if cont >= 85:
        adj *= 1.05
    elif cont < 60:
        adj *= 0.80
    if ext >= 70:
        adj *= 0.85
    elif ext >= 60:
        adj *= 0.95
    if expansion_type == 'DISTRIBUTION_RISK':
        adj *= 0.70
    return _clamp(adj)


_LIFECYCLE_FIT = {('EARLY', 'T120'): 85.0, ('EARLY', 'T60'): 78.0,
                  ('DEVELOPING', 'T60'): 80.0, ('DEVELOPING', 'T20'): 72.0,
                  ('MATURE', 'T20'): 82.0, ('MATURE', 'T60'): 75.0}


def _position(exec_state, action, score, te_cfg):
    """§20：A+ 8~12% / A 5~8% / B 3~5% / C 0~3%；首次建仓 = 目标×initial_ratio，分批确认。"""
    if exec_state == 'SKIP' or action in ('NO_CHASE', 'WAIT', 'WATCH', 'SKIP'):
        return '-', '-'
    pos = te_cfg.get('position') or {}
    init_r = _f(te_cfg.get('initial_ratio', 0.4), 0.4)
    if score >= 85 and exec_state == 'READY_BUY' and action == 'BUY':
        band, g = pos.get('a_plus', [8, 12]), 'A+'
    elif score >= 75:
        band, g = pos.get('a', [5, 8]), 'A'
    elif score >= 65:
        band, g = pos.get('b', [3, 5]), 'B'
    elif score >= 50:
        band, g = pos.get('c', [0, 3]), 'C'
    else:
        return '-', '-'
    band = [float(x) for x in (band if isinstance(band, (list, tuple)) else [0, 3])]
    target = f"{g} {band[0]:g}%~{band[1]:g}%"
    initial = f"首次{band[0] * init_r:.1f}%~{band[1] * init_r:.1f}%（分批确认后加仓）"
    return target, initial


def _confirmation_level(exec_state, action, intraday_ok):
    if exec_state == 'BREAKOUT_WAIT':
        return 'NEED_PLATFORM_BREAK'
    if exec_state == 'PULLBACK_BUY':
        return 'NEED_VWAP_RECLAIM' if intraday_ok else 'NEED_RECLAIM_CONFIRM'
    if action == 'BUY_ON_CONFIRM':
        return 'NEED_OPEN_CONFIRM'
    if exec_state == 'NO_CHASE':
        return 'WAIT_PULLBACK_TO_ZONE'
    if exec_state == 'WAIT_CONFIRM':
        return 'NEED_MORE_EVIDENCE'
    return 'CONFIRMED' if action == 'BUY' else 'N/A'


def _open_playbook(exec_state, action, p, te_cfg, intraday_ok):
    """§17：次日四种开盘情况的执行预案。"""
    tr, zl, zh, inv, ncl = p['trigger'], p['zone_low'], p['zone_high'], p['invalidation'], p['no_chase_level']
    tgt, init = p['position'], p['initial']
    vwap_note = '' if intraday_ok else '（无分钟数据：VWAP确认不可用，以量价与收盘位替代 §37.11）'
    if action in ('BUY', 'BUY_ON_CONFIRM'):
        return [
            f"A 正常高开[-1%,+3%]：不破{inv:.2f}且量价健康 → 分批执行，{init}{vwap_note}",
            f"B 高开>+5%：默认不追，等回踩 {zl:.2f}~{zh:.2f} 缩量企稳再接",
            f"C 低开：缩量下探不破{inv:.2f}并重新走强 → PULLBACK_BUY",
            f"D 低开放量跌破{inv:.2f}且无法收复 → 撤销（SKIP）"]
    if exec_state == 'BREAKOUT_WAIT':
        return [
            f"A 未突破：观望，触发价 {tr:.2f}（平台高点×confirm_ratio）",
            f"B 放量突破{tr:.2f}（量≥1.3x、收盘位≥0.75、非长上影）→ 确认后转 BUY",
            f"C 冲高未破位回落 → 继续 WAIT，不追盘中急拉",
            f"D 放量跌破{inv:.2f}（假突破/跌回平台）→ 移出观察池"]
    if exec_state == 'PULLBACK_BUY':
        return [
            f"A 开盘续跌但缩量、不破{inv:.2f} → 在 {zl:.2f}~{zh:.2f} 分批接（回踩买点）",
            f"B 直接放量上攻 → 不追高，越过{ncl:.2f}放弃当日",
            f"C 低开放量跌破{inv:.2f}不收复 → 回踩失败，SKIP",
            f"D 横盘缩量 → 等重新走强确认{vwap_note}"]
    if exec_state == 'NO_CHASE':
        return [
            f"A 任何高开：不追（NO_CHASE_LEVEL={ncl:.2f}，§9 绝对禁止追涨）",
            f"B 回落至 {zl:.2f}~{zh:.2f} 且缩量企稳 → 重新评估",
            f"C 低开放量跌破{inv:.2f} → SKIP",
            f"D 缩量横盘 → WAIT，等新买点"]
    if exec_state == 'SKIP':
        return ["A 开盘任一情况：不参与（硬风控覆盖/结构破坏）",
                f"B 若后续重新站稳 {tr:.2f} 且量价健康 → 重新走 TE 评估",
                "C 观察是否出现新的 HVT 事件",
                "D 无操作预案"]
    return [   # WAIT_CONFIRM
        f"A 突破{tr:.2f}且量≥1.3x、收盘位≥0.75 → 升级 BUY_ON_CONFIRM",
        f"B 高开>+5% 或急拉 → NO_CHASE",
        f"C 回踩 {zl:.2f}~{zh:.2f} 缩量不破{inv:.2f} → PULLBACK_BUY",
        f"D 放量跌破{inv:.2f} → SKIP"]


def compute_trade_execution(df, ev, te_cfg, confirm_ratio=1.01):
    """Trade Execution 增量层主入口（只读，不修改 ev）。

    Args:
        df: 截至决策日的日线（调用方保证无未来数据）
        ev: HvtEvent（须已完成 V3.0 评分 / V3.1 FE / build_trade_plan）
        te_cfg: config.trade_execution 节
        confirm_ratio: 与 engine breakout.confirm_ratio 同源（平台突破触发系数）
    Returns:
        dict：TE_JSON_FIELDS 全字段（buyability_parts/why_not_buy/open_playbook 为容器）
    """
    out = {k: (0.0 if k in _TE_FLOATS else '' if k in _TE_STRS else
               False if k == 'intraday_available' else [] if k in ('why_not_buy', 'open_playbook') else {})
           for k in TE_JSON_FIELDS}
    if df is None or len(df) < 60 or ev is None:
        return out
    intraday_ok = False                            # 无分钟数据源：不伪造 VWAP（§37.12）

    try:
        close = float(df['close'].iloc[-1])
    except Exception:
        return out
    if not math.isfinite(close) or close <= 0:
        return out

    atr = _f(ev.atr14)
    if atr <= 0:
        atr = _f(_atr14(df))
    if atr <= 0:
        atr = close * 0.03                         # 极端兜底（正常路径不会走到）

    t0_high = _f(ev.t0_high)
    if t0_high <= 0:
        return out
    pbl = t0_high * float(confirm_ratio)           # 平台突破触发位（与 engine 同源）

    has_breakout = bool(ev.breakout_date) and not ev.false_breakout
    days_since_break = -1
    if ev.breakout_date:
        try:
            arr = df['trade_date'].astype(str).to_numpy()
            m = np.where(arr == str(ev.breakout_date))[0]
            if len(m):
                days_since_break = len(df) - 1 - int(m[0])
        except Exception:
            pass

    # ---- FE / Continuation / ExtRisk 取数（只读） ----
    fe20, fe60, fe120 = _f(ev.fe20), _f(ev.fe60), _f(ev.fe120)
    fe_score = _f(ev.fe_score)
    cont, ext = _clamp(_f(ev.continuation_score)), _clamp(_f(ev.extension_risk))
    stock_type = _stock_type(ev, fe20, fe60, cont, ext)
    horizon = _primary_horizon(ev, stock_type, fe20, fe60, fe120)

    # ---- 价格体系（全部由现有规则导出，禁止虚构） ----
    trigger = _f(ev.entry) if _f(ev.entry) > 0 else pbl
    invalidation = _f(ev.stop_loss) if _f(ev.stop_loss) > 0 else max(t0_high * 0.95, trigger - 1.2 * atr)
    no_chase_level = pbl + _f(te_cfg.get('no_chase_atr', 1.2)) * atr
    zone_atr = _f(te_cfg.get('zone_atr', 0.5))
    zone_low = max(trigger - zone_atr * atr, trigger * 0.99)
    zone_high = trigger + zone_atr * atr

    # ---- BUYABILITY 八分量（§11；intraday 无数据 → 中性50 + 标记） ----
    w = (te_cfg.get('weights') or {}).get('buyability') or {}
    s_brk = _breakout_readiness(ev, has_breakout, close, days_since_break, t0_high)
    s_loc = _price_location(close, trigger, atr, stock_type, cont)
    s_pbk = _pullback_quality(ev)
    s_vol = _volume_quality(ev, has_breakout, df)
    s_int = 50.0                                    # INTRADAY_CONFIRMATION_UNAVAILABLE
    sec_s = _clamp(_f(ev.sector_strength, 50.0), 0.0, 100.0) if ev.sector_strength else 50.0
    rs20 = _f(ev.rs20, np.nan)
    s_mkt = (80.0 if rs20 >= 70 else 60.0 if rs20 >= 50 else 40.0) if math.isfinite(rs20) else 60.0
    s_rr = _risk_reward(ev)
    parts = {'breakout_readiness': s_brk, 'price_location': s_loc, 'pullback_quality': s_pbk,
             'volume_quality': s_vol, 'intraday_structure': s_int, 'sector_confirmation': sec_s,
             'market_regime': s_mkt, 'risk_reward': s_rr}
    buyability = _clamp(sum(_f(w.get(k, dflt)) * v for k, v, dflt in
                            zip(parts.keys(), parts.values(),
                                (0.30, 0.20, 0.15, 0.10, 0.10, 0.05, 0.05, 0.05))))

    # ---- EXECUTION_SCORE（§24） ----
    we = (te_cfg.get('weights') or {}).get('execution_score') or {}
    fe_adj = _fe_riskadj(horizon, fe20, fe60, fe120, fe_score, cont, ext, ev.expansion_type)
    lifec_fit = _LIFECYCLE_FIT.get((str(ev.lifecycle or ''), horizon), 55.0)
    if stock_type == 'LATE_STAGE':
        lifec_fit = 20.0
    exec_score = (_f(we.get('buyability', 0.40)) * buyability
                  + _f(we.get('fe_riskadj', 0.25)) * fe_adj
                  + _f(we.get('continuation', 0.15)) * cont
                  + _f(we.get('price_rr', 0.10)) * 0.5 * (s_loc + s_rr)
                  + _f(we.get('lifecycle_fit', 0.05)) * lifec_fit
                  + _f(we.get('sector_market', 0.05)) * max(sec_s, s_mkt))
    exec_score = _clamp(exec_score)

    # ---- SKIP 硬覆盖（§10/§25：硬风控优先于任何评分） ----
    skip_reasons = []
    if ev.state in ('DISTRIBUTION', 'FAILED', 'EXIT'):
        skip_reasons.append(f"结构状态{ev.state}（硬风控覆盖）")
    if ev.false_breakout:
        skip_reasons.append('假突破（突破失败）')
    if ev.hard_veto:
        skip_reasons.append('硬否决：' + '；'.join(ev.hard_veto))
    if has_breakout and close < t0_high * 0.97 and getattr(ev, 'pb_verdict', 'NA') != 'GOOD':
        skip_reasons.append(f'收盘{close:.2f}明显跌回平台高点{t0_high:.2f}下方（突破失效）')

    th = te_cfg.get('thresholds') or {}
    t_ready, t_boc = _f(th.get('ready_buy', 85)), _f(th.get('buy_on_confirm', 75))
    t_wait, t_watch = _f(th.get('wait_confirm', 65)), _f(th.get('watch', 50))

    state, action, why_not, reason = '', '', [], ''
    if skip_reasons:
        state, action = 'SKIP', 'SKIP'
        why_not = skip_reasons
        reason = '硬风控覆盖：' + '；'.join(skip_reasons)
    elif has_breakout and close > no_chase_level:
        # §9/§18：即使 FE 极高，越过合理扩展位也不追
        state, action = 'NO_CHASE', 'NO_CHASE'
        why_not = [f"收盘{close:.2f}已超追高上限{no_chase_level:.2f}（突破位+{te_cfg.get('no_chase_atr', 1.2):g}×ATR）",
                   f"FE20={fe20:.0f} 再高也不改变追涨的风险收益比（§18）"]
        reason = f"价格透支：收盘超 NO_CHASE_LEVEL {no_chase_level:.2f}，等待回踩而非追高"
    elif not has_breakout and ev.state in ('HVT_STRONG', 'LOCKING', 'LOCKED', 'BREAKOUT_READY', 'WATCH'):
        # §6：ENTRY/FE 高 ≠ 买点，LOCKED 未突破 → 突破触发制
        state, action = 'BREAKOUT_WAIT', 'WAIT'
        why_not = [f"尚未完成平台突破（HVT天量日{ev.t0_date}后锁筹观察中）",
                   f"等待放量突破 {pbl:.2f}（量≥1.3x、收盘位≥0.75）后才允许买入"]
        reason = f"突破触发制：ENTRY={_f(ev.entry_score):.0f}/FE20={fe20:.0f} 再高，突破{pbl:.2f}前不买"
    elif has_breakout and getattr(ev, 'pb_verdict', 'NA') == 'GOOD' and close >= t0_high * 0.97:
        # §7：突破→回踩→缩量→不破位→重新向上，优先级高于追涨
        state = 'PULLBACK_BUY'
        if exec_score >= t_boc:
            action = 'BUY'
            reason = (f"高质量回踩（缩量比{ev.pb_shrink_ratio:.2f}、低点守T0_High、收复突破收盘），"
                      f"执行分{exec_score:.0f} ≥ {t_boc:.0f}，回踩买点优先于追涨")
        elif exec_score >= t_wait:
            action = 'BUY_ON_CONFIRM'
            reason = f"回踩结构GOOD但执行分{exec_score:.0f}居中，需次日重新走强确认"
        else:
            action = 'WAIT'
            why_not = [f"回踩结构GOOD但执行分仅{exec_score:.0f}（<{t_wait:.0f}），其它证据不足"]
            reason = f"回踩结构好但综合证据弱（执行分{exec_score:.0f}），继续观察"
    else:
        # §5：READY_BUY 基础门（不机械使用）+ 分数门
        ready_base = (fe20 >= _f(te_cfg.get('ready_fe20', 70))
                      and (fe60 >= _f(te_cfg.get('ready_fe_mid', 70)) or fe120 >= _f(te_cfg.get('ready_fe_mid', 70)))
                      and cont >= _f(te_cfg.get('ready_cont', 65))
                      and ext < _f(te_cfg.get('ready_extrisk', 60)))
        is_primary = ev.state == 'PRIMARY_BUY' and not ev.hard_veto
        in_zone = close <= no_chase_level
        if is_primary and ready_base and in_zone and exec_score >= t_ready:
            state, action = 'READY_BUY', 'BUY'
            reason = (f"PRIMARY_BUY + 突破确认 + FE20={fe20:.0f}/FE{'60' if fe60 >= fe120 else '120'}="
                      f"{max(fe60, fe120):.0f} + Cont={cont:.0f} + ExtRisk={ext:.0f}，"
                      f"价格在买区（≤NO_CHASE {no_chase_level:.2f}），执行分{exec_score:.0f}")
        elif is_primary and ready_base and in_zone and exec_score >= t_boc:
            state, action = 'READY_BUY', 'BUY_ON_CONFIRM'
            reason = (f"基础门全过（FE20={fe20:.0f} Cont={cont:.0f} ExtRisk={ext:.0f}）"
                      f"但执行分{exec_score:.0f}∈[{t_boc:.0f},{t_ready:.0f})，需次日开盘确认后买")
        else:
            state = 'WAIT_CONFIRM'
            action = 'WAIT' if exec_score >= t_watch else ('WATCH' if exec_score >= t_watch else 'WAIT')
            miss = []
            if not is_primary:
                miss.append(f"未过PRIMARY门（state={ev.state}）")
            if fe20 < _f(te_cfg.get('ready_fe20', 70)):
                miss.append(f"FE20={fe20:.0f}<{te_cfg.get('ready_fe20', 70):g}")
            if fe60 < _f(te_cfg.get('ready_fe_mid', 70)) and fe120 < _f(te_cfg.get('ready_fe_mid', 70)):
                miss.append(f"FE60={fe60:.0f}/FE120={fe120:.0f}均不足")
            if cont < _f(te_cfg.get('ready_cont', 65)):
                miss.append(f"Continuation={cont:.0f}不足")
            if ext >= _f(te_cfg.get('ready_extrisk', 60)):
                miss.append(f"ExtRisk={ext:.0f}偏高")
            if not in_zone:
                miss.append(f"收盘{close:.2f}超出合理执行区（NO_CHASE={no_chase_level:.2f}下方才可执行）")
            if exec_score < t_boc:
                miss.append(f"执行分{exec_score:.0f}不足{t_boc:.0f}")
            why_not = miss or ['确认证据不足']
            reason = 'WAIT_CONFIRM：' + '；'.join(miss[:3])

    # §8：FE 数据缺失时的显式说明（不伪造）
    if state and fe_score <= 0 and state != 'SKIP':
        why_not = list(why_not) + ['FE未来扩张数据缺失（样本/数据不足），无法确认空间']

    tgt, init = _position(state, action, exec_score, te_cfg)
    p = {'trigger': trigger, 'zone_low': zone_low, 'zone_high': zone_high,
         'invalidation': invalidation, 'no_chase_level': no_chase_level,
         'position': tgt, 'initial': init}

    out.update({
        'buyability': round(buyability, 1),
        'buyability_parts': {k: round(v, 1) for k, v in parts.items()},
        'execution_score': round(exec_score, 1),
        'execution_state': state,
        'next_day_action': action,
        'entry_trigger': round(trigger, 2),
        'buy_zone_low': round(zone_low, 2),
        'buy_zone_high': round(zone_high, 2),
        'invalidation': round(invalidation, 2),
        'no_chase_level': round(no_chase_level, 2),
        'position_size': tgt,
        'initial_position': init,
        'primary_horizon': horizon,
        'stock_type': stock_type,
        'confirmation_level': _confirmation_level(state, action, intraday_ok),
        'execution_reason': reason,
        'why_not_buy': why_not,
        'open_playbook': _open_playbook(state, action, p, te_cfg, intraday_ok) if state else [],
        'intraday_available': intraday_ok,
    })
    return out
