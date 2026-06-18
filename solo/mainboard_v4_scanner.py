# -*- coding: utf-8 -*-
"""
主板中军龙头识别 v4
================================================

核心思路：完全摆脱 theme_score，基于个股自身的价格结构判断
评分维度：
    A. 中军基础分（市值 + 成交容量）—— 能容纳大资金
    B. 历史辨识度分（第一波涨幅 + 涨停次数）—— 已建立市场认知
    C. 价值健康分（回撤 + 乖离 + 累计涨幅）—— 没有价值透支
    D. 趋势结构分（均线多头 + 斜率 + 量比）—— 二波潜力

关键特征要识别的是：
    "已完成第一波主升，经过健康洗盘/整理，长期趋势未破坏，即将开启二波"
而不是：
    "短期爆拉情绪高位股" 或 "连续涨停妖股"

运行: python mainboard_v4_scanner.py
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_TUSHARE = os.path.join(BASE_DIR, "cache_backbone_tushare")
KLINE_CACHE = os.path.join(BASE_DIR, "cache_daily")

PORTFOLIO_DB = os.path.join(CACHE_TUSHARE, "theme_portfolio.db")
THEME_SCORE_DB = os.path.join(CACHE_TUSHARE, "theme_trend_sentiment.db")


def is_mainboard(code):
    """判断是否主板/双创（SH:60, SZ:000,001,002,003 + 科创板688 + 创业板30）"""
    if code.startswith("60") or code.startswith("68"):
        return True
    if code.startswith("00") or code.startswith("30"):
        return True
    return False


def get_latest_trade_date():
    """获取最近交易日"""
    try:
        conn = sqlite3.connect(THEME_SCORE_DB)
        c = conn.cursor()
        c.execute("SELECT MAX(trade_date) FROM theme_scores")
        result = c.fetchone()[0]
        conn.close()
        if result:
            # 统一成 YYYY-MM-DD
            if len(str(result)) == 8:
                return f"{str(result)[:4]}-{str(result)[4:6]}-{str(result)[6:8]}"
            return str(result)
    except Exception:
        pass
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def load_stock_pool():
    """
    从 portfolio 表加载股票池
    去重（一只股票可能在多个主题），取市值最大的那条记录
    """
    print("[1/5] 加载股票池...")
    conn = sqlite3.connect(PORTFOLIO_DB)
    c = conn.cursor()
    c.execute("SELECT ts_code, name, theme_name, layer, mcap, turnover, purity FROM portfolio")
    rows = c.fetchall()
    conn.close()

    # 去重：一只股票出现多次，取第一条（主题可能不同，但不影响评分）
    stock_map = {}
    theme_map = {}  # code -> 第一个主题
    for r in rows:
        code = r[0]
        if code not in stock_map:
            stock_map[code] = {
                "ts_code": code,
                "name": r[1],
                "market_cap_yi": float(r[4]) if r[4] else 0,
                "turnover_yi": float(r[5]) if r[5] else 0,
                "layer": r[3] if r[3] else "",
            }
            theme_map[code] = r[2] if r[2] else ""

    print(f"  原始 {len(rows)} 条，去重后 {len(stock_map)} 只股票")
    return stock_map, theme_map


def load_kline_data(ts_code):
    """加载个股K线数据"""
    file_path = os.path.join(KLINE_CACHE, f"{ts_code}.csv")
    if not os.path.exists(file_path):
        return None
    try:
        df = pd.read_csv(file_path)
        if df.empty or len(df) < 60:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        return df
    except Exception:
        return None


def sma(arr, n):
    """简单移动平均"""
    if len(arr) < n:
        return np.zeros(len(arr))
    result = np.zeros(len(arr))
    for i in range(n - 1):
        result[i] = np.mean(arr[: i + 1])
    result[n - 1 :] = np.convolve(arr, np.ones(n) / n, mode="valid")
    return result


def slope_n(arr, n):
    """计算n日斜率（单位：%/日，归一化）"""
    if len(arr) < n + 5:
        return 0.0
    recent = arr[max(0, len(arr) - n - 5) :]
    x = np.arange(len(recent))
    try:
        s = np.polyfit(x, recent, 1)[0]
        return s / np.mean(recent) * 100 if np.mean(recent) > 0 else 0
    except Exception:
        return 0.0


def compute_stock_features(ts_code, name, theme, market_cap_yi, trade_date):
    """
    计算个股核心结构特征
    完全不依赖 theme_score
    """
    kline = load_kline_data(ts_code)
    if kline is None:
        return None

    last = len(kline) - 1
    if last < 60:
        return None

    close = kline["close"].values
    high = kline["high"].values
    low = kline["low"].values
    amount = kline["amount"].values

    current_price = close[last]

    # ============ 1. 涨幅统计 ============
    ret_5 = (close[last] / close[max(0, last - 4)] - 1) * 100 if last >= 4 else 0
    ret_20 = (close[last] / close[max(0, last - 19)] - 1) * 100 if last >= 19 else 0
    ret_60 = (close[last] / close[max(0, last - 59)] - 1) * 100 if last >= 59 else 0
    ret_120 = (close[last] / close[max(0, last - 119)] - 1) * 100 if last >= 119 else 0

    # ============ 2. 均线 ============
    ma5 = sma(close, 5)[last]
    ma10 = sma(close, 10)[last]
    ma20 = sma(close, 20)[last]
    ma60 = sma(close, 60)[last]
    ma120 = sma(close, 120)[last] if last >= 119 else ma60

    # ============ 3. 乖离率 ============
    bias_ma20 = (current_price / ma20 - 1) * 100 if ma20 > 0 else 0
    bias_ma60 = (current_price / ma60 - 1) * 100 if ma60 > 0 else 0

    # ============ 4. 斜率 ============
    slope_20 = round(slope_n(close, 20), 2)
    slope_60 = round(slope_n(close, 60), 2)

    # ============ 5. 均线多头评分 ============
    bull_score = 0
    if ma10 > ma20:
        bull_score += 20
    if ma20 > ma60:
        bull_score += 20
    if current_price > ma20:
        bull_score += 20
    if slope_20 > 0:
        bull_score += 20
    if ma5 > ma10:
        bull_score += 20

    # ============ 6. 第一波高度（过去120日最高点到当前的回撤）============
    if last >= 119:
        high_120 = np.max(high[last - 119 : last + 1])
        high_60 = np.max(high[last - 59 : last + 1])
    else:
        high_120 = np.max(high[: last + 1])
        high_60 = np.max(high[max(0, last - 59) : last + 1])

    drawdown_from_high_120 = (current_price / high_120 - 1) * 100  # 负值
    drawdown_from_high_60 = (current_price / high_60 - 1) * 100

    # 120日最大回撤（过程中的最大回撤幅度）
    if last >= 119:
        prices_120 = close[last - 119 : last + 1]
        peak_120 = np.maximum.accumulate(prices_120)
        dd_120 = (prices_120 - peak_120) / peak_120 * 100
        max_drawdown_120 = round(np.min(dd_120), 1)
    else:
        max_drawdown_120 = 0

    # 60日最大回撤
    if last >= 59:
        prices_60 = close[last - 59 : last + 1]
        peak_60 = np.maximum.accumulate(prices_60)
        dd_60 = (prices_60 - peak_60) / peak_60 * 100
        max_drawdown_60 = round(np.min(dd_60), 1)
    else:
        max_drawdown_60 = 0

    # ============ 7. 成交额（amount单位：千元 → 亿元需/1e5）============
    avg_amount_20d = float(np.mean(amount[max(0, last - 19) : last + 1])) / 1e5 if len(amount) > 20 else 0
    avg_amount_60d = float(np.mean(amount[max(0, last - 59) : last + 1])) / 1e5 if len(amount) > 60 else 0
    amount_ratio = avg_amount_20d / avg_amount_60d if avg_amount_60d > 0 else 1.0

    # ============ 8. 涨停次数（120日） ============
    limit_up_count_120 = 0
    for i in range(max(0, last - 119), last + 1):
        if i > 0 and close[i] >= close[i - 1] * 1.097:
            limit_up_count_120 += 1

    # ============ 9. 波动率 ============
    if last >= 59:
        returns_60 = [(close[i] / close[i - 1] - 1) * 100 for i in range(last - 59, last + 1) if i > 0]
        volatility_60 = round(float(np.std(returns_60)), 2)
    else:
        volatility_60 = 0

    # ============ 10. 整理结构分析（关键创新点） ============
    # 1) 高点出现位置：120日内最高点发生在多久之前？(越靠前越好，证明是在整理)
    if last >= 119:
        high_idx_120 = np.argmax(high[last - 119 : last + 1])  # 0-119
        days_since_high_120 = 119 - high_idx_120  # 高点距今多少天
    else:
        days_since_high_120 = 0

    # 2) 近20日振幅 vs 120日振幅（缩量整理=振幅收窄）
    if last >= 119:
        range_20 = (np.max(high[last - 19 : last + 1]) - np.min(low[last - 19 : last + 1])) / np.min(low[last - 19 : last + 1]) * 100
        range_120 = (np.max(high[last - 119 : last + 1]) - np.min(low[last - 119 : last + 1])) / np.min(low[last - 119 : last + 1]) * 100
        range_ratio = range_20 / range_120 if range_120 > 0 else 1.0
    else:
        range_ratio = 1.0

    feat = {
        "ts_code": ts_code,
        "name": name,
        "theme": theme,
        "market_cap_yi": round(float(market_cap_yi), 0),
        "avg_amount_20d_yi": round(avg_amount_20d, 2),
        "avg_amount_60d_yi": round(avg_amount_60d, 2),
        "amount_ratio": round(amount_ratio, 2),

        "current_price": round(float(current_price), 2),
        "ret_5": round(float(ret_5), 1),
        "ret_20": round(float(ret_20), 1),
        "ret_60": round(float(ret_60), 1),
        "ret_120": round(float(ret_120), 1),

        "ma5": round(float(ma5), 2),
        "ma10": round(float(ma10), 2),
        "ma20": round(float(ma20), 2),
        "ma60": round(float(ma60), 2),
        "ma120": round(float(ma120), 2),

        "bias_ma20": round(bias_ma20, 2),
        "bias_ma60": round(bias_ma60, 2),
        "slope_20": slope_20,
        "slope_60": slope_60,
        "bull_score": bull_score,

        "drawdown_from_high_120": round(drawdown_from_high_120, 1),
        "drawdown_from_high_60": round(drawdown_from_high_60, 1),
        "max_drawdown_60d": max_drawdown_60,
        "max_drawdown_120d": max_drawdown_120,

        "days_since_high_120": int(days_since_high_120),
        "range_ratio_20_120": round(range_ratio, 3),

        "volatility_60": volatility_60,
        "limit_up_count_120": limit_up_count_120,
    }

    return feat


# ============================================================
# 评分函数
# ============================================================

def score_a_core_size(feat):
    """
    A. 中军基础分（市值 + 成交容量）
    逻辑：中军必须能容纳大资金进出
    - 市值 300-3000 亿最佳
    - 20日均成交 10-100 亿最佳
    """
    mc = feat["market_cap_yi"]
    amt = feat["avg_amount_20d_yi"]

    # 市值评分（钟形，太小=小票，太大=大象难涨）
    if mc <= 0:
        mc_score = 0
    elif mc < 100:
        mc_score = 20 + mc * 0.5
    elif mc < 300:
        mc_score = 50 + (mc - 100) * 0.25
    elif mc < 1500:
        mc_score = 85 + (mc - 300) * 0.03
    elif mc < 4000:
        mc_score = 90 - (mc - 1500) * 0.01
    else:
        mc_score = max(50, 75 - (mc - 4000) * 0.01)

    # 成交评分（10-100亿最佳，太大会爆量）
    if amt <= 0:
        amt_score = 0
    elif amt < 3:
        amt_score = 20 + amt * 10
    elif amt < 10:
        amt_score = 50 + (amt - 3) * 5
    elif amt < 50:
        amt_score = 85 + (amt - 10) * 0.5
    elif amt < 150:
        amt_score = 95 - (amt - 50) * 0.2
    else:
        amt_score = max(60, 80 - (amt - 150) * 0.1)

    return round(mc_score * 0.55 + amt_score * 0.45, 1)


def score_b_recognition(feat):
    """
    B. 历史辨识度分（证明这只股票"有过行情"）
    - 120日最大涨幅：必须有过显著上涨，证明市场认可
    - 120日涨停次数：3-10次最佳（太少=没行情，太多=妖股）
    - 120日振幅：有足够波动吸引资金关注
    """
    # 120日最高涨幅（从最低点到最高点）
    ret_120 = feat["ret_120"]
    lu = feat["limit_up_count_120"]

    # 120日涨幅评分：30-120%为最佳区间（证明有过行情但未爆拉透支）
    if ret_120 <= 0:
        ret_score = 30  # 连趋势都没有
    elif ret_120 <= 20:
        ret_score = 50 + ret_120 * 1.5
    elif ret_120 <= 80:
        ret_score = 85 + (ret_120 - 20) * 0.25
    elif ret_120 <= 150:
        ret_score = 95 - (ret_120 - 80) * 0.3
    else:
        ret_score = max(40, 75 - (ret_120 - 150) * 0.3)

    # 涨停次数评分（每10%涨幅用多少次涨停）
    if ret_120 > 10:
        lu_density = lu / (ret_120 / 10)  # 每10%涨幅用几个涨停
    else:
        lu_density = lu

    # 涨停密度：越低越好（稳健上涨 vs 涨停爆拉）
    if lu_density <= 0.3:
        density_score = 95
    elif lu_density <= 0.8:
        density_score = 85 - (lu_density - 0.3) * 20
    elif lu_density <= 1.5:
        density_score = 70 - (lu_density - 0.8) * 30
    else:
        density_score = max(30, 55 - (lu_density - 1.5) * 10)

    # 绝对涨停次数：3-10次为最佳（证明有市场关注度但不是妖股）
    if lu <= 0:
        lu_score = 30
    elif lu <= 3:
        lu_score = 50 + lu * 10
    elif lu <= 8:
        lu_score = 80 + (lu - 3) * 3
    elif lu <= 15:
        lu_score = 90 - (lu - 8) * 2
    else:
        lu_score = max(40, 75 - (lu - 15) * 3)

    return round(ret_score * 0.45 + density_score * 0.25 + lu_score * 0.30, 1)


def score_c_value_health(feat):
    """
    C. 价值健康分（当前不在高位，有健康调整）
    关键指标：
    - 从120日高点回撤：10-30%为最佳（有洗盘但不破位）
    - 相对MA20乖离：越小越好
    - 高点距今天数：越高点发生在40-80日前最佳（给了洗盘时间）
    - 20日/120日振幅比：<0.5为最佳（缩量整理）
    """
    dd_120 = feat["drawdown_from_high_120"]  # 负值
    bias = abs(feat["bias_ma20"])
    days_since = feat["days_since_high_120"]
    range_ratio = feat["range_ratio_20_120"]

    # 回撤评分（从高点回撤 10-30%为最佳）
    dd = -dd_120  # 转为正值
    if dd <= 5:
        dd_score = 40  # 基本没回撤，仍在高位
    elif dd <= 10:
        dd_score = 60 + (dd - 5) * 6
    elif dd <= 25:
        dd_score = 90 + (dd - 10) * 0.67
    elif dd <= 40:
        dd_score = 95 - (dd - 25) * 2
    else:
        dd_score = max(30, 65 - (dd - 40) * 2)

    # 乖离率评分
    if bias <= 5:
        bias_score = 100
    elif bias <= 10:
        bias_score = 90 - (bias - 5) * 2
    elif bias <= 20:
        bias_score = 75 - (bias - 10) * 2
    else:
        bias_score = max(30, 55 - (bias - 20) * 1.5)

    # 高点距今天数评分（高点发生在 30-90 日前最佳，有充分洗盘时间）
    if days_since <= 10:
        days_score = 30  # 高点就在最近，还没洗盘
    elif days_since <= 30:
        days_score = 50 + (days_since - 10) * 2
    elif days_since <= 60:
        days_score = 90 + (days_since - 30) * 0.33
    elif days_since <= 100:
        days_score = 95 - (days_since - 60) * 0.5
    else:
        days_score = max(50, 75 - (days_since - 100) * 0.5)

    # 振幅收窄评分（近20日振幅相对于120日振幅收窄=整理）
    if range_ratio <= 0.2:
        range_score = 80  # 太窄可能没活力
    elif range_ratio <= 0.4:
        range_score = 95 + (range_ratio - 0.2) * 25
    elif range_ratio <= 0.7:
        range_score = 100 - (range_ratio - 0.4) * 50
    else:
        range_score = max(40, 85 - (range_ratio - 0.7) * 80)

    return round(
        0.35 * dd_score
        + 0.25 * bias_score
        + 0.20 * days_score
        + 0.20 * range_score,
        1,
    )


def score_d_trend_structure(feat):
    """
    D. 趋势结构分（长期趋势未破坏，可能开启二波）
    - 均线多头排列
    - 中长期斜率仍向上
    - 量比健康（成交既不爆量也不枯竭）
    - 价格在MA20上方但不远离
    """
    bull = feat["bull_score"]
    slope_60 = feat["slope_60"]
    slope_20 = feat["slope_20"]
    amt_ratio = feat["amount_ratio"]
    bias = abs(feat["bias_ma20"])

    # 均线多头评分
    bull_score = bull  # 0-100

    # 60日斜率评分（缓慢向上最佳，0.2-1.0 %/日）
    if slope_60 <= 0:
        slope60_score = 40  # 长期趋势向下
    elif slope_60 <= 0.2:
        slope60_score = 70 + slope_60 * 100
    elif slope_60 <= 0.8:
        slope60_score = 90 + (slope_60 - 0.2) * 16.7
    elif slope_60 <= 1.5:
        slope60_score = 95 - (slope_60 - 0.8) * 20
    else:
        slope60_score = max(40, 80 - (slope_60 - 1.5) * 30)

    # 20日斜率评分（不能太陡）
    if slope_20 <= 0:
        slope20_score = 50
    elif slope_20 <= 0.5:
        slope20_score = 85 + slope_20 * 20
    elif slope_20 <= 1.5:
        slope20_score = 95 - (slope_20 - 0.5) * 15
    else:
        slope20_score = max(40, 80 - (slope_20 - 1.5) * 25)

    # 量比评分（成交温和，0.8-1.2最佳）
    if amt_ratio <= 0.5:
        amt_score = 50
    elif amt_ratio <= 0.8:
        amt_score = 75 + (amt_ratio - 0.5) * 83
    elif amt_ratio <= 1.3:
        amt_score = 100 - (amt_ratio - 0.8) * 20
    elif amt_ratio <= 2.0:
        amt_score = 90 - (amt_ratio - 1.3) * 30
    else:
        amt_score = max(40, 70 - (amt_ratio - 2.0) * 20)

    # 乖离控制（加分项）
    if bias <= 8:
        control_score = 100
    elif bias <= 15:
        control_score = 90 - (bias - 8) * 1.4
    else:
        control_score = max(40, 75 - (bias - 15) * 2)

    return round(
        0.30 * bull_score
        + 0.25 * slope60_score
        + 0.15 * slope20_score
        + 0.15 * amt_score
        + 0.15 * control_score,
        1,
    )


def judge_rating(ultimate_score, value_health):
    """评级：综合终极分和价值健康分（更严格的门槛）"""
    if ultimate_score >= 90 and value_health >= 80:
        return "S+"
    elif ultimate_score >= 85 and value_health >= 75:
        return "S"
    elif ultimate_score >= 80 and value_health >= 70:
        return "A"
    elif ultimate_score >= 75:
        return "B"
    else:
        return "C"


def get_stage_desc(feat):
    """判断个股所处阶段（用于理解）"""
    dd = feat["drawdown_from_high_120"]
    bull = feat["bull_score"]
    slope = feat["slope_20"]
    bias = feat["bias_ma20"]

    if dd >= -5 and bull >= 80 and slope > 0.5:
        return "主升期"
    elif dd >= -15 and bull >= 60:
        return "强势整理"
    elif dd >= -30 and bull >= 40:
        return "健康洗盘"
    elif dd < -30 or bull < 40 or slope < 0:
        return "趋势走弱"
    else:
        return "平台震荡"


def main():
    trade_date = get_latest_trade_date()
    print(f"分析日期: {trade_date}\n")
    print("=" * 80)
    print("【v4 算法说明】")
    print("  完全不使用 theme_score，仅基于个股自身价格结构")
    print("  目标：识别经过健康洗盘、具备二波潜力的中军龙头")
    print("  评分维度：中军基础(A) + 历史辨识度(B) + 价值健康(C) + 趋势结构(D)")
    print("=" * 80)

    stock_pool, theme_map = load_stock_pool()

    print("[2/5] 计算个股结构特征...")

    results = []
    processed = 0

    for code, info in stock_pool.items():
        if not is_mainboard(code):
            continue

        feat = compute_stock_features(
            code,
            info["name"],
            theme_map.get(code, ""),
            info["market_cap_yi"],
            trade_date,
        )
        if feat is None:
            continue
        processed += 1

        # 最低门槛（非常基础，只排除明显不行的）
        mc = feat["market_cap_yi"]
        amt = feat["avg_amount_20d_yi"]

        # 硬过滤：市值太小或太大、成交过低
        if mc < 80 or mc > 6000:
            continue
        if amt < 1.5:
            continue
        if feat["bull_score"] < 20:  # 至少有1项多头条件满足（非常宽松）
            continue

        # 四维评分
        a = min(100, score_a_core_size(feat))
        b = min(100, score_b_recognition(feat))
        c = min(100, score_c_value_health(feat))
        d = min(100, score_d_trend_structure(feat))

        # 终极评分（权重：价值健康最高，因为要避免追高）
        ultimate = round(0.20 * a + 0.20 * b + 0.32 * c + 0.28 * d, 1)

        rating = judge_rating(ultimate, c)
        stage = get_stage_desc(feat)

        result = {
            **feat,
            "score_a_core": a,
            "score_b_recognition": b,
            "score_c_value_health": c,
            "score_d_trend": d,
            "ultimate_score": ultimate,
            "rating": rating,
            "stage": stage,
        }
        results.append(result)

    print(f"  完成特征计算：{processed} 只，通过硬过滤：{len(results)} 只")

    # 排序
    results.sort(key=lambda x: x["ultimate_score"], reverse=True)

    # ============ 输出 ============
    print(f"\n[3/5] Top 30 中军候选名单:")
    print("=" * 200)
    header = (
        f"{'排名':<4}{'代码':<12}{'名称':<10}{'主题':<16}{'评级':<4}{'阶段':<10}"
        f"{'终极':<7}{'A中军':<8}{'B辨识':<8}{'C价值':<8}{'D趋势':<8}"
        f"{'高点回撤':<10}{'60日涨':<10}{'120日涨':<10}"
        f"{'MA20乖':<8}{'涨停':<6}{'市值亿':<10}"
    )
    print(header)
    print("-" * 200)
    for i, r in enumerate(results[:30], 1):
        print(
            f"{i:<4}{r['ts_code']:<12}{r['name']:<10}{r['theme']:<16}{r['rating']:<4}{r['stage']:<10}"
            f"{r['ultimate_score']:<7.1f}{r['score_a_core']:<8.1f}{r['score_b_recognition']:<8.1f}"
            f"{r['score_c_value_health']:<8.1f}{r['score_d_trend']:<8.1f}"
            f"{r['drawdown_from_high_120']:<10.1f}{r['ret_60']:<10.1f}{r['ret_120']:<10.1f}"
            f"{r['bias_ma20']:<8.1f}{r['limit_up_count_120']:<6}{r['market_cap_yi']:<10.0f}"
        )
    print("=" * 200)

    # 评级分布
    print("\n[4/5] 评级分布:")
    rating_counts = Counter(r["rating"] for r in results)
    for rating in ["S+", "S", "A", "B", "C"]:
        count = rating_counts.get(rating, 0)
        if count > 0:
            names = [r["name"] for r in results if r["rating"] == rating][:10]
            print(f"  {rating}级: {count} 只 -> {', '.join(names)}")

    # 主题分布（Top 50中各主题占比，验证是否不再被半导体垄断）
    print("\n[5/5] Top 50 主题分布（验证算法不被单一主题垄断）:")
    theme_counts = Counter(r["theme"] for r in results[:50])
    for theme, count in theme_counts.most_common(15):
        names = [r["name"] for r in results[:50] if r["theme"] == theme][:5]
        print(f"  {theme}: {count} 只 -> {', '.join(names)}")

    # ============ 保存 ============
    output = {
        "scan_date": trade_date,
        "algorithm": "v4 - 中军基础(A) + 历史辨识度(B) + 价值健康(C) + 趋势结构(D)，完全不依赖主题评分",
        "weights": "A=20%, B=20%, C=32%, D=28%",
        "total_count": len(results),
        "data": results[:150],
    }

    os.makedirs(os.path.join(BASE_DIR, "report_daily"), exist_ok=True)

    json_path = os.path.join(BASE_DIR, "report_daily", f"mainboard_v4_scan_{trade_date.replace('-', '')}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[Save] JSON: {json_path}")

    csv_path = os.path.join(BASE_DIR, "report_daily", f"mainboard_v4_scan_{trade_date.replace('-', '')}.csv")
    df_out = pd.DataFrame(results[:150])
    df_out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[Save] CSV: {csv_path}")

    print("\n[Done] v4 扫描完成！")


if __name__ == "__main__":
    main()
