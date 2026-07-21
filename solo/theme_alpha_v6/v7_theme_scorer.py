#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V7 梯队爆发力与真资金驱动评分算法 — 完整量化打分引擎

V7综合得分 = 0.35 * 资金交易弹性分 + 0.30 * 梯队完整度分 + 0.20 * 趋势爆发分 + 0.15 * 基础逻辑分

输入: df_theme_data (pandas DataFrame)
  必须包含列: ts_code, trade_date, close, pct_chg, amount, turnover_rate, circ_mv, high, low
  可选列: net_money_flow (资金净流入), net_money_flow_main (主力净流入)

输出: pandas DataFrame
  包含: 主题, V7综合得分, V7阶段, 资金分, 梯队分, 趋势分, 惩罚项说明, 各子维度明细

Author: Quant Director
Version: 7.0
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional


def calculate_v7_theme_score(df_theme_data: pd.DataFrame) -> pd.DataFrame:
    """
    V7 梯队爆发力与真资金驱动评分主函数

    Parameters
    ----------
    df_theme_data : pd.DataFrame
        必须包含列:
        - ts_code        : 股票代码
        - trade_date     : 交易日 (YYYYMMDD 格式字符串或整数)
        - close          : 收盘价
        - pct_chg        : 涨跌幅 (%)
        - amount         : 成交额 (元)
        - turnover_rate  : 换手率 (%)
        - circ_mv        : 自由流通市值 (万元)
        - high           : 最高价
        - low            : 最低价
        可选列:
        - net_money_flow     : 资金净流入 (元)
        - net_money_flow_main : 主力净流入 (元)

    Returns
    -------
    pd.DataFrame
        每行一个主题的评分结果
    """
    _validate_input(df_theme_data)

    theme_names = df_theme_data["theme"].unique()

    results = []
    for theme in theme_names:
        sub = df_theme_data[df_theme_data["theme"] == theme].copy()
        codes = sub["ts_code"].unique().tolist()

        if len(codes) < 5:
            results.append(_build_empty_result(theme, reason="成分股不足5只"))
            continue

        try:
            result = _score_single_theme(sub, codes, theme)
            results.append(result)
        except Exception as e:
            results.append(_build_empty_result(theme, reason=f"异常: {str(e)}"))

    out = pd.DataFrame(results)

    # 排序：综合得分降序
    out = out.sort_values("V7综合得分", ascending=False).reset_index(drop=True)
    out.insert(0, "排名", range(1, len(out) + 1))

    return out


# =========================================================================
# 输入校验
# =========================================================================

_REQUIRED_COLS = {"ts_code", "trade_date", "close", "pct_chg",
                  "amount", "turnover_rate", "circ_mv", "high", "low"}


def _validate_input(df: pd.DataFrame):
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"输入数据缺少必需列: {missing}")
    if "theme" not in df.columns:
        raise ValueError("输入数据必须包含 'theme' 列（主题名称）")


def _build_empty_result(theme: str, reason: str = "") -> dict:
    return {
        "主题": theme,
        "V7综合得分": 0.0,
        "V7阶段": "数据不足",
        "资金分": 0.0,
        "梯队分": 0.0,
        "趋势分": 0.0,
        "基础分": 0.0,
        "惩罚项说明": reason,
    }


# =========================================================================
# 单主题评分
# =========================================================================

def _score_single_theme(sub: pd.DataFrame, codes: List[str], theme: str) -> dict:
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day].copy()

    # ---------- 1. 资金交易弹性分 (35%) ----------
    capital_score, cap_detail = _calc_capital_vitality(sub, latest, codes)

    # ---------- 2. 梯队完整度分 (30%) ----------
    echelon_score, ech_detail, penalty_info = _calc_echelon(sub, latest, codes)
    # penalty_info 从梯队中提取哑铃化信息

    # ---------- 3. 趋势爆发分 (20%) ----------
    trend_score, trd_detail = _calc_trend_breakout(sub, codes)

    # ---------- 4. 基础逻辑分 (15%) ----------
    fundamental_score, fund_detail = _calc_fundamental(sub, latest, codes)

    # ---------- 合成原始得分 ----------
    raw_score = (
        capital_score * 0.35
        + echelon_score * 0.30
        + trend_score * 0.20
        + fundamental_score * 0.15
    )
    raw_score = 0.0 if np.isnan(raw_score) else raw_score

    # ---------- 硬规则惩罚 ----------
    penalties = []
    adjusted_score = raw_score

    # ① 假大阳/拉指数惩罚
    adj1, p1 = _rule_false_big_candle(adjusted_score, latest, codes)
    adjusted_score = adj1
    penalties.extend(p1)

    # ② 梯队哑铃化/龙头单飞惩罚
    adj2, p2 = _rule_dumbbell_fly(adjusted_score, latest, codes)
    adjusted_score = adj2
    penalties.extend(p2)

    # ③ 中军破位硬否决
    adj3, p3, backbone_breakdown = _rule_backbone_veto(adjusted_score, sub, latest, codes)
    adjusted_score = adj3
    penalties.extend(p3)

    # ---------- 阶段判定 ----------
    has_backbone_breakdown = backbone_breakdown
    has_false_big_candle = any("假大阳" in p.get("原因", "") for p in penalties)
    has_dumbbell = any("哑铃化" in p.get("原因", "") or "龙头单飞" in p.get("原因", "") for p in penalties)

    stage = _classify_stage(
        capital_score, echelon_score, trend_score,
        adjusted_score, raw_score,
        has_backbone_breakdown, has_false_big_candle, has_dumbbell,
        len(penalties),
    )

    # ---------- ④ 退潮期系数折扣 ----------
    if stage == "退潮期":
        adjusted_score = adjusted_score * 0.7

    adjusted_score = float(np.clip(adjusted_score, 0, 100))

    # ---------- 惩罚说明 ----------
    penalty_desc = "; ".join(p["原因"] for p in penalties) if penalties else ""

    return {
        "主题": theme,
        "V7综合得分": round(adjusted_score, 1),
        "V7阶段": stage,
        "资金分": round(capital_score, 1),
        "梯队分": round(echelon_score, 1),
        "趋势分": round(trend_score, 1),
        "基础分": round(fundamental_score, 1),
        "惩罚项说明": penalty_desc,
        **{f"资金_{k}": round(v, 1) for k, v in cap_detail.items()},
        **{f"梯队_{k}": round(v, 1) for k, v in ech_detail.items()},
        **{f"趋势_{k}": round(v, 1) for k, v in trd_detail.items()},
        **{f"基础_{k}": round(v, 1) for k, v in fund_detail.items()},
    }


# =========================================================================
# 1. 资金交易弹性分 (35%)
#   权重: 相对换手率 Z-Score(40%) + 自由流通市值流入比(30%) + 大阳线/涨停渗透率(30%)
# =========================================================================

def _calc_capital_vitality(sub: pd.DataFrame, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, dict]:
    if latest.empty or len(codes) < 3:
        return 50.0, {}

    # ---------- ① 相对换手率 Z-Score (40%) ----------
    z_score = _calc_turnover_z(sub, codes)

    # ---------- ② 自由流通市值流入比 (30%) ----------
    circ_mv_ratio = _calc_circ_mv_inflow_ratio(latest, codes)

    # ---------- ③ 大阳线/涨停渗透率 — 近3日 (30%) ----------
    big_candle_score = _calc_big_candle_3d(sub, codes)

    raw = z_score * 0.40 + circ_mv_ratio * 0.30 + big_candle_score * 0.30
    raw = 0.0 if np.isnan(raw) else raw
    score = float(np.clip(_amplify(raw / 100.0) * 100, 5, 98))

    detail = {
        "换手率Z分": round(z_score, 1),
        "自由流通市值流入比": round(circ_mv_ratio, 1),
        "大阳线渗透率": round(big_candle_score, 1),
    }
    return score, detail


def _calc_turnover_z(sub: pd.DataFrame, codes: List[str]) -> float:
    """相对换手率 Z-Score：成分股当日换手率 vs 自身20日均值

    若换手率数据仅当日可用（历史为NaN），则改用成交额(amount)作Z-score。
    """
    z_list = []
    for code in codes:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 21:
            continue

        tr = stock["turnover_rate"]
        latest_tr = tr.iloc[-1]

        # 检查历史换手率是否有效（非NaN、非常数）
        hist_tr = tr.iloc[-21:-1]
        hist_mean = hist_tr.mean()
        hist_std = hist_tr.std()

        if pd.isna(hist_std) or hist_std <= 1e-9:
            # 换手率数据不可用（仅当日有值/常数值），改用成交额作Z-score
            amt = stock["amount"]
            latest_amt = amt.iloc[-1]
            hist_amt = amt.iloc[-21:-1]
            amt_mean = hist_amt.mean()
            amt_std = hist_amt.std()
            if pd.isna(amt_std) or amt_std <= 1e-9:
                continue
            z = (latest_amt - amt_mean) / amt_std
        else:
            z = (latest_tr - hist_mean) / hist_std
        z_list.append(z)

    if not z_list:
        return 50.0

    avg_z = np.mean(z_list)
    score = 50 + avg_z * 20
    return float(np.clip(score, 5, 95))


def _calc_circ_mv_inflow_ratio(latest: pd.DataFrame, codes: List[str]) -> float:
    """自由流通市值流入比

    有主力净流入数据时：主力净流入 / 自由流通市值
    无主力净流入数据时：成交额 / 自由流通市值 (换手效率)
    """
    if "net_money_flow_main" in latest.columns:
        # 有主力净流入数据
        total_inflow = latest["net_money_flow_main"].sum()
    else:
        # 无资金流数据，用成交额和换手率估算
        # 默认假设20%的成交额来自增量资金
        total_amount = latest["amount"].sum()
        total_inflow = total_amount * 0.20

    if total_inflow <= 0 or np.isnan(total_inflow):
        return 30.0

    total_circ_mv = latest["circ_mv"].sum() * 1e4  # 万元→元
    if total_circ_mv <= 0 or np.isnan(total_circ_mv):
        return 50.0

    ratio = total_inflow / total_circ_mv  # 无量纲

    # 饱和映射
    if ratio > 0.03:
        return 85.0
    elif ratio > 0.02:
        return 70.0
    elif ratio > 0.01:
        return 55.0
    elif ratio > 0.005:
        return 45.0
    elif ratio > 0.002:
        return 35.0
    else:
        return 25.0


def _calc_big_candle_3d(sub: pd.DataFrame, codes: List[str]) -> float:
    """大阳线/涨停渗透率 — 近3日

    统计近3个交易日中，涨幅>5%或涨停的成分股出现次数占比。
    多头排列时，大阳线密度高 = 资金攻击意愿强。
    """
    trade_dates = sorted(sub["trade_date"].unique())
    if len(trade_dates) < 3:
        return 50.0

    recent_dates = trade_dates[-3:]
    recent = sub[sub["trade_date"].isin(recent_dates)].copy()

    if recent.empty:
        return 50.0

    # 逐日统计
    daily_hits = []
    for dt in recent_dates:
        day_data = recent[recent["trade_date"] == dt]
        pct = day_data["pct_chg"].dropna()
        if len(pct) == 0:
            continue
        total = len(pct)
        hits = (pct > 5).sum()  # 涨幅>5% 或涨停
        daily_hits.append(hits / total if total > 0 else 0)

    if not daily_hits:
        return 50.0

    avg_hit_ratio = np.mean(daily_hits)

    # 评分
    if avg_hit_ratio > 0.30:
        score = 90
    elif avg_hit_ratio > 0.20:
        score = 75
    elif avg_hit_ratio > 0.10:
        score = 60
    elif avg_hit_ratio > 0.05:
        score = 45
    else:
        score = 30

    return float(score)


# =========================================================================
# 2. 梯队完整度分 (30%)
#   权重: 龙头(35%) + 中军(35%) + 跟风扩散(30%)
# =========================================================================

def _calc_echelon(sub: pd.DataFrame, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, dict, dict]:
    if latest.empty or len(codes) < 5:
        return 50.0, {}, {}

    penalty_info = {}

    # ---------- ① 龙头得分 (35%) ----------
    leader_score, leader_detail = _calc_leader(sub, latest, codes)

    # ---------- ② 中军得分 (35%) ----------
    backbone_score, backbone_detail = _calc_backbone(sub, latest, codes)

    # ---------- ③ 跟风扩散度 (30%) ----------
    follower_score, follower_detail = _calc_follower(latest, codes)

    raw = leader_score * 0.35 + backbone_score * 0.35 + follower_score * 0.30
    raw = 0.0 if np.isnan(raw) else raw
    score = float(np.clip(_amplify(raw / 100.0) * 100, 5, 98))

    detail = {
        **leader_detail,
        **backbone_detail,
        **follower_detail,
    }

    return score, detail, penalty_info


def _calc_leader(sub: pd.DataFrame, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, dict]:
    """龙头得分 (0-100)

    龙头 = 近5日涨幅×0.6 + 成交额百分位×0.4 综合排名 Top 1-2
    考量: 涨幅强度、创新高(60日)、连板(涨停)
    """
    recent = sub.groupby("ts_code").apply(
        lambda g: g.sort_values("trade_date").tail(5)
    ).reset_index(drop=True)

    if recent.empty:
        return 50.0, {}

    pct_5d = recent.groupby("ts_code")["pct_chg"].sum()
    latest_amt = latest.groupby("ts_code")["amount"].sum()

    pct_rank = pct_5d.rank(pct=True)
    amt_rank = latest_amt.rank(pct=True)
    combined = (pct_rank * 0.6 + amt_rank * 0.4).sort_values(ascending=False)

    if len(combined) == 0:
        return 50.0, {}

    top2 = combined.head(2)
    top2_stocks = top2.index.tolist()

    # 龙头涨幅
    leader_ret = pct_5d[top2_stocks].mean() if len(top2_stocks) > 0 else 0

    # 龙头创新高能力
    check_high = 0
    check_limit = 0
    for code in top2_stocks:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) >= 5:
            close = stock["close"].values
            latest_close = close[-1]
            lookback = min(100, len(close) - 1)
            high_60d = close[-lookback:-1].max() if lookback > 0 else close[-2]
            if latest_close >= high_60d * 0.98:
                check_high += 1
            # 连板检查
            if len(stock) >= 3:
                last3 = stock.tail(3)["pct_chg"].values
                if all(c > 9.5 for c in last3):
                    check_limit += 2
                elif sum(1 for c in last3 if c > 9.5) >= 2:
                    check_limit += 1

    score = 50
    if leader_ret > 0.15:
        score += 20
    elif leader_ret > 0.08:
        score += 12
    elif leader_ret > 0.03:
        score += 5
    elif leader_ret < -0.05:
        score -= 10

    score += check_high * 10
    score += check_limit * 8

    if len(top2_stocks) < 2:
        score -= 5

    score = float(np.clip(score, 5, 98))

    detail = {
        "龙头涨幅": round(leader_ret * 100, 1),
        "龙头创新高": check_high,
        "龙头连板": check_limit,
    }
    return score, detail


def _calc_backbone(sub: pd.DataFrame, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, dict]:
    """中军得分 (0-100)

    中军 = 成交额前20%的权重股（主题底盘）
    核心: 20日均线多头排列 + 成交量稳定
    """
    latest_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if latest_amt.empty:
        return 50.0, {}

    n_backbone = max(1, len(latest_amt) // 5)
    backbone_codes = latest_amt.head(n_backbone).index.tolist()

    recent = sub[sub["ts_code"].isin(backbone_codes)].copy()
    if recent.empty:
        return 50.0, {}

    backbone_5d = []
    below_ma20_count = 0
    vol_shrink_count = 0

    for code in backbone_codes:
        stock = recent[recent["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 5:
            continue

        r5 = (stock["close"].iloc[-1] / stock["close"].iloc[-6] - 1) if len(stock) > 5 else 0
        backbone_5d.append(r5)

        # 20日均线多头：MA5 > MA10 > MA20
        if len(stock) >= 20:
            close_s = stock["close"].values
            ma5 = np.mean(close_s[-5:])
            ma10 = np.mean(close_s[-10:])
            ma20 = np.mean(close_s[-20:])
            is_multi_line = ma5 > ma10 > ma20
            if not is_multi_line:
                below_ma20_count += 1

            # 成交量萎缩检查
            if len(stock) >= 5:
                vol_5 = stock["amount"].iloc[-5:].mean()
                vol_20 = stock["amount"].iloc[-20:].mean()
                if vol_20 > 0 and vol_5 / vol_20 < 0.6:
                    vol_shrink_count += 1

    if len(backbone_5d) == 0:
        return 50.0, {}

    mean_ret = np.nanmean(backbone_5d)
    below_ratio = below_ma20_count / len(backbone_codes)
    vol_shrink_ratio = vol_shrink_count / len(backbone_codes)

    score = 50
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

    if below_ratio > 0.5:
        score -= 20
    elif below_ratio > 0.3:
        score -= 10
    elif below_ratio > 0.1:
        score -= 3

    if vol_shrink_ratio > 0.5:
        score -= 10
    elif vol_shrink_ratio > 0.3:
        score -= 5

    if len(backbone_codes) >= 5:
        score += 5
    elif len(backbone_codes) >= 3:
        score += 2

    score = float(np.clip(score, 5, 98))

    detail = {
        "中军数量": len(backbone_codes),
        "中军5日涨幅": round(mean_ret * 100, 1),
        "中军破位比例": round(below_ratio * 100, 1),
        "中军缩量比例": round(vol_shrink_ratio * 100, 1),
    }
    return score, detail


def _calc_follower(latest: pd.DataFrame, codes: List[str]) -> Tuple[float, dict]:
    """跟风扩散度 (0-100)

    当日涨幅>3%的成分股占比 = 核心扩散指标
    """
    if latest.empty:
        return 50.0, {}

    pct = latest["pct_chg"].dropna()
    if len(pct) == 0:
        return 50.0, {}

    total = len(pct)
    pct_gt_3 = (pct > 3).sum() / total
    pct_gt_5 = (pct > 5).sum() / total
    pct_gt_0 = (pct > 0).sum() / total
    pct_lt_neg3 = (pct < -3).sum() / total
    pct_limit = (pct > 9.5).sum() / total

    score = 50
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

    if pct_gt_5 > 0.2:
        score += 10
    elif pct_gt_5 > 0.1:
        score += 5

    if pct_limit > 0.1:
        score += 10
    elif pct_limit > 0.05:
        score += 5

    if pct_gt_0 > 0.7:
        score += 5
    elif pct_gt_0 < 0.3:
        score -= 5

    if pct_lt_neg3 > 0.3:
        score -= 15
    elif pct_lt_neg3 > 0.15:
        score -= 8

    score = float(np.clip(score, 5, 98))

    detail = {
        "跟风>3%比例": round(pct_gt_3 * 100, 1),
        "跟风>5%比例": round(pct_gt_5 * 100, 1),
        "跟风上涨比例": round(pct_gt_0 * 100, 1),
    }
    return score, detail


# =========================================================================
# 3. 趋势爆发分 (20%)
#   基于 RSRS + 均线多头排列持续天数
# =========================================================================

def _calc_trend_breakout(sub: pd.DataFrame, codes: List[str]) -> Tuple[float, dict]:
    """趋势爆发分 (0-100)

    基于主题等权指数的:
      - RSRS 阻力支撑相对强度 (60%)
      - 均线多头排列持续天数 (40%)
    """
    # 构建主题等权指数
    close_pivot = sub.pivot_table(
        index="trade_date", columns="ts_code", values="close", aggfunc="first"
    )
    if close_pivot.empty or len(close_pivot) < 20:
        return 50.0, {}

    norm = close_pivot / close_pivot.iloc[0] * 100
    index_close = norm.mean(axis=1).values

    # 构建 high/low 统一指数
    high_pivot = sub.pivot_table(
        index="trade_date", columns="ts_code", values="high", aggfunc="first"
    )
    low_pivot = sub.pivot_table(
        index="trade_date", columns="ts_code", values="low", aggfunc="first"
    )
    if not high_pivot.empty and not low_pivot.empty:
        high_norm = high_pivot / close_pivot.iloc[0] * 100
        low_norm = low_pivot / close_pivot.iloc[0] * 100
        index_high = high_norm.mean(axis=1).values
        index_low = low_norm.mean(axis=1).values
    else:
        index_high = index_close * 1.02
        index_low = index_close * 0.98

    # ---------- ① RSRS 趋势强度 (60%) ----------
    rsrs_score = _calc_rsrs(index_high, index_low, index_close)

    # ---------- ② 均线多头排列持续天数 (40%) ----------
    ma_score = _calc_ma_multi_day(index_close)

    raw = rsrs_score * 0.60 + ma_score * 0.40
    raw = 0.0 if np.isnan(raw) else raw
    score = float(np.clip(_amplify(raw / 100.0) * 100, 5, 98))

    detail = {
        "RSRS强度": round(rsrs_score, 1),
        "均线多头天数": round(ma_score, 1),
    }
    return score, detail


def _calc_rsrs(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
    """
    RSRS (阻力支撑相对强度)

    对近20日 (high - low) 和 (close - low) 做回归：
      (close - low) = beta * (high - low) + epsilon
    beta 衡量收盘价在 HL 区间中的位置。
    beta > 0.5 = 收盘靠近H = 支撑强于阻力 = 上涨趋势质量高
    beta < 0.5 = 收盘靠近L = 阻力强于支撑 = 趋势质量差
    """
    if len(close) < 30:
        return 50.0

    n = min(20, len(close) - 1)
    recent_h = high[-n:]
    recent_l = low[-n:]
    recent_c = close[-n:]

    range_arr = recent_h - recent_l
    body_arr = recent_c - recent_l

    valid = range_arr > 1e-9
    if valid.sum() < 5:
        return 50.0

    range_arr = range_arr[valid]
    body_arr = body_arr[valid]

    X = range_arr
    y = body_arr
    X_mean = X.mean()
    y_mean = y.mean()
    beta = np.sum((X - X_mean) * (y - y_mean)) / np.sum((X - X_mean) ** 2)

    # beta 映射到 0-100
    # beta = 0.5 → 50分, beta = 0.8 → 80分, beta = 0.2 → 20分
    raw_score = 50 + (beta - 0.5) * 100

    # RSRS 趋势改善加分
    if len(close) >= n + 5:
        earlier_h = high[-(n + 5):-5]
        earlier_l = low[-(n + 5):-5]
        earlier_c = close[-(n + 5):-5]
        erange = earlier_h - earlier_l
        ebody = earlier_c - earlier_l
        evalid = erange > 1e-9
        if evalid.sum() >= 5:
            erange = erange[evalid]
            ebody = ebody[evalid]
            eX_m = erange.mean()
            ey_m = ebody.mean()
            ebeta = np.sum((erange - eX_m) * (ebody - ey_m)) / np.sum((erange - eX_m) ** 2)
            if beta > ebeta:
                raw_score += 8
            else:
                raw_score -= 5

    return float(np.clip(raw_score, 5, 95))


def _calc_ma_multi_day(close: np.ndarray) -> float:
    """均线多头排列持续天数

    统计最近20个交易日中 MA5>MA10>MA20 的天数占比。
    持续天数越长 = 趋势越稳固。
    """
    if len(close) < 60:
        return 50.0

    close_s = pd.Series(close)
    ma5 = close_s.rolling(5, min_periods=5).mean()
    ma10 = close_s.rolling(10, min_periods=10).mean()
    ma20 = close_s.rolling(20, min_periods=20).mean()
    ma60 = close_s.rolling(60, min_periods=60).mean()

    multi_line = (ma5 > ma10) & (ma10 > ma20)

    recent = multi_line.iloc[-20:] if len(multi_line) >= 20 else multi_line
    ratio = recent.mean() if len(recent) > 0 else 0

    last5_all = multi_line.iloc[-5:].all() if len(multi_line) >= 5 else False

    if last5_all and ratio > 0.8:
        return 88.0
    elif ratio > 0.7:
        return 72.0
    elif ratio > 0.5:
        return 55.0
    elif ratio > 0.3:
        return 40.0
    elif ratio > 0.1:
        return 30.0
    else:
        bear_line = (ma5 < ma10) & (ma10 < ma20) & (ma20 < ma60)
        bear_ratio = bear_line.iloc[-20:].mean() if len(bear_line) >= 20 else 0.0
        if bear_ratio > 0.5:
            return 15.0
        else:
            return 35.0


# =========================================================================
# 4. 基础逻辑分 (15%)
#   行业高频催化 + 业绩预期因子
# =========================================================================

def _calc_fundamental(sub: pd.DataFrame, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, dict]:
    """基础逻辑分 (0-100)

    子维度:
      1. 涨停/龙虎榜催化 (40%) - 涨停数量、龙虎榜上榜
      2. 业绩预期 (30%) - 用涨跌幅分布推断业绩预期
      3. 行业政策/事件 (30%) - 用成交额异常放大推断事件驱动

    没有直接的数据源时，用行情数据的代理指标。
    """
    if latest.empty:
        return 50.0, {}

    # ---------- ① 涨停/龙虎榜催化 (40%) ----------
    pct = latest["pct_chg"].dropna()
    total = len(pct) if len(pct) > 0 else 1

    limit_up_count = (pct > 9.5).sum()
    big_up_count = (pct > 5).sum()

    catalyst_score = 50.0
    if limit_up_count >= 3:
        catalyst_score = 90.0
    elif limit_up_count >= 1:
        catalyst_score = 70.0
    elif big_up_count >= 3:
        catalyst_score = 60.0
    elif big_up_count >= 1:
        catalyst_score = 50.0
    else:
        catalyst_score = 30.0

    # ---------- ② 业绩预期 (30%) ----------
    # 用涨跌幅分布推断：涨幅>3%且成交额放大的股票比例
    amt = latest["amount"].dropna()
    if len(amt) > 0 and len(pct) > 0:
        amt_median = amt.median()
        # 量价齐升的股票比例
        good_signals = ((pct > 3) & (amt > amt_median)).sum()
        performance_ratio = good_signals / total
        performance_score = 30 + performance_ratio * 50
    else:
        performance_score = 50.0

    # ---------- ③ 行业政策/事件 (30%) ----------
    # 用成交额异常放大推断事件驱动
    amt_series = sub.groupby("trade_date")["amount"].sum().sort_index()
    if len(amt_series) >= 10:
        cur_amt = amt_series.iloc[-1]
        ma5_amt = amt_series.iloc[-5:].mean()
        if ma5_amt > 0:
            amt_ratio = cur_amt / ma5_amt
            if amt_ratio > 1.5:
                event_score = 80.0  # 放量50%+ = 事件催化
            elif amt_ratio > 1.2:
                event_score = 65.0
            elif amt_ratio > 0.9:
                event_score = 50.0
            elif amt_ratio > 0.7:
                event_score = 35.0
            else:
                event_score = 20.0
        else:
            event_score = 50.0
    else:
        event_score = 50.0

    raw = catalyst_score * 0.40 + performance_score * 0.30 + event_score * 0.30
    score = float(np.clip(raw, 5, 95))

    detail = {
        "催化得分": round(catalyst_score, 1),
        "业绩预期分": round(performance_score, 1),
        "事件驱动分": round(event_score, 1),
    }
    return score, detail


# =========================================================================
# 硬规则惩罚
# =========================================================================

def _rule_false_big_candle(score: float, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, list]:
    """规则1: 假大阳/拉指数惩罚

    若主题指数上涨，但成分股上涨比例 < 40%（个股普遍下跌），
    综合得分直接 -20 分。
    """
    if latest.empty or len(codes) < 5:
        return score, []

    pct = latest["pct_chg"].dropna()
    if len(pct) < 5:
        return score, []

    # 主题等权涨幅
    theme_mean = pct.mean()
    # 上涨比例
    up_ratio = (pct > 0).mean()

    if theme_mean > 0.5 and up_ratio < 0.40:
        penalty = -20
        reason = f"假大阳/拉指数: 主题涨{theme_mean:.1f}%, 但仅{up_ratio*100:.0f}%个股上涨"
        return score + penalty, [{"规则": "假大阳拉指数", "惩罚": penalty, "原因": reason}]

    return score, []


def _rule_dumbbell_fly(score: float, latest: pd.DataFrame, codes: List[str]) -> Tuple[float, list]:
    """规则2: 梯队哑铃化/龙头单飞惩罚

    若 Top1 龙头涨幅 > 8%，但后排跟风平均涨幅 < 0%（甚至为负），
    判定为"极度分歧/哑铃化"，综合得分直接 -25 分。
    """
    if latest.empty or len(codes) < 8:
        return score, []

    pct = latest["pct_chg"].dropna()
    if len(pct) < 8:
        return score, []

    sorted_pct = pct.sort_values(ascending=False)

    top1_ret = sorted_pct.iloc[0]
    # 后排：后50%的股票
    n_follower = max(1, len(sorted_pct) // 2)
    bottom_mean = sorted_pct.iloc[-n_follower:].mean()

    if top1_ret > 8 and bottom_mean < 0:
        gap = top1_ret - bottom_mean
        penalty = -25
        reason = (f"梯队哑铃化/龙头单飞: Top1涨{top1_ret:.1f}%, "
                  f"后排{n_follower}只跟风平均涨{bottom_mean:.1f}%, 差距{gap:.1f}%")
        return score + penalty, [{"规则": "哑铃化龙头单飞", "惩罚": penalty, "原因": reason}]

    return score, []


def _rule_backbone_veto(score: float, sub: pd.DataFrame, latest: pd.DataFrame,
                        codes: List[str]) -> Tuple[float, list, bool]:
    """规则3: 中军破位硬否决

    若市值前20%的中军股票中有 > 30% 跌破20日均线，
    强制扣除 15 分，且阶段判定强行降级为"退潮期"。
    """
    if latest.empty or len(codes) < 5:
        return score, [], False

    latest_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if latest_amt.empty:
        return score, [], False

    n_backbone = max(1, len(latest_amt) // 5)
    backbone_codes = latest_amt.head(n_backbone).index.tolist()

    if len(backbone_codes) < 2:
        return score, [], False

    breakdown_count = 0
    heavy_breakdown_count = 0
    for code in backbone_codes:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 20:
            continue
        last_close = stock["close"].iloc[-1]
        ma20 = stock["close"].iloc[-20:].mean()
        if last_close < ma20 * 0.98:
            breakdown_count += 1
            if last_close < ma20 * 0.95:
                heavy_breakdown_count += 1

    breakdown_ratio = breakdown_count / len(backbone_codes)
    heavy_ratio = heavy_breakdown_count / len(backbone_codes)

    if breakdown_ratio > 0.30:
        penalty = -15
        reason = (f"中军破位硬否决: {breakdown_ratio:.0%}中军跌破20日线 "
                  f"(严重{heavy_ratio:.0%}), 强制降级退潮期")
        return score + penalty, [{"规则": "中军破位硬否决", "惩罚": penalty, "原因": reason}], True

    return score, [], False


# =========================================================================
# 阶段判定
# =========================================================================

def _classify_stage(
    capital_score: float, echelon_score: float, trend_score: float,
    adjusted_score: float, raw_score: float,
    has_backbone_breakdown: bool,
    has_false_big_candle: bool,
    has_dumbbell: bool,
    penalty_count: int,
) -> str:
    """5阶段判定逻辑

    【启动期】/【主升期】/【分歧期】/【退潮期】/【震荡期】
    """
    # 退潮期（硬否决优先级最高）
    if has_backbone_breakdown:
        return "退潮期"

    # 退潮期（低分）
    if adjusted_score < 25:
        return "退潮期"

    # 分歧期
    if has_dumbbell or has_false_big_candle:
        return "分歧期"
    if penalty_count >= 2 and adjusted_score < 50:
        return "分歧期"

    # 主升期
    if (adjusted_score >= 65 and echelon_score >= 60
            and capital_score >= 55 and trend_score >= 50
            and not has_dumbbell and not has_false_big_candle):
        return "主升期"

    # 启动期
    if (adjusted_score >= 45 and capital_score >= 45
            and echelon_score >= 40):
        return "启动期"

    # 震荡期（默认）
    return "震荡期"


# =========================================================================
# 工具函数
# =========================================================================

def _amplify(pct: float) -> float:
    """非线性放大：让高分区分度更大"""
    return float(np.clip(np.power(np.clip(pct, 0, 1), 0.75), 0, 1))


# =========================================================================
# 使用示例
# =========================================================================

if __name__ == "__main__":
    # 构造示例数据
    N_STOCKS = 10
    N_DAYS = 100
    np.random.seed(42)

    dates = pd.date_range("2026-01-01", periods=N_DAYS, freq="B")
    date_strs = [d.strftime("%Y%m%d") for d in dates]

    rows = []
    for i in range(N_STOCKS):
        base_price = 10 + np.random.rand() * 50
        trend = np.random.randn(N_DAYS) * 0.5 + 0.1
        prices = base_price * np.exp(np.cumsum(trend) / 100)
        for t, dt in enumerate(date_strs):
            rows.append({
                "theme": "测试主题",
                "ts_code": f"T{i:04d}",
                "trade_date": dt,
                "close": prices[t],
                "pct_chg": np.random.randn() * 2,
                "amount": np.random.rand() * 1e8,
                "turnover_rate": np.random.rand() * 5,
                "circ_mv": 10 + np.random.rand() * 90,
                "high": prices[t] * 1.03,
                "low": prices[t] * 0.97,
            })

    df = pd.DataFrame(rows)
    print(f"示例数据: {len(df)} 行, {df['ts_code'].nunique()} 只股票, 主题: {df['theme'].unique()}")

    result = calculate_v7_theme_score(df)
    print("\nV7评分结果:")
    print(result.to_string(index=False))