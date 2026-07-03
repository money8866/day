#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 趋势评分模块

四个子维度：
 ① Relative Momentum (35%) — 多周期收益率在全主题中的百分位
 ② MA Breadth (20%)       — 站上MA5/10/20/60 的股票比例
 ③ Trend Persistence (25%) — 连续新高、EMA趋势、上涨天数
 ④ Drawdown Quality (20%)  — 最大回撤与恢复速度
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
warnings.filterwarnings("ignore")


def compute_trend_score(daily: pd.DataFrame, theme_codes: list,
                        all_theme_returns: dict = None) -> float:
    """返回 0-100 的趋势评分"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty or len(theme_codes) < 5:
        return 50.0

    # ===== ① Relative Momentum (35%) =====
    # 计算主题等权价格序列
    price_series = sub.groupby("trade_date")["close"].mean().sort_index()
    ret5 = (price_series.iloc[-1] / price_series.iloc[-6] - 1) if len(price_series) > 5 else 0
    ret10 = (price_series.iloc[-1] / price_series.iloc[-11] - 1) if len(price_series) > 10 else 0
    ret20 = (price_series.iloc[-1] / price_series.iloc[-21] - 1) if len(price_series) > 20 else 0
    ret40 = (price_series.iloc[-1] / price_series.iloc[-41] - 1) if len(price_series) > 40 else 0

    # 无全主题排名时，使用绝对值映射
    raw_momentum = (ret5 * 0.25 + ret10 * 0.30 + ret20 * 0.25 + ret40 * 0.20)
    momentum_score = np.clip(raw_momentum * 1500 + 50, 0, 100)  # 正收益线性映射到 50-100

    # ===== ② MA Breadth (20%) =====
    # 对每只股票检查站上各均线比例
    latest = sub[sub["trade_date"] == sub["trade_date"].max()].copy()
    if not latest.empty:
        stock_dates = sub.groupby("ts_code").last().reset_index()
        codes_in_sub = stock_dates["ts_code"].tolist()
        ma_scores = []
        for code in codes_in_sub:
            sd = sub[sub["ts_code"] == code].sort_values("trade_date")
            if len(sd) < 60:
                continue
            closes = sd["close"].values
            p = closes[-1]
            ma5 = np.mean(closes[-5:])
            ma10 = np.mean(closes[-10:])
            ma20 = np.mean(closes[-20:])
            ma60 = np.mean(closes[-60:])
            score = (p > ma5) + (p > ma10) + (p > ma20) + (p > ma60)
            ma_scores.append(score)
        if ma_scores:
            ma_breadth_pct = np.mean(ma_scores) / 4.0
            ma_breadth_score = ma_breadth_pct * 100
        else:
            ma_breadth_score = 50
    else:
        ma_breadth_score = 50

    # ===== ③ Trend Persistence (25%) =====
    ret_series = sub.groupby("trade_date")["pct_chg"].mean().sort_index()
    pct_arr = ret_series.values
    n = len(pct_arr)

    # 连续创新高天数
    cum_max = np.maximum.accumulate(price_series.values)
    new_high_count = np.sum(price_series.values[-20:] == cum_max[-20:])

    # EMA20 向上天数
    if n >= 1:
        ema20_val = price_series.values[-1]
        ref_val = price_series.values[-21] if n >= 21 else price_series.values[0]
        ema_up = 1 if ema20_val > ref_val else 0
    else:
        ema_up = 0

    # 过去20日上涨天数
    up20 = np.sum(pct_arr[-20:] > 0) if n >= 20 else np.sum(pct_arr > 0) * max(1, 20 // n)
    up_ratio = up20 / min(n, 20)

    persistence_score = (
        (new_high_count / 20) * 100 * 0.30 +
        ema_up * 100 * 0.30 +
        up_ratio * 100 * 0.40
    )

    # ===== ④ Drawdown Quality (20%) =====
    lookback = min(n, 40)
    if lookback >= 10:
        prices = price_series.values[-lookback:]
        running_max = np.maximum.accumulate(prices)
        drawdowns = (running_max - prices) / running_max
        max_dd = drawdowns.max()
        # 恢复速度：当前距最高点的距离
        recovery = 0
        if max_dd > 0:
            dd_valley = prices[np.argmax(drawdowns)]
            recovery = (prices[-1] - dd_valley) / (running_max[-1] - dd_valley) if running_max[-1] > dd_valley else 1
        dd_score = (1 - max_dd) * 70 + recovery * 30
        dd_score = np.clip(dd_score, 0, 100)
    else:
        dd_score = 50

    # ===== 合成 =====
    final = (momentum_score * 0.35 + ma_breadth_score * 0.20 +
             persistence_score * 0.25 + dd_score * 0.20)
    return float(np.clip(final, 0, 100))


if __name__ == "__main__":
    print("[Trend] 趋势评分模块加载完成")
