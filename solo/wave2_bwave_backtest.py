# -*- coding: utf-8 -*-
"""
Wave2 B浪低点识别策略回测 (向量化加速版)

算法来源: d:/mystock/solo/bwave_strategy.py
接入框架: tdx_backtest

形态定义 (B浪):
  - 主板+双创 (INCLUDE_CHUANGCHUANG=True, CHUANGCHUANG_ONLY=False)
  - A浪: 20-60天涨幅>=60%, MA20上行, 站上MA20, 放量
  - B浪: 从A浪高点回调20-45%, 时长>=A浪*0.8, 缩量, ATR下降, 站上MA120*0.97, MA60上升
  - 启动信号: 从B浪低点反弹至38.2%黄金分割位

回测模式: T+1 开盘买入, 持有 N 天收盘卖出
涨停过滤: T+1 开盘价 >= 前收×涨停板×0.999 时跳过 (避免追高)

性能优化:
  - 每只股票只调用一次 detect_signals_vectorized, 一次性算出全部交易日信号
  - 用 numpy 向量化操作
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

TDX_BT_DIR = r"d:\mystock\tdx_backtest"
sys.path.insert(0, TDX_BT_DIR)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from indicators import MA, MACD, RSI


# =========================================================
# 算法常量 (B浪低点识别)
# =========================================================
AWAVE_LOOKBACK = 120
AWAVE_GAIN_MIN = 0.60
AWAVE_GAIN_MAX = 1.0  # 优化v3: A浪涨幅>100%过滤(胜率0%-50%)
AWAVE_DURATION_MIN = 20
AWAVE_DURATION_MAX = 60
AWAVE_MA20_UP_RATIO = 0.6
AWAVE_ABOVE_MA20_RATIO = 0.6
AWAVE_VOL_RATIO = 1.3

BWAVE_DROP_MIN = 0.20
BWAVE_DROP_MAX = 0.25  # 优化v4: 20-25%胜率66%均收+20%, 25-30%仅60%均收+9%
BWAVE_DURATION_RATIO = 0.8
BWAVE_MA120_FLOOR = 0.97
BWAVE_SCORE_MIN = 85
BWAVE_SCORE_MAX = 90

# 优化v2: 底背离检测常量
DIVERGENCE_DIF_UP_MIN_PCT = 15.0
DIVERGENCE_SIGNAL_MAX_DAYS = 30
DIVERGENCE_B_LOW_FLOOR = 0.93
DIVERGENCE_LOW_PRICE_MIN = 8.0
DIVERGENCE_VOL_SHRINK_MAX = 0.7

SEARCH_LOOKBACK_A = 120

INCLUDE_CHUANGCHUANG = True
CHUANGCHUANG_ONLY = True
EXCLUDE_KECHUANG = True  # 优化v4: 科创板胜率仅44%~51%,全线拖累,仅保留创业板


# =========================================================
# 主板/双创判定
# =========================================================
def is_main_board(ts_code: str) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if sym.startswith(("3", "688", "689")):
        return False
    if sym.startswith(("60", "00")):
        return True
    return False

def is_tradeable(ts_code: str, include_chuangchuang: bool = True,
                 chuangchuang_only: bool = False) -> bool:
    if not ts_code or "." not in ts_code:
        return False
    sym = ts_code.split(".")[0]
    if sym.startswith(("999", "8", "4")):
        return False
    if chuangchuang_only:
        return sym.startswith(("3", "688", "689"))
    if include_chuangchuang:
        if sym.startswith(("60", "00", "3", "688", "689")):
            return True
        return False
    else:
        return is_main_board(ts_code)


# =========================================================
# numpy 版 rolling 工具
# =========================================================
def _rolling_mean_np(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n:
        return np.full(len(arr), np.nan)
    ret = np.cumsum(arr, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    ret = ret / n
    ret[:n - 1] = np.nan
    return ret


# =========================================================
# 向量化信号生成
# =========================================================
def detect_signals_vectorized(df: pd.DataFrame) -> Tuple[np.ndarray, List[Dict]]:
    """一次性为整只股票的全部交易日计算 B浪 信号

    核心逻辑:
      1. 对每个交易日 i (i>=130), 在 [i-120, i-5] 范围内找 A浪 (局部低点→高点对)
      2. 从 A浪高点 a_end 到 i 找 B浪回调低点
      3. 检测启动信号 (反弹至38.2%黄金分割位)
      4. BWaveScore >= 85 时触发信号
    """
    n = len(df)
    signals = np.zeros(n, dtype=bool)
    infos: List[Dict] = [{} for _ in range(n)]
    if n < 130:
        return signals, infos

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["vol"].values.astype(float)

    # 预计算指标
    close_series = df["close"]
    ma5_series = MA(close_series, 5).values
    ma10_series = MA(close_series, 10).values
    ma20_series = MA(close_series, 20).values
    ma60_series = MA(close_series, 60).values
    ma120_series = MA(close_series, 120).values
    ma250_series = MA(close_series, 250).values
    rsi6_series = RSI(close_series, 6).values
    dif_series, dea_series, macd_series = MACD(close_series)
    dif_arr = dif_series.values
    dea_arr = dea_series.values
    macd_arr = macd_series.values

    # ATR(14)
    prev_close = np.roll(C, 1)
    prev_close[0] = C[0]
    tr = np.maximum(H - L,
                    np.maximum(np.abs(H - prev_close),
                               np.abs(L - prev_close)))
    atr_arr = _rolling_mean_np(tr, 14)

    for i in range(130, n):
        lookback_start = max(0, i - SEARCH_LOOKBACK_A)
        lookback_end = i - 5
        if lookback_end <= lookback_start:
            continue

        seg_len = lookback_end - lookback_start + 1
        seg_close = C[lookback_start:lookback_end + 1]
        seg_ma20 = ma20_series[lookback_start:lookback_end + 1]
        seg_vol = V[lookback_start:lookback_end + 1]

        # 找局部低点和局部高点
        low_mask = np.zeros(seg_len, dtype=bool)
        high_mask = np.zeros(seg_len, dtype=bool)
        for j in range(1, seg_len - 1):
            abs_idx = lookback_start + j
            if C[abs_idx] <= C[abs_idx - 1] and C[abs_idx] <= C[abs_idx + 1]:
                low_mask[j] = True
            if C[abs_idx] >= C[abs_idx - 1] and C[abs_idx] >= C[abs_idx + 1]:
                high_mask[j] = True

        low_indices = np.where(low_mask)[0]
        high_indices = np.where(high_mask)[0]
        if len(low_indices) == 0 or len(high_indices) == 0:
            continue

        low_abs = lookback_start + low_indices
        high_abs = lookback_start + high_indices

        # 找 best A浪
        best_awave = None
        for li in range(len(low_abs)):
            a_start = low_abs[li]
            if a_start < lookback_start:
                continue
            for hi in range(len(high_abs)):
                a_end = high_abs[hi]
                duration = a_end - a_start
                if duration < AWAVE_DURATION_MIN or duration > AWAVE_DURATION_MAX:
                    continue
                if a_end > i - 5:
                    continue

                start_price = C[a_start]
                end_price = C[a_end]
                if start_price <= 0:
                    continue
                gain = (end_price / start_price - 1)
                if gain < AWAVE_GAIN_MIN:
                    continue
                if gain > AWAVE_GAIN_MAX:  # 优化v3: A浪涨幅>100%过滤
                    continue

                ma20_seg = ma20_series[a_start:a_end + 1]
                ma20_up_count = np.sum(np.diff(ma20_seg) > 0)
                ma20_up_ratio = ma20_up_count / max(len(ma20_seg) - 1, 1)
                if ma20_up_ratio < AWAVE_MA20_UP_RATIO:
                    continue

                above_ma20 = np.sum(C[a_start:a_end + 1] > ma20_series[a_start:a_end + 1])
                above_ratio = above_ma20 / max(duration, 1)
                if above_ratio < AWAVE_ABOVE_MA20_RATIO:
                    continue

                a_vol = np.mean(V[a_start:a_end + 1])
                vol_40_start = max(0, a_start - 40)
                vol_40 = np.mean(V[vol_40_start:a_start]) if a_start > vol_40_start else a_vol
                vol_ratio_a = a_vol / vol_40 if vol_40 > 0 else 0
                if vol_ratio_a < AWAVE_VOL_RATIO:
                    continue

                a_score = 0
                if gain >= 0.80:
                    a_score += 40
                elif gain >= 0.60:
                    a_score += 25
                a_score += min(20, int(ma20_up_ratio * 20))
                a_score += min(20, int(above_ratio * 20))
                a_score += min(20, int(min(vol_ratio_a / 2, 1) * 20))

                if best_awave is None or a_score > best_awave["a_score"]:
                    best_awave = {
                        "start_idx": a_start,
                        "end_idx": a_end,
                        "start_price": start_price,
                        "end_price": end_price,
                        "gain": gain,
                        "duration": duration,
                        "ma20_up_ratio": ma20_up_ratio,
                        "above_ma20_ratio": above_ratio,
                        "vol_ratio": vol_ratio_a,
                        "avg_vol": a_vol,
                        "a_score": a_score,
                    }

        if best_awave is None:
            continue

        # B浪检测
        a_end = best_awave["end_idx"]
        a_high = best_awave["end_price"]
        a_duration = best_awave["duration"]
        a_avg_vol = best_awave["avg_vol"]
        a_vol_ratio = best_awave["vol_ratio"]

        search_end = min(a_end + a_duration * 2 + 10, n - 5)
        if i < a_end + int(a_duration * BWAVE_DURATION_RATIO):
            continue

        if a_vol_ratio > 2.0:
            vol_shrink_limit = 1.5
        elif a_vol_ratio > 1.5:
            vol_shrink_limit = 1.2
        else:
            vol_shrink_limit = 0.7

        b_best = None
        for b_low in range(a_end + int(a_duration * BWAVE_DURATION_RATIO), min(i, search_end) + 1):
            if b_low >= n:
                break

            seg_prices = C[a_end:b_low + 1]
            real_low_pos = int(np.argmin(seg_prices))
            real_low_idx = a_end + real_low_pos
            low_price = C[real_low_idx]

            drop = (a_high - low_price) / a_high
            if drop < BWAVE_DROP_MIN or drop > BWAVE_DROP_MAX:
                continue

            b_duration = real_low_idx - a_end
            if b_duration < a_duration * BWAVE_DURATION_RATIO:
                continue

            recent_10_vol = np.mean(V[max(real_low_idx - 9, a_end):real_low_idx + 1])
            vol_shrink = recent_10_vol / a_avg_vol if a_avg_vol > 0 else 0
            if vol_shrink > vol_shrink_limit:
                continue

            atr_start = atr_arr[a_end] if atr_arr[a_end] > 0 else 0
            atr_end_val = atr_arr[real_low_idx] if atr_arr[real_low_idx] > 0 else 0
            atr_drop_val = (atr_start - atr_end_val) / atr_start if atr_start > 0 else 0
            if atr_drop_val < 0:
                continue

            ma60_val = ma60_series[real_low_idx]
            ma120_val = ma120_series[real_low_idx]
            if ma120_val > 0 and low_price < ma120_val * BWAVE_MA120_FLOOR:
                continue

            ma60_30ago = ma60_series[max(0, real_low_idx - 30)]
            ma60_up = ma60_val > ma60_30ago if ma60_30ago > 0 else False
            if not ma60_up:
                continue

            time_ratio = b_duration / a_duration if a_duration > 0 else 0

            b_score = 0
            if 0.25 <= drop <= 0.35:
                b_score += 30
            elif 0.20 <= drop < 0.25 or 0.35 < drop <= 0.40:
                b_score += 20
            else:
                b_score += 10

            if 1.0 <= time_ratio <= 1.5:
                b_score += 25
            elif 0.8 <= time_ratio < 1.0 or 1.5 < time_ratio <= 2.0:
                b_score += 15
            else:
                b_score += 5

            if vol_shrink <= 0.5:
                b_score += 20
            elif vol_shrink <= 0.6:
                b_score += 15
            else:
                b_score += 10

            atr_drop_pct = atr_drop_val * 100
            if atr_drop_pct >= 30:
                b_score += 15
            elif atr_drop_pct >= 20:
                b_score += 10
            else:
                b_score += 5

            ma60_dist = (low_price / ma60_val - 1) * 100 if ma60_val > 0 else 0
            if ma60_dist > 0:
                b_score += 10

            if b_best is None or b_score > b_best["b_score"]:
                b_best = {
                    "start_idx": a_end,
                    "low_idx": real_low_idx,
                    "high_price": a_high,
                    "low_price": low_price,
                    "drop": drop,
                    "duration": b_duration,
                    "time_ratio": time_ratio,
                    "vol_shrink_ratio": vol_shrink,
                    "atr_drop_pct": atr_drop_pct,
                    "ma60_dist": ma60_dist,
                    "ma60_up": ma60_up,
                    "b_score": b_score,
                }

        if b_best is None or not (BWAVE_SCORE_MIN <= b_best["b_score"] < BWAVE_SCORE_MAX):
            continue

        # 启动信号检测 (简化版)
        low_idx = b_best["low_idx"]
        b_low_price = b_best["low_price"]
        b_high_price = b_best["high_price"]
        recovery_mid = b_low_price + (b_high_price - b_low_price) * 0.382

        scan_end = min(low_idx + 41, n)
        launch_idx = -1
        for la in range(scan_end - 1, low_idx - 1, -1):
            if C[la] >= recovery_mid:
                launch_idx = la
                break

        if launch_idx < 0:
            continue

        if launch_idx > i:
            continue

        # 优化v3: 启动信号缩量硬过滤 (vol_shrink>=0.7则跳过)
        if b_best["vol_shrink_ratio"] >= 0.7:
            continue

        signals[i] = True
        infos[i] = {
            "bwave_score": b_best["b_score"],
            "a_score": best_awave["a_score"],
            "a_gain_pct": round(best_awave["gain"] * 100, 1),
            "a_duration": best_awave["duration"],
            "a_vol_ratio": round(best_awave["vol_ratio"], 2),
            "b_drop_pct": round(b_best["drop"] * 100, 1),
            "b_duration": b_best["duration"],
            "b_time_ratio": round(b_best["time_ratio"], 2),
            "b_vol_shrink": round(b_best["vol_shrink_ratio"], 2),
            "b_atr_drop_pct": round(b_best["atr_drop_pct"], 1),
            "b_ma60_dist": round(b_best["ma60_dist"], 1),
            "launch_idx": launch_idx,
            "trigger": "BWAVE_SCORE85",
        }

    return signals, infos


# =========================================================
# 底背离信号检测 (优化v2)
# =========================================================
def detect_divergence_signals_vectorized(df: pd.DataFrame) -> Tuple[np.ndarray, List[Dict]]:
    """MACD底背离信号检测

    优化v2条件:
      - DIF抬高 >= 15%
      - RSI确认 (p2_rsi > p1_rsi) -- 必要条件
      - MACD绿柱缩短 -- 必要条件
      - 低价股过滤 (< 8元剔除)
      - 缩量要求 vol_shrink < 0.7
    """
    n = len(df)
    signals = np.zeros(n, dtype=bool)
    infos: List[Dict] = [{} for _ in range(n)]
    if n < 130:
        return signals, infos

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["vol"].values.astype(float)

    close_series = df["close"]
    ma5_series = MA(close_series, 5).values
    ma10_series = MA(close_series, 10).values
    ma20_series = MA(close_series, 20).values
    ma60_series = MA(close_series, 60).values
    ma120_series = MA(close_series, 120).values
    rsi6_series = RSI(close_series, 6).values
    dif_series, dea_series, macd_series = MACD(close_series)
    dif_arr = dif_series.values
    dea_arr = dea_series.values
    macd_arr = macd_series.values

    prev_close = np.roll(C, 1)
    prev_close[0] = C[0]
    tr = np.maximum(H - L,
                    np.maximum(np.abs(H - prev_close),
                               np.abs(L - prev_close)))
    atr_arr = _rolling_mean_np(tr, 14)

    for i in range(130, n):
        lookback_start = max(0, i - SEARCH_LOOKBACK_A)
        lookback_end = i - 5
        if lookback_end <= lookback_start:
            continue

        seg_len = lookback_end - lookback_start + 1
        seg_close = C[lookback_start:lookback_end + 1]
        seg_ma20 = ma20_series[lookback_start:lookback_end + 1]
        seg_vol = V[lookback_start:lookback_end + 1]

        low_mask = np.zeros(seg_len, dtype=bool)
        high_mask = np.zeros(seg_len, dtype=bool)
        for j in range(1, seg_len - 1):
            abs_idx = lookback_start + j
            if C[abs_idx] <= C[abs_idx - 1] and C[abs_idx] <= C[abs_idx + 1]:
                low_mask[j] = True
            if C[abs_idx] >= C[abs_idx - 1] and C[abs_idx] >= C[abs_idx + 1]:
                high_mask[j] = True

        low_indices = np.where(low_mask)[0]
        high_indices = np.where(high_mask)[0]
        if len(low_indices) == 0 or len(high_indices) == 0:
            continue

        low_abs = lookback_start + low_indices
        high_abs = lookback_start + high_indices

        best_awave = None
        for li in range(len(low_abs)):
            a_start = low_abs[li]
            if a_start < lookback_start:
                continue
            for hi in range(len(high_abs)):
                a_end = high_abs[hi]
                duration = a_end - a_start
                if duration < AWAVE_DURATION_MIN or duration > AWAVE_DURATION_MAX:
                    continue
                if a_end > i - 5:
                    continue

                start_price = C[a_start]
                end_price = C[a_end]
                if start_price <= 0:
                    continue
                gain = (end_price / start_price - 1)
                if gain < AWAVE_GAIN_MIN:
                    continue
                if gain > AWAVE_GAIN_MAX:  # 优化v3: A浪涨幅>100%过滤
                    continue

                ma20_seg = ma20_series[a_start:a_end + 1]
                ma20_up_count = np.sum(np.diff(ma20_seg) > 0)
                ma20_up_ratio = ma20_up_count / max(len(ma20_seg) - 1, 1)
                if ma20_up_ratio < AWAVE_MA20_UP_RATIO:
                    continue

                above_ma20 = np.sum(C[a_start:a_end + 1] > ma20_series[a_start:a_end + 1])
                above_ratio = above_ma20 / max(duration, 1)
                if above_ratio < AWAVE_ABOVE_MA20_RATIO:
                    continue

                a_vol = np.mean(V[a_start:a_end + 1])
                vol_40_start = max(0, a_start - 40)
                vol_40 = np.mean(V[vol_40_start:a_start]) if a_start > vol_40_start else a_vol
                vol_ratio_a = a_vol / vol_40 if vol_40 > 0 else 0
                if vol_ratio_a < AWAVE_VOL_RATIO:
                    continue

                a_score = 0
                if gain >= 0.80:
                    a_score += 40
                elif gain >= 0.60:
                    a_score += 25
                a_score += min(20, int(ma20_up_ratio * 20))
                a_score += min(20, int(above_ratio * 20))
                a_score += min(20, int(min(vol_ratio_a / 2, 1) * 20))

                if best_awave is None or a_score > best_awave["a_score"]:
                    best_awave = {
                        "start_idx": a_start,
                        "end_idx": a_end,
                        "start_price": start_price,
                        "end_price": end_price,
                        "gain": gain,
                        "duration": duration,
                        "ma20_up_ratio": ma20_up_ratio,
                        "above_ma20_ratio": above_ratio,
                        "vol_ratio": vol_ratio_a,
                        "avg_vol": a_vol,
                        "a_score": a_score,
                    }

        if best_awave is None:
            continue

        a_end = best_awave["end_idx"]
        a_high = best_awave["end_price"]
        a_duration = best_awave["duration"]
        a_avg_vol = best_awave["avg_vol"]

        search_end = min(a_end + a_duration * 2 + 10, n - 5)
        if i < a_end + int(a_duration * 0.5):  # 背离模式放宽: 0.8->0.5
            continue

        vol_shrink_limit = 1.5 if best_awave["vol_ratio"] > 2.0 else (1.2 if best_awave["vol_ratio"] > 1.5 else 0.7)

        b_best = None
        for b_low in range(a_end + int(a_duration * 0.5), min(i, search_end) + 1):
            if b_low >= n:
                break

            seg_prices = C[a_end:b_low + 1]
            real_low_pos = int(np.argmin(seg_prices))
            real_low_idx = a_end + real_low_pos
            low_price = C[real_low_idx]

            drop = (a_high - low_price) / a_high
            if drop < BWAVE_DROP_MIN or drop > BWAVE_DROP_MAX:
                continue

            b_duration = real_low_idx - a_end
            if b_duration < a_duration * 0.5:  # 背离模式放宽: 0.8->0.5
                continue

            recent_10_vol = np.mean(V[max(real_low_idx - 9, a_end):real_low_idx + 1])
            vol_shrink = recent_10_vol / a_avg_vol if a_avg_vol > 0 else 0
            if vol_shrink > vol_shrink_limit:
                continue

            atr_start = atr_arr[a_end] if atr_arr[a_end] > 0 else 0
            atr_end_val = atr_arr[real_low_idx] if atr_arr[real_low_idx] > 0 else 0
            atr_drop_val = (atr_start - atr_end_val) / atr_start if atr_start > 0 else 0
            if atr_drop_val < 0:
                continue

            ma60_val = ma60_series[real_low_idx]
            ma120_val = ma120_series[real_low_idx]
            if ma120_val > 0 and low_price < ma120_val * BWAVE_MA120_FLOOR:
                continue

            ma60_30ago = ma60_series[max(0, real_low_idx - 30)]
            ma60_up = ma60_val > ma60_30ago if ma60_30ago > 0 else False
            if not ma60_up:
                continue

            time_ratio = b_duration / a_duration if a_duration > 0 else 0

            b_score = 0
            if 0.25 <= drop <= 0.35:
                b_score += 30
            elif 0.20 <= drop < 0.25 or 0.35 < drop <= 0.40:
                b_score += 20
            else:
                b_score += 10

            if 1.0 <= time_ratio <= 1.5:
                b_score += 25
            elif 0.8 <= time_ratio < 1.0 or 1.5 < time_ratio <= 2.0:
                b_score += 15
            else:
                b_score += 5

            if vol_shrink <= 0.5:
                b_score += 20
            elif vol_shrink <= 0.6:
                b_score += 15
            else:
                b_score += 10

            atr_drop_pct = atr_drop_val * 100
            if atr_drop_pct >= 30:
                b_score += 15
            elif atr_drop_pct >= 20:
                b_score += 10
            else:
                b_score += 5

            ma60_dist = (low_price / ma60_val - 1) * 100 if ma60_val > 0 else 0
            if ma60_dist > 0:
                b_score += 10

            if b_best is None or b_score > b_best["b_score"]:
                b_best = {
                    "start_idx": a_end,
                    "low_idx": real_low_idx,
                    "high_price": a_high,
                    "low_price": low_price,
                    "drop": drop,
                    "duration": b_duration,
                    "time_ratio": time_ratio,
                    "vol_shrink_ratio": vol_shrink,
                    "atr_drop_pct": atr_drop_pct,
                    "ma60_dist": ma60_dist,
                    "b_score": b_score,
                }

        if b_best is None:
            continue

        b_low_price = b_best["low_price"]
        b_low_idx = b_best["low_idx"]

        # === 底背离检测 (优化v2) ===
        # 在B浪内找局部低点 (搜索范围: A浪结束到当前交易日)
        b_seg_start = a_end
        b_seg_end = i
        if b_seg_end <= b_seg_start:
            continue

        b_seg_len = b_seg_end - b_seg_start + 1
        b_low_mask = np.zeros(b_seg_len, dtype=bool)
        for j in range(1, b_seg_len - 1):
            abs_idx = b_seg_start + j
            if C[abs_idx] <= C[abs_idx - 1] and C[abs_idx] <= C[abs_idx + 1]:
                b_low_mask[j] = True

        b_low_indices = np.where(b_low_mask)[0]
        if len(b_low_indices) < 2:
            continue

        b_low_abs = b_seg_start + b_low_indices
        p2_abs = b_low_abs[-1]
        p1_abs = b_low_abs[-2]

        p1_close = C[p1_abs]
        p2_close = C[p2_abs]
        p1_dif = dif_arr[p1_abs]
        p2_dif = dif_arr[p2_abs]

        # 信号时效检查: p2在当前交易日30天内 OR 当前价格接近p2低点(5%以内)
        today_close = C[i]
        near_p2 = abs(today_close / p2_close - 1) <= 0.05
        if (i - p2_abs > DIVERGENCE_SIGNAL_MAX_DAYS) and (not near_p2):
            continue

        # 优化v2: 低价股过滤
        if p2_close < DIVERGENCE_LOW_PRICE_MIN:
            continue

        # 价格持平或更低
        if p2_close > p1_close * 1.005:
            continue

        # 优化v2: DIF抬高 >= 15%
        if p1_dif == 0:
            continue
        dif_up_pct = (p2_dif - p1_dif) / abs(p1_dif) * 100
        if dif_up_pct < DIVERGENCE_DIF_UP_MIN_PCT:
            continue

        # 背离低点不能远低于B浪低点
        if p2_close < b_low_price * DIVERGENCE_B_LOW_FLOOR:
            continue

        # 优化v2: RSI确认 -- 必要条件
        p1_rsi = rsi6_series[p1_abs]
        p2_rsi = rsi6_series[p2_abs]
        if p2_rsi <= p1_rsi:
            continue

        # 优化v2: MACD绿柱缩短或红柱增长 (择一)
        today_macd = macd_arr[i]
        prev_macd = macd_arr[i - 1] if i >= 1 else today_macd
        macd_shrinking = (today_macd < 0 and today_macd > prev_macd)
        macd_turning = (today_macd > 0 and today_macd > prev_macd)
        if not (macd_shrinking or macd_turning):
            continue

        # 优化v2: 缩量检查
        today_vol = V[i]
        avg_vol_20 = np.mean(V[max(0, i - 20):i]) if i >= 20 else today_vol
        vol_shrink_now = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1

        today_dif = dif_arr[i]
        today_dea = dea_arr[i]
        dif_recovery = (p2_dif / p1_dif - 1) * 100 if p1_dif > 0 else 0
        dist_to_a_high = (a_high / p2_close - 1) * 100 if p2_close > 0 else 0

        div_score = int(
            30 +
            min(20, int(dif_recovery)) +
            10 +  # RSI确认 (已为必要条件)
            10 +  # MACD绿柱缩短 (已为必要条件)
            (10 if today_dif > today_dea else 0) +
            (5 if vol_shrink_now < DIVERGENCE_VOL_SHRINK_MAX else 0) +
            (5 if dist_to_a_high < 10 else 0)
        )

        signals[i] = True
        infos[i] = {
            "divergence_score": div_score,
            "bwave_score": b_best["b_score"],
            "a_score": best_awave["a_score"],
            "a_gain_pct": round(best_awave["gain"] * 100, 1),
            "a_duration": best_awave["duration"],
            "b_drop_pct": round(b_best["drop"] * 100, 1),
            "b_duration": b_best["duration"],
            "b_vol_shrink": round(b_best["vol_shrink_ratio"], 2),
            "dif_up_pct": round(dif_up_pct, 1),
            "p2_close": round(p2_close, 2),
            "p1_close": round(p1_close, 2),
            "rsi_p2": round(p2_rsi, 1),
            "rsi_p1": round(p1_rsi, 1),
            "vol_shrink_now": round(vol_shrink_now, 2),
            "dist_to_a_high": round(dist_to_a_high, 1),
            "launch_idx": p2_abs,
            "trigger": "DIVERGENCE_V2",
        }

    return signals, infos


# =========================================================
# 回测引擎
# =========================================================
class Wave2BWaveBacktester:
    """Wave2 B浪低点识别回测 (向量化版)"""

    def __init__(self,
                 start_date: str = "20250101",
                 end_date: str = None,
                 max_stocks: Optional[int] = None,
                 lookback_days: int = 300,
                 pool_codes: Optional[List[str]] = None,
                 signal_mode: str = "launch"):
        from datetime import datetime
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.lookback_days = lookback_days
        self.pool_codes = set(pool_codes) if pool_codes else None
        self.signal_mode = signal_mode  # "launch" 或 "divergence"

        self.kline_dict: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._signal_cache: Dict[str, Tuple[np.ndarray, List[Dict]]] = {}
        self._load_all_klines_and_signals(max_stocks)

        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                   if self.start_date <= d <= self.end_date])
        pool_desc = f"{len(self.pool_codes)}只指定股池" if self.pool_codes else "全主板+双创"
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}, 股池: {pool_desc}", flush=True)

    def _load_all_klines_and_signals(self, max_stocks: Optional[int]):
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=self.lookback_days)).strftime("%Y%m%d")

        t0 = time.time()
        n_ok, n_skip, n_with_signal = 0, 0, 0
        for path in iter_all_day_files(markets=("SH", "SZ")):
            ts_code = tdx_filename_to_ts_code(path)
            if not ts_code:
                continue
            if not is_tradeable(ts_code, INCLUDE_CHUANGCHUANG, CHUANGCHUANG_ONLY):
                continue
            # 优化v4: 过滤科创板(688/689),仅保留创业板
            if EXCLUDE_KECHUANG and ts_code.split(".")[0].startswith(("688", "689")):
                continue
            if self.pool_codes is not None and ts_code not in self.pool_codes:
                continue
            if max_stocks and n_ok >= max_stocks:
                break
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 130:
                n_skip += 1
                continue

            sym = ts_code.split(".")[0]
            if sym.startswith(("3", "688", "689")):
                df["_zt_up"] = 1.198
            else:
                df["_zt_up"] = 1.098

            try:
                if self.signal_mode == "divergence":
                    signals, infos = detect_divergence_signals_vectorized(df)
                else:
                    signals, infos = detect_signals_vectorized(df)
            except Exception:
                n_skip += 1
                continue

            self.kline_dict[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            self._signal_cache[ts_code] = (signals, infos)
            n_ok += 1
            if signals.any():
                n_with_signal += 1

            if n_ok % 500 == 0:
                elapsed = time.time() - t0
                print(f"  [Loading] 已加载 {n_ok} 只 (含信号 {n_with_signal} 只), "
                      f"耗时 {elapsed:.1f}s", flush=True)

        elapsed = time.time() - t0
        print(f"[Load] 加载 {n_ok} 只 (含信号 {n_with_signal} 只), "
              f"跳过 {n_skip}, 耗时 {elapsed:.1f}s", flush=True)

    def run_single_day(self, trade_date: str) -> List[Tuple[str, Dict]]:
        selected = []
        for ts_code, (signals, infos) in self._signal_cache.items():
            if not signals.any():
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None or i >= len(signals):
                continue
            if signals[i]:
                selected.append((ts_code, infos[i]))
        return selected

    def evaluate_signals(self, selected: List[Tuple[str, Dict]],
                         trade_date: str, hold_days: int = 5) -> List[Dict]:
        records = []
        for ts_code, info in selected:
            df = self.kline_dict.get(ts_code)
            if df is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None:
                continue

            buy_idx = i + 1
            if buy_idx >= len(df):
                continue
            buy_row = df.iloc[buy_idx]
            prev_close = df.iloc[i]["close"]
            zt_up = buy_row["_zt_up"]
            if buy_row["open"] >= prev_close * zt_up * 0.999:
                continue

            buy_price = buy_row["open"]
            buy_date = buy_row["trade_date"]

            sell_idx = min(buy_idx + hold_days, len(df) - 1)
            sell_row = df.iloc[sell_idx]
            sell_price = sell_row["close"]
            sell_date = sell_row["trade_date"]

            ret = (sell_price / buy_price - 1) * 100
            records.append({
                "ts_code": ts_code,
                "signal_date": trade_date,
                "buy_date": buy_date,
                "buy_price": round(buy_price, 2),
                "sell_date": sell_date,
                "sell_price": round(sell_price, 2),
                "hold_days": sell_idx - buy_idx,
                "return": round(ret, 2),
                "trigger": info.get("trigger", ""),
                "bwave_score": info.get("bwave_score", 0),
                "a_score": info.get("a_score", 0),
                "a_gain_pct": info.get("a_gain_pct", 0),
                "a_duration": info.get("a_duration", 0),
                "a_vol_ratio": info.get("a_vol_ratio", 0),
                "b_drop_pct": info.get("b_drop_pct", 0),
                "b_duration": info.get("b_duration", 0),
                "b_time_ratio": info.get("b_time_ratio", 0),
                "b_vol_shrink": info.get("b_vol_shrink", 0),
                "b_atr_drop_pct": info.get("b_atr_drop_pct", 0),
                "b_ma60_dist": info.get("b_ma60_dist", 0),
                "divergence_score": info.get("divergence_score", 0),
                "dif_up_pct": info.get("dif_up_pct", 0),
                "p2_close": info.get("p2_close", 0),
                "rsi_p2": info.get("rsi_p2", 0),
                "rsi_p1": info.get("rsi_p1", 0),
                "vol_shrink_now": info.get("vol_shrink_now", 0),
                "dist_to_a_high": info.get("dist_to_a_high", 0),
            })
        return records

    def run_backtest(self, hold_days: int = 5,
                     top_n: Optional[int] = None,
                     verbose: bool = True) -> Dict:
        daily_counts = []
        all_returns = []
        trade_records = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td)

            if top_n and len(selected) > top_n:
                selected.sort(key=lambda x: -x[1].get("bwave_score", 0))
                selected = selected[:top_n]

            daily_counts.append(len(selected))

            if selected:
                records = self.evaluate_signals(selected, td, hold_days)
                for r in records:
                    all_returns.append(r["return"])
                    trade_records.append(r)

            if verbose and (i % 20 == 0 or i == len(self.trade_dates) - 1):
                elapsed = time.time() - t0
                eta = elapsed / (i + 1) * (len(self.trade_dates) - i - 1)
                print(f"  [{i+1}/{len(self.trade_dates)}] {td}: 选中 {len(selected)} 只, "
                      f"累计 {len(all_returns)} 笔, 耗时 {elapsed:.1f}s, ETA {eta:.0f}s", flush=True)

        all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
        win_rate = (all_returns_arr > 0).mean() * 100 if all_returns else 0
        avg_ret = all_returns_arr.mean() if all_returns else 0
        med_ret = np.median(all_returns_arr) if all_returns else 0

        daily_counts_arr = np.array(daily_counts)
        n_days_1_5 = int(((daily_counts_arr >= 1) & (daily_counts_arr <= 5)).sum())

        return {
            "daily_counts": daily_counts,
            "all_returns": all_returns,
            "trade_records": trade_records,
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(med_ret, 2),
            "n_signals": len(all_returns),
            "n_days_1_5": n_days_1_5,
            "n_total_days": len(self.trade_dates),
        }


def _load_pool_codes(pool_path: str) -> Optional[List[str]]:
    if not os.path.exists(pool_path):
        print(f"[Pool] 股池文件不存在: {pool_path}, 回退到全主板+双创", flush=True)
        return None

    try:
        df = pd.read_csv(pool_path)
        code_col = None
        for c in ("ts_code", "code", "股票代码", "symbol"):
            if c in df.columns:
                code_col = c
                break
        if code_col:
            codes = []
            for v in df[code_col].astype(str).tolist():
                v = v.strip()
                if not v or v == "nan":
                    continue
                if "." not in v:
                    v = f"{v}.SH" if v.startswith("6") else f"{v}.SZ"
                codes.append(v)
            if codes:
                print(f"[Pool] 从 {os.path.basename(pool_path)} 加载 {len(codes)} 只股票", flush=True)
                return codes
    except Exception as e:
        print(f"[Pool] CSV加载失败: {e}", flush=True)

    print(f"[Pool] 回退到全主板+双创", flush=True)
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Wave2 B浪低点识别回测 (向量化)")
    parser.add_argument("--start", type=str, default="20250101")
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--hold", type=int, default=20, help="持有天数(优化v4: 5->20, 中线最优)")
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--pool", type=str,
                        default=r"d:\mystock\solo\report_daily\bull_stocks_qualified.csv",
                        help="股票池 CSV (含 ts_code/code 列)")
    parser.add_argument("--signal-mode", type=str, default="launch",
                        choices=["launch", "divergence"],
                        help="信号模式: launch=启动信号, divergence=底背离信号")
    args = parser.parse_args()

    pool_codes = _load_pool_codes(args.pool)

    print("=" * 80)
    print("  Wave2 B浪低点识别回测 (主板+双创, T+1 开盘买入, 向量化)")
    print("=" * 80)
    print(f"  算法参数:")
    print(f"    A浪搜索范围: {SEARCH_LOOKBACK_A}天, 最小涨幅: {AWAVE_GAIN_MIN*100:.0f}%")
    print(f"    A浪时长: {AWAVE_DURATION_MIN}-{AWAVE_DURATION_MAX}天")
    print(f"    A浪MA20上涨占比: {AWAVE_MA20_UP_RATIO*100:.0f}%")
    print(f"    A浪站上MA20占比: {AWAVE_ABOVE_MA20_RATIO*100:.0f}%")
    print(f"    A浪量比: {AWAVE_VOL_RATIO}")
    print(f"    B浪回调: {BWAVE_DROP_MIN*100:.0f}%-{BWAVE_DROP_MAX*100:.0f}%")
    print(f"    B浪时长: >=A浪*{BWAVE_DURATION_RATIO}")
    print(f"    B浪低点 >= MA120*{BWAVE_MA120_FLOOR}")
    print(f"    入场硬过滤: BWaveScore >= {BWAVE_SCORE_MIN}, A浪涨幅<={AWAVE_GAIN_MAX*100:.0f}%, 缩量<0.7")
    print(f"    涨停板开盘跳过 (避免追高)")
    print(f"  股池文件: {args.pool}")
    print(f"  板块范围: 仅创业板 (EXCLUDE_KECHUANG={EXCLUDE_KECHUANG})")
    print("=" * 80, flush=True)

    bt = Wave2BWaveBacktester(
        start_date=args.start,
        end_date=args.end,
        max_stocks=args.max_stocks,
        pool_codes=pool_codes,
        signal_mode=args.signal_mode,
    )

    res = bt.run_backtest(hold_days=args.hold, top_n=args.top_n, verbose=True)

    print("\n" + "=" * 70)
    print("  回测结果 (T+1 开盘买入)")
    print("=" * 70)
    print(f"  回测区间:     {args.start} ~ {args.end or '最新'}")
    print(f"  交易日数:     {res['n_total_days']}")
    print(f"  持有天数:     {args.hold}")
    print(f"  总信号数:     {res['n_signals']}")
    print(f"  胜率:         {res['win_rate']}%")
    print(f"  平均收益:     {res['avg_return']}%")
    print(f"  中位收益:     {res['median_return']}%")
    if res['n_signals'] > 0:
        rets = np.array(res['all_returns'])
        print(f"  最大盈利:     {rets.max():.2f}%")
        print(f"  最大亏损:     {rets.min():.2f}%")
        pos = rets[rets > 0]
        neg = rets[rets < 0]
        if len(neg) > 0 and len(pos) > 0:
            print(f"  盈亏比:       {abs(pos.mean() / neg.mean()):.2f}")
        print(f"  日均选股数:   {np.mean(res['daily_counts']):.1f}")
        print(f"  选股1-5只天数: {res['n_days_1_5']}/{res['n_total_days']} "
              f"({res['n_days_1_5']/res['n_total_days']*100:.1f}%)")

    if res.get("trade_records"):
        recs = res["trade_records"]

        print("\n  BWaveScore 分档胜率:")
        for lo, hi in [(85, 90), (90, 95), (95, 999)]:
            sub = [r["return"] for r in recs if lo <= r["bwave_score"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                label = f"BWaveScore{lo}-{hi}" if hi < 999 else f"BWaveScore{lo}+"
                print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  B浪回调深度分档胜率:")
        for lo, hi in [(20, 25), (25, 30), (30, 35), (35, 40), (40, 45)]:
            sub = [r["return"] for r in recs if lo <= r["b_drop_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    回调{lo}-{hi}%: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  A浪涨幅分档胜率:")
        for lo, hi in [(60, 80), (80, 100), (100, 999)]:
            sub = [r["return"] for r in recs if lo <= r["a_gain_pct"] < hi]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                label = f"A浪涨幅{lo}-{hi}%" if hi < 999 else f"A浪涨幅{lo}%+"
                print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        print("\n  板块分档胜率:")
        for board, name in [("main", "主板"), ("gem", "创业板"), ("star", "科创板")]:
            if board == "main":
                sub = [r["return"] for r in recs
                       if not r["ts_code"].startswith(("3", "688", "689"))]
            elif board == "gem":
                sub = [r["return"] for r in recs if r["ts_code"].startswith("3")]
            else:
                sub = [r["return"] for r in recs
                       if r["ts_code"].startswith(("688", "689"))]
            if sub:
                wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                avg = np.mean(sub)
                print(f"    {name}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

        if args.signal_mode == "divergence":
            print("\n  底背离评分分档胜率:")
            for lo, hi in [(50, 60), (60, 70), (70, 80), (80, 999)]:
                sub = [r["return"] for r in recs if lo <= r.get("divergence_score", 0) < hi]
                if sub:
                    wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                    avg = np.mean(sub)
                    label = f"DivScore{lo}-{hi}" if hi < 999 else f"DivScore{lo}+"
                    print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

            print("\n  DIF抬高幅度分档胜率:")
            for lo, hi in [(15, 25), (25, 40), (40, 60), (60, 999)]:
                sub = [r["return"] for r in recs if lo <= r.get("dif_up_pct", 0) < hi]
                if sub:
                    wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                    avg = np.mean(sub)
                    label = f"DIF抬高{lo}%-{hi}%" if hi < 999 else f"DIF抬高{lo}%+"
                    print(f"    {label}: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

    if args.out:
        out_path = args.out
    else:
        if args.signal_mode == "divergence":
            out_path = r"d:\mystock\solo\tdx_backtest_bwave_divergence_trades.csv"
        else:
            out_path = r"d:\mystock\solo\tdx_backtest_bwave_trades.csv"

    if res.get("trade_records"):
        df_out = pd.DataFrame(res["trade_records"])
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  交易记录已保存: {out_path}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
