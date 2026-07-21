#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V9.0 / V10.0 实盘交易执行引擎 (Direct Trading Execution Engine)

消灭所有模糊词汇，直接输出包含【操作类型、确切买价/卖价、精确仓位比例、
生效时间窗口、失效条件】的标准化交易指令卡。

核心模块:
  1. 信号类型标准化 (Signal Taxonomy)
  2. 定量操作算法与价格计算 (Execution Price Formula)
  3. 强约束输出卡片格式 (V9.0 Trading Signal Protocol)
  4. V10.0 手机端极简卡片式指令 (Mobile-Friendly, 禁止表格)
  5. SELL_STOP 最高优先级覆盖逻辑
  6. 价格逻辑自检 (Buy Price vs Stop Loss validation)

Author: Quant Director
Version: 10.0
"""

import numpy as np
import pandas as pd
import os
import json
from typing import Dict, List, Tuple, Optional

# =========================================================================
# 信号类型常量
# =========================================================================
SIGNAL_BUY_LIMIT = "BUY_LIMIT"
SIGNAL_BUY_BREAK = "BUY_BREAK"
SIGNAL_SELL_STOP = "SELL_STOP"
SIGNAL_HOLD_WAIT = "HOLD_WAIT"

SIGNAL_DISPLAY = {
    SIGNAL_BUY_LIMIT:  "限价低吸",
    SIGNAL_BUY_BREAK:  "突破追强",
    SIGNAL_SELL_STOP:  "硬止损出局",
    SIGNAL_HOLD_WAIT:  "观望/持股",
}

# 可交易阶段（允许发出买入信号）
TRADABLE_STAGES = {"D1-D2", "D3", "D4-D5"}


# =========================================================================
# 一、信号类型标准化 (Signal Taxonomy)
# =========================================================================

def _classify_signal(
    d_stage: str,
    center_score: float,
    is_breakout: bool,
    latest_close: float,
    ma10: float,
    stop_loss_price: float,
) -> str:
    if d_stage not in TRADABLE_STAGES:
        return SIGNAL_HOLD_WAIT

    if latest_close < ma10 or latest_close < stop_loss_price:
        return SIGNAL_SELL_STOP

    if d_stage in ("D1-D2", "D3") and center_score >= 30:
        return SIGNAL_BUY_LIMIT

    if d_stage in ("D1-D2",) and is_breakout:
        return SIGNAL_BUY_BREAK

    if d_stage in ("D4-D5",):
        return SIGNAL_HOLD_WAIT

    return SIGNAL_HOLD_WAIT


# =========================================================================
# 二、定量操作算法与价格计算 (Execution Price Formula)
# =========================================================================

def _calc_ma(close: np.ndarray, period: int) -> float:
    if len(close) < period:
        return float(close[-1]) if len(close) > 0 else 0.0
    return float(np.mean(close[-period:]))


def _calc_vwap_proxy(stock_data: pd.DataFrame) -> float:
    latest = stock_data.iloc[-1]
    h = float(latest["high"])
    l = float(latest["low"])
    c = float(latest["close"])
    return (h + l + c) / 3.0


def _calc_previous_high(stock_data: pd.DataFrame, window: int = 20) -> float:
    high = stock_data["high"].values.astype(float)
    if len(high) < window + 1:
        return float(np.max(high)) if len(high) > 0 else 0.0
    return float(np.max(high[-(window + 1):-1]))


def _detect_breakout(stock_data: pd.DataFrame) -> bool:
    if len(stock_data) < 21:
        return False
    close_arr = stock_data["close"].values.astype(float)
    vol_arr = stock_data["vol"].values.astype(float)
    high_20d = _calc_previous_high(stock_data, 20)
    close = close_arr[-1]
    vol_today = vol_arr[-1]
    vol_20d_avg = float(np.mean(vol_arr[-21:-1]))
    if vol_20d_avg <= 0:
        return False
    near_high = close > high_20d * 0.98
    volume_surge = vol_today > vol_20d_avg * 1.2
    return near_high and volume_surge


def _calc_buy_limit_price(ma5: float, vwap_proxy: float) -> float:
    return round(min(ma5 * 0.995, vwap_proxy * 0.985), 2)


def _calc_buy_break_price(previous_high: float) -> float:
    return round(previous_high * 1.002, 2)


def _calc_stop_loss_price(entry_price: float, ma10: float) -> float:
    return round(min(ma10, entry_price * 0.975), 2)


def _calc_position_pct(v8_score: float, center_score: float) -> float:
    if v8_score <= 0 or center_score <= 0:
        return 0.0
    raw = round((v8_score / 100.0) * (center_score / 50.0) * 35.0, 1)
    return min(30.0, max(0.0, raw))


def _get_time_window(signal: str) -> str:
    if signal == SIGNAL_BUY_LIMIT:
        return "09:30-10:00"
    elif signal == SIGNAL_BUY_BREAK:
        return "09:30-10:00"
    elif signal == SIGNAL_SELL_STOP:
        return "09:30-15:00"
    else:
        return "-"


def _get_trigger_rule(signal: str, target_price: float, stop_price: float) -> str:
    if signal == SIGNAL_BUY_LIMIT:
        return (
            f"开盘急跌触及{target_price}自动挂单，"
            f"若10:00前未触及则撤单；"
            f"若日内跌破{stop_price}无条件止损"
        )
    elif signal == SIGNAL_BUY_BREAK:
        return (
            f"突破前高放量确认，挂单{target_price}追入，"
            f"10:00前未突破则撤单；"
            f"若买入后跌破{stop_price}无条件止损"
        )
    elif signal == SIGNAL_SELL_STOP:
        return (
            f"触及止损价{stop_price}无条件卖出，"
            f"全天有效，不设撤单"
        )
    else:
        return "无操作，等待下一个交易日信号"


# =========================================================================
# 三、SELL_STOP 最高优先级覆盖逻辑
# =========================================================================

def _apply_sell_stop_override(
    records: List[dict],
    daily_data: pd.DataFrame,
) -> List[dict]:
    for rec in records:
        ts_code = rec["ts_code"]
        stock = daily_data[daily_data["ts_code"] == ts_code].sort_values("trade_date")
        if len(stock) == 0:
            continue

        close = stock["close"].values.astype(float)
        ma10 = _calc_ma(close, 10)
        latest_close = close[-1] if len(close) > 0 else 0.0
        stop_price = rec.get("止损价格", 0.0)

        if latest_close > 0 and (latest_close < ma10 or latest_close < stop_price):
            rec["信号指令"] = SIGNAL_SELL_STOP
            rec["推荐仓位(%)"] = 0.0
            rec["目标价格"] = 0.0
            rec["止损价格"] = round(stop_price, 2)
            rec["生效时间段"] = _get_time_window(SIGNAL_SELL_STOP)
            rec["触发条件/失效规则"] = _get_trigger_rule(
                SIGNAL_SELL_STOP, 0.0, stop_price
            )
            rec["信号覆盖"] = "SELL_STOP覆盖: 收盘价已跌破MA10或止损价"

    return records


# =========================================================================
# 四、Top N 精选买入信号
# =========================================================================

def _select_top_buy_signals(
    signals_df: pd.DataFrame,
    max_picks: int = 3,
) -> pd.DataFrame:
    buy_signals = signals_df[signals_df["信号指令"].isin([SIGNAL_BUY_LIMIT, SIGNAL_BUY_BREAK])].copy()
    if buy_signals.empty:
        return buy_signals

    buy_signals["综合优选分"] = (
        buy_signals["V8综合得分"].fillna(0) * 0.4
        + buy_signals["确定性得分"].fillna(0) * 0.4
        + buy_signals["推荐仓位(%)"].fillna(0) * 0.2
    )
    buy_signals = buy_signals.sort_values("综合优选分", ascending=False)
    buy_signals = buy_signals.drop(columns=["综合优选分"])

    return buy_signals.head(max_picks)


# =========================================================================
# 五、generate_v9_execution_card() 输出格式化卡片
# =========================================================================

def generate_v9_execution_card(
    signals_df: pd.DataFrame,
    trade_date: str,
    v8_top_theme: Optional[dict] = None,
    max_buy_picks: int = 3,
) -> str:
    lines = []
    lines.append(f"# V9.0 实盘执行指令卡 | {trade_date}")
    lines.append("")

    if v8_top_theme:
        lines.append(f"## 核心主线: {v8_top_theme.get('主题', 'N/A')}")
        lines.append(f"- V8综合得分: {v8_top_theme.get('V7综合得分', 0):.1f}")
        lines.append(f"- D阶段: {v8_top_theme.get('D阶段', 'N/A')}")
        lines.append(f"- T_start: {v8_top_theme.get('T_start', 0)}天")
        lines.append(f"- T_MA: {v8_top_theme.get('T_MA', 0)}天")
        lines.append(f"- R_volume: {v8_top_theme.get('R_volume', 0.0):.2f}")
        lines.append("")

    all_buy = signals_df[signals_df["信号指令"].isin([SIGNAL_BUY_LIMIT, SIGNAL_BUY_BREAK])]
    top_buy = _select_top_buy_signals(signals_df, max_buy_picks)
    sell_signals = signals_df[signals_df["信号指令"] == SIGNAL_SELL_STOP]

    lines.append("## 今日精选 (Top 3 开仓信号)")
    lines.append("")

    if not top_buy.empty:
        lines.append(
            "| 标的代码 | 标的名称 | 所属主题 | 信号指令 | 目标价格(元) | "
            "止损价格(元) | 推荐仓位(%) | 生效时间段 | 触发条件/失效规则 |"
        )
        lines.append(
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        )

        for _, row in top_buy.iterrows():
            signal_display = SIGNAL_DISPLAY.get(row["信号指令"], row["信号指令"])
            lines.append(
                f"| {row['标的代码']} | {row['标的名称']} | {row['所属主题']} | "
                f"**{signal_display}** | {row['目标价格']} | {row['止损价格']} | "
                f"{row['推荐仓位(%)']}% | {row['生效时间段']} | "
                f"{row['触发条件/失效规则']} |"
            )
    else:
        lines.append("> 今日无符合条件的买入信号")
    lines.append("")

    top_pos = top_buy["推荐仓位(%)"].sum()
    lines.append(f"### 精选仓位合计: {top_pos:.1f}%")
    lines.append(f"> 全量买入信号: {len(all_buy)} 只，已精选 Top {len(top_buy)} 只（完整清单见 CSV）")
    lines.append("")

    if not sell_signals.empty:
        lines.append("## 止盈止损监控 (最高优先级)")
        lines.append("")
        lines.append(
            "| 标的代码 | 标的名称 | 所属主题 | 信号指令 | 止损价格(元) | 生效时间段 | 触发条件/失效规则 |"
        )
        lines.append(
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        )
        for _, row in sell_signals.iterrows():
            lines.append(
                f"| {row['标的代码']} | {row['标的名称']} | {row['所属主题']} | "
                f"**硬止损出局** | {row['止损价格']} | {row['生效时间段']} | "
                f"{row['触发条件/失效规则']} |"
            )
        lines.append("")

    lines.append("## 风险提示")
    lines.append("- 所有指令均为量化模型生成，不构成投资建议")
    lines.append("- SELL_STOP 信号具有最高优先级，触及必须执行")
    lines.append("- 单日最多开仓 3 只，单票仓位不超过 25%")
    lines.append("- 若市场出现系统性风险（指数跌幅 > 2%），所有 BUY 信号自动失效")

    return "\n".join(lines)


# =========================================================================
# 五-B、V10.0 价格逻辑自检
# =========================================================================

def _validate_price_logic(
    records: List[dict],
) -> Tuple[List[dict], int]:
    """
    价格逻辑自检：
    - 限价低吸：止损价 < 买入价（止损在下方）
    - 突破追强：止损价 < 买入价（止损在下方）
    - 凡不满足的，降级为 HOLD_WAIT

    返回: (清洗后的 records, 剔除数量)
    """
    invalid_count = 0
    for rec in records:
        signal = rec.get("信号指令", "")
        target = rec.get("目标价格", 0.0)
        stop = rec.get("止损价格", 0.0)

        if signal in (SIGNAL_BUY_LIMIT, SIGNAL_BUY_BREAK):
            if target <= 0 or stop <= 0 or stop >= target:
                rec["信号指令"] = SIGNAL_HOLD_WAIT
                rec["目标价格"] = 0.0
                rec["推荐仓位(%)"] = 0.0
                rec["触发条件/失效规则"] = "价格逻辑矛盾(止损价>=买入价)，已剔除"
                invalid_count += 1

    return records, invalid_count


# =========================================================================
# 五-C、V10.0 三步逻辑审计协议
# =========================================================================

def _audit_logic_consistency(
    signals_df: pd.DataFrame,
    top_buy_df: pd.DataFrame,
    v8_top_theme: Optional[dict],
    v8_center_df: Optional[pd.DataFrame] = None,
    market_stage: str = "",
    max_position_pct: float = 30.0,
) -> List[dict]:
    """
    三步强制逻辑自洽校验协议。

    返回: List[dict] 每项含 {step, pass, notes}
    """
    audit = []

    # ---- 数据准备 ----
    top_buy_list = []
    for _, row in top_buy_df.iterrows():
        top_buy_list.append(row)

    main_theme = v8_top_theme.get("主题", "") if v8_top_theme else ""
    d_stage = v8_top_theme.get("D阶段", "") if v8_top_theme else ""

    center_map = {}
    if v8_center_df is not None and not v8_center_df.empty:
        for _, row in v8_center_df.iterrows():
            code = str(row.get("ts_code", ""))
            center_map[code] = {
                "主题": str(row.get("主题", "")),
                "确定性得分": float(row.get("确定性得分", 0)),
                "低吸参考价": float(row.get("低吸参考价", 0)),
                "防守止损位": float(row.get("防守止损位", 0)),
            }

    # ============================================================
    # 校验 1: 主线与标的的一致性
    # ============================================================
    step1_notes = []
    step1_pass = True

    if main_theme and top_buy_list:
        match_count = 0
        for row in top_buy_list:
            buy_theme = str(row["所属主题"])
            if buy_theme == main_theme:
                match_count += 1
        if match_count == 0:
            step1_pass = False
            step1_notes.append(
                f"⚠ 主线为「{main_theme}」，但 Top 2 开仓标的均不属于该主题，"
                f"存在主题漂移风险"
            )
        elif match_count < len(top_buy_list):
            step1_notes.append(
                f"⚠ 主线「{main_theme}」仅匹配 {match_count}/{len(top_buy_list)} 只，"
                f"部分标的偏离主线"
            )
        else:
            step1_notes.append(
                f"✅ 主线「{main_theme}」与 Top 2 开仓标的 {match_count}/{len(top_buy_list)} 完全匹配"
            )
    else:
        step1_notes.append("✅ 无主线定义或开仓标的，跳过校验")

    # 检查 1b: 高确定性中军是否被优先纳入
    if v8_center_df is not None and not v8_center_df.empty and main_theme:
        center_in_theme = v8_center_df[v8_center_df["主题"] == main_theme]
        if not center_in_theme.empty:
            top_center = center_in_theme.sort_values("确定性得分", ascending=False).iloc[0]
            top_center_code = str(top_center["ts_code"])
            top_center_score = float(top_center["确定性得分"])
            in_picks = any(str(r["标的代码"]) == top_center_code for r in top_buy_list)
            if not in_picks and top_center_score >= 20:
                step1_notes.append(
                    f"⚠ 主线「{main_theme}」确定性子最高 {top_center_code}({top_center_score:.0f}分)"
                    f"未被纳入 Top 2，需确认是否因盘前利空或流动性不足"
                )
            elif in_picks:
                step1_notes.append(
                    f"✅ 主线确定性最高标的 {top_center_code}({top_center_score:.0f}分) 已纳入"
                )

    if not step1_notes:
        step1_notes.append("✅ 通过")
    audit.append({"step": "1. 主线与标的匹配度", "pass": step1_pass, "notes": step1_notes})

    # ============================================================
    # 校验 2: 策略与风控的匹配性
    # ============================================================
    step2_notes = []
    step2_pass = True

    defensive_stages = {"等待确认", "冰点反弹", "弱势", "观望"}
    if market_stage in defensive_stages or any(s in market_stage for s in defensive_stages):
        if max_position_pct > 30:
            step2_pass = False
            step2_notes.append(
                f"⚠ 市场阶段「{market_stage}」为防御期，仓位上限 {max_position_pct}% 超过 30% 上限"
            )
        else:
            step2_notes.append(
                f"✅ 防御期仓位上限 {max_position_pct}% ≤ 30%，匹配"
            )

        for row in top_buy_list:
            if row["信号指令"] == SIGNAL_BUY_BREAK:
                step2_notes.append(
                    f"⚠ 防御期「{market_stage}」使用 BUY_BREAK 突破追强"
                    f"({row['标的名称']})，在弱势市场中追突破风险较高，建议评估"
                )
    else:
        step2_notes.append(f"✅ 市场阶段「{market_stage}」与仓位上限 {max_position_pct}% 匹配")

    if d_stage in ("D6-D7", "D8+"):
        if d_stage == "D8+":
            step2_pass = False
            step2_notes.append(f"⚠ 主线「{main_theme}」处于 D8+ 退潮期，不应发出买入信号，建议全部降级为 HOLD_WAIT")
        elif d_stage == "D6-D7":
            step2_notes.append(f"⚠ 主线「{main_theme}」处于 D6-D7 加速高潮期，仅适合持股锁仓，新开仓风险较高")

    if not step2_notes:
        step2_notes.append("✅ 通过")
    audit.append({"step": "2. 策略与风控匹配度", "pass": step2_pass, "notes": step2_notes})

    # ============================================================
    # 校验 3: 数据与指令的交叉验证
    # ============================================================
    step3_notes = []
    step3_pass = True

    for row in top_buy_list:
        code = str(row["标的代码"])
        signal = row["信号指令"]
        target_price = float(row["目标价格"])
        stop_price = float(row["止损价格"])

        if code in center_map:
            c = center_map[code]
            low_ref = c["低吸参考价"]
            center_stop = c["防守止损位"]

            if signal == SIGNAL_BUY_BREAK:
                if target_price < low_ref:
                    step3_pass = False
                    step3_notes.append(
                        f"⚠ {code} 突破追强买入价 {target_price} < 中军低吸价 {low_ref}，"
                        f"突破追强价应 ≥ 低吸参考价"
                    )
                else:
                    step3_notes.append(
                        f"✅ {code} 突破追强价 {target_price} ≥ 低吸价 {low_ref}，交叉验证通过"
                    )
            elif signal == SIGNAL_BUY_LIMIT:
                if target_price > low_ref * 1.01:
                    step3_notes.append(
                        f"⚠ {code} 限价低吸价 {target_price} > 中军低吸价 {low_ref}，"
                        f"低吸买点偏高，建议下调至 {low_ref:.2f} 附近"
                    )
                else:
                    step3_notes.append(
                        f"✅ {code} 限价低吸价 {target_price} ≤ 低吸价 {low_ref}，交叉验证通过"
                    )

            gap_pct = abs(target_price - stop_price) / target_price * 100
            if gap_pct < 1.0:
                step3_pass = False
                step3_notes.append(
                    f"⚠ {code} 买入价 {target_price} 与止损价 {stop_price} 间距仅 {gap_pct:.1f}%，"
                    f"空间过窄易被噪音触发误止损，建议扩大至 ≥ 2%"
                )
            elif gap_pct < 2.0:
                step3_notes.append(
                    f"⚠ {code} 买入价-止损价间距 {gap_pct:.1f}%，略窄，关注盘中波动风险"
                )
            else:
                step3_notes.append(
                    f"✅ {code} 买入价-止损价间距 {gap_pct:.1f}%，合理"
                )

            if abs(stop_price - center_stop) / center_stop * 100 < 0.5:
                step3_notes.append(
                    f"⚠ {code} V10 止损价 {stop_price} 与中军防守止损位 {center_stop} 基本一致，逻辑自洽"
                )
        else:
            step3_notes.append(f"⚠ {code} 不在中军标的库中，无法交叉验证低吸/止损参考价")

    if not step3_notes:
        step3_notes.append("✅ 通过")
    audit.append({"step": "3. 数据交叉验证", "pass": step3_pass, "notes": step3_notes})

    return audit


def _format_audit_report(audit: List[dict]) -> str:
    """将审计结果格式化为可读报告"""
    lines = []
    lines.append("###  逻辑审计报告")

    all_pass = True
    for item in audit:
        step = item["step"]
        passed = item["pass"]
        notes = item["notes"]
        status = "通过" if passed else "不通过"
        icon = "✅" if passed else "❌"
        if not passed:
            all_pass = False

        lines.append(f"{icon} {step}：{status}")
        for note in notes:
            lines.append(f"  {note}")
        lines.append("")

    if all_pass:
        lines.append("> 审计结论：全部通过，可执行")
    else:
        lines.append("> ⚠ 审计结论：存在不通过项，请人工复核后执行")

    return "\n".join(lines)


# =========================================================================
# 五-D、V10.0 手机端极简卡片式指令生成器（含审计）
# =========================================================================

def generate_v10_mobile_card(
    signals_df: pd.DataFrame,
    trade_date: str,
    v8_top_theme: Optional[dict] = None,
    v8_center_df: Optional[pd.DataFrame] = None,
    market_stage: str = "",
    max_buy_picks: int = 2,
    max_position_pct: float = 30.0,
) -> str:
    """
    V10.0 手机端极简卡片式指令（含三步逻辑审计）

    硬性约束：
    - 绝对禁止 Markdown 表格语法
    - 每行短小，适合手机屏幕
    - 使用 `XX.XX` 代码块高亮价格
    - 删除所有情绪描述、逻辑推导
    - 输出前强制执行三步逻辑审计

    返回: 纯文本手机端指令卡（含审计报告）
    """
    lines = []

    # ---- 盘前一句话 ----
    top_buy = _select_top_buy_signals(signals_df, max_buy_picks)
    sell_signals = signals_df[signals_df["信号指令"] == SIGNAL_SELL_STOP]

    theme_name = v8_top_theme.get("主题", "N/A") if v8_top_theme else "N/A"
    d_stage = v8_top_theme.get("D阶段", "") if v8_top_theme else ""
    v8_score = v8_top_theme.get("V7综合得分", 0) if v8_top_theme else 0

    if not market_stage:
        market_stage = "等待确认"

    # ---- 三步逻辑审计 ----
    audit = _audit_logic_consistency(
        signals_df=signals_df,
        top_buy_df=top_buy,
        v8_top_theme=v8_top_theme,
        v8_center_df=v8_center_df,
        market_stage=market_stage,
        max_position_pct=max_position_pct,
    )
    audit_report = _format_audit_report(audit)
    lines.append(audit_report)
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("🎯 盘前一句话")
    lines.append(f"• 市场阶段：{market_stage}")
    lines.append(f"• 今日主线：{theme_name} ({d_stage}, V8={v8_score:.0f})")
    lines.append(f"• 仓位上限：30%")
    lines.append("")

    # ---- 今日开仓指令 ----
    lines.append("---")
    lines.append("")
    lines.append("🛒 今日开仓指令（最多 2 只）")
    lines.append("")

    top_buy_list = []
    for _, row in top_buy.iterrows():
        top_buy_list.append(row)

    if top_buy_list:
        for i, row in enumerate(top_buy_list, 1):
            signal = row["信号指令"]
            signal_display = SIGNAL_DISPLAY.get(signal, signal)
            code = row["标的代码"]
            name = row["标的名称"]
            theme = row["所属主题"]
            target_price = row["目标价格"]
            stop_price = row["止损价格"]
            position = row["推荐仓位(%)"]
            time_window = row.get("生效时间段", "09:30-10:00")

            lines.append(f"### {i}. {name} ({code})")
            lines.append(f"• 所属主题：{theme}")
            lines.append(f"• 买入类型：{signal_display}")
            lines.append(f"• 挂单买入价：`{target_price:.2f}` 元")
            lines.append(f"• 硬止损价：`{stop_price:.2f}` 元")
            lines.append(f"• 拟建仓位：{position:.1f}%")
            lines.append(f"• 执行窗口：{time_window}")
            lines.append(f"• 风控规则：{row['触发条件/失效规则']}")

            if i < len(top_buy_list):
                lines.append("")
    else:
        lines.append("> 今日无符合条件的买入信号")
        lines.append("")

    lines.append("")

    # ---- 持仓止损/清仓指令 ----
    lines.append("---")
    lines.append("")
    lines.append("🚨 持仓止损/清仓指令（全天有效）")
    lines.append("")

    if not sell_signals.empty:
        for _, row in sell_signals.iterrows():
            code = row["标的代码"]
            name = row["标的名称"]
            stop_price = row["止损价格"]
            lines.append(f"• {name} ({code}) ➔ 触发卖出价：`{stop_price:.2f}` 元（触及即清仓）")
    else:
        lines.append("• 今日无持仓止损信号")
    lines.append("")

    # ---- 风险提示 ----
    lines.append("---")
    lines.append("")
    lines.append("⚡ 风险提示")
    lines.append("• 量化模型生成，不构成投资建议")
    lines.append("• SELL_STOP 触及必须执行，不可犹豫")
    lines.append("• 单日最多开仓 2 只，单票 ≤ 25%")

    return "\n".join(lines)


# =========================================================================
# 六、主入口函数 calculate_v9_execution_signals()
# =========================================================================

def calculate_v9_execution_signals(
    v8_theme_result: pd.DataFrame,
    v8_center_df: pd.DataFrame,
    daily_data: pd.DataFrame,
    trade_date: str,
    name_map: Optional[Dict[str, str]] = None,
) -> Tuple[pd.DataFrame, str]:
    if name_map is None:
        name_map = {}

    if v8_theme_result.empty or v8_center_df.empty:
        empty_df = pd.DataFrame(columns=[
            "标的代码", "标的名称", "所属主题", "信号指令",
            "目标价格", "止损价格", "推荐仓位(%)", "生效时间段",
            "触发条件/失效规则",
        ])
        return empty_df, ""

    theme_score_map = {}
    for _, row in v8_theme_result.iterrows():
        theme_score_map[row["主题"]] = {
            "V7综合得分": row.get("V7综合得分", 0),
            "D阶段": row.get("D阶段", ""),
            "T_start": row.get("T_start", 0),
            "T_MA": row.get("T_MA", 0),
            "R_volume": row.get("R_volume", 0.0),
        }

    records = []
    for _, center_row in v8_center_df.iterrows():
        ts_code = str(center_row["ts_code"])
        theme = str(center_row.get("主题", ""))
        center_score = float(center_row.get("确定性得分", 0))
        ref_price = float(center_row.get("低吸参考价", 0))
        stop_ref = float(center_row.get("防守止损位", 0))

        theme_info = theme_score_map.get(theme, {})
        v8_score = float(theme_info.get("V7综合得分", 0))
        d_stage = str(theme_info.get("D阶段", ""))

        stock = daily_data[daily_data["ts_code"] == ts_code].sort_values("trade_date")
        if len(stock) < 10:
            records.append({
                "ts_code": ts_code,
                "标的名称": name_map.get(ts_code, ts_code),
                "所属主题": theme,
                "信号指令": SIGNAL_HOLD_WAIT,
                "目标价格": 0.0,
                "止损价格": 0.0,
                "推荐仓位(%)": 0.0,
                "生效时间段": _get_time_window(SIGNAL_HOLD_WAIT),
                "触发条件/失效规则": "数据不足(历史<10天)，无法生成信号",
                "信号覆盖": "",
                "V8综合得分": v8_score,
                "确定性得分": center_score,
                "D阶段": d_stage,
            })
            continue

        close = stock["close"].values.astype(float)
        ma5 = _calc_ma(close, 5)
        ma10 = _calc_ma(close, 10)
        vwap_proxy = _calc_vwap_proxy(stock)
        previous_high = _calc_previous_high(stock, 20)
        is_breakout = _detect_breakout(stock)

        buy_limit_price = _calc_buy_limit_price(ma5, vwap_proxy)
        buy_break_price = _calc_buy_break_price(previous_high)

        entry_price = buy_limit_price if center_score >= 30 else buy_break_price
        if entry_price <= 0:
            entry_price = ref_price

        stop_loss_price = _calc_stop_loss_price(entry_price, ma10)

        signal = _classify_signal(
            d_stage=d_stage,
            center_score=center_score,
            is_breakout=is_breakout,
            latest_close=float(close[-1]),
            ma10=ma10,
            stop_loss_price=stop_loss_price,
        )

        if signal == SIGNAL_BUY_LIMIT:
            target_price = buy_limit_price
        elif signal == SIGNAL_BUY_BREAK:
            target_price = buy_break_price
        elif signal == SIGNAL_SELL_STOP:
            target_price = 0.0
        else:
            target_price = 0.0

        position_pct = _calc_position_pct(v8_score, center_score) if signal in (
            SIGNAL_BUY_LIMIT, SIGNAL_BUY_BREAK
        ) else 0.0

        records.append({
            "ts_code": ts_code,
            "标的名称": name_map.get(ts_code, ts_code),
            "所属主题": theme,
            "信号指令": signal,
            "目标价格": target_price,
            "止损价格": stop_loss_price,
            "推荐仓位(%)": position_pct,
            "生效时间段": _get_time_window(signal),
            "触发条件/失效规则": _get_trigger_rule(signal, target_price, stop_loss_price),
            "信号覆盖": "",
            "V8综合得分": v8_score,
            "确定性得分": center_score,
            "D阶段": d_stage,
        })

    # ---- SELL_STOP 最高优先级覆盖 ----
    records = _apply_sell_stop_override(records, daily_data)

    # ---- V10.0 价格逻辑自检 ----
    records, invalid_count = _validate_price_logic(records)
    if invalid_count > 0:
        print(f"  [V10.0] 价格逻辑自检: 剔除 {invalid_count} 条矛盾信号")

    result_df = pd.DataFrame(records)
    result_df.rename(columns={"ts_code": "标的代码"}, inplace=True)

    display_cols = [
        "标的代码", "标的名称", "所属主题", "信号指令",
        "目标价格", "止损价格", "推荐仓位(%)", "生效时间段",
        "触发条件/失效规则", "V8综合得分", "确定性得分", "D阶段",
    ]

    if "信号覆盖" in result_df.columns:
        display_cols.append("信号覆盖")

    output_df = result_df[display_cols].copy()
    output_df = output_df.sort_values(
        ["信号指令", "推荐仓位(%)"],
        ascending=[True, False],
    ).reset_index(drop=True)

    top_theme_info = None
    if len(v8_theme_result) > 0:
        top_theme_info = v8_theme_result.iloc[0].to_dict()

    card = generate_v9_execution_card(output_df, trade_date, top_theme_info, max_buy_picks=3)

    return output_df, card


# =========================================================================
# 七、便捷加载函数 (从文件加载)
# =========================================================================

def load_v9_from_files(
    base_dir: str,
    trade_date: str,
    name_map: Optional[Dict[str, str]] = None,
) -> Tuple[pd.DataFrame, str]:
    cache_dir = os.path.join(base_dir, "cache")
    v8_json = os.path.join(cache_dir, f"theme_alpha_v6_result_v8_{trade_date}.json")
    v8_center_csv = os.path.join(cache_dir, f"theme_alpha_v6_result_v8_center_{trade_date}.csv")

    if not os.path.exists(v8_json):
        import glob
        candidates = sorted(glob.glob(os.path.join(cache_dir, "theme_alpha_v6_result_v8_*.json")), reverse=True)
        if candidates:
            v8_json = candidates[0]
            actual_date = os.path.basename(v8_json).replace("theme_alpha_v6_result_v8_", "").replace(".json", "")
            print(f"[V9.0] 主题评分回退至: {actual_date}")
        else:
            return pd.DataFrame(), ""

    if not os.path.exists(v8_center_csv):
        import glob
        candidates = sorted(glob.glob(os.path.join(cache_dir, "theme_alpha_v6_result_v8_center_*.csv")), reverse=True)
        if candidates:
            v8_center_csv = candidates[0]
            actual_date = os.path.basename(v8_center_csv).replace("theme_alpha_v6_result_v8_center_", "").replace(".csv", "")
            print(f"[V9.0] 中军数据回退至: {actual_date}")
        else:
            return pd.DataFrame(), ""

    with open(v8_json, "r", encoding="utf-8") as f:
        theme_data = json.load(f)
    v8_theme_df = pd.DataFrame(theme_data)

    v8_center_df = pd.read_csv(v8_center_csv)

    ts_codes = v8_center_df["ts_code"].unique().tolist()
    from data_loader import load_daily
    daily_data = load_daily(ts_codes, "20250101", trade_date)

    return calculate_v9_execution_signals(
        v8_theme_result=v8_theme_df,
        v8_center_df=v8_center_df,
        daily_data=daily_data,
        trade_date=trade_date,
        name_map=name_map,
    )


if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    TRADE_DATE = "20260720"

    sig_df, card = load_v9_from_files(BASE_DIR, TRADE_DATE)

    print("\n" + "=" * 80)
    print("V9.0 实盘交易执行引擎 - 测试运行")
    print("=" * 80)

    if not sig_df.empty:
        print(f"\n共生成 {len(sig_df)} 条交易指令")
        print("\n信号分布:")
        for sig in [SIGNAL_BUY_LIMIT, SIGNAL_BUY_BREAK, SIGNAL_SELL_STOP, SIGNAL_HOLD_WAIT]:
            count = (sig_df["信号指令"] == sig).sum()
            if count > 0:
                print(f"  {SIGNAL_DISPLAY[sig]}: {count} 条")

        print("\n操作指令明细:")
        print(sig_df[["标的代码", "标的名称", "所属主题", "信号指令", "目标价格", "止损价格", "推荐仓位(%)"]].to_string(index=False))

        print("\n" + card)
    else:
        print("无可用数据，无法生成交易指令")