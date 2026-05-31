# -*- coding: utf-8 -*-
"""市场状态机 - 冰点/修复/主升/退潮"""
import pandas as pd
import numpy as np
from config import MARKET_STATE
from data_engine import fetch_index_daily, fetch_market_breadth, calc_ma

def _compute_ma_state(idx_df):
    """判断均线状态"""
    if len(idx_df) < 20:
        return {"idx_above_ma5": False, "idx_above_ma20": False,
                "idx_ma5_below_ma20": True}
    latest = idx_df.iloc[-1]
    return {
        "idx_above_ma5": latest['close'] >= latest.get('ma5', 0),
        "idx_above_ma20": latest['close'] >= latest.get('ma20', 0),
        "idx_ma5_below_ma20": latest.get('ma5', 0) < latest.get('ma20', 0),
    }

def _compute_volume_trend(idx_df, lookback=20):
    """量能趋势: 近5日均量 / 前20日均量"""
    if len(idx_df) < 25:
        return {"vol_ratio": 1.0, "vol_shrink": False, "vol_expand": False}
    recent = idx_df['vol'].tail(5).mean()
    prev = idx_df['vol'].iloc[-25:-5].mean()
    ratio = recent / max(prev, 1)
    return {
        "vol_ratio": ratio,
        "vol_shrink": ratio < MARKET_STATE["ice"]["vol_shrink"],
        "vol_expand": ratio > MARKET_STATE["bull"]["vol_expand"],
    }

def _compute_breadth_trend(breadth_series):
    """市场宽度趋势(连续变化)"""
    if len(breadth_series) < 3:
        return {"ad_declining": False, "breadth_trend": "stable"}
    recent3 = breadth_series.tail(3).tolist()
    if all(recent3[i] > recent3[i+1] for i in range(len(recent3)-1)):
        return {"ad_declining": True, "breadth_trend": "declining"}
    elif all(recent3[i] < recent3[i+1] for i in range(len(recent3)-1)):
        return {"ad_declining": False, "breadth_trend": "improving"}
    return {"ad_declining": False, "breadth_trend": "stable"}

def classify_market_state(trade_date=None):
    """
    判断当前市场状态
    返回: dict with state, score, metrics
    """
    idx_df = fetch_index_daily("000001.SH", start_date="20250101")
    if idx_df is None or len(idx_df) < 20:
        return {"state": "recover", "score": 50, "confidence": 0.3,
                "metrics": {}, "reason": "数据不足"}

    idx_df = calc_ma(idx_df, [5, 10, 20, 60])

    if trade_date:
        idx_df = idx_df[idx_df['trade_date'] <= pd.Timestamp(trade_date)]

    latest = idx_df.iloc[-1]
    ad_ratio = latest.get('ad_ratio', 0.5)

    # 如果没有实时涨跌数据,用指数日涨幅近似
    if 'ad_ratio' not in latest or pd.isna(latest['ad_ratio']):
        if len(idx_df) >= 2:
            pct = (idx_df['close'].iloc[-1] - idx_df['close'].iloc[-2]) / idx_df['close'].iloc[-2]
            ad_ratio = 0.5 + pct * 10  # 粗略映射
            ad_ratio = max(0.1, min(0.9, ad_ratio))
        else:
            ad_ratio = 0.5

    ma_state = _compute_ma_state(idx_df)
    vol_state = _compute_volume_trend(idx_df)

    metrics = {
        "ad_ratio": ad_ratio,
        "close": latest['close'],
        "pct_chg": (latest['close'] - idx_df['close'].iloc[-2]) / idx_df['close'].iloc[-2] * 100
                     if len(idx_df) >= 2 else 0,
        "vol_ratio": vol_state["vol_ratio"],
        "idx_above_ma5": ma_state["idx_above_ma5"],
        "idx_above_ma20": ma_state["idx_above_ma20"],
        "idx_ma5_below_ma20": ma_state["idx_ma5_below_ma20"],
    }

    # 状态判定逻辑 (优先级: 冰点 > 退潮 > 主升 > 修复)
    state = "recover"
    confidence = 0.5
    reason = ""

    # 冰点: 量缩+均线空头+涨跌比极低
    if (vol_state["vol_shrink"] and ma_state["idx_ma5_below_ma20"]
            and ad_ratio < MARKET_STATE["ice"]["ad_ratio"]):
        state = "ice"
        confidence = 0.8
        reason = f"量缩{vol_state['vol_ratio']:.1f}x + MA5<MA20 + 涨跌比{ad_ratio:.0%}"

    # 主升: 量增+均线多头+涨跌比高
    elif (vol_state["vol_expand"] and ma_state["idx_above_ma20"]
          and ad_ratio > MARKET_STATE["bull"]["ad_ratio"]):
        state = "bull"
        confidence = 0.8
        reason = f"量增{vol_state['vol_ratio']:.1f}x + MA5>MA20 + 涨跌比{ad_ratio:.0%}"

    # 退潮: 涨跌比下降+均线走弱
    elif (ma_state["idx_ma5_below_ma20"] or
          (ma_state["idx_above_ma5"] and not ma_state["idx_above_ma20"])):
        if ad_ratio < 0.40:
            state = "ebb"
            confidence = 0.7
            reason = f"均线走弱 + 涨跌比{ad_ratio:.0%}"

    # 修复
    else:
        if MARKET_STATE["recover"]["ad_ratio"][0] <= ad_ratio < MARKET_STATE["recover"]["ad_ratio"][1]:
            confidence = 0.6
            reason = f"涨跌比{ad_ratio:.0%} 处于修复区间"

    return {
        "state": state,
        "score": _state_to_score(state),
        "confidence": confidence,
        "metrics": metrics,
        "reason": reason,
        "date": str(latest['trade_date'].date()) if 'trade_date' in latest else "",
    }

def _state_to_score(state):
    """市场状态评分 0-100"""
    scores = {"ice": 15, "recover": 45, "bull": 85, "ebb": 30}
    return scores.get(state, 50)

def get_state_display(state):
    names = {"ice": "🥶 冰点", "recover": "🔧 修复", "bull": "🚀 主升", "ebb": "🌊 退潮"}
    return names.get(state, state)
