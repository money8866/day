#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题综合评分 V2（复用行情缓存）
评分公式：
  THEME_TOTAL_SCORE = 0.30 * trend_score + 0.15 * sentiment_score + 0.25 * persistence_score + 0.15 * leader_score + 0.15 * acceleration_score

详细公式见文件底部注释。
"""
import os
import sys
import json
import time
import sqlite3
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

# 复用相同的 SQLite 缓存数据库
CACHE_DB = os.path.join(CACHE_DIR, 'cache.db')

# 输出 DB（新文件）
OUTPUT_DB = os.path.join(CACHE_DIR, "theme_score_v2.db")

# =========================
# 导入同目录下工具函数（复用数据层）
# =========================
sys.path.insert(0, BASE_DIR)
import theme_trend_sentiment_score as tts

# 复用已有函数
load_theme_json = tts.load_theme_json
get_dc_members = tts.get_dc_members
get_stock_basic = tts.get_stock_basic
get_daily_basic = tts.get_daily_basic
match_theme_stocks = tts.match_theme_stocks
get_daily_kline = tts.get_daily_kline
get_index_kline = tts.get_index_kline
per_stock_features = tts.per_stock_features
_linear = tts.linear
_sigmoid = tts.sigmoid
TRADE_DATE = tts.TRADE_DATE
START_DATE = tts.START_DATE
MIN_STOCKS = tts.MIN_STOCKS
TOP_N_PER_THEME = tts.TOP_N_PER_THEME
N_DAYS = tts.N_DAYS


# =========================
# 评分工具
# =========================
def linear01(x, lo, hi):
    """将x从[lo,hi]线性映射到[0,1]"""
    return _linear(x, lo, hi, 0.0, 1.0)


def compute_trend_score(stock_feats):
    """
    trend_score = 0.15 * ret_1d + 0.30 * ret_5d + 0.35 * ret_10d + 0.20 * ret_20d
    
    各维度使用板块均值的线性映射归一化到0-100
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    ret_1d = np.mean([s["pct_chg"] for s in stock_feats])
    ret_5d = np.mean([s["ret_5"] for s in stock_feats])
    ret_10d = np.mean([s["ret_10"] for s in stock_feats])
    ret_20d = np.mean([s["ret_20"] for s in stock_feats])

    # 各维度映射到0-100
    s1d = linear01(ret_1d, -3, 5) * 100
    s5d = linear01(ret_5d, -10, 20) * 100
    s10d = linear01(ret_10d, -15, 30) * 100
    s20d = linear01(ret_20d, -20, 40) * 100

    score = 0.15 * s1d + 0.30 * s5d + 0.35 * s10d + 0.20 * s20d
    score = max(0, min(100, score))

    detail = {
        "ret_1d": round(ret_1d, 2), "ret_5d": round(ret_5d, 2),
        "ret_10d": round(ret_10d, 2), "ret_20d": round(ret_20d, 2),
        "score_1d": round(s1d, 1), "score_5d": round(s5d, 1),
        "score_10d": round(s10d, 1), "score_20d": round(s20d, 1),
    }
    return round(score, 1), detail


def compute_sentiment_score(stock_feats, market_ret_10=None):
    """
    sentiment_score = 0.3 * 涨停家数占比 + 0.2 * 连板高度 + 0.2 * 人气排名 + 0.3 * 强势股占比
    
    涨停家数占比 = 涨停股票数 / 总股票数
    连板高度 = 板块内最高连板数
    人气排名 = 基于上涨比例
    强势股占比 = 涨幅>=5%的股票占比
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    pcts = [s["pct_chg"] for s in stock_feats]
    zt_count = sum(1 for s in stock_feats if s["zt_flag"] == 1)
    strong_count = sum(1 for s in stock_feats if s["strong_flag"] == 1)
    up_count = sum(1 for p in pcts if p > 0)

    # 涨停家数占比 -> 0-100
    zt_ratio = zt_count / n if n > 0 else 0
    zt_score = linear01(zt_ratio, 0, 0.15) * 100

    # 连板高度 -> 0-100
    lb_heights = [s.get("lb_height", 0) for s in stock_feats]
    max_lb = max(lb_heights) if lb_heights else 0
    lb_score = linear01(max_lb, 0, 6) * 100  # 6板以上算满分

    # 人气排名（上涨占比）-> 0-100
    up_ratio = up_count / n if n > 0 else 0
    popularity_score = linear01(up_ratio, 0.2, 0.85) * 100

    # 强势股占比 -> 0-100
    strong_ratio = strong_count / n if n > 0 else 0
    strong_score = linear01(strong_ratio, 0, 0.25) * 100

    score = 0.3 * zt_score + 0.2 * lb_score + 0.2 * popularity_score + 0.3 * strong_score
    score = max(0, min(100, score))

    detail = {
        "zt_count": zt_count, "zt_ratio": round(zt_ratio * 100, 1),
        "max_lb_height": max_lb, "lb_score": round(lb_score, 1),
        "up_ratio": round(up_ratio * 100, 1), "popularity_score": round(popularity_score, 1),
        "strong_count": strong_count, "strong_ratio": round(strong_ratio * 100, 1),
    }
    return round(score, 1), detail


def load_prev_day_data():
    """
    从 theme_score_v2.db 读取上一交易日的 persistence 和 total_score
    返回: {theme_name: {"persistence": float, "total_score": float}}
    """
    prev_data = {}
    if not os.path.exists(OUTPUT_DB):
        return prev_data

    try:
        # 找到上一个有数据的交易日
        conn = sqlite3.connect(OUTPUT_DB)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM theme_scores WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1", (TRADE_DATE,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return prev_data
        prev_date = row[0]

        cur.execute("SELECT theme, persistence_score, total_score FROM theme_scores WHERE trade_date = ?", (prev_date,))
        for theme, p, ts in cur.fetchall():
            prev_data[theme] = {"persistence": p, "total_score": ts}
        conn.close()
        print(f"[Persistence] 读取上一交易日 {prev_date} 数据: {len(prev_data)} 个主题")
    except Exception as e:
        print(f"[Persistence] 读取历史数据失败: {e}")
    return prev_data


def load_historical_accel(days=5):
    """
    从 theme_score_v2.db 读取最近N个交易日的 acceleration_score
    返回: {theme_name: [day_N_accel, day_N-1_accel, ..., day_1_accel]} 从旧到新
    """
    hist = {}
    if not os.path.exists(OUTPUT_DB):
        return hist

    try:
        conn = sqlite3.connect(OUTPUT_DB)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM theme_scores WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?",
                    (TRADE_DATE, days))
        dates = [row[0] for row in cur.fetchall()]
        dates.sort()  # 从旧到新
        if not dates:
            conn.close()
            return hist

        for d in dates:
            cur.execute("SELECT theme, acceleration_score FROM theme_scores WHERE trade_date = ?", (d,))
            for theme, accel in cur.fetchall():
                hist.setdefault(theme, []).append(accel)
        conn.close()
        print(f"[Accel] 读取 {len(dates)} 个交易日历史加速分: {dates[0]} ~ {dates[-1]}  ({len(hist)} 个主题)")
    except Exception as e:
        print(f"[Accel] 读取历史加速分失败: {e}")
    return hist


def compute_persistence_score(theme_name, trend_score, sentiment_score, prev_data=None):
    """
    persistence_score 指数移动平均计算：

    初始化（无历史数据）：
        persistence = 0.7 * trend_score + 0.3 * sentiment_score

    每日更新（有历史数据）：
        persistence = 0.95 * prev_persistence + 0.05 * prev_total_score
    """
    if not theme_name:
        return 0.0, {}

    if prev_data and theme_name in prev_data:
        prev = prev_data[theme_name]
        score = 0.95 * prev["persistence"] + 0.05 * prev["total_score"]
        detail = {
            "method": "ema_update",
            "prev_persistence": round(prev["persistence"], 1),
            "prev_total_score": round(prev["total_score"], 1),
        }
    else:
        # 无历史数据，用趋势分和情绪分初始化
        score = 0.7 * trend_score + 0.3 * sentiment_score
        detail = {
            "method": "init",
            "trend_score": round(trend_score, 1),
            "sentiment_score": round(sentiment_score, 1),
        }

    score = max(0, min(100, round(score, 1)))
    return score, detail


def compute_leader_score(stock_feats, sentiment_score=None):
    """
    龙头分 - 衡量板块龙头的带动效应

    新公式：
    leader_score =
     0.35 * 龙头趋势（龙头个股10日涨幅）
    + 0.20 * 龙头强度（龙头当日涨幅）
    + 0.20 * 龙头成交额（龙头成交额）
    + 0.15 * 板块共振度（涨停占比 + 平均涨幅 + 情绪分）
    + 0.10 * 中军质量指数（市值>200亿且纯度>=1的成份股数量）

    龙头 = 板块内综合得分最高的股票（涨幅+成交额+纯度）
    中军 = 市值>200亿且纯度>=1的股票（排除龙头）
    """
    if not stock_feats or len(stock_feats) < 3:
        return 0.0, {}

    n = len(stock_feats)

    # 找龙头：按 涨幅*0.3 + 成交额归一化*0.3 + 连板*0.2 + 纯度*0.2 综合评分
    max_amt = max(s.get("amount_latest", 0) for s in stock_feats) or 1
    leader_candidates = []
    for s in stock_feats:
        amt_norm = min(s.get("amount_latest", 0) / max_amt, 1)
        ls = (abs(s["pct_chg"]) * 0.3 + amt_norm * 0.3 +
              min(s.get("lb_height", 0), 10) / 10 * 0.2 +
              min(s.get("purity", 0), 5) / 5 * 0.2)
        leader_candidates.append((s, ls))
    leader_candidates.sort(key=lambda x: x[1], reverse=True)
    leader = leader_candidates[0][0] if leader_candidates else None

    if leader is None:
        return 0.0, {}

    # 龙头趋势：龙头个股的10日涨幅
    leader_trend = leader.get("ret_10", 0)
    leader_trend_score = linear01(leader_trend, -10, 30) * 100

    # 龙头成交额（amount_latest已由per_stock_features除以10万，10=10亿成交额）
    leader_amt = leader.get("amount_latest", 0)
    leader_amt_score = linear01(leader_amt, 0, 50) * 100  # 50亿以上满分

    # 龙头强度：当日涨幅
    leader_pct = leader.get("pct_chg", 0)
    leader_pct_score = linear01(abs(leader_pct), 0, 10) * 100

    # 中军质量指数：市值>200亿且纯度>=1的股票（排除龙头）
    leader_code = leader.get("ts_code", "")
    zhongjun_count = sum(1 for s in stock_feats
                         if s.get("ts_code", "") != leader_code
                         and s.get("total_mv", 0) > 2000000  # 200亿
                         and s.get("purity", 0) >= 1)
    zhongjun_score = linear01(zhongjun_count, 0, 10) * 100

    # 板块共振度 = 涨停数占比 + 平均涨幅 + 情绪分
    zt_count = sum(1 for s in stock_feats if s.get("pct_chg", 0) >= 9.8)
    zt_ratio = zt_count / len(stock_feats) if stock_feats else 0
    avg_pct = np.mean([s.get("pct_chg", 0) for s in stock_feats]) if stock_feats else 0
    zt_ratio_score = linear01(zt_ratio, 0, 0.2) * 100       # 涨停占比0-20%→0-100
    avg_pct_score = linear01(max(avg_pct, 0), 0, 5) * 100   # 平均涨幅0-5%→0-100
    sentiment = sentiment_score if sentiment_score is not None else 50
    resonance_score = (zt_ratio_score + avg_pct_score + sentiment) / 3

    # 新权重：0.35趋势 + 0.20强度 + 0.20成交额 + 0.15共振 + 0.10中军
    score = (0.35 * leader_trend_score + 0.20 * leader_pct_score +
             0.20 * leader_amt_score + 0.15 * resonance_score +
             0.10 * zhongjun_score)
    score = max(0, min(100, score))

    detail = {
        "leader_code": leader.get("ts_code", ""),
        "leader_name": leader.get("name", ""),
        "leader_trend": round(leader_trend, 2),
        "leader_amt": round(leader_amt, 2),
        "leader_pct": round(leader_pct, 2),
        "zhongjun_count": zhongjun_count,
        "leader_trend_score": round(leader_trend_score, 1),
        "leader_amt_score": round(leader_amt_score, 1),
        "leader_pct_score": round(leader_pct_score, 1),
        "zhongjun_score": round(zhongjun_score, 1),
        "zt_ratio": round(zt_ratio, 4),
        "avg_pct": round(avg_pct, 2),
        "resonance_score": round(resonance_score, 1),
    }
    return round(score, 1), detail


def compute_acceleration_score(stock_feats):
    """
    加速度评分 - 识别刚刚启动加速的主题

    核心逻辑：
    短期动量（5日涨幅） vs 中期趋势（20日涨幅）
    如果5日涨幅明显强于20日涨幅 → 主题在加速启动

    评分公式：
    - 计算板块均值 ret_5d 与 ret_20d 的差值
    - 差值映射到0-100，差值越大加速度越高
    """
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    ret_5d = np.mean([s["ret_5"] for s in stock_feats])
    ret_20d = np.mean([s["ret_20"] for s in stock_feats])

    # 加速度 = 5日涨幅 - 20日涨幅（正值表示近期在加速）
    acceleration_raw = ret_5d - ret_20d

    # 映射到0-100：加速度从-10到+10线性映射（负值代表走弱，正值代表加速）
    score = linear01(acceleration_raw, -10, 10) * 100
    score = max(0, min(100, score))

    detail = {
        "ret_5d": round(ret_5d, 2),
        "ret_20d": round(ret_20d, 2),
        "acceleration_raw": round(acceleration_raw, 2),
    }
    return round(score, 1), detail


def save_to_db(results):
    """保存到DB文件"""
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()

    # 先建表，再删旧数据
    cur.execute("""CREATE TABLE IF NOT EXISTS theme_scores (
        rank INTEGER, theme TEXT, n_stocks INTEGER,
        trend_score REAL, sentiment_score REAL, persistence_score REAL, leader_score REAL,
        acceleration_score REAL DEFAULT 0,
        accel_1d REAL DEFAULT 0, accel_3d REAL DEFAULT 0, accel_5d REAL DEFAULT 0,
        switch_score REAL DEFAULT 0,
        risk_score REAL DEFAULT 0, rotation_score REAL DEFAULT 0,
        confirm_score REAL DEFAULT 0, follow_score REAL DEFAULT 0,
        mainline_score REAL DEFAULT 0, entry_score REAL DEFAULT 0,
        total_score REAL,
        high_risk_flag INTEGER DEFAULT 0,
        stage TEXT DEFAULT '', trade_mode TEXT DEFAULT '', label TEXT DEFAULT '', advice TEXT DEFAULT '',
        trend_detail TEXT, sentiment_detail TEXT, persistence_detail TEXT, leader_detail TEXT,
        acceleration_detail TEXT DEFAULT '', trade_date TEXT
    )""")

    # 检查旧表结构，缺少的列自动补充
    cur.execute("PRAGMA table_info(theme_scores)")
    existing_cols = {row[1] for row in cur.fetchall()}
    for col_name, col_type in [("acceleration_score", "REAL DEFAULT 0"),
                                ("acceleration_detail", "TEXT DEFAULT ''"),
                                ("accel_1d", "REAL DEFAULT 0"),
                                ("accel_3d", "REAL DEFAULT 0"),
                                ("accel_5d", "REAL DEFAULT 0"),
                                ("switch_score", "REAL DEFAULT 0"),
                                ("risk_score", "REAL DEFAULT 0"),
                                ("rotation_score", "REAL DEFAULT 0"),
                                ("confirm_score", "REAL DEFAULT 0"),
                                ("follow_score", "REAL DEFAULT 0"),
                                ("mainline_score", "REAL DEFAULT 0"),
                                ("entry_score", "REAL DEFAULT 0"),
                                ("high_risk_flag", "INTEGER DEFAULT 0"),
                                ("stage", "TEXT DEFAULT ''"),
                                ("trade_mode", "TEXT DEFAULT ''"),
                                ("label", "TEXT DEFAULT ''"),
                                ("advice", "TEXT DEFAULT ''")]:
        if col_name not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE theme_scores ADD COLUMN {col_name} {col_type}")
                print(f"[Schema] 新增列 {col_name}")
            except Exception:
                pass

    # 删除当前日期旧数据
    cur.execute("DELETE FROM theme_scores WHERE trade_date = ?", (TRADE_DATE,))

    for i, r in enumerate(results, 1):
        import json as _json
        cur.execute("""
            INSERT OR REPLACE INTO theme_scores
            (rank, theme, n_stocks, trend_score, sentiment_score, persistence_score, leader_score,
             acceleration_score, accel_1d, accel_3d, accel_5d, switch_score,
             risk_score, rotation_score,
             confirm_score, follow_score, mainline_score, entry_score, total_score,
             high_risk_flag, stage, trade_mode, label, advice,
             trend_detail, sentiment_detail, persistence_detail, leader_detail, acceleration_detail, trade_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            i, r["theme"], r["n_stocks"],
            r["trend_score"], r["sentiment_score"], r["persistence_score"], r["leader_score"],
            r["acceleration_score"],
            r.get("accel_1d", 0), r.get("accel_3d", 0), r.get("accel_5d", 0),
            r.get("switch_score", 0),
            r["risk_score"], r["rotation_score"],
            r["confirm_score"], r["follow_score"], r["mainline_score"], r["entry_score"],
            r["total_score"],
            r.get("high_risk_flag", 0),
            r.get("stage", ""), r.get("trade_mode", ""), r.get("label", ""), r.get("advice", ""),
            _json.dumps(r.get("trend_detail", {}), ensure_ascii=False),
            _json.dumps(r.get("sentiment_detail", {}), ensure_ascii=False),
            _json.dumps(r.get("persistence_detail", {}), ensure_ascii=False),
            _json.dumps(r.get("leader_detail", {}), ensure_ascii=False),
            _json.dumps(r.get("acceleration_detail", {}), ensure_ascii=False),
            TRADE_DATE
        ))

    conn.commit()
    conn.close()
    print(f"[Save] DB: {OUTPUT_DB}  ({len(results)} 个主题)")


def print_all_themes_table(results):
    """打印所有主题的分数排名总表（全量数据）"""
    print("\n" + "=" * 130)
    print(f"  全主题排名总表 - {TRADE_DATE}")
    print("=" * 130)
    header = f"{'排名':<4}{'主题':<16}{'总分':<7}{'趋势':<7}{'情绪':<7}{'持续':<7}{'龙头':<7}{'加速':<7}{'开仓':<6}{'切换':<6}{'轮动':<6}{'风险':<6}{'阶段':<8}{'操作建议'}"
    print(header)
    print("-" * 130)
    for r in results:
        label = r.get("label", "")
        stage = r.get("stage", "")
        advice = r.get("advice", "")
        # 简化建议显示
        if len(advice) > 28:
            advice_short = advice[:26] + ".."
        else:
            advice_short = advice
        print(f"{r['rank']:<4}{r['theme']:<16}{r['total_score']:<7}{r['trend_score']:<7}"
              f"{r['sentiment_score']:<7}{r['persistence_score']:<7}{r['leader_score']:<7}"
              f"{r['acceleration_score']:<7}{r['entry_score']:<6}{r['switch_score']:<6}"
              f"{r['rotation_score']:<6}{r['risk_score']:<6}{stage:<8}{advice_short}")
    print("=" * 130)


def print_report(tradable_results):
    """打印可交易主题报告（含个股），同时保存到 report_YYYYMMDD.txt"""
    lines = []
    lines.append("\n" + "=" * 140)
    lines.append("  \u660e\u65e5\u6700\u53ef\u80fd\u8d5a\u94b1\u7684\u65b9\u5411 - " + TRADE_DATE)
    lines.append("=" * 140)

    if not tradable_results:
        lines.append("\n  ❌ 今日无可交易主题")
        lines.append("=" * 140)
    else:
        # 提取预警信息（所有主题共享同一份）
        clustering_warnings = []
        for r in tradable_results:
            if r.get("_clustering_warnings"):
                clustering_warnings = r["_clustering_warnings"]
                break

        lines.append(f"\n  \u5171 {len(tradable_results)} \u4e2a\u53ef\u4ea4\u6613\u4e3b\u9898")
        # 显示同质化预警
        for w in clustering_warnings:
            lines.append(f"  {w}")
        lines.append("")

        for r in tradable_results:
            layer = r.get("label", "")
            lines.append(f"  {'─' * 60}")
            lines.append(f"  {r['rank']}. {r['theme']:<16} {layer:<20}  "
                         f"开仓={r['entry_score']}  切换={r.get('switch_score', 0)}  风险={r['risk_score']}")
            lines.append(f"     主线={r['mainline_score']}  轮动={r['rotation_score']}  确认={r['confirm_score']}  跟随={r['follow_score']}  加速={r['acceleration_score']}")
            lines.append(f"     阶段={r.get('stage', '')}  模式={r.get('trade_mode', '')}")
            lines.append(f"     建议: {r.get('advice', '')}")
            lines.append("")

        lines.append("=" * 140)

    output = "\n".join(lines)
    print(output)

    # 保存到文件
    report_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"report_{TRADE_DATE}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(output + "\n")
    print(f"[Report] 已保存: {report_path}")


def init_data():
    """一次性初始化：加载主题配置、板块成分股、股票基础信息（回溯时只调用一次）"""
    print("=" * 60)
    print("主题综合评分 V2（复用行情缓存）")
    print("=" * 60)

    hot_themes = load_theme_json()
    print(f"[Theme] 加载 {len(hot_themes)} 个主题")

    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    print(f"[Data] stock_basic: {len(stock_basic)}")

    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)

    all_codes = set()
    for tn, m in theme_stock_map.items():
        all_codes.update(m.keys())
    print(f"[Match] 成份股去重: {len(all_codes)} 只")

    return hot_themes, theme_stock_map, name_map_basic, stock_industry, stock_concepts, all_codes, dc_df, stock_basic


def main_with_data(hot_themes, theme_stock_map, name_map_basic, stock_industry, stock_concepts, all_codes, dc_df, stock_basic):
    """使用已初始化的数据进行当日评分"""
    # 读取上一交易日数据（用于持续分EMA计算）
    prev_day_data = load_prev_day_data()

    # 读取历史加速分（用于accel_3d、accel_5d多周期计算）
    historical_accel = load_historical_accel(days=5)

    # 获取当日的 daily_basic（含市值/换手率）
    daily_basic = get_daily_basic()

    # 获取当日K线数据（按需从缓存读取）
    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
    print(f"[KLine] {len(kline_df)} 条记录")

    kline_groups = {}
    if not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub

    results = []

    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            results.append({
                "theme": theme_name, "n_stocks": 0,
                "trend_score": 0, "sentiment_score": 0,
                "persistence_score": 0, "leader_score": 0, "total_score": 0,
                "trend_detail": {}, "sentiment_detail": {},
                "persistence_detail": {}, "leader_detail": {},
                "acceleration_score": 0, "acceleration_detail": {},
                "accel_1d": 0, "accel_3d": 0, "accel_5d": 0, "switch_score": 0,
                "risk_score": 0, "rotation_score": 0,
                "confirm_score": 0, "follow_score": 0, "mainline_score": 0, "entry_score": 0,
                "stage": "", "trade_mode": "", "label": "", "advice": "",
                "high_risk_flag": 0, "label_priority": 9,
                "is_tradable": 0, "trade_layer": "",
            })
            continue

        mcap_dict = {}
        if not daily_basic.empty:
            mcap_dict = {r["ts_code"]: r for _, r in daily_basic.iterrows()}

        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])

        rows = []
        for code, meta in matched.items():
            kdf = kline_groups.get(code)
            if kdf is None or len(kdf) < 6:
                continue
            feat = per_stock_features(kdf)
            if feat is None:
                continue

            # 合并换手率
            if not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)

            # 纯度计算
            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            if tts._in_industry_list(stock_industry.get(code, ""), industry_list):
                purity += 1

            mv = mcap_dict.get(code, {}).get("total_mv", 0) or 0
            feat["ts_code"] = code
            feat["name"] = name_map_basic.get(code, code)
            feat["purity"] = purity
            feat["total_mv"] = mv
            feat["industry_match"] = meta.get("industry_match", False)
            rows.append(feat)

        if len(rows) < MIN_STOCKS:
            results.append({
                "theme": theme_name, "n_stocks": len(rows),
                "trend_score": 0, "sentiment_score": 0,
                "persistence_score": 0, "leader_score": 0, "total_score": 0,
                "trend_detail": {}, "sentiment_detail": {},
                "persistence_detail": {}, "leader_detail": {},
                "acceleration_score": 0, "acceleration_detail": {},
                "accel_1d": 0, "accel_3d": 0, "accel_5d": 0, "switch_score": 0,
                "risk_score": 0, "rotation_score": 0,
                "confirm_score": 0, "follow_score": 0, "mainline_score": 0, "entry_score": 0,
                "stage": "", "trade_mode": "", "label": "", "advice": "",
                "high_risk_flag": 0, "label_priority": 9,
                "is_tradable": 0, "trade_layer": "",
            })
            continue

        # 趋势分用TOP30（按市值权重排序）
        for r in rows:
            r["mcap_w"] = (r["total_mv"] / 10000) ** 0.5 * 0.8 + r["purity"] * 2
            r["mcap_w"] *= 1.0 if r["industry_match"] else 0.5
        rows.sort(key=lambda x: x["mcap_w"], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]

        # 计算五维评分
        trend_score, trend_detail = compute_trend_score(top_rows)
        sentiment_score, sentiment_detail = compute_sentiment_score(rows)  # 情绪用全量
        persistence_score, persistence_detail = compute_persistence_score(
            theme_name, trend_score, sentiment_score, prev_day_data
        )
        leader_score, leader_detail = compute_leader_score(top_rows, sentiment_score)

        # ===== 单日加速分（原始加速分）=====
        accel_1d, acceleration_detail = compute_acceleration_score(top_rows)
        if accel_1d == 0:
            print(f"  [Accel=0] {theme_name}: ret_5d={acceleration_detail.get('ret_5d', '?')}, "
                  f"ret_20d={acceleration_detail.get('ret_20d', '?')}, "
                  f"raw={acceleration_detail.get('acceleration_raw', '?')}")

        # ===== 多周期加速分 =====
        hist = historical_accel.get(theme_name, [])
        accel_3d_raw = accel_1d - (hist[-3] if len(hist) >= 3 else accel_1d)
        accel_5d_raw = accel_1d - (hist[-5] if len(hist) >= 5 else accel_1d)
        accel_3d = linear01(accel_3d_raw, -50, 50) * 100
        accel_5d = linear01(accel_5d_raw, -50, 50) * 100

        # 最终资金加速分
        acceleration_score = (0.50 * accel_1d + 0.30 * accel_3d + 0.20 * accel_5d)
        acceleration_score = max(0, min(100, round(acceleration_score, 1)))

        # 风险分
        ret_5d = acceleration_detail.get("ret_5d", 0)
        max_lb_height = sentiment_detail.get("max_lb_height", 0)
        ret_5d_score = linear01(max(ret_5d, 0), 0, 10) * 100
        lb_height_score = linear01(max_lb_height, 0, 6) * 100
        risk_score = (0.5 * sentiment_score + 0.3 * ret_5d_score + 0.2 * lb_height_score)
        risk_score = round(risk_score, 1)

        # 轮动分
        rotation_score = (0.5 * acceleration_score + 0.3 * leader_score -
                          0.2 * sentiment_score)
        rotation_score = max(0, min(100, round(rotation_score, 1)))

        # 确认分
        confirm_score = (0.35 * trend_score + 0.30 * sentiment_score +
                         0.20 * persistence_score + 0.15 * leader_score)
        confirm_score = round(max(0, min(100, confirm_score)), 1)

        # 跟随分
        follow_score = (0.4 * sentiment_score + 0.3 * persistence_score +
                        0.3 * leader_score)
        follow_score = round(max(0, min(100, follow_score)), 1)

        n = len(rows)
        sample_penalty = 0.8 if n < 10 else 1.0

        # 主线强度（新公式）
        mainline_score = (0.25 * confirm_score + 0.25 * follow_score +
                          0.30 * rotation_score + 0.20 * trend_score)
        mainline_score = round(max(0, min(100, mainline_score)), 1)

        # 开仓价值——不扣减风险
        entry_score = (0.4 * rotation_score + 0.3 * confirm_score +
                       0.3 * follow_score)
        entry_score = round(max(0, min(100, entry_score * sample_penalty)), 1)
        rotation_adj = round(rotation_score * sample_penalty, 1)

        # 主线切换指数
        switch_score = (0.6 * acceleration_score + 0.2 * confirm_score +
                        0.2 * follow_score)
        switch_score = max(0, min(100, round(switch_score, 1)))

        # ============================================================
        # 阶段判断（高潮需加速度下降）
        # ============================================================
        if sentiment_score >= 80 and acceleration_score < 50:
            stage = "\u9ad8\u6f6e"  # 高潮
        elif confirm_score >= 50 and follow_score >= 50:
            stage = "\u4e3b\u5347"  # 主升
        elif rotation_adj >= 45 and confirm_score >= 30 and confirm_score < 50:
            stage = "\u53d1\u9175"  # 发酵
        elif rotation_adj >= 50 and confirm_score < 30:
            stage = "\u542f\u52a8"  # 启动
        elif rotation_adj < 30 and confirm_score < 35:
            stage = "\u9000\u6f6e"  # 退潮
        else:
            stage = "\u9707\u8361"  # 震荡

        # 主线确认增强
        if stage == "\u542f\u52a8" and acceleration_score > 60 and confirm_score > 40 and follow_score > 40:
            stage = "\u53d1\u9175"  # 发酵

        # ============================================================
        # 交易模式
        # ============================================================
        if stage == "\u542f\u52a8":  # 启动
            trade_mode = "\u8865\u6da8/\u9996\u677f"  # 补涨/首板
        elif stage == "\u53d1\u9175":  # 发酵
            trade_mode = "\u9f99\u5934+\u4e2d\u519b"  # 龙头+中军
        elif stage == "\u4e3b\u5347":  # 主升
            trade_mode = "\u9f99\u5934+\u8d8b\u52bf\u4e2d\u519b"  # 龙头+趋势中军
        elif stage == "\u9ad8\u6f6e":  # 高潮
            trade_mode = "\u51cf\u4ed3/\u6301\u80a1"  # 减仓/持股
        elif stage == "\u9000\u6f6e":  # 退潮
            trade_mode = "\u56de\u907f"  # 回避
        elif persistence_score > 50 and rotation_adj < 30:
            trade_mode = "\u4f4e\u5438"  # 低吸
        else:
            trade_mode = "\u89c2\u5bdf"  # 观察

        # ============================================================
        # 标签系统（精简：仅预分配回避 / 未定）
        # ============================================================
        high_risk_flag = 1 if (sentiment_score > 85 and acceleration_score < 50) else 0

        # 回避条件
        if entry_score < 30:
            label = "\u274c \u56de\u907f"  # ❌ 回避
        else:
            label = ""  # 留空，后处理统一判定

        # ============================================================
        # 操作建议
        # ============================================================
        if label == "\u274c \u56de\u907f":
            advice = "\u8d44\u91d1\u5173\u6ce8\u5ea6\u4e0d\u8db3\uff0c\u6682\u65e0\u660e\u663e\u4ea4\u6613\u4ef7\u503c"
        else:
            advice = "\u7b49\u5f85\u660e\u786e\u4fe1\u53f7"

        # ============================================================
        # 可交易主题过滤条件 + 分层
        # ============================================================
        # 5条条件，满足≥2条可交易
        cond_entry = 1 if entry_score >= 40 else 0
        cond_rotation = 1 if rotation_adj >= 50 else 0
        cond_switch = 1 if switch_score >= 55 else 0
        cond_mainline = 1 if mainline_score >= 48 else 0
        cond_accel = 1 if (acceleration_score >= 60 and accel_3d > 0) else 0
        tradable_conditions = cond_entry + cond_rotation + cond_switch + cond_mainline + cond_accel
        is_tradable = 1 if tradable_conditions >= 2 else 0

        # 分层
        trade_layer = ""
        if is_tradable:
            if switch_score >= 60 and rotation_adj > 50:
                trade_layer = "\U0001f525 \u65b0\u4e3b\u7ebf\u5019\u9009"  # 🔥 新主线候选
            elif confirm_score > 50 and follow_score > 50:
                trade_layer = "\U0001f7e2 \u4e3b\u5347\u4e3b\u9898"  # 🟢 主升主题
            elif rotation_adj > 50 and confirm_score < 50:
                trade_layer = "\U0001f7e1 \u8f6e\u52a8\u4e3b\u9898"  # 🟡 轮动主题
            else:
                trade_layer = "\U0001f504 \u53cd\u62bd\u4e3b\u9898"  # 🔄 反抽主题

        # 总分
        total_score = (0.30 * trend_score + 0.15 * sentiment_score +
                       0.25 * persistence_score + 0.15 * leader_score +
                       0.15 * acceleration_score)
        total_score = round(total_score, 1)

        results.append({
            "theme": theme_name, "n_stocks": n,
            "trend_score": trend_score, "sentiment_score": sentiment_score,
            "persistence_score": persistence_score, "leader_score": leader_score,
            "acceleration_score": acceleration_score,
            "accel_1d": round(accel_1d, 1), "accel_3d": round(accel_3d, 1),
            "accel_5d": round(accel_5d, 1), "switch_score": switch_score,
            "risk_score": risk_score, "high_risk_flag": high_risk_flag,
            "rotation_score": rotation_adj, "confirm_score": confirm_score,
            "follow_score": follow_score, "mainline_score": mainline_score,
            "entry_score": entry_score,
            "stage": stage, "trade_mode": trade_mode, "label": label,
            "advice": advice, "total_score": total_score,
            "is_tradable": is_tradable,
            "trade_layer": trade_layer,
            "trend_detail": trend_detail, "sentiment_detail": sentiment_detail,
            "persistence_detail": persistence_detail, "leader_detail": leader_detail,
            "acceleration_detail": acceleration_detail,
        })

    # ================================================================
    # 后处理：最终交易收敛规则（输出=明日最可能赚钱的5个方向）
    # ================================================================

    # 计算市场加速均值
    accel_values = [r["acceleration_score"] for r in results if r["acceleration_score"] > 0]
    market_avg_accel = np.mean(accel_values) if accel_values else 30

    # ----- 阶段1：主题去重（成份股重合度>30%则合并为强者）-----
    theme_stock_set = {}
    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        theme_stock_set[theme_name] = set(matched.keys())

    def overlap_pct(set_a, set_b):
        if not set_a or not set_b:
            return 0
        return len(set_a & set_b) / min(len(set_a), len(set_b))

    merged_map = {}
    all_theme_names = list(hot_themes.keys())
    for i in range(len(all_theme_names)):
        for j in range(i + 1, len(all_theme_names)):
            ta, tb = all_theme_names[i], all_theme_names[j]
            sa = theme_stock_set.get(ta, set())
            sb = theme_stock_set.get(tb, set())
            if overlap_pct(sa, sb) > 0.30:
                ra = next((r for r in results if r["theme"] == ta), None)
                rb = next((r for r in results if r["theme"] == tb), None)
                if ra is None or rb is None:
                    continue
                if ra["mainline_score"] >= rb["mainline_score"]:
                    merged_map[tb] = ta
                else:
                    merged_map[ta] = tb

    for r in results:
        if r["theme"] in merged_map:
            if r["label"] not in ("\u274c \u56de\u907f",):
                r["label"] = "\u26aa \u89c2\u5bdf"

    # ----- 阶段2：高潮→强制主线，主线锁定检查-----
    for r in results:
        if r.get("stage") == "\u9ad8\u6f6e" and r["label"] != "\u274c \u56de\u907f":
            r["label"] = "\U0001f525 \u5f53\u524d\u4e3b\u7ebf"

    confirm_ranked = sorted([r for r in results if r["label"] != "\u274c \u56de\u907f"],
                            key=lambda x: x["confirm_score"], reverse=True)
    rotation_ranked = sorted([r for r in results if r["label"] != "\u274c \u56de\u907f"],
                             key=lambda x: x["rotation_score"], reverse=True)

    non_huibi = [r for r in results
                 if r["label"] not in ("\u274c \u56de\u907f", "\U0001f525 \u5f53\u524d\u4e3b\u7ebf")
                 and r.get("stage") != "\u9ad8\u6f6e"]
    non_huibi.sort(key=lambda r: r["switch_score"], reverse=True)
    top_switch_score = non_huibi[0]["switch_score"] if non_huibi else 0

    for r in results:
        if r["label"] == "\U0001f525 \u5f53\u524d\u4e3b\u7ebf":
            confirm_rank = next((i + 1 for i, x in enumerate(confirm_ranked) if x["theme"] == r["theme"]), 99)
            rotation_rank = next((i + 1 for i, x in enumerate(rotation_ranked) if x["theme"] == r["theme"]), 99)
            if not (confirm_rank <= 2 and rotation_rank <= 2 and r["switch_score"] < top_switch_score + 10):
                r["label"] = ""  # 主线降级

    # ----- 阶段3：硬重置标签（仅保留 回避）后重新分配-----
    for r in results:
        if r["label"] not in ("\u274c \u56de\u907f",):
            r["label"] = ""

    # ----- 阶段4：新主线候选唯一（switch_score TOP1，0~1个）-----
    switch_candidates = [r for r in results
                         if r["label"] not in ("\u274c \u56de\u907f",)
                         and r.get("stage") != "\u9ad8\u6f6e"]
    switch_candidates.sort(key=lambda r: r["switch_score"], reverse=True)

    candidate_assigned = False
    for r in switch_candidates:
        if not candidate_assigned and r["switch_score"] >= 55:
            if (r["acceleration_score"] > market_avg_accel + 20 and
                r["confirm_score"] < 50 and
                r["rotation_score"] > 50):
                r["label"] = "\U0001f680 \u65b0\u4e3b\u7ebf\u5019\u9009"
                candidate_assigned = True

    # ----- 阶段5：当前主线唯一（综合评分 TOP1）-----
    mainline_candidates = [r for r in results
                           if r["label"] not in ("\U0001f680 \u65b0\u4e3b\u7ebf\u5019\u9009",
                                                  "\u274c \u56de\u907f")]
    for r in mainline_candidates:
        r["_mainline_score_weighted"] = r["confirm_score"] * 0.5 + r["rotation_score"] * 0.3 + r["mainline_score"] * 0.2
    mainline_candidates.sort(key=lambda r: r["_mainline_score_weighted"], reverse=True)

    mainline_assigned = False
    for r in mainline_candidates:
        if not mainline_assigned and r["mainline_score"] >= 48:
            r["label"] = "\U0001f525 \u5f53\u524d\u4e3b\u7ebf"
            mainline_assigned = True

    # ----- 阶段6：弱轮动剔除（entry<40 且 switch<55 → 观察）-----
    for r in results:
        if r["label"] == "":
            if r["entry_score"] < 40 and r["switch_score"] < 55:
                r["label"] = "\u26aa \u89c2\u5bdf"

    # ----- 阶段7：轮动收敛 rank_score = 0.4*rotation + 0.3*entry + 0.3*switch -----
    lundong_candidates = [r for r in results
                          if r["label"] not in ("\U0001f525 \u5f53\u524d\u4e3b\u7ebf",
                                                 "\U0001f680 \u65b0\u4e3b\u7ebf\u5019\u9009",
                                                 "\u274c \u56de\u907f",
                                                 "\u26aa \u89c2\u5bdf")]
    for r in lundong_candidates:
        r["_rank_score"] = 0.4 * r["rotation_score"] + 0.3 * r["entry_score"] + 0.3 * r["switch_score"]
    lundong_candidates.sort(key=lambda r: r["_rank_score"], reverse=True)

    lundong_assigned = 0
    for r in lundong_candidates:
        if lundong_assigned < 3:
            r["label"] = "\U0001f7e1 \u8f6e\u52a8\u65b9\u5411"
            lundong_assigned += 1
        else:
            r["label"] = "\u26aa \u89c2\u5bdf"

    # ----- 阶段8：轮动内部排序（switch>entry>acceleration）-----
    lundong_themes = [r for r in results if r["label"] == "\U0001f7e1 \u8f6e\u52a8\u65b9\u5411"]
    lundong_themes.sort(key=lambda r: (r["switch_score"], r["entry_score"], r["acceleration_score"]), reverse=True)
    for i, r in enumerate(lundong_themes):
        r["_lundong_rank"] = i + 1

    # ----- 阶段9：剩余统一标记为观察 -----
    for r in results:
        if r["label"] == "":
            r["label"] = "\u26aa \u89c2\u5bdf"

    # ================================================================
    # 动态交易建议生成（基于阶段+风险分）
    # ================================================================
    def dynamic_advice(stage, risk_score, is_mainline=False, is_candidate=False):
        """根据阶段和风险分动态生成交易建议"""
        stage_map = {
            "\u4e3b\u5347": {
                "low_risk": "\u5f53\u524d\u5e02\u573a\u6838\u5fc3\u4e3b\u7ebf\uff0c\u8d44\u91d1\u62b1\u56e2\u7d27\u5bc6\u3002\u56f4\u7ed5\u6838\u5fc3\u9f99\u5934\u6301\u80a1\u6216\u5728\u9996\u6b21\u5206\u6b67\u65f6\u4f4e\u5438\u6838\u5fc3\u4e2d\u519b\uff0c\u7edd\u4e0d\u8f7b\u6613\u505a\u7a7a\u3002",
                "high_risk": "\u4e3b\u5347\u4f46\u98ce\u9669\u8fc7\u9ad8\uff0c\u9700\u5f53\u5fc3\u5206\u6b67\u56de\u8c03\u3002\u5efa\u8bae\u51cf\u4ed3\u6301\u80a1\uff0c\u4e0d\u8ffd\u9ad8\u3002"
            },
            "\u53d1\u9175": {
                "low_risk": "\u8d44\u91d1\u6301\u7eed\u8bd5\u9519\u53d1\u9175\u4e2d\u3002\u53ef\u9002\u5ea6\u4ed3\u4f4d\u53c2\u4e0e\u524d\u6392\u8fde\u677f\u8bd5\u9519\uff0c\u6216\u4f4e\u5438\u8fa8\u8bc6\u5ea6\u4e2d\u519b\uff0c\u786e\u8ba4\u4e3b\u5347\u540e\u52a0\u4ed3\u3002",
                "high_risk": "\u53d1\u9174\u4f46\u98ce\u9669\u504f\u9ad8\uff0c\u8d44\u91d1\u52a8\u80fd\u4e0d\u8db3\u3002\u5efa\u8bae\u89c2\u671b\uff0c\u7b49\u5f85\u660e\u786e\u4fe1\u53f7\u518d\u8fdb\u573a\u3002"
            },
            "\u9ad8\u6f6e": {
                "low_risk": "\u677f\u5757\u60c5\u7eea\u9ad8\u6f6e\uff0c\u4f46\u98ce\u9669\u53ef\u63a7\u3002\u6301\u7b79\u8005\u53ef\u7ee7\u7eed\u6301\u80a1\uff0c\u65b0\u4ed3\u9700\u7b49\u5206\u6b67\u56de\u8c03\u3002",
                "high_risk": "\u26a0\ufe0f \u677f\u5757\u60c5\u7eea\u6781\u5ea6\u9ad8\u6f6e\uff0c\u6f5c\u5728\u5206\u6b67\u98ce\u9669\u5de8\u5927\u3002\u6301\u7b79\u8005\u8003\u8651\u9022\u9ad8\u51cf\u4ed3\uff0c\u6301\u5e01\u8005\u7ba1\u4f4f\u624b\uff0c\u7edd\u4e0d\u63a5\u76d8\u63a5\u529b\u3002"
            },
        }
        # 默认建议（非主升/发酵/高潮）
        default_low = "\u677f\u5757\u5904\u4e8e\u652f\u7ebf\u8f6e\u52a8\u671f\u3002\u4e25\u7981\u8ffd\u9ad8\u4e70\u5165\uff0c\u4ec5\u9002\u5408\u5728\u677f\u5757\u51b0\u70b9\u56de\u8e29\u6838\u5fc3\u5747\u7ebf\u65f6\u6f5c\u4f0f\uff0c\u7b49\u5f85\u8f6e\u52a8\u62c9\u5347\u5356\u51fa\u3002"
        default_high = "\u9ad8\u98ce\u9669\u9884\u8b66\uff0c\u8bf7\u4e25\u683c\u63a7\u5236\u4ed3\u4f4d\u3002"

        # 使用 risk_score 判断高低风险（阈值45）
        if risk_score < 45:
            risk_level = "low_risk"
        else:
            risk_level = "high_risk"

        if stage in stage_map:
            return stage_map[stage].get(risk_level, default_low)
        else:
            return default_low if risk_score < 45 else default_high

    for r in results:
        stage = r.get("stage", "")
        risk_score = r.get("risk_score", 0)
        label = r.get("label", "")
        if label == "\u274c \u56de\u907f":
            r["advice"] = "\u8d44\u91d1\u5173\u6ce8\u5ea6\u4e0d\u8db3\uff0c\u6682\u65e0\u660e\u663e\u4ea4\u6613\u4ef7\u503c"
        else:
            is_mainline = (label == "\U0001f525 \u5f53\u524d\u4e3b\u7ebf")
            is_candidate = (label == "\U0001f680 \u65b0\u4e3b\u7ebf\u5019\u9009")
            r["advice"] = dynamic_advice(stage, risk_score, is_mainline, is_candidate)

    # 排序
    label_order = {
        "\U0001f680 \u65b0\u4e3b\u7ebf\u5019\u9009": 0,
        "\U0001f525 \u5f53\u524d\u4e3b\u7ebf": 1,
        "\U0001f7e1 \u8f6e\u52a8\u65b9\u5411": 2,
        "\u26aa \u89c2\u5bdf": 3,
        "\u274c \u56de\u907f": 4,
    }
    for r in results:
        r["label_order"] = label_order.get(r["label"], 9)

    results.sort(key=lambda r: (r["label_order"], -r["entry_score"]))
    for i, r in enumerate(results, 1):
        r["rank"] = i

    # 提取可交易主题（主线+新主线候选+轮动）
    tradable = [r for r in results if r["label"] in (
        "\U0001f680 \u65b0\u4e3b\u7ebf\u5019\u9009",
        "\U0001f525 \u5f53\u524d\u4e3b\u7ebf",
        "\U0001f7e1 \u8f6e\u52a8\u65b9\u5411")]

    # ================================================================
    # 主题聚类与同质化预警（Jaccard相似度）
    # ================================================================
    clustering_warnings = []
    if len(tradable) >= 2:
        # 收集各可交易主题的Top10成分股
        theme_top10_stocks = {}
        for r in tradable:
            theme_name = r["theme"]
            matched = theme_stock_map.get(theme_name, {})
            # 取Top10个股code
            top_codes = set(list(matched.keys())[:10])
            theme_top10_stocks[theme_name] = top_codes

        # 两两计算Jaccard相似度
        theme_names = list(theme_top10_stocks.keys())
        cluster_groups = []  # [(group_name, [theme_names])]
        visited = set()
        for i in range(len(theme_names)):
            if theme_names[i] in visited:
                continue
            group = [theme_names[i]]
            visited.add(theme_names[i])
            for j in range(i + 1, len(theme_names)):
                if theme_names[j] in visited:
                    continue
                set_a = theme_top10_stocks[theme_names[i]]
                set_b = theme_top10_stocks[theme_names[j]]
                if not set_a or not set_b:
                    continue
                jaccard = len(set_a & set_b) / len(set_a | set_b)
                if jaccard > 0.40:
                    group.append(theme_names[j])
                    visited.add(theme_names[j])
            if len(group) >= 2:
                cluster_groups.append(group)

        # 生成预警
        for group in cluster_groups:
            warning = (f"\u26a0\ufe0f \u8d44\u91d1\u6781\u5ea6\u805a\u7126\u3010{'/'.join(group)}\u3011\n"
                       f"    \u5408\u5e76\u540c\u7c7b\u9879\uff0c\u8bf7\u6ce8\u610f\u677f\u5757\u96c6\u4e2d\u5ea6\u8fc7\u9ad8\u98ce\u9669\uff01")
            clustering_warnings.append(warning)

    # 将预警注入 tradable 结果（print_report 中读取）
    for r in tradable:
        r["_clustering_warnings"] = clustering_warnings

    # 总结构强制压缩 ≤5（由收敛逻辑保证）
    print_all_themes_table(results)
    print_report(tradable)
    save_to_db(results)


def main():
    """单日运行模式：初始化+当日评分"""
    (hot_themes, theme_stock_map, name_map_basic, stock_industry,
     stock_concepts, all_codes, dc_df, stock_basic) = init_data()
    main_with_data(hot_themes, theme_stock_map, name_map_basic,
                   stock_industry, stock_concepts, all_codes, dc_df, stock_basic)


def backfill_last_n_days(n_days=20):
    """
    批量回溯最近N个交易日（从旧到新，确保EMA持续分正确积累）
    """
    print("=" * 80)
    print(f"批量回溯最近 {n_days} 个交易日（从旧到新）")
    print("=" * 80)

    # 获取交易日历
    from datetime import date as dt_date
    today = dt_date.today()
    start_cal = (datetime.now() - timedelta(days=n_days * 2)).strftime("%Y%m%d")
    end_cal = datetime.now().strftime("%Y%m%d")

    try:
        pro_obj = tts.pro
        if pro_obj is None:
            print("[Backfill] 无tushare，无法获取交易日历")
            return
        cal = pro_obj.trade_cal(exchange='', start_date=start_cal, end_date=end_cal)
        cal = cal[cal['is_open'] == 1]
        trade_dates = sorted(cal['cal_date'].tolist(), reverse=True)[:n_days]
        trade_dates.sort()  # 从旧到新
    except Exception as e:
        print(f"[Backfill] 获取交易日历失败: {e}")
        # 兜底：用自然日倒退
        trade_dates = []
        d = datetime.strptime(tts.TRADE_DATE, "%Y%m%d")
        while len(trade_dates) < n_days:
            ds = d.strftime("%Y%m%d")
            if d.weekday() < 5:  # 跳过周末
                trade_dates.append(ds)
            d -= timedelta(days=1)
        trade_dates.sort()

    print(f"待处理 {len(trade_dates)} 个交易日: {trade_dates[0]} ~ {trade_dates[-1]}")

    # ====== 一次性初始化（板块成分股、股票基础信息） ======
    (hot_themes, theme_stock_map, name_map_basic, stock_industry,
     stock_concepts, all_codes, dc_df, stock_basic) = init_data()

    for i, target_date in enumerate(trade_dates, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(trade_dates)}] 处理 {target_date}")
        print(f"{'='*60}")

        # 设置全局变量
        global TRADE_DATE, START_DATE
        TRADE_DATE = target_date
        START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")

        # 同步tts模块的全局变量
        tts.TRADE_DATE = TRADE_DATE
        tts.START_DATE = START_DATE

        try:
            main_with_data(hot_themes, theme_stock_map, name_map_basic,
                           stock_industry, stock_concepts, all_codes, dc_df, stock_basic)
        except Exception as e:
            print(f"[Backfill] {target_date} 失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n[全部完成] 共处理 {len(trade_dates)} 个交易日")


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        cmd = sys.argv[1]
        if cmd == "backfill":
            n_days = int(sys.argv[2]) if len(sys.argv) >= 3 else 20
            backfill_last_n_days(n_days)
        else:
            # 支持指定交易日
            TRADE_DATE = cmd
            START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
            print(f"[V2] 指定日期: {TRADE_DATE}")
            tts.TRADE_DATE = TRADE_DATE
            tts.START_DATE = START_DATE
            main()
    else:
        main()
