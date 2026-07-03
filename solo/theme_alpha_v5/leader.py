#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 龙头识别模块

综合评分（非唯涨幅论）：
 ① Relative Strength (30%)  — 近5日/10日相对收益
 ② 成交额 (25%)             — 近10日均成交额
 ③ 趋势 (20%)               — 均线多头排列
 ④ 连续强势 (15%)           — 连涨天数
 ⑤ 龙虎榜/机构加持 (10%)    — 机构席位买入
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
warnings.filterwarnings("ignore")


def identify_leader(daily: pd.DataFrame, theme_codes: list,
                    limit_df: pd.DataFrame = None) -> tuple:
    """返回 (leader_code, leader_score)"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty:
        return (None, 0)

    latest_day = sub["trade_date"].max()
    scores = {}
    top_set = set()
    if limit_df is not None and not limit_df.empty:
        col = "ts_code" if "ts_code" in limit_df.columns else ("symbol" if "symbol" in limit_df.columns else None)
        if col:
            top_set = set(limit_df[col].tolist())

    for code in theme_codes:
        sd = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(sd) < 10:
            continue
        closes = sd["close"].values
        amounts = sd["amount"].values
        pct = sd["pct_chg"].values

        # ① Relative Strength (30%)
        ret5 = (closes[-1] / closes[-6] - 1) if len(closes) > 5 else 0
        ret10 = (closes[-1] / closes[-11] - 1) if len(closes) > 10 else 0
        rs = np.clip((ret5 * 0.6 + ret10 * 0.4) * 200 + 50, 0, 100)

        # ② 成交额 (25%)
        avg_amt = np.mean(amounts[-10:]) / 1e8
        amt_score = np.clip(avg_amt * 3, 0, 100)

        # ③ 趋势 (20%)
        price_now = closes[-1]
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:])
        ma20 = np.mean(closes[-20:])
        trend = 40
        if price_now > ma5 > ma10 > ma20:
            trend = 100
        elif price_now > ma10 > ma20:
            trend = 75
        elif price_now > ma20:
            trend = 60

        # ④ 连续强势 (15%)
        up_streak = 0
        for i in range(len(pct) - 1, max(-1, len(pct) - 11), -1):
            if pct[i] > 0:
                up_streak += 1
            else:
                break
        streak_score = np.clip(up_streak * 10, 0, 100)

        # ⑤ 龙虎榜 (10%)
        top_bonus = 30 if code in top_set else 0

        total = rs * 0.30 + amt_score * 0.25 + trend * 0.20 + streak_score * 0.15 + top_bonus * 0.10
        scores[code] = total

    if not scores:
        return (None, 0)

    best = max(scores, key=scores.get)
    return (best, scores[best])


if __name__ == "__main__":
    print("[Leader] 龙头识别模块加载完成")
