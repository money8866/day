# -*- coding: utf-8 -*-
"""
主板高辨识度中军龙头 · 第二波主升浪识别
=============================================

核心思路：
    跳过短线题材股，寻找【已经被市场记住，但主升浪尚未走完】的核心资产。

算法结构：
    1. 数据获取（从缓存读取）
        - theme_portfolio.db: 主题→成分股映射（mcap, layer, purity, turnover）
        - theme_trend_sentiment.db: 主题评分（trend/sentiment/composite_score, top10_days）
        - cache_daily/*.csv: 个股K线（close, pct_chg, vol, amount）
        - cache_backbone_tushare/dc_hot/*.csv: 东财热榜（主题活跃度）

    2. Recognition Score（辨识度）
        - theme_rank_score: 主题龙头地位（主题综合分 + 成分股排名）
        - attention_score: 东财热榜曝光（最近30日上榜天数/热榜得分）
        - active_score: 市场活跃度（日均成交额排名/持续活跃天数）
        - capacity_score: 机构容量（市值+日均成交 → 可容纳大资金程度）
        - memory_score: 涨停记忆（120日涨停次数+连板高度）

    3. 二波潜力评分（Second Wave Score）
        - industry_strength: 主题仍在强化（主题评分趋势 + 持续性得分）
        - earnings_strength: 业绩兑现（K线结构 + 稳定性 替代）
        - institution_strength: 机构资金（成交放大+均线多头+趋势斜率）

    4. 市场过滤器
        - 代码: SH(60/68/69开头) / SZ(00/30开头) / BJ(排除北交所)
        - 排除: 43xxx.BJ / 83xxx.BJ / 92xxx.BJ（北交所）
        - 市值: 50亿~5000亿（rmb亿）
        - 成交额: 20日均成交 ≥ 5亿
        - K线: 至少60个交易日

运行:
    python mainboard_second_wave_scanner.py
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

# ============================================================
# 配置路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_TUSHARE = os.path.join(BASE_DIR, "cache_backbone_tushare")
KLINE_CACHE = os.path.join(BASE_DIR, "cache_daily")
DC_HOT_DIR = os.path.join(CACHE_TUSHARE, "dc_hot")

THEME_SCORE_DB = os.path.join(CACHE_TUSHARE, "theme_trend_sentiment.db")
PORTFOLIO_DB = os.path.join(CACHE_TUSHARE, "theme_portfolio.db")


# ============================================================
# 工具函数
# ============================================================
def is_mainboard(code):
    """判断是否为主板或双创板（SH: 60/68开头；SZ: 00/30 开头）"""
    try:
        symbol = str(code).split(".")[0]
        if str(code).endswith(".SH"):
            # 60/68/69 开头（主板 + 科创板）
            if symbol.startswith("6") or symbol.startswith("68") or symbol.startswith("69"):
                return True
        if str(code).endswith(".SZ"):
            # 00 开头（主板）/ 30 开头（创业板）
            if symbol.startswith("00") or symbol.startswith("30"):
                return True
    except Exception:
        pass
    return False


def load_kline(code, lookback=240):
    """从cache_daily读取K线"""
    path = os.path.join(KLINE_CACHE, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or len(df) < 20:
            return None
        df["trade_date"] = df["trade_date"].astype(str)
        df = df.sort_values("trade_date").reset_index(drop=True)
        if len(df) > lookback + 30:
            df = df.iloc[-(lookback + 30):].reset_index(drop=True)
        return df
    except Exception:
        return None


# ============================================================
# 数据加载
# ============================================================
def load_theme_portfolio():
    """从 theme_portfolio.db 读取成分股，返回 {ts_code: {theme, mcap, turnover, purity, layer}}"""
    mapping = {}
    if not os.path.exists(PORTFOLIO_DB):
        return mapping
    try:
        conn = sqlite3.connect(PORTFOLIO_DB)
        cur = conn.cursor()
        # 读取最新交易日的 portfolio 数据
        cur.execute("SELECT DISTINCT trade_date FROM portfolio ORDER BY trade_date DESC LIMIT 1")
        latest = cur.fetchone()
        if not latest:
            conn.close()
            return mapping
        latest_date = latest[0]
        print(f"[Theme] 读取 {latest_date} 主题成分股数据")

        cur.execute(
            "SELECT ts_code, name, theme_name, layer, mcap, turnover, purity FROM portfolio WHERE trade_date = ?",
            (latest_date,),
        )
        for row in cur.fetchall():
            ts_code, name, theme_name, layer, mcap, turnover, purity = row
            mapping[ts_code] = {
                "name": str(name or ""),
                "theme_name": str(theme_name or ""),
                "layer": str(layer or ""),
                "mcap_yi": float(mcap or 0),  # 原始单位已经是亿
                "turnover_yi": float(turnover or 0),
                "purity": int(purity or 0),
            }
        conn.close()
        print(f"[Theme] 共 {len(mapping)} 只成分股")
        return mapping
    except Exception as e:
        print(f"[Warn] theme_portfolio.db 读取失败: {e}")
        return mapping


def load_theme_scores(days=30):
    """从 theme_trend_sentiment.db 读取最近N天主题评分"""
    scores = defaultdict(list)
    if not os.path.exists(THEME_SCORE_DB):
        return scores
    try:
        conn = sqlite3.connect(THEME_SCORE_DB)
        cur = conn.cursor()
        # 找到最近N个交易日
        cur.execute(
            f"SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT {days}"
        )
        dates = sorted([r[0] for r in cur.fetchall()])
        # 获取每个主题数据
        for d in dates:
            cur.execute(
                "SELECT theme, composite_score, trend_score, sentiment_score, rank, top10_days_10d, top10_days_20d, n_stocks, ret_5, ret_10, ret_20 FROM theme_scores WHERE trade_date = ?",
                (d,),
            )
            for row in cur.fetchall():
                theme, comp, trend, sent, rank, t10, t20, ns, r5, r10, r20 = row
                scores[theme].append(
                    {
                        "trade_date": d,
                        "composite_score": float(comp or 0),
                        "trend_score": float(trend or 0),
                        "sentiment_score": float(sent or 0),
                        "rank": int(rank or 0),
                        "top10_days_10d": int(t10 or 0),
                        "top10_days_20d": int(t20 or 0),
                        "n_stocks": int(ns or 0),
                        "ret_5": float(r5 or 0),
                        "ret_10": float(r10 or 0),
                        "ret_20": float(r20 or 0),
                    }
                )
        conn.close()
        print(f"[Score] 共 {len(scores)} 个主题，最近 {len(dates)} 天数据")
        return scores
    except Exception as e:
        print(f"[Warn] theme_scores 读取失败: {e}")
        return scores


def load_dc_hot_days(days=30):
    """读取最近N天热榜，统计每只股票出现次数及得分"""
    stock_hot = defaultdict(lambda: {"days": 0, "total_score": 0.0, "avg_rank": 0})
    end = datetime.now()
    for i in range(days + 5):
        d = (end - timedelta(days=i)).strftime("%Y%m%d")
        path = os.path.join(DC_HOT_DIR, f"dc_hot_{d}.csv")
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path)
            if df.empty:
                continue
            # 兼容不同列名
            code_col = None
            for col in ["code", "ts_code", "股票代码"]:
                if col in df.columns:
                    code_col = col
                    break
            if code_col is None:
                # 尝试第一列
                code_col = df.columns[0]
            for idx, row in df.iterrows():
                code = str(row[code_col]).strip()
                if not code or code.lower() in ["nan", "none", ""]:
                    continue
                # 归一化代码格式（可能不带 .SH/.SZ 后缀）
                if "." not in code and len(code) == 6:
                    # 假设沪市60开头，深市00/30开头
                    if code.startswith("6"):
                        code = code + ".SH"
                    else:
                        code = code + ".SZ"
                stock_hot[code]["days"] += 1
                # 得分：排名越高分越高（1名=100分，100名=1分）
                rank_val = min(100, max(1, int(idx) + 1))
                score = 100.0 - rank_val + 1
                stock_hot[code]["total_score"] += score
        except Exception:
            pass
    # 计算平均排名
    for code, info in stock_hot.items():
        if info["days"] > 0:
            info["avg_rank"] = info["total_score"] / info["days"]
    print(f"[Hot] 热榜 {sum(1 for _, v in stock_hot.items() if v['days'] > 0)} 只股票上榜")
    return dict(stock_hot)


# ============================================================
# 核心特征计算
# ============================================================
def compute_stock_features(code, df_kline, theme_name, theme_data, stock_hot):
    """对单只股票计算多维特征"""
    feat = {
        "ts_code": code,
        "theme_name": theme_name,
    }

    if df_kline is None or len(df_kline) < 60:
        return None

    n = len(df_kline)
    close = df_kline["close"].astype(float).values
    pct = df_kline["pct_chg"].astype(float).values
    vol = df_kline["vol"].astype(float).values
    amount = df_kline["amount"].astype(float).values / 100000.0  # 千元→亿元

    last = n - 1

    # ---- 1. 价格与收益 ----
    def safe_ret(offset, window):
        if last - offset - window < 0:
            return 0.0
        start_idx = max(0, last - offset - window + 1)
        end_idx = last - offset
        if close[start_idx] == 0:
            return 0.0
        return (close[end_idx] / close[start_idx] - 1) * 100

    ret_5 = safe_ret(0, 5)
    ret_10 = safe_ret(0, 10)
    ret_20 = safe_ret(0, 20)
    ret_60 = safe_ret(0, 60)
    ret_120 = safe_ret(0, 120)

    # 前一个20日区间（-40~-20天）的收益，用于判断回踩/恢复
    ret_prev20 = safe_ret(20, 20)

    feat["ret_5"] = round(ret_5, 1)
    feat["ret_10"] = round(ret_10, 1)
    feat["ret_20"] = round(ret_20, 1)
    feat["ret_60"] = round(ret_60, 1)
    feat["ret_120"] = round(ret_120, 1)
    feat["ret_prev20"] = round(ret_prev20, 1)

    # ---- 2. 均线结构 ----
    def ma(window, offset=0):
        idx = last - offset
        if idx - window + 1 < 0:
            return 0.0
        return close[idx - window + 1: idx + 1].mean()

    ma5 = ma(5)
    ma10 = ma(10)
    ma20 = ma(20)
    ma60 = ma(60) if n >= 60 else ma20
    ma120 = ma(120) if n >= 120 else ma60

    feat["price"] = round(close[last], 2)
    feat["ma5"] = round(ma5, 2)
    feat["ma10"] = round(ma10, 2)
    feat["ma20"] = round(ma20, 2)
    feat["ma60"] = round(ma60, 2)
    feat["ma120"] = round(ma120, 2)
    feat["bias_ma5"] = round((close[last] / ma5 - 1) * 100, 2) if ma5 > 0 else 0
    feat["bias_ma10"] = round((close[last] / ma10 - 1) * 100, 2) if ma10 > 0 else 0
    feat["bias_ma20"] = round((close[last] / ma20 - 1) * 100, 2) if ma20 > 0 else 0
    feat["bias_ma60"] = round((close[last] / ma60 - 1) * 100, 2) if ma60 > 0 else 0

    # 均线多头程度: close > MA5 > MA10 > MA20 > MA60 的个数
    bull_cond = [
        close[last] > ma5,
        ma5 > ma10,
        ma10 > ma20,
        ma20 > ma60,
    ]
    feat["bull_score"] = sum(bull_cond)  # 0~4

    # MA 斜率（线性回归）
    def slope(prices):
        if len(prices) < 5:
            return 0.0
        x = np.arange(len(prices))
        try:
            s = np.polyfit(x, prices, 1)[0]
            return s / np.mean(prices) * 100 if np.mean(prices) > 0 else 0
        except Exception:
            return 0.0

    feat["slope_10"] = round(slope(close[max(0, last - 9): last + 1]), 2)
    feat["slope_20"] = round(slope(close[max(0, last - 19): last + 1]), 2)
    feat["slope_60"] = round(slope(close[max(0, last - 59): last + 1]), 2) if n >= 60 else 0

    # ---- 3. 成交量 / 成交额 ----
    avg_amount_20 = amount[max(0, last - 19): last + 1].mean()
    avg_amount_60 = amount[max(0, last - 59): last + 1].mean() if n >= 60 else avg_amount_20
    feat["avg_amount_20d_yi"] = round(avg_amount_20, 2)  # 已经是亿元
    feat["avg_amount_60d_yi"] = round(avg_amount_60, 2)
    feat["amount_ratio_20_60"] = round(avg_amount_20 / max(avg_amount_60, 0.001), 2)

    # 最近5日成交放大 vs 60日均
    avg_amount_5 = amount[max(0, last - 4): last + 1].mean()
    feat["amount_ratio_5_60"] = round(avg_amount_5 / max(avg_amount_60, 0.001), 2)

    # ---- 4. 涨停 / 连板特征 ----
    zt_days_120 = int(np.sum(pct[max(0, last - 119): last + 1] >= 9.5))
    zt_days_60 = int(np.sum(pct[max(0, last - 59): last + 1] >= 9.5))
    # 最大连板高度（最近60日）
    max_height = 0
    cur = 0
    for p in pct[max(0, last - 59): last + 1]:
        if p >= 9.5:
            cur += 1
            max_height = max(max_height, cur)
        else:
            cur = 0
    feat["limit_up_count_120d"] = zt_days_120
    feat["limit_up_count_60d"] = zt_days_60
    feat["max_limit_up_height_60d"] = max_height

    # ---- 5. 回撤 / 波动性 ----
    if n >= 60:
        window_60 = close[last - 59: last + 1]
        running_max = np.maximum.accumulate(window_60)
        dd = (window_60 / running_max - 1) * 100
        feat["max_drawdown_60d"] = round(dd.min(), 1)
    else:
        feat["max_drawdown_60d"] = -10.0

    # 60日波动率
    if n >= 60:
        feat["volatility_60d"] = round(np.std(pct[max(0, last - 59): last + 1]), 2)
    else:
        feat["volatility_60d"] = 0.0

    # 上涨日数 vs 下跌日数
    feat["up_days_60"] = int(np.sum(pct[max(0, last - 59): last + 1] > 0))

    # ---- 6. 主题匹配度 ----
    theme_score_latest = 0
    theme_trend = 0
    theme_top10_20d = 0
    if theme_name and theme_data and theme_name in theme_data:
        data = theme_data[theme_name]
        if data:
            latest = data[-1]
            theme_score_latest = latest.get("composite_score", 0)
            theme_trend = latest.get("trend_score", 0)
            theme_top10_20d = latest.get("top10_days_20d", 0)

    feat["theme_composite_score"] = round(theme_score_latest, 1)
    feat["theme_trend_score"] = round(theme_trend, 1)
    feat["theme_top10_days_20d"] = theme_top10_20d

    # ---- 7. 热榜关注度 ----
    hot_info = stock_hot.get(code, {"days": 0, "avg_rank": 0, "total_score": 0})
    feat["hot_days_30"] = int(hot_info["days"])
    feat["hot_score"] = round(float(hot_info.get("total_score", 0)), 1)

    return feat


# ============================================================
# 评分系统
# ============================================================
def norm(value, vmin, vmax, reverse=False):
    """归一化 0-100，超出范围截断"""
    if vmax == vmin:
        return 50.0
    ratio = (value - vmin) / (vmax - vmin)
    if reverse:
        ratio = 1 - ratio
    return max(0.0, min(100.0, ratio * 100))


def compute_recognition(feat, theme_data):
    """
    Recognition Score: 衡量市场是否记住这只股票
    """
    # 1. theme_rank_score（主题龙头地位 0~100）
    ts = feat.get("theme_composite_score", 0)
    theme_hot = min(100, ts * 1.2)
    # 如果是 theme_name 的龙头（成分股内高市值+高匹配度），额外加分
    leader_bonus = 15 if feat.get("is_theme_leader", False) else 0
    theme_rank_score = min(100, theme_hot + leader_bonus)

    # 2. attention_score（热榜曝光 0~100）
    hot_days = feat.get("hot_days_30", 0)
    hot_score_raw = feat.get("hot_score", 0)
    attention_score = min(100, hot_days * 10 + hot_score_raw / 10)

    # 3. active_score（市场活跃度，基于成交量/天数）
    avg_amt_20 = feat.get("avg_amount_20d_yi", 0)
    # 成交额 2 亿 起步，10 亿以上满分
    active_score = norm(avg_amt_20, 1.0, 15.0)

    # 4. capacity_score（机构容量，基于市值+成交）
    mcap = feat.get("mcap_yi", 0)
    mcap_score = norm(mcap, 30, 1000)
    amt_score = norm(avg_amt_20, 1, 20)
    capacity_score = round(0.6 * mcap_score + 0.4 * amt_score, 1)

    # 5. memory_score（涨停/连板记忆）
    zt_count = feat.get("limit_up_count_120d", 0)
    zt_height = feat.get("max_limit_up_height_60d", 0)
    # 涨停太多=短线票，反扣；太少=无记忆
    zt_balanced = min(100, max(0, 60 - abs(zt_count - 8) * 6))
    height_score = min(100, zt_height * 30)
    memory_score = round(0.5 * zt_balanced + 0.5 * height_score, 1)

    # 综合
    recognition = round(
        0.25 * theme_rank_score
        + 0.15 * attention_score
        + 0.15 * active_score
        + 0.30 * capacity_score
        + 0.15 * memory_score,
        1,
    )
    return {
        "recognition_score": recognition,
        "theme_rank_score": round(theme_rank_score, 1),
        "attention_score": round(attention_score, 1),
        "active_score": round(active_score, 1),
        "capacity_score": capacity_score,
        "memory_score": memory_score,
    }


def compute_value_preservation(feat):
    """
    Value Preservation Score: 衡量长期价值是否被透支（0~100）
    高分 = 价值未被透支，仍有上涨空间
    """
    # 1. 涨幅温和性（60日涨幅 20~50%最佳，过大=透支，过小=启动初期）
    ret_60 = feat.get("ret_60", 0)
    if ret_60 < 10:
        gain_score = norm(ret_60, 0, 30)  # 启动初期，有空间
    elif ret_60 <= 50:
        gain_score = 100  # 最佳区间
    else:
        gain_score = max(0, 100 - (ret_60 - 50) * 2)  # 超过50%开始扣分

    # 2. 乖离率控制（MA20乖离 0~15%最佳，过大=透支）
    bias_ma20 = feat.get("bias_ma20", 0)
    bias_ma60 = feat.get("bias_ma60", 0)
    bias_score20 = norm(abs(bias_ma20), 0, 20, reverse=True)
    bias_score60 = norm(abs(bias_ma60), 0, 40, reverse=True)
    bias_score = round(0.6 * bias_score20 + 0.4 * bias_score60, 1)

    # 3. 上涨质量（小阳线为主，不是连续涨停）
    zt_60 = feat.get("limit_up_count_60d", 0)
    zt_120 = feat.get("limit_up_count_120d", 0)
    # 涨停太少=无催化剂，涨停太多=短线炒作
    if zt_60 <= 2:
        quality_score = norm(zt_60, 0, 3)  # 需要一定涨停确认趋势
    elif zt_60 <= 6:
        quality_score = 100  # 最佳区间：有确认但不过度
    else:
        quality_score = max(0, 100 - (zt_60 - 6) * 8)  # 过多涨停扣分

    # 4. 量价配合（温和放量，不是天量）
    amt_ratio_20_60 = feat.get("amount_ratio_20_60", 1.0)
    amt_ratio_5_60 = feat.get("amount_ratio_5_60", 1.0)
    # 20日/60日 0.8~1.5 最佳，5日/60日 0.8~2.0 最佳
    flow_score = norm(amt_ratio_20_60, 0.6, 1.6) if amt_ratio_20_60 <= 2 else norm(amt_ratio_20_60, 1.6, 4, reverse=True)
    flow_score5 = norm(amt_ratio_5_60, 0.6, 2.0) if amt_ratio_5_60 <= 2.5 else norm(amt_ratio_5_60, 2.0, 5, reverse=True)
    flow_score = round(0.6 * flow_score + 0.4 * flow_score5, 1)

    # 5. 回撤控制（有适度回撤=健康洗盘，无回撤=过于激进）
    dd = feat.get("max_drawdown_60d", 0)
    # 回撤 -5~-15% 最佳，无回撤=没有充分换手，回撤过大=趋势不稳
    if dd >= -5:
        dd_score = norm(dd, -5, -2, reverse=True)  # 回撤太小，扣分
    elif dd >= -20:
        dd_score = 100  # 最佳区间
    else:
        dd_score = norm(dd, -30, -20)  # 回撤太大，扣分

    # 6. 热榜适度（曝光适度，不是过度炒作）
    hot_days = feat.get("hot_days_30", 0)
    if hot_days <= 3:
        hot_score = norm(hot_days, 0, 5)  # 需要一定曝光
    elif hot_days <= 8:
        hot_score = 100  # 最佳区间
    else:
        hot_score = max(0, 100 - (hot_days - 8) * 5)  # 过度曝光扣分

    # 综合价值未透支评分
    value_preservation = round(
        0.20 * gain_score
        + 0.20 * bias_score
        + 0.15 * quality_score
        + 0.15 * flow_score
        + 0.15 * dd_score
        + 0.15 * hot_score,
        1,
    )

    return {
        "value_preservation_score": value_preservation,
        "gain_score": round(gain_score, 1),
        "bias_score": bias_score,
        "quality_score": round(quality_score, 1),
        "flow_score": flow_score,
        "dd_score": round(dd_score, 1),
        "hot_score": round(hot_score, 1),
    }


def compute_second_wave(feat, rec, theme_data):
    """
    Second Wave Score: 衡量走出第二波主升浪的潜力
    """
    theme_name = feat.get("theme_name", "")

    # 1. industry_strength（主题仍在强化）
    theme_score = feat.get("theme_composite_score", 0)
    theme_trend = feat.get("theme_trend_score", 0)
    top10_days = feat.get("theme_top10_days_20d", 0)

    # 主题持续性：Top10 天数多 = 持续受关注
    persistence = min(100, top10_days * 6 + 30)
    industry_strength = round(0.5 * theme_score + 0.25 * persistence + 0.25 * theme_trend, 1)

    # 2. earnings_strength（业绩/基本面兑现 - 用K线质量+回撤控制+斜率替代）
    ret_60 = feat.get("ret_60", 0)
    ret_120 = feat.get("ret_120", 0)
    slope_60 = feat.get("slope_60", 0)
    dd = feat.get("max_drawdown_60d", 0)

    # 稳健上涨评分（60日涨幅 20~60%最佳，斜率正，回撤可控）
    ret_score = norm(ret_60, -5, 60)
    slope_score = norm(slope_60, -0.1, 0.5)
    dd_score = norm(dd, -25, -5)  # 回撤越小越好
    vol_score = norm(feat.get("volatility_60d", 0), 4, 2, reverse=True)

    earnings_strength = round(
        0.30 * ret_score + 0.25 * slope_score + 0.25 * dd_score + 0.20 * vol_score, 1
    )

    # 3. institution_strength（机构强化 - 成交持续性+均线结构）
    amt_ratio = feat.get("amount_ratio_20_60", 1.0)
    amt_5_60 = feat.get("amount_ratio_5_60", 1.0)
    bull = feat.get("bull_score", 0)
    ma_bias = feat.get("bias_ma60", 0)
    ret_prev20 = feat.get("ret_prev20", 0)

    # 资金流入强度: 20日/60日成交比（温和放量=1.0~1.5最佳）
    flow_score = norm(amt_ratio, 0.7, 1.6)
    # 均线结构: 多头排列程度（0~4）
    structure_score = bull * 25
    # 站在MA60之上 = 结构性强
    above_ma60_score = norm(ma_bias, -5, 15)
    # 前一段有回踩（ret_prev20 相对较小），当前刚恢复
    recovery_score = norm(ret_60 - ret_prev20, -10, 30)

    institution_strength = round(
        0.25 * flow_score
        + 0.30 * structure_score
        + 0.25 * above_ma60_score
        + 0.20 * recovery_score,
        1,
    )

    # 综合
    second_wave = round(
        0.30 * rec["recognition_score"]
        + 0.30 * industry_strength
        + 0.20 * earnings_strength
        + 0.20 * institution_strength,
        1,
    )
    return {
        "second_wave_score": second_wave,
        "industry_strength": industry_strength,
        "earnings_strength": earnings_strength,
        "institution_strength": institution_strength,
    }


def judge_stage(feat, rec, sw):
    """判断主题所处阶段"""
    rec_s = rec["recognition_score"]
    sw_s = sw["second_wave_score"]
    ret_60 = feat.get("ret_60", 0)
    bull = feat.get("bull_score", 0)
    slope60 = feat.get("slope_60", 0)
    bias60 = feat.get("bias_ma60", 0)

    # 阶段1: 启动（低位刚突破）
    if ret_60 < 0 and bull >= 2 and slope60 > -0.05:
        return "阶段1: 启动复苏"
    # 阶段2: 龙头确立（结构性强+市场开始认可）
    if 60 <= rec_s < 80 and bull >= 3 and ret_60 > 0:
        return "阶段2: 龙头确立"
    # 阶段3-4: 机构抱团/二波主升
    if rec_s >= 80 and sw_s >= 80 and bull >= 3 and bias60 > 5:
        return "阶段3-4: 机构抱团/二波主升"
    # 阶段5: 估值扩张/高位震荡
    if ret_60 > 60 and bias60 > 20:
        return "阶段5: 估值扩张"
    if ret_60 < -20 and bias60 < -10:
        return "下跌阶段"
    return "过渡阶段"


def judge_rating(feat, rec, sw, vp):
    """S/A/B/C 评级（加入价值未透支维度）"""
    sw_s = sw["second_wave_score"]
    rec_s = rec["recognition_score"]
    vp_s = vp["value_preservation_score"]
    mcap = feat.get("mcap_yi", 0)
    amount = feat.get("avg_amount_20d_yi", 0)

    # S级：高辨识度 + 二波潜力 + 价值未被透支
    if sw_s >= 75 and rec_s >= 70 and vp_s >= 70 and mcap >= 100 and amount >= 5:
        return "S"
    # A级：有潜力但价值可能已部分透支，或价值未透支但辨识度稍低
    elif sw_s >= 65 and rec_s >= 60 and vp_s >= 60 and mcap >= 50 and amount >= 3:
        return "A"
    # B级：主题龙头，等待催化
    elif sw_s >= 50 and rec_s >= 50:
        return "B"
    else:
        return "C"


def build_criteria_text(feat, rec, sw):
    """生成核心逻辑说明（结构化要点）"""
    reasons = []
    theme = feat.get("theme_name", "")
    if theme:
        reasons.append(f"主题 {theme}（综合分 {feat.get('theme_composite_score', 0):.0f}，20日Top10 {feat.get('theme_top10_days_20d', 0)}天）")
    reasons.append(f"辨识度 {rec['recognition_score']:.0f}分（主题{rec['theme_rank_score']:.0f}/曝光{rec['attention_score']:.0f}/活跃{rec['active_score']:.0f}/容量{rec['capacity_score']:.0f}/记忆{rec['memory_score']:.0f}）")
    reasons.append(
        f"二波潜力 {sw['second_wave_score']:.0f}分（产业强度{sw['industry_strength']:.0f}/业绩兑现{sw['earnings_strength']:.0f}/机构强化{sw['institution_strength']:.0f}）"
    )
    reasons.append(
        f"市值 {feat.get('mcap_yi', 0):.0f}亿 · 20日均成交 {feat.get('avg_amount_20d_yi', 0):.1f}亿 · 涨停{feat.get('limit_up_count_120d', 0)}次"
    )
    reasons.append(
        f"60日+{feat.get('ret_60', 0):+.1f}% · 120日+{feat.get('ret_120', 0):+.1f}% · MA60偏{feat.get('bias_ma60', 0):+.1f}% · 均线多头{feat.get('bull_score', 0)}/4"
    )
    if feat.get("hot_days_30", 0) > 0:
        reasons.append(f"近30天上热榜 {feat.get('hot_days_30', 0)} 天")
    return reasons


def build_risk_text(feat):
    """生成风险提示"""
    risks = []
    bias = feat.get("bias_ma20", 0)
    if bias > 15:
        risks.append(f"短期乖离过大（MA20+{bias:.1f}%），有回调风险")
    dd = feat.get("max_drawdown_60d", 0)
    if dd < -25:
        risks.append(f"60日最大回撤{dd:.1f}%，波动率偏高")
    zt = feat.get("limit_up_count_60d", 0)
    if zt > 15:
        risks.append(f"60日涨停{zt}次，游资炒作痕迹明显")
    vol = feat.get("volatility_60d", 0)
    if vol > 3.5:
        risks.append(f"波动率{vol:.1f}%，日内波动较大")
    if not risks:
        risks.append("暂无显著风险因子")
    return risks


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 70)
    print("主板高辨识度中军龙头 · 第二波主升浪识别")
    print("=" * 70)

    # 1. 加载数据
    portfolio = load_theme_portfolio()
    theme_scores = load_theme_scores(days=60)
    stock_hot = load_dc_hot_days(days=30)

    # 计算每只股票的市场信息
    if not portfolio:
        print("[Error] 没有找到主题成分股数据，无法继续")
        return

    # 2. 计算每只股票特征
    print("\n[Calc] 开始计算股票特征...")
    all_stocks = []
    # 计算每个主题的龙头/中军（按 mcap 排 Top 3）
    theme_members = defaultdict(list)
    for code, info in portfolio.items():
        theme_members[info["theme_name"]].append((code, info))

    theme_leaders = {}
    for theme, members in theme_members.items():
        # 按 mcap 降序排 Top 3 作为龙头候选
        sorted_m = sorted(members, key=lambda x: x[1]["mcap_yi"], reverse=True)
        for rank, (code, _) in enumerate(sorted_m):
            theme_leaders[code] = (rank < 3, rank + 1)

    for idx, (code, info) in enumerate(portfolio.items()):
        if idx % 200 == 0:
            print(f"  进度 {idx}/{len(portfolio)} ...")
        if not is_mainboard(code):
            continue

        # 从 K线缓存读取
        kline = load_kline(code, lookback=180)
        if kline is None or len(kline) < 60:
            continue

        # 计算特征
        feat = compute_stock_features(code, kline, info["theme_name"], theme_scores, stock_hot)
        if feat is None:
            continue

        # 合并 portfolio 的基础信息
        feat["name"] = info["name"]
        feat["mcap_yi"] = info["mcap_yi"]  # 已经是亿单位
        feat["purity"] = info["purity"]

        # 校验市值（放大到5000亿）
        if feat["mcap_yi"] < 50 or feat["mcap_yi"] > 5000:
            continue
        # 校验成交额
        if feat.get("avg_amount_20d_yi", 0) < 5:
            continue

        # 是否主题龙头
        is_leader, leader_rank = theme_leaders.get(code, (False, 99))
        feat["is_theme_leader"] = is_leader
        feat["theme_leader_rank"] = leader_rank

        # 评分
        rec = compute_recognition(feat, theme_scores)
        sw = compute_second_wave(feat, rec, theme_scores)
        vp = compute_value_preservation(feat)

        # 评级
        stage = judge_stage(feat, rec, sw)
        rating = judge_rating(feat, rec, sw, vp)

        # 组装结果
        result = {
            "ts_code": code,
            "name": feat["name"],
            "theme": feat["theme_name"],
            "industry": info.get("layer", ""),
            "market_cap_yi": round(feat["mcap_yi"], 1),
            "avg_amount_20d_yi": round(feat.get("avg_amount_20d_yi", 0), 2),
            # 辨识度分项
            "theme_rank_score": rec["theme_rank_score"],
            "attention_score": rec["attention_score"],
            "active_score": rec["active_score"],
            "capacity_score": rec["capacity_score"],
            "memory_score": rec["memory_score"],
            "recognition_score": rec["recognition_score"],
            # 二波潜力分项
            "industry_strength": sw["industry_strength"],
            "earnings_strength": sw["earnings_strength"],
            "institution_strength": sw["institution_strength"],
            "second_wave_score": sw["second_wave_score"],
            # 价值未透支评分
            "value_preservation_score": vp["value_preservation_score"],
            "gain_score": vp["gain_score"],
            "bias_score": vp["bias_score"],
            "quality_score": vp["quality_score"],
            "flow_score": vp["flow_score"],
            "dd_score": vp["dd_score"],
            "hot_score": vp["hot_score"],
            # 辅助指标
            "ret_5": feat.get("ret_5", 0),
            "ret_10": feat.get("ret_10", 0),
            "ret_20": feat.get("ret_20", 0),
            "ret_60": feat.get("ret_60", 0),
            "ret_120": feat.get("ret_120", 0),
            "slope_10": feat.get("slope_10", 0),
            "slope_60": feat.get("slope_60", 0),
            "bias_ma20": feat.get("bias_ma20", 0),
            "bias_ma60": feat.get("bias_ma60", 0),
            "bull_score": feat.get("bull_score", 0),
            "volatility_60d": feat.get("volatility_60d", 0),
            "max_drawdown_60d": feat.get("max_drawdown_60d", 0),
            "limit_up_count_120d": feat.get("limit_up_count_120d", 0),
            "hot_days_30": feat.get("hot_days_30", 0),
            "amount_ratio_20_60": feat.get("amount_ratio_20_60", 0),
            "theme_composite_score": feat.get("theme_composite_score", 0),
            "theme_top10_days_20d": feat.get("theme_top10_days_20d", 0),
            "is_theme_leader": feat.get("is_theme_leader", False),
            # 最终判断
            "stage": stage,
            "rating": rating,
            "core_reason": build_criteria_text(feat, rec, sw),
            "risk_factor": build_risk_text(feat),
        }
        all_stocks.append(result)

    print(f"[Result] 符合主板筛选条件的股票: {len(all_stocks)} 只")

    # 3. 排序（按 second_wave_score + value_preservation_score 综合排序）
    all_stocks.sort(key=lambda x: x["second_wave_score"] * 0.6 + x["value_preservation_score"] * 0.4, reverse=True)

    # 4. 控制台输出
    print("\n" + "=" * 120)
    print(
        f"{'排名':<5}{'股票':<10}{'主题':<14}{'市值(亿)':<10}{'20日均(亿)':<12}"
        f"{'识别分':<8}{'二波分':<8}{'价值分':<8}{'评级':<6}{'阶段':<30}"
    )
    print("-" * 120)
    for i, r in enumerate(all_stocks[:30], 1):
        print(
            f"{i:<5}{r['name']:<10}{r['theme']:<14}{r['market_cap_yi']:<10.0f}"
            f"{r['avg_amount_20d_yi']:<12.1f}{r['recognition_score']:<8.1f}"
            f"{r['second_wave_score']:<8.1f}{r['value_preservation_score']:<8.1f}"
            f"{r['rating']:<6}{r['stage']:<30}"
        )
    print("=" * 120)

    # 5. 详细输出 Top 20
    print("\n\n" + "=" * 110)
    print("【Top 20 详细信息】")
    print("=" * 110)
    for i, r in enumerate(all_stocks[:20], 1):
        print(f"\n{'-' * 100}")
        print(
            f"No.{i} [{r['rating']}] {r['name']}({r['ts_code']}) · {r['theme']} | "
            f"市值 {r['market_cap_yi']:.0f}亿 · 20日均成交 {r['avg_amount_20d_yi']:.1f}亿"
        )
        print(f"  阶段: {r['stage']}")
        print(
            f"  辨识度 {r['recognition_score']:.0f} = "
            f"主题{r['theme_rank_score']:.0f}/曝光{r['attention_score']:.0f}/活跃{r['active_score']:.0f}/容量{r['capacity_score']:.0f}/记忆{r['memory_score']:.0f}"
        )
        print(
            f"  二波分 {r['second_wave_score']:.0f} = "
            f"产业强度{r['industry_strength']:.0f}/业绩兑现{r['earnings_strength']:.0f}/机构强化{r['institution_strength']:.0f}"
        )
        print(
            f"  K线: 5日+{r['ret_5']:+.1f}% / 20日+{r['ret_20']:+.1f}% / 60日+{r['ret_60']:+.1f}% / 120日+{r['ret_120']:+.1f}%"
        )
        print(
            f"  斜率: MA10={r['slope_10']:+.2f}% / MA60={r['slope_60']:+.2f}% | MA60偏{r['bias_ma60']:+.1f}% | 均线多头{r['bull_score']}/4"
        )
        print(
            f"  波动率: {r['volatility_60d']:.2f}% | 60日最大回撤: {r['max_drawdown_60d']:.1f}%"
        )
        print(
            f"  涨停: 120日{r['limit_up_count_120d']}次 | 热榜: 30日内{r['hot_days_30']}天 | 成交比(20/60): {r['amount_ratio_20_60']:.2f}"
        )
        for reason in r["core_reason"]:
            print(f"  ✔ {reason}")
        for risk in r["risk_factor"]:
            print(f"  ⚠ {risk}")

    # 6. 按评级分组
    print("\n\n" + "=" * 110)
    print("【按评级统计】")
    print("=" * 110)
    for rating in ["S", "A", "B", "C"]:
        count = sum(1 for r in all_stocks if r["rating"] == rating)
        print(f"  {rating}级: {count} 只")
        if rating in ["S", "A"] and count > 0:
            rated = [r for r in all_stocks if r["rating"] == rating]
            for r in rated[:5]:
                print(f"     - {r['name']}({r['ts_code']}) | {r['theme']} | 二波{r['second_wave_score']:.0f}")

    # 7. 持久化
    output_dir = os.path.join(BASE_DIR, "report_daily")
    os.makedirs(output_dir, exist_ok=True)

    try:
        # 转换 numpy 类型为 Python 原生类型
        def _to_py(v):
            if isinstance(v, (np.integer, np.int64, np.int32)):
                return int(v)
            if isinstance(v, (np.floating, np.float64, np.float32)):
                return float(v)
            if isinstance(v, np.ndarray):
                return v.tolist()
            if isinstance(v, dict):
                return {k: _to_py(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_to_py(x) for x in v]
            if isinstance(v, bool):
                return v
            return v

        # 准备输出数据
        output_data = []
        for r in all_stocks:
            row = _to_py(r)
            row["core_reason"] = " | ".join(row["core_reason"]) if isinstance(row["core_reason"], list) else str(row.get("core_reason", ""))
            row["risk_factor"] = " | ".join(row["risk_factor"]) if isinstance(row["risk_factor"], list) else str(row.get("risk_factor", ""))
            output_data.append(row)

        # JSON
        output_json = {
            "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": len(output_data),
            "data": output_data,
        }
        output_path_json = os.path.join(output_dir, "mainboard_second_wave.json")
        with open(output_path_json, "w", encoding="utf-8") as f:
            json.dump(output_json, f, ensure_ascii=False, indent=2)
        print(f"\n[Save] 已保存 JSON: {output_path_json}")

        # CSV（如果被占用则跳过）
        try:
            import time as _time
            ts = datetime.now().strftime("%H%M%S")
            tmp_csv = os.path.join(output_dir, f"_tmp_mainboard_{ts}.csv")
            df_out = pd.DataFrame(output_data)
            df_out.to_csv(tmp_csv, index=False, encoding="utf-8-sig")
            final_csv = os.path.join(output_dir, "mainboard_second_wave.csv")
            try:
                os.remove(final_csv)
            except OSError:
                pass
            os.replace(tmp_csv, final_csv)
            print(f"[Save] 已保存 CSV: {final_csv}")
        except Exception as _e:
            tmp_csv = os.path.join(output_dir, f"mainboard_second_wave_{datetime.now().strftime('%H%M%S')}.csv")
            df_out = pd.DataFrame(output_data)
            df_out.to_csv(tmp_csv, index=False, encoding="utf-8-sig")
            print(f"[Save] CSV被占用，已另存: {tmp_csv}")
    except Exception as e:
        import traceback
        print(f"[Warn] 保存失败: {e}")
        traceback.print_exc()

    print("\n[Done] 扫描完成")


if __name__ == "__main__":
    main()
