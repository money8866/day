#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
出场信号检测模块
================
提供多种出场信号的检测函数：
- 止损 (stop_loss)
- 跌破均线 (ma_break)
- MACD顶背离 (macd_divergence)
- 量价背离 (volume_divergence)
- 移动止损 (trailing_stop)
- 综合出场信号 (composite_exit_signal)

依赖: pandas, numpy
数据: 由 calc_all_indicators 生成的完整指标 DataFrame
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

LOG = logging.getLogger("timing_trading.exit_signals")


def _check_required_columns(df: pd.DataFrame, required: List[str]) -> bool:
    """检查 DataFrame 是否包含所有必需列"""
    missing = [c for c in required if c not in df.columns]
    if missing:
        LOG.warning("DataFrame 缺少列: %s", missing)
        return False
    return True


def _get_ma_value(df: pd.DataFrame, ma_name: str) -> float:
    """获取指定均线的值，ma_name 如 'ma5', 'ma20' 等"""
    if ma_name in df.columns:
        return float(df.iloc[-1][ma_name])
    # 尝试转换数字
    try:
        period = int(ma_name.replace("ma", ""))
        col = f"ma{period}"
        if col in df.columns:
            return float(df.iloc[-1][col])
    except (ValueError, TypeError):
        pass
    return 0.0


# ═════════════════════════════════════════════════════════════════
# 1. 止损信号
# ═════════════════════════════════════════════════════════════════


def detect_stop_loss(df: pd.DataFrame, entry_price: float, config: dict) -> dict:
    """检测固定止损信号

    条件:
        - 当前收盘价跌破 entry_price * (1 + stop_loss%)

    参数:
        df: 完整指标 DataFrame
        entry_price: 入场价格
        config: 配置字典

    返回:
        {"signal": bool, "score": float, "strength": str, "details": dict}
    """
    result = {"signal": False, "score": 0.0, "strength": "weak", "details": {}}

    if df.empty or entry_price <= 0:
        result["details"] = {"error": "数据为空或入场价格无效"}
        return result

    exit_cfg = config.get("exit", config)
    stop_loss_pct = exit_cfg.get("stop_loss", -8.0)

    current_price = float(df.iloc[-1].get("close", 0))
    # 止损价 = 入场价 * (1 + stop_loss_pct%)
    # stop_loss_pct 是负数，如 -8.0
    stop_price = entry_price * (1 + stop_loss_pct / 100.0)
    pnl_pct = (current_price / entry_price - 1) * 100

    details = {
        "entry_price": round(entry_price, 2),
        "current_price": round(current_price, 2),
        "stop_price": round(stop_price, 2),
        "stop_loss_pct": stop_loss_pct,
        "pnl_pct": round(float(pnl_pct), 2),
    }

    if current_price <= stop_price:
        result["signal"] = True
        result["score"] = 100.0  # 止损是硬性条件，满分触发
        result["strength"] = "strong"
        details["reason"] = f"跌破止损价 {stop_price:.2f} ({stop_loss_pct:+.1f}%)"

    result["details"] = details
    return result


# ═════════════════════════════════════════════════════════════════
# 2. 跌破均线信号
# ═════════════════════════════════════════════════════════════════


def detect_ma_break(df: pd.DataFrame, config: dict) -> dict:
    """检测跌破均线信号

    条件:
        - 当日收盘价跌破 config.exit.ma_break 指定的均线 (默认 ma20)
        - 且前一日收盘价在均线上方（确认跌破而非持续在下方）

    参数:
        df: 完整指标 DataFrame
        config: 配置字典

    返回:
        {"signal": bool, "score": float, "strength": str, "details": dict}
    """
    result = {"signal": False, "score": 0.0, "strength": "weak", "details": {}}
    required = ["trade_date", "close"]
    if not _check_required_columns(df, required):
        return result
    if len(df) < 3:
        result["details"] = {"error": "数据不足3根K线"}
        return result

    exit_cfg = config.get("exit", config)
    ma_break = exit_cfg.get("ma_break", "ma20")

    ma_col = ma_break if ma_break.startswith("ma") else f"ma{ma_break}"
    if ma_col not in df.columns:
        result["details"] = {"error": f"均线列 {ma_col} 不存在"}
        return result

    last = df.iloc[-1]
    prev = df.iloc[-2]

    current_close = float(last.get("close", 0))
    current_ma = float(last.get(ma_col, 0))
    prev_close = float(prev.get("close", 0))
    prev_ma = float(prev.get(ma_col, 0))

    details = {
        "ma_break": ma_break,
        "ma_value": round(current_ma, 2),
        "close": round(current_close, 2),
        "prev_close": round(prev_close, 2),
        "prev_ma": round(prev_ma, 2),
        "dist_ma": round((current_close / current_ma - 1) * 100, 2) if current_ma > 0 else 0,
    }

    # 跌破: 今日收盘在均线下，且昨日收盘在均线上（或紧贴均线）
    cond_break = current_close < current_ma and prev_close >= prev_ma * 0.995

    if cond_break:
        result["signal"] = True
        # 跌破程度评分: 刚跌破=85, 跌破很深反而可能超卖=60
        dist_pct = (1 - current_close / current_ma) * 100
        if dist_pct <= 1.0:
            score = 85.0
        elif dist_pct <= 3.0:
            score = 75.0
        else:
            score = 60.0
        result["score"] = score
        result["strength"] = "strong" if score >= 80 else "medium"
        details["reason"] = f"跌破{ma_break} ({current_ma:.2f})"
        details["break_distance_pct"] = round(float(dist_pct), 2)

    result["details"] = details
    return result


# ═════════════════════════════════════════════════════════════════
# 3. MACD顶背离信号
# ═════════════════════════════════════════════════════════════════


def detect_macd_divergence(df: pd.DataFrame) -> dict:
    """检测 MACD 顶背离信号

    条件:
        - 价格创近期新高（高于前一个高点）
        - 但 MACD 的 DIFF 或 DEA 未创新高
        - 通常用于判断上涨动能衰竭

    方法:
        比较最近两个波峰处的价格和 MACD 值

    返回:
        {"signal": bool, "score": float, "strength": str, "details": dict}
    """
    result = {"signal": False, "score": 0.0, "strength": "weak", "details": {}}
    required = ["trade_date", "close", "high", "macd_diff", "macd_dea"]
    if not _check_required_columns(df, required):
        return result
    if len(df) < 30:
        result["details"] = {"error": "数据不足30根K线"}
        return result

    # 取最近 60 根 K线分析
    lookback = min(60, len(df))
    sub = df.tail(lookback).reset_index(drop=True)
    highs = sub["high"].values
    macd_diff = sub["macd_diff"].values

    # 寻找最近的两个波峰
    # 从右向左找
    peaks_price = []
    peaks_macd = []
    n = len(sub)

    # 方法: 找显著高点（前后各有至少3根K线更低）
    for i in range(3, n - 3):
        left_ok = all(highs[i] >= highs[i - j] for j in range(1, 4))
        right_ok = all(highs[i] >= highs[i + j] for j in range(1, 4))
        if left_ok and right_ok:
            peaks_price.append((i, highs[i]))
            peaks_macd.append((i, macd_diff[i]))

    # 如果找不到足够波峰，放宽条件
    if len(peaks_price) < 2:
        # 尝试用 close 代替 high
        closes = sub["close"].values
        for i in range(3, n - 3):
            left_ok = all(closes[i] >= closes[i - j] for j in range(1, 4))
            right_ok = all(closes[i] >= closes[i + j] for j in range(1, 4))
            if left_ok and right_ok:
                peaks_price.append((i, closes[i]))
                peaks_macd.append((i, macd_diff[i]))

    if len(peaks_price) < 2:
        result["details"] = {"error": f"波峰不足: {len(peaks_price)}"}
        return result

    # 取最近两个波峰
    latest_peak = peaks_price[-1]
    prev_peak = peaks_price[-2]

    # 找到对应位置的 MACD 值
    latest_macd = None
    prev_macd = None
    for idx, val in peaks_macd:
        if idx == latest_peak[0]:
            latest_macd = val
        if idx == prev_peak[0]:
            prev_macd = val

    if latest_macd is None or prev_macd is None:
        # 按位置近似取
        latest_macd = macd_diff[latest_peak[0]]
        prev_macd = macd_diff[prev_peak[0]]

    # MACD 顶背离: 价格新高 + MACD 不创新高
    price_higher = latest_peak[1] > prev_peak[1]
    macd_lower = latest_macd < prev_macd

    details = {
        "latest_peak_price": round(float(latest_peak[1]), 2),
        "prev_peak_price": round(float(prev_peak[1]), 2),
        "latest_macd_diff": round(float(latest_macd), 4),
        "prev_macd_diff": round(float(prev_macd), 4),
        "price_higher": bool(price_higher),
        "macd_lower": bool(macd_lower),
    }

    is_divergence = price_higher and macd_lower

    if is_divergence:
        result["signal"] = True
        # MACD 顶背离评分：根据背离幅度
        price_rise_pct = (latest_peak[1] / prev_peak[1] - 1) * 100
        macd_diff_pct = (latest_macd - prev_macd) / abs(prev_macd) * 100 if prev_macd != 0 else -5
        # 幅度越大，分数越高
        score_price = min(price_rise_pct * 5, 40)  # 涨幅贡献最多40分
        score_macd = min(abs(macd_diff_pct), 40)  # MACD差异最多40分
        score = min(60.0 + score_price + score_macd, 100.0)
        result["score"] = round(score, 1)
        result["strength"] = "strong" if score >= 80 else "medium"
        details["reason"] = "MACD顶背离: 价格新高但MACD未创新高"
        details["price_rise_pct"] = round(float(price_rise_pct), 2)
        details["macd_diff_pct"] = round(float(macd_diff_pct), 2)

    result["details"] = details
    return result


# ═════════════════════════════════════════════════════════════════
# 4. 量价背离信号
# ═════════════════════════════════════════════════════════════════


def detect_volume_divergence(df: pd.DataFrame, config: dict) -> dict:
    """检测量价背离信号

    条件:
        - 连续 N 日 (默认 3 日) 价格上涨但成交量下降

    参数:
        df: 完整指标 DataFrame
        config: 配置字典

    返回:
        {"signal": bool, "score": float, "strength": str, "details": dict}
    """
    result = {"signal": False, "score": 0.0, "strength": "weak", "details": {}}
    required = ["trade_date", "close", "vol"]
    if not _check_required_columns(df, required):
        return result
    if len(df) < 5:
        result["details"] = {"error": "数据不足5根K线"}
        return result

    exit_cfg = config.get("exit", config)
    vd_cfg = exit_cfg.get("volume_divergence", {})
    lookback_days = int(vd_cfg.get("price_up_vol_down_days", 3))

    if len(df) < lookback_days + 2:
        result["details"] = {"error": f"数据不足{lookback_days + 2}根K线"}
        return result

    # 取最近 lookback_days 根 K 线
    recent = df.tail(lookback_days + 1).reset_index(drop=True)  # 多取一根用于计算变化
    closes = recent["close"].values
    vols = recent["vol"].values

    # 检查价格是否持续上涨: 每日收盘价依次递增
    price_up = all(closes[i] > closes[i - 1] for i in range(1, len(closes)))

    # 检查成交量是否持续下降
    vol_down = all(vols[i] < vols[i - 1] for i in range(1, len(vols)))

    # 计算量价相关系数（最近10日）
    corr_window = min(10, len(df))
    if corr_window >= 5:
        corr_sub = df.tail(corr_window)
        corr_coef = corr_sub["close"].corr(corr_sub["vol"])
    else:
        corr_coef = 0.0

    close_prices = [round(float(c), 2) for c in closes]
    vol_values = [round(float(v), 2) for v in vols]

    details = {
        "lookback_days": lookback_days,
        "price_up": bool(price_up),
        "vol_down": bool(vol_down),
        "close_series": close_prices,
        "vol_series": vol_values,
        "corr_coef": round(float(corr_coef), 4),
    }

    # 量价背离: 价格涨 + 量缩
    if price_up and vol_down:
        result["signal"] = True
        # 评分: 连续天数越多分越高
        base_score = 70.0
        bonus = min(lookback_days * 5.0, 20.0)  # 每增加一天 +5，最多+20
        score = min(base_score + bonus, 100.0)
        # 如果相关系数为负，加分
        if corr_coef < -0.3:
            score = min(score + 10.0, 100.0)
        result["score"] = round(score, 1)
        result["strength"] = "strong" if score >= 80 else "medium"
        details["reason"] = f"连续{lookback_days}日量价背离(价涨量缩)"

    result["details"] = details
    return result


# ═════════════════════════════════════════════════════════════════
# 5. 移动止损信号
# ═════════════════════════════════════════════════════════════════


def detect_trailing_stop(df: pd.DataFrame, highest_price: float, config: dict) -> dict:
    """检测移动止损信号

    条件:
        - 从最高点回落 trailing_stop% (默认 -12%)

    参数:
        df: 完整指标 DataFrame
        highest_price: 持仓期间最高价
        config: 配置字典

    返回:
        {"signal": bool, "score": float, "strength": str, "details": dict}
    """
    result = {"signal": False, "score": 0.0, "strength": "weak", "details": {}}

    if df.empty or highest_price <= 0:
        result["details"] = {"error": "数据为空或最高价无效"}
        return result

    exit_cfg = config.get("exit", config)
    trailing_stop_pct = exit_cfg.get("trailing_stop", -12.0)

    current_price = float(df.iloc[-1].get("close", 0))
    drawdown_pct = (current_price / highest_price - 1) * 100
    trail_price = highest_price * (1 + trailing_stop_pct / 100.0)

    details = {
        "highest_price": round(highest_price, 2),
        "current_price": round(current_price, 2),
        "trail_price": round(trail_price, 2),
        "trailing_stop_pct": trailing_stop_pct,
        "drawdown_pct": round(float(drawdown_pct), 2),
    }

    if current_price <= trail_price:
        result["signal"] = True
        # 评分: 根据回撤深度，越深越需要止损
        dd = abs(drawdown_pct)
        if dd <= abs(trailing_stop_pct) * 1.2:
            score = 80.0
        elif dd <= abs(trailing_stop_pct) * 1.5:
            score = 90.0
        else:
            score = 100.0
        result["score"] = score
        result["strength"] = "strong"
        details["reason"] = f"从最高点回落{drawdown_pct:.1f}%，触发移动止损"

    result["details"] = details
    return result


# ═════════════════════════════════════════════════════════════════
# 6. 综合出场信号
# ═════════════════════════════════════════════════════════════════


def composite_exit_signal(
    df: pd.DataFrame,
    entry_price: float,
    highest_price: float,
    config: dict,
) -> dict:
    """综合出场信号检测

    调用全部 5 种出场信号检测，任一触发则建议卖出。

    参数:
        df: 完整指标 DataFrame
        entry_price: 入场价格
        highest_price: 持仓期间最高价
        config: 配置字典

    返回:
        {
            "should_exit": bool,
            "reason": str,
            "primary_signal": str,
            "signals": dict,
            "score": float,
            "details": dict,
        }
    """
    result = {
        "should_exit": False,
        "reason": "",
        "primary_signal": "",
        "signals": {},
        "score": 0.0,
        "details": {},
    }

    if not _check_required_columns(df, [
        "trade_date", "close", "high", "low", "vol", "pct_chg",
        "ma5", "ma10", "ma20", "ma60",
        "macd_diff", "macd_dea", "macd_bar",
    ]):
        return result

    # ── 调用全部 5 种出场信号 ──
    signals = {}

    sl = detect_stop_loss(df, entry_price, config)
    signals["stop_loss"] = sl

    mb = detect_ma_break(df, config)
    signals["ma_break"] = mb

    md = detect_macd_divergence(df)
    signals["macd_divergence"] = md

    vd = detect_volume_divergence(df, config)
    signals["volume_divergence"] = vd

    ts = detect_trailing_stop(df, highest_price, config)
    signals["trailing_stop"] = ts

    # ── 判断是否出场 ──
    exit_signals = {k: v for k, v in signals.items() if v["signal"]}

    # 硬性出场: 止损或移动止损触发，立即出场
    hard_exit = any(k in ("stop_loss", "trailing_stop") and v["signal"]
                    for k, v in signals.items())

    soft_exit = any(k in ("ma_break", "macd_divergence", "volume_divergence") and v["signal"]
                    for k, v in signals.items())

    should_exit = hard_exit or soft_exit

    # ── 综合评分 ──
    if exit_signals:
        # 取最高信号分数
        max_score = max(v["score"] for v in exit_signals.values())
        # 硬性出场加分
        if hard_exit:
            max_score = max(max_score, 85.0)
        score = round(min(max_score, 100.0), 1)
    else:
        score = 0.0

    # ── 主因和理由 ──
    primary_signal = ""
    reason_parts = []

    signal_priority = ["stop_loss", "trailing_stop", "ma_break", "macd_divergence", "volume_divergence"]

    for sig_name in signal_priority:
        if sig_name in exit_signals:
            sig = exit_signals[sig_name]
            details = sig.get("details", {})
            reason = details.get("reason", f"触发{sig_name}")
            reason_parts.append(reason)
            if not primary_signal:
                primary_signal = sig_name

    reason = "; ".join(reason_parts) if reason_parts else "无出场信号"

    result["should_exit"] = should_exit
    result["reason"] = reason
    result["primary_signal"] = primary_signal
    result["signals"] = signals
    result["score"] = score
    result["details"] = {
        "exit_signal_count": len(exit_signals),
        "hard_exit": hard_exit,
        "soft_exit": soft_exit,
    }

    return result
