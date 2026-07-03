#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 龙头识别模块

综合：RS(30%) + 成交额(25%) + 趋势(20%) + 连续强势(15%) + 龙虎榜(10%)
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")


def identify_leader(daily, codes, top_df=None, top_inst_df=None):
    """返回 (leader_code, leader_score)"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return (None, 0)

    top_set = set()
    inst_set = set()
    for df in [top_df, top_inst_df]:
        if df is not None and not df.empty:
            col = "ts_code" if "ts_code" in df.columns else None
            if col:
                top_set.update(df[col].tolist())

    scores = {}
    for code in codes:
        sd = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(sd) < 10:
            continue
        c = sd["close"].values
        a = sd["amount"].values
        p = sd["pct_chg"].values

        # ① RS (30%) — 放大系数 200→300，让强势龙头能拿高分
        r5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
        r10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
        rs = np.clip((r5 * 0.6 + r10 * 0.4) * 300 + 40, 0, 100)

        # ② 成交额 (25%) — 放大系数 3→5，让 20 亿龙头能拿满分
        avg_amt = np.mean(a[-10:]) / 1e8
        amt = np.clip(avg_amt * 5, 0, 100)

        # ③ 趋势 (20%)
        p_now = c[-1]
        ma5, ma10, ma20 = np.mean(c[-5:]), np.mean(c[-10:]), np.mean(c[-20:])
        trend = 40
        if p_now > ma5 > ma10 > ma20:
            trend = 100
        elif p_now > ma10 > ma20:
            trend = 75
        elif p_now > ma20:
            trend = 60

        # ④ 连续强势 (15%)
        streak = 0
        for i in range(len(p) - 1, max(-1, len(p) - 11), -1):
            if p[i] > 0:
                streak += 1
            else:
                break
        streak_s = np.clip(streak * 12, 0, 100)

        # ⑤ 龙虎榜 (10%)
        top_bonus = 30 if code in top_set else 0

        total = rs * 0.30 + amt * 0.25 + trend * 0.20 + streak_s * 0.15 + top_bonus * 0.10
        scores[code] = total

    if not scores:
        return (None, 0)
    best = max(scores, key=scores.get)
    return (best, scores[best])
