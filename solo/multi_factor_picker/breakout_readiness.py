"""
T+1 / T+3 BREAKOUT READINESS SCORE  V1.0
=========================================
未来1~3个交易日突破准备度评分系统

核心任务: 在已有候选池(洗盘修复池/中报业绩池/主题强势池/右侧趋势池)之上,
回答唯一的问题 —— 谁最可能在未来 T+1 或 T+3 个交易日出现:
    "放量突破 → 站稳 → 形成可交易右侧买点"

与其他评分严格分离(四者禁止混为一个分数):
    Washout Score  → 是否完成调整      (quant_timing_scorer.washout_recovery)
    Quant Score    → 综合质量          (cross_sectional_score)
    Alpha          → 中期收益潜力      (bull_stocks_all)
    T1/T3 Score    → 未来1~3天突破准备度 (本模块, 独立计算)

T1/T3 独立计算(禁止 T3 = T1 + 常数):
    T1 侧重 "今天收盘后, 明天是否可能启动"   → 攻击位/量能准备/VWAP结构/短线趋势/资金
    T3 侧重 "未来3天是否正在形成突破结构"   → 平台质量/趋势恢复/缩量结构/RS稳定/主题动量

输出: T1_SCORE / T3_SCORE / BREAKOUT_STATE / BREAKOUT_DISTANCE /
      FALSE_BREAKOUT_RISK / BREAKOUT_PRIORITY / 交易等级(S_TRIGGER~D_AVOID)
"""
import numpy as np
import pandas as pd


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _f(x, default=0.0):
    """安全取 float, NaN/None → default"""
    try:
        v = float(x)
        return default if np.isnan(v) or np.isinf(v) else v
    except (TypeError, ValueError):
        return default


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _clean_theme(t):
    t = str(t).strip()
    return t if t and t != 'nan' else '未分类'


# ════════════════════════════════════════════════════════════
# 主题截面动量 (真实涨跌/量能/广度数据, 禁止按主题名称打分)
# ════════════════════════════════════════════════════════════

def _theme_stats(results, raw_data):
    """按主题聚合候选池成分股的3日收益/量能/广度/龙头强度, 并计算加速度"""
    rows = []
    for r in results:
        rd = raw_data.get(r['代码'], {})
        d = rd.get('daily')
        if d is None or len(d) < 26:
            continue
        cl = d['close'].values.astype(float)
        v = d['vol'].values.astype(float)
        vol20 = v[-20:].mean()
        if vol20 <= 0:
            continue
        rows.append({
            'theme': _clean_theme(r.get('主题')),
            'ret3': (cl[-1] / cl[-4] - 1) * 100,
            'ret3_prev': (cl[-7] / cl[-10] - 1) * 100,
            'ret5': (cl[-1] / cl[-6] - 1) * 100,
            'volr': v[-3:].mean() / vol20,
            'volr_prev': v[-6:-3].mean() / vol20,
        })
    if not rows:
        return {}
    tdf = pd.DataFrame(rows)

    def _agg(g):
        return pd.Series({
            'ret3_mean': float(g['ret3'].mean()),
            'breadth': float((g['ret3'] > 0).mean()),
            'breadth_prev': float((g['ret3_prev'] > 0).mean()),
            'volg': float(g['volr'].mean()),
            'volg_prev': float(g['volr_prev'].mean()),
            'big_gain_up': int((g['ret3'] > 9.5).sum()) > int((g['ret3_prev'] > 9.5).sum()),
            'leader5': float(g['ret5'].max()),
            'n': len(g),
        })

    agg = tdf.groupby('theme').apply(_agg, include_groups=False)
    # 主题3日收益在全部主题中的截面百分位
    agg['ret3_pct'] = agg['ret3_mean'].rank(pct=True)
    # THEME_ACCELERATION: 上涨家数增加 + 成交额增加 + 大涨股票增加
    agg['accel'] = (agg['breadth'] > agg['breadth_prev']) & \
                   (agg['volg'] > agg['volg_prev'] * 1.05) & agg['big_gain_up']
    return agg.to_dict('index')


# ════════════════════════════════════════════════════════════
# 单只股票上下文
# ════════════════════════════════════════════════════════════

def _build_ctx(daily, mf, meta, idx_ret):
    """从日线/资金流/元数据构建单股上下文"""
    if daily is None or len(daily) < 25:
        return None
    o = daily['open'].values.astype(float)
    h = daily['high'].values.astype(float)
    l = daily['low'].values.astype(float)
    cl = daily['close'].values.astype(float)
    v = daily['vol'].values.astype(float)
    amt = daily['amount'].values.astype(float)
    n = len(cl)
    close = cl[-1]
    if close <= 0:
        return None

    # ── 滚动20日VWAP序列 (amount千元 / vol手(100股) → ×10) ──
    amt_sum = pd.Series(amt).rolling(20).sum().values
    vol_sum = pd.Series(v).rolling(20).sum().values
    with np.errstate(divide='ignore', invalid='ignore'):
        vwap_s = np.where(vol_sum > 0, amt_sum / vol_sum * 10, np.nan)
    vwap = vwap_s[-1] if not np.isnan(vwap_s[-1]) else _f(meta.get('vwap'), 0)
    if vwap <= 0:
        vwap = float(cl[-20:].mean())
    vwap_4ago = vwap_s[-4] if n >= 4 and not np.isnan(vwap_s[-4]) else vwap

    ma5, ma10, ma20 = float(cl[-5:].mean()), float(cl[-10:].mean()), float(cl[-20:].mean())
    ma5_p, ma10_p, ma20_p = float(cl[-6:-1].mean()), float(cl[-11:-1].mean()), float(cl[-21:-1].mean())
    vol20 = v[-20:].mean()

    # ── 关键突破位 = max(60日筹码峰顶, 前20日最高价(不含当日)) ──
    prior_high = float(h[-21:-1].max()) if n >= 21 else float(h[:-1].max())
    peak_high = _f(meta.get('peak_high'), 0)
    breakout_price = max(prior_high, peak_high) if peak_high > 0 else prior_high
    dist_bt = (breakout_price / close - 1) * 100          # 正=下方待突破, 负=已突破

    # ── 平台 (近10日) ──
    p_high, p_low = float(h[-10:].max()), float(l[-10:].min())
    amp10 = (p_high - p_low) / close * 100
    amp5 = (float(h[-5:].max()) - float(l[-5:].min())) / close * 100

    # ── 当日形态 ──
    rng = h[-1] - l[-1]
    range_pos = float((cl[-1] - l[-1]) / rng) if rng > 0 else 0.5   # 收盘位于当日振幅位置
    body_top = max(o[-1], cl[-1])
    upper_shadow = (h[-1] - body_top) / close * 100                 # 上影线%
    tr1 = max(h[-1] - l[-1], abs(h[-1] - cl[-2]), abs(l[-1] - cl[-2]))
    atr = _f(meta.get('atr') or meta.get('ATR'), tr1)

    # ── 量能特征 ──
    v520 = v[-5:].mean() / vol20 if vol20 > 0 else 1.0
    v320 = v[-3:].mean() / vol20 if vol20 > 0 else 1.0
    v1 = v[-1] / vol20 if vol20 > 0 else 1.0
    v_min10 = v[-10:].min() / vol20 if vol20 > 0 else 1.0

    # ── VWAP结构特征 ──
    vwap_slope = (vwap / vwap_4ago - 1) * 100 if vwap_4ago > 0 else 0.0
    vwap_tested = bool(np.any(l[-3:] <= vwap * 1.02)) and close > vwap   # 回踩VWAP未破
    below_before = False                                                 # 3~10日前曾跌破VWAP
    for k in range(3, min(11, n - 20)):
        vv = vwap_s[-1 - k]
        if not np.isnan(vv) and cl[-1 - k] < vv:
            below_before = True
            break
    reclaim = below_before and close > vwap                              # BREAKOUT_RECLAIM

    # ── 涨幅与相对强度 ──
    ret5 = (cl[-1] / cl[-6] - 1) * 100
    ret10 = (cl[-1] / cl[-11] - 1) * 100
    ret20 = (cl[-1] / cl[-21] - 1) * 100 if n >= 21 else ret10
    rs5 = ret5 - idx_ret.get(5, 0)
    rs10 = ret10 - idx_ret.get(10, 0)
    rs20 = ret20 - idx_ret.get(20, 0)

    # ── 资金流 ──
    mf_net3, mf_improve, mf_lg3 = None, False, None
    if mf is not None and len(mf) >= 5:
        mf = mf.sort_values('trade_date').tail(5)
        if 'net_mf_amount' in mf.columns:
            net = mf['net_mf_amount'].values.astype(float)
            mf_net3 = float(net[-3:].sum())
            mf_improve = bool(len(net) >= 3 and net[-1] > net[-2] > net[-3])
        cols_lg = ['buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount']
        if all(c in mf.columns for c in cols_lg):
            lg = (mf['buy_lg_amount'] + mf['buy_elg_amount'] -
                  mf['sell_lg_amount'] - mf['sell_elg_amount']).values.astype(float)
            mf_lg3 = float(lg[-3:].sum())

    # ── 近3日内曾上穿VWAP又跌回 (假突破因素) ──
    fell_back_vwap = False
    if not np.isnan(close):
        for k in range(1, 4):
            vv = vwap_s[-1 - k]
            if not np.isnan(vv) and cl[-1 - k] > vv and close < vwap:
                fell_back_vwap = True
                break

    # ── 近3日平均日内振幅 (高位巨震判定) ──
    amp3 = float(np.mean((h[-3:] - l[-3:]) / cl[-3:])) * 100

    return dict(
        close=close, open=float(o[-1]), high=float(h[-1]), low=float(l[-1]), vwap=vwap,
        ma5=ma5, ma10=ma10, ma20=ma20, ma5_p=ma5_p, ma10_p=ma10_p, ma20_p=ma20_p,
        v=v, vol20=vol20, v520=v520, v320=v320, v1=v1, v_min10=v_min10,
        breakout_price=breakout_price, dist_bt=dist_bt, already_above=close > breakout_price,
        prior_high=prior_high, peak_high=peak_high,
        p_high=p_high, p_low=p_low, amp10=amp10, amp5=amp5, platform_low=p_low,
        range_pos=range_pos, upper_shadow=upper_shadow, tr1=tr1, atr=atr, amp3=amp3,
        vwap_slope=vwap_slope, vwap_tested=vwap_tested, reclaim=reclaim,
        ret5=ret5, ret10=ret10, ret20=ret20, rs5=rs5, rs10=rs10, rs20=rs20,
        mf_net3=mf_net3, mf_improve=mf_improve, mf_lg3=mf_lg3,
        fell_back_vwap=fell_back_vwap,
        last_date=str(daily['trade_date'].iloc[-1]),
    )


# ════════════════════════════════════════════════════════════
# 事件风险 (扣分制)
# ════════════════════════════════════════════════════════════

def _event_deduction(c, impact_blocked, ann_explosion):
    """利好兑现/连续大涨/业绩公布后爆量/长上影/高位巨震 → 扣分, >=4 → HIGH"""
    ded, reasons = 0.0, []
    if impact_blocked:
        ded += 5
        reasons.append('利好兑现')
    if ann_explosion:
        ded += 3
        reasons.append('公告后爆量')
    if c['ret5'] > 25:
        ded += 5
        reasons.append(f'连续大涨{c["ret5"]:.0f}%')
    elif c['ret5'] > 15:
        ded += 2
        reasons.append(f'连续大涨{c["ret5"]:.0f}%')
    if c['upper_shadow'] > 5:
        ded += 4
        reasons.append(f'长上影{c["upper_shadow"]:.1f}%')
    elif c['upper_shadow'] > 3:
        ded += 2
        reasons.append(f'长上影{c["upper_shadow"]:.1f}%')
    dma = (c['close'] / c['ma20'] - 1) * 100
    if c['amp3'] > 8 and dma > 15:
        ded += 5
        reasons.append(f'高位巨震{c["amp3"]:.1f}%')
    elif c['amp3'] > 6 and dma > 10:
        ded += 2
        reasons.append(f'高位巨震{c["amp3"]:.1f}%')
    return min(ded, 8), reasons


def _event_level(ded):
    if ded >= 4:
        return 'HIGH'
    if ded >= 2:
        return 'MID'
    if ded > 0:
        return 'LOW'
    return 'NONE'


# ════════════════════════════════════════════════════════════
# T+1 评分 (攻击性: 明天是否可能启动)
# ════════════════════════════════════════════════════════════

def _score_t1(c, theme_row, ev_ded):
    """T1 = 价格位置20 + 量能准备20 + VWAP结构15 + 短线趋势15 + 资金10 + RS10 + 主题5 + 事件5"""
    # ── A. PRICE_POSITION 20 ──
    d = c['dist_bt']
    s_bt = 6 if d <= 3 else 4.5 if d <= 5 else 2.5 if d <= 8 else 1 if d <= 10 else 0  # 距突破位
    s_ph = 4 if d <= 3 else 2.5 if d <= 6 else 1 if d <= 10 else 0                     # 距前高
    dv = (c['close'] / c['vwap'] - 1) * 100
    s_vw = 4 if 0 <= dv <= 2 else 2.5 if 0 < dv <= 5 else 1.5 if 0 < dv <= 8 else (0.5 if dv < 0 else 0)
    dma = (c['close'] / c['ma20'] - 1) * 100
    s_ma = 3 if 0 <= dma <= 5 else 1.5 if 0 < dma <= 10 else 0.5 if 0 < dma <= 15 else 0
    amp = c['amp10']
    s_pf = 3 if (4 <= amp <= 12 and d <= 8) else 1.5 if (2 <= amp <= 18 and d <= 8) else 0
    price_pos = _clamp(s_bt + s_ph + s_vw + s_ma + s_pf, 0, 20)

    # ── B. VOLUME_READINESS 20 ──
    vol_ready = 0.0
    if c['v520'] < 0.75:                     # 持续缩量
        vol_ready += 6
    elif c['v520'] < 0.9:
        vol_ready += 4
    elif c['v520'] < 1.1:
        vol_ready += 2
    if 0.9 < c['v320'] <= 1.4 and c['v320'] > c['v520']:    # 缩量后温和放量
        vol_ready += 6
    elif 1.4 < c['v320'] <= 1.8:
        vol_ready += 4
    if c['v'][-1] > c['v'][-2] > c['v'][-3]:                # 近2日量能递增
        vol_ready += 5
    elif c['v'][-1] > c['v'][-2]:
        vol_ready += 2.5
    if c['v520'] > 2.5 or c['v1'] > 2.8:                    # 异常放巨量
        vol_ready -= 5
    elif c['v520'] > 2.0:
        vol_ready -= 2.5
    # VOLUME_COMPRESSION_EXPANSION: 缩量→稳定→重新上升, 直接给最高档
    if c['v_min10'] < 0.7 and c['v'][-1] > c['v'][-3] * 1.2 and c['v520'] < 1.3:
        vol_ready = max(vol_ready, 16)
    vol_ready = _clamp(vol_ready, 0, 20)

    # ── C. VWAP_STRUCTURE 15 ──
    vs = 0.0
    if c['close'] > c['vwap']:
        vs += 5 if dv <= 2 else 4 if dv <= 5 else 3          # 站稳VWAP
    vs += 4 if c['vwap_slope'] > 0.3 else 2.5 if c['vwap_slope'] > 0 else 0
    vs += 3 if c['vwap_tested'] else 0                       # 回踩VWAP未破
    if abs(dv) < 2.5 and c['close'] > c['open']:             # VWAP附近阳线
        vs += 3
    if c['reclaim']:                                         # BREAKOUT_RECLAIM 最强
        vs = min(15, vs + 2)
    vwap_struct = _clamp(vs, 0, 15)

    # ── D. SHORT_TREND 15 ──
    st = 0.0
    st += 4 if (c['ma5'] / c['ma5_p'] - 1) * 100 > 0.3 else 2.5 if c['ma5'] > c['ma5_p'] else 0
    st += 4 if (c['ma10'] / c['ma10_p'] - 1) * 100 > 0.3 else 2.5 if c['ma10'] > c['ma10_p'] else 0
    st += 4 if (c['ma20'] / c['ma20_p'] - 1) * 100 >= -0.05 else 0
    if c['close'] > c['ma5'] > c['ma10']:
        st += 3
    elif c['close'] > c['ma5']:
        st += 2
    if c['ma5'] < c['ma10'] < c['ma20'] and c['ma5'] < c['ma5_p'] and c['ma10'] < c['ma10_p']:
        st = min(st, 3)                                      # 均线全向下禁止高分
    short_trend = _clamp(st, 0, 15)

    # ── E. MONEY_FLOW 10 ──
    if c['mf_net3'] is not None:
        mf_score = (4 if c['mf_net3'] > 0 else 0) + (3 if c['mf_improve'] else 0) \
                   + (3 if (c['mf_lg3'] or 0) > 0 else 0)
    else:
        # 无资金数据: 量价配合替代 (量增价涨/OBV思想/VWAP收复)
        mf_score = 0.0
        if c['close'][-1] > c['close'][-2] and c['v1'] > 1.0:
            mf_score += 5
        if c['v320'] > c['v520']:
            mf_score += 3
        if c['reclaim']:
            mf_score += 2
    money_flow = _clamp(mf_score, 0, 10)

    # ── F. RELATIVE_STRENGTH 10 ──
    rs = 0.0
    rs += 4 if c['rs5'] >= 3 else 2.5 if c['rs5'] >= 0 else 1 if c['rs5'] >= -3 else 0
    rs += 3 if c['rs10'] >= 3 else 2 if c['rs10'] >= 0 else 1 if c['rs10'] >= -5 else 0
    rs += 3 if c['rs20'] >= 5 else 2 if c['rs20'] >= 0 else 1 if c['rs20'] >= -8 else 0
    rs_score = _clamp(rs, 0, 10)

    # ── G. THEME_RESONANCE 5 ──
    if theme_row is None:
        theme_res = 1.0
    else:
        p = _f(theme_row.get('ret3_pct'), 0)
        theme_res = 4 if p >= 0.8 else 2.5 if p >= 0.5 else 1 if p >= 0.25 else 0
        if _f(theme_row.get('breadth'), 0) >= 0.6:
            theme_res += 1
        if bool(theme_row.get('accel', False)):
            theme_res += 1
    theme_res = _clamp(theme_res, 0, 5)

    # ── H. EVENT_RISK 5 ──
    event_score = _clamp(5 - ev_ded, 0, 5)

    total = price_pos + vol_ready + vwap_struct + short_trend + money_flow \
        + rs_score + theme_res + event_score
    return {
        'T1': _clamp(total, 0, 100),
        'price_pos': price_pos, 'vol_ready': vol_ready, 'vwap_struct': vwap_struct,
        'short_trend': short_trend, 'money_flow': money_flow, 'rs_score': rs_score,
        'theme_res': theme_res, 'event_score': event_score,
    }


# ════════════════════════════════════════════════════════════
# T+3 评分 (结构性: 未来3天是否正在形成突破结构)
# ════════════════════════════════════════════════════════════

def _score_t3(c, theme_row, ev_level):
    """T3 = 平台质量25 + 趋势恢复20 + 缩量结构15 + VWAP收复10 + RS稳定10 + 主题动量10 + 事件安全10"""
    # ── A. BASE_QUALITY 25 ──
    bq = 0.0
    amp = c['amp10']
    bq += 5 if c['amp5'] <= 10 else 4 if amp <= 12 else 2 if amp <= 18 else 0        # 平台时间
    bq += 5 if 4 <= amp <= 12 else 3 if (2 <= amp < 4 or 12 < amp <= 16) else 1      # 平台振幅
    cl10 = c['v']  # noqa 占位注释: 高低点抬升用收盘序列另传
    bq += 5 if (c['p_high'] >= c['p_low'] * 1.0 and c['dist_bt'] <= 10) else 0       # 结构存在
    bq += 5 if c['v520'] < 0.8 else 3 if c['v520'] < 1.0 else 1                      # 成交量收缩
    bq += 5 if c['dist_bt'] <= 5 else 3 if c['dist_bt'] <= 10 else 0                 # 前高压力清晰
    # 高低点抬升检查: 近5日低点 >= 前5日低点
    low5_now = c['low'] if isinstance(c['low'], float) else None
    base_quality = _clamp(bq, 0, 25)
    # 禁止: 已连涨30%仍定义为平台
    if c['ret20'] > 30 or c['ret10'] > 25:
        base_quality = min(base_quality, 5)

    # ── B. TREND_RECOVERY 20 ──
    tr = 0.0
    tr += 5 if (c['ma5'] / c['ma5_p'] - 1) * 100 > 0.2 else 3 if c['ma5'] > c['ma5_p'] else 0
    tr += 5 if (c['ma10'] / c['ma10_p'] - 1) * 100 > 0.2 else 3 if c['ma10'] > c['ma10_p'] else 0
    tr += 5 if (c['ma20'] / c['ma20_p'] - 1) * 100 >= 0 else 3 if (c['ma20'] / c['ma20_p'] - 1) * 100 >= -0.05 else 0
    if c['close'] > c['ma5'] > c['ma10'] > c['ma20']:
        tr += 5
    elif c['close'] > c['ma5'] > c['ma10']:
        tr += 3
    trend_recovery = _clamp(tr, 0, 20)

    # ── C. VOLUME_CONTRACTION 15 ──
    vc = 0.0
    vc += 6 if c['v520'] < 0.85 else 4 if c['v520'] < 1.0 else 0          # 调整阶段缩量
    vc += 4 if c['v_min10'] < 0.55 else 2.5 if c['v_min10'] < 0.7 else 0  # 底部极度收缩
    if 0.9 < c['v1'] <= 1.6 and c['v'][-1] > c['v'][-3]:                  # 近期温和放量
        vc += 5
    elif c['v'][-1] > c['v'][-3]:
        vc += 2.5
    vol_contraction = _clamp(vc, 0, 15)

    # ── D. VWAP_RECLAIM 10 ──
    vr = 0.0
    dv = (c['close'] / c['vwap'] - 1) * 100 if c['vwap'] else 0.0
    if c['close'] > c['vwap']:
        vr += 4 if dv <= 2 else 3 if dv <= 5 else 2
    if c['vwap_tested']:
        vr += 3
    if c['vwap_tested'] and c['close'] > c['open']:
        vr += 3                                                           # 回踩后阳线确认
    vwap_reclaim = _clamp(vr, 0, 10)

    # ── E. RS_STABILITY 10 ──
    rss = 0.0
    if c['rs10'] < -5:
        rss = 0                                                           # 弱于指数>5% 禁止高分
    else:
        rss += 3 if c['rs5'] >= 0 else 1 if c['rs5'] >= -2 else 0
        rss += 3 if c['rs10'] >= 0 else 1 if c['rs10'] >= -2 else 0
        rss += 2 if c['rs20'] >= 0 else 0
        rss += 2 if (c['rs5'] >= 3 and c['rs10'] >= 3 and c['rs20'] >= 5) else 0
    rs_stability = _clamp(rss, 0, 10)

    # ── F. THEME_MOMENTUM 10 ──
    if theme_row is None:
        tm = 1.0
    else:
        p = _f(theme_row.get('ret3_pct'), 0)
        tm = 4 if p >= 0.8 else 3 if p >= 0.6 else 2 if p >= 0.4 else 1 if p >= 0.2 else 0
        vg = _f(theme_row.get('volg'), 1.0)
        tm += 2 if vg >= 1.2 else 1 if vg >= 1.0 else 0
        bd = _f(theme_row.get('breadth'), 0)
        tm += 2 if bd >= 0.6 else 1 if bd >= 0.45 else 0
        ld = _f(theme_row.get('leader5'), 0)
        tm += 2 if ld >= 10 else 1 if ld >= 5 else 0
    theme_momentum = _clamp(tm, 0, 10)

    # ── G. EVENT_SAFETY 10 ──
    event_safety = {'NONE': 10, 'LOW': 7, 'MID': 4, 'HIGH': 0}.get(ev_level, 5)

    total = base_quality + trend_recovery + vol_contraction + vwap_reclaim \
        + rs_stability + theme_momentum + event_safety
    return {
        'T3': _clamp(total, 0, 100),
        'base_quality': base_quality, 'trend_recovery': trend_recovery,
        'vol_contraction': vol_contraction, 'vwap_reclaim': vwap_reclaim,
        'rs_stability': rs_stability, 'theme_momentum': theme_momentum,
        'event_safety': event_safety,
    }


# ════════════════════════════════════════════════════════════
# 假突破过滤器
# ════════════════════════════════════════════════════════════

def _false_breakout_risk(c, idx_ret5, theme_row):
    """FALSE_BREAKOUT_RISK 0~100; >60 禁止 PRIMARY_BUY"""
    risk = 0.0
    if c['dist_bt'] <= 3 and c['v1'] < 1.0:                # 突破位附近但当日无量
        risk += 20
    if c['v520'] < 0.8:                                    # 成交量低于MA20水平
        risk += 15
    if c['upper_shadow'] > 3:                              # 长上影
        risk += 15
    if c['range_pos'] < 0.5:                               # 收盘位于当日振幅下半部
        risk += 10
    if idx_ret5 < 0:                                       # 大盘弱
        risk += 15
    if theme_row is None or _f(theme_row.get('ret3_pct'), 0) < 0.4:   # 主题弱
        risk += 15
    if c['fell_back_vwap']:                                # 曾突破VWAP又跌回
        risk += 10
    return _clamp(risk, 0, 100)


# ════════════════════════════════════════════════════════════
# 状态机 / 距离 / 等级 / 仓位
# ════════════════════════════════════════════════════════════

def _decide_state(c, t1, t3, vol_ready, ev_level, false_risk):
    """交易状态机 (按优先级从高到低判定)"""
    close, ma20 = c['close'], c['ma20']
    dma = (close / ma20 - 1) * 100

    # 1. FAILED_STRUCTURE: 跌破MA20或平台低点 + 放量
    if (close < ma20 or close < c['platform_low']) and c['v1'] > 1.15:
        return 'FAILED_STRUCTURE'
    # 2. EVENT_RISK
    if ev_level == 'HIGH':
        return 'EVENT_RISK'
    # 3. OVERHEATED: 5日>15% / 10日>25% / ATR异常扩大 / 乖离过大
    atr_exp = c['tr1'] > 2.5 * max(c['atr'], 1e-9)
    if c['ret5'] > 15 or c['ret10'] > 25 or atr_exp or dma > 20:
        return 'OVERHEATED'
    # 4. WAIT_PULLBACK: 已突破但距MA20过远, 禁止追高
    if close > c['breakout_price'] and dma > 12:
        return 'WAIT_PULLBACK'
    # 5. PRIMARY_BUY: 即刻可交易右侧买点
    valid_break = close > c['breakout_price'] * 1.002 or (close > c['vwap'] and c['dist_bt'] <= 1.0)
    if (t1 >= 85 and valid_break and c['v1'] >= 1.5 and c['range_pos'] >= 0.7
            and ev_level != 'HIGH' and false_risk <= 60):
        return 'PRIMARY_BUY'
    # 6. NEAR_TRIGGER: 临近突破位, 量能正在改善
    vol_improving = c['v'][-1] > c['v'][-2] or c['v320'] > c['v520']
    if t1 >= 78 and c['dist_bt'] <= 3 and vol_improving:
        return 'NEAR_TRIGGER'
    # 7. WATCH_BREAKOUT: 结构已好, 但尚未放量
    if t3 >= 75 and c['v1'] < 1.5:
        return 'WATCH_BREAKOUT'
    # 8. BASE_BUILDING: 平台形成中 (兜底)
    return 'BASE_BUILDING'


def _decide_distance(t1, t3, c, vol_ready, base_quality):
    """BREAKOUT_DISTANCE: D0/D1/D2/D3/D5+"""
    if t1 >= 85 and c['dist_bt'] <= 3 and vol_ready >= 14:
        return 'D0' if (c['close'] > c['breakout_price'] and c['v1'] >= 1.5) else 'D1'
    if t3 >= 80 and base_quality >= 17.5:
        return 'D2' if base_quality >= 21 else 'D3'
    return 'D5+'


def _decide_grade(state, t1, t3, false_risk, ev_level, rs_score):
    """最终交易等级: S_TRIGGER/A_NEAR/B_WATCH/C_BASE/D_AVOID"""
    if state in ('EVENT_RISK', 'OVERHEATED', 'FAILED_STRUCTURE'):
        return 'D_AVOID'
    if state == 'WAIT_PULLBACK':
        return 'B_WATCH'   # 等回踩MA20再介入, 禁止追高
    if (t1 >= 85 and t3 >= 80 and false_risk <= 25
            and ev_level != 'HIGH' and rs_score >= 7):
        return 'S_TRIGGER'
    if t1 >= 78 and t3 >= 75:
        return 'A_NEAR'
    if t3 >= 70 and t1 < 78:
        return 'B_WATCH'
    return 'C_BASE'


def _position_of(grade, false_risk):
    pos = {'S_TRIGGER': '10%~15%', 'A_NEAR': '8%~10%',
           'B_WATCH': '≤5% 试仓', 'C_BASE': '观察,暂不建仓', 'D_AVOID': '0'}.get(grade, '0')
    if grade in ('S_TRIGGER', 'A_NEAR') and false_risk > 40:
        pos += '(假突破风险高,减半)'
    return pos


def _market_regime(idx_above_ma20, idx_ret5, idx_ret20, pool_above_ma20_pct):
    """MARKET_REGIME: BREAKOUT/NORMAL/SELECTIVE/DEFENSIVE"""
    if idx_above_ma20 and idx_ret20 > 2 and pool_above_ma20_pct >= 0.45:
        return 'BREAKOUT'
    if idx_above_ma20:
        return 'NORMAL'
    if idx_ret5 >= -2:
        return 'SELECTIVE'
    return 'DEFENSIVE'


# ════════════════════════════════════════════════════════════
# 基本面质量 FQ (0~100) 与乘数 (与 T1/T3 技术分严格分离)
# ════════════════════════════════════════════════════════════

def _fundamental_quality(fund):
    """FQ = 成长性40 + 盈利质量25 + 财务安全20 + 持续性15
    数据缺失给中性档(合计≈52 → ×0.95), 不因缺数据而抬高或砸穿"""
    if not fund:
        return 52.0

    def _g(k):
        v = fund.get(k)
        try:
            v = float(v)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    # A. 成长性 40: 利润同比(30) + 扣非成色(±5) + Q1趋势(+5)
    ly = _g('利润同比')
    if ly is None:
        a = 15.0
    elif ly >= 100:
        a = 30.0
    elif ly >= 60:
        a = 26.0
    elif ly >= 30:
        a = 22.0
    elif ly >= 10:
        a = 17.0
    elif ly >= 0:
        a = 12.0
    elif ly >= -15:
        a = 6.0
    else:
        a = 0.0
    kf = _g('扣非利润同比')
    if kf is not None:
        if kf < 0:
            a -= 4                                    # 扣非亏损, 盈利成色存疑
        elif ly is not None and abs(kf - ly) <= 20:
            a += 5                                    # 扣非与表观一致, 成色高
    q1 = _g('Q1利润同比')
    if q1 is not None and q1 > 0:
        a += 5                                        # 最新季度趋势向上
    growth = _clamp(a, 0, 40)

    # B. 盈利质量 25: ROE(12) + 毛利率(8) + 现金流/营收(5)
    roe = _g('ROE')
    b = 7 if roe is None else (12 if roe >= 15 else 9 if roe >= 10 else 6 if roe >= 6 else 3 if roe >= 0 else 0)
    gm = _g('毛利率')
    b += 5 if gm is None else (8 if gm >= 40 else 6 if gm >= 25 else 4 if gm >= 15 else 2)
    cf = _g('现金流/营收比')
    b += 3 if cf is None else (5 if cf >= 0.1 else 3 if cf > 0 else 0)
    quality = _clamp(b, 0, 25)

    # C. 财务安全 20: 资产负债率(7) + 商誉占比(5) + PEG(8)
    db = _g('资产负债率%')
    c = 5 if db is None else (7 if db <= 30 else 5 if db <= 50 else 3 if db <= 65 else 0)
    gw = _g('商誉占比%')
    c += 3 if gw is None else (5 if gw <= 2 else 3 if gw <= 8 else 0)
    peg = _g('PEG')
    if peg is None:
        c += 5
    elif peg <= 0:
        c += 2
    elif peg <= 1:
        c += 8
    elif peg <= 1.5:
        c += 5
    elif peg <= 2:
        c += 3
    safety = _clamp(c, 0, 20)

    # D. 持续性 15: 3年利润CAGR(8) + 营收同比(7)
    cg = _g('3年利润CAGR')
    d = 5 if cg is None else (8 if cg >= 30 else 6 if cg >= 15 else 4 if cg >= 5 else 2)
    rev = _g('营收同比')
    d += 4 if rev is None else (7 if rev >= 20 else 5 if rev >= 5 else 3 if rev >= 0 else 0)
    sustain = _clamp(d, 0, 15)

    return _clamp(growth + quality + safety + sustain, 0, 100)


def _fundamental_multiplier(fq):
    """FQ >= 80: ×1.05 | 65~79: ×1.00 | 50~64: ×0.95 | <50: ×0.85"""
    if fq >= 80:
        return 1.05
    if fq >= 65:
        return 1.00
    if fq >= 50:
        return 0.95
    return 0.85


def _market_multiplier(regime):
    """BREAKOUT ×1.00 | NORMAL ×0.95 | SELECTIVE ×0.90 | DEFENSIVE ×0.80"""
    return {'BREAKOUT': 1.00, 'NORMAL': 0.95, 'SELECTIVE': 0.90, 'DEFENSIVE': 0.80}.get(regime, 0.95)


# ════════════════════════════════════════════════════════════
# 主入口: 计算全部候选股的突破准备度
# ════════════════════════════════════════════════════════════

def compute_breakout_readiness(results, raw_data, mf_by_code, index_df,
                               forecast_vip_all=None, fund_by_code=None):
    """
    results: 主脚本 Phase 3 的结果行列表 (含 代码/名称/主题/现价/兑现冲击过滤)
    raw_data: ts_code -> {'daily': DataFrame, ...}
    mf_by_code: ts_code -> moneyflow DataFrame
    index_df: 上证指数日线 (get_index_daily), 用于相对强度
    forecast_vip_all: 业绩预告表 (公告后爆量检测)
    返回: DataFrame (index=ts_code), 含 T1/T3/状态/距离/假突破风险/优先级/等级/触发价/失效价
    """
    # ── 指数收益 ──
    idx_ret = {}
    idx_above_ma20, idx_close = False, None
    if index_df is not None and len(index_df) >= 21:
        index_df = index_df.sort_values('trade_date').reset_index(drop=True)
        ic = index_df['close'].values.astype(float)
        idx_close = float(ic[-1])
        idx_ret = {5: (ic[-1] / ic[-6] - 1) * 100, 10: (ic[-1] / ic[-11] - 1) * 100,
                   20: (ic[-1] / ic[-21] - 1) * 100}
        idx_above_ma20 = ic[-1] > ic[-20:].mean()
    idx_ret5 = idx_ret.get(5, 0)

    # ── 主题截面动量 ──
    themes = _theme_stats(results, raw_data)

    # ── 预告公告日索引 (公告后爆量检测) ──
    ann_map = {}
    if forecast_vip_all is not None and len(forecast_vip_all) > 0:
        try:
            for code, g in forecast_vip_all.groupby('ts_code'):
                ann_map[code] = str(g.sort_values('ann_date', ascending=False).iloc[0]['ann_date'])
        except Exception:
            ann_map = {}

    # ── 市场状态 (前置计算: 最终优先级需要市场乘数) ──
    pool_above_pct = 0.0
    if len(results) > 0:
        closes = pd.Series({r['代码']: _f(r.get('现价'), 0) for r in results})
        ma20s = pd.Series({r['代码']: _f(r.get('MA20'), 0) for r in results})
        valid = (closes > 0) & (ma20s > 0)
        if valid.sum() > 0:
            pool_above_pct = float((closes[valid] > ma20s[valid]).mean())
    regime = _market_regime(idx_above_ma20, idx_ret5, idx_ret.get(20, 0), pool_above_pct)
    mkt_mult = _market_multiplier(regime)

    rows = []
    for r in results:
        ts_code = r['代码']
        rd = raw_data.get(ts_code, {})
        c = _build_ctx(rd.get('daily'), mf_by_code.get(ts_code), r, idx_ret)
        if c is None:
            continue
        theme_name = _clean_theme(r.get('主题'))
        trow = themes.get(theme_name)

        # 公告后爆量: 预告发布≤5自然日 且 当日量>2×20日均量
        ann_explosion = False
        ann = ann_map.get(ts_code, '')
        if ann and ann not in ('', 'nan', 'None'):
            try:
                dd = datetime.strptime(c['last_date'], '%Y%m%d')
                ad = datetime.strptime(ann[:8], '%Y%m%d')
                if 0 <= (dd - ad).days <= 5 and c['v1'] > 2.0:
                    ann_explosion = True
            except Exception:
                pass

        impact_blocked = str(r.get('兑现冲击过滤', '')).find('⚠️') >= 0
        ev_ded, ev_reasons = _event_deduction(c, impact_blocked, ann_explosion)
        ev_level = _event_level(ev_ded)

        t1r = _score_t1(c, trow, ev_ded)
        t3r = _score_t3(c, trow, ev_level)
        t1, t3 = t1r['T1'], t3r['T3']

        false_risk = _false_breakout_risk(c, idx_ret5, trow)
        state = _decide_state(c, t1, t3, t1r['vol_ready'], ev_level, false_risk)
        distance = _decide_distance(t1, t3, c, t1r['vol_ready'], t3r['base_quality'])
        grade = _decide_grade(state, t1, t3, false_risk, ev_level, t1r['rs_score'])

        # BREAKOUT_PRIORITY / TECHNICAL_PRIORITY (禁止直接用 QuantScore/WashoutScore 排序)
        technical = (t1 * 0.45 + t3 * 0.30 + t1r['rs_score'] / 10 * 100 * 0.10
                     + t1r['vol_ready'] / 20 * 100 * 0.10
                     + t3r['theme_momentum'] / 10 * 100 * 0.05
                     - false_risk * 0.20)

        # FINAL_TRADE_PRIORITY = TECHNICAL_PRIORITY × FUNDAMENTAL_MULTIPLIER × MARKET_MULTIPLIER
        fq = _fundamental_quality((fund_by_code or {}).get(ts_code))
        fq_mult = _fundamental_multiplier(fq)
        final_priority = technical * fq_mult * mkt_mult

        # 触发价 / 失效价
        trigger = c['breakout_price']
        already = c['close'] > trigger
        if already:
            invalid = max(trigger * 0.97, c['ma20'] * 0.99)   # 已突破: 跌回突破位-3%或MA20失效
        else:
            invalid = min(c['ma20'], c['platform_low'])        # 未突破: 跌破MA20/平台低点失效

        rows.append({
            '代码': ts_code,
            'T1评分': round(t1, 1),
            'T3评分': round(t3, 1),
            '突破状态': state,
            '突破距离': distance,
            '假突破风险': round(false_risk, 0),
            '技术优先级': round(technical, 1),
            'FQ基本面分': round(fq, 1),
            '基本面乘数': fq_mult,
            '最终交易优先级': round(final_priority, 1),
            '突破等级': grade,
            '关键突破价': round(trigger, 2),
            '失效价': round(invalid, 2),
            '建议仓位': _position_of(grade, false_risk),
            'T1_价格位置': round(t1r['price_pos'], 1),
            'T1_量能准备': round(t1r['vol_ready'], 1),
            'T1_VWAP结构': round(t1r['vwap_struct'], 1),
            'T1_短线趋势': round(t1r['short_trend'], 1),
            'T1_资金流': round(t1r['money_flow'], 1),
            'T1_相对强度': round(t1r['rs_score'], 1),
            'T1_主题共振': round(t1r['theme_res'], 1),
            'T1_事件风险': round(t1r['event_score'], 1),
            'T3_平台质量': round(t3r['base_quality'], 1),
            'T3_趋势恢复': round(t3r['trend_recovery'], 1),
            'T3_缩量结构': round(t3r['vol_contraction'], 1),
            'T3_VWAP收复': round(t3r['vwap_reclaim'], 1),
            'T3_RS稳定': round(t3r['rs_stability'], 1),
            'T3_主题动量': round(t3r['theme_momentum'], 1),
            'T3_事件安全': round(t3r['event_safety'], 1),
            '事件风险级别': ev_level,
            '事件风险明细': ','.join(ev_reasons) if ev_reasons else '',
            '距离突破位%': round(c['dist_bt'], 2),
            '量比(当日/20日均量)': round(c['v1'], 2),
            '收盘振幅位置': round(c['range_pos'], 2),
        })

    br = pd.DataFrame(rows).set_index('代码')

    br.attrs['market_regime'] = regime
    br.attrs['market_mult'] = mkt_mult
    br.attrs['market_suit'] = {'BREAKOUT': '适合突破策略(全面进攻)',
                               'NORMAL': '正常市(正常操作)',
                               'SELECTIVE': '精选个股(只做最强)',
                               'DEFENSIVE': '防守市(以守代攻)'}.get(regime, '')
    br.attrs['idx_ret'] = idx_ret
    br.attrs['idx_close'] = idx_close
    return br


# ════════════════════════════════════════════════════════════
# 报告输出
# ════════════════════════════════════════════════════════════

def _stock_block(row, name, theme):
    """单只股票报告块"""
    confirm = (f"放量≥1.5×20日均量(当前{row['量比(当日/20日均量)']:.2f}) "
               f"+ 收盘站上{row['关键突破价']:.2f} + 收于当日振幅上30%")
    lines = [
        f"  股票: {name} ({row.name})  主题: {theme}",
        f"    T1 Score: {row['T1评分']}   T3 Score: {row['T3评分']}   "
        f"Breakout Distance: {row['突破距离']}   Grade: {row['突破等级']}",
        f"    关键突破价: {row['关键突破价']}   距离: {row['距离突破位%']}%   "
        f"Volume Readiness: {row['T1_量能准备']}/20   RS: {row['T1_相对强度']}/10",
        f"    False Breakout Risk: {row['假突破风险']:.0f}/100   "
        f"事件风险: {row['事件风险级别']}{'(' + row['事件风险明细'] + ')' if row['事件风险明细'] else ''}",
        f"    FQ: {row['FQ基本面分']}  基本面乘数: ×{row['基本面乘数']}  "
        f"最终优先级: {row['最终交易优先级']} (技术{row['技术优先级']})",
        f"    确认条件: {confirm}",
        f"    失效价: {row['失效价']}   建议仓位: {row['建议仓位']}",
    ]
    return '\n'.join(lines)


def print_breakout_report(br, results_map, idx_close):
    """打印 ★ T+1/T+3 BREAKOUT READINESS REPORT"""
    if br is None or len(br) == 0:
        print('\n★ T+1/T+3 BREAKOUT READINESS: 无有效数据')
        return
    regime = br.attrs.get('market_regime', '')
    suit = br.attrs.get('market_suit', '')
    mkt_mult = br.attrs.get('market_mult', 1.0)
    print(f'\n{"="*160}')
    print(f'  ★ T+1 / T+3 BREAKOUT READINESS REPORT (突破准备度评分系统 V1.0)')
    print(f'{"="*160}')
    print(f'  市场状态 MARKET_REGIME: {regime}   市场适合: {suit}'
          f'   (上证={idx_close}, 5日{br.attrs.get("idx_ret", {}).get(5, 0):+.1f}%, '
          f'20日{br.attrs.get("idx_ret", {}).get(20, 0):+.1f}%)')
    print(f'  最终排序: FINAL_TRADE_PRIORITY = 技术优先级 × FQ基本面乘数 × 市场乘数'
          f'(×{mkt_mult:.2f}@{regime})'
          f'   [FQ≥80→×1.05 | 65~79→×1.00 | 50~64→×0.95 | <50→×0.85]')

    # ── S_TRIGGER | 未来1日 ──
    s = br[br['突破等级'] == 'S_TRIGGER'].sort_values('最终交易优先级', ascending=False)
    print(f'\n  {"─"*76}')
    if len(s) == 0:
        print('  【S_TRIGGER｜未来1日】')
        print('  当前没有符合T+1高胜率突破条件的股票。')
    else:
        print(f'  【S_TRIGGER｜未来1日】({len(s)}只)')
        for code, row in s.iterrows():
            m = results_map.get(code, {})
            print(_stock_block(row, m.get('名称', ''), _clean_theme(m.get('主题'))))

    # ── A_NEAR | 未来1~3日 ──
    a = br[br['突破等级'] == 'A_NEAR'].sort_values('最终交易优先级', ascending=False).head(10)
    print(f'\n  {"─"*76}')
    if len(a) == 0:
        print('  【A_NEAR｜未来1~3日】无')
    else:
        print(f'  【A_NEAR｜未来1~3日】(前{len(a)}只)')
        for code, row in a.iterrows():
            m = results_map.get(code, {})
            print(_stock_block(row, m.get('名称', ''), _clean_theme(m.get('主题'))))

    # ── B_WATCH | 正在构建 ──
    b = br[br['突破等级'] == 'B_WATCH'].sort_values('最终交易优先级', ascending=False).head(10)
    print(f'\n  {"─"*76}')
    if len(b) == 0:
        print('  【B_WATCH｜正在构建】无')
    else:
        print(f'  【B_WATCH｜正在构建】(前{len(b)}只)')
        for code, row in b.iterrows():
            m = results_map.get(code, {})
            print(_stock_block(row, m.get('名称', ''), _clean_theme(m.get('主题')))
                  + f'\n    预计: {row["突破距离"]}')

    # ── C_BASE 概览 ──
    cb = br[br['突破等级'] == 'C_BASE']
    if len(cb) > 0:
        print(f'\n  【C_BASE｜平台构建中】{len(cb)}只 (D5+为主, 等待3~10日, 暂不建仓)')

    # ── NO TRADE ──
    print(f'\n  {"─"*76}')
    print('  【NO TRADE｜明确禁止交易】')
    any_nt = False
    for st in ['EVENT_RISK', 'OVERHEATED', 'FAILED_STRUCTURE']:
        sub = br[(br['突破状态'] == st) & (br['最终交易优先级'] >= br['最终交易优先级'].quantile(0.6))]
        sub = sub.sort_values('最终交易优先级', ascending=False).head(8)
        if len(sub) > 0:
            any_nt = True
            names = ', '.join(f"{results_map.get(c2, {}).get('名称', c2)}"
                              f"(T1={r['T1评分']})" for c2, r in sub.iterrows())
            print(f'    {st}: {names}')
    if not any_nt:
        print('    无 (高优先级候选中无事件/过热/结构破坏股)')

    # ── 明日最可能突破 Top3 (PRIMARY_BUY/NEAR_TRIGGER/D0-D1, 按T1) ──
    print(f'\n  {"─"*76}')
    cand_t1 = br[(br['T1评分'] >= 85) &
                 (~br['突破状态'].isin(['EVENT_RISK', 'OVERHEATED', 'FAILED_STRUCTURE',
                                       'WAIT_PULLBACK']))]
    cand_t1 = cand_t1.sort_values('T1评分', ascending=False).head(3)
    if len(cand_t1) == 0:
        print('  1) 明日最可能突破 Top3:')
        print('     当前没有符合T+1高胜率突破条件的股票。')
    else:
        print('  1) 明日最可能突破 Top3:')
        for i, (code, row) in enumerate(cand_t1.iterrows(), 1):
            m = results_map.get(code, {})
            print(f'     {i}. {m.get("名称", "")} ({code}) T1={row["T1评分"]} '
                  f'T3={row["T3评分"]} 状态={row["突破状态"]} 距离={row["突破距离"]} '
                  f'触发价={row["关键突破价"]} 失效价={row["失效价"]} 仓位={row["建议仓位"]}')

    # ── 未来3日最可能突破 Top5 (按T3) ──
    print(f'\n  2) 未来3日最可能突破 Top5:')
    cand_t3 = br[(br['T3评分'] >= 80) &
                 (~br['突破状态'].isin(['EVENT_RISK', 'OVERHEATED', 'FAILED_STRUCTURE']))]
    cand_t3 = cand_t3.sort_values('T3评分', ascending=False).head(5)
    if len(cand_t3) == 0:
        print('     当前没有明确的T+3突破候选，继续等待。')
    else:
        for i, (code, row) in enumerate(cand_t3.iterrows(), 1):
            m = results_map.get(code, {})
            print(f'     {i}. {m.get("名称", "")} ({code}) T3={row["T3评分"]} '
                  f'T1={row["T1评分"]} 状态={row["突破状态"]} 距离={row["突破距离"]} '
                  f'平台质量={row["T3_平台质量"]}/25 触发价={row["关键突破价"]} '
                  f'失效价={row["失效价"]} 仓位={row["建议仓位"]}')

    # ── 状态分布 ──
    print(f'\n  3) 突破状态分布: '
          + ', '.join(f'{k}={v}' for k, v in br['突破状态'].value_counts().items()))
    print(f'{"="*160}')
