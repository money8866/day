# -*- coding: utf-8 -*-
"""
资金流层因子

9. etf_net_inflow - ETF净申购领先指标
10. mainflow_north - 主力净流入 + 北向资金
"""
import pandas as pd
import numpy as np


def calc_etf_net_inflow(etf_share_df: pd.DataFrame, etf_daily_df: pd.DataFrame,
                        theme_codes: list, moneyflow: pd.DataFrame) -> dict:
    """
    ETF净申购 + 主题成份股资金净流入

    逻辑：
    - ETF份额连续3日净申购 + 价格未大涨 → 资金埋伏，看涨概率70%+
    - ETF份额连续净赎回 → 资金撤离
    - 主题成份股主力净流入/成交额 > 5% → 强主力买入

    Returns:
        {"score": 0-100, "share_change_3d": float, "etf_price_change": float,
         "mainflow_ratio": float, "signal": str}
    """
    share_change_3d = 0
    etf_price_change = 0

    # ETF份额变动
    if etf_share_df is not None and not etf_share_df.empty:
        share_col = None
        for col in ["total_share", "trade_sh", "share"]:
            if col in etf_share_df.columns:
                share_col = col
                break
        if share_col and len(etf_share_df) >= 3:
            shares = etf_share_df.sort_values("trade_date")[share_col]
            share_change_3d = float((shares.iloc[-1] - shares.iloc[-3]) / shares.iloc[-3] * 100)

    # ETF价格变动
    if etf_daily_df is not None and not etf_daily_df.empty:
        close_col = "close" if "close" in etf_daily_df.columns else etf_daily_df.columns[-1]
        if len(etf_daily_df) >= 5:
            closes = etf_daily_df.sort_values("trade_date")[close_col]
            etf_price_change = float((closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100)

    # 主题成份股主力净流入
    mainflow_ratio = 0
    if moneyflow is not None and not moneyflow.empty and theme_codes:
        code_set = set(theme_codes)
        if "ts_code" in moneyflow.columns:
            theme_mf = moneyflow[moneyflow["ts_code"].isin(code_set)].copy()
            if not theme_mf.empty:
                # 找主力净流入列
                mf_col = None
                for col in ["net_mf_amount", "main_net_amt", "main_net_inflow"]:
                    if col in theme_mf.columns:
                        mf_col = col
                        break
                amt_col = "amount" if "amount" in theme_mf.columns else None

                if mf_col and amt_col:
                    total_mf = theme_mf[mf_col].sum()
                    total_amt = theme_mf[amt_col].sum()
                    mainflow_ratio = float(total_mf / total_amt * 100) if total_amt > 0 else 0

    score = 50
    signal = "中性"

    # ETF份额变动（领先指标）
    if share_change_3d > 2:
        score += 20
        if etf_price_change < 3:
            score += 10  # 份额增但价格未涨 → 资金埋伏
            signal = "ETF净申购·资金埋伏"
        else:
            signal = "ETF净申购"
    elif share_change_3d > 0.5:
        score += 10
    elif share_change_3d < -2:
        score -= 20
        signal = "ETF净赎回·资金撤离"
    elif share_change_3d < -0.5:
        score -= 10

    # 主力净流入
    if mainflow_ratio > 5:
        score += 20
        signal = "主力强买入"
    elif mainflow_ratio > 2:
        score += 10
    elif mainflow_ratio < -5:
        score -= 20
        signal = "主力大流出"
    elif mainflow_ratio < -2:
        score -= 10

    # ETF份额增 + 主力流入 共振
    if share_change_3d > 0.5 and mainflow_ratio > 2:
        score += 10
        signal = "ETF+主力共振·强看涨"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "share_change_3d": round(share_change_3d, 2),
            "etf_price_change": round(etf_price_change, 2),
            "mainflow_ratio": round(mainflow_ratio, 2), "signal": signal}


def calc_north_flow(north_hold: pd.DataFrame, theme_codes: list,
                    klines: dict = None) -> dict:
    """
    北向资金行业偏好

    逻辑：
    - 北向连续5日净买入 → 中线看涨概率65%+
    - 北向连续5日净卖出 → 中线看跌

    Returns:
        {"score": 0-100, "north_ratio": float, "north_change": float, "signal": str}
    """
    if north_hold is None or north_hold.empty or not theme_codes:
        return {"score": 50, "north_ratio": 0, "north_change": 0, "signal": "数据不足"}

    code_set = set(theme_codes)

    # 找持股比例列
    ratio_col = None
    for col in ["hold_ratio", "hold_ratio_n", "north_ratio"]:
        if col in north_hold.columns:
            ratio_col = col
            break

    if ratio_col is None:
        return {"score": 50, "north_ratio": 0, "north_change": 0, "signal": "数据不足"}

    if "ts_code" in north_hold.columns:
        theme_north = north_hold[north_hold["ts_code"].isin(code_set)].copy()
    else:
        return {"score": 50, "north_ratio": 0, "north_change": 0, "signal": "数据不足"}

    if theme_north.empty:
        return {"score": 50, "north_ratio": 0, "north_change": 0, "signal": "无北向数据"}

    avg_ratio = float(theme_north[ratio_col].mean())

    # 北向变化（如果有多日数据）
    north_change = 0
    if "trade_date" in theme_north.columns and ratio_col:
        by_date = theme_north.groupby("trade_date")[ratio_col].mean().sort_index()
        if len(by_date) >= 2:
            north_change = float(by_date.iloc[-1] - by_date.iloc[0])

    score = 50
    signal = "中性"

    # 北向持股比例
    if avg_ratio > 5:
        score += 15
    elif avg_ratio > 2:
        score += 8
    elif avg_ratio < 0.5:
        score -= 5

    # 北向变化趋势
    if north_change > 0.5:
        score += 20
        signal = "北向加仓·看涨"
    elif north_change > 0.1:
        score += 10
        signal = "北向小幅加仓"
    elif north_change < -0.5:
        score -= 20
        signal = "北向减仓·看跌"
    elif north_change < -0.1:
        score -= 10
        signal = "北向小幅减仓"

    score = max(0, min(100, score))
    return {"score": round(score, 1), "north_ratio": round(avg_ratio, 4),
            "north_change": round(north_change, 4), "signal": signal}


def calc_all_flow(etf_share_df: pd.DataFrame, etf_daily_df: pd.DataFrame,
                  theme_codes: list, moneyflow: pd.DataFrame,
                  north_hold: pd.DataFrame, klines: dict = None) -> dict:
    """计算资金流层全部因子"""
    return {
        "etf_net_inflow": calc_etf_net_inflow(etf_share_df, etf_daily_df, theme_codes, moneyflow),
        "north_flow": calc_north_flow(north_hold, theme_codes, klines),
    }
