# -*- coding: utf-8 -*-
"""
时序因子层

3个时序因子，捕捉横截面因子无法捕捉的动态变化：
1. rs_slope - RS趋势斜率（5日），捕捉抱团松动/轮动启动
2. concentration_change - 资金集中度变化，捕捉资金分散/聚焦
3. leader_lag - 领先滞后转换，捕捉抱团末段龙头滞涨/成份股补涨

核心价值：横截面因子看"当前状态"，时序因子看"变化方向"
"""
import numpy as np
import pandas as pd
from collections import deque


def calc_rs_slope(theme_index: pd.DataFrame, market_index: pd.DataFrame,
                   short_window: int = 5, long_window: int = 20) -> dict:
    """
    RS趋势斜率

    逻辑：
    - RS斜率持续为正 → 抱团延续/强势主题
    - RS斜率由正转负 → 抱团松动预警（最早信号，比价格早3-5天）
    - RS斜率由负转正 → 轮动启动/反转启动

    Returns:
        {"score": 0-100, "rs_slope_5": float, "rs_slope_20": float, "signal": str}
    """
    if theme_index.empty or market_index.empty:
        return {"score": 50, "rs_slope_5": 0, "rs_slope_20": 0, "signal": "数据不足"}

    t = theme_index.set_index("trade_date")["theme_close"]
    m = market_index.set_index("trade_date")
    close_col = "close" if "close" in m.columns else m.columns[0]
    m_close = m[close_col]
    m_close = m_close / m_close.iloc[0] * 100

    aligned = pd.DataFrame({"theme": t, "market": m_close}).dropna()
    if len(aligned) < long_window + 5:
        return {"score": 50, "rs_slope_5": 0, "rs_slope_20": 0, "signal": "数据不足"}

    rs = aligned["theme"] / aligned["market"]

    # 短期斜率（5日）
    rs_recent_5 = rs.iloc[-5:]
    rs_slope_5 = float(np.polyfit(range(5), rs_recent_5.values, 1)[0] / rs.iloc[-1] * 100)

    # 长期斜率（20日）
    rs_recent_20 = rs.iloc[-20:]
    rs_slope_20 = float(np.polyfit(range(20), rs_recent_20.values, 1)[0] / rs.iloc[-1] * 100)

    # 前一天的短期斜率（用于判断转折）
    if len(rs) >= 6:
        rs_prev_5 = rs.iloc[-6:-1]
        rs_slope_5_prev = float(np.polyfit(range(5), rs_prev_5.values, 1)[0] / rs.iloc[-2] * 100)
    else:
        rs_slope_5_prev = 0

    # 评分逻辑
    score = 50
    signal = "中性"

    # 短期斜率为正且长期斜率为正 → 强势延续
    if rs_slope_5 > 0.5 and rs_slope_20 > 0:
        score = 75
        signal = "RS双正·强势延续"

    # 短期斜率由正转负 → 抱团松动预警
    elif rs_slope_5 < -0.3 and rs_slope_5_prev > 0:
        score = 25
        signal = "RS转负·抱团松动"

    # 短期斜率为负 → 趋势走弱
    elif rs_slope_5 < -0.5:
        score = 30
        signal = "RS走弱"

    # 短期斜率由负转正 → 反转启动
    elif rs_slope_5 > 0.3 and rs_slope_5_prev < 0:
        score = 70
        signal = "RS转正·反转启动"

    # 短期斜率小幅波动
    elif abs(rs_slope_5) <= 0.3:
        if rs_slope_20 > 0:
            score = 60
            signal = "RS横盘·中期偏强"
        else:
            score = 40
            signal = "RS横盘·中期偏弱"

    return {
        "score": score,
        "rs_slope_5": round(rs_slope_5, 3),
        "rs_slope_20": round(rs_slope_20, 3),
        "rs_slope_5_prev": round(rs_slope_5_prev, 3),
        "signal": signal,
    }


def calc_concentration_change(all_theme_klines: dict, market_index: pd.DataFrame,
                                short_window: int = 5, lookback: int = 20) -> dict:
    """
    资金集中度变化

    逻辑：
    - Top5主题成交额占比上升 → 资金聚焦，抱团加剧
    - Top5占比从峰值回落 → 资金分散，抱团松动
    - Bottom5占比上升 → 资金扩散，补涨启动

    Returns:
        {"score": 0-100, "top5_share": float, "share_change": float, "signal": str}
    """
    if not all_theme_klines or market_index is None:
        return {"score": 50, "top5_share": 0, "share_change": 0, "signal": "数据不足"}

    # 计算每个主题的近期成交额
    theme_amounts = {}
    for theme_name, klines in all_theme_klines.items():
        total_amount = 0
        count = 0
        for code, df in klines.items():
            if "amount" in df.columns and len(df) >= short_window:
                # 近5日成交额
                total_amount += df["amount"].iloc[-short_window:].sum()
                count += 1
        if count > 0:
            theme_amounts[theme_name] = total_amount

    if len(theme_amounts) < 5:
        return {"score": 50, "top5_share": 0, "share_change": 0, "signal": "数据不足"}

    # 总成交额
    total_all = sum(theme_amounts.values())

    # Top5占比
    sorted_themes = sorted(theme_amounts.items(), key=lambda x: -x[1])
    top5_amount = sum(t[1] for t in sorted_themes[:5])
    top5_share = top5_amount / total_all if total_all > 0 else 0

    # 计算前一段时间的Top5占比（用于对比）
    # 简化处理：用前short_window天的数据
    prev_theme_amounts = {}
    for theme_name, klines in all_theme_klines.items():
        total_amount = 0
        count = 0
        for code, df in klines.items():
            if "amount" in df.columns and len(df) >= short_window * 2:
                # 前5日成交额
                total_amount += df["amount"].iloc[-short_window*2:-short_window].sum()
                count += 1
        if count > 0:
            prev_theme_amounts[theme_name] = total_amount

    if len(prev_theme_amounts) >= 5:
        prev_total = sum(prev_theme_amounts.values())
        prev_sorted = sorted(prev_theme_amounts.items(), key=lambda x: -x[1])
        prev_top5 = sum(t[1] for t in prev_sorted[:5])
        prev_top5_share = prev_top5 / prev_total if prev_total > 0 else 0
        share_change = top5_share - prev_top5_share
    else:
        share_change = 0

    # 评分逻辑
    score = 50
    signal = "中性"

    if share_change > 0.02:  # 占比上升>2%
        score = 70
        signal = "资金聚焦·抱团加剧"
    elif share_change > 0.005:
        score = 60
        signal = "资金小幅聚焦"
    elif share_change < -0.02:  # 占比下降>2%
        score = 30
        signal = "资金分散·抱团松动"
    elif share_change < -0.005:
        score = 40
        signal = "资金小幅分散"
    else:
        if top5_share > 0.4:  # 高集中度但变化不大
            score = 65
            signal = "高集中度·抱团维持"
        else:
            score = 50
            signal = "资金分布稳定"

    return {
        "score": score,
        "top5_share": round(top5_share, 4),
        "share_change": round(share_change, 4),
        "signal": signal,
    }


def calc_leader_lag(theme_klines: dict, short_window: int = 5) -> dict:
    """
    领先滞后转换

    逻辑：
    - 龙头涨+成份股跟涨 → 抱团中段（正常）
    - 龙头滞涨+成份股补涨 → 抱团末段（风格切换前兆）
    - 龙头跌+成份股跟跌 → 抱团瓦解

    通过成份股涨幅的离散度判断：
    - 头部成份股涨幅 - 尾部成份股涨幅 = 分化度
    - 分化度收敛（变小）→ 补涨启动，抱团末段

    Returns:
        {"score": 0-100, "leader_ret": float, "lagger_ret": float, "signal": str}
    """
    if not theme_klines or len(theme_klines) < 5:
        return {"score": 50, "leader_ret": 0, "lagger_ret": 0, "signal": "数据不足"}

    # 计算每只股票近5日涨幅
    rets = {}
    for code, df in theme_klines.items():
        if "close" in df.columns and len(df) >= short_window + 1:
            ret = (df["close"].iloc[-1] / df["close"].iloc[-short_window-1] - 1) * 100
            rets[code] = ret

    if len(rets) < 5:
        return {"score": 50, "leader_ret": 0, "lagger_ret": 0, "signal": "数据不足"}

    sorted_rets = sorted(rets.items(), key=lambda x: -x[1])
    n = len(sorted_rets)
    top_n = max(3, n // 5)  # 前20%
    bottom_n = max(3, n // 5)  # 后20%

    leader_ret = float(np.mean([r[1] for r in sorted_rets[:top_n]]))
    lagger_ret = float(np.mean([r[1] for r in sorted_rets[-bottom_n:]]))
    avg_ret = float(np.mean(list(rets.values())))

    # 分化度
    divergence = leader_ret - lagger_ret

    # 计算前一段时间的分化度（用于判断收敛趋势）
    prev_rets = {}
    for code, df in theme_klines.items():
        if "close" in df.columns and len(df) >= short_window * 2 + 1:
            ret = (df["close"].iloc[-short_window-1] / df["close"].iloc[-short_window*2-1] - 1) * 100
            prev_rets[code] = ret

    prev_divergence = 0
    if len(prev_rets) >= 5:
        prev_sorted = sorted(prev_rets.items(), key=lambda x: -x[1])
        prev_leader = float(np.mean([r[1] for r in prev_sorted[:top_n]]))
        prev_lagger = float(np.mean([r[1] for r in prev_sorted[-bottom_n:]]))
        prev_divergence = prev_leader - prev_lagger

    divergence_change = divergence - prev_divergence

    # 评分逻辑
    score = 50
    signal = "中性"

    if avg_ret > 0:  # 主题在涨
        if divergence_change < -1:  # 分化收敛
            score = 70
            signal = "补涨启动·抱团末段"
        elif divergence > 5 and divergence_change > 0:
            score = 65
            signal = "龙头领涨·抱团中段"
        elif divergence < 2:
            score = 60
            signal = "全面上涨"
        else:
            score = 55
            signal = "温和分化"
    else:  # 主题在跌
        if divergence_change > 1:  # 分化扩大（龙头抗跌，成份股补跌）
            score = 30
            signal = "龙头抗跌·成份补跌"
        elif divergence < -3:  # 龙头领跌
            score = 25
            signal = "龙头领跌·抱团瓦解"
        else:
            score = 35
            signal = "齐跌"

    return {
        "score": score,
        "leader_ret": round(leader_ret, 2),
        "lagger_ret": round(lagger_ret, 2),
        "avg_ret": round(avg_ret, 2),
        "divergence": round(divergence, 2),
        "divergence_change": round(divergence_change, 2),
        "signal": signal,
    }


def calc_all_timeseries_factors(theme_klines: dict, theme_index: pd.DataFrame,
                                  market_index: pd.DataFrame,
                                  all_theme_klines: dict = None) -> dict:
    """
    计算时序因子层全部因子

    Args:
        theme_klines: 单主题的成份股K线
        theme_index: 主题等权指数
        market_index: 大盘指数
        all_theme_klines: 全市场所有主题的成份股K线（用于资金集中度）
    """
    rs_slope = calc_rs_slope(theme_index, market_index)

    # 资金集中度需要全市场数据，如果没有则降级
    if all_theme_klines:
        conc = calc_concentration_change(all_theme_klines, market_index)
    else:
        conc = {"score": 50, "top5_share": 0, "share_change": 0, "signal": "无全市场数据"}

    leader_lag = calc_leader_lag(theme_klines)

    return {
        "rs_slope": rs_slope,
        "concentration_change": conc,
        "leader_lag": leader_lag,
    }
