#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V7 - 梯队完整度与扩散因子 (30%)

核心目标：量化主题的"龙头-中军-跟风"三级梯队结构。

设计哲学：
  真正有持续性的主题不是靠一只龙头单打独斗，而是：
    龙头 - 打开空间（Top 1-2，创新高能力强）
    中军 - 托住底盘（市值前20%，趋势稳健）
    跟风 - 造赚钱效应（涨幅>3%比例高，扩散广）

  三个梯队缺一不可：
    - 龙头高但无跟风 = 独角戏，不可持续
    - 跟风多但无龙头 = 群龙无首，走不远
    - 龙头跟风都好但中军破位 = 底盘不稳，随时崩塌

子维度：
  1. 龙头高度与创新高能力 (35%)
  2. 中军趋势强度 (30%)
  3. 跟风扩散度 (25%)
  4. 梯队结构完整性 (10%) - 三梯队协同打分
"""

import numpy as np
import pandas as pd


def compute_echelon_score(daily, codes, top_df=None, limit_df=None):
    """梯队完整度与扩散综合评分 (0-100)

    参数:
        daily: DataFrame, 全市场日线
        codes: list, 主题成分股代码
        top_df: DataFrame, 龙虎榜数据（可选）
        limit_df: DataFrame, 涨停数据（可选）

    返回:
        score: float 0-100
        sub_metrics: dict 子维度明细
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 5:
        return 50.0, {}

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day].copy()

    if latest.empty:
        return 50.0, {}

    # ============================================================
    # ① 龙头高度与创新高能力 (35%)
    # ============================================================
    leader_score = _calc_leader_height(sub, latest, codes, top_df, limit_df)

    # ============================================================
    # ② 中军趋势强度 (30%)
    # ============================================================
    backbone_score = _calc_backbone_strength(sub, latest, codes)

    # ============================================================
    # ③ 跟风扩散度 (25%)
    # ============================================================
    follower_score = _calc_follower_diffusion(latest, codes)

    # ============================================================
    # ④ 梯队结构完整性 (10%)
    # ============================================================
    structure_score = _calc_structure_integrity(
        leader_score, backbone_score, follower_score
    )

    # ===== 加权合成 =====
    raw = (
        leader_score * 0.35 +
        backbone_score * 0.30 +
        follower_score * 0.25 +
        structure_score * 0.10
    )

    # 非线性拉伸
    score = float(np.clip(_amplify(raw / 100.0) * 100, 5, 98))

    sub_metrics = {
        "leader_height": round(leader_score, 1),
        "backbone_strength": round(backbone_score, 1),
        "follower_diffusion": round(follower_score, 1),
        "structure_integrity": round(structure_score, 1),
    }

    return score, sub_metrics


def _calc_leader_height(sub, latest, codes, top_df=None, limit_df=None):
    """龙头高度与创新高能力 (0-100)

    核心逻辑：
      - 龙头 = 阶段涨幅最大、成交额最高的1-2只股票
      - 衡量：近5日涨幅、是否创新高(60日高点)、是否涨停
      - 龙头如果同时是龙虎榜上榜股，加分

    返回: 0-100
    """
    # 确定龙头候选：近5日涨幅最大 + 成交额最大
    recent = sub.groupby("ts_code").apply(
        lambda g: _get_latest_n(g, 5)
    ).reset_index(drop=True)

    if recent.empty:
        return 50.0

    # 最近5日涨幅
    pct_5d = recent.groupby("ts_code")["pct_chg"].sum()

    # 最新日成交额
    latest_idx = sub["trade_date"].max()
    latest_amt = sub[sub["trade_date"] == latest_idx].groupby("ts_code")["amount"].sum()

    # 综合评分 = 涨幅 * 0.6 + 成交额百分位 * 0.4
    pct_rank = pct_5d.rank(pct=True)
    amt_rank = latest_amt.rank(pct=True)
    combined = (pct_rank * 0.6 + amt_rank * 0.4).sort_values(ascending=False)

    if len(combined) == 0:
        return 50.0

    # 取Top 2作为龙头
    top2 = combined.head(2)
    top2_stocks = top2.index.tolist()

    # ===== 龙头指标 =====
    # 1. 龙头涨幅强度
    leader_ret = pct_5d[top2_stocks].mean() if len(top2_stocks) > 0 else 0

    # 2. 龙头是否创新高 (近60日)
    _check_high = 0
    for code in top2_stocks:
        stock_data = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock_data) >= 5:
            close = stock_data["close"].values
            latest_close = close[-1]
            # 60日高点（100个交易日）
            lookback = min(100, len(close) - 1)
            high_60d = close[-lookback:-1].max() if lookback > 0 else close[-2]
            if latest_close >= high_60d * 0.98:
                _check_high += 1

    # 3. 龙头是否涨停
    _check_limit = 0
    if limit_df is not None and not limit_df.empty:
        limit_set = set(limit_df["ts_code"].tolist()) if "ts_code" in limit_df.columns else set()
        _check_limit = len(limit_set & set(top2_stocks))

    # 4. 龙头是否上龙虎榜
    _check_top = 0
    if top_df is not None and not top_df.empty:
        top_set = set(top_df["ts_code"].tolist()) if "ts_code" in top_df.columns else set()
        _check_top = len(top_set & set(top2_stocks))

    # ===== 综合评分 =====
    score = 50  # 基础分

    # 龙头涨幅
    if leader_ret > 0.15:
        score += 20
    elif leader_ret > 0.08:
        score += 12
    elif leader_ret > 0.03:
        score += 5
    elif leader_ret < -0.05:
        score -= 10

    # 创新高
    score += _check_high * 10

    # 涨停
    score += _check_limit * 8

    # 龙虎榜
    score += _check_top * 5

    # 龙头数量（2只最好）
    effective_leaders = min(len(top2_stocks), 2)
    if effective_leaders < 2:
        score -= 5

    return float(np.clip(score, 5, 98))


def _get_latest_n(group, n):
    """取每个group最近n条记录"""
    return group.sort_values("trade_date").tail(n)


def _calc_backbone_strength(sub, latest, codes):
    """中军趋势强度 (0-100)

    中军 = 市值前20%的权重股（主题的"底盘"）
    中军不走弱 = 主题底盘稳健

    核心逻辑：
      - 中军近5日趋势是否向上
      - 中军是否破位（跌破20日线）
      - 中军成交额是否稳定（不萎缩）

    返回: 0-100
    """
    # 按成交额排序取前20%作为中军
    latest_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if latest_amt.empty:
        return 50.0

    n_backbone = max(1, len(latest_amt) // 5)
    backbone_codes = latest_amt.head(n_backbone).index.tolist()

    # 中军近5日涨幅
    recent = sub[sub["ts_code"].isin(backbone_codes)].copy()
    if recent.empty:
        return 50.0

    # 各中军股近5日涨幅
    backbone_5d = []
    below_ma20 = 0
    for code in backbone_codes:
        stock = recent[recent["ts_code"] == code].sort_values("trade_date")
        if len(stock) >= 5:
            r5 = (stock["close"].iloc[-1] / stock["close"].iloc[-6] - 1) if len(stock) > 5 else 0
            backbone_5d.append(r5)

            # 是否跌破20日线
            if len(stock) >= 20:
                ma20 = stock["close"].iloc[-20:].mean()
                if stock["close"].iloc[-1] < ma20 * 0.98:
                    below_ma20 += 1

    if len(backbone_5d) == 0:
        return 50.0

    mean_ret = np.mean(backbone_5d)
    below_ratio = below_ma20 / len(backbone_codes)

    # ===== 评分 =====
    score = 50

    # 中军趋势
    if mean_ret > 0.05:
        score += 20
    elif mean_ret > 0.02:
        score += 10
    elif mean_ret > 0:
        score += 3
    elif mean_ret < -0.03:
        score -= 10
    elif mean_ret < -0.06:
        score -= 20

    # 中军破位惩罚
    if below_ratio > 0.5:
        score -= 20  # 超过一半中军破位 = 底盘不稳
    elif below_ratio > 0.3:
        score -= 10
    elif below_ratio > 0.1:
        score -= 3

    # 中军数量加分（中军多 = 底盘厚）
    if len(backbone_codes) >= 5:
        score += 5
    elif len(backbone_codes) >= 3:
        score += 2

    return float(np.clip(score, 5, 98))


def _calc_follower_diffusion(latest, codes):
    """跟风扩散度 (0-100)

    衡量主题的赚钱效应是否扩散到跟风股。

    核心逻辑：
      - 涨幅>3%的成分股占比 = 短期赚钱效应
      - 涨幅>0%的成分股占比 = 整体活跃度
      - 涨停股占比 = 极致赚钱效应
      - 跌幅>3%的成分股占比 = 负向扩散（惩罚）

    返回: 0-100
    """
    if latest.empty:
        return 50.0

    pct = latest["pct_chg"].dropna()
    if len(pct) == 0:
        return 50.0

    total = len(pct)

    # 各级扩散度
    pct_gt_5 = (pct > 5).sum() / total  # 大涨
    pct_gt_3 = (pct > 3).sum() / total  # 中阳
    pct_gt_0 = (pct > 0).sum() / total  # 上涨
    pct_lt_neg3 = (pct < -3).sum() / total  # 大跌

    # 涨停
    pct_limit = (pct > 9.5).sum() / total

    # ===== 评分 =====
    score = 50

    # 核心扩散：涨幅>3%的成分股占比 > 30% = 强扩散
    if pct_gt_3 > 0.5:
        score += 25
    elif pct_gt_3 > 0.3:
        score += 15
    elif pct_gt_3 > 0.15:
        score += 8
    elif pct_gt_3 > 0.05:
        score += 2
    else:
        score -= 5

    # 大涨扩散 (>5%)
    if pct_gt_5 > 0.2:
        score += 10
    elif pct_gt_5 > 0.1:
        score += 5

    # 涨停扩散
    if pct_limit > 0.1:
        score += 10
    elif pct_limit > 0.05:
        score += 5

    # 上涨家数占比（普涨加分）
    if pct_gt_0 > 0.7:
        score += 5
    elif pct_gt_0 < 0.3:
        score -= 5

    # 大跌惩罚（负扩散）
    if pct_lt_neg3 > 0.3:
        score -= 15
    elif pct_lt_neg3 > 0.15:
        score -= 8

    return float(np.clip(score, 5, 98))


def _calc_structure_integrity(leader, backbone, follower):
    """梯队结构完整性 (0-100)

    检查三个梯队是否协同，是否存在"短板"。

    核心逻辑：
      - 三个梯队分都高 = 完美结构
      - 一个梯队明显低于其他两个 = 结构缺陷
      - 梯队总分 = 最小值惩罚 + 平均值奖励

    返回: 0-100
    """
    scores = np.array([leader, backbone, follower])

    # 最小值（短板效应）
    min_score = scores.min()
    # 平均值
    mean_score = scores.mean()

    # 标准差（衡量梯队差异）
    std_score = scores.std()

    # 如果标准差 > 15，说明梯队差异大，有短板
    if std_score > 15:
        penalty = std_score * 0.5
        raw = mean_score * 0.6 + min_score * 0.4 - penalty
    else:
        # 梯队均匀，奖励
        raw = mean_score * 0.5 + min_score * 0.5 + 5

    return float(np.clip(raw, 5, 98))


def _amplify(pct):
    """非线性放大"""
    return np.clip(np.power(np.clip(pct, 0, 1), 0.75), 0, 1)