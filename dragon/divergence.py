# -*- coding: utf-8 -*-
"""分歧预警系统 - 识别退潮风险"""
import pandas as pd
from config import DIVERGENCE
from data_engine import fetch_index_daily, calc_ma

def check_divergence(market_state_result, sector_result=None, dragon_df=None):
    """
    综合分歧检测
    返回: dict with risk_level (0-3), warnings[], signals[]
    """
    warnings = []
    signals = []
    risk_score = 0

    metrics = market_state_result.get("metrics", {})
    state = market_state_result.get("state", "recover")

    # 1. 市场状态本身的风险
    if state == "ebb":
        risk_score += 2
        warnings.append("⚠️ 市场已进入退潮状态")
    elif state == "ice":
        risk_score += 3
        warnings.append("🥶 市场冰点，全面防御")

    # 2. 指数偏离MA5过高 (短期过热)
    idx_df = fetch_index_daily("000001.SH", start_date="20250101")
    if idx_df is not None and len(idx_df) >= 10:
        idx_df = calc_ma(idx_df, [5, 10, 20])
        latest = idx_df.iloc[-1]
        if latest.get('ma5', 0) > 0:
            dev_pct = (latest['close'] - latest['ma5']) / latest['ma5']
            if abs(dev_pct) > DIVERGENCE["idx_above_ma5_pct"]:
                risk_score += 1
                direction = "超涨" if dev_pct > 0 else "超跌"
                warnings.append(f"📊 指数偏离MA5 {dev_pct:.1%}，{direction}风险")

        # 3. 量能连续萎缩检测
        if len(idx_df) >= 10:
            recent_vol = idx_df['vol'].tail(5).mean()
            prev_vol = idx_df['vol'].iloc[-10:-5].mean()
            if prev_vol > 0 and recent_vol / prev_vol < 0.7:
                risk_score += 1
                warnings.append("📉 量能连续萎缩，市场动能下降")

    # 4. 板块强度变化 (如果提供了历史比较)
    if sector_result and dragon_df is not None:
        # 检查龙头股RSI超买
        rsi_col = 'rsi' if 'rsi' in dragon_df.columns else None
        if rsi_col and len(dragon_df) > 0:
            overbought = dragon_df[dragon_df[rsi_col] > DIVERGENCE["rsi_overbought"]]
            if len(overbought) > 0:
                risk_score += 1
                names = ", ".join(overbought['name'].head(3).tolist())
                signals.append(f"🔥 {names} RSI>{DIVERGENCE['rsi_overbought']}，短期过热")

    # 5. 退潮信号综合
    ad_ratio = metrics.get("ad_ratio", 0.5)
    if ad_ratio < 0.3:
        risk_score += 1
        signals.append(f"🔻 涨跌比仅{ad_ratio:.0%}，市场亏钱效应明显")

    # 风险等级
    if risk_score >= 4:
        risk_level = 3  # 高危
    elif risk_score >= 3:
        risk_level = 2  # 中等
    elif risk_score >= 1:
        risk_level = 1  # 轻微
    else:
        risk_level = 0  # 安全

    level_names = {0: "✅ 安全", 1: "🟡 轻微分歧", 2: "🟠 中度分歧", 3: "🔴 高度分歧"}
    level_actions = {
        0: "正常操作",
        1: "控制仓位，不追高",
        2: "减仓至半仓以下",
        3: "降至冰点仓位，防守为主",
    }

    return {
        "risk_level": risk_level,
        "risk_level_name": level_names[risk_level],
        "action": level_actions[risk_level],
        "risk_score": risk_score,
        "warnings": warnings,
        "signals": signals,
    }
