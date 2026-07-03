#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 风险评分模块

风险越高分越高（0-100）
包含：波动率、振幅、换手率、连续大涨、拥挤度
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
warnings.filterwarnings("ignore")


def compute_risk_score(daily: pd.DataFrame, theme_codes: list) -> float:
    """返回 0-100 风险评分（越高越危险）"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty:
        return 50.0

    # 主题等权收益序列
    ret_series = sub.groupby("trade_date")["pct_chg"].mean().sort_index()
    pct = ret_series.values

    # ===== ① 近10日波动率 (25%) =====
    if len(pct) >= 10:
        vol = np.std(pct[-10:]) * 100
        vol_score = np.clip(vol * 5, 0, 100)
    else:
        vol_score = 50

    # ===== ② 振幅 (20%) =====
    hi = sub.groupby("trade_date")["high"].mean()
    lo = sub.groupby("trade_date")["low"].mean()
    cl = sub.groupby("trade_date")["close"].mean()
    if len(hi) >= 10:
        amp = ((hi[-10:] - lo[-10:]) / cl[-10:]).mean() * 100
        amp_score = np.clip(amp * 6, 0, 100)
    else:
        amp_score = 50

    # ===== ③ 换手率 (20%) =====
    if "turnover_rate" in sub.columns:
        tr = sub.groupby("ts_code").last()["turnover_rate"].mean()
        tr_score = np.clip(tr * 3, 0, 100)
    else:
        tr_score = 50

    # ===== ④ 连续大涨 (20%) =====
    big_days = np.sum(pct[-10:] > 5) if len(pct) >= 10 else 0
    surge_score = np.clip(big_days * 15, 0, 100)

    # ===== ⑤ 拥挤度 (15%) =====
    # 使用成交量占比变化衡量拥挤
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    crowd = latest["amount"].sum() / (latest["amount"].sum() + 1e8)
    crowd_score = np.clip(crowd * 5, 0, 100)

    final = (vol_score * 0.25 + amp_score * 0.20 + tr_score * 0.20 +
             surge_score * 0.20 + crowd_score * 0.15)
    return float(np.clip(final, 0, 100))


if __name__ == "__main__":
    print("[Risk] 风险评分模块加载完成")
