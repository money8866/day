#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V8.0 主题生命周期节奏与高确定性中军交易指导系统

继承V7.2因子与惩罚架构，新增：
  1. 天数节奏模型 (T_start, T_MA, R_volume → D1-D8+)
  2. 高确定性趋势中军筛选与确定性得分
  3. 次日实盘指导卡生成

V8综合得分 = 0.35 * 资金交易弹性 + 0.30 * 梯队完整度 + 0.20 * 趋势爆发 + 0.15 * 基础逻辑

Author: Quant Director
Version: 8.0
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from v7_theme_scorer import calculate_v7_theme_score, _validate_input, _build_empty_result


# =========================================================================
# V8.0 综合评分入口 (继承V7.2 + 天数节奏模型 + 中军筛选 + 指导卡)
# =========================================================================

def calculate_v8_theme_score(df_theme_data: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, str]:
    """
    V8.0 主函数

    Parameters
    ----------
    df_theme_data : pd.DataFrame
        必须包含列: ts_code, trade_date, close, pct_chg, amount,
                    turnover_rate, circ_mv, high, low, theme
        可选列: net_money_flow, net_money_flow_main

    Returns
    -------
    Tuple[pd.DataFrame, pd.DataFrame, str]
        - v8_result   : 所有主题的V8评分 + 天数节奏模型结果
        - center_df   : Top主题的高确定性中军标的
        - trading_card: 次日实盘交易指导卡 Markdown
    """
    _validate_input(df_theme_data)

    # ---- Step 1: 继承V7.2评分 ----
    v7_result = calculate_v7_theme_score(df_theme_data)

    # ---- Step 2: 对每个主题添加天数节奏模型 ----
    theme_names = df_theme_data["theme"].unique()
    rhythm_records = {}

    for theme in theme_names:
        sub = df_theme_data[df_theme_data["theme"] == theme].copy()
        codes = sub["ts_code"].unique().tolist()

        if len(codes) < 5:
            rhythm_records[theme] = {
                "T_start": 0, "T_MA": 0, "R_volume": 0.0,
                "D阶段": "数据不足", "策略动作": "观望",
            }
            continue

        try:
            T_start = _calc_t_start(sub, codes)
            T_MA = _calc_t_ma(sub, codes)
            R_volume = _calc_r_volume(sub, codes)
            d_stage = _classify_d_stage(T_start, T_MA, R_volume, sub, codes)
            action = _get_d_stage_action(d_stage)

            rhythm_records[theme] = {
                "T_start": T_start,
                "T_MA": T_MA,
                "R_volume": round(R_volume, 2),
                "D阶段": d_stage,
                "策略动作": action,
            }
        except Exception as e:
            rhythm_records[theme] = {
                "T_start": 0, "T_MA": 0, "R_volume": 0.0,
                "D阶段": "异常", "策略动作": "观望",
            }

    rhythm_df = pd.DataFrame.from_dict(rhythm_records, orient="index")
    rhythm_df.index.name = "主题"
    rhythm_df = rhythm_df.reset_index()

    # 合并V7评分 + 节奏模型
    v8_result = v7_result.merge(rhythm_df, on="主题", how="left")

    # ---- Step 3: 高确定性中军筛选 (Ranked前10的启动/主升/D3期主题) ----
    center_records = []
    top_n = min(10, len(v8_result))
    for idx in range(top_n):
        row = v8_result.iloc[idx]
        theme = row["主题"]
        stage = row.get("D阶段", "")
        if stage in ("D1-D2", "D3", "D4-D5"):
            sub = df_theme_data[df_theme_data["theme"] == theme].copy()
            codes = sub["ts_code"].unique().tolist()
            centers = _calc_center_scores(sub, codes)
            for c in centers:
                c["主题"] = theme
                c["主题排名"] = idx + 1
                c["D阶段"] = stage
            center_records.extend(centers)

    center_df = pd.DataFrame(center_records) if center_records else pd.DataFrame()

    # ---- Step 4: 生成次日实盘交易指导卡 (排名第一的主题) ----
    trading_card = ""
    if len(v8_result) > 0:
        top_theme = v8_result.iloc[0]
        theme_centers = [
            c for c in center_records
            if c.get("主题") == top_theme["主题"]
        ]
        sub = df_theme_data[df_theme_data["theme"] == top_theme["主题"]].copy()
        codes = sub["ts_code"].unique().tolist()
        trading_card = generate_next_day_trading_card(
            top_theme.to_dict(), theme_centers, sub, codes
        )

    return v8_result, center_df, trading_card


# =========================================================================
# 一、天数节奏模型 (Theme Life Cycle Rhythm Engine)
# =========================================================================

def _calc_t_start(sub: pd.DataFrame, codes: List[str]) -> int:
    """
    主升爆发天数 T_start - V8.2 改进版

    主题成分股中涨幅 > 5% 的股票比例 > 15% 的交易日天数。
    - 如果今天激活：返回连续激活天数（同原版）
    - 如果今天未激活：回看近15天，返回最近一次激活期的峰值天数
    支持跟踪二波启动：即使中间有间隔，只要最近有过激活就不归零。
    """
    daily_pct = sub.groupby("trade_date")["pct_chg"].apply(
        lambda x: (x > 5).sum() / len(x) if len(x) > 0 else 0
    ).sort_index()

    if daily_pct.empty:
        return 0

    values = daily_pct.values

    # 模式1：今天激活 → 连续计数（原版行为）
    if values[-1] > 0.15:
        count = 0
        for v in values[::-1]:
            if v > 0.15:
                count += 1
            else:
                break
        return count

    # 模式2：今天未激活 → 回看最近15天，找最近一次激活期的峰值
    lookback = min(15, len(values))
    recent_values = values[-lookback:]

    current_streak = 0
    max_streak = 0
    for v in recent_values:
        if v > 0.15:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return max_streak


def _calc_t_ma(sub: pd.DataFrame, codes: List[str]) -> int:
    """
    中军均线多头天数 T_MA

    主题内 Top 3 权重股 (按自由流通市值 circ_mv) 的 5/10/20 日均线多头排列持续天数。
    取三者中持续天数最小值作为主题的 T_MA。
    """
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    top3 = latest.groupby("ts_code")["circ_mv"].first().nlargest(3).index.tolist()
    if len(top3) < 2:
        return 0

    all_days = []
    for code in top3:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 20:
            continue

        close = stock["close"].values
        close_s = pd.Series(close)
        ma5 = close_s.rolling(5, min_periods=5).mean().values
        ma10 = close_s.rolling(10, min_periods=10).mean().values
        ma20 = close_s.rolling(20, min_periods=20).mean().values

        count = 0
        for i in range(len(close) - 1, -1, -1):
            if i >= 19 and ma5[i] > ma10[i] > ma20[i]:
                count += 1
            else:
                break
        all_days.append(count)

    if not all_days:
        return 0

    return min(all_days)


def _calc_r_volume(sub: pd.DataFrame, codes: List[str]) -> float:
    """
    量比 R_volume

    今日主题总成交额 / 5日均成交额
    """
    daily_amount = sub.groupby("trade_date")["amount"].sum().sort_index()

    if len(daily_amount) < 5:
        return 1.0

    today_amt = daily_amount.iloc[-1]
    ma5_amt = daily_amount.iloc[-5:].mean()

    if ma5_amt <= 0:
        return 1.0

    return today_amt / ma5_amt


def _classify_d_stage(T_start: int, T_MA: int, R_volume: float,
                      sub: pd.DataFrame, codes: List[str]) -> str:
    """
    基于天数节奏指标判定 D1-D8+ 阶段 - V8.2 改进版

    新增:
      - 今日激活强度 > T_start 历史 → 二波启动/升级阶段
      - T_start>0 + 今日未激活 + 中军健康 → 回调蓄势（非潜伏期）

    判定优先级: D8+ > D6-D7 > 今日激活强度 > D4-D5 > D3 > 回调蓄势 > D1-D2 > 潜伏期
    """
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    pct = latest["pct_chg"].dropna()
    up_ratio = (pct > 0).mean() if len(pct) > 0 else 0
    gt3_ratio = (pct > 3).mean() if len(pct) > 0 else 0
    gt5_ratio = (pct > 5).mean() if len(pct) > 0 else 0
    limit_up_count = (pct > 9.5).sum() if len(pct) > 0 else 0

    # D8+: 中军跌破10日线或20日线
    if _check_backbone_break_ma(sub, latest, codes, ma_period=10):
        return "D8+"
    if _check_backbone_break_ma(sub, latest, codes, ma_period=20):
        return "D8+"

    # 放巨量检查
    is_high_volume = R_volume > 1.5
    zha_ban_signal = limit_up_count >= 2 and gt3_ratio < 0.30

    # D6-D7: 加速高潮/派发期
    if T_start >= 6 and (is_high_volume or zha_ban_signal):
        return "D6-D7"

    # === 新增：今日激活强度判定 ===
    # 如果今天极高激活(>50%成分股涨超5%)且有历史激活记录 → 直接认定为D4-D5主升加速
    if gt5_ratio > 0.50 and T_start > 0:
        return "D4-D5"
    # 如果今天高激活(>30%)且有历史激活记录 → 二波启动/D3
    if gt5_ratio > 0.30 and T_start > 0:
        return "D3"

    # === 新增：健康回调判定 ===
    # T_start>0但今天未激活 → 检查是否健康回调
    if T_start > 0 and gt5_ratio <= 0.15:
        backbone_healthy = _check_backbone_healthy(sub, latest, codes)
        if backbone_healthy and R_volume < 1.1:
            if T_start >= 4:
                return "D4-D5(回调蓄势)"
            elif T_start == 3:
                return "D3(回调休整)"
            else:
                return "D1-D2(回调)"
        elif backbone_healthy:
            if T_start >= 4:
                return "D4-D5(缩量回调)"
            else:
                return "D1-D2(缩量回调)"
        else:
            return "D8+"

    # D4-D5: 主升加速期
    if 4 <= T_start <= 5 and gt3_ratio > 0.50:
        return "D4-D5"

    # D3: 分歧首分日
    if T_start == 3:
        if _check_backbone_healthy(sub, latest, codes):
            return "D3"

    # D1-D2: 启动/发酵期
    if T_start <= 2 and up_ratio > 0.70:
        return "D1-D2"

    # 兜底: 根据T_start值推断
    if T_start == 0:
        return "潜伏期"
    elif T_start <= 2:
        return "D1-D2"
    elif T_start == 3:
        return "D3"
    elif T_start <= 5:
        return "D4-D5"
    elif T_start <= 7:
        return "D6-D7"
    else:
        return "D8+"


def _check_backbone_break_ma(sub: pd.DataFrame, latest: pd.DataFrame,
                              codes: List[str], ma_period: int = 10) -> bool:
    """
    检查中军是否跌破均线

    若 > 30% 的中军跌破指定均线，返回 True
    """
    if latest.empty or len(codes) < 5:
        return False

    latest_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if latest_amt.empty:
        return False

    n_backbone = max(1, len(latest_amt) // 5)
    backbone_codes = latest_amt.head(n_backbone).index.tolist()

    breakdown_count = 0
    for code in backbone_codes:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < ma_period + 1:
            continue
        last_close = stock["close"].iloc[-1]
        ma = stock["close"].iloc[-ma_period:].mean()
        if last_close < ma * 0.995:
            breakdown_count += 1

    if len(backbone_codes) == 0:
        return False

    return (breakdown_count / len(backbone_codes)) > 0.30


def _check_backbone_healthy(sub: pd.DataFrame, latest: pd.DataFrame,
                             codes: List[str]) -> bool:
    """
    检查中军是否健康 (用于D3判定)

    条件: 中军跌幅 < 2% 且未破 MA5
    """
    if latest.empty or len(codes) < 5:
        return False

    latest_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if latest_amt.empty:
        return False

    n_backbone = max(1, len(latest_amt) // 5)
    backbone_codes = latest_amt.head(n_backbone).index.tolist()

    healthy_count = 0
    for code in backbone_codes:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 6:
            continue
        last_pct = stock["pct_chg"].iloc[-1]
        last_close = stock["close"].iloc[-1]
        ma5 = stock["close"].iloc[-5:].mean()
        if not np.isnan(last_pct) and last_pct > -2 and last_close >= ma5 * 0.995:
            healthy_count += 1

    if len(backbone_codes) == 0:
        return False

    return (healthy_count / len(backbone_codes)) > 0.50


def _get_d_stage_action(d_stage: str) -> str:
    """根据天数阶段返回对应的策略动作"""
    action_map = {
        "D1-D2": "试错/轻仓买入",
        "D3": "买在首分低吸/加仓",
        "D4-D5": "持股锁仓/持有待涨",
        "D6-D7": "逢高落袋/分批减仓",
        "D8+": "清仓/回避",
        "潜伏期": "观望等待",
        "数据不足": "观望",
        "D4-D5(回调蓄势)": "等待二波启动/低吸",
        "D4-D5(缩量回调)": "等待缩量企稳/低吸",
        "D3(回调休整)": "观望/准备低吸",
        "D1-D2(回调)": "观望/准备低吸",
        "D1-D2(缩量回调)": "缩量企稳可低吸",
    }
    return action_map.get(d_stage, "观望")


# =========================================================================
# 二、高确定性趋势中军筛选与确定性得分 (Center Score)
# =========================================================================

def _calc_center_scores(sub: pd.DataFrame, codes: List[str]) -> List[dict]:
    """
    V8.1 高确定性中军筛选（参考顶级私募量化框架）

    硬门槛:
      1. 自由流通市值 > 100亿 (circ_mv > 1,000,000万元)
      2. 近20日均成交额 > 2亿 (流动性硬门槛)

    五维确定性得分:
      CenterScore = 0.25 * TrendQuality + 0.25 * LiquidityCapacity
                  + 0.20 * RelativeStrength + 0.15 * Stability + 0.15 * MarketStatus

    各维度说明:
      - TrendQuality (趋势质量): MA多头天数 + MA斜率 + 价格相对MA20位置
      - LiquidityCapacity (流动性容量): 日均成交额 + 自由流通市值 (300-1500亿最优)
      - RelativeStrength (相对强度): Beta_theme (0.8-1.2最优) + 个股超额收益
      - Stability (稳定性): 低换手率 + 低回撤 + 量能稳定性
      - MarketStatus (市场地位): 主题内市值排名 + 成交额排名
    """
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    # ---- 初筛: 市值 + 流动性 硬门槛 ----
    # 日均成交额（近20日）
    daily_amt = sub[["trade_date", "ts_code", "amount"]].drop_duplicates()
    daily_amt = daily_amt.groupby(["trade_date", "ts_code"])["amount"].sum().reset_index()
    avg_amt_20d = daily_amt.groupby("ts_code")["amount"].mean()

    circ_mv_sorted = latest.groupby("ts_code")["circ_mv"].first().sort_values(ascending=False)
    candidates = circ_mv_sorted[circ_mv_sorted > 1_000_000]       # > 100亿
    # amount 单位为千元, 2亿 = 200,000千元
    candidates = candidates[candidates.index.isin(
        avg_amt_20d[avg_amt_20d > 200_000].index                    # 日均成交额 > 2亿
    )]

    if len(candidates) < 2:
        # 放宽: 市值 > 50亿即可
        candidates = circ_mv_sorted[circ_mv_sorted > 500_000]
        candidates = candidates.head(5)

    if len(candidates) < 1:
        return []

    theme_index = _build_theme_index(sub, codes)
    results = []

    # 预计算主题内的排名基准 (用于MarketStatus)
    total_in_theme = len(circ_mv_sorted)

    for code in candidates.index.tolist():
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 20:
            continue

        # ---- 维度1: TrendQuality (趋势质量, 25%) ----
        ma_days = _calc_stock_ma_days(stock)
        trend_ma = _normalize_trend_ma(ma_days)       # 0-100

        ma_slope = _calc_ma_slope(stock)               # 0-100
        price_position = _calc_price_position(stock)    # 0-100

        trend_quality = 0.45 * trend_ma + 0.30 * ma_slope + 0.25 * price_position

        # ---- 维度2: LiquidityCapacity (流动性容量, 25%) ----
        mv_val = float(circ_mv_sorted.get(code, 0)) / 10000  # 亿
        raw_amt = float(avg_amt_20d.get(code, 0))
        # amount 单位为千元（tushare 标准），换算成亿: / 100000
        amt_val = raw_amt / 100000                            # 亿

        liq_mv = _normalize_market_cap(mv_val)         # 0-100
        liq_amt = _normalize_amount(amt_val)            # 0-100

        liquidity_capacity = 0.40 * liq_mv + 0.60 * liq_amt

        # ---- 维度3: RelativeStrength (相对强度, 20%) ----
        beta = _calc_beta_theme(stock, theme_index)
        beta_score = _normalize_beta(beta)              # 0-100 (0.8-1.2最优)

        excess_return = _calc_excess_return(stock, theme_index)
        excess_score = _normalize_excess_return(excess_return)

        relative_strength = 0.50 * beta_score + 0.50 * excess_score

        # ---- 维度4: Stability (稳定性, 15%) ----
        max_dd_30 = _calc_max_drawdown_n(stock, 30)    # 近30日最大回撤
        dd_score = max(0, 100.0 - max_dd_30 * 200)     # 回撤5%→90, 10%→80, 20%→60, 50%→0

        turnover_stability = _calc_turnover_stability(stock)
        vol_stability = _calc_volume_stability(stock)

        stability = 0.40 * dd_score + 0.30 * turnover_stability + 0.30 * vol_stability

        # ---- 维度5: MarketStatus (市场地位, 15%) ----
        mv_rank = circ_mv_sorted.index.get_loc(code) + 1 if code in circ_mv_sorted else total_in_theme
        rank_score = max(0, 100.0 - (mv_rank - 1) / max(1, total_in_theme) * 100)
        market_status = rank_score

        # ---- 综合 ----
        center_score = (0.25 * trend_quality + 0.25 * liquidity_capacity
                        + 0.20 * relative_strength + 0.15 * stability
                        + 0.15 * market_status)
        center_score = float(np.clip(center_score, 0, 100))

        # ---- 买卖参考价（沿用MA逻辑） ----
        close = stock["close"].values
        ma10 = np.mean(close[-10:]) if len(close) >= 10 else close[-1]
        ma5 = np.mean(close[-5:]) if len(close) >= 5 else close[-1]
        latest_close = close[-1] if len(close) > 0 else 0.0
        low_absorb_price = min(ma5, latest_close * 0.985)
        stop_loss_price = ma10

        results.append({
            "ts_code": code,
            "自由流通市值(亿)": round(mv_val, 1),
            "均线多头天数": ma_days,
            "趋势质量分": round(trend_quality, 1),
            "流动性容量分": round(liquidity_capacity, 1),
            "相对强度分": round(relative_strength, 1),
            "稳定性分": round(stability, 1),
            "市场地位分": round(market_status, 1),
            "Beta_theme": round(beta, 3),
            "近10日最大回撤%": round(_calc_max_drawdown_n(stock, 10) * 100, 1),
            "确定性得分": round(center_score, 1),
            "低吸参考价": round(low_absorb_price, 2),
            "防守止损位": round(stop_loss_price, 2),
        })

    results.sort(key=lambda x: x["确定性得分"], reverse=True)
    return results


def _build_theme_index(sub: pd.DataFrame, codes: List[str]) -> pd.Series:
    """构建主题等权指数 (收盘价归一化)"""
    close_pivot = sub.pivot_table(
        index="trade_date", columns="ts_code", values="close", aggfunc="first"
    )
    if close_pivot.empty or close_pivot.shape[1] < 3:
        return pd.Series(dtype=float)

    norm = close_pivot / close_pivot.iloc[0] * 100
    index_close = norm.mean(axis=1)
    index_close.index = index_close.index.astype(str)
    return index_close


def _calc_stock_ma_days(stock: pd.DataFrame) -> int:
    """
    计算个股均线多头排列持续天数

    从最新交易日向前追溯 MA5 > MA10 > MA20 的连续天数
    """
    close = stock["close"].values
    if len(close) < 20:
        return 0

    close_s = pd.Series(close)
    ma5 = close_s.rolling(5, min_periods=5).mean().values
    ma10 = close_s.rolling(10, min_periods=10).mean().values
    ma20 = close_s.rolling(20, min_periods=20).mean().values

    count = 0
    for i in range(len(close) - 1, -1, -1):
        if i >= 19 and ma5[i] > ma10[i] > ma20[i]:
            count += 1
        else:
            break
    return count


def _normalize_trend_ma(days: int) -> float:
    """
    均线多头天数归一化（非线性曲线，更符合中军特征）

    1天→5,  3天→25,  5天→55,  8天→80,  10天→90,  15天+→100
    早期快速加分(有就行)，中期稳定增长，长期饱和
    """
    if days <= 0:
        return 0.0
    if days >= 15:
        return 100.0
    # logistic 式曲线: 在 3-8 天区间加速, 之后趋缓
    return min(100.0, 100.0 / (1.0 + np.exp(-0.45 * (days - 5.0))))


def _calc_ma_slope(stock: pd.DataFrame) -> float:
    """
    计算近10日MA20斜率的强度 (0-100)

    用线性回归拟合近10日收盘价，斜率表示趋势强度
    """
    close = stock["close"].values
    n = min(20, len(close))
    if n < 5:
        return 50.0

    y = close[-n:]
    x = np.arange(n)
    slope = np.polyfit(x, y, 1)[0]
    # 斜率归一化: 以价格均值为基准
    base_price = np.mean(y)
    if base_price <= 0:
        return 50.0
    pct_slope = slope / base_price * 100  # 每日斜率百分比
    # 0%→50, 0.2%→60, 0.5%→75, 1%→90, 2%+→100
    score = 50.0 + pct_slope * 50.0
    return float(np.clip(score, 0, 100))


def _calc_price_position(stock: pd.DataFrame) -> float:
    """
    计算价格相对MA20的位置评分 (0-100)

    紧贴MA20上方(0-5%)最优→100分
    远离MA20(>20%)或跌破MA20(<0%)→扣分
    """
    close = stock["close"].values
    if len(close) < 20:
        return 50.0

    ma20 = np.mean(close[-20:])
    latest = close[-1]
    if ma20 <= 0:
        return 50.0

    deviation = (latest - ma20) / ma20 * 100
    # 0-5% 最优
    if 0 <= deviation <= 5:
        return 100.0
    elif -2 <= deviation < 0:
        return 70.0
    elif 5 < deviation <= 10:
        return 80.0
    elif 10 < deviation <= 20:
        return 60.0
    elif -5 <= deviation < -2:
        return 50.0
    else:
        return max(0, 30.0 - abs(deviation))


def _calc_beta_theme(stock: pd.DataFrame, theme_index: pd.Series) -> float:
    """
    计算个股对主题指数的 Beta (回归斜率)

    使用近20个交易日的日收益率做OLS回归
    """
    stock = stock.copy()
    stock["trade_date"] = stock["trade_date"].astype(str)
    stock = stock.set_index("trade_date")

    common_dates = stock.index.intersection(theme_index.index)
    if len(common_dates) < 10:
        return 1.0  # 数据不足时返回中性 Beta=1

    stock_ret = stock.loc[common_dates, "pct_chg"].iloc[-20:] / 100.0
    theme_close = theme_index.loc[common_dates].iloc[-20:]
    theme_ret = theme_close.pct_change()

    valid = ~(stock_ret.isna() | theme_ret.isna())
    stock_ret = stock_ret[valid].values
    theme_ret = theme_ret[valid].values

    if len(stock_ret) < 5 or np.std(theme_ret) < 1e-9:
        return 1.0  # 数据不足时返回中性 Beta=1

    cov = np.cov(stock_ret, theme_ret)[0, 1]
    var = np.var(theme_ret)

    if var < 1e-9:
        return 1.0  # 方差为0时返回中性 Beta=1

    beta = cov / var
    return float(beta)  # 返回原始 Beta（实际值如 1.39），由调用方用 _normalize_beta 归一化


def _calc_max_drawdown_n(stock: pd.DataFrame, n: int = 10) -> float:
    """
    计算近 N 个交易日的最大回撤

    MaxDD = (max - min) / max
    """
    close = stock["close"].values
    if len(close) < n:
        n = len(close)
    if n < 2:
        return 0.0

    recent = close[-n:]
    max_price = np.max(recent)
    min_price = np.min(recent)

    if max_price <= 0:
        return 0.0

    return (max_price - min_price) / max_price


def _normalize_market_cap(mv_yi: float) -> float:
    """
    自由流通市值归一化 (0-100)

    顶级私募视角: 300-1500亿为最优中军区间
    100亿→10, 200亿→50, 300亿→80, 800亿→100, 1500亿→85, 3000亿→60
    """
    if mv_yi <= 0:
        return 0.0
    if mv_yi >= 3000:
        return max(30.0, 100.0 - (mv_yi - 800) / 2200 * 70)
    # 峰值在 800 亿左右, 向两侧递减
    # 使用 half-normal 分布: peak at 800
    peak = 800.0
    sigma = 600.0
    score = 100.0 * np.exp(-0.5 * ((mv_yi - peak) / sigma) ** 2)
    return float(np.clip(score, 0, 100))


def _normalize_amount(amt_yi: float) -> float:
    """
    日均成交额归一化 (0-100)

    2亿→10, 5亿→40, 10亿→65, 20亿→85, 30亿+→100
    """
    if amt_yi <= 0:
        return 0.0
    # 对数增长: log10(amt_yi) 映射
    score = 100.0 * (np.log10(max(amt_yi, 0.5)) - np.log10(0.5)) / (np.log10(50) - np.log10(0.5))
    return float(np.clip(score, 0, 100))


def _normalize_beta(beta: float) -> float:
    """
    Beta 归一化 (0-100)

    0.8-1.2 最优区间→100分
    偏离越远分越低: <0.5 或 >2.0 大幅扣分
    """
    if beta <= 0.5 or beta >= 2.0:
        return max(0.0, 30.0 - abs(beta - 1.0) * 40.0)
    if 0.8 <= beta <= 1.2:
        return 100.0
    if beta < 0.8:
        return 50.0 + (beta - 0.5) / 0.3 * 50.0
    # beta > 1.2
    return 100.0 - (beta - 1.2) / 0.8 * 70.0


def _calc_excess_return(stock: pd.DataFrame, theme_index: pd.Series) -> float:
    """
    计算个股相对主题指数的超额收益 (近20日)
    """
    stock_copy = stock.copy()
    stock_copy["trade_date"] = stock_copy["trade_date"].astype(str)
    stock_copy = stock_copy.set_index("trade_date")

    common_dates = stock_copy.index.intersection(theme_index.index)
    if len(common_dates) < 5:
        return 0.0

    common = common_dates[-20:] if len(common_dates) > 20 else common_dates

    stock_ret = stock_copy.loc[common, "pct_chg"].mean()
    theme_close = theme_index.loc[common]
    theme_ret = theme_close.pct_change().dropna().mean() * 100

    if np.isnan(theme_ret):
        return float(stock_ret)

    return float(stock_ret - theme_ret)


def _normalize_excess_return(excess: float) -> float:
    """
    超额收益归一化 (0-100)

    0%→50(跟上主题), +5%→75, +10%→90, +20%+→100
    -5%→25, -10%→10, -20%→0
    """
    # sigmoid-like centered at 0
    score = 50.0 + excess * 4.0
    return float(np.clip(score, 0, 100))


def _calc_turnover_stability(stock: pd.DataFrame) -> float:
    """
    换手率稳定性评分 (0-100)

    中军特征: 换手率适中且稳定 (日均2-8%最佳, 变异系数小)
    """
    if "turnover_rate" not in stock.columns:
        return 50.0

    tr = stock["turnover_rate"].dropna().values[-20:]
    if len(tr) < 5:
        return 50.0

    mean_tr = np.mean(tr)
    std_tr = np.std(tr)

    # 日均换手率评分: 2-8% 最优
    if mean_tr < 1:
        tr_level = 30.0
    elif 2 <= mean_tr <= 8:
        tr_level = 100.0
    elif mean_tr > 20:
        tr_level = 20.0
    elif mean_tr > 8:
        tr_level = max(30.0, 100.0 - (mean_tr - 8) / 12 * 70)
    else:
        tr_level = 30.0 + (mean_tr - 1) / 1 * 70

    # 稳定性评分: CV越小越稳定
    cv = std_tr / max(mean_tr, 0.01)
    cv_score = max(0, 100.0 - cv * 50.0)

    return 0.5 * tr_level + 0.5 * cv_score


def _calc_volume_stability(stock: pd.DataFrame) -> float:
    """
    量能稳定性评分 (0-100)

    中军特征: 量能稳定放大, 不放巨量也不急剧萎缩
    """
    if "amount" not in stock.columns:
        return 50.0

    amt = stock["amount"].dropna().values[-20:]
    if len(amt) < 5:
        return 50.0

    mean_amt = np.mean(amt)
    std_amt = np.std(amt)

    if mean_amt <= 0:
        return 50.0

    # 量能变异系数: 越小越稳定
    cv = std_amt / mean_amt
    cv_score = max(0, 100.0 - cv * 40.0)

    # 量比合理性: R_volume 0.8-1.5 为健康
    today_amt = amt[-1]
    r_vol = today_amt / max(mean_amt, 0.01)
    if 0.8 <= r_vol <= 1.5:
        r_score = 100.0
    elif r_vol > 3.0:
        r_score = 30.0
    elif r_vol > 1.5:
        r_score = 70.0 - (r_vol - 1.5) / 1.5 * 40
    elif r_vol < 0.5:
        r_score = 40.0
    else:
        r_score = 70.0

    return 0.5 * cv_score + 0.5 * r_score


# =========================================================================
# 三、次日实盘指导卡生成
# =========================================================================

def generate_next_day_trading_card(
    theme_info: dict,
    center_stocks: List[dict],
    sub: pd.DataFrame,
    codes: List[str],
) -> str:
    """
    生成次日实盘交易指导卡 (Markdown 格式)

    Parameters
    ----------
    theme_info : dict
        主题的V8评分信息 (包含D阶段、T_start、T_MA、R_volume等)
    center_stocks : List[dict]
        高确定性中军标的列表
    sub : pd.DataFrame
        该主题的完整行情数据
    codes : List[str]
        该主题的成分股代码列表

    Returns
    -------
    str
        结构化 Markdown 交易指导卡
    """
    theme_name = theme_info.get("主题", "未知")
    v8_score = theme_info.get("V7综合得分", 0)
    d_stage = theme_info.get("D阶段", "未知")
    action = theme_info.get("策略动作", "观望")
    T_start = theme_info.get("T_start", 0)
    T_MA = theme_info.get("T_MA", 0)
    R_volume = theme_info.get("R_volume", 0.0)
    capital_score = theme_info.get("资金分", 0)
    echelon_score = theme_info.get("梯队分", 0)
    trend_score = theme_info.get("趋势分", 0)
    fundamental_score = theme_info.get("基础分", 0)
    penalty = theme_info.get("惩罚项说明", "")

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    pct = latest["pct_chg"].dropna()
    up_ratio = (pct > 0).mean() * 100 if len(pct) > 0 else 0
    gt3_ratio = (pct > 3).mean() * 100 if len(pct) > 0 else 0
    limit_up_count = (pct > 9.5).sum() if len(pct) > 0 else 0
    total_stocks = len(pct) if len(pct) > 0 else 0

    daily_amount = sub.groupby("trade_date")["amount"].sum().sort_index()
    today_amt = daily_amount.iloc[-1] if len(daily_amount) > 0 else 0
    total_amt_yi = round(today_amt / 1e8, 1)

    stage_descriptions = {
        "D1-D2": "启动/发酵期：题材刚被市场发掘，龙头率先涨停，跟风扩散率高，资金开始试错。",
        "D3": "分歧首分日：前排分化，后排掉队，但中军仍健康（跌幅<2%且未破MA5），是低吸加仓的黄金窗口。",
        "D4-D5": "主升加速期：梯队完整，跟风涨幅>3%比例超50%，中军均线多头排列，需要持股锁仓享受主升浪。",
        "D6-D7": "加速高潮/派发期：放巨量（R_volume>1.5）或炸板率上升，筹码开始松动，需逢高减仓。",
        "D8+": "衰退期/退潮期：中军跌破10日/20日线，趋势破位，应清仓回避。",
        "潜伏期": "潜伏期：主题尚未爆发，需等待启动信号。",
        "D4-D5(回调蓄势)": "主升后健康回调：之前有过强势主升（T_start>=4），缩量回调蓄势，等待二波启动信号。",
        "D4-D5(缩量回调)": "主升后缩量回调：量能收缩，中军健康，适合观察低吸机会。",
        "D3(回调休整)": "首分后回调休整：经历首分分歧后进入回调，等待企稳。",
        "D1-D2(回调)": "启动后回调：刚启动就遇回调，观察是否企稳。",
        "D1-D2(缩量回调)": "启动后缩量回调：缩量企稳可能是二次介入机会。",
    }
    stage_desc = stage_descriptions.get(d_stage, "")

    card = f"""# 📋 次日实盘交易指导卡

---

## 一、主题概览

| 项目 | 数值 |
|------|------|
| **主题名称** | {theme_name} |
| **V8综合得分** | {v8_score} |
| **天数阶段** | {d_stage} |
| **策略动作** | **{action}** |
| 成分股数量 | {total_stocks}只 |
| 今日上涨比例 | {up_ratio:.1f}% |
| 今日涨幅>3%比例 | {gt3_ratio:.1f}% |
| 今日涨停数 | {limit_up_count}只 |
| 今日主题总成交额 | {total_amt_yi}亿 |

## 二、天数节奏模型

| 指标 | 数值 |
|------|------|
| **T_start（主升爆发天数）** | {T_start}天 |
| **T_MA（中军均线多头天数）** | {T_MA}天 |
| **R_volume（量比）** | {R_volume} |
| **阶段判定** | **{d_stage}** |

> {stage_desc}

## 三、多因子评分明细

| 维度 | 得分 | 权重 |
|------|:----:|:----:|
| 资金交易弹性 | {capital_score} | 35% |
| 梯队完整度 | {echelon_score} | 30% |
| 趋势爆发 | {trend_score} | 20% |
| 基础逻辑 | {fundamental_score} | 15% |
"""

    if penalty:
        card += f"""
## 四、惩罚项说明

> ⚠️ {penalty}
"""

    if center_stocks:
        card += """
## 五、高确定性中军标的

| 标的 | 自由流通市值(亿) | 均线多头天数 | 趋势质量 | 流动性容量 | 相对强度 | 稳定性 | 市场地位 | 确定性得分 |
|:----:|:--------------:|:----------:|:-------:|:---------:|:-------:|:-----:|:-------:|:---------:|
"""
        for c in center_stocks:
            code = c.get("ts_code", "")
            mv = c.get("自由流通市值(亿)", 0)
            ma_days = c.get("均线多头天数", 0)
            tq = c.get("趋势质量分", 0)
            lq = c.get("流动性容量分", 0)
            rs = c.get("相对强度分", 0)
            stb = c.get("稳定性分", 0)
            mkt = c.get("市场地位分", 0)
            cs = c.get("确定性得分", 0)
            card += f"| {code} | {mv} | {ma_days} | {tq} | {lq} | {rs} | {stb} | {mkt} | {cs} |\n"

        card += """
## 六、次日定量买卖参考位

| 标的 | 低吸参考价 | 防守止损位(MA10) | 盈亏比参考 |
|:----:|:--------:|:--------------:|:--------:|
"""
        for c in center_stocks:
            code = c.get("ts_code", "")
            low_price = c.get("低吸参考价", 0)
            stop_price = c.get("防守止损位", 0)
            if stop_price > 0 and low_price > 0:
                profit_ratio = round((low_price - stop_price) / stop_price * 100, 1)
                card += f"| {code} | **{low_price}** | {stop_price} | {profit_ratio}% |\n"
            else:
                card += f"| {code} | **{low_price}** | {stop_price} | - |\n"

    else:
        card += """
## 五、高确定性中军标的

> 当前主题未满足中军筛选条件（自由流通市值Top 20%且>100亿），无高确定性中军标的。

"""

    card += f"""
---

*📅 生成日期: {latest_day}*
*⚠️ 本卡为量化模型输出，仅供参考，不构成投资建议。*
"""
    return card


# =========================================================================
# 测试入口
# =========================================================================

if __name__ == "__main__":
    # 构造示例数据 — 模拟多个主题在多个交易日的数据
    print("=" * 60)
    print("V8.0 主题生命周期节奏与高确定性中军交易指导系统")
    print("=" * 60)

    N_STOCKS = 15
    N_DAYS = 100
    np.random.seed(42)

    dates = pd.date_range("2026-01-01", periods=N_DAYS, freq="B")
    date_strs = [d.strftime("%Y%m%d") for d in dates]

    themes = ["煤炭链", "保险", "银行", "脑机接口", "医疗服务"]
    rows = []

    for theme_idx, theme_name in enumerate(themes):
        for i in range(N_STOCKS):
            base_price = 10 + np.random.rand() * 50
            if theme_name in ("煤炭链", "保险", "银行"):
                trend = np.random.randn(N_DAYS) * 0.4 + 0.25
                base_mv = 50 + np.random.rand() * 200
            elif theme_name in ("脑机接口",):
                trend = np.random.randn(N_DAYS) * 0.6 + 0.10
                base_mv = 10 + np.random.rand() * 50
            else:
                trend = np.random.randn(N_DAYS) * 0.5 + 0.15
                base_mv = 20 + np.random.rand() * 80

            prices = base_price * np.exp(np.cumsum(trend) / 100)
            for t, dt in enumerate(date_strs):
                pct = np.random.randn() * 2
                high = prices[t] * (1 + abs(np.random.randn()) * 0.02)
                low = prices[t] * (1 - abs(np.random.randn()) * 0.02)
                rows.append({
                    "theme": theme_name,
                    "ts_code": f"{theme_name[:2]}{i:04d}",
                    "trade_date": dt,
                    "close": prices[t],
                    "pct_chg": pct,
                    "amount": np.random.rand() * 5e8,
                    "turnover_rate": np.random.rand() * 5,
                    "circ_mv": base_mv + np.random.rand() * 50,
                    "high": high,
                    "low": low,
                })

    df = pd.DataFrame(rows)
    print(f"输入数据: {len(df)} 行, {df['ts_code'].nunique()} 只股票, {df['theme'].nunique()} 个主题")

    v8_result, center_df, trading_card = calculate_v8_theme_score(df)

    print("\n" + "=" * 60)
    print("V8.0 评分结果 (Top 10)")
    print("=" * 60)
    display_cols = ["排名", "主题", "V7综合得分", "D阶段", "策略动作",
                    "T_start", "T_MA", "R_volume", "资金分", "梯队分", "趋势分", "基础分"]
    display_cols = [c for c in display_cols if c in v8_result.columns]
    print(v8_result[display_cols].head(10).to_string(index=False))

    if not center_df.empty:
        print("\n" + "=" * 60)
        print("高确定性中军标的")
        print("=" * 60)
        center_cols = ["主题", "ts_code", "自由流通市值(亿)", "确定性得分",
                       "均线多头天数", "Beta_theme", "近10日最大回撤%",
                       "低吸参考价", "防守止损位"]
        center_cols = [c for c in center_cols if c in center_df.columns]
        print(center_df[center_cols].to_string(index=False))

    print("\n" + "=" * 60)
    print("次日实盘交易指导卡")
    print("=" * 60)
    print(trading_card)