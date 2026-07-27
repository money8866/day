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
    """
    if total_score >= min_score_strong:
        return '强烈超跌反弹', '回撤充分+卖压衰竭+止跌信号共振，极高胜率左侧买入时机'
    elif total_score >= min_score_moderate:
        return '一般超跌反弹', '回撤后缩量止跌，可轻仓试探，等待放量确认'
    else:
        return '等待观望', '回调未到位或卖压未衰竭，继续观察'


# ====================================================================
# 新算法：震荡缩量到尾声 + MACD波动收窄
# ====================================================================
# 核心理念：中报预增股经过前期上涨后，进入震荡整理阶段。
# 震荡缩量到尾声 = 价格横盘 + 成交量持续萎缩
# MACD波动收窄 = DIF-DEA间距收敛 + MACD柱缩短
# 两者共振时，意味着浮动筹码清洗干净、多空平衡即将打破，
# 是二次拉升或反弹的前兆信号。
# 总分100分：震荡缩量(50分) + MACD收窄(50分)
# ====================================================================


def calc_consolidation_factors(factor_df: pd.DataFrame) -> Dict[str, float]:
    """计算震荡缩量+MACD收窄因子原始值

    factor_df 需包含: close, high, low (不复权用于震荡区间),
                      close_hfq (后复权用于MACD归一化),
                      vol, macd_dif_hfq, macd_dea_hfq, macd_hfq

    Returns
    -------
    dict: 各因子原始值
    """
    if factor_df is None or len(factor_df) < 40:
        return None

    df = factor_df.sort_values('trade_date').reset_index(drop=True)
    n = len(df)
    result = {}

    # 价格用不复权(close)，避免复权因子导致区间失真
    price = float(df['close'].iloc[-1])
    result['close'] = price

    # ── Part 1: 震荡缩量 ──

    # 价格震荡范围 (近20日不复权high-low / 收盘价%)
    lookback = min(20, n)
    recent_high = float(df['high'].iloc[-lookback:].max())
    recent_low = float(df['low'].iloc[-lookback:].min())
    osc_range = (recent_high - recent_low) / price * 100
    result['osc_range_pct'] = round(osc_range, 2)

    # 量比 = 5日均量 / 20日均量
    vols = df['vol'].values.astype(float)
    vol_5 = float(np.mean(vols[-5:]))
    vol_20 = float(np.mean(vols[-20:]))
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 99
    result['vol_ratio'] = round(vol_ratio, 3)

    # 量能趋势: 近15日成交量线性斜率(归一化)
    if n >= 15:
        recent_vols = vols[-15:].astype(float)
        x = np.arange(len(recent_vols))
        slope = np.polyfit(x, recent_vols, 1)[0]
        vol_slope = slope / (np.mean(recent_vols) + 1e-10) * 100
    else:
        vol_slope = 0.0
    result['vol_slope_pct'] = round(vol_slope, 4)

    # 近期量能是否持续创新低: 近5日最低量 < 近20日最低量
    vol_5_low = float(np.min(vols[-5:]))
    vol_20_low = float(np.min(vols[-20:]))
    result['vol_new_low'] = (vol_5_low <= vol_20_low * 1.05)

    # ── Part 2: MACD收窄 ──

    dif = df['macd_dif_hfq'].values.astype(float)
    dea = df['macd_dea_hfq'].values.astype(float)
    macd_hist = df['macd_hfq'].values.astype(float)

    # DIF-DEA间距（归一化到后复权价格%）
    close_hfq = df['close_hfq'].values.astype(float)
    current_hfq_price = float(close_hfq[-1])

    current_gap = abs(float(dif[-1] - dea[-1]))
    lookback_gap = min(20, n)
    gaps = np.abs(dif[-lookback_gap:] - dea[-lookback_gap:])
    max_gap = float(np.max(gaps))
    gap_ratio = current_gap / max_gap if max_gap > 1e-10 else 0
    result['macd_gap_current'] = round(current_gap, 4)
    result['macd_gap_max'] = round(max_gap, 4)
    result['macd_gap_ratio'] = round(gap_ratio, 4)

    # MACD柱收缩: 近5日绝对值均值 < 前5日 * 0.8
    if n >= 10:
        recent_abs = np.abs(macd_hist[-5:])
        prev_abs = np.abs(macd_hist[-10:-5])
        macd_shrinking = float(np.mean(recent_abs)) < float(np.mean(prev_abs)) * 0.85
    else:
        macd_shrinking = False
    result['macd_shrinking'] = macd_shrinking

    # MACD绝对值趋势: 近5日 vs 前5日
    if n >= 10:
        result['macd_abs_ratio'] = round(
            float(np.mean(np.abs(macd_hist[-5:]))) / (
                float(np.mean(np.abs(macd_hist[-10:-5]))) + 1e-10), 3
        )
    else:
        result['macd_abs_ratio'] = 1.0

    # MACD当前位置 = macd_hfq / close_hfq * 100 (% of price, 归一化)
    macd_val = float(macd_hist[-1])
    macd_norm = macd_val / current_hfq_price * 100 if current_hfq_price > 0 else 0
    result['macd_value'] = round(macd_val, 4)
    result['macd_norm'] = round(macd_norm, 4)

    # MACD位置描述（使用归一化值）
    if abs(macd_norm) < 0.5:
        pos_desc = '零轴附近'
    elif macd_norm > 0:
        pos_desc = '零轴上'
    else:
        pos_desc = '零轴下'
    result['macd_position'] = pos_desc

    # DIF与DEA是否金叉/死叉
    if n >= 2:
        prev_dif, prev_dea = float(dif[-2]), float(dea[-2])
        curr_cross = dif[-1] > dea[-1]
        prev_cross = prev_dif > prev_dea
        if curr_cross and not prev_cross:
            result['macd_cross'] = '金叉'
        elif not curr_cross and prev_cross:
            result['macd_cross'] = '死叉'
        else:
            result['macd_cross'] = '无'
    else:
        result['macd_cross'] = '无'

    return result


def score_consolidation(factors: Dict) -> Tuple[float, Dict[str, float]]:
    """评分: 震荡缩量(50分) + MACD收窄(50分) = 100分"""
    if factors is None:
        return 0, {}

    sub = {}

    # ═══════════════════════════════════════════
    #   Part A: 震荡缩量评分 (50分)
    # ═══════════════════════════════════════════

    # A1. 价格震荡区间 (15分) — 横盘越标准越好
    osc = factors.get('osc_range_pct', 0)
    if 5 <= osc <= 12:
        f_osc = 15        # 完美震荡区间
    elif 3 <= osc < 5:
        f_osc = 12        # 偏窄但可接受
    elif 12 < osc <= 18:
        f_osc = 10        # 偏宽，仍在横盘范畴
    elif 18 < osc <= 25:
        f_osc = 5         # 过宽，可能仍在趋势中
    elif osc < 3:
        f_osc = 8         # 太窄，可能是持续阴跌或僵尸股
    else:
        f_osc = 2         # >25%，明显趋势中，非震荡
    # 接近最低点加分：价格靠近震荡区间下沿
    price = factors.get('close', 0)
    if price > 0 and 'close' in factors:
        # 已在前面震荡区间计算好了，这里不再重复加分
        pass
    sub['震荡区间'] = f_osc

    # A2. 量比缩量 (20分) — 量越缩越好
    vr = factors.get('vol_ratio', 99)
    if vr <= 0.55:
        f_vr = 20
    elif vr <= 0.70:
        f_vr = 17
    elif vr <= 0.85:
        f_vr = 13
    elif vr <= 1.00:
        f_vr = 8
    elif vr <= 1.20:
        f_vr = 3
    else:
        f_vr = 0
    sub['量比缩量'] = f_vr

    # A3. 量能趋势 (15分) — 持续缩量趋势
    vs = factors.get('vol_slope_pct', 0)
    if vs < -1.5:
        f_vs = 15         # 强烈缩量趋势
    elif vs < -0.8:
        f_vs = 13
    elif vs < -0.3:
        f_vs = 10         # 温和缩量
    elif vs < 0.3:
        f_vs = 7          # 基本走平
    elif vs < 1.0:
        f_vs = 3
    else:
        f_vs = 0          # 放量，非缩量整理
    sub['量能趋势'] = f_vs

    consolidate_score = f_osc + f_vr + f_vs
    sub['震荡缩量'] = consolidate_score

    # ═══════════════════════════════════════════
    #   Part B: MACD收窄评分 (50分)
    # ═══════════════════════════════════════════

    # B1. DIF-DEA间距收敛 (25分) — 间距越小越好
    gr = factors.get('macd_gap_ratio', 1)
    if gr <= 0.15:
        f_gap = 25        # 高度收敛，近乎粘合
    elif gr <= 0.30:
        f_gap = 22
    elif gr <= 0.45:
        f_gap = 18
    elif gr <= 0.60:
        f_gap = 14
    elif gr <= 0.80:
        f_gap = 8
    else:
        f_gap = 3         # 间距仍在扩大
    sub['DIF-DEA收敛'] = f_gap

    # B2. MACD柱收缩 (15分)
    if factors.get('macd_shrinking', False):
        f_shrink = 15
    else:
        # 即使没明显收缩，如果间距已经很小也部分得分
        if gr <= 0.30:
            f_shrink = 10
        else:
            f_shrink = 5
    sub['MACD柱收缩'] = f_shrink

    # B3. MACD位置 (10分) — 使用归一化 macd_norm (% of price)
    macd_norm = factors.get('macd_norm', 0)
    pos = factors.get('macd_position', '')
    cross = factors.get('macd_cross', '无')

    if abs(macd_norm) < 0.3:
        f_pos = 10        # 零轴附近，最佳
    elif -1.5 <= macd_norm < -0.3:
        f_pos = 9         # 零轴下，超卖区收敛可靠
    elif 0.3 <= macd_norm <= 1.5:
        f_pos = 7         # 零轴上但不高
    elif macd_norm < -1.5:
        f_pos = 6         # 远离零轴下方，跌过头
    else:
        f_pos = 5         # 远离零轴上方

    # 金叉加分
    if cross == '金叉':
        f_pos += 3

    sub['MACD位置'] = min(13, f_pos)

    macd_score = f_gap + f_shrink + min(13, f_pos)
    sub['MACD收窄'] = macd_score

    total = consolidate_score + macd_score

    return round(total, 1), sub


def classify_consolidation_signal(total_score: float) -> Tuple[str, str]:
    """根据震荡缩量+MACD收窄评分给出信号等级"""
    if total_score >= 80:
        return '强烈信号', '震荡缩量充分+MACD高度收敛，双重共振，变盘前兆'
    elif total_score >= 65:
        return '一般信号', '缩量震荡中+MACD趋向收敛，关注后续确认'
    elif total_score >= 50:
        return '弱信号', '震荡或MACD收敛一方不足，继续观察'
    else:
        return '无信号', '未满足震荡缩量或MACD收敛条件'


# ====================================================================
# 三级入场信号：缩量低吸(A) / MACD金叉确认(B) / 放量突破(C)
# ====================================================================
# 入场优先级权重：B类(最佳性价比) > C类(趋势确认) > A类(左侧试探)
# ====================================================================


def calc_entry_timing(factors: dict, factor_df: pd.DataFrame) -> dict:
    """
    在 calc_consolidation_factors 的基础上，计算入场时机信号

    Parameters
    ----------
    factors : dict
        calc_consolidation_factors() 的输出
    factor_df : DataFrame
        原始 stk_factor_pro DataFrame（需含 ma_bfq_5, high, low, close,
        macd_dif_hfq, macd_dea_hfq 等列）

    Returns
    -------
    dict: 包含 entry_signal, entry_score, entry_type, stop_loss,
          target, risk_reward 等
    """
    if factors is None or factor_df is None or len(factor_df) < 20:
        return {
            'entry_signal': False,
            'entry_score': 0,
            'entry_type': '无信号',
            'entry_type_name': '无',
            'detail': {},
        }

    df = factor_df.sort_values('trade_date').reset_index(drop=True)
    n = len(df)

    detail = {}

    # ── 从 factors 读取已有计算结果 ──
    close = factors.get('close', 0)
    vol_ratio = factors.get('vol_ratio', 99)
    osc_range = factors.get('osc_range_pct', 0)
    macd_gap_ratio = factors.get('macd_gap_ratio', 1)
    macd_cross = factors.get('macd_cross', '无')
    macd_norm = factors.get('macd_norm', 0)
    vol_slope = factors.get('vol_slope_pct', 0)
    macd_shrinking = factors.get('macd_shrinking', False)

    # ── 从 factor_df 读取额外数据 ──
    lookback = min(20, n)
    high_20d = float(df['high'].iloc[-lookback:].max())
    low_20d = float(df['low'].iloc[-lookback:].min())

    # 价格在震荡区间内的位置 (0~1)
    price_range = high_20d - low_20d
    price_position = (close - low_20d) / price_range if price_range > 1e-10 else 0.5
    detail['price_position'] = round(price_position, 4)

    # MA5（不复权）
    ma_bfq_5 = None
    if 'ma_bfq_5' in df.columns:
        ma_bfq_5 = float(df['ma_bfq_5'].iloc[-1])

    # MACD DIF / DEA（后复权）
    macd_dif = None
    macd_dea = None
    if 'macd_dif_hfq' in df.columns and 'macd_dea_hfq' in df.columns:
        macd_dif = float(df['macd_dif_hfq'].iloc[-1])
        macd_dea = float(df['macd_dea_hfq'].iloc[-1])

    # ── 计算震荡缩量分和MACD收窄分（与 score_consolidation 保持一致） ──

    # 震荡缩量 (满分50)
    if 5 <= osc_range <= 12:
        f_osc = 15
    elif 3 <= osc_range < 5:
        f_osc = 12
    elif 12 < osc_range <= 18:
        f_osc = 10
    elif 18 < osc_range <= 25:
        f_osc = 5
    elif osc_range < 3:
        f_osc = 8
    else:
        f_osc = 2

    if vol_ratio <= 0.55:
        f_vr = 20
    elif vol_ratio <= 0.70:
        f_vr = 17
    elif vol_ratio <= 0.85:
        f_vr = 13
    elif vol_ratio <= 1.00:
        f_vr = 8
    elif vol_ratio <= 1.20:
        f_vr = 3
    else:
        f_vr = 0

    if vol_slope < -1.5:
        f_vs = 15
    elif vol_slope < -0.8:
        f_vs = 13
    elif vol_slope < -0.3:
        f_vs = 10
    elif vol_slope < 0.3:
        f_vs = 7
    elif vol_slope < 1.0:
        f_vs = 3
    else:
        f_vs = 0

    consolidate_score = f_osc + f_vr + f_vs
    detail['震荡缩量分'] = consolidate_score

    # MACD收窄 (满分50)
    if macd_gap_ratio <= 0.15:
        f_gap = 25
    elif macd_gap_ratio <= 0.30:
        f_gap = 22
    elif macd_gap_ratio <= 0.45:
        f_gap = 18
    elif macd_gap_ratio <= 0.60:
        f_gap = 14
    elif macd_gap_ratio <= 0.80:
        f_gap = 8
    else:
        f_gap = 3

    if macd_shrinking:
        f_shrink = 15
    else:
        f_shrink = 10 if macd_gap_ratio <= 0.30 else 5

    if abs(macd_norm) < 0.3:
        f_pos = 10
    elif -1.5 <= macd_norm < -0.3:
        f_pos = 9
    elif 0.3 <= macd_norm <= 1.5:
        f_pos = 7
    elif macd_norm < -1.5:
        f_pos = 6
    else:
        f_pos = 5
    if macd_cross == '金叉':
        f_pos += 3
    f_pos = min(13, f_pos)

    macd_score = f_gap + f_shrink + f_pos
    detail['MACD收窄分'] = macd_score

    # ═══════════════════════════════════════════
    #  A类: 缩量低吸（左侧试探）
    # ═══════════════════════════════════════════
    a_raw = 0
    a_conds = []
    if price_position < 0.33:
        a_conds.append('价格在区间下1/3')
        a_raw += 25
    if vol_ratio < 0.6:
        a_conds.append('极度缩量')
        a_raw += 25
    if consolidate_score >= 30:
        a_conds.append('震荡缩量到位')
        a_raw += 20
    if close > low_20d * 1.02:
        a_conds.append('未创新低')
        a_raw += 10
    a_score = min(80, 60 + a_raw // 5) if a_raw >= 30 else 0
    a_detail = {
        'signal': a_raw >= 30,
        'score': a_score,
        'conditions': a_conds,
    }
    detail['A类信号'] = a_detail

    # ═══════════════════════════════════════════
    #  B类: MACD金叉确认（最佳买点）
    # ═══════════════════════════════════════════
    b_raw = 0
    b_conds = []
    if macd_cross == '金叉':
        b_conds.append('MACD金叉')
        b_raw += 30
    if vol_ratio is not None and 0.7 <= vol_ratio <= 1.2:
        b_conds.append('量能温和')
        b_raw += 20
    if ma_bfq_5 is not None and close > ma_bfq_5:
        b_conds.append('站上MA5')
        b_raw += 20
    if consolidate_score >= 25:
        b_conds.append('震荡缩量达标')
        b_raw += 15
    if macd_score >= 30:
        b_conds.append('MACD收窄达标')
        b_raw += 15
    b_score = min(100, 80 + b_raw // 10) if macd_cross == '金叉' and b_raw >= 30 else 0
    b_detail = {
        'signal': macd_cross == '金叉' and b_raw >= 30,
        'score': b_score,
        'conditions': b_conds,
    }
    detail['B类信号'] = b_detail

    # ═══════════════════════════════════════════
    #  C类: 放量突破（右侧追入）
    #  ⚠️ 必要条件：必须突破或放量，缺一不可
    # ═══════════════════════════════════════════
    c_raw = 0
    c_conds = []
    c_has_breakout = False
    c_has_volume = False
    if close > high_20d * 0.98:
        c_conds.append('接近/突破近期高点')
        c_raw += 30
        c_has_breakout = True
    if vol_ratio > 1.3:
        c_conds.append('放量')
        c_raw += 25
        c_has_volume = True
    if macd_dif is not None and macd_dea is not None and macd_dif > macd_dea:
        c_conds.append('MACD多头')
        c_raw += 20
    if consolidate_score >= 20:
        c_conds.append('震荡缩量基础')
        c_raw += 15
    # 核心条件: 必须突破 or 放量
    c_core_ok = c_has_breakout or c_has_volume
    c_score = min(90, 70 + c_raw // 5) if c_raw >= 30 and c_core_ok else 0
    c_detail = {
        'signal': c_core_ok and c_raw >= 30,
        'score': c_score,
        'conditions': c_conds,
    }
    detail['C类信号'] = c_detail

    # ═══════════════════════════════════════════
    #  选择最优信号（优先级 B > A > C）
    #  下跌市中A类(左侧低吸)比C类(追入)更安全
    # ═══════════════════════════════════════════
    selected_type = None
    selected_detail = None
    for st in ('B', 'A', 'C'):
        sd = {'B': b_detail, 'A': a_detail, 'C': c_detail}[st]
        if sd['signal']:
            selected_type = st
            selected_detail = sd
            break

    type_names = {
        'A': '缩量低吸(左侧试探)',
        'B': 'MACD金叉确认(最佳买点)',
        'C': '放量突破(右侧追入)',
    }

    result = {
        'entry_signal': selected_type is not None,
        'entry_type': selected_type if selected_type else '无信号',
        'entry_type_name': type_names.get(selected_type, '无'),
        'entry_score': selected_detail['score'] if selected_detail else 0,
        'close': close,
        'high_20d': high_20d,
        'low_20d': low_20d,
        'price_position': round(price_position, 4),
        'detail': detail,
    }

    # ── 止损价 ──
    recent_5_low = float(df['low'].iloc[-5:].min()) if n >= 5 else low_20d
    if selected_type == 'A':
        stop_loss = round(min(low_20d, recent_5_low), 2)
    elif selected_type == 'B':
        stop_loss = round(low_20d * 0.97, 2)
    elif selected_type == 'C':
        recent_3_low = float(df['low'].iloc[-3:].min()) if n >= 3 else low_20d
        stop_loss = round(min(high_20d * 0.95, recent_3_low), 2)
    else:
        stop_loss = round(low_20d * 0.95, 2)
    result['stop_loss'] = stop_loss

    # ── 止盈价 ──
    risk = close - stop_loss
    if risk <= 0:
        risk = close * 0.02
    if selected_type == 'A':
        target = round(close + risk * 2.0, 2)
    elif selected_type == 'B':
        target = round(close + risk * 2.5, 2)
    elif selected_type == 'C':
        target = round(close + risk * 1.8, 2)
    else:
        target = round(close + risk * 1.5, 2)
    result['target'] = target

    result['risk_reward'] = round((target - close) / risk, 2) if risk > 0 else 0

    return result


def score_entry_timing(entry_info: dict) -> tuple:
    """
    对入场时机评分，结合震荡缩量总分给出综合操作建议

    Returns
    -------
    (entry_score, entry_grade, action_advice)
    entry_score : float  0-100
    entry_grade : str    'A级最佳买点' / 'B级可入场' / 'C级观望' / 'D级等待'
    action_advice : str  具体操作建议字符串
    """
    if not entry_info or not entry_info.get('entry_signal'):
        return (0, 'D级等待', '当前无入场信号，继续观察等待')

    entry_type = entry_info.get('entry_type', '')
    entry_score = entry_info.get('entry_score', 0)
    risk_reward = entry_info.get('risk_reward', 0)

    rr_score = min(100, risk_reward / 3.0 * 100)
    final_score = round(entry_score * 0.7 + rr_score * 0.3, 1)

    # ── 等级判定 ──
    if entry_type == 'B' and entry_score >= 80 and risk_reward >= 2.0:
        grade = 'A级最佳买点'
    elif entry_type == 'B' and entry_score >= 70:
        grade = 'B级可入场'
    elif entry_type == 'C' and entry_score >= 75:
        grade = 'B级可入场'
    elif entry_type == 'A' and entry_score >= 60:
        grade = 'C级观望'
    elif entry_type in ('B', 'C') and entry_score >= 50:
        grade = 'C级观望'
    else:
        grade = 'D级等待'

    # ── 操作建议 ──
    close = entry_info.get('close', 0)
    stop_loss = entry_info.get('stop_loss', 0)
    target = entry_info.get('target', 0)
    type_name = entry_info.get('entry_type_name', '')

    if grade == 'A级最佳买点':
        advice = (
            f'★★★★★ {type_name}，综合评分{final_score}分，'
            f'风险收益比1:{risk_reward}\n'
            f'建议仓位：6-8成。入场价{close:.2f}附近，'
            f'止损{stop_loss:.2f}，止盈{target:.2f}'
        )
    elif grade == 'B级可入场':
        advice = (
            f'★★★☆☆ {type_name}，综合评分{final_score}分，'
            f'风险收益比1:{risk_reward}\n'
            f'建议仓位：3-5成。入场价{close:.2f}附近，'
            f'止损{stop_loss:.2f}，止盈{target:.2f}'
        )
    elif grade == 'C级观望':
        advice = (
            f'★☆☆☆☆ {type_name}，综合评分{final_score}分，'
            f'风险收益比1:{risk_reward}\n'
            f'条件部分满足，建议轻仓试探或等待进一步确认'
        )
    else:
        advice = (
            f'当前无合适入场信号（综合评分{final_score}分），建议继续等待'
        )

    return (final_score, grade, advice)
