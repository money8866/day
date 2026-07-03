#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
趋势延续评分 (ContinuationScore) — 衡量主题"继续走强"的概率

核心思想：
  强势主题的正常回调不是"转弱"，而是"分歧买点"。
  本模块识别两类机会：
    1. 已经强势且会延续（均线多头 + 回调健康 + 龙头不倒）
    2. 分歧后大概率回归强势（回调缩量 + 支撑有效 + 龙头守住）

四个子维度：
  ① 主题指数趋势 (30%) — 均线位置 + 斜率方向
  ② 回调健康度 (25%) — 回撤深度是否在主升浪正常范围
  ③ 龙头延续性 (25%) — 龙头是否守住 MA10、未现高位长阴
  ④ 分歧度 (20%) — 主题内股票涨跌离散度，适度分歧=买点
"""
import numpy as np
import pandas as pd


def _theme_index(daily: pd.DataFrame, codes: list) -> pd.Series:
    """构建主题指数（成分股等权平均收盘价）"""
    sub = daily[daily["ts_code"].isin(codes)]
    return sub.groupby("trade_date")["close"].mean().sort_index()


def compute_continuation_score(daily: pd.DataFrame, codes: list,
                                leader_code: str = None) -> float:
    """
    返回 0-100 趋势延续评分。
    高分 = 继续走强概率高（强势延续 或 分歧后回归强势）
    低分 = 趋势已破坏
    """
    if not codes or len(codes) < 3:
        return 50.0

    theme_idx = _theme_index(daily, codes)
    n = len(theme_idx)
    if n < 20:
        return 50.0

    prices = theme_idx.values
    latest = prices[-1]

    # ===== ① 主题指数趋势 (30%) =====
    ma5 = np.mean(prices[-5:])
    ma10 = np.mean(prices[-10:])
    ma20 = np.mean(prices[-20:])
    ma60 = np.mean(prices[-60:]) if n >= 60 else ma20

    # MA20 斜率（近20日 vs 前20日）
    if n >= 40:
        ma20_prev = np.mean(prices[-40:-20])
        ma20_slope = (ma20 - ma20_prev) / ma20_prev
    else:
        ma20_slope = 0

    # MA10 斜率
    if n >= 20:
        ma10_prev = np.mean(prices[-20:-10])
        ma10_slope = (ma10 - ma10_prev) / ma10_prev
    else:
        ma10_slope = 0

    if latest > ma20 and ma20_slope > 0.01:
        trend_s = 95  # 强势：价在MA20上 + MA20上行
    elif latest > ma10 and ma10_slope > 0:
        trend_s = 80  # 偏强：价在MA10上 + MA10上行
    elif latest > ma20:
        trend_s = 65  # 中性偏强：价在MA20上但斜率平
    elif latest > ma60:
        trend_s = 40  # 回调到MA60
    else:
        trend_s = 20  # 趋势破坏

    # ===== ② 回调健康度 (25%) =====
    high_20 = np.max(prices[-20:])
    drawdown = (high_20 - latest) / high_20

    # 健康回调区间：3%-8%（主升浪正常回调，最佳买点）
    if 0.03 <= drawdown <= 0.08:
        dd_s = 95
    elif 0.08 < drawdown <= 0.12:
        dd_s = 65  # 深度回调但未破位
    elif drawdown < 0.03:
        dd_s = 70  # 高位，可能有追高风险
    elif 0.12 < drawdown <= 0.20:
        dd_s = 40  # 大幅回调，观察
    else:
        dd_s = 15  # 趋势破坏

    # ===== ③ 龙头延续性 (25%) =====
    leader_s = 50
    if leader_code and leader_code in daily["ts_code"].values:
        ld = daily[daily["ts_code"] == leader_code].sort_values("trade_date")
        if len(ld) >= 20:
            lc = ld["close"].values
            lp = ld["pct_chg"].values
            ll = lc[-1]
            lma5 = np.mean(lc[-5:])
            lma10 = np.mean(lc[-10:])
            lma20 = np.mean(lc[-20:])
            high_5 = np.max(lc[-5:])
            high_10 = np.max(lc[-10:])
            last_pct = lp[-1]
            # 近3天最大跌幅
            max_drop_3 = np.min(lp[-3:]) if len(lp) >= 3 else 0

            if ll > lma5 > lma10 and high_5 >= high_10 * 0.98 and last_pct > -5:
                leader_s = 95  # 龙头强势：多头排列 + 近5日接新高 + 无长阴
            elif ll > lma10 and last_pct > -5 and max_drop_3 > -7:
                leader_s = 75  # 龙头稳：守住MA10 + 无长阴
            elif ll > lma10:
                leader_s = 55  # 龙头回调但守住MA10
            elif ll > lma20:
                leader_s = 35  # 龙头破MA10但守MA20
            else:
                leader_s = 15  # 龙头破位

    # ===== ④ 分歧度 (20%) =====
    # 主题内股票近5天涨跌的离散度
    trade_dates = sorted(daily["trade_date"].unique())
    if len(trade_dates) >= 5:
        recent_5d_dates = trade_dates[-5:]
        recent = daily[
            (daily["ts_code"].isin(codes)) &
            (daily["trade_date"].isin(recent_5d_dates))
        ]
        if not recent.empty:
            stock_returns = recent.groupby("ts_code")["pct_chg"].sum()
            dispersion = stock_returns.std()
            theme_ret = stock_returns.mean()

            # 适度分歧 + 主题整体正收益 = 健康分歧，买点
            # 高度一致看好 = 追高风险
            # 高度一致看跌 = 趋势破坏
            if theme_ret > 0 and 2 < dispersion < 8:
                div_s = 88  # 健康分歧，最佳买点
            elif theme_ret > 0 and dispersion <= 2:
                div_s = 65  # 一致看好，追高风险
            elif theme_ret > 5 and dispersion >= 8:
                div_s = 55  # 强势但分歧大，分歧转一致or见顶
            elif theme_ret < -3 and dispersion > 8:
                div_s = 25  # 一致看跌
            elif theme_ret > 0:
                div_s = 72  # 正常偏强
            elif theme_ret > -3:
                div_s = 50  # 横盘
            else:
                div_s = 30  # 偏弱
        else:
            div_s = 50
    else:
        div_s = 50

    final = (trend_s * 0.30 + dd_s * 0.25 +
             leader_s * 0.25 + div_s * 0.20)
    return float(np.clip(final, 0, 100))


def continuation_signal(cont_score: float, composite: float, stage: str) -> str:
    """
    生成延续标签（用于报告展示，与 trade_signal 阈值一致）：
      - 强势延续：综合强 + 延续强 + 启动/扩张
      - 分歧买点：综合低 + 延续很高 + 启动/扩张
      - 观察等待：延续中等
      - 趋势走弱：延续低
    """
    import config
    # 强势延续
    if (cont_score >= config.SB_CONTINUATION
        and composite >= config.WATCH_COMPOSITE
        and stage in config.SB_STAGES):
        return "强势延续"
    # 分歧买点：综合低 + 延续很高
    if (cont_score >= config.WATCH_CONTINUATION
        and composite < config.WATCH_DIV_COMPOSITE
        and stage in config.SB_STAGES):
        return "分歧买点"
    # 观察等待
    if cont_score >= config.HOLD_CONTINUATION:
        return "观察等待"
    return "趋势走弱"
