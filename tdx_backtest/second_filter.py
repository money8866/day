# -*- coding: utf-8 -*-
"""
技术指标二次过滤算法 — 机构量化择时

输入: bull_stocks_qualified.csv 股池 (strategy 已选出的股票)
输出: 评分 + 推荐次日开盘买入的股票

设计思路 (针对次日开盘买入胜率):
  1. 量价配合 (25分) - 避免追高: 涨幅/量比/下影线
  2. 均线系统 (20分) - 趋势确认: ma5斜率/收盘vs ma5/多头排列
  3. 动量指标 (25分) - 强弱判断: MACD柱/KDJ/RSI
  4. 波动率   (15分) - 风险控制: 布林带位置/ATR
  5. 资金流向 (15分) - 主力动向: OBV趋势/当日量能

总分 100, >=70 分推荐次日开盘买入.
"""
from __future__ import annotations
import os
import sys
import time
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline
from indicators import MA, EMA, MACD, KDJ, RSI, BOLL, OBV, add_indicators
from strategy_backtest import (
    precompute_indicators, strategy_vectorized, Filters as StrategyFilters,
)


# =========================================================
# 股票代码转换: csv 中 "2709" → "002709.SZ", "688525" → "688525.SH"
# =========================================================
def code_to_ts_code(code) -> str:
    """6位代码 → ts_code (带市场后缀)"""
    s = str(code).zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    elif s.startswith(("0", "3", "2")):
        return f"{s}.SZ"
    elif s.startswith(("8", "4")):
        return f"{s}.BJ"
    return f"{s}.SZ"


# =========================================================
# 资金流制度识别 (Cap Flow Regime Switch)
# =========================================================
REGIME_EARLY_TREND       = "early_trend"        # 早期趋势
REGIME_MID_EXPANSION     = "mid_expansion"      # 中期扩张
REGIME_CLIMAX_DISTRIB    = "climax_distribution"  # 高潮派发
REGIME_ROTATION_EXIT     = "rotation_exit"      # 轮动退出

REGIME_SCORE_ADJUST = {
    REGIME_EARLY_TREND:    +10,  # 早期趋势: 加分 (启动初期, 胜率高)
    REGIME_MID_EXPANSION:  +5,   # 中期扩张: 小加分 (趋势确认)
    REGIME_CLIMAX_DISTRIB: -10,  # 高潮派发: 扣分 (追高风险)
    REGIME_ROTATION_EXIT:  -20,  # 轮动退出: 大扣分 (规避)
}


def detect_regime(df: pd.DataFrame) -> Tuple[str, Dict]:
    """识别资金流制度 (4阶段)

    判断维度:
      1. 均线排列 (ma5/ma10/ma20/ma60)
      2. 量能趋势 (5日均量/20日均量)
      3. 当日量比 (vol/5日均量)
      4. RSI 强弱
      5. 近20日涨幅
      6. ma5 斜率
      7. OBV 趋势

    Returns:
        (regime, info_dict)
    """
    if len(df) < 60:
        return REGIME_MID_EXPANSION, {"reason": "数据不足"}

    last = df.iloc[-1]
    C = last["close"]
    ma5  = last.get("MA5", np.nan)
    ma10 = last.get("MA10", np.nan)
    ma20 = last.get("MA20", np.nan)

    # 量能
    vol      = last["vol"]
    vol_ma5  = df["vol"].iloc[-5:].mean()
    vol_ma20 = df["vol"].iloc[-20:].mean()
    vol_ratio_5 = vol / vol_ma5 if vol_ma5 > 0 else 1.0
    vol_trend   = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0  # 5日均量/20日均量

    # RSI
    rsi = last.get("RSI6", 50)
    if pd.isna(rsi):
        rsi = 50

    # 近20日涨幅
    ret_20d = (C / df["close"].iloc[-21] - 1) * 100 if len(df) >= 21 else 0

    # ma5 斜率
    prev_ma5 = df["MA5"].iloc[-2] if "MA5" in df.columns else np.nan
    if pd.notna(prev_ma5) and prev_ma5 > 0:
        ma5_slope = (ma5 / prev_ma5 - 1) * 100
    else:
        ma5_slope = 0

    # OBV 趋势
    obv_now = last.get("OBV", 0)
    obv_5d_ago = df["OBV"].iloc[-6] if "OBV" in df.columns and len(df) >= 6 else 0
    obv_trend = (obv_now - obv_5d_ago) / abs(obv_5d_ago) * 100 if obv_5d_ago != 0 else 0

    # 均线排列
    full_bullish = (pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20)
                    and ma5 > ma10 > ma20)
    partial_bullish = (pd.notna(ma5) and pd.notna(ma10) and ma5 > ma10)

    info = {
        "regime": "",
        "ma5_slope": round(ma5_slope, 3),
        "vol_ratio_5": round(vol_ratio_5, 2),
        "vol_trend": round(vol_trend, 2),
        "rsi": round(rsi, 1),
        "ret_20d": round(ret_20d, 2),
        "obv_trend": round(obv_trend, 2),
        "full_bullish": full_bullish,
    }

    # ===== 制度识别 (按优先级判断) =====

    # 1. rotation_exit: 均线空头 或 量能萎缩 + RSI 偏弱
    if (not partial_bullish) or (vol_trend < 0.9 and rsi < 50):
        regime = REGIME_ROTATION_EXIT
    # 2. climax_distribution: 量能放大 + RSI 偏高 或 涨幅过大
    elif (vol_ratio_5 > 1.8 and rsi > 68) or (ret_20d > 22 and vol_trend > 1.4):
        regime = REGIME_CLIMAX_DISTRIB
    # 3. early_trend: 多头 + 量能温和 + 涨幅较小 (启动初期)
    elif partial_bullish and vol_trend < 1.15 and ret_20d < 18:
        regime = REGIME_EARLY_TREND
    # 4. mid_expansion: 完整多头 + 量能放大 + RSI 偏强
    elif full_bullish and vol_trend >= 1.15 and rsi >= 55:
        regime = REGIME_MID_EXPANSION
    # 默认: 根据量能判断
    elif vol_trend >= 1.0:
        regime = REGIME_MID_EXPANSION
    else:
        regime = REGIME_EARLY_TREND

    info["regime"] = regime
    return regime, info


# =========================================================
# 二次过滤评分
# =========================================================
def score_second_filter(df: pd.DataFrame) -> Tuple[float, Dict]:
    """对单只股票的最新交易日打分

    Args:
        df: 含 add_indicators 列的 K 线 DataFrame
    Returns:
        (总分, 各维度得分明细)
    """
    if len(df) < 30:
        return 0.0, {"reason": "数据不足"}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    C, O, H, L = last["close"], last["open"], last["high"], last["low"]
    VOL = last["vol"]

    details = {}
    total = 0.0

    # ===== 1. 量价配合 (25分) =====
    # 1.1 当日涨幅 (10分): <3% 满分, 3-5% 7分, 5-8% 3分, >8% 0分
    pct_chg = (C / prev["close"] - 1) * 100
    if pct_chg < 3:
        s_11 = 10
    elif pct_chg < 5:
        s_11 = 7
    elif pct_chg < 8:
        s_11 = 3
    else:
        s_11 = 0
    # 涨幅过大直接扣分 (追高风险)
    if pct_chg > 9.5:
        s_11 = -5  # 接近涨停, 次日开盘必回调

    # 1.2 量比 (10分): <1.2 满分, 1.2-2.0 7分, 2.0-3.0 3分, >3.0 0分
    vol_ma5 = df["vol"].iloc[-5:].mean()
    vol_ratio = VOL / vol_ma5 if vol_ma5 > 0 else 1.0
    if vol_ratio < 1.2:
        s_12 = 10
    elif vol_ratio < 2.0:
        s_12 = 7
    elif vol_ratio < 3.0:
        s_12 = 3
    else:
        s_12 = 0

    # 1.3 下影线支撑 (5分): 下影线长度/实体长度 > 0.5 满分
    body = abs(C - O)
    lower_shadow = min(O, C) - L
    if body > 0:
        lower_ratio = lower_shadow / body
    else:
        lower_ratio = 1.0 if lower_shadow > 0 else 0
    if lower_ratio >= 0.5:
        s_13 = 5
    elif lower_ratio >= 0.2:
        s_13 = 3
    else:
        s_13 = 0

    s1 = s_11 + s_12 + s_13
    details["量价_涨幅%"] = round(pct_chg, 2)
    details["量价_量比"] = round(vol_ratio, 2)
    details["量价_下影比"] = round(lower_ratio, 2)
    details["量价_得分"] = s1
    total += s1

    # ===== 2. 均线系统 (20分) =====
    ma5, ma10, ma20 = last["MA5"], last["MA10"], last["MA20"]
    prev_ma5 = prev["MA5"]

    # 2.1 ma5 斜率 (10分): ma5 上升满分, 走平5分, 下行0分
    if pd.notna(ma5) and pd.notna(prev_ma5) and prev_ma5 > 0:
        ma5_slope = (ma5 / prev_ma5 - 1) * 100
        if ma5_slope > 0.2:
            s_21 = 10
        elif ma5_slope > -0.2:
            s_21 = 5
        else:
            s_21 = 0
    else:
        s_21 = 0
        ma5_slope = np.nan

    # 2.2 收盘价 vs ma5 (5分): 站稳ma5 满分
    if pd.notna(ma5) and ma5 > 0:
        if C > ma5:
            s_22 = 5
        elif C > ma5 * 0.98:
            s_22 = 3
        else:
            s_22 = 0
    else:
        s_22 = 0

    # 2.3 多头排列 (5分): ma5 > ma10 > ma20
    if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20):
        if ma5 > ma10 > ma20:
            s_23 = 5
        elif ma5 > ma10:
            s_23 = 3
        else:
            s_23 = 0
    else:
        s_23 = 0

    s2 = s_21 + s_22 + s_23
    details["均线_ma5斜率%"] = round(ma5_slope, 2) if pd.notna(ma5_slope) else None
    details["均线_C_vs_ma5"] = round(C / ma5 - 1, 4) if pd.notna(ma5) and ma5 > 0 else None
    details["均线_多头排列"] = (ma5 > ma10 > ma20) if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) else False
    details["均线_得分"] = s2
    total += s2

    # ===== 3. 动量指标 (25分) =====
    # 3.1 MACD 柱 (10分): >0 且上升 满分, >0 5分, 由弱转强 8分
    macd_hist = last["MACD"]
    prev_macd_hist = prev["MACD"]
    if pd.notna(macd_hist) and pd.notna(prev_macd_hist):
        if macd_hist > 0 and macd_hist > prev_macd_hist:
            s_31 = 10
        elif macd_hist > 0:
            s_31 = 5
        elif macd_hist > prev_macd_hist:  # 由弱转强 (柱线缩短)
            s_31 = 8
        else:
            s_31 = 0
    else:
        s_31 = 0

    # 3.2 KDJ (10分): J<90 且 K>D (金叉/即将金叉)
    k, d, j = last["K"], last["D"], last["J"]
    prev_k, prev_d = prev["K"], prev["D"]
    if pd.notna(k) and pd.notna(d) and pd.notna(j):
        if j >= 90:
            s_32 = 0  # 超买
        elif k > d and prev_k <= prev_d:  # 刚金叉
            s_32 = 10
        elif k > d:  # 金叉后
            s_32 = 7
        elif k > prev_k:  # 即将金叉
            s_32 = 5
        else:
            s_32 = 0
    else:
        s_32 = 0

    # 3.3 RSI (5分): 50-70 强势不超买 满分
    rsi = last.get("RSI6", np.nan)
    if pd.notna(rsi):
        if 50 <= rsi < 70:
            s_33 = 5
        elif 40 <= rsi < 50:
            s_33 = 3
        elif rsi >= 70:
            s_33 = 0  # 超买
        else:
            s_33 = 2
    else:
        s_33 = 0

    s3 = s_31 + s_32 + s_33
    details["动量_MACD柱"] = round(macd_hist, 4) if pd.notna(macd_hist) else None
    details["动量_KDJ"] = f"K={k:.1f},D={d:.1f},J={j:.1f}" if pd.notna(k) else None
    details["动量_RSI6"] = round(rsi, 1) if pd.notna(rsi) else None
    details["动量_得分"] = s3
    total += s3

    # ===== 4. 波动率 (15分) =====
    # 4.1 布林带位置 (10分): 中轨上方不破上轨 满分
    boll_up, boll_mid, boll_dn = last["BOLL_UP"], last["BOLL_MID"], last["BOLL_DN"]
    if pd.notna(boll_up) and pd.notna(boll_mid) and pd.notna(boll_dn) and boll_mid > 0:
        pos = (C - boll_mid) / (boll_up - boll_mid) if (boll_up - boll_mid) > 0 else 0
        if 0 <= pos < 0.8:  # 中轨到上轨80%之间
            s_41 = 10
        elif -0.5 <= pos < 0:  # 中轨下方但接近
            s_41 = 7
        elif pos >= 0.8:  # 接近上轨
            s_41 = 3
        else:  # 跌破下轨区域
            s_41 = 0
    else:
        s_41 = 0
        pos = np.nan

    # 4.2 ATR/价格 (5分): 波动率适中
    if len(df) >= 20:
        tr = (df["high"].iloc[-20:] - df["low"].iloc[-20:]).mean()
        atr_pct = tr / C * 100 if C > 0 else 0
        if 2 <= atr_pct < 6:
            s_42 = 5
        elif atr_pct < 2:
            s_42 = 3  # 波动过小, 缺乏动能
        else:
            s_42 = 0  # 波动过大
    else:
        s_42 = 0
        atr_pct = np.nan

    s4 = s_41 + s_42
    details["波动_布林位置"] = round(pos, 2) if pd.notna(pos) else None
    details["波动_ATR%"] = round(atr_pct, 2) if pd.notna(atr_pct) else None
    details["波动_得分"] = s4
    total += s4

    # ===== 5. 资金流向 (15分) =====
    # 5.1 OBV 5日趋势 (10分): 上升满分
    obv_now = last["OBV"]
    obv_5d_ago = df["OBV"].iloc[-6] if len(df) >= 6 else np.nan
    if pd.notna(obv_now) and pd.notna(obv_5d_ago) and obv_5d_ago != 0:
        obv_slope = (obv_now - obv_5d_ago) / abs(obv_5d_ago) * 100
        if obv_slope > 2:
            s_51 = 10
        elif obv_slope > 0:
            s_51 = 6
        elif obv_slope > -2:
            s_51 = 3
        else:
            s_51 = 0
    else:
        s_51 = 0
        obv_slope = np.nan

    # 5.2 当日量能 (5分): 当日量 > 5日均量
    if vol_ma5 > 0:
        if VOL > vol_ma5 * 1.2:
            s_52 = 5  # 放量上涨 (与涨幅配合)
        elif VOL > vol_ma5:
            s_52 = 3
        else:
            s_52 = 2  # 缩量也ok (洗盘)
    else:
        s_52 = 0

    s5 = s_51 + s_52
    details["资金_OBV趋势%"] = round(obv_slope, 2) if pd.notna(obv_slope) else None
    details["资金_当日量比5均"] = round(VOL / vol_ma5, 2) if vol_ma5 > 0 else None
    details["资金_得分"] = s5
    total += s5

    # ===== 6. 资金流制度调整 (Regime Switch) =====
    regime, regime_info = detect_regime(df)
    regime_adj = REGIME_SCORE_ADJUST.get(regime, 0)
    total_adj = max(0, min(100, total + regime_adj))  # clip 0-100
    details["regime"] = regime
    details["regime_调整"] = regime_adj
    details["regime_量能趋势"] = regime_info.get("vol_trend")
    details["regime_RSI"] = regime_info.get("rsi")
    details["regime_20日涨幅%"] = regime_info.get("ret_20d")
    details["regime_ma5斜率%"] = regime_info.get("ma5_slope")
    details["原分"] = round(total, 1)
    details["总分"] = round(total_adj, 1)
    return total_adj, details


# =========================================================
# 主流程: 读取股池 → 计算 → 输出
# =========================================================
def process_pool(csv_path: str,
                 start_date: str = "20240101",
                 min_score: float = 70.0,
                 top_n: int = 30) -> pd.DataFrame:
    """处理股池, 输出评分结果

    Args:
        csv_path: bull_stocks_qualified.csv 路径
        start_date: 加载K线的起始日期
        min_score: 推荐买入的最低分数
        top_n: 输出前N只

    Returns:
        评分结果 DataFrame (按分数降序)
    """
    pool = pd.read_csv(csv_path)
    print(f"[Pool] 加载股池: {len(pool)} 只股票")
    print(f"[Pool] 列: {list(pool.columns)[:10]}...")

    results = []
    t0 = time.time()
    n_ok, n_fail, n_skip = 0, 0, 0

    for i, row in pool.iterrows():
        code = row["code"]
        name = row.get("name", "")
        ts_code = code_to_ts_code(code)

        # 加载K线
        df = load_kline(ts_code, start_date=start_date)
        if df.empty or len(df) < 30:
            n_fail += 1
            continue

        # 添加指标
        try:
            df = add_indicators(df)
        except Exception as e:
            n_fail += 1
            continue

        # 评分
        try:
            score, details = score_second_filter(df)
        except Exception as e:
            n_fail += 1
            continue

        if score <= 0:
            n_skip += 1
            continue

        n_ok += 1
        result = {
            "ts_code": ts_code,
            "code": code,
            "name": name,
            "score": score,
            "trade_date": df.iloc[-1]["trade_date"],
            "close": df.iloc[-1]["close"],
        }
        # 合并股池的关键列
        for col in ["最终分", "等级", "龙头类型", "theme", "industry", "涨停次数"]:
            if col in row.index:
                result[col] = row[col]
        # 合并评分明细
        result.update(details)
        results.append(result)

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(pool)}] 已评分 {n_ok} 只, 失败 {n_fail}, 跳过 {n_skip}, 耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    print(f"\n[Done] 评分 {n_ok} 只, 失败 {n_fail}, 跳过 {n_skip}, 总耗时 {elapsed:.1f}s")

    if not results:
        print("[WARN] 无有效结果")
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)

    # 标记推荐
    out["推荐"] = np.where(out["score"] >= min_score, "★推荐", "")
    n_recommend = (out["score"] >= min_score).sum()
    print(f"[Recommend] >= {min_score} 分: {n_recommend} 只")

    # 打印 TOP N
    print("\n" + "=" * 120)
    print(f"  TOP {min(top_n, len(out))} 推荐次日开盘买入 (按评分降序)")
    print("=" * 120)
    head_cols = ["score", "推荐", "code", "name", "trade_date", "close",
                 "量价_得分", "均线_得分", "动量_得分", "波动_得分", "资金_得分",
                 "量价_涨幅%", "量价_量比", "动量_KDJ", "动量_RSI6"]
    head_cols = [c for c in head_cols if c in out.columns]
    print(out[head_cols].head(top_n).to_string(index=False))

    return out


# =========================================================
# 回测引擎: 次日开盘买入, 持有 N 天收盘卖出
# =========================================================
class SecondFilterBacktester:
    """二次过滤算法历史回测

    流程:
      1. 预加载股池所有股票 + 预计算指标 (一次)
      2. 遍历回测区间每个交易日 T
      3. (可选) 第一层: strategy 函数过滤 (XH 信号)
      4. 第二层: 二次过滤评分 >= min_score
      5. 模拟 T+1 开盘买入, T+1+N 收盘卖出
      6. 统计胜率/盈亏比/平均收益
    """

    def __init__(self, pool_csv: str,
                 start_date: str = "20250101",
                 end_date: str = None,
                 lookback_days: int = 400,
                 use_strategy_filter: bool = False):
        from datetime import datetime, timedelta
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.use_strategy_filter = use_strategy_filter
        self.strategy_filters = StrategyFilters()

        # 加载股池
        self.pool = pd.read_csv(pool_csv)
        print(f"[Pool] 股池: {len(self.pool)} 只, strategy 过滤: {use_strategy_filter}")

        # 预加载 K 线 + 指标
        self.kline_dict: Dict[str, pd.DataFrame] = {}
        self._date_idx_map: Dict[str, Dict[str, int]] = {}
        self._strategy_signals: Dict[str, np.ndarray] = {}  # ts_code -> 信号向量
        self._load_all_klines(lookback_days)

        # 回测交易日
        all_dates = set()
        for df in self.kline_dict.values():
            all_dates.update(df["trade_date"].tolist())
        self.trade_dates = sorted([d for d in all_dates
                                    if self.start_date <= d <= self.end_date])
        print(f"[Backtest] 区间: {self.start_date} ~ {self.end_date}, "
              f"交易日: {len(self.trade_dates)}")

    def _load_all_klines(self, lookback_days: int):
        from datetime import datetime, timedelta
        dt = datetime.strptime(self.start_date, "%Y%m%d")
        load_start = (dt - timedelta(days=lookback_days)).strftime("%Y%m%d")

        t0 = time.time()
        n_ok, n_fail = 0, 0
        for i, row in self.pool.iterrows():
            ts_code = code_to_ts_code(row["code"])
            df = load_kline(ts_code, start_date=load_start, end_date=self.end_date)
            if df.empty or len(df) < 30:
                n_fail += 1
                continue
            try:
                # 同时计算两套指标: second_filter 用 add_indicators, strategy 用 precompute
                df = add_indicators(df)
                if self.use_strategy_filter:
                    df = precompute_indicators(df)
                    sig = strategy_vectorized(df, ts_code, self.strategy_filters)
                    self._strategy_signals[ts_code] = sig
            except Exception:
                n_fail += 1
                continue
            # 涨停阈值 (T+1 买入时检查)
            if ts_code.startswith(("3", "688", "689")):
                df["_zt_up"] = 1.198
            else:
                df["_zt_up"] = 1.098
            self.kline_dict[ts_code] = df
            self._date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
            n_ok += 1

            if (i + 1) % 200 == 0:
                print(f"  [{i+1}/{len(self.pool)}] 已加载 {n_ok} 只, 失败 {n_fail}, "
                      f"耗时 {time.time()-t0:.1f}s")

        print(f"[Load] 加载 {n_ok} 只, 失败 {n_fail}, 总耗时 {time.time()-t0:.1f}s")

    def run_single_day(self, trade_date: str, min_score: float = 70.0,
                       top_n: int = None
                       ) -> List[Tuple[str, float]]:
        """单日选股: (可选)strategy 过滤 → 二次过滤评分

        Args:
            trade_date: 交易日
            min_score: 最低分数
            top_n: 每日最多选 N 只 (按分数降序), None=不限
        """
        selected = []
        for ts_code, df in self.kline_dict.items():
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None or i < 30:
                continue
            # 第一层: strategy 过滤 (XH 信号)
            if self.use_strategy_filter:
                sig = self._strategy_signals.get(ts_code)
                if sig is None or i >= len(sig) or not sig[i]:
                    continue
            # 第二层: 二次过滤评分
            df_slice = df.iloc[: i + 1]
            try:
                score, _ = score_second_filter(df_slice)
            except Exception:
                continue
            if score >= min_score:
                selected.append((ts_code, score))
        # 按分数降序, 取 top_n
        if top_n and len(selected) > top_n:
            selected.sort(key=lambda x: -x[1])
            selected = selected[:top_n]
        return selected

    def evaluate_signals(self, selected: List[Tuple[str, float]],
                         trade_date: str, hold_days: int = 5
                         ) -> List[Dict]:
        """评估信号: T+1 开盘买入, T+1+N 收盘卖出

        Returns:
            交易记录列表 [{ts_code, score, signal_date, buy_date, buy_price,
                          sell_date, sell_price, return, regime, ...}]
        """
        records = []
        for ts_code, score in selected:
            df = self.kline_dict.get(ts_code)
            if df is None:
                continue
            idx_map = self._date_idx_map[ts_code]
            i = idx_map.get(trade_date)
            if i is None:
                continue

            # T+1 买入
            buy_idx = i + 1
            if buy_idx >= len(df):
                continue
            zt_up = df.iloc[buy_idx]["_zt_up"]
            # T+1 当天如果一字涨停或开盘即涨停, 无法买入
            buy_row = df.iloc[buy_idx]
            # 用前一日收盘 * zt_up 判断 T+1 是否涨停
            prev_close = df.iloc[i]["close"]
            if buy_row["open"] >= prev_close * zt_up * 0.999:
                # 开盘即涨停, 无法买入
                continue

            buy_price = buy_row["open"]
            buy_date = buy_row["trade_date"]

            # T+1+N 卖出 (收盘价)
            sell_idx = min(buy_idx + hold_days, len(df) - 1)
            # 如果卖出日涨停, 顺延一天 (可选)
            sell_row = df.iloc[sell_idx]
            sell_price = sell_row["close"]
            sell_date = sell_row["trade_date"]

            # 记录 regime (信号日)
            df_slice = df.iloc[: i + 1]
            regime, _ = detect_regime(df_slice)

            ret = (sell_price / buy_price - 1) * 100
            records.append({
                "ts_code": ts_code,
                "score": score,
                "signal_date": trade_date,
                "buy_date": buy_date,
                "buy_price": round(buy_price, 2),
                "sell_date": sell_date,
                "sell_price": round(sell_price, 2),
                "hold_days": sell_idx - buy_idx,
                "return": round(ret, 2),
                "regime": regime,
            })
        return records

    def run_backtest(self, min_score: float = 70.0, hold_days: int = 5,
                     top_n: int = None, verbose: bool = True) -> Dict:
        """完整回测: 遍历所有交易日"""
        daily_counts = []
        all_returns = []
        trade_records = []

        t0 = time.time()
        for i, td in enumerate(self.trade_dates):
            selected = self.run_single_day(td, min_score, top_n=top_n)
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
                      f"累计 {len(all_returns)} 笔, 耗时 {elapsed:.1f}s, ETA {eta:.0f}s")

        # 统计
        all_returns_arr = np.array(all_returns) if all_returns else np.array([0])
        win_rate = (all_returns_arr > 0).mean() * 100 if len(all_returns) > 0 else 0
        avg_ret = all_returns_arr.mean() if len(all_returns) > 0 else 0
        med_ret = np.median(all_returns_arr) if len(all_returns) > 0 else 0

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


# =========================================================
# 主入口
# =========================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="技术指标二次过滤 - 次日开盘买入选股")
    parser.add_argument("--pool", type=str,
                        default=r"D:\mystock\solo\report_daily\bull_stocks_qualified.csv",
                        help="股池CSV路径")
    parser.add_argument("--start", type=str, default="20240101",
                        help="K线起始日期")
    parser.add_argument("--min-score", type=float, default=70.0,
                        help="推荐买入最低分数")
    parser.add_argument("--top", type=int, default=30,
                        help="打印前N只")
    parser.add_argument("--out", type=str, default=None,
                        help="输出CSV路径 (默认: 股池同目录 second_filter_result.csv)")
    parser.add_argument("--backtest", action="store_true",
                        help="运行回测模式 (遍历历史交易日)")
    parser.add_argument("--end", type=str, default=None, help="回测结束日")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--top-n", type=int, default=None,
                        help="每日最多选N只 (按分数降序), 默认不限")
    parser.add_argument("--strategy-filter", action="store_true",
                        help="启用 strategy 第一层过滤 (XH 信号)")
    args = parser.parse_args()

    if args.backtest:
        # 回测模式
        bt = SecondFilterBacktester(
            pool_csv=args.pool,
            start_date=args.start,
            end_date=args.end,
            use_strategy_filter=args.strategy_filter,
        )
        res = bt.run_backtest(min_score=args.min_score, hold_days=args.hold,
                              top_n=args.top_n)

        print("\n" + "=" * 70)
        print("  二次过滤回测结果 (次日开盘买入)")
        print("=" * 70)
        print(f"  回测区间:     {args.start} ~ {args.end or '最新'}")
        print(f"  交易日数:     {res['n_total_days']}")
        print(f"  最低分数:     {args.min_score}")
        print(f"  持有天数:     {args.hold}")
        print(f"  总信号数:     {res['n_signals']}")
        print(f"  胜率:         {res['win_rate']}%")
        print(f"  平均收益:     {res['avg_return']}%")
        print(f"  中位收益:     {res['median_return']}%")
        if res['n_signals'] > 0:
            rets = np.array(res['all_returns'])
            print(f"  最大盈利:     {rets.max():.2f}%")
            print(f"  最大亏损:     {rets.min():.2f}%")
            print(f"  盈亏比:       {abs(rets[rets>0].mean()/rets[rets<0].mean()):.2f}")
        print(f"  日均选股数:   {np.mean(res['daily_counts']):.1f}")
        print(f"  选股1-5只天数: {res['n_days_1_5']}/{res['n_total_days']} "
              f"({res['n_days_1_5']/res['n_total_days']*100:.1f}%)")

        # 分数档位胜率对比
        if res.get("trade_records"):
            print("\n  分数档位胜率对比:")
            recs = res["trade_records"]
            for lo, hi in [(70,75),(75,80),(80,85),(85,90),(90,100)]:
                sub = [r["return"] for r in recs if lo <= r["score"] < hi]
                if sub:
                    wr = sum(1 for x in sub if x > 0) / len(sub) * 100
                    avg = np.mean(sub)
                    print(f"    {lo}-{hi}分: {len(sub)}笔, 胜率{wr:.1f}%, 均收益{avg:.2f}%")

            # Regime 分布胜率
            print("\n  资金流制度 (Regime) 分布:")
            regime_groups: Dict[str, list] = {}
            for r in recs:
                rg = r.get("regime", "unknown")
                regime_groups.setdefault(rg, []).append(r["return"])
            regime_order = [REGIME_EARLY_TREND, REGIME_MID_EXPANSION,
                            REGIME_CLIMAX_DISTRIB, REGIME_ROTATION_EXIT]
            for rg in regime_order + [k for k in regime_groups if k not in regime_order]:
                rets = regime_groups.get(rg, [])
                if rets:
                    wr = sum(1 for x in rets if x > 0) / len(rets) * 100
                    avg = np.mean(rets)
                    pf = (abs(np.mean([x for x in rets if x > 0]) /
                              np.mean([x for x in rets if x < 0]))
                          if any(x < 0 for x in rets) else float("inf"))
                    print(f"    {rg:22s}: {len(rets):4d}笔, 胜率{wr:5.1f}%, "
                          f"均收益{avg:6.2f}%, 盈亏比{pf:.2f}")

        # 保存交易记录
        if res.get("trade_records"):
            out_path = os.path.join(os.path.dirname(args.pool),
                                     "second_filter_backtest_trades.csv")
            pd.DataFrame(res["trade_records"]).to_csv(
                out_path, index=False, encoding="utf-8-sig")
            print(f"\n  [交易记录已保存] {out_path}")
    else:
        # 单日评分模式
        out_df = process_pool(args.pool, start_date=args.start,
                              min_score=args.min_score, top_n=args.top)

        if not out_df.empty:
            out_path = args.out or os.path.join(os.path.dirname(args.pool),
                                                 "second_filter_result.csv")
            out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[Output] 结果已保存: {out_path}")

            # 分数分布
            print("\n  评分分布:")
            bins = [0, 50, 60, 70, 80, 90, 100]
            for i in range(len(bins) - 1):
                n = ((out_df["score"] >= bins[i]) & (out_df["score"] < bins[i+1])).sum()
                print(f"    {bins[i]}-{bins[i+1]}分: {n} 只")


if __name__ == "__main__":
    main()
