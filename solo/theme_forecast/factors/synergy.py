# -*- coding: utf-8 -*-
"""
协同度层因子

4. synergy_coefficient - 协同度（成份股两两相关系数均值）
5. leadership_divergence - 领涨滞涨分化度
6. breakout_ratio - 突破比例（站上MA20占比）
"""
import pandas as pd
import numpy as np


def calc_synergy_coefficient(klines: dict, window: int = 20) -> dict:
    """
    协同度系数

    逻辑：
    - 主题内所有成份股日收益率两两相关系数的均值
    - 协同度从0.3升至0.7 → 板块效应启动
    - 协同度>0.85 → 过度一致，临近分化见顶

    Returns:
        {"score": 0-100, "synergy": float, "synergy_change": float, "signal": str}
    """
    if len(klines) < 5:
        return {"score": 50, "synergy": 0, "synergy_change": 0, "signal": "数据不足"}

    # 收集日收益率
    ret_dict = {}
    for code, df in klines.items():
        if "pct_chg" in df.columns:
            s = df.set_index("trade_date")["pct_chg"]
            ret_dict[code] = s
        elif "close" in df.columns:
            s = df.set_index("trade_date")["close"].pct_change() * 100
            ret_dict[code] = s

    if len(ret_dict) < 5:
        return {"score": 50, "synergy": 0, "synergy_change": 0, "signal": "数据不足"}

    ret_df = pd.DataFrame(ret_dict).sort_index()
    # 取最近window日
    ret_recent = ret_df.tail(window)
    ret_prev = ret_df.iloc[-2*window:-window] if len(ret_df) >= 2*window else ret_df.head(window)

    # 计算相关系数矩阵
    corr_recent = ret_recent.corr()
    # 取上三角（不含对角线）的均值
    mask = np.triu(np.ones_like(corr_recent, dtype=bool), k=1)
    corr_values = corr_recent.values[mask]
    corr_values = corr_values[~np.isnan(corr_values)]

    if len(corr_values) == 0:
        return {"score": 50, "synergy": 0, "synergy_change": 0, "signal": "数据不足"}

    synergy_current = float(np.mean(corr_values))

    # 前一期协同度
    synergy_prev = synergy_current
    if not ret_prev.empty and len(ret_prev) >= 10:
        corr_prev = ret_prev.corr()
        corr_prev_values = corr_prev.values[mask[:corr_prev.shape[0], :corr_prev.shape[1]]] if corr_prev.shape == corr_recent.shape else None
        if corr_prev_values is not None and len(corr_prev_values) > 0:
            corr_prev_values = corr_prev_values[~np.isnan(corr_prev_values)]
            if len(corr_prev_values) > 0:
                synergy_prev = float(np.mean(corr_prev_values))

    synergy_change = synergy_current - synergy_prev

    # 关键修复：判断当日平均涨跌方向
    # 高协同 + 一起涨 = 看涨；高协同 + 一起跌 = 齐跌·看跌
    avg_ret_today = 0
    if not ret_recent.empty:
        avg_ret_today = float(ret_recent.iloc[-1].mean())

    score = 50
    signal = "中性"

    if synergy_current < 0.3:
        score -= 10
        signal = "无板块效应"
    elif synergy_current < 0.5:
        score += 5
        signal = "弱协同"
    elif synergy_current < 0.7:
        # 区分齐涨还是齐跌
        if avg_ret_today > 0.5:
            score += 25
            if synergy_change > 0.1:
                score += 10
                signal = "板块效应启动"
            else:
                signal = "协同走强"
        elif avg_ret_today < -0.5:
            score -= 20  # 齐跌！
            signal = "齐跌·看跌"
        else:
            score += 10
            signal = "弱协同"
    elif synergy_current < 0.85:
        if avg_ret_today > 0.5:
            score += 15
            signal = "高度协同"
        elif avg_ret_today < -0.5:
            score -= 25  # 高协同齐跌更危险
            signal = "高度齐跌·看跌"
        else:
            score += 5
            signal = "高度协同"
    else:
        if avg_ret_today > 0.5:
            score -= 15  # 过度一致，临近分化
            signal = "过度一致·见顶警示"
        elif avg_ret_today < -0.5:
            score -= 30  # 极度齐跌
            signal = "极度齐跌·恐慌"
        else:
            score -= 10
            signal = "过度一致"

    # 协同度变化趋势
    if synergy_change > 0.15 and avg_ret_today > 0:
        score += 10
    elif synergy_change < -0.15:
        score -= 10

    score = max(0, min(100, score))
    return {"score": round(score, 1), "synergy": round(synergy_current, 4),
            "synergy_change": round(synergy_change, 4), "signal": signal}


def calc_leadership_divergence(klines: dict) -> dict:
    """
    领涨-滞涨分化度

    逻辑：
    - 分化度 = (头部5只20日涨幅 - 尾部5只20日涨幅) / 平均涨幅
    - 分化度持续扩大 → 资金聚焦龙头，行情中后段
    - 分化度收敛 → 补涨扩散，行情末段

    Returns:
        {"score": 0-100, "divergence": float, "leader_ret": float, "laggard_ret": float, "signal": str}
    """
    if len(klines) < 10:
        return {"score": 50, "divergence": 0, "leader_ret": 0, "laggard_ret": 0, "signal": "数据不足"}

    # 计算每只股票20日涨幅
    rets = {}
    for code, df in klines.items():
        if len(df) >= 20 and "close" in df.columns:
            ret_20 = (df["close"].iloc[-1] / df["close"].iloc[-20] - 1) * 100
            rets[code] = ret_20

    if len(rets) < 10:
        return {"score": 50, "divergence": 0, "leader_ret": 0, "laggard_ret": 0, "signal": "数据不足"}

    sorted_rets = sorted(rets.values(), reverse=True)
    n = len(sorted_rets)
    top5 = np.mean(sorted_rets[:5])
    bottom5 = np.mean(sorted_rets[-5:])
    avg_ret = np.mean(sorted_rets)

    divergence = (top5 - bottom5) / abs(avg_ret) if abs(avg_ret) > 0.1 else top5 - bottom5

    score = 50
    signal = "中性"

    if avg_ret > 0:  # 主题整体上涨
        if divergence > 3:
            score += 15
            signal = "资金聚焦龙头"
        elif divergence > 1.5:
            score += 10
            signal = "龙头领涨"
        elif divergence < 0.5 and top5 > 5:
            score -= 10  # 分化收敛 + 已涨 → 补涨末段
            signal = "补涨扩散·末段警示"
        elif divergence < 0.5:
            score += 5
            signal = "普涨格局"
    else:  # 主题整体下跌
        if divergence > 2:
            score -= 15  # 龙头补跌
            signal = "龙头补跌"
        elif divergence < 0.5:
            score -= 10  # 普跌
            signal = "普跌格局"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "divergence": round(float(divergence), 2),
            "leader_ret": round(float(top5), 2), "laggard_ret": round(float(bottom5), 2),
            "signal": signal}


def calc_breakout_ratio(klines: dict) -> dict:
    """
    突破比例

    逻辑：
    - 主题内站上20日均线 / 创20日新高的股票占比
    - 突破比例>60% → 板块整体启动
    - 突破比例>80%后回落 → 顶部信号

    Returns:
        {"score": 0-100, "above_ma20_ratio": float, "new_high_ratio": float, "signal": str}
    """
    if len(klines) < 10:
        return {"score": 50, "above_ma20_ratio": 0, "new_high_ratio": 0, "signal": "数据不足"}

    above_ma20 = 0
    new_high_20 = 0
    total = 0

    for code, df in klines.items():
        if len(df) < 20 or "close" not in df.columns:
            continue
        total += 1
        close = df["close"]
        ma20 = close.rolling(20).mean()
        if close.iloc[-1] > ma20.iloc[-1]:
            above_ma20 += 1
        # 创20日新高
        if close.iloc[-1] >= close.tail(20).max():
            new_high_20 += 1

    if total == 0:
        return {"score": 50, "above_ma20_ratio": 0, "new_high_ratio": 0, "signal": "数据不足"}

    above_ratio = above_ma20 / total
    high_ratio = new_high_20 / total

    score = 50
    signal = "中性"

    if above_ratio > 0.8:
        score += 25
        signal = "板块整体启动"
        if high_ratio > 0.5:
            score += 10
        # 突破比例>80%但近3日回落
        if len(klines) > 0:
            sample_df = list(klines.values())[0]
            if len(sample_df) >= 3:
                close_3ago = sample_df["close"].iloc[-3]
                close_now = sample_df["close"].iloc[-1]
                if close_now < close_3ago:
                    score -= 15
                    signal = "高位回落·顶部信号"
    elif above_ratio > 0.6:
        score += 15
        signal = "多数走强"
    elif above_ratio < 0.3:
        score -= 20
        signal = "多数走弱"
    elif above_ratio < 0.5:
        score -= 10
        signal = "偏弱"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "above_ma20_ratio": round(float(above_ratio), 4),
            "new_high_ratio": round(float(high_ratio), 4), "signal": signal}


def calc_all_synergy(klines: dict) -> dict:
    """计算协同度层全部因子"""
    return {
        "synergy_coefficient": calc_synergy_coefficient(klines),
        "leadership_divergence": calc_leadership_divergence(klines),
        "breakout_ratio": calc_breakout_ratio(klines),
    }
