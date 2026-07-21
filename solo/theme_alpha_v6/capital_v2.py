#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V7 - 资金活跃与弹性因子 (35%)

核心目标：剔除"高股息/沉淀资金"的虚高得分，精准度量
"交易型活跃资金/游资/机构增量"的真实活性。

设计哲学：
  资金的大小 ≠ 资金的活性
  高股息股票的资金量巨大但惰性强，不具备交易弹性
  真正的活跃主题 = 换手率提升 + 大阳线密度 + 自由流通市值有效换手

子维度：
  1. 相对换手率 Z-Score (30%) - 主题换手率 vs 全市场基准
  2. 大阳线/涨停渗透率 (25%) - 涨幅>3%成分股占比
  3. 自由流通市值流入比 (20%) - 成交额 / 自由流通市值 (换手质量)
  4. 成交额弹性 (15%) - 5日量/20日量的变异系数 (活跃度趋势)
  5. 游资/机构净流入比 (10%) - 小单vs大单结构 (识别游资活跃度)
"""

import numpy as np
import pandas as pd


def compute_capital_vitality(daily, daily_basic, codes, market_turnover=None):
    """综合资金活跃与弹性评分 (0-100)

    参数:
        daily: DataFrame, 全市场日线 (含 ts_code, trade_date, pct_chg, amount, vol)
        daily_basic: DataFrame, 每日基本面 (含 ts_code, turnover_rate, circ_mv)
        codes: list, 主题成分股代码
        market_turnover: float, 全市场当日成交额(亿元), 可选

    返回:
        score: float 0-100
        sub_metrics: dict 子维度明细
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0, {}

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    # ============================================================
    # ① 相对换手率 Z-Score (30%)
    # ============================================================
    rel_turnover_z = _calc_rel_turnover_z(daily_basic, codes, latest_day)

    # ============================================================
    # ② 大阳线/涨停渗透率 (25%)
    # ============================================================
    big_candle_ratio = _calc_big_candle_ratio(latest, codes)

    # ============================================================
    # ③ 自由流通市值流入比 (20%)
    # ============================================================
    circ_mv_turnover = _calc_circ_mv_turnover(latest, daily_basic, codes)

    # ============================================================
    # ④ 成交额弹性 (15%)
    # ============================================================
    amt_elasticity = _calc_amt_elasticity(sub, codes)

    # ============================================================
    # ⑤ 游资/机构净流入比 (10%)
    # ============================================================
    hot_money_ratio = _calc_hot_money_ratio(latest, codes)

    # ===== 加权合成 =====
    raw = (
        rel_turnover_z * 0.30 +
        big_candle_ratio * 0.25 +
        circ_mv_turnover * 0.20 +
        amt_elasticity * 0.15 +
        hot_money_ratio * 0.10
    )

    # 非线性拉伸：让高分区分度更大
    score = float(np.clip(_amplify(raw / 100.0) * 100, 5, 98))

    sub_metrics = {
        "rel_turnover_z": round(rel_turnover_z, 1),
        "big_candle_ratio": round(big_candle_ratio, 1),
        "circ_mv_turnover": round(circ_mv_turnover, 1),
        "amt_elasticity": round(amt_elasticity, 1),
        "hot_money_ratio": round(hot_money_ratio, 1),
    }

    return score, sub_metrics


def _calc_rel_turnover_z(daily_basic, codes, latest_day):
    """相对换手率 Z-Score

    衡量主题换手率相对于全市场水平的偏离程度。
    高股息/防御板块换手率低，Z-Score 为负。
    活跃主题换手率高，Z-Score 为正。

    返回: 0-100 (Z=0 -> 50, Z=1 -> 70, Z=2 -> 85, Z=-1 -> 30)
    """
    if daily_basic is None or daily_basic.empty:
        return 50.0

    basic = daily_basic[daily_basic["ts_code"].isin(codes)]
    if basic.empty:
        return 50.0

    # 主题平均换手率
    theme_turnover = basic["turnover_rate"].mean()
    if pd.isna(theme_turnover):
        return 50.0

    # 全市场换手率分布
    market_turnovers = daily_basic["turnover_rate"].dropna()
    if market_turnovers.empty:
        return 50.0

    market_mean = market_turnovers.mean()
    market_std = market_turnovers.std()
    if market_std <= 1e-9:
        return 50.0

    z = (theme_turnover - market_mean) / market_std

    # Z-score 映射到 0-100 (两端压缩)
    score = 50 + z * 20
    return float(np.clip(score, 5, 95))


def _calc_big_candle_ratio(latest, codes):
    """大阳线/涨停渗透率

    当日涨幅 > 3% 的成分股占比。
    涨停股双倍权重（涨停 = 最强弹性信号）。

    返回: 0-100
    """
    if latest.empty:
        return 50.0

    pct = latest["pct_chg"].dropna()
    if len(pct) == 0:
        return 50.0

    # 大阳线 (>3%)
    big_candle = (pct > 3).mean()
    # 涨停 (>9.8%)
    limit_up = (pct > 9.5).mean()
    # 大阴线惩罚 (>5%跌幅)
    big_black = (pct < -5).mean()

    raw = big_candle * 60 + limit_up * 80 - big_black * 40
    return float(np.clip(raw, 5, 95))


def _calc_circ_mv_turnover(latest, daily_basic, codes):
    """自由流通市值流入比

    成交额 / 自由流通市值。
    同样的成交额，自由流通市值越小的主题弹性越大。
    这能有效区分"大市值沉底资金"和"小市值活跃资金"。

    返回: 0-100
    """
    if latest.empty:
        return 50.0

    # 主题当日总成交额(万元)
    total_amt = latest["amount"].sum()  # amount 单位是元
    if total_amt <= 0:
        return 50.0

    # 主题自由流通市值
    if daily_basic is not None and not daily_basic.empty:
        basic = daily_basic[daily_basic["ts_code"].isin(codes)]
        if not basic.empty:
            total_circ_mv = basic["circ_mv"].sum() * 1e4  # 万元
            if total_circ_mv > 0:
                turnover_ratio = total_amt / total_circ_mv
                # 换手率 > 2% = 高活性, < 0.5% = 低活性
                # 饱和映射
                if turnover_ratio > 0.05:
                    score = 85
                elif turnover_ratio > 0.03:
                    score = 70
                elif turnover_ratio > 0.02:
                    score = 60
                elif turnover_ratio > 0.01:
                    score = 50
                elif turnover_ratio > 0.005:
                    score = 40
                else:
                    score = 25
                return float(score)

    return 50.0


def _calc_amt_elasticity(sub, codes):
    """成交额弹性

    近5日成交额 vs 近20日成交额的变异系数(CV)，
    以及近5日/20日均量的比值。
    活跃主题的成交额应该是"放大中"的，而非萎缩的。

    返回: 0-100
    """
    amt = sub.groupby("trade_date")["amount"].sum().sort_index()
    if len(amt) < 5:
        return 50.0

    amt_5 = amt.iloc[-5:].mean()
    amt_20 = amt.iloc[-20:].mean() if len(amt) >= 20 else amt.mean()

    if amt_20 <= 0:
        return 50.0

    # 量比 (5日/20日)
    vol_ratio = amt_5 / amt_20

    # 变异系数 (衡量活跃度的波动放大)
    cv_5 = amt.iloc[-5:].std() / amt_5 if amt_5 > 0 else 0

    # 综合评分
    # 量比 > 1.2 = 放量活跃, cv 高 = 弹性大(波动活跃)
    if vol_ratio > 1.3 and cv_5 > 0.15:
        score = 80  # 放量 + 高弹性 = 极活跃
    elif vol_ratio > 1.1 and cv_5 > 0.1:
        score = 65
    elif vol_ratio > 0.9:
        score = 50  # 中性
    elif vol_ratio > 0.7:
        score = 35  # 缩量但未萎缩
    else:
        score = 20  # 极度缩量 = 资金离场

    return float(score)


def _calc_hot_money_ratio(latest, codes):
    """游资/机构净流入比

    通过成交额结构判断：
    - 小单成交占比高 = 游资活跃
    - 大单成交占比高 = 机构参与
    - 没有资金结构数据时，用涨停/大阳线替代

    返回: 0-100
    """
    # 没有资金结构数据时，用大阳线+涨停比例替代
    if latest.empty:
        return 50.0

    pct = latest["pct_chg"].dropna()
    if len(pct) == 0:
        return 50.0

    # 涨停/大阳线数量
    active_count = (pct > 5).sum()
    limit_up_count = (pct > 9.5).sum()

    total_stocks = len(pct)
    if total_stocks == 0:
        return 50.0

    # 活跃股比例
    active_ratio = active_count / total_stocks

    # 有涨停 = 游资参与感强
    if limit_up_count >= 3:
        score = 85
    elif limit_up_count >= 1:
        score = 70
    elif active_ratio > 0.3:
        score = 65
    elif active_ratio > 0.15:
        score = 55
    elif active_ratio > 0.05:
        score = 45
    else:
        score = 30

    return float(score)


def _amplify(pct):
    """非线性放大"""
    return np.clip(np.power(np.clip(pct, 0, 1), 0.75), 0, 1)