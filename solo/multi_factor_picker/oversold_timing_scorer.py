"""
超跌择时评分模块 — 业绩预增股的左侧/左侧偏右买入时机
=====================================================
核心理念：基本面优质的股票（预告增速≥30%）在冲高回调后，
由于基本面未变，回调创造安全边际。捕捉回调末端、卖压衰竭、
即将反弹的买入时机。

7因子评分体系（满分100分）:
  F1: 回撤深度(20%) — 从20日高点回落幅度
  F2: 缩量程度(15%) — 回调期量比（卖压衰竭证据，权重下调因强势市场反向）
  F3: 支撑强度(15%) — 距MA20/MA60距离
  F4: RSI超卖(15%) — RSI(6)超卖区域及拐头
  F5: K线止跌(10%) — 下影线/小阳线/十字星等止跌形态（权重下调，区分度不足）
  F6: 基本面锚定(10%) — 预告增速越高，安全垫越厚
  F7: 趋势保护(15%) — 中长期均线仍在上升通道（权重上调，3月/5月回测显示最强区分度）
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple


def calc_oversold_factors(daily: pd.DataFrame,
                          forecast_profit_yoy: float = 0,
                          factor_df: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    """
    计算单只股票的全部7个超跌因子原始值

    Parameters
    ----------
    daily : DataFrame
        个股日线数据（至少60个交易日），需含 trade_date, open, high, low, close, vol
    forecast_profit_yoy : float
        中报预告利润同比增速（%）
    factor_df : DataFrame, optional
        stk_factor_pro 专业版技术因子数据。若提供，优先使用其中的预计算指标：
        - rsi_hfq_6  代替手动RSI(6) Wilder平滑计算
        - ma_hfq_20  代替手动MA20计算
        - ma_hfq_60  代替手动MA60计算
        - close_hfq/high_hfq/low_hfq/open_hfq 作为后复权价格数据
        - vol 作为成交量数据
    Returns
    -------
    dict: 各因子原始值 + 辅助数据
    """
    if daily is None or len(daily) < 40:
        return None

    # ── 决定数据源：优先使用 stk_factor_pro 后复权数据 ──
    use_factor_df = (factor_df is not None and len(factor_df) >= 40
                     and 'close_hfq' in factor_df.columns)

    if use_factor_df:
        fdf = factor_df.sort_values('trade_date').reset_index(drop=True)
        closes = fdf['close_hfq'].values.astype(float)
        highs = fdf['high_hfq'].values.astype(float)
        lows = fdf['low_hfq'].values.astype(float)
        opens = fdf['open_hfq'].values.astype(float)
        vols = fdf['vol'].values.astype(float)
    else:
        df = daily.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].values.astype(float)
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        opens = df['open'].values.astype(float)
        vols = df['vol'].values.astype(float)

    price = float(closes[-1])
    n = len(closes)
    result = {}

    # ── F1: 回撤深度 (20%) ──
    # 从最近20日的最高点回撤幅度
    lookback = min(20, n)
    recent_high = float(np.max(closes[-lookback:]))
    drawdown_pct = (recent_high - price) / recent_high * 100
    result['drawdown_pct'] = drawdown_pct

    # ── F2: 缩量程度 (20%) ──
    # 回调期的量比 = 近5日均量 / 近20日均量
    vol_5 = float(np.mean(vols[-5:])) if n >= 5 else 0
    vol_20 = float(np.mean(vols[-20:])) if n >= 20 else vol_5
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 99
    result['vol_ratio'] = vol_ratio

    # ── F3: 支撑强度 (15%) ──
    # 到MA20的距离 + MA20方向
    if use_factor_df and 'ma_hfq_20' in fdf.columns:
        ma20_arr = fdf['ma_hfq_20'].values.astype(float)
        ma20 = float(ma20_arr[-1])
        ma20_dir = 0
        if len(ma20_arr) >= 5:
            ma20_prev = float(ma20_arr[-5])
            ma20_dir = 1 if ma20 > ma20_prev * 1.005 else (-1 if ma20 < ma20_prev * 0.995 else 0)
    else:
        ma20 = float(np.mean(closes[-20:])) if n >= 20 else None
        ma20_dir = 0
        if ma20 and ma20 > 0 and n >= 25:
            ma20_prev = float(np.mean(closes[-25:-5]))
            ma20_dir = 1 if ma20 > ma20_prev * 1.005 else (-1 if ma20 < ma20_prev * 0.995 else 0)

    dist_to_ma20_pct = None
    if ma20 and ma20 > 0:
        dist_to_ma20_pct = (price - ma20) / ma20 * 100

    # 到MA60的距离
    if use_factor_df and 'ma_hfq_60' in fdf.columns:
        ma60 = float(fdf['ma_hfq_60'].iloc[-1])
    else:
        ma60 = float(np.mean(closes[-60:])) if n >= 60 else None

    dist_to_ma60_pct = None
    if ma60 and ma60 > 0:
        dist_to_ma60_pct = (price - ma60) / ma60 * 100

    result['ma20'] = ma20
    result['ma20_dir'] = ma20_dir
    result['dist_to_ma20_pct'] = dist_to_ma20_pct
    result['ma60'] = ma60
    result['dist_to_ma60_pct'] = dist_to_ma60_pct

    # ── F4: RSI超卖 (15%) ──
    # 优先使用 stk_factor_pro 预计算的 RSI(6) 后复权值
    rsi_6 = None
    rsi_trend = None
    if use_factor_df and 'rsi_hfq_6' in fdf.columns:
        rsi_arr = fdf['rsi_hfq_6'].values.astype(float)
        rsi_6 = round(float(rsi_arr[-1]), 1)
        if len(rsi_arr) >= 2:
            rsi_prev = float(rsi_arr[-2])
            rsi_trend = 1 if rsi_6 > rsi_prev else (-1 if rsi_6 < rsi_prev else 0)
    elif n >= 20:
        # 手动 Wilder 平滑法（后备方案）
        avg_gain, avg_loss = 0.0, 0.0
        for j in range(1, 7):
            ch = closes[j] - closes[j-1]
            if ch > 0:
                avg_gain += ch
            else:
                avg_loss += abs(ch)
        avg_gain /= 6.0
        avg_loss /= 6.0
        rsi_prev = 100.0 if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
        rsi_curr = rsi_prev
        for j in range(7, n):
            ch = closes[j] - closes[j-1]
            gain = ch if ch > 0 else 0.0
            loss = abs(ch) if ch < 0 else 0.0
            avg_gain = (avg_gain * 5.0 + gain) / 6.0
            avg_loss = (avg_loss * 5.0 + loss) / 6.0
            rsi_prev = rsi_curr
            rsi_curr = 100.0 if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
        rsi_6 = round(rsi_curr, 1)
        rsi_trend = 1 if rsi_curr > rsi_prev else (-1 if rsi_curr < rsi_prev else 0)

    result['rsi_6'] = rsi_6
    result['rsi_trend'] = rsi_trend

    # ── F5: K线止跌 (15%) ──
    # 分析最近3根K线的形态组合
    # 1. 下影线比例
    # 2. 是否收阳
    # 3. 是否连续缩量小实体
    # 4. 连续阳线衰减：连续多日收阳后不再是止跌信号
    lower_shadow_ratios = []
    body_ratios = []
    is_yang = []

    for i in range(-3, 0):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        upper = h - max(o, c)
        lower = min(o, c) - l
        body = abs(c - o)
        total_range = h - l
        if total_range > 0:
            lower_shadow_ratios.append(lower / total_range)
            body_ratios.append(body / total_range)
        else:
            lower_shadow_ratios.append(0)
            body_ratios.append(1)
        is_yang.append(c > o)

    last_lower_shadow = lower_shadow_ratios[-1] if lower_shadow_ratios else 0
    last_is_yang = is_yang[-1] if is_yang else False
    last_body_ratio = body_ratios[-1] if body_ratios else 1

    # 连续缩量判断
    vol_3 = vols[-3:] if n >= 3 else vols
    vol_shrinking = len(vol_3) >= 2 and vol_3[-1] < vol_3[-2] * 0.9

    # 连续阳线计数（从最新K线往前数）
    consecutive_up_days = 0
    for i in range(-1, -min(10, n), -1):
        if closes[i] > opens[i]:
            consecutive_up_days += 1
        else:
            break

    result['last_lower_shadow_ratio'] = last_lower_shadow
    result['last_is_yang'] = last_is_yang
    result['last_body_ratio'] = last_body_ratio
    result['vol_shrinking'] = vol_shrinking
    result['consecutive_up_days'] = consecutive_up_days

    # ── F6: 基本面锚定 (10%) ──
    # 预告增速越高，容错空间越大
    result['forecast_profit_yoy'] = forecast_profit_yoy

    # ── F7: 趋势保护 (5%) ──
    # MA50 > MA120（整体仍在上升通道）
    ma50 = float(np.mean(closes[-50:])) if n >= 50 else None
    ma120 = float(np.mean(closes[-120:])) if n >= 120 else None
    trend_protect = False
    if ma50 and ma120:
        trend_protect = ma50 > ma120
    result['ma50'] = ma50
    result['ma120'] = ma120
    result['trend_protect'] = trend_protect

    return result


def _default_market_params() -> Dict:
    """默认市场参数（震荡市标准）"""
    return {
        'min_score': 80,
        'f1_peak_start': 8, 'f1_peak_end': 15,
        'f1_penalty_after': 25,
        'f2_vol_good': 0.80,
        'f4_rsi_os_low': 25, 'f4_rsi_os_high': 45,
        'f5_min_shadow': 0.40,
    }


def score_oversold(factors: Dict,
                   market_params: Optional[Dict] = None) -> Tuple[float, Dict[str, float]]:
    """
    将7个超跌因子原始值映射为0-100分，加权合成

    Parameters
    ----------
    factors : dict
        calc_oversold_factors() 的返回值
    market_params : dict, optional
        市场状态动态参数。由 market_regime.detect_market_regime() 返回的 params 字典。
        包含 min_score, f1_peak_start/end, f2_vol_good, f4_rsi_os_low/high, f5_min_shadow 等。
        为 None 时使用震荡市标准参数。

    Returns
    -------
    (total_score, sub_scores) : (float, dict)
        total_score: 0-100综合评分
        sub_scores: 各因子单项得分明细
    """
    if factors is None:
        return 0, {}

    mp = _default_market_params()
    if market_params is not None:
        mp.update({k: v for k, v in market_params.items() if k in mp})

    sub = {}

    # 提取动态参数
    ps = mp['f1_peak_start']   # 回撤满分区间起始
    pe = mp['f1_peak_end']     # 回撤满分区间结束
    pa = mp['f1_penalty_after']  # 超过此值开始惩罚
    vol_good = mp['f2_vol_good']  # 缩量阈值
    rsi_low = mp['f4_rsi_os_low']   # RSI超卖下界
    rsi_high = mp['f4_rsi_os_high']  # RSI超卖上界
    shadow_min = mp['f5_min_shadow']  # 下影线最低要求

    # ── F1: 回撤深度 20分 ──
    d = factors.get('drawdown_pct', 0)
    # 根据市场状态动态调整评分曲线：
    #   [0, ps]: 线性从0上升到60分（ps%以内回撤不够）
    #   [ps, pe]: 满分区间（60→100分）
    #   [pe, pa]: 缓慢下降（100→80分，回撤太深但可接受）
    #   [pa, pa+15]: 快速下降（80→0分，趋势可能破位）
    #   > pa+15: 0分
    if d <= 0:
        f1 = 0
    elif d <= ps:
        f1 = d / ps * 60
    elif d <= pe:
        f1 = 60 + (d - ps) / (pe - ps) * 40 if pe > ps else 100
    elif d <= pa:
        f1 = 100 - (d - pe) / (pa - pe) * 20
    elif d <= pa + 15:
        f1 = 80 - (d - pa) / 15 * 80
    else:
        f1 = 0
    sub['F1回撤深度'] = min(100, max(0, f1))

    # ── F2: 缩量程度 20分 ──
    vr = factors.get('vol_ratio', 99)
    # 动态缩量阈值：弱势市场要求更低的量比
    #   < vol_good*0.6: 极度缩量 100分
    #   [vol_good*0.6, vol_good]: 良好缩量 100→80分
    #   [vol_good, 1.0]: 正常量 80→50分
    #   [1.0, 1.5]: 略微放量 50→10分
    #   >1.5: 0分
    vol_full = vol_good * 0.6
    if vr <= vol_full:
        f2 = 100
    elif vr <= vol_good:
        f2 = 100 - (vr - vol_full) / (vol_good - vol_full) * 20
    elif vr <= 1.0:
        f2 = 80 - (vr - vol_good) / (1.0 - vol_good) * 30
    elif vr <= 1.5:
        f2 = 50 - (vr - 1.0) / 0.5 * 40
    else:
        f2 = 0
    sub['F2缩量程度'] = min(100, max(0, f2))

    # ── F3: 支撑强度 15分 ──
    dist = factors.get('dist_to_ma20_pct')
    ma20_dir = factors.get('ma20_dir', 0)
    f3 = 0
    if dist is not None:
        if -3 <= dist <= 3:
            dist_score = 100
        elif 3 < dist <= 8:
            dist_score = 80 - (dist - 3) / 5 * 40
        elif -8 <= dist < -3:
            dist_score = 60 - (abs(dist) - 3) / 5 * 30
        elif 8 < dist <= 15:
            dist_score = 40 - (dist - 8) / 7 * 30
        elif -15 <= dist < -8:
            dist_score = 30 - (abs(dist) - 8) / 7 * 20
        else:
            dist_score = 0

        dir_bonus = 0
        if ma20_dir == 1:
            dir_bonus = 20
        elif ma20_dir == 0:
            dir_bonus = 10
        elif ma20_dir == -1:
            dir_bonus = -20

        f3 = dist_score + dir_bonus

    dist60 = factors.get('dist_to_ma60_pct')
    if dist60 is not None and dist60 < 0 and abs(dist60) < 5:
        f3 += 10
    sub['F3支撑强度'] = min(100, max(0, f3))

    # ── F4: RSI超卖 15分 ──
    rsi = factors.get('rsi_6')
    rsi_trend = factors.get('rsi_trend', 0)
    f4 = 0
    if rsi is not None:
        # 动态RSI阈值：
        #   < rsi_low: 严重超卖 100分
        #   [rsi_low, rsi_low+10]: 超卖区 100→70分
        #   [rsi_low+10, rsi_high]: 中性偏低 70→40分
        #   [rsi_high, rsi_high+15]: 中性 40→10分
        #   > rsi_high+15: 0分
        rsi_mid = rsi_low + 10
        if rsi < rsi_low:
            f4 = 100
        elif rsi <= rsi_mid:
            f4 = 100 - (rsi - rsi_low) / (rsi_mid - rsi_low) * 30
        elif rsi <= rsi_high:
            f4 = 70 - (rsi - rsi_mid) / (rsi_high - rsi_mid) * 30
        elif rsi <= rsi_high + 15:
            f4 = 40 - (rsi - rsi_high) / 15 * 30
        else:
            f4 = 0

        if rsi_trend == 1:
            f4 += 15
        elif rsi_trend == -1:
            f4 -= 10
    sub['F4_RSI超卖'] = min(100, max(0, f4))

    # ── F5: K线止跌 15分 ──
    f5 = 0
    ls = factors.get('last_lower_shadow_ratio', 0)
    # 动态下影线评分：弱势市场要求更高的下影线比例
    #   >= shadow_min*1.5: 强止跌信号 40分
    #   >= shadow_min: 一般止跌信号 25分
    #   >= shadow_min*0.5: 弱止跌 10分
    high_shadow = shadow_min * 1.5
    mid_shadow = shadow_min
    low_shadow = shadow_min * 0.5

    if ls >= high_shadow:
        f5 += 40
    elif ls >= mid_shadow:
        f5 += 25
    elif ls >= low_shadow:
        f5 += 10

    if factors.get('last_is_yang', False):
        f5 += 30
    else:
        f5 += 5

    br = factors.get('last_body_ratio', 1)
    if br <= 0.3:
        f5 += 20
    elif br <= 0.5:
        f5 += 10

    if factors.get('vol_shrinking', False):
        f5 += 10

    # ── 连续阳线衰减：连续3+天阳线后止跌信号失效 ──
    consec_up = factors.get('consecutive_up_days', 0)
    if consec_up >= 4:
        f5 -= 50  # 4天+连续阳线→已脱离超跌区，几乎全扣止跌分
    elif consec_up == 3:
        f5 -= 25  # 3天连续阳线→止跌信号已过期，大幅扣分
    elif consec_up == 2:
        f5 -= 5   # 2天连续阳线→轻微衰减

    sub['F5_K线止跌'] = min(100, max(0, f5))

    # ── F6: 基本面锚定 10分 ──
    profit = factors.get('forecast_profit_yoy', 0)
    # 增速越高，安全垫越厚
    if profit >= 500:
        f6 = 100
    elif profit >= 200:
        f6 = 80 + (profit - 200) / 300 * 20
    elif profit >= 100:
        f6 = 60 + (profit - 100) / 100 * 20
    elif profit >= 50:
        f6 = 40 + (profit - 50) / 50 * 20
    elif profit >= 30:
        f6 = 20 + (profit - 30) / 20 * 20
    elif profit >= 0:
        f6 = profit / 30 * 20
    else:
        f6 = 0
    sub['F6基本面锚定'] = min(100, max(0, f6))

    # ── F7: 趋势保护 5分 ──
    if factors.get('trend_protect', False):
        f7 = 100
    else:
        f7 = 0
    sub['F7趋势保护'] = f7

    # ── 加权合成 ──
    # 权重依据3月/5月回测分析调整：
    #   F2从20%→15%（强势市场反向特征）
    #   F5从15%→10%（区分度持续不足）
    #   F7从5%→15%（两月回测最强区分度因子）
    weights = {
        'F1回撤深度': 0.20,
        'F2缩量程度': 0.15,
        'F3支撑强度': 0.15,
        'F4_RSI超卖': 0.15,
        'F5_K线止跌': 0.10,
        'F6基本面锚定': 0.10,
        'F7趋势保护': 0.15,
    }

    total = sum(sub[k] * weights[k] for k in weights)

    return round(total, 1), sub


def classify_oversold_signal(total_score: float,
                              min_score_strong: float = 80,
                              min_score_moderate: float = 65) -> Tuple[str, str]:
    """
    根据超跌评分给出信号等级（支持动态市场阈值）

    Parameters
    ----------
    total_score : float
        0-100综合评分
    min_score_strong : float
        强烈反弹阈值（动态调整，默认80）
    min_score_moderate : float
        一般反弹阈值（动态调整，默认65）

    Returns
    -------
    (signal_level, signal_desc) : (str, str)
    """
    if total_score >= min_score_strong:
        return '强烈超跌反弹', '回撤充分+卖压衰竭+止跌信号共振，极高胜率左侧买入时机'
    elif total_score >= min_score_moderate:
        return '一般超跌反弹', '回撤后缩量止跌，可轻仓试探，等待放量确认'
    else:
        return '等待观望', '回调未到位或卖压未衰竭，继续观察'
