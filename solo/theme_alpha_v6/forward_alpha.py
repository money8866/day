#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Forward Alpha Module V2 - 未来收益预测核心

六因子结构（AQR / Two Sigma 风格）：
  ① Rotation Timing (25%)        - 轮动时机（含反转张力作为辅助）
  ② Capital Persistence (20%)   - 持续资金（含聪明钱拆分）
  ③ Trend Quality (20%)         - 趋势质量
  ④ Catalyst (15%)              - 事件催化（涨停密度+龙虎榜+DC热度）
  ⑤ Relative Rotation (10%)     - 相对轮动强度（vs全市场）
  ⑥ Leader Ecology (10%)        - 龙头生态

已删除：
  ❌ 单独的"反转张力" -> 并入 Rotation Timing 作为辅助条件
  ❌ 单独的"聪明钱"   -> 拆分到 Capital Persistence 避免重复计分
"""
import numpy as np
import pandas as pd


# ============================================================
#  ① Rotation Timing (25%) - 轮动时机
# ============================================================

def compute_rotation_timing(daily, codes):
    """轮动时机：判断主题是否处于最佳介入时点

    核心逻辑：
      - 健康回调后企稳 = 最佳介入时机
      - 上涨中加速 = 良好时机
      - 超涨滞涨 = 差时机（反转风险）
      - 加速下跌 = 最差时机

    含反转张力作为辅助条件：
      - 短线超跌（r5 < -8%）且出现企稳信号 -> 反弹概率高
      - 短线超涨（r5 > 12%）-> 回撤风险高

    返回 0-100
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0

    price = sub.groupby("trade_date")["close"].mean().sort_index()
    n = len(price)
    if n < 25:
        return 50.0

    r3 = (price.iloc[-1] / price.iloc[-4] - 1) if n > 3 else 0
    r5 = (price.iloc[-1] / price.iloc[-6] - 1) if n > 5 else 0
    r10 = (price.iloc[-1] / price.iloc[-11] - 1) if n > 10 else 0
    r20 = (price.iloc[-1] / price.iloc[-21] - 1) if n > 20 else 0

    # 动量加速度
    accel_short = r5 - r20
    accel_mid = r10 - r20
    raw_accel = accel_short * 0.6 + accel_mid * 0.4

    # ===== 主判断：四象限 =====
    if r5 > 0 and r20 > 0:
        # 上涨趋势中
        if raw_accel >= 0:
            score = 70 + min(raw_accel * 500, 20)  # 70-90 加速上行
        else:
            score = 50 + raw_accel * 300  # 50-55 减速但仍在涨
    elif r5 < 0 and r20 < 0:
        # 下降通道中
        if raw_accel >= 0:
            score = 35 + min(raw_accel * 300, 10)  # 35-45 下跌减速
        else:
            score = 15 + max(raw_accel * 200, -5)  # 10-15 加速下跌
    elif r5 > 0 and r20 < 0:
        # 短期反弹但中期下跌
        score = 45 + min(r5 * 400, 10)  # 45-55 可能只是反弹
    elif r5 < 0 and r20 > 0:
        # 短期回调但中期上涨 = 健康回调（最佳买点！）
        score = 60 + min(abs(raw_accel) * 200, 15)  # 60-75
    else:
        score = 50

    # ===== 辅助条件：反转张力（原reversion_tension并入）=====
    # 超跌反弹信号：r5深跌但出现企稳
    if r5 < -0.08 and r3 > -0.01:
        # 5日大跌但近3天企稳 -> 反弹概率高
        score = max(score, 65)
    if r20 < -0.10 and r5 > 0:
        # 深度回调后5日转正 -> 底部反转
        score = max(score, 68)

    # 超涨回撤风险：r5暴涨但3日加速度为负（滞涨）
    if r5 > 0.12 and r3 < r5 * 0.3:
        score = min(score, 35)  # 超涨+滞涨 = 差时机

    return float(np.clip(score, 5, 95))


# ============================================================
#  ② Capital Persistence (20%) - 持续资金
# ============================================================

def compute_capital_persistence(daily, codes, moneyflow=None):
    """持续资金：资金流入的持续性和质量

    核心逻辑：
      - 量价齐升 = 持续资金流入
      - 缩量回调 = 资金未走
      - 放量大跌 = 资金出逃
      - 价涨量缩 = 虚涨

    含聪明钱拆分（原smart_money_divergence的量价部分并入）：
      - 机构逆势买入 = 底部信号
      - 机构顺势卖出 = 顶背离

    返回 0-100
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0

    price = sub.groupby("trade_date")["close"].mean().sort_index()
    amt = sub.groupby("trade_date")["amount"].sum().sort_index()
    n = len(price)
    if n < 10:
        return 50.0

    r5_price = (price.iloc[-1] / price.iloc[-6] - 1) if n > 5 else 0
    amt_5 = amt.iloc[-5:].mean()
    amt_20 = amt.iloc[-20:].mean() if n > 20 else amt.mean()
    vol_change = (amt_5 / amt_20 - 1) if amt_20 > 0 else 0

    # ===== 量价关系评分（原smart_money核心）=====
    if r5_price > 0.03 and vol_change > 0.2:
        score = 85  # 量价齐升
    elif r5_price > 0.03 and vol_change < -0.1:
        score = 30  # 价涨量缩 = 虚涨
    elif r5_price < -0.03 and vol_change < -0.2:
        score = 75  # 缩量回调 = 资金未走
    elif r5_price < -0.03 and vol_change > 0.3:
        score = 35  # 放量大跌 = 资金出逃
    elif r5_price > 0.02 and abs(vol_change) < 0.1:
        score = 68  # 温和放量上涨
    elif abs(r5_price) < 0.02 and vol_change > 0.2:
        score = 70  # 横盘放量 = 蓄势
    elif abs(r5_price) < 0.02 and vol_change < -0.1:
        score = 48  # 横盘缩量
    else:
        score = 52

    # ===== 持续性加分：近10日成交额趋势 =====
    if n > 20:
        amt_10 = amt.iloc[-10:].mean()
        amt_prev10 = amt.iloc[-20:-10].mean()
        amt_trend = (amt_10 / amt_prev10 - 1) if amt_prev10 > 0 else 0
        if amt_trend > 0.3 and r5_price > 0:
            score = min(score + 8, 92)  # 成交额持续放大+价格上涨
        elif amt_trend < -0.3:
            score = max(score - 5, 10)  # 成交额持续萎缩

    # ===== 聪明钱拆分：机构资金方向（原smart_money的moneyflow部分）=====
    if moneyflow is not None and not moneyflow.empty:
        mf = moneyflow[moneyflow["ts_code"].isin(codes)]
        if not mf.empty:
            buy_cols = [c for c in ["buy_elg_amount", "buy_elg_amounts",
                                     "elarg_buy_amount"] if c in mf.columns]
            sell_cols = [c for c in ["sell_elg_amount", "sell_elg_amounts",
                                      "elarg_sell_amount"] if c in mf.columns]
            if buy_cols and sell_cols:
                mf_dates = sorted(mf["trade_date"].unique())
                recent_dates = mf_dates[-3:] if len(mf_dates) >= 3 else mf_dates
                mf_recent = mf[mf["trade_date"].isin(recent_dates)]
                buy = mf_recent[buy_cols].sum().sum()
                sell = mf_recent[sell_cols].sum().sum()
                inst_net = (buy - sell) / (buy + sell + 1e-9) if (buy + sell) > 0 else 0

                # 机构逆势买入：价格跌但机构在买 -> 底部信号
                if r5_price < -0.02 and inst_net > 0.05:
                    score = max(score, 80)
                # 机构顺势卖出：价格涨但机构在卖 -> 顶背离
                elif r5_price > 0.03 and inst_net < -0.05:
                    score = min(score, 32)

    return float(np.clip(score, 5, 95))


# ============================================================
#  ③ Trend Quality (20%) - 趋势质量
# ============================================================

def compute_trend_quality(daily, codes):
    """趋势质量：MA排列 + 趋势连续性 + 回撤质量

    核心逻辑：
      - 价格站上所有均线 + 多头排列 = 最高质量
      - 连续创新高 = 趋势强
      - 回撤浅且恢复快 = 趋势健康
      - 均线空头排列 = 趋势破坏

    返回 0-100
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    # ===== ① MA Breadth (40%) - 站上均线的股票比例 =====
    ma5 = sub[sub["trade_date"].isin(sub["trade_date"].unique()[-5:])].groupby("ts_code")["close"].mean()
    ma10 = sub[sub["trade_date"].isin(sub["trade_date"].unique()[-10:])].groupby("ts_code")["close"].mean()
    ma20 = sub[sub["trade_date"].isin(sub["trade_date"].unique()[-20:])].groupby("ts_code")["close"].mean()
    latest_close = latest.set_index("ts_code")["close"]

    common = latest_close.index.intersection(ma20.index)
    if len(common) == 0:
        return 50.0

    above_ma5 = (latest_close[common] > ma5[common]).mean()
    above_ma10 = (latest_close[common] > ma10[common]).mean()
    above_ma20 = (latest_close[common] > ma20[common]).mean()
    ma_breadth = above_ma5 * 0.3 + above_ma10 * 0.3 + above_ma20 * 0.4

    # ===== ② 多头排列 (30%) =====
    avg_close = latest["close"].mean()
    avg_ma5 = ma5[common].mean()
    avg_ma10 = ma10[common].mean()
    avg_ma20 = ma20[common].mean()

    if avg_close > avg_ma5 > avg_ma10 > avg_ma20:
        alignment = 1.0  # 完美多头排列
    elif avg_close > avg_ma10 > avg_ma20:
        alignment = 0.7  # 部分多头
    elif avg_close > avg_ma20:
        alignment = 0.4
    else:
        alignment = 0.0  # 空头排列

    # ===== ③ 回撤质量 (30%) =====
    price = sub.groupby("trade_date")["close"].mean().sort_index()
    n = len(price)
    if n > 20:
        # 近20日最大回撤
        window = price.iloc[-20:]
        peak = window.expanding().max()
        drawdown = (window / peak - 1).min()
        # 回撤浅=质量好
        if drawdown > -0.03:
            dd_quality = 1.0  # 几乎无回撤
        elif drawdown > -0.06:
            dd_quality = 0.8
        elif drawdown > -0.10:
            dd_quality = 0.6
        elif drawdown > -0.15:
            dd_quality = 0.4
        else:
            dd_quality = 0.2
    else:
        dd_quality = 0.5

    score = ma_breadth * 40 + alignment * 30 + dd_quality * 30
    return float(np.clip(score * 100, 5, 95))


# ============================================================
#  ④ Catalyst (15%) - 事件催化
# ============================================================

def compute_catalyst(daily, codes, limit_df=None, top_df=None, dc_hot=None):
    """事件催化：涨停密度+龙虎榜+DC热度

    核心逻辑：
      - 涨停股多 = 强催化
      - 龙虎榜上榜 = 机构关注
      - DC热度高 = 散户关注度高
      - 无催化 = 中性偏低

    返回 0-100
    """
    score = 35  # 基础分（无催化时偏低）

    # ===== ① 涨停密度 (50%) =====
    if limit_df is not None and not limit_df.empty:
        limit_set = set(limit_df["ts_code"].tolist()) if "ts_code" in limit_df.columns else set()
        limit_count = len(limit_set & set(codes))
        limit_ratio = limit_count / len(codes) if codes else 0
        # 涨停密度评分
        if limit_ratio > 0.15:
            score = 85  # 15%以上涨停 = 强催化
        elif limit_ratio > 0.08:
            score = 72
        elif limit_ratio > 0.03:
            score = 60
        elif limit_ratio > 0:
            score = 48

    # ===== ② 龙虎榜 (30%) =====
    if top_df is not None and not top_df.empty:
        top_set = set(top_df["ts_code"].tolist()) if "ts_code" in top_df.columns else set()
        top_count = len(top_set & set(codes))
        if top_count >= 3:
            score = min(score + 12, 92)  # 多只上龙虎榜
        elif top_count >= 1:
            score = min(score + 6, 85)

    # ===== ③ DC热度 (20%) =====
    if dc_hot is not None and not dc_hot.empty:
        # DC热度表中可能有股票代码列
        dc_col = None
        for col in ["ts_code", "code", "stock_code"]:
            if col in dc_hot.columns:
                dc_col = col
                break
        if dc_col:
            dc_set = set(dc_hot[dc_col].tolist())
            dc_count = len(dc_set & set(codes))
            if dc_count >= 5:
                score = min(score + 8, 90)
            elif dc_count >= 2:
                score = min(score + 4, 85)

    return float(np.clip(score, 5, 95))


# ============================================================
#  ⑤ Relative Rotation (10%) - 相对轮动强度
# ============================================================

def compute_relative_rotation(daily, codes, all_momentums=None):
    """相对轮动强度：vs全市场的相对强弱

    核心逻辑：
      - 主题涨幅远超市场平均 = 强相对轮动
      - 与市场同步 = 中性
      - 跑输市场 = 弱轮动

    返回 0-100
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0

    price = sub.groupby("trade_date")["close"].mean().sort_index()
    n = len(price)
    if n < 20:
        return 50.0

    r5 = (price.iloc[-1] / price.iloc[-6] - 1) if n > 5 else 0
    r10 = (price.iloc[-1] / price.iloc[-11] - 1) if n > 10 else 0
    r20 = (price.iloc[-1] / price.iloc[-21] - 1) if n > 20 else 0
    theme_momentum = r5 * 0.25 + r10 * 0.30 + r20 * 0.25 + (r20 * 0.20 if n > 40 else 0)

    # 全市场平均动量
    if all_momentums and len(all_momentums) > 5:
        market_avg = np.mean(all_momentums)
        market_std = np.std(all_momentums)
        # Z-score 衡量相对强弱
        if market_std > 0:
            z_score = (theme_momentum - market_avg) / market_std
        else:
            z_score = 0
        # Z-score 映射到 0-100
        # z=1.0 (top 16%) -> 80, z=0 -> 55, z=-1.0 -> 30
        score = 55 + z_score * 25
    else:
        # 无全市场数据时用绝对动量
        score = 50 + theme_momentum * 800

    return float(np.clip(score, 5, 95))


# ============================================================
#  ⑥ Leader Ecology (10%) - 龙头生态
# ============================================================

def compute_leader_ecology(daily, codes, leader_code=None, leader_score=0):
    """龙头生态：龙头股的带动效应

    核心逻辑：
      - 龙头强势 + 跟风股多 = 良好生态
      - 龙头弱 + 无跟风 = 生态差
      - 无龙头 = 中性

    返回 0-100
    """
    if not leader_code:
        return 45  # 无龙头识别 = 偏低

    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return 45

    leader_data = sub[sub["ts_code"] == leader_code].sort_values("trade_date")
    if len(leader_data) < 10:
        return 45

    # ===== ① 龙头自身强度 (50%) =====
    leader_score_norm = np.clip(leader_score, 0, 100)

    # ===== ② 跟风效应 (50%) =====
    # 龙头近5日涨幅 vs 主题平均涨幅
    leader_r5 = (leader_data["close"].iloc[-1] / leader_data["close"].iloc[-6] - 1) if len(leader_data) > 5 else 0

    price = sub.groupby("trade_date")["close"].mean().sort_index()
    theme_r5 = (price.iloc[-1] / price.iloc[-6] - 1) if len(price) > 5 else 0

    # 跟风系数：主题涨幅 / 龙头涨幅（>0.5 = 良好跟风）
    if abs(leader_r5) > 0.02:
        follow_ratio = theme_r5 / leader_r5
        if follow_ratio > 0.6:
            follow_score = 85  # 良好跟风
        elif follow_ratio > 0.3:
            follow_score = 65
        elif follow_ratio > 0:
            follow_score = 45
        else:
            follow_score = 30  # 龙头涨但主题跌 = 无跟风
    else:
        follow_score = 50

    score = leader_score_norm * 0.5 + follow_score * 0.5
    return float(np.clip(score, 5, 95))


# ============================================================
#  综合：Forward Alpha Score (六因子加权)
# ============================================================

def compute_forward_alpha(daily, codes, moneyflow=None,
                         limit_df=None, top_df=None, dc_hot=None,
                         all_momentums=None, leader_code=None, leader_score=0,
                         trend_score=50):
    """计算未来Alpha预测分（六因子加权）

    参数：
        daily: 全市场日线数据
        codes: 主题成份股代码列表
        moneyflow: 资金流数据（可选）
        limit_df: 涨停数据（可选）
        top_df: 龙虎榜数据（可选）
        dc_hot: DC热度数据（可选）
        all_momentums: 全主题动量列表（可选）
        leader_code: 龙头代码（可选）
        leader_score: 龙头评分（可选）
        trend_score: 已计算的趋势分（避免重复计算）

    返回：
        (forward_score, signal, reason, sub_scores)
    """
    # 六因子计算
    rotation = compute_rotation_timing(daily, codes)
    cap_persist = compute_capital_persistence(daily, codes, moneyflow)
    trend_q = compute_trend_quality(daily, codes)
    catalyst = compute_catalyst(daily, codes, limit_df, top_df, dc_hot)
    rel_rot = compute_relative_rotation(daily, codes, all_momentums)
    leader_eco = compute_leader_ecology(daily, codes, leader_code, leader_score)

    # 加权合成
    forward_score = (
        rotation * 0.25 +      # Rotation Timing
        cap_persist * 0.20 +   # Capital Persistence
        trend_q * 0.20 +       # Trend Quality
        catalyst * 0.15 +      # Catalyst
        rel_rot * 0.10 +       # Relative Rotation
        leader_eco * 0.10      # Leader Ecology
    )

    # 信号生成
    if forward_score >= 72:
        signal = "强烈看多"
    elif forward_score >= 58:
        signal = "看多"
    elif forward_score >= 42:
        signal = "中性"
    elif forward_score >= 30:
        signal = "看空"
    else:
        signal = "强烈看空"

    reason = _build_reason(rotation, cap_persist, trend_q, catalyst,
                           rel_rot, leader_eco, signal)

    sub_scores = {
        "rotation_timing": round(rotation, 1),
        "capital_persist": round(cap_persist, 1),
        "trend_quality": round(trend_q, 1),
        "catalyst": round(catalyst, 1),
        "relative_rotation": round(rel_rot, 1),
        "leader_ecology": round(leader_eco, 1),
    }

    return float(round(forward_score, 1)), signal, reason, sub_scores


def _build_reason(rotation, cap_persist, trend_q, catalyst,
                  rel_rot, leader_eco, signal):
    """构建预测理由文本"""
    parts = []

    if rotation >= 70:
        parts.append("轮动时机佳(健康回调/加速上行)")
    elif rotation >= 55:
        parts.append("轮动时机尚可")
    elif rotation <= 30:
        parts.append("轮动时机差(趋势衰竭)")

    if cap_persist >= 75:
        parts.append("资金持续流入/量价齐升")
    elif cap_persist <= 30:
        parts.append("资金出逃/量价背离")

    if trend_q >= 70:
        parts.append("趋势质量优(多头排列)")
    elif trend_q <= 35:
        parts.append("趋势破坏(空头排列)")

    if catalyst >= 70:
        parts.append("事件催化强(涨停/龙虎榜)")
    elif catalyst <= 40:
        parts.append("缺乏催化")

    if rel_rot >= 70:
        parts.append("相对轮动强(跑赢市场)")
    elif rel_rot <= 35:
        parts.append("相对轮动弱(跑输市场)")

    if leader_eco >= 70:
        parts.append("龙头生态好(龙头强+跟风)")
    elif leader_eco <= 40:
        parts.append("龙头生态弱")

    if not parts:
        parts.append("多空信号混杂")

    return "，".join(parts)
