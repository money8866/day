# -*- coding: utf-8 -*-
"""
主板高辨识度龙头 · 第二波主升浪识别系统 v2
================================================

任务：寻找未来3~12个月最有可能走出第二波/第三波机构抱团的核心中军资产。

算法架构：
    1. 产业主线过滤器（industry_strength >= 60）
    2. 高辨识度 Recognition Score（市场记忆）
    3. Leader Persistence Score（龙头持续性）
    4. 二波潜力 Second Wave Score
    5. 终极牛股 Ultimate Score
    6. 强制过滤器 + 升级过滤器
    7. 最终 S+/S/A/B 评级

运行:
    python mainboard_v2_scanner.py
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, 'multi_factor_picker'))
from data_fetcher import DataFetcher

CACHE_TUSHARE = os.path.join(BASE_DIR, "cache_backbone_tushare")
KLINE_CACHE = os.path.join(BASE_DIR, "cache_daily")
DC_HOT_DIR = os.path.join(CACHE_TUSHARE, "dc_hot")

THEME_SCORE_DB = os.path.join(CACHE_TUSHARE, "theme_trend_sentiment.db")
PORTFOLIO_DB = os.path.join(CACHE_TUSHARE, "theme_portfolio.db")

# ============================================================
# 工具函数
# ============================================================
def is_mainboard(code):
    """主板+双创板（SH: 6开头；SZ: 0/3开头）"""
    try:
        symbol = str(code).split(".")[0]
        if str(code).endswith(".SH") and symbol.startswith("6"):
            return True
        if str(code).endswith(".SZ") and (symbol.startswith("00") or symbol.startswith("30")):
            return True
    except Exception:
        pass
    return False


def load_kline(code, lookback=240):
    """从 cache_daily 读取 K 线"""
    path = os.path.join(KLINE_CACHE, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or len(df) < 60:
            return None
        df["trade_date"] = df["trade_date"].astype(str)
        df = df.sort_values("trade_date").reset_index(drop=True)
        if len(df) > lookback + 30:
            df = df.iloc[-(lookback + 30):].reset_index(drop=True)
        return df
    except Exception:
        return None


# 北向资金持股比例缓存（trade_date -> {ts_code: hold_ratio}）
_NORTH_HOLD_CACHE = {}


def load_north_hold_batch(trade_date):
    """
    批量加载北向资金持股比例（沪深股通）

    注：tushare的 hk_hold(trade_date=...) 返回港股通南向数据，
    北向资金持股（沪深股通）需用 hk_hold(ts_code=...) 按股票查询。
    本函数返回空字典作为占位，实际北向持股查询在 compute_stock_features 中按需进行。

    Returns: {} 空字典（实际查询改用按股票模式）
    """
    return {}


# DataFetcher 单例（复用统一速率锁与 parquet/JSON 缓存）
_df_singleton = None


def _get_df():
    """获取 DataFetcher 单例（懒加载，复用 TUSHARE_TOKEN）"""
    global _df_singleton
    if _df_singleton is not None:
        return _df_singleton
    try:
        from data_fetcher import DataFetcher
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            env_path = os.path.join(BASE_DIR, '.env')
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('TUSHARE_TOKEN=') and not line.startswith('#'):
                            token = line.split('=', 1)[1].strip()
                            break
        if not token:
            return None
        config = {
            'cache': {'enabled': True, 'dir': os.path.join(BASE_DIR, 'multi_factor_picker', 'cache'), 'expire_hours': 168},
            'tushare': {'max_retry': 3, 'retry_delay': 5},
        }
        _df_singleton = DataFetcher(token, config)
    except Exception:
        return None
    return _df_singleton


def _query_north_hold_single(ts_code):
    """按股票代码查询北向持股比例（复用DataFetcher统一缓存）"""
    df = _get_df()
    if df is None:
        return 0.0
    try:
        info = df.get_hk_hold_by_code(ts_code)
        return float(info.get('ratio', 0.0)) if info else 0.0
    except Exception:
        return 0.0


def norm(value, vmin, vmax, reverse=False):
    """归一化 0-100"""
    if vmax == vmin:
        return 50.0
    ratio = (value - vmin) / (vmax - vmin)
    if reverse:
        ratio = 1 - ratio
    return max(0.0, min(100.0, ratio * 100))


# ============================================================
# 数据加载
# ============================================================
def load_theme_portfolio():
    """从 theme_portfolio.db 读取成分股"""
    mapping = {}
    if not os.path.exists(PORTFOLIO_DB):
        return mapping
    try:
        conn = sqlite3.connect(PORTFOLIO_DB)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM portfolio ORDER BY trade_date DESC LIMIT 1")
        latest = cur.fetchone()
        if not latest:
            conn.close()
            return mapping
        latest_date = latest[0]
        print(f"[Data] 读取 {latest_date} 主题成分股")
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
                "mcap_yi": float(mcap or 0),
                "turnover_yi": float(turnover or 0),
                "purity": int(purity or 0),
            }
        conn.close()
        print(f"[Data] 共 {len(mapping)} 只成分股")
        return mapping
    except Exception as e:
        print(f"[Warn] theme_portfolio.db 读取失败: {e}")
        return mapping


def load_theme_scores(days=180):
    """从 theme_trend_sentiment.db 读取历史主题评分"""
    scores = defaultdict(list)
    if not os.path.exists(THEME_SCORE_DB):
        return scores
    try:
        conn = sqlite3.connect(THEME_SCORE_DB)
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT {days}")
        dates = sorted([r[0] for r in cur.fetchall()])
        for d in dates:
            cur.execute(
                "SELECT theme, composite_score, trend_score, sentiment_score, rank, top10_days_10d, top10_days_20d, ret_5, ret_10, ret_20 FROM theme_scores WHERE trade_date = ?",
                (d,),
            )
            for row in cur.fetchall():
                theme, comp, trend, sent, rank, t10, t20, r5, r10, r20 = row
                scores[theme].append({
                    "trade_date": d,
                    "composite_score": float(comp or 0),
                    "trend_score": float(trend or 0),
                    "sentiment_score": float(sent or 0),
                    "rank": int(rank or 0),
                    "top10_days_10d": int(t10 or 0),
                    "top10_days_20d": int(t20 or 0),
                    "ret_5": float(r5 or 0),
                    "ret_10": float(r10 or 0),
                    "ret_20": float(r20 or 0),
                })
        conn.close()
        print(f"[Data] 共 {len(scores)} 个主题，近 {len(dates)} 天数据")
        return scores
    except Exception as e:
        print(f"[Warn] theme_scores 读取失败: {e}")
        return scores


def load_stock_hot_days(days=120):
    """读取 120 天热榜，统计每只股票出现次数"""
    stock_hot = defaultdict(lambda: {"dc_days": 0, "total_score": 0.0})
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
            code_col = None
            for col in ["code", "ts_code", "股票代码"]:
                if col in df.columns:
                    code_col = col
                    break
            if code_col is None:
                code_col = df.columns[0]
            for idx, row in df.iterrows():
                code = str(row[code_col]).strip()
                if not code or code.lower() in ["nan", "none", ""]:
                    continue
                if "." not in code and len(code) == 6:
                    code = (code + ".SH") if code.startswith("6") else (code + ".SZ")
                stock_hot[code]["dc_days"] += 1
                rank_val = min(100, max(1, int(idx) + 1))
                stock_hot[code]["total_score"] += 100.0 - rank_val + 1
        except Exception:
            pass
    print(f"[Data] 热榜 {sum(1 for v in stock_hot.values() if v['dc_days'] > 0)} 只股票上榜")
    return dict(stock_hot)


def load_financial_data():
    """从 tushare fundamental 缓存读取财务数据"""
    fin = {}
    db_path = os.path.join(CACHE_TUSHARE, "cache.db")
    if not os.path.exists(db_path):
        return fin
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # 尝试读取财务数据
        cur.execute("SELECT key FROM cache_data WHERE key LIKE '%financial%' OR key LIKE '%fina%' LIMIT 3")
        keys = cur.fetchall()
        for k in keys:
            cur.execute("SELECT data FROM cache_data WHERE key = ?", (k[0],))
            d = cur.fetchone()
            if d:
                try:
                    obj = json.loads(d[0])
                    if isinstance(obj, dict):
                        for code, vals in obj.items():
                            fin[code] = vals
                except:
                    pass
        conn.close()
    except Exception:
        pass
    return fin


# ============================================================
# 计算个股 120 日行为特征
# ============================================================
def compute_stock_features(code, df_kline, theme_name, theme_data, stock_hot, fin_data, north_hold_ratio=None):
    """计算 120 日市场行为特征

    Args:
        north_hold_ratio: 北向资金持股比例(%)，None表示无数据，使用估算
    """
    feat = {"ts_code": code, "theme_name": theme_name}

    if df_kline is None or len(df_kline) < 120:
        return None

    n = len(df_kline)
    close = df_kline["close"].astype(float).values
    pct = df_kline["pct_chg"].astype(float).values
    amount = df_kline["amount"].astype(float).values / 100000.0  # 千元→亿元
    vol = df_kline["vol"].astype(float).values
    last = n - 1

    # ---- K线基础指标 ----
    feat["price"] = round(close[last], 2)

    # 各周期收益率
    def safe_ret(offset, window):
        if last - offset - window < 0:
            return 0.0
        s = max(0, last - offset - window + 1)
        e = last - offset
        if close[s] == 0:
            return 0.0
        return (close[e] / close[s] - 1) * 100

    feat["ret_5"] = round(safe_ret(0, 5), 1)
    feat["ret_10"] = round(safe_ret(0, 10), 1)
    feat["ret_20"] = round(safe_ret(0, 20), 1)
    feat["ret_60"] = round(safe_ret(0, 60), 1)
    feat["ret_120"] = round(safe_ret(0, 120), 1)
    feat["ret_prev60"] = round(safe_ret(60, 60), 1)  # 前60日收益

    # ---- 均线系统 ----
    def ma(window, offset=0):
        idx = last - offset
        if idx - window + 1 < 0:
            return close[0]
        return close[idx - window + 1: idx + 1].mean()

    ma5 = ma(5)
    ma10 = ma(10)
    ma20 = ma(20)
    ma60 = ma(60)
    ma120 = ma(120) if n >= 120 else ma60

    feat["ma5"] = round(ma5, 2)
    feat["ma20"] = round(ma20, 2)
    feat["ma60"] = round(ma60, 2)
    feat["ma120"] = round(ma120, 2)
    feat["bias_ma20"] = round((close[last] / ma20 - 1) * 100, 1) if ma20 > 0 else 0
    feat["bias_ma60"] = round((close[last] / ma60 - 1) * 100, 1) if ma60 > 0 else 0

    # MA 斜率（先计算，用于均线评分）
    def slope(prices):
        if len(prices) < 5:
            return 0.0
        x = np.arange(len(prices))
        try:
            s = np.polyfit(x, prices, 1)[0]
            return s / np.mean(prices) * 100 if np.mean(prices) > 0 else 0
        except Exception:
            return 0.0

    slope_20 = round(slope(close[max(0, last - 19): last + 1]), 2)
    slope_60 = round(slope(close[max(0, last - 59): last + 1]), 2)
    feat["slope_20"] = slope_20
    feat["slope_60"] = slope_60

    # 均线多头：允许 MA5 < MA10（洗盘），但中长期均线必须多头
    # 条件1: close > MA20（股价站稳中期均线）
    # 条件2: MA10 > MA20 > MA60（中长期多头排列）
    # 条件3: MA20 斜率为正（趋势向上）
    bull_cond = [
        close[last] > ma20,           # 站稳MA20
        ma10 > ma20,                  # 中期多头
        ma20 > ma60,                  # 长期多头
        slope_20 > -0.1,              # MA20斜率不为负
    ]
    # 额外加分：MA5 > MA10（强势）
    if ma5 > ma10:
        bull_cond.append(True)
    feat["bull_score"] = sum(bull_cond) * 20  # 0/20/40/60/80/100

    # ---- 成交额 ----
    avg_amount_20 = amount[max(0, last - 19): last + 1].mean()
    avg_amount_60 = amount[max(0, last - 59): last + 1].mean()
    avg_amount_120 = amount[max(0, last - 119): last + 1].mean()
    feat["avg_amount_20d_yi"] = round(avg_amount_20, 2)
    feat["avg_amount_60d_yi"] = round(avg_amount_60, 2)
    feat["avg_amount_120d_yi"] = round(avg_amount_120, 2)

    # ---- 涨停统计 ----
    feat["limit_up_count_120"] = int(np.sum(pct[max(0, last - 119): last + 1] >= 9.5))
    feat["limit_up_count_60"] = int(np.sum(pct[max(0, last - 59): last + 1] >= 9.5))

    # 最大连板高度
    max_height = 0
    cur_h = 0
    for p in pct[max(0, last - 119): last + 1]:
        if p >= 9.5:
            cur_h += 1
            max_height = max(max_height, cur_h)
        else:
            cur_h = 0
    feat["max_limit_up_height_120"] = max_height

    # ---- 回撤 ----
    if n >= 60:
        window_60 = close[last - 59: last + 1]
        running_max = np.maximum.accumulate(window_60)
        dd = (window_60 / running_max - 1) * 100
        feat["max_drawdown_60d"] = round(dd.min(), 1)
    else:
        feat["max_drawdown_60d"] = -10.0

    # 波动率
    if n >= 60:
        feat["volatility_60d"] = round(np.std(pct[max(0, last - 59): last + 1]), 2)
    else:
        feat["volatility_60d"] = 0.0

    # 上涨日数
    feat["up_days_60"] = int(np.sum(pct[max(0, last - 59): last + 1] > 0))

    # ---- 热榜 ----
    hot_info = stock_hot.get(code, {"dc_days": 0, "total_score": 0})
    feat["dc_hot_days_120"] = int(hot_info["dc_days"])
    feat["hot_total_score"] = round(float(hot_info["total_score"]), 1)

    # ---- 主题数据 ----
    theme_score_latest = 0
    theme_trend = 0
    theme_top10_20d = 0
    theme_rank = 0
    # 尝试精确匹配，不行则模糊匹配
    theme_key = None
    if theme_name and theme_data:
        if theme_name in theme_data:
            theme_key = theme_name
        else:
            # 模糊匹配：去掉空格
            for tk in theme_data:
                if tk.strip() == theme_name.strip():
                    theme_key = tk
                    break
    if theme_key:
        data = theme_data[theme_key]
        if data:
            latest = data[-1]
            theme_score_latest = latest.get("composite_score", 0)
            theme_trend = latest.get("trend_score", 0)
            theme_top10_20d = latest.get("top10_days_20d", 0)
            theme_rank = latest.get("rank", 0)

    # theme_score_latest 只在 if theme_key: 块中设置，若未匹配则为 0
    feat["theme_score"] = round(theme_score_latest, 1)
    feat["theme_trend_score"] = round(theme_trend, 1)
    feat["theme_rank"] = int(theme_rank)

    # ---- 估算 120 日主题 Top3/Top1 天数 ----
    # top10_20d 在上方 if data: 块中已设置，此处可直接使用
    dc_days_120 = hot_info.get("dc_days", 0)
    theme_top3_days_120 = min(120, int(dc_days_120 * 2.5 + feat["limit_up_count_120"] * 3 + theme_top10_20d * 3))
    theme_top1_days_120 = min(60, int(dc_days_120 * 0.8 + theme_top10_20d * 1))

    feat["theme_top3_days_120"] = theme_top3_days_120
    feat["theme_top1_days_120"] = theme_top1_days_120
    feat["ths_hot_days_120"] = int(dc_days_120 * 0.2)  # 估算THS热榜天数

    # ---- 龙虎榜估算（根据涨停和成交放大模式推测）----
    # 涨停次日高开、低开等模式可推测机构参与度
    # 这里用成交量放大 + 涨停模式近似估算
    zt_next_day_up = 0
    for i in range(max(0, last - 119), last):
        if pct[i] >= 9.5 and i + 1 <= last:
            zt_next_day_up += 1 if pct[i + 1] > 0 else 0
    feat["dragon_tiger_approx_120"] = int(feat["limit_up_count_120"] * 0.3 + zt_next_day_up * 0.5)

    # ---- 相对强势天数估算 ----
    # 股价涨幅超过同期指数（用 theme_ret_20 替代）
    theme_ret = theme_data.get(theme_name, [{}])[-1].get("ret_20", 0) if theme_data.get(theme_name) else 0
    relative_days = 0
    for i in range(max(0, last - 119), last, 5):  # 每5天采样
        if close[i] > 0 and theme_ret > 0:
            # 粗略：股价涨幅 vs 主题涨幅
            if feat["ret_120"] > theme_ret * 0.5:
                relative_days += 3
            elif feat["ret_120"] > 0:
                relative_days += 1
    feat["relative_strength_days_120"] = min(120, relative_days)

    # ---- 成交额排名靠前天数估算 ----
    # 假设成交额超过主题均值的日期
    theme_avg_amount = avg_amount_120  # 简化：与自身均值比
    amount_top_days = 0
    for i in range(max(0, last - 119), last + 1):
        if amount[i] > avg_amount_60 * 1.2:
            amount_top_days += 1
    feat["amount_top3_days_120"] = min(120, amount_top_days)

    # ---- 财务数据（从缓存读取，缺失则用 K 线估算）----
    fin = fin_data.get(code, {})
    feat["profit_yoy"] = float(fin.get("profit_yoy", 0)) if fin else 0
    feat["revenue_yoy"] = float(fin.get("revenue_yoy", 0)) if fin else 0
    feat["roe"] = float(fin.get("roe", 0)) if fin else 0
    feat["gross_margin"] = float(fin.get("gross_margin", 0)) if fin else 0
    feat["rd_ratio"] = float(fin.get("rd_ratio", 0)) if fin else 0

    # 若无财务数据，从 K 线模式估算业绩趋势
    # 【重要】不能用涨幅来推断业绩——涨幅≠基本面
    # 应该用毛利率、ROE、营收增速的合理假设
    if feat["profit_yoy"] == 0:
        # 保守假设：行业平均增速（按主题调整）
        theme_name_lower = theme_name.lower()
        if any(k in theme_name_lower for k in ["半导体", "电子", "AI"]):
            feat["profit_yoy"] = 20.0
            feat["revenue_yoy"] = 15.0
        elif any(k in theme_name_lower for k in ["新能源", "汽车", "医疗"]):
            feat["profit_yoy"] = 15.0
            feat["revenue_yoy"] = 12.0
        else:
            feat["profit_yoy"] = 10.0
            feat["revenue_yoy"] = 8.0

    # ---- 机构强度估算 ----
    # 从成交量结构、均线多头、趋势稳定性综合判断
    amount_stability = norm(np.std(pct[max(0, last - 59): last + 1]), 1, 5, reverse=True)
    trend_quality = norm(feat["slope_60"], -0.1, 1.0)
    structure_score = feat["bull_score"] / 100 * 50
    feat["institution_score"] = round(0.4 * amount_stability + 0.3 * trend_quality + 0.3 * structure_score, 1)

    # 北向资金评分：优先使用真实北向持股数据，否则用估算
    if north_hold_ratio is not None and north_hold_ratio > 0:
        # 真实北向持股比例评分（调整后门槛更合理）
        # ≥3%=100分；1-3%=85分；0.5-1%=70分；0.1-0.5%=55分；>0=40分
        if north_hold_ratio >= 3:
            nb_score = 100
        elif north_hold_ratio >= 1:
            nb_score = 85
        elif north_hold_ratio >= 0.5:
            nb_score = 70
        elif north_hold_ratio >= 0.1:
            nb_score = 55
        else:
            nb_score = 40
        feat["northbound_score"] = round(nb_score, 1)
        feat["northbound_hold_ratio"] = round(north_hold_ratio, 3)
        feat["northbound_source"] = "real"  # 标识数据来源
    else:
        # 批量数据为空时，尝试按股票代码单股查询北向持股
        nb_ratio_real = _query_north_hold_single(code)
        if nb_ratio_real > 0:
            if nb_ratio_real >= 3:
                nb_score = 100
            elif nb_ratio_real >= 1:
                nb_score = 85
            elif nb_ratio_real >= 0.5:
                nb_score = 70
            elif nb_ratio_real >= 0.1:
                nb_score = 55
            else:
                nb_score = 40
            feat["northbound_score"] = round(nb_score, 1)
            feat["northbound_hold_ratio"] = round(nb_ratio_real, 3)
            feat["northbound_source"] = "real"
        else:
            # 无真实北向数据时，使用机构强度估算（保留向后兼容）
            feat["northbound_score"] = round(feat["institution_score"] * 0.8, 1)
            feat["northbound_source"] = "estimated"

    # ---- 产业需求和订单爆发估算 ----
    feat["industry_demand_score"] = round(theme_score_latest * 0.7 + theme_trend * 0.3, 1)
    feat["order_explosion_score"] = round(min(100, feat["theme_top3_days_120"] * 1.5 + feat["limit_up_count_120"] * 3), 1)

    return feat


# ============================================================
# 评分系统
# ============================================================
def compute_industry_strength(feat):
    """
    产业主线强度（第一层过滤器）
    公式: industry_strength = 0.4*theme_score + 0.3*industry_demand + 0.3*order_explosion
    """
    theme_score = feat.get("theme_score", 0)  # 主题综合分（0-100）
    industry_demand = feat.get("industry_demand_score", 0)  # 产业需求分（0-100）
    order_explosion = feat.get("order_explosion_score", 0)  # 订单爆发分（0-100）

    # 直接使用主题评分作为核心，附加成交活跃度和趋势
    theme_trend = feat.get("theme_trend_score", 0)

    # industry_demand 用主题评分和趋势加权
    industry_demand_computed = round(0.7 * theme_score + 0.3 * theme_trend, 1)

    # order_explosion 用主题内活跃度（涨停次数 + 热榜天数 + 成交额排名天数）
    theme_top3_days = feat.get("theme_top3_days_120", 0)
    limit_up = feat.get("limit_up_count_120", 0)
    dc_hot = feat.get("dc_hot_days_120", 0)
    order_explosion_computed = round(min(100, theme_top3_days * 0.5 + limit_up * 2 + dc_hot * 0.8), 1)

    industry_strength = round(
        0.4 * theme_score
        + 0.3 * industry_demand_computed
        + 0.3 * order_explosion_computed,
        1,
    )
    return industry_strength


def compute_recognition_score(feat, theme_members_data):
    """高辨识度 Recognition Score"""
    # 1. Theme Rank（主题内排名）
    theme_rank = feat.get("theme_rank", 0)
    theme_leader_rank = feat.get("theme_leader_rank", 99)
    # 如果数据库没有排名，用市值排名替代
    if theme_rank == 0 and theme_leader_rank < 99:
        theme_rank = theme_leader_rank
    theme_rank_score = max(0, 100 - (theme_rank - 1) * 10) if theme_rank > 0 else 30

    # 2. Attention Score
    dc_days = feat.get("dc_hot_days_120", 0)
    ths_days = feat.get("ths_hot_days_120", int(dc_days * 0.2))
    attention_score = min(100, (dc_days + ths_days) * 0.8)

    # 3. Active Score
    top3_days = feat.get("theme_top3_days_120", 0)
    active_score = top3_days / 120 * 100

    # 4. Capacity Score
    avg_amt = feat.get("avg_amount_20d_yi", 0)
    if avg_amt < 5:
        capacity_score = 20
    elif avg_amt < 10:
        capacity_score = 40
    elif avg_amt < 20:
        capacity_score = 60
    elif avg_amt < 50:
        capacity_score = 80
    else:
        capacity_score = 100

    recognition_score = round(
        0.30 * theme_rank_score
        + 0.25 * attention_score
        + 0.20 * active_score
        + 0.25 * capacity_score,
        1,
    )

    return {
        "recognition_score": recognition_score,
        "theme_rank_score": round(theme_rank_score, 1),
        "attention_score": round(attention_score, 1),
        "active_score": round(active_score, 1),
        "capacity_score": capacity_score,
    }


def compute_leader_persistence_score(feat):
    """Leader Persistence Score（龙头持续性）"""
    # 1. Theme Leadership
    top3_days = feat.get("theme_top3_days_120", 0)
    theme_leadership_score = norm(top3_days, 0, 120)

    # 2. Memory Score
    zt_count = feat.get("limit_up_count_120", 0)
    zt_score = min(100, zt_count * 8)

    dragon_tiger = feat.get("dragon_tiger_approx_120", 0)
    dt_score = min(100, dragon_tiger * 15)

    hot_days = feat.get("dc_hot_days_120", 0)
    hot_score = min(100, hot_days * 2)

    memory_score = round(
        0.35 * zt_score
        + 0.35 * dt_score
        + 0.30 * hot_score,
        1,
    )

    # 3. Relative Strength Score
    rel_days = feat.get("relative_strength_days_120", 0)
    relative_strength_score = rel_days / 120 * 100

    # 4. Liquidity Score
    amount_top_days = feat.get("amount_top3_days_120", 0)
    liquidity_score = norm(amount_top_days, 0, 120)

    lps = round(
        0.35 * theme_leadership_score
        + 0.25 * memory_score
        + 0.20 * relative_strength_score
        + 0.20 * liquidity_score,
        1,
    )

    return {
        "leader_persistence_score": lps,
        "theme_leadership_score": round(theme_leadership_score, 1),
        "memory_score": memory_score,
        "relative_strength_score": round(relative_strength_score, 1),
        "liquidity_score": round(liquidity_score, 1),
    }


def compute_earnings_strength(feat):
    """Earnings Strength"""
    profit_yoy = feat.get("profit_yoy", 0)
    revenue_yoy = feat.get("revenue_yoy", 0)

    profit_yoy_score = norm(profit_yoy, 0, 80)
    revenue_yoy_score = norm(revenue_yoy, 0, 60)

    # 业绩质量：从 ROE、毛利率、趋势稳定性综合判断
    roe = feat.get("roe", 0)
    gross_margin = feat.get("gross_margin", 0)
    volatility = feat.get("volatility_60d", 0)
    earnings_quality_score = round(
        0.4 * norm(roe, 5, 25)
        + 0.3 * norm(gross_margin, 10, 50)
        + 0.3 * norm(volatility, 1, 5, reverse=True),
        1,
    )

    earnings_strength = round(
        0.4 * profit_yoy_score
        + 0.3 * revenue_yoy_score
        + 0.3 * earnings_quality_score,
        1,
    )
    return earnings_strength


def compute_institution_strength(feat):
    """Institution Strength"""
    inst_score = feat.get("institution_score", 0)
    nb_score = feat.get("northbound_score", 0)
    return round(0.5 * inst_score + 0.5 * nb_score, 1)


def compute_second_wave_score(rec, lps, industry_strength, institution_strength):
    """Second Wave Score"""
    second_wave_score = round(
        0.30 * rec["recognition_score"]
        + 0.25 * lps["leader_persistence_score"]
        + 0.25 * industry_strength
        + 0.20 * institution_strength,
        1,
    )
    return second_wave_score


def compute_ultimate_score(theme_score, recognition, lps_score, bull_score):
    """Ultimate Score（终极牛股评分）
    = 0.35*bull_score + 0.25*recognition + 0.20*LPS + 0.20*theme_score
    """
    ultimate_score = round(
        0.35 * bull_score
        + 0.25 * recognition
        + 0.20 * lps_score
        + 0.20 * theme_score,
        1,
    )
    return ultimate_score


def judge_rating(ultimate, vm):
    """最终评级（考虑价值余量）"""
    vms = vm["value_margin_score"]
    # S+：历史验证高 + 价值余量也高（还没充分涨）
    if ultimate >= 75 and vms >= 70:
        return "S+"
    # S：历史验证高 + 价值余量中等
    elif ultimate >= 70 and vms >= 55:
        return "S"
    # A：历史验证高 + 价值余量偏低
    elif ultimate >= 65 and vms >= 40:
        return "A"
    # B：历史验证中等
    elif ultimate >= 60:
        return "B"
    else:
        return "C"


def judge_stage(feat, rec, lps, industry_strength):
    """判断所处阶段"""
    bull = feat.get("bull_score", 0)
    ret_60 = feat.get("ret_60", 0)
    ret_120 = feat.get("ret_120", 0)
    bias_60 = feat.get("bias_ma60", 0)
    lps_s = lps["leader_persistence_score"]
    rec_s = rec["recognition_score"]

    if bull >= 75 and lps_s >= 80 and rec_s >= 80:
        return "阶段4: 二波/三波主升浪"
    elif bull >= 50 and industry_strength >= 80:
        return "阶段3: 机构抱团确认"
    elif bull >= 50 and rec_s >= 75:
        return "阶段2: 龙头确立"
    elif ret_120 < 10 and bull >= 25:
        return "阶段1: 启动/复苏"
    elif ret_120 > 100 and bias_60 > 40:
        return "阶段5: 估值扩张/高位"
    elif ret_60 < -15:
        return "阶段0: 调整/洗盘"
    else:
        return "过渡阶段"


def compute_value_margin(feat):
    """
    Value Margin Score: 衡量还有多少上涨空间（0~100）
    高分 = 价值尚未透支，仍有较大上涨空间
    低分 = 已经涨太多，透支未来

    核心原则：不以历史涨幅论英雄，而以估值/结构健康度论空间
    """
    # 1. 乖离率控制（最重要的指标）
    bias_ma20 = abs(feat.get("bias_ma20", 0))
    bias_ma60 = abs(feat.get("bias_ma60", 0))

    # MA20乖离：0~10% = 健康；10~20% = 偏贵；20~30% = 透支；>30% = 严重透支
    if bias_ma20 <= 10:
        bias_score = 100
    elif bias_ma20 <= 20:
        bias_score = 80 - (bias_ma20 - 10) * 4
    elif bias_ma20 <= 30:
        bias_score = 60 - (bias_ma20 - 20) * 5
    else:
        bias_score = max(0, 40 - (bias_ma20 - 30) * 2)

    # 2. 回撤控制（近期有没有洗盘）
    dd = feat.get("max_drawdown_60d", 0)
    # 有适度回撤（-5~-20%）= 健康洗盘；无回撤=没有充分换手；回撤过大=趋势不稳
    if dd >= -5:  # 几乎没有回撤
        dd_score = 50  # 换手不充分
    elif dd >= -20:
        dd_score = 100  # 最佳：适度洗盘
    else:
        dd_score = max(0, 80 + (dd + 20) * 2)  # 回撤过大

    # 3. 近期启动（vs 120日全程上涨）
    ret_5 = feat.get("ret_5", 0)
    ret_20 = feat.get("ret_20", 0)
    ret_60 = feat.get("ret_60", 0)
    ret_120 = feat.get("ret_120", 0)

    # 如果120日涨幅过大，即使近期在调整也说明已经透支
    if ret_120 > 150:
        overall_score = 30  # 已经涨太多
    elif ret_120 > 80:
        overall_score = 50
    elif ret_120 > 40:
        overall_score = 70
    else:
        overall_score = 90  # 120日涨幅不大

    # 近期是否刚启动（20日涨幅 vs 120日涨幅的比例）
    if ret_120 > 0:
        recent_ratio = ret_20 / ret_120  # 越接近1 = 主要是最近涨的 = 刚启动
        if recent_ratio >= 0.5:
            start_score = 100  # 刚启动或刚恢复
        elif recent_ratio >= 0.3:
            start_score = 80
        else:
            start_score = 60  # 主要是之前涨的
    else:
        start_score = 100  # 还没涨

    # 4. 涨停密度（120日涨停/120日涨幅 = 每1%涨幅用了几次涨停）
    # 涨停太多 = 情绪炒作 = 透支
    zt_120 = feat.get("limit_up_count_120", 0)
    if ret_120 > 0:
        zt_density = zt_120 / (ret_120 / 10)  # 每10%涨幅对应涨停次数
    else:
        zt_density = 0

    if zt_density <= 0.3:
        quality_score = 100  # 稳健上涨
    elif zt_density <= 0.8:
        quality_score = 80
    elif zt_density <= 1.5:
        quality_score = 60
    else:
        quality_score = max(0, 40 - (zt_density - 1.5) * 10)

    # 综合价值余量
    value_margin = round(
        0.30 * max(0, min(100, bias_score))
        + 0.20 * dd_score
        + 0.25 * overall_score
        + 0.15 * start_score
        + 0.10 * quality_score,
        1,
    )

    return {
        "value_margin_score": value_margin,
        "bias_score": round(max(0, min(100, bias_score)), 1),
        "dd_score": round(dd_score, 1),
        "overall_score": round(overall_score, 1),
        "start_score": round(start_score, 1),
        "quality_score": round(quality_score, 1),
    }


def apply_upgrade_filter(stocks):
    """升级过滤器（候选 > 30 时启动，满足 ≥ 2 项）"""
    if len(stocks) <= 30:
        return stocks

    filtered = []
    for s in stocks:
        checks = 0
        if s["theme_top3_days_120"] >= 20:
            checks += 1
        if s["dc_hot_days_120"] >= 3:
            checks += 1
        if s.get("dragon_tiger_approx_120", 0) >= 2:
            checks += 1
        if s.get("institution_score", 0) >= 50:
            checks += 1
        if s["industry_strength"] >= 60:
            checks += 1

        if checks >= 2:
            filtered.append(s)

    print(f"[Filter] 升级过滤: {len(stocks)} → {len(filtered)} 只（需满足≥2项升级条件）")
    return filtered


def build_core_reason(feat, rec, lps, industry_strength, institution_strength, second_wave, ultimate, vm):
    """生成核心逻辑"""
    reasons = []
    theme = feat.get("theme_name", "")
    if theme:
        reasons.append(f"主题 {theme}（综合分 {feat.get('theme_score', 0):.0f}，产业强度 {industry_strength:.0f}）")
    reasons.append(f"辨识度 {rec['recognition_score']:.0f}（排名{rec['theme_rank_score']:.0f}/曝光{rec['attention_score']:.0f}/活跃{rec['active_score']:.0f}/容量{rec['capacity_score']:.0f}）")
    reasons.append(f"龙头持续性 {lps['leader_persistence_score']:.0f}（领导力{lps['theme_leadership_score']:.0f}/记忆{lps['memory_score']:.0f}/相对强势{lps['relative_strength_score']:.0f}）")
    reasons.append(f"价值余量 {vm['value_margin_score']:.0f}（乖离{vm['bias_score']:.0f}/回撤{vm['dd_score']:.0f}/整体涨幅{vm['overall_score']:.0f}/启动时间{vm['start_score']:.0f}）")
    reasons.append(f"终极评分 {ultimate:.0f}（历史验证60%+价值余量40%，价值余量防止选出一堆已经涨到天上的票）")
    reasons.append(f"二波潜力 {second_wave:.0f}（产业强化{industry_strength:.0f}/机构强化{institution_strength:.0f}）")
    reasons.append(f"市值 {feat.get('mcap_yi', 0):.0f}亿 · 20日均成交 {feat.get('avg_amount_20d_yi', 0):.1f}亿")
    reasons.append(f"60日+{feat.get('ret_60', 0):+.1f}% · 120日+{feat.get('ret_120', 0):+.1f}% · 涨停{feat.get('limit_up_count_120', 0)}次 · 热榜{feat.get('dc_hot_days_120', 0)}天")
    return reasons


def build_risk_factor(feat):
    """生成风险提示"""
    risks = []
    bias = feat.get("bias_ma20", 0)
    if bias > 20:
        risks.append(f"短期乖离过大（MA20+{bias:.1f}%），有回调风险")
    dd = feat.get("max_drawdown_60d", 0)
    if dd < -25:
        risks.append(f"60日最大回撤{dd:.1f}%，波动率偏高")
    zt = feat.get("limit_up_count_60", 0)
    if zt > 12:
        risks.append(f"60日涨停{zt}次，游资炒作痕迹明显")
    vol = feat.get("volatility_60d", 0)
    if vol > 4.0:
        risks.append(f"波动率{vol:.1f}%，日内波动较大")
    profit_yoy = feat.get("profit_yoy", 0)
    if profit_yoy < 20:
        risks.append(f"净利润增速{profit_yoy:.0f}%偏低，业绩支撑不足")
    if not risks:
        risks.append("暂无显著风险因子")
    return risks


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 70)
    print("主板高辨识度龙头 · 第二波主升浪识别系统 v2")
    print("=" * 70)

    # 1. 加载数据
    portfolio = load_theme_portfolio()
    theme_scores = load_theme_scores(days=180)
    stock_hot = load_stock_hot_days(days=120)
    fin_data = load_financial_data()

    if not portfolio:
        print("[Error] 无主题成分股数据")
        return

    # 预加载北向资金持股数据（一次性批量加载，避免每只股票调用API）
    today_str = datetime.now().strftime('%Y%m%d')
    print(f"\n[Load] 预加载北向资金持股数据 ({today_str})...")
    north_hold_data = load_north_hold_batch(today_str)
    if north_hold_data:
        print(f"  [OK] 北向数据加载成功: {len(north_hold_data)} 只股票有持股")
    else:
        print(f"  [Warn] 北向数据加载失败，将使用估算模式")

    # 2. 计算每只股票特征
    print("\n[Calc] 计算股票特征...")
    all_stocks = []

    # 过滤统计
    filter_stats = {"industry": 0, "bull": 0, "rec": 0, "lps": 0, "profit": 0}

    # 计算每个主题的龙头
    theme_members = defaultdict(list)
    for code, info in portfolio.items():
        theme_members[info["theme_name"]].append((code, info))

    theme_amounts = defaultdict(list)
    for code, info in portfolio.items():
        theme_amounts[info["theme_name"]].append(info["mcap_yi"])

    theme_leader_rank = {}
    for theme, amounts in theme_amounts.items():
        sorted_codes = sorted(
            [(c, info["mcap_yi"]) for c, info in portfolio.items() if info["theme_name"] == theme],
            key=lambda x: x[1],
            reverse=True,
        )
        for rank, (code, _) in enumerate(sorted_codes):
            theme_leader_rank[code] = rank + 1

    for idx, (code, info) in enumerate(portfolio.items()):
        if idx % 200 == 0:
            print(f"  进度 {idx}/{len(portfolio)} ...")
        if not is_mainboard(code):
            continue

        kline = load_kline(code, lookback=180)
        if kline is None or len(kline) < 120:
            continue

        feat = compute_stock_features(code, kline, info["theme_name"], theme_scores, stock_hot, fin_data, north_hold_data.get(code))
        if feat is None:
            continue

        feat["name"] = info["name"]
        feat["mcap_yi"] = info["mcap_yi"]
        feat["theme_leader_rank"] = theme_leader_rank.get(code, 99)

        # 市值过滤
        if feat["mcap_yi"] < 50 or feat["mcap_yi"] > 5000:
            continue
        # 成交额过滤
        if feat.get("avg_amount_20d_yi", 0) < 5:
            continue

        # ---- 第一层：产业主线过滤器 ----
        industry_strength = compute_industry_strength(feat)
        if industry_strength < 60:
            filter_stats["industry"] += 1
            continue

        # ---- 第二层：Recognition Score ----
        rec = compute_recognition_score(feat, theme_members)

        # ---- 第三层：Leader Persistence Score ----
        lps = compute_leader_persistence_score(feat)

        # ---- 第四层：机构强度 & 业绩强度 ----
        earnings_strength = compute_earnings_strength(feat)
        institution_strength = compute_institution_strength(feat)

        # ---- 二波潜力 ----
        second_wave = compute_second_wave_score(rec, lps, industry_strength, institution_strength)

        # ---- 价值余量评分 ----
        # 【关键】加入价值余量，防止选出一堆已经涨到天上的票
        vm = compute_value_margin(feat)

        # ---- 终极评分 ----
        # 平衡"已经被验证"和"还有空间"两个维度
        theme_score = feat.get("theme_score", 0)
        recognition = rec["recognition_score"]
        lps_score = lps["leader_persistence_score"]
        bull = feat.get("bull_score", 0)

        # 纯"历史验证"分数
        validated_score = (
            0.30 * recognition
            + 0.25 * lps_score
            + 0.20 * theme_score
            + 0.25 * bull
        )

        # 综合：60%历史验证 + 40%价值余量
        ultimate = round(0.60 * validated_score + 0.40 * vm["value_margin_score"], 1)

        # ---- 强制过滤器（v3：降低识别门槛 + 硬性过滤价值透支）----
        # 核心改变：价值余量必须 >= 50 是硬门槛；识别分/LPS/产业强度等降低要求
        filter_reason = None
        # 1) 价值余量必须 >= 50（硬性过滤：已经透支的直接排除）
        if vm["value_margin_score"] < 50:
            filter_reason = f"value_margin={vm['value_margin_score']:.1f}<50 (价值透支)"
            filter_stats["vm"] = filter_stats.get("vm", 0) + 1
        # 2) 均线多头 >= 60（不需要非常强，只要健康）
        elif feat["bull_score"] < 60:
            filter_reason = f"bull={feat['bull_score']:.0f}<60"
            filter_stats["bull"] += 1
        # 3) 识别分 >= 50（有一些市场记忆即可，不要求极高识别度）
        elif rec["recognition_score"] < 50:
            filter_reason = f"rec={rec['recognition_score']:.1f}<50"
            filter_stats["rec"] += 1
        # 4) 产业强度 >= 55（必须有产业逻辑支撑）
        elif industry_strength < 40:
            filter_reason = f"ind={industry_strength:.1f}<40"
            filter_stats["industry"] += 1
        # 5) 净利润增速 >= 10（最低正增长要求）
        elif feat["profit_yoy"] < 10:
            filter_reason = f"profit_yoy={feat['profit_yoy']:.0f}<10"
            filter_stats["profit"] += 1

        if filter_reason:
            continue

        # ---- 评级 ----
        rating = judge_rating(ultimate, vm)
        stage = judge_stage(feat, rec, lps, industry_strength)

        result = {
            "ts_code": code,
            "name": feat["name"],
            "theme": feat["theme_name"],
            "industry": info.get("layer", ""),
            "market_cap_yi": round(feat["mcap_yi"], 1),
            "avg_amount_20d_yi": round(feat.get("avg_amount_20d_yi", 0), 2),
            # 各项评分
            "industry_strength": industry_strength,
            "recognition_score": rec["recognition_score"],
            "theme_rank_score": rec["theme_rank_score"],
            "attention_score": rec["attention_score"],
            "active_score": rec["active_score"],
            "capacity_score": rec["capacity_score"],
            "leader_persistence_score": lps["leader_persistence_score"],
            "theme_leadership_score": lps["theme_leadership_score"],
            "memory_score": lps["memory_score"],
            "relative_strength_score": lps["relative_strength_score"],
            "earnings_strength": earnings_strength,
            "institution_strength": institution_strength,
            "second_wave_score": second_wave,
            "ultimate_score": ultimate,
            "value_margin_score": vm["value_margin_score"],
            "value_bias_score": vm["bias_score"],
            "value_dd_score": vm["dd_score"],
            "value_overall_score": vm["overall_score"],
            "value_start_score": vm["start_score"],
            "value_quality_score": vm["quality_score"],
            "theme_score": feat.get("theme_score", 0),
            # K线指标
            "bull_score": feat.get("bull_score", 0),
            "ret_5": feat.get("ret_5", 0),
            "ret_20": feat.get("ret_20", 0),
            "ret_60": feat.get("ret_60", 0),
            "ret_120": feat.get("ret_120", 0),
            "bias_ma20": feat.get("bias_ma20", 0),
            "bias_ma60": feat.get("bias_ma60", 0),
            "slope_60": feat.get("slope_60", 0),
            "volatility_60d": feat.get("volatility_60d", 0),
            "max_drawdown_60d": feat.get("max_drawdown_60d", 0),
            # 行为数据
            "limit_up_count_120": feat.get("limit_up_count_120", 0),
            "dc_hot_days_120": feat.get("dc_hot_days_120", 0),
            "dragon_tiger_approx_120": feat.get("dragon_tiger_approx_120", 0),
            "theme_top3_days_120": feat.get("theme_top3_days_120", 0),
            "relative_strength_days_120": feat.get("relative_strength_days_120", 0),
            "amount_top3_days_120": feat.get("amount_top3_days_120", 0),
            # 财务
            "profit_yoy": feat.get("profit_yoy", 0),
            "revenue_yoy": feat.get("revenue_yoy", 0),
            "institution_score": feat.get("institution_score", 0),
            # 最终判断
            "stage": stage,
            "rating": rating,
            "core_reason": build_core_reason(feat, rec, lps, industry_strength, institution_strength, second_wave, ultimate, vm),
            "risk_factor": build_risk_factor(feat),
        }
        all_stocks.append(result)

    print(f"\n[Filter] 强制过滤后剩余: {len(all_stocks)} 只")
    print(f"       过滤原因: 产业强度<60: {filter_stats['industry']} | 均线<75: {filter_stats['bull']} | 辨识度<65: {filter_stats['rec']} | LPS<65: {filter_stats['lps']} | 利润<20%: {filter_stats['profit']}")

    # ---- 升级过滤器 ----
    all_stocks = apply_upgrade_filter(all_stocks)

    # 3. 排序
    all_stocks.sort(
        key=lambda x: (x["ultimate_score"], x["leader_persistence_score"], x["recognition_score"]),
        reverse=True,
    )

    # 只输出 Top 20
    top20 = all_stocks[:20]

    # 4. 控制台输出
    print("\n" + "=" * 140)
    print(
        f"{'排名':<4}{'代码':<12}{'名称':<10}{'主题':<14}{'评级':<4}"
        f"{'终极分':<8}{'LPS':<7}{'辨识度':<8}{'产业强':<8}{'二波分':<8}{'价值余量':<9}{'阶段'}"
    )
    print("-" * 140)
    for i, r in enumerate(top20, 1):
        print(
            f"{i:<4}{r['ts_code']:<12}{r['name']:<10}{r['theme']:<14}{r['rating']:<4}"
            f"{r['ultimate_score']:<8.1f}{r['leader_persistence_score']:<7.1f}"
            f"{r['recognition_score']:<8.1f}{r['industry_strength']:<8.1f}"
            f"{r['second_wave_score']:<8.1f}{r['value_margin_score']:<9.1f}{r['stage']}"
        )
    print("=" * 140)

    # 5. 详细输出 Top 20
    print("\n" + "=" * 130)
    print("【Top 20 详细信息】")
    print("=" * 130)
    for i, r in enumerate(top20, 1):
        print(f"\n{'=' * 100}")
        print(
            f"No.{i} [{r['rating']}] {r['name']}({r['ts_code']}) · {r['theme']} | "
            f"市值 {r['market_cap_yi']:.0f}亿 · 20日均 {r['avg_amount_20d_yi']:.1f}亿"
        )
        print(f"  阶段: {r['stage']}")
        validated = (
            0.30 * r['recognition_score']
            + 0.25 * r['leader_persistence_score']
            + 0.20 * r['theme_score']
            + 0.25 * r['bull_score']
        )
        print(
            f"  终极评分 {r['ultimate_score']:.1f} = 历史验证{validated:.1f}×0.60 + 价值余量{r['value_margin_score']:.0f}×0.40"
        )
        print(
            f"  价值余量 {r['value_margin_score']:.1f} = "
            f"乖离控制{r['value_bias_score']:.0f}×0.30 / 回撤健康{r['value_dd_score']:.0f}×0.20 / "
            f"整体涨幅{r['value_overall_score']:.0f}×0.25 / 启动时间{r['value_start_score']:.0f}×0.15 / "
            f"涨停质量{r['value_quality_score']:.0f}×0.10"
        )
        print(
            f"  辨识度 {r['recognition_score']:.1f} = "
            f"排名{r['theme_rank_score']:.0f}×0.30 / 曝光{r['attention_score']:.0f}×0.25 / "
            f"活跃{r['active_score']:.0f}×0.20 / 容量{r['capacity_score']:.0f}×0.25"
        )
        print(
            f"  LPS {r['leader_persistence_score']:.1f} = "
            f"领导力{r['theme_leadership_score']:.0f}×0.35 / 记忆{r['memory_score']:.0f}×0.25 / "
            f"相对强势{r['relative_strength_score']:.0f}×0.20 / 流动性{r['relative_strength_score']:.0f}×0.20"
        )
        print(
            f"  产业强度 {r['industry_strength']:.1f} | "
            f"机构强化 {r['institution_strength']:.1f} | "
            f"业绩强度 {r['earnings_strength']:.1f}"
        )
        print(
            f"  二波分 {r['second_wave_score']:.1f} = "
            f"辨识度{r['recognition_score']:.0f}×0.30 + LPS{r['leader_persistence_score']:.0f}×0.25 + "
            f"产业{r['industry_strength']:.0f}×0.25 + 机构{r['institution_strength']:.0f}×0.20"
        )
        print(
            f"  K线: 5日{r['ret_5']:+.1f}% / 20日{r['ret_20']:+.1f}% / 60日{r['ret_60']:+.1f}% / 120日{r['ret_120']:+.1f}%"
        )
        print(
            f"  MA20偏{r['bias_ma20']:+.1f}% | MA60偏{r['bias_ma60']:+.1f}% | "
            f"均线多头{r['bull_score']:.0f}/100 | 斜率{r['slope_60']:+.2f}%"
        )
        print(
            f"  涨停{r['limit_up_count_120']}次 | 热榜{r['dc_hot_days_120']}天 | "
            f"主题Top3天数{r['theme_top3_days_120']} | 机构得分{r['institution_score']:.0f}"
        )
        print(
            f"  财务: 净利润增速{r['profit_yoy']:.0f}% | 营收增速{r['revenue_yoy']:.0f}%"
        )
        for reason in r["core_reason"]:
            print(f"  ✔ {reason}")
        for risk in r["risk_factor"]:
            print(f"  ⚠ {risk}")

    # 6. 按评级统计
    print("\n\n" + "=" * 100)
    print("【按评级统计】")
    print("=" * 100)
    for rating in ["S+", "S", "A", "B"]:
        count = sum(1 for r in top20 if r["rating"] == rating)
        rated = [r for r in top20 if r["rating"] == rating]
        print(f"  {rating}级: {count} 只")
        for r in rated[:5]:
            print(f"     - {r['name']}({r['ts_code']}) | {r['theme']} | 终极分{r['ultimate_score']:.0f} | LPS {r['leader_persistence_score']:.0f}")

    # 7. 持久化
    output_dir = os.path.join(BASE_DIR, "report_daily")
    os.makedirs(output_dir, exist_ok=True)

    try:
        def _to_py(v):
            if isinstance(v, (np.integer, np.int64)):
                return int(v)
            if isinstance(v, (np.floating, np.float64)):
                return float(v)
            if isinstance(v, np.ndarray):
                return v.tolist()
            if isinstance(v, dict):
                return {k: _to_py(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_to_py(x) for x in v]
            return v

        output_data = []
        for r in top20:
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
        json_path = os.path.join(output_dir, "mainboard_v2_scan.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output_json, f, ensure_ascii=False, indent=2)
        print(f"\n[Save] JSON: {json_path}")

        # CSV
        import time as _time, shutil
        ts = datetime.now().strftime("%H%M%S")
        tmp_csv = os.path.join(output_dir, f"_tmp_v2_{ts}.csv")
        df_out = pd.DataFrame(output_data)
        df_out.to_csv(tmp_csv, index=False, encoding="utf-8-sig")
        csv_path = os.path.join(output_dir, "mainboard_v2_scan.csv")
        try:
            os.remove(csv_path)
        except OSError:
            pass
        os.replace(tmp_csv, csv_path)
        print(f"[Save] CSV: {csv_path}")

    except Exception as e:
        import traceback
        print(f"[Warn] 保存失败: {e}")
        traceback.print_exc()

    print("\n[Done] 扫描完成")


if __name__ == "__main__":
    main()
