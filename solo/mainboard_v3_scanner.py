# -*- coding: utf-8 -*-
"""
主板中军龙头识别 v3
================================================

核心思路：完全摆脱 theme_score 的依赖，主题仅用于分组理解
评分维度：
    1. 中军属性 Score（市值 + 成交）
    2. 市场辨识度 Score（涨停 + 热榜 + 龙虎榜 + 主题活跃天数）
    3. 价值余量 Score（乖离 + 涨幅 + 回撤）
    4. 趋势健康 Score（均线多头 + 斜率 + 量比）

运行: python mainboard_v3_scanner.py
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_TUSHARE = os.path.join(BASE_DIR, "cache_backbone_tushare")
KLINE_CACHE = os.path.join(BASE_DIR, "cache_daily")
DC_HOT_DIR = os.path.join(CACHE_TUSHARE, "dc_hot")

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
            return result
    except Exception as e:
        pass
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def load_theme_portfolio():
    """加载主题持仓"""
    print("[1/4] 加载主题持仓数据...")
    conn = sqlite3.connect(PORTFOLIO_DB)
    c = conn.cursor()
    c.execute("SELECT theme, ts_code, name, market_cap_yi, weight FROM theme_portfolio")
    rows = c.fetchall()
    conn.close()

    portfolio = defaultdict(list)
    for row in rows:
        portfolio[row[0]].append({
            "ts_code": row[1],
            "name": row[2],
            "market_cap_yi": row[3] if row[3] else 0,
            "weight": row[4] if row[4] else 0,
        })
    print(f"  加载 {len(portfolio)} 个主题，{sum(len(v) for v in portfolio.values())} 只股票")
    return portfolio


def load_kline_data(ts_code):
    """加载个股K线数据"""
    file_path = os.path.join(KLINE_CACHE, f"{ts_code}.csv")
    if not os.path.exists(file_path):
        return None
    try:
        df = pd.read_csv(file_path)
        if df.empty or len(df) < 30:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        return df
    except Exception:
        return None


def compute_stock_features(ts_code, name, theme, market_cap_yi, trade_date):
    """计算个股核心特征（不依赖 theme_score）"""
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

    # 基础数据
    current_price = close[last]
    ret_5 = (close[last] / close[max(0, last-4)] - 1) * 100 if last >= 4 else 0
    ret_20 = (close[last] / close[max(0, last-19)] - 1) * 100 if last >= 19 else 0
    ret_60 = (close[last] / close[max(0, last-59)] - 1) * 100 if last >= 59 else 0
    ret_120 = (close[last] / close[max(0, last-119)] - 1) * 100 if last >= 119 else 0

    # MA计算
    def sma(arr, n):
        if len(arr) < n:
            return np.zeros(len(arr))
        result = np.zeros(len(arr))
        for i in range(n-1):
            result[i] = np.mean(arr[:i+1])
        result[n-1:] = np.convolve(arr, np.ones(n)/n, mode="valid")
        return result

    ma5 = sma(close, 5)[last]
    ma10 = sma(close, 10)[last]
    ma20 = sma(close, 20)[last]
    ma60 = sma(close, 60)[last]

    # 乖离率
    bias_ma20 = (current_price / ma20 - 1) * 100 if ma20 > 0 else 0
    bias_ma60 = (current_price / ma60 - 1) * 100 if ma60 > 0 else 0

    # MA斜率
    def slope_n(arr, n):
        if len(arr) < n + 5:
            return 0.0
        recent = arr[max(0, len(arr)-n-5): len(arr)]
        x = np.arange(len(recent))
        try:
            s = np.polyfit(x, recent, 1)[0]
            return s / np.mean(recent) * 100 if np.mean(recent) > 0 else 0
        except Exception:
            return 0.0

    slope_20 = round(slope_n(close, 20), 2)
    slope_60 = round(slope_n(close, 60), 2)

    # 均线多头评分（允许MA5 < MA10即洗盘）
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

    # 60日最大回撤
    if last >= 59:
        prices_60 = close[last-59:last+1]
        peak = np.maximum.accumulate(prices_60)
        drawdown = (prices_60 - peak) / peak * 100
        max_drawdown_60 = round(np.min(drawdown), 1)
    else:
        max_drawdown_60 = 0

    # 成交额
    avg_amount_20d = float(np.mean(amount[max(0, last-19):last+1])) / 1e8 if len(amount) > 20 else 0
    avg_amount_60d = float(np.mean(amount[max(0, last-59):last+1])) / 1e8 if len(amount) > 60 else 0
    amount_ratio = avg_amount_20d / avg_amount_60d if avg_amount_60d > 0 else 1.0

    # 涨停次数（120日）
    limit_up_count_120 = 0
    for i in range(max(0, last-119), last+1):
        if i > 0 and close[i] >= close[i-1] * 1.097:
            limit_up_count_120 += 1

    # 波动率（60日）
    if last >= 59:
        volatility_60 = round(float(np.std([(close[i]/close[i-1]-1)*100 for i in range(last-59, last+1) if i > 0])), 2)
    else:
        volatility_60 = 0

    # 主题评分（仅用于展示，不用于评分）
    theme_score_latest = 0
    theme_trend = 0
    try:
        conn = sqlite3.connect(THEME_SCORE_DB)
        c = conn.cursor()
        c.execute("SELECT composite_score, trend_score FROM theme_scores WHERE theme = ? ORDER BY trade_date DESC LIMIT 1", (theme,))
        row = c.fetchone()
        conn.close()
        if row:
            theme_score_latest = row[0] if row[0] else 0
            theme_trend = row[1] if row[1] else 0
    except Exception:
        pass

    # 东财热榜天数（120日）
    dc_hot_days_120 = 0
    try:
        conn = sqlite3.connect(CACHE_TUSHARE + "/dc_hot.db")
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM dc_hot WHERE ts_code = ? AND trade_date >= ?",
            (ts_code, (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=180)).strftime("%Y-%m-%d"))
        )
        row = c.fetchone()
        conn.close()
        if row:
            dc_hot_days_120 = int(row[0]) if row[0] else 0
    except Exception:
        pass

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

        "bias_ma20": bias_ma20,
        "bias_ma60": bias_ma60,
        "slope_20": slope_20,
        "slope_60": slope_60,
        "bull_score": bull_score,

        "max_drawdown_60d": max_drawdown_60,
        "volatility_60": volatility_60,

        "limit_up_count_120": limit_up_count_120,
        "dc_hot_days_120": dc_hot_days_120,

        "theme_score": round(float(theme_score_latest), 1),
        "theme_trend_score": round(float(theme_trend), 1),
    }

    return feat


def compute_mid_cap_score(feat):
    """
    1. 中军属性 Score（市值 + 成交）
    - 市值 300-5000 亿为最佳（太小=小票，太大=大象难涨）
    - 20日均成交 10-300 亿为最佳（太冷门没人关注，太大=大象）
    """
    mc = feat["market_cap_yi"]
    amt = feat["avg_amount_20d_yi"]

    # 市值评分（钟形曲线）
    if mc <= 0:
        mc_score = 0
    elif mc < 100:
        mc_score = 30 + mc * 0.7
    elif mc < 300:
        mc_score = 60 + (mc - 100) * 0.2
    elif mc < 1000:
        mc_score = 80 + (mc - 300) * 0.05
    elif mc < 3000:
        mc_score = 90 - (mc - 1000) * 0.01
    elif mc < 5000:
        mc_score = 70 - (mc - 3000) * 0.02
    else:
        mc_score = 50

    # 成交评分
    if amt <= 0:
        amt_score = 0
    elif amt < 5:
        amt_score = 40 + amt * 8
    elif amt < 20:
        amt_score = 80 + (amt - 5) * 1.5
    elif amt < 100:
        amt_score = 95 - (amt - 20) * 0.1
    elif amt < 300:
        amt_score = 80 - (amt - 100) * 0.1
    else:
        amt_score = 60

    return round(mc_score * 0.55 + amt_score * 0.45, 1)


def compute_recognition_score(feat):
    """
    2. 市场辨识度 Score（涨停 + 热榜 + 主题活跃天数）
    - 涨停次数：每3次涨停=10分，最高40分
    - 热榜天数：每5天=10分，最高30分
    - 成交活跃度：20日成交足够大 = 加分
    """
    lu = feat["limit_up_count_120"]
    dc = feat["dc_hot_days_120"]
    amt = feat["avg_amount_20d_yi"]

    # 涨停评分（每3次10分，超过20次反而扣分——妖股特征）
    if lu <= 0:
        lu_score = 10
    elif lu <= 3:
        lu_score = 20 + lu * 5
    elif lu <= 10:
        lu_score = 40 + (lu - 3) * 2
    elif lu <= 20:
        lu_score = 55 + (lu - 10) * 0.5
    else:
        lu_score = 60 - (lu - 20) * 2  # 太多涨停=妖股，扣分

    # 热榜评分
    if dc <= 0:
        dc_score = 10
    elif dc <= 5:
        dc_score = 30 + dc * 3
    elif dc <= 20:
        dc_score = 45 + (dc - 5) * 1
    elif dc <= 60:
        dc_score = 60 + (dc - 20) * 0.5
    else:
        dc_score = 80 - (dc - 60) * 0.5  # 太多曝光=透支

    # 成交活跃度（20日均20亿以上=足够活跃）
    amt_active = min(100, amt * 2) if amt < 50 else 80

    return round(lu_score * 0.35 + dc_score * 0.35 + amt_active * 0.30, 1)


def compute_value_margin_score(feat):
    """
    3. 价值余量 Score（乖离 + 涨幅 + 回撤 + 涨停密度）
    - 乖离率：MA20乖离 0-10%最佳
    - 涨幅：120日涨幅 20-80%最佳（太小没趋势，太大透支）
    - 回撤：有5-20%回撤=健康洗盘
    - 涨停密度：每10%涨幅用涨停数
    """
    bias = abs(feat["bias_ma20"])
    ret_120 = feat["ret_120"]
    ret_60 = feat["ret_60"]
    dd = feat["max_drawdown_60d"]
    lu = feat["limit_up_count_120"]

    # 乖离评分
    if bias <= 5:
        bias_score = 100
    elif bias <= 10:
        bias_score = 90 - (bias - 5) * 2
    elif bias <= 20:
        bias_score = 80 - (bias - 10) * 3
    elif bias <= 30:
        bias_score = 50 - (bias - 20) * 2
    else:
        bias_score = max(0, 30 - (bias - 30))

    # 120日涨幅评分
    if ret_120 <= 0:
        ret_score = 40  # 还没涨
    elif ret_120 <= 20:
        ret_score = 60 + ret_120 * 0.5
    elif ret_120 <= 60:
        ret_score = 70 + (ret_120 - 20) * 0.5
    elif ret_120 <= 100:
        ret_score = 90 - (ret_120 - 60) * 0.5
    elif ret_120 <= 150:
        ret_score = 70 - (ret_120 - 100) * 0.5
    else:
        ret_score = max(0, 50 - (ret_120 - 150) * 0.3)

    # 回撤健康度（有适度回撤=加分）
    if dd >= -5:
        dd_score = 50  # 几乎没回撤，换手不充分
    elif dd >= -15:
        dd_score = 100
    elif dd >= -30:
        dd_score = 80 + (dd + 15) * 1.3
    else:
        dd_score = max(0, 40 + (dd + 30) * 2)

    # 涨停密度（每10%涨幅用几次涨停，低=稳健上涨）
    if ret_120 > 10:
        density = lu / (ret_120 / 10)
    else:
        density = lu

    if density <= 0.2:
        density_score = 100
    elif density <= 0.5:
        density_score = 90 - (density - 0.2) * 30
    elif density <= 1.0:
        density_score = 80 - (density - 0.5) * 40
    elif density <= 2.0:
        density_score = 60 - (density - 1.0) * 30
    else:
        density_score = max(0, 30 - (density - 2.0) * 10)

    # 综合
    value_margin = round(
        0.30 * bias_score
        + 0.25 * ret_score
        + 0.25 * dd_score
        + 0.20 * density_score,
        1
    )
    return value_margin


def compute_trend_health_score(feat):
    """
    4. 趋势健康 Score（均线多头 + 斜率 + 量比）
    - 均线多头：健康趋势
    - MA20斜率：向上的斜率
    - 量价配合：20日/60日成交比 0.7-1.5 = 健康
    """
    bull = feat["bull_score"]
    slope = feat["slope_20"]
    amt_ratio = feat["amount_ratio"]
    bias = abs(feat["bias_ma20"])

    # 均线多头评分
    bull_score = bull  # 0-100

    # 斜率评分
    if slope <= 0:
        slope_score = 50
    elif slope <= 0.5:
        slope_score = 70 + slope * 20
    elif slope <= 1.5:
        slope_score = 80 + (slope - 0.5) * 10
    elif slope <= 3:
        slope_score = 90 - (slope - 1.5) * 10
    else:
        slope_score = max(0, 70 - (slope - 3) * 20)  # 太陡=爆拉

    # 量价配合评分
    if amt_ratio <= 0.5:
        ratio_score = 50  # 缩量过甚
    elif amt_ratio <= 0.8:
        ratio_score = 70 + (amt_ratio - 0.5) * 100
    elif amt_ratio <= 1.2:
        ratio_score = 100
    elif amt_ratio <= 2.0:
        ratio_score = 90 - (amt_ratio - 1.2) * 25
    else:
        ratio_score = max(0, 70 - (amt_ratio - 2.0) * 20)  # 爆量

    # 乖离控制额外加分
    if bias <= 10:
        control_score = 100
    elif bias <= 20:
        control_score = 80 - (bias - 10) * 2
    else:
        control_score = max(0, 60 - (bias - 20) * 1.5)

    trend_health = round(
        0.35 * bull_score
        + 0.25 * slope_score
        + 0.25 * ratio_score
        + 0.15 * control_score,
        1
    )
    return trend_health


def judge_rating(ultimate_score, value_margin):
    """评级（综合终极分和价值分）"""
    if ultimate_score >= 75 and value_margin >= 70:
        return "S+"
    elif ultimate_score >= 70 and value_margin >= 60:
        return "S"
    elif ultimate_score >= 65 and value_margin >= 50:
        return "A"
    elif ultimate_score >= 60:
        return "B"
    else:
        return "C"


def main():
    trade_date = get_latest_trade_date()
    print(f"分析日期: {trade_date}\n")

    portfolio = load_theme_portfolio()

    print("[2/4] 计算个股特征与评分...")

    results = []
    processed_codes = set()
    total = 0

    for theme, stocks in portfolio.items():
        for stock in stocks:
            code = stock["ts_code"]
            if code in processed_codes:
                continue
            processed_codes.add(code)

            # 过滤非主板/双创
            if not is_mainboard(code):
                continue

            feat = compute_stock_features(
                code, stock["name"], theme, stock["market_cap_yi"], trade_date
            )
            if feat is None:
                continue

            total += 1

            # 最低门槛（非常宽松，让更多股票进入评分环节）
            mc = feat["market_cap_yi"]
            amt = feat["avg_amount_20d_yi"]
            if mc <= 50 or amt <= 2:
                continue
            if feat["bull_score"] < 40:
                continue
            if mc > 10000:
                continue

            # 四个维度评分
            mid_cap = compute_mid_cap_score(feat)
            recognition = compute_recognition_score(feat)
            value_margin = compute_value_margin_score(feat)
            trend_health = compute_trend_health_score(feat)

            # 终极评分：等权重
            ultimate = round((mid_cap + recognition + value_margin + trend_health) / 4, 1)

            rating = judge_rating(ultimate, value_margin)

            result = {
                **feat,
                "mid_cap_score": mid_cap,
                "recognition_score": recognition,
                "value_margin_score": value_margin,
                "trend_health_score": trend_health,
                "ultimate_score": ultimate,
                "rating": rating,
            }
            results.append(result)

    print(f"  计算了 {total} 只股票的特征")

    # 按终极分排序
    results.sort(key=lambda x: x["ultimate_score"], reverse=True)

    # 输出
    print(f"\n[3/4] 通过最低门槛: {len(results)} 只股票")
    print(f"\n[4/4] Top 30 名单（v3算法，不依赖theme_score）:")
    print("=" * 150)
    print(
        f"{'排名':<4}{'代码':<12}{'名称':<10}{'主题':<18}{'评级':<4}"
        f"{'终极':<8}{'中军':<8}{'辨识':<8}{'价值':<8}{'趋势':<8}"
        f"{'MA20乖':<8}{'60日涨':<10}{'120日涨':<10}{'涨停':<8}{'市值亿':<10}"
    )
    print("-" * 150)
    for i, r in enumerate(results[:30], 1):
        print(
            f"{i:<4}{r['ts_code']:<12}{r['name']:<10}{r['theme']:<18}{r['rating']:<4}"
            f"{r['ultimate_score']:<8.1f}{r['mid_cap_score']:<8.1f}"
            f"{r['recognition_score']:<8.1f}{r['value_margin_score']:<8.1f}"
            f"{r['trend_health_score']:<8.1f}{r['bias_ma20']:<8.1f}"
            f"{r['ret_60']:<10.1f}{r['ret_120']:<10.1f}"
            f"{r['limit_up_count_120']:<8}{r['market_cap_yi']:<10.0f}"
        )
    print("=" * 150)

    # 评级分布
    from collections import Counter
    rating_counts = Counter(r["rating"] for r in results)
    print("\n【评级分布】")
    for rating in ["S+", "S", "A", "B", "C"]:
        if rating_counts.get(rating, 0) > 0:
            names = [r["name"] for r in results if r["rating"] == rating][:10]
            print(f"  {rating}级: {rating_counts[rating]} 只 -> {', '.join(names)}")

    # 主题分布
    theme_counts = Counter(r["theme"] for r in results[:50])
    print("\n【Top 50主题分布】")
    for theme, count in theme_counts.most_common(10):
        names = [r["name"] for r in results[:50] if r["theme"] == theme]
        print(f"  {theme}: {count} 只 -> {', '.join(names)}")

    # 保存
    output = {
        "scan_date": trade_date,
        "algorithm": "v3 - 中军属性 + 辨识度 + 价值余量 + 趋势健康",
        "total_count": len(results),
        "data": results[:100],
    }

    # 确保目录
    os.makedirs(os.path.join(BASE_DIR, "report_daily"), exist_ok=True)

    json_path = os.path.join(BASE_DIR, "report_daily", f"mainboard_v3_scan_{trade_date.replace('-', '')}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[Save] JSON: {json_path}")

    # 保存CSV
    csv_path = os.path.join(BASE_DIR, "report_daily", f"mainboard_v3_scan_{trade_date.replace('-', '')}.csv")
    df_out = pd.DataFrame(results[:100])
    df_out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[Save] CSV: {csv_path}")

    print("\n[Done] 扫描完成！")


if __name__ == "__main__":
    main()
