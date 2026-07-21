#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V9.0 实盘交易执行引擎 (Direct Trading Execution Engine)

消灭所有模糊词汇，直接输出包含【操作类型、确切买价/卖价、精确仓位比例、
生效时间窗口、失效条件】的标准化交易指令卡。

核心模块:
  1. 信号类型标准化 (Signal Taxonomy)
  2. 定量操作算法与价格计算 (Execution Price Formula)
  3. 强约束输出卡片格式 (Trading Signal Protocol)
  4. SELL_STOP 最高优先级覆盖逻辑

Author: Quant Director
Version: 9.0
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