#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V7 - 趋势动量与连贯性因子 (20%)

核心目标：识别真正的"主升浪"而非"横盘震荡/虚假启动"。

设计哲学：
  传统趋势分（如20日涨幅）容易被"脉冲式上涨"误导。
  真正的趋势 = 价格持续在均线上方运行 + 波动率配合 + 动量结构完整。

子维度：
  1. RSRS趋势强度 (30%) - 阻力支撑相对强度，辨识趋势质量
  2. 均线多头排列持续度 (30%) - MA5>MA10>MA20>MA60的有效天数
  3. 波动率挤压突破 (20%) - Bollinger Band Squeeze Breakout
  4. 趋势斜率一致性 (20%) - 日收益率的正收益连续性与斜率稳定性
"""

import numpy as np
import pandas as pd


def compute_trend_momentum(daily, codes, n_window=60):
    """趋势动量与连贯性综合评分 (0-100)

    参数:
        daily: DataFrame, 全市场日线
        codes: list, 主题成分股代码
        n_window: int, 分析窗口(交易日)

    返回:
        score: float 0-100
        sub_metrics: dict 子维度明细
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0, {}

    # 构建主题等权指数
    index_df = _build_theme_index(sub, codes)
    if index_df is None or len(index_df) < 20:
        return 50.0, {}

    close = index_df["close"].values
    dates = index_df["trade_date"].values
    high = index_df["high"].values if "high" in index_df.columns else close * 1.02
    low = index_df["low"].values if "low" in index_df.columns else close * 0.98

    # ============================================================
    # ① RSRS趋势强度 (30%)
    # ============================================================
    rsrs_score = _calc_rsrs(high, low, close)

    # ============================================================
    # ② 均线多头排列持续度 (30%)
    # ============================================================
    ma_score = _calc_ma_multi_line(close)

    # ============================================================
    # ③ 波动率挤压突破 (20%)
    # ============================================================
    squeeze_score = _calc_squeeze_breakout(high, low, close)

    # ============================================================
    # ④ 趋势斜率一致性 (20%)
    # ============================================================
    slope_score = _calc_slope_consistency(close)

    # ===== 加权合成 =====
    raw = (
        rsrs_score * 0.30 +
        ma_score * 0.30 +
        squeeze_score * 0.20 +
        slope_score * 0.20
    )

    score = float(np.clip(_amplify(raw / 100.0) * 100, 5, 98))

    sub_metrics = {
        "rsrs_strength": round(rsrs_score, 1),
        "ma_multi_line": round(ma_score, 1),
        "squeeze_breakout": round(squeeze_score, 1),
        "slope_consistency": round(slope_score, 1),
    }

    return score, sub_metrics


def _build_theme_index(sub, codes):
    """构建主题等权指数

    取所有成分股每日收盘价归一化等权平均。
    """
    sub = sub.copy()
    if sub.empty:
        return None

    # 获取每个股票的最后n_window个交易日
    result = []
    for code in codes:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 20:
            continue
        result.append(stock)

    if not result:
        return None

    # 合并所有股票
    all_data = pd.concat(result)

    # 按日期pivot
    close_pivot = all_data.pivot_table(
        index="trade_date", columns="ts_code", values="close", aggfunc="first"
    )

    # 归一化到100
    norm = close_pivot / close_pivot.iloc[0] * 100

    # 等权指数
    index_close = norm.mean(axis=1)
    index_df = index_close.reset_index()
    index_df.columns = ["trade_date", "close"]
    index_df = index_df.sort_values("trade_date")

    # 如果有high/low数据也构建
    if "high" in all_data.columns:
        high_pivot = all_data.pivot_table(
            index="trade_date", columns="ts_code", values="high", aggfunc="first"
        )
        high_norm = high_pivot / close_pivot.iloc[0] * 100
        index_df["high"] = high_norm.mean(axis=1).values

    if "low" in all_data.columns:
        low_pivot = all_data.pivot_table(
            index="trade_date", columns="ts_code", values="low", aggfunc="first"
        )
        low_norm = low_pivot / close_pivot.iloc[0] * 100
        index_df["low"] = low_norm.mean(axis=1).values

    return index_df


def _calc_rsrs(high, low, close):
    """RSRS (阻力支撑相对强度) 趋势质量分

    核心逻辑：
      对每日 (high - low) 和 (close - low) 做岭回归，
      斜率 beta 的标准化值 = RSRS 指标。
      beta > 0 且大 = 支撑强于阻力 = 上涨趋势质量高
      beta < 0 = 阻力强于支撑 = 下跌趋势

    简化版实现：
      滚动20日，计算 (close - low) 对 (high - low) 的回归斜率，
      衡量支撑强度。
      斜率 > 0 且持续上升 = 优质趋势

    返回: 0-100
    """
    if len(close) < 30:
        return 50.0

    n = min(20, len(close) - 1)

    # 最近n日的RSRS
    recent_high = high[-n:]
    recent_low = low[-n:]
    recent_close = close[-n:]

    range_arr = recent_high - recent_low
    body_arr = recent_close - recent_low

    # 过滤掉range为0的情况
    valid = range_arr > 1e-9
    if valid.sum() < 5:
        return 50.0

    range_arr = range_arr[valid]
    body_arr = body_arr[valid]

    # 岭回归（简化：用普通最小二乘）
    X = range_arr.reshape(-1, 1)
    y = body_arr

    # 直接用numpy计算回归斜率
    X_mean = X.mean()
    y_mean = y.mean()
    beta = np.sum((X.flatten() - X_mean) * (y - y_mean)) / np.sum((X.flatten() - X_mean) ** 2)

    # 标准化beta到0-100
    # beta = 0.5 表示收盘价在 HL 中间 = 50分
    # beta = 0.8 表示收盘价靠近H = 支撑强 = 80分
    # beta = 0.2 表示收盘价靠近L = 支撑弱 = 20分
    raw_score = 50 + (beta - 0.5) * 100

    # 近5日斜率的趋势（RSRS本身在改善还是恶化）
    if len(close) >= n + 5:
        # 更早的5日
        earlier_high = high[-(n + 5):-5]
        earlier_low = low[-(n + 5):-5]
        earlier_close = close[-(n + 5):-5]

        erange = earlier_high - earlier_low
        ebody = earlier_close - earlier_low
        evalid = erange > 1e-9
        if evalid.sum() >= 5:
            erange = erange[evalid]
            ebody = ebody[evalid]
            eX_mean = erange.mean()
            ebody_mean = ebody.mean()
            ebeta = np.sum((erange - eX_mean) * (ebody - ebody_mean)) / np.sum((erange - eX_mean) ** 2)
            # RSRS改善加分
            if beta > ebeta:
                raw_score += 5
            else:
                raw_score -= 5

    return float(np.clip(raw_score, 5, 95))


def _calc_ma_multi_line(close):
    """均线多头排列持续度

    核心逻辑：
      检查 MA5 > MA10 > MA20 > MA60 的天数占比。
      持续的时间越长，趋势越真。

    返回: 0-100
    """
    if len(close) < 60:
        return 50.0

    close_series = pd.Series(close)

    # 计算各周期均线
    ma5 = close_series.rolling(5, min_periods=5).mean()
    ma10 = close_series.rolling(10, min_periods=10).mean()
    ma20 = close_series.rolling(20, min_periods=20).mean()
    ma60 = close_series.rolling(60, min_periods=60).mean()

    # 检查多头排列 (MA5 > MA10 > MA20 > MA60)
    multi_line = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)

    # 最近20天中多头排列的天数占比
    recent = multi_line.iloc[-20:] if len(multi_line) >= 20 else multi_line
    ratio = recent.mean() if len(recent) > 0 else 0

    # 最近5天是否全部多头
    last5_all = multi_line.iloc[-5:].all() if len(multi_line) >= 5 else False

    # 评分
    if last5_all and ratio > 0.8:
        score = 85  # 完美多头，且持续
    elif ratio > 0.7:
        score = 70
    elif ratio > 0.5:
        score = 55
    elif ratio > 0.3:
        score = 40
    elif ratio > 0.1:
        score = 30
    else:
        # 检查是否空头排列
        bear_line = (ma5 < ma10) & (ma10 < ma20) & (ma20 < ma60)
        bear_ratio = bear_line.iloc[-20:].mean() if len(bear_line) >= 20 else 0
        if bear_ratio > 0.5:
            score = 15  # 空头排列
        else:
            score = 40  # 震荡

    return float(score)


def _calc_squeeze_breakout(high, low, close):
    """波动率挤压突破 (Squeeze Breakout)

    核心逻辑：
      当 Bollinger Band 宽度收缩到极低水平后，
      价格向上突破 = 爆发力极强的启动信号。
      这种模式捕捉的是"横有多长竖有多高"的突破。

    实现：
      1. 计算 Bollinger Band 宽度 = (上轨 - 下轨) / 中轨
      2. 宽度在近半年的百分位 < 20% = 挤压状态
      3. 挤压状态中价格突破上轨 = 突破信号

    返回: 0-100
    """
    if len(close) < 20:
        return 50.0

    close_series = pd.Series(close)

    # Bollinger Band (20,2)
    bb_mid = close_series.rolling(20, min_periods=20).mean()
    bb_std = close_series.rolling(20, min_periods=20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Band宽度
    bb_width = (bb_upper - bb_lower) / bb_mid

    # 当前宽度在近100天的百分位
    recent_width = bb_width.iloc[-100:] if len(bb_width) >= 100 else bb_width
    if len(recent_width) < 20:
        return 50.0

    current_width = bb_width.iloc[-1]
    width_percentile = (recent_width < current_width).mean()

    # 检查是否挤压中 (宽度 < 20%分位)
    is_squeeze = width_percentile < 0.20

    # 检查是否突破上轨
    if len(close) >= 2:
        last_close = close[-1]
        last_upper = bb_upper.iloc[-1]
        is_breakout = last_close > last_upper
    else:
        is_breakout = False

    # 检查是否突破下轨（下跌信号）
    if len(close) >= 2:
        last_lower = bb_lower.iloc[-1]
        is_breakdown = last_close < last_lower
    else:
        is_breakdown = False

    # 评分
    if is_squeeze and is_breakout:
        score = 90  # 挤压后向上突破 = 最强信号
    elif is_breakout:
        score = 70  # 直接突破（非挤压状态）
    elif is_squeeze and not is_breakout and not is_breakdown:
        score = 50  # 挤压中，蓄势待发
    elif is_squeeze and is_breakdown:
        score = 15  # 挤压后向下突破 = 危险信号
    elif is_breakdown:
        score = 25  # 向下突破
    else:
        # 正常趋势中
        # 检查近5日价格在BB中的位置
        pos = (close[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]) if (bb_upper.iloc[-1] - bb_lower.iloc[-1]) > 0 else 0.5
        if pos > 0.8:
            score = 60  # 靠近上轨
        elif pos > 0.5:
            score = 50
        else:
            score = 40

    return float(score)


def _calc_slope_consistency(close):
    """趋势斜率一致性

    核心逻辑：
      真正的趋势不是一根大阳线，而是持续的小阳线积累。
      衡量：
        - 近10日正收益占比
        - 连续上涨天数
        - 日收益率的标准差（波动率适中）
        - 价格稳定在均线上方

    返回: 0-100
    """
    if len(close) < 15:
        return 50.0

    close_series = pd.Series(close)

    # 日收益率
    ret = close_series.pct_change().dropna()
    recent_ret = ret.iloc[-10:] if len(ret) >= 10 else ret

    if len(recent_ret) < 5:
        return 50.0

    # 正收益占比
    pos_ratio = (recent_ret > 0).mean()

    # 连续上涨天数
    consecutive_up = 0
    for r in reversed(recent_ret.values):
        if r > 0:
            consecutive_up += 1
        else:
            break

    # 累计涨幅
    cum_ret = (1 + recent_ret).prod() - 1

    # 波动率
    vol = recent_ret.std()

    # 价格在20日线上方
    ma20 = close_series.rolling(20, min_periods=20).mean()
    above_ma20 = (close[-1] > ma20.iloc[-1]) if len(ma20) > 0 else False

    # 评分
    score = 50

    # 正收益占比
    if pos_ratio > 0.8:
        score += 15
    elif pos_ratio > 0.6:
        score += 8
    elif pos_ratio < 0.3:
        score -= 10

    # 连续上涨
    if consecutive_up >= 5:
        score += 15
    elif consecutive_up >= 3:
        score += 8
    elif consecutive_up >= 2:
        score += 3

    # 累计涨幅
    if cum_ret > 0.08:
        score += 8
    elif cum_ret > 0.03:
        score += 4
    elif cum_ret < -0.03:
        score -= 5
    elif cum_ret < -0.08:
        score -= 10

    # 波动率惩罚（波动太大 = 不连贯）
    if vol > 0.04:
        score -= 5  # 日波动 > 4% = 不稳定
    elif vol > 0.03:
        score -= 2

    # 均线位置
    if above_ma20:
        score += 5
    else:
        score -= 8

    return float(np.clip(score, 5, 95))


def _amplify(pct):
    """非线性放大"""
    return np.clip(np.power(np.clip(pct, 0, 1), 0.75), 0, 1)