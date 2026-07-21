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
    主升爆发天数 T_start

    主题成分股中涨幅 > 5% 的股票比例持续 > 15% 的连续交易日天数。
    从最新交易日向前追溯。
    """
    daily_pct = sub.groupby("trade_date")["pct_chg"].apply(
        lambda x: (x > 5).sum() / len(x) if len(x) > 0 else 0
    ).sort_index()

    if daily_pct.empty:
        return 0

    count = 0
    for pct in daily_pct.iloc[::-1]:
        if pct > 0.15:
            count += 1
        else:
            break

    return count


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
    基于天数节奏指标判定 D1-D8+ 阶段

    判定优先级: D8+ > D6-D7 > D4-D5 > D3 > D1-D2
    """
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    pct = latest["pct_chg"].dropna()
    up_ratio = (pct > 0).mean() if len(pct) > 0 else 0
    gt3_ratio = (pct > 3).mean() if len(pct) > 0 else 0
    limit_up_count = (pct > 9.5).sum() if len(pct) > 0 else 0

    # D8+: 中军跌破10日线或20日线
    if _check_backbone_break_ma(sub, latest, codes, ma_period=10):
        return "D8+"
    if _check_backbone_break_ma(sub, latest, codes, ma_period=20):
        return "D8+"

    # 放巨量检查: R_volume > 1.5
    is_high_volume = R_volume > 1.5

    # 炸板率近似: 涨停股数多但整体涨幅低
    if limit_up_count >= 2 and gt3_ratio < 0.30:
        zha_ban_signal = True
    else:
        zha_ban_signal = False

    # D6-D7: 加速高潮/派发期
    if T_start >= 6 and (is_high_volume or zha_ban_signal):
        return "D6-D7"

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
    }
    return action_map.get(d_stage, "观望")


# =========================================================================
# 二、高确定性趋势中军筛选与确定性得分 (Center Score)
# =========================================================================

def _calc_center_scores(sub: pd.DataFrame, codes: List[str]) -> List[dict]:
    """
    高确定性趋势中军筛选与确定性得分

    筛选条件:
      1. 主题内自由流通市值 Top 20%
      2. 绝对市值 > 100 亿 (circ_mv > 1,000,000 万元)

    确定性得分:
      CenterScore = 0.4 * 均线多头天数分 + 0.3 * Beta_theme + 0.3 * (1 - 近10日最大回撤)
    """
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    circ_mv_sorted = latest.groupby("ts_code")["circ_mv"].first().sort_values(ascending=False)

    # 筛选 Top 20% 且市值 > 100亿
    n_top = max(1, len(circ_mv_sorted) // 5)
    candidates = circ_mv_sorted.head(n_top)
    candidates = candidates[candidates > 1_000_000]

    # 如果不够2只，放宽到 Top 5 只
    if len(candidates) < 2:
        candidates = circ_mv_sorted.head(5)
        candidates = candidates[candidates > 500_000]

    if len(candidates) < 1:
        return []

    theme_index = _build_theme_index(sub, codes)

    results = []
    for code in candidates.index.tolist():
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 20:
            continue

        ma_days = _calc_stock_ma_days(stock)
        ma_score = _normalize_ma_days(ma_days)

        beta = _calc_beta_theme(stock, theme_index)

        max_dd = _calc_max_drawdown_10d(stock)

        center_score = 0.4 * ma_score + 0.3 * beta + 0.3 * (1.0 - max_dd)
        center_score = float(np.clip(center_score, 0, 100))

        close = stock["close"].values
        if len(close) >= 10:
            ma10 = np.mean(close[-10:])
        else:
            ma10 = close[-1] if len(close) > 0 else 0.0

        if len(close) >= 5:
            ma5 = np.mean(close[-5:])
        else:
            ma5 = close[-1] if len(close) > 0 else 0.0

        latest_close = close[-1] if len(close) > 0 else 0.0
        low_absorb_price = min(ma5, latest_close * 0.985)
        stop_loss_price = ma10

        results.append({
            "ts_code": code,
            "自由流通市值(亿)": round(float(circ_mv_sorted.get(code, 0)) / 10000, 1),
            "均线多头天数": ma_days,
            "均线多头天数分": round(ma_score, 1),
            "Beta_theme": round(beta, 3),
            "近10日最大回撤%": round(max_dd * 100, 1),
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


def _normalize_ma_days(days: int) -> float:
    """
    均线多头天数归一化到 0-100 分

    1天→10, 3天→30, 5天→50, 8天→80, 10天+→100
    """
    if days <= 0:
        return 0.0
    score = min(100.0, days * 10.0)
    return score


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
        return 50.0

    stock_ret = stock.loc[common_dates, "pct_chg"].iloc[-20:] / 100.0
    theme_close = theme_index.loc[common_dates].iloc[-20:]
    theme_ret = theme_close.pct_change()

    valid = ~(stock_ret.isna() | theme_ret.isna())
    stock_ret = stock_ret[valid].values
    theme_ret = theme_ret[valid].values

    if len(stock_ret) < 5 or np.std(theme_ret) < 1e-9:
        return 50.0

    cov = np.cov(stock_ret, theme_ret)[0, 1]
    var = np.var(theme_ret)

    if var < 1e-9:
        return 50.0

    beta = cov / var

    beta_score = 50.0 + (beta - 1.0) * 40.0
    return float(np.clip(beta_score, 0, 100))


def _calc_max_drawdown_10d(stock: pd.DataFrame) -> float:
    """
    计算近10个交易日的最大回撤

    MaxDD = (max - min) / max
    """
    close = stock["close"].values
    if len(close) < 10:
        return 0.0

    recent = close[-10:]
    max_price = np.max(recent)
    min_price = np.min(recent)

    if max_price <= 0:
        return 0.0

    return (max_price - min_price) / max_price


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

| 标的 | 自由流通市值(亿) | 均线多头天数 | 均线多头天数分 | Beta_theme | 近10日最大回撤% | 确定性得分 |
|:----:|:--------------:|:----------:|:------------:|:----------:|:-------------:|:---------:|
"""
        for c in center_stocks:
            code = c.get("ts_code", "")
            mv = c.get("自由流通市值(亿)", 0)
            ma_days = c.get("均线多头天数", 0)
            ma_score = c.get("均线多头天数分", 0)
            beta = c.get("Beta_theme", 0)
            max_dd = c.get("近10日最大回撤%", 0)
            cs = c.get("确定性得分", 0)
            card += f"| {code} | {mv} | {ma_days} | {ma_score} | {beta} | {max_dd} | {cs} |\n"

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