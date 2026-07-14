# -*- coding: utf-8 -*-
"""
动量层因子

1. relative_strength - 主题相对强度 RS（主题等权指数 / 大盘指数）
2. momentum_acceleration - 动量加速度（5日动量 + 动量变化率）
3. adx_trend - ADX趋势强度
"""
import pandas as pd
import numpy as np


def calc_theme_index(klines: dict, window: int = 60) -> pd.DataFrame:
    """
    构建主题等权指数（成份股等权平均收盘价归一化）

    Returns:
        DataFrame(trade_date, theme_close)
    """
    if not klines:
        return pd.DataFrame()

    # 收集所有收盘价序列
    close_dict = {}
    for code, df in klines.items():
        if "close" in df.columns and "trade_date" in df.columns:
            s = df.set_index("trade_date")["close"]
            s.name = code
            close_dict[code] = s

    if not close_dict:
        return pd.DataFrame()

    close_df = pd.DataFrame(close_dict).sort_index()
    # 归一化到100起点
    norm_df = close_df / close_df.iloc[0] * 100
    # 等权平均
    theme_close = norm_df.mean(axis=1)
    return pd.DataFrame({"trade_date": theme_close.index, "theme_close": theme_close.values})


def calc_relative_strength(theme_index: pd.DataFrame, market_index: pd.DataFrame) -> dict:
    """
    主题相对强度 RS

    逻辑：
    - RS = 主题指数 / 大盘指数
    - RS持续走强（20日RS斜率>0）→ 跑赢大盘，看涨
    - RS顶背离（主题涨但RS不涨）→ 见顶信号

    Returns:
        {"score": 0-100, "rs": float, "rs_slope_20": float, "signal": str}
    """
    if theme_index.empty or market_index.empty:
        return {"score": 50, "rs": None, "rs_slope_20": 0, "signal": "数据不足"}

    # 合并
    t = theme_index.set_index("trade_date")["theme_close"]
    m = market_index.set_index("trade_date")
    # 找收盘价列
    close_col = "close" if "close" in m.columns else m.columns[0]
    m_close = m[close_col]
    m_close = m_close / m_close.iloc[0] * 100  # 归一化

    aligned = pd.DataFrame({"theme": t, "market": m_close}).dropna()
    if len(aligned) < 25:
        return {"score": 50, "rs": None, "rs_slope_20": 0, "signal": "数据不足"}

    rs = aligned["theme"] / aligned["market"]
    rs_current = rs.iloc[-1]
    rs_20_ago = rs.iloc[-20] if len(rs) >= 20 else rs.iloc[0]
    rs_slope = (rs_current - rs_20_ago) / rs_20_ago * 100  # 百分比变化

    # 关键修复：增加近5日RS变化，对近期急跌更敏感
    rs_5_ago = rs.iloc[-5] if len(rs) >= 5 else rs.iloc[0]
    rs_slope_5 = (rs_current - rs_5_ago) / rs_5_ago * 100

    # 评分逻辑
    score = 50
    signal = "中性"

    if rs_current > 1.0:  # 主题跑赢大盘
        score += 10  # 降低基础分（原15）
    else:
        score -= 10

    # 20日RS斜率（中期趋势）
    if rs_slope > 5:
        score += 15  # 降低（原25），避免历史涨幅掩盖近期下跌
        signal = "中期偏强"
    elif rs_slope > 2:
        score += 10
    elif rs_slope < -5:
        score -= 15
        signal = "中期偏弱"
    elif rs_slope < -2:
        score -= 10

    # 5日RS斜率（短期敏感度）—— 新增
    if rs_slope_5 > 3:
        score += 15
        if signal == "中期偏强":
            signal = "看涨"
    elif rs_slope_5 > 1:
        score += 8
    elif rs_slope_5 < -3:
        score -= 20  # 短期急跌，重罚
        signal = "短期急跌"
    elif rs_slope_5 < -1:
        score -= 10

    # RS顶背离判断（20日涨但5日跌）
    if rs_slope > 5 and rs_slope_5 < -2:
        score -= 15  # 顶背离
        signal = "顶背离警示"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "rs": round(float(rs_current), 4),
            "rs_slope_20": round(float(rs_slope), 2), "signal": signal}


def calc_momentum_acceleration(theme_index: pd.DataFrame) -> dict:
    """
    动量加速度

    逻辑：
    - 一阶导（5日动量）> 0 → 上涨趋势
    - 二阶导（动量变化率）> 0 → 加速上涨
    - 动量>0但加速度<0 → 顶部减速

    Returns:
        {"score": 0-100, "momentum_5": float, "acceleration": float, "signal": str}
    """
    if theme_index.empty or len(theme_index) < 15:
        return {"score": 50, "momentum_5": 0, "acceleration": 0, "signal": "数据不足"}

    close = theme_index.set_index("trade_date")["theme_close"]

    # 1日动量（当日涨跌）—— 新增，提高短期敏感度
    mom_1 = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0
    # 3日动量
    mom_3 = (close.iloc[-1] / close.iloc[-3] - 1) * 100 if len(close) >= 3 else 0
    # 5日动量
    mom_5 = (close.iloc[-1] / close.iloc[-5] - 1) * 100
    # 10日动量
    mom_10 = (close.iloc[-1] / close.iloc[-10] - 1) * 100
    # 加速度 = 近3日动量 - 前3日动量
    acceleration = mom_3 - (mom_5 - mom_3)

    score = 50
    signal = "中性"

    # 1日动量判断（当日急跌急涨）
    if mom_1 < -3:
        score -= 25  # 当日大跌
        signal = "当日大跌"
    elif mom_1 < -1.5:
        score -= 15
        signal = "当日下跌"
    elif mom_1 > 3:
        score += 15
        signal = "当日大涨"
    elif mom_1 > 1.5:
        score += 8

    # 3日动量判断
    if mom_3 < -5:
        score -= 15
        if signal == "中性":
            signal = "短期下跌"
    elif mom_3 < -2:
        score -= 8
    elif mom_3 > 5:
        score += 15
        if signal == "中性":
            signal = "短期上涨"
    elif mom_3 > 2:
        score += 8

    # 5日动量判断
    if mom_5 > 3:
        score += 10
        if signal == "中性":
            signal = "看涨"
    elif mom_5 > 1:
        score += 5
    elif mom_5 < -3:
        score -= 10
    elif mom_5 < -1:
        score -= 5

    # 加速度判断
    if mom_5 > 0 and acceleration > 1:
        score += 20  # 主升浪初期
        signal = "加速上涨"
    elif mom_5 > 0 and acceleration < -1:
        score -= 15  # 顶部减速
        signal = "顶部减速"
    elif mom_5 < 0 and acceleration < -1:
        score -= 10  # 跌势加速
    elif mom_5 < 0 and acceleration > 1:
        score += 15  # 跌势放缓，可能见底
        signal = "跌势放缓"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "momentum_1": round(float(mom_1), 2),
            "momentum_3": round(float(mom_3), 2),
            "momentum_5": round(float(mom_5), 2),
            "acceleration": round(float(acceleration), 2), "signal": signal}


def calc_adx(theme_index: pd.DataFrame, period: int = 14) -> dict:
    """
    ADX趋势强度

    逻辑：
    - ADX > 25 → 趋势行情
    - ADX从20下方拐头向上 → 趋势启动
    - ADX > 40后回落 → 趋势衰竭

    Returns:
        {"score": 0-100, "adx": float, "trend_dir": str, "signal": str}
    """
    if theme_index.empty or len(theme_index) < period * 3:
        return {"score": 50, "adx": 0, "trend_dir": "无", "signal": "数据不足"}

    close = theme_index.set_index("trade_date")["theme_close"]
    high = close.rolling(2).max()  # 近似（等权指数无high/low，用close构造）
    low = close.rolling(2).min()

    # +DM / -DM
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    # TR
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)

    # 平滑
    atr = tr.rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=close.index).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=close.index).rolling(period).mean() / atr

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(period).mean()

    adx_current = adx.iloc[-1]
    if np.isnan(adx_current):
        return {"score": 50, "adx": 0, "trend_dir": "无", "signal": "数据不足"}

    adx_prev = adx.iloc[-3] if len(adx) >= 3 else adx.iloc[0]
    adx_rising = adx_current > adx_prev

    # 趋势方向
    if plus_di.iloc[-1] > minus_di.iloc[-1]:
        trend_dir = "多头"
    else:
        trend_dir = "空头"

    score = 50
    signal = "震荡"

    if adx_current > 25:
        score += 15
        if trend_dir == "多头":
            score += 15
            signal = "多头趋势"
        else:
            score -= 15
            signal = "空头趋势"

    if adx_current > 40 and not adx_rising:
        score -= 15  # 趋势衰竭
        signal = "趋势衰竭"
    elif adx_rising and adx_prev < 20 and adx_current > 20:
        score += 20  # 趋势启动
        signal = "趋势启动"
    elif adx_rising and adx_current > 25:
        score += 10  # 趋势增强

    score = max(0, min(100, score))
    return {"score": round(score, 1), "adx": round(float(adx_current), 2),
            "trend_dir": trend_dir, "signal": signal}


def calc_all_momentum(klines: dict, market_index: pd.DataFrame) -> dict:
    """计算动量层全部因子"""
    theme_index = calc_theme_index(klines)

    rs = calc_relative_strength(theme_index, market_index)
    mom = calc_momentum_acceleration(theme_index)
    adx = calc_adx(theme_index)

    return {
        "relative_strength": rs,
        "momentum_acceleration": mom,
        "adx_trend": adx,
        "theme_index_close": round(float(theme_index["theme_close"].iloc[-1]), 2) if not theme_index.empty else None,
    }
