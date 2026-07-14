# -*- coding: utf-8 -*-
"""
情绪层因子

7. limit_up_ladder - 涨停梯队完整度
8. turnover_distribution - 换手率分布异常
"""
import pandas as pd
import numpy as np


def calc_limit_up_ladder(theme_codes: list, limit_list: pd.DataFrame, limit_step: pd.DataFrame,
                         daily_basic: pd.DataFrame, trade_date: str) -> dict:
    """
    涨停梯队完整度

    逻辑：
    - 梯队完整（首板+2板+3板齐全）→ 情绪主升，次日涨跌比3:1
    - 连板高度断档（只有首板无接力）→ 情绪退潮
    - 炸板率>40% → 见顶预警

    Returns:
        {"score": 0-100, "limit_up_count": int, "max_consecutive": int,
         "ladder_complete": bool, "bomb_rate": float, "signal": str}
    """
    code_set = set(theme_codes)

    if limit_list is None or limit_list.empty:
        return {"score": 50, "limit_up_count": 0, "max_consecutive": 0,
                "ladder_complete": False, "bomb_rate": 0, "signal": "无涨停数据"}

    # 筛选主题内涨停股
    if "ts_code" in limit_list.columns:
        theme_limits = limit_list[limit_list["ts_code"].isin(code_set)]
    else:
        theme_limits = pd.DataFrame()

    limit_count = len(theme_limits)

    # 连板信息（limit_list_d 通常含 limit_times 字段表示连板数）
    max_consecutive = 1
    ladder_levels = set()
    if not theme_limits.empty:
        # 尝试读取连板次数列
        for col in ["limit_times", "consecutive_times", "l_times"]:
            if col in theme_limits.columns:
                consec = theme_limits[col].dropna()
                if not consec.empty:
                    max_consecutive = int(consec.max())
                    ladder_levels = set(consec.unique())
                    break

    # 炸板率
    bomb_count = 0
    if limit_step is not None and not limit_step.empty:
        if "ts_code" in limit_step.columns:
            theme_bombs = limit_step[limit_step["ts_code"].isin(code_set)]
            bomb_count = len(theme_bombs)

    total_attempts = limit_count + bomb_count
    bomb_rate = bomb_count / total_attempts if total_attempts > 0 else 0

    # 梯队完整度判断
    ladder_complete = False
    if max_consecutive >= 3 and len(ladder_levels) >= 3:
        ladder_complete = True

    score = 50
    signal = "中性"

    # 涨停数量
    if limit_count >= 5:
        score += 20
        signal = "情绪主升"
    elif limit_count >= 3:
        score += 12
        signal = "情绪活跃"
    elif limit_count >= 1:
        score += 5
        signal = "有情绪"
    else:
        score -= 5
        signal = "情绪冷淡"

    # 连板高度
    if max_consecutive >= 4:
        score += 15
        signal = "高度连板"
    elif max_consecutive >= 3:
        score += 10
    elif max_consecutive >= 2:
        score += 5

    # 梯队完整度
    if ladder_complete:
        score += 10
    elif max_consecutive >= 2 and limit_count >= 3:
        # 有连板但梯队不完整
        pass
    elif limit_count >= 3 and max_consecutive == 1:
        score -= 10  # 只有首板无接力 → 退潮
        signal = "梯队断档·退潮预警"

    # 炸板率
    if bomb_rate > 0.4:
        score -= 20
        signal = "炸板率高·见顶预警"
    elif bomb_rate > 0.25:
        score -= 10
    elif bomb_rate < 0.1 and limit_count >= 2:
        score += 5  # 封板坚决

    score = max(0, min(100, score))
    return {"score": round(score, 1), "limit_up_count": limit_count,
            "max_consecutive": max_consecutive, "ladder_complete": ladder_complete,
            "bomb_rate": round(float(bomb_rate), 4), "signal": signal}


def calc_turnover_distribution(daily_basic: pd.DataFrame, theme_codes: list,
                               klines: dict = None) -> dict:
    """
    换手率分布异常检测

    逻辑：
    - 换手率均值升高 + 标准差收敛 → 全面活跃，看涨
    - 换手率均值升高 + 标准差发散（少数暴涨多数不动）→ 分歧加大，顶部信号

    Returns:
        {"score": 0-100, "avg_turnover": float, "std_turnover": float,
         "skewness": float, "signal": str}
    """
    if daily_basic is None or daily_basic.empty:
        return {"score": 50, "avg_turnover": 0, "std_turnover": 0, "skewness": 0, "signal": "数据不足"}

    code_set = set(theme_codes)

    # 筛选主题内股票
    if "ts_code" in daily_basic.columns:
        theme_basic = daily_basic[daily_basic["ts_code"].isin(code_set)].copy()
    else:
        return {"score": 50, "avg_turnover": 0, "std_turnover": 0, "skewness": 0, "signal": "数据不足"}

    if theme_basic.empty:
        return {"score": 50, "avg_turnover": 0, "std_turnover": 0, "skewness": 0, "signal": "数据不足"}

    # 换手率列
    turnover_col = None
    for col in ["turnover_rate", "turnover", "tv_rate"]:
        if col in theme_basic.columns:
            turnover_col = col
            break

    if turnover_col is None:
        return {"score": 50, "avg_turnover": 0, "std_turnover": 0, "skewness": 0, "signal": "无换手率字段"}

    turnovers = theme_basic[turnover_col].dropna()
    if len(turnovers) < 5:
        return {"score": 50, "avg_turnover": 0, "std_turnover": 0, "skewness": 0, "signal": "数据不足"}

    avg_to = float(turnovers.mean())
    std_to = float(turnovers.std())
    # 变异系数
    cv = std_to / avg_to if avg_to > 0 else 0
    # 偏度
    skew = float(turnovers.skew()) if len(turnovers) >= 10 else 0

    # 历史对比（如果有K线数据中的换手率）
    hist_avg = avg_to
    if klines:
        hist_turnovers = []
        for code, df in klines.items():
            for col in ["turnover_rate", "turnover"]:
                if col in df.columns and len(df) >= 20:
                    hist_turnovers.extend(df[col].iloc[-20:-1].dropna().tolist())
                    break
        if hist_turnovers:
            hist_avg = np.mean(hist_turnovers)

    turnover_change = avg_to / hist_avg if hist_avg > 0 else 1

    # 关键修复：获取当日平均涨跌，区分放量上涨 vs 放量下跌
    avg_pct_today = 0
    if klines:
        pcts = []
        for code, df in klines.items():
            if "pct_chg" in df.columns and len(df) >= 1:
                pcts.append(df["pct_chg"].iloc[-1])
            elif "close" in df.columns and len(df) >= 2:
                pcts.append((df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100)
        if pcts:
            avg_pct_today = float(np.mean(pcts))

    score = 50
    signal = "中性"

    # 换手率水平
    if avg_to > 8:
        if avg_pct_today > 0.5:
            score += 15
            signal = "高度活跃"
        elif avg_pct_today < -0.5:
            score -= 20  # 放量大跌 = 恐慌抛售
            signal = "放量下跌·恐慌"
        else:
            score += 5
    elif avg_to > 5:
        if avg_pct_today > 0.5:
            score += 10
            signal = "活跃"
        elif avg_pct_today < -0.5:
            score -= 15  # 放量下跌
            signal = "放量下跌"
        else:
            score += 5
    elif avg_to > 3:
        if avg_pct_today > 0.5:
            score += 5
        elif avg_pct_today < -0.5:
            score -= 5
    elif avg_to < 1.5:
        score -= 10
        signal = "极度低迷"

    # 换手率变化
    if turnover_change > 1.5:
        if avg_pct_today > 0:
            score += 10  # 放量上涨
        else:
            score -= 10  # 放量下跌
    elif turnover_change < 0.7:
        score -= 10  # 缩量

    # 分布形态
    if avg_to > 4 and cv < 0.6:
        if avg_pct_today > 0.5:
            score += 15  # 均值高+收敛+涨 → 全面活跃
            signal = "全面活跃·看涨"
        elif avg_pct_today < -0.5:
            score -= 20  # 均值高+收敛+跌 → 全面抛售
            signal = "全面抛售·看跌"
    elif avg_to > 4 and cv > 1.2:
        score -= 15  # 均值高+发散 → 分歧大
        signal = "分歧加大·顶部预警"
    elif avg_to > 4 and skew > 1.5:
        score -= 10  # 少数暴涨
        signal = "少数活跃·顶部警示"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "avg_turnover": round(avg_to, 2),
            "std_turnover": round(std_to, 2), "skewness": round(skew, 2),
            "turnover_change": round(float(turnover_change), 2), "signal": signal}


def calc_all_sentiment(theme_codes: list, limit_list: pd.DataFrame, limit_step: pd.DataFrame,
                       daily_basic: pd.DataFrame, trade_date: str, klines: dict = None) -> dict:
    """计算情绪层全部因子"""
    return {
        "limit_up_ladder": calc_limit_up_ladder(theme_codes, limit_list, limit_step, daily_basic, trade_date),
        "turnover_distribution": calc_turnover_distribution(daily_basic, theme_codes, klines),
    }
