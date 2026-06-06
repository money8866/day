#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题趋势 + 情绪 评分（自建"行业最强"算法）
- 复用 theme_portfolio_strategy_cached_dc.py 的成份股匹配逻辑
  （_in_industry_list / _strip_ii / dc_index / dc_member / stock_basic）
- 拉取成份股近 60 个交易日的日线，计算多维度指标
- 输出 trend_score / sentiment_score / composite_score 排名

评分思路（自建）：

【趋势分 TrendScore 0-100】
  1) 多周期收益（5/10/20日 加权）
  2) 均线多头排列占比（站上 MA5/MA10/MA20）
  3) 趋势斜率（10日线性回归斜率）
  4) 趋势加速度（5日 - 10日）
  5) 龙头强度（板块内 Top3 个股平均涨幅）
  6) 抗跌性（10日最大回撤倒数）

【情绪分 SentimentScore 0-100】
  1) 上涨家数占比（breadth）
  2) 涨停占比（>=9.5%）
  3) 强势股占比（>=5%）
  4) 量能放大（5日均量 / 20日均量）
  5) 换手率活跃度
  6) 赚钱效应（中位数涨幅 + 0.5*均值涨幅）
  7) 相对市场强度（板块均值 - 沪深300 均值）
  8) 主线共振（领涨股 + 涨停股同时存在）

【综合分 Composite = 0.55 * Trend + 0.45 * Sentiment】
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

# SQLite 缓存数据库配置
DB_PATH = os.path.join(CACHE_DIR, 'cache.db')

def init_db():
    """初始化SQLite数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache_data (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            expire_time INTEGER,
            created_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# 初始化数据库
init_db()

REPORT_DIR = os.path.join(os.path.dirname(BASE_DIR), "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

# 终极方案：patch os.path.expanduser，不让 tushare 访问用户根目录
# 用 sentinel 标记确保 reload 时 original_expanduser 仍指向真实的 expanduser
if not hasattr(os, '_original_expanduser'):
    os._original_expanduser = os.path.expanduser
original_expanduser = os._original_expanduser

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(CACHE_DIR, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

import tushare as ts

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None

OUTPUT_CSV = os.path.join(CACHE_DIR, "theme_trend_sentiment.csv")
OUTPUT_DB = os.path.join(CACHE_DIR, "theme_trend_sentiment.db")

N_DAYS = 60
TOP_N_PER_THEME = 30
MIN_STOCKS = 3

TOP_TREND_N = 5
RSI_BUY_THRESHOLD = 70
TURNOVER_SELL_PERCENTILE = 90

CLIMAX_TREND_THRESHOLD = 65
CLIMAX_SENTIMENT_THRESHOLD = 75
CLIMAX_SENTIMENT_RANK1 = True

DIP_TREND_THRESHOLD = 55  # 提高趋势分阈值
DIP_SENTIMENT_CEILING = 50


def _strip_ii(name):
    if not isinstance(name, str) or not name:
        return ""
    for suf in ("Ⅱ",):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _in_industry_list(name, industry_list):
    if not isinstance(name, str) or not name:
        return False
    stripped = _strip_ii(name)
    for ind in industry_list:
        if isinstance(ind, str) and _strip_ii(ind) == stripped:
            return True
    return False


def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


TRADE_DATE = get_last_trade_date()
START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
print(f"[Init] 交易日: {TRADE_DATE}  K线区间: {START_DATE} ~ {TRADE_DATE}")


def cache_get(name, **kwargs):
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{TRADE_DATE}"
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data, expire_time FROM cache_data WHERE key = ?', (cache_key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row
            # 检查是否过期（0 或 None 表示永不过期）
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                # 已过期，删除
                cursor.execute('DELETE FROM cache_data WHERE key = ?', (cache_key,))
                conn.commit()
                return None
            # 返回缓存数据
            from io import StringIO
            return pd.read_csv(StringIO(data_str))
    except Exception as e:
        print(f"[Cache] get error: {e}")
    finally:
        conn.close()
    return None


def cache_set(name, data, expire_hours=None, **kwargs):
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{TRADE_DATE}"
    
    # 默认永不过期（expire_hours 为 None 或 <=0 时）
    if expire_hours and expire_hours > 0:
        expire_time = int(time.time()) + expire_hours * 3600
    else:
        expire_time = 0  # 0 表示永不过期
    created_at = int(time.time())
    
    # 将DataFrame转为字符串
    from io import StringIO
    buffer = StringIO()
    data.to_csv(buffer, index=False)
    data_str = buffer.getvalue()
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        ''', (cache_key, data_str, expire_time, created_at))
        conn.commit()
    except Exception as e:
        print(f"[Cache] set error: {e}")
    finally:
        conn.close()


def load_theme_json():
    path = os.path.join(BASE_DIR, "theme.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到 {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("HOT_THEMES", {})


def get_dc_members():
    cached = cache_get("dc_all_members")
    if cached is not None:
        print(f"[DC] 缓存命中: {len(cached)} 条成份股记录")
        return cached

    if pro is None:
        print("[DC] 缺少 Tushare token，无法拉取东财板块")
        return pd.DataFrame()

    print("[DC] 调用 Tushare dc_index / dc_member 拉取板块成份股...")
    concept_df = pro.dc_index(trade_date=TRADE_DATE, idx_type="概念板块")
    time.sleep(0.2)
    industry_df = pro.dc_index(trade_date=TRADE_DATE, idx_type="行业板块")
    time.sleep(0.2)
    boards = pd.concat([concept_df[["ts_code", "name"]], industry_df[["ts_code", "name"]]], ignore_index=True)
    name_map = dict(zip(boards["ts_code"], boards["name"]))
    codes = boards["ts_code"].tolist()

    all_members = []
    total = len(codes)
    for i, code in enumerate(codes):
        try:
            m = pro.dc_member(trade_date=TRADE_DATE, ts_code=code)
            if m is not None and not m.empty:
                m["concept_name"] = m["ts_code"].map(name_map)
                m = m.dropna(subset=["concept_name"])
                all_members.append(m)
            if (i + 1) % 100 == 0:
                print(f"[DC] 进度: {i+1}/{total}")
            time.sleep(0.08)
        except Exception as e:
            pass

    if not all_members:
        return pd.DataFrame()
    df = pd.concat(all_members, ignore_index=True).drop_duplicates(subset=["con_code", "concept_name"])
    cache_set("dc_all_members", df)
    print(f"[DC] 拉取完成: {len(df)} 条")
    return df


def get_stock_basic():
    cached = cache_get("stock_basic")
    if cached is not None:
        return cached
    if pro is None:
        return pd.DataFrame()
    df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    time.sleep(0.1)
    cache_set("stock_basic", df)
    return df


def get_daily_basic(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    cached = cache_get("daily_basic", trade_date=trade_date)
    if cached is not None:
        return cached
    if pro is None:
        return pd.DataFrame()
    df = pro.daily_basic(trade_date=trade_date, fields="ts_code,total_mv,circ_mv,turnover_rate,pe,pb")
    time.sleep(0.1)
    cache_set("daily_basic", df, trade_date=trade_date)
    return df


def _add_ma_columns(df):
    """为K线数据添加MA5/MA10/MA20均线列（原地修改并返回）"""
    if df is None or df.empty:
        return df
    df = df.sort_values('trade_date').copy()
    if 'ma5' not in df.columns:
        df['ma5'] = df['close'].rolling(5).mean().bfill()
    if 'ma10' not in df.columns:
        df['ma10'] = df['close'].rolling(10).mean().bfill()
    if 'ma20' not in df.columns:
        df['ma20'] = df['close'].rolling(20).mean().bfill()
    if 'ma60' not in df.columns and len(df) >= 60:
        df['ma60'] = df['close'].rolling(60).mean().bfill()
    return df


def get_daily_kline(ts_codes, start, end):
    if pro is None or not ts_codes:
        return pd.DataFrame()
    
    all_parts = []
    need_fetch_codes = []
    
    # 先尝试从单只股票缓存里读取
    for code in ts_codes:
        cache_key = f"daily_kline_{code}_{start}_{end}"
        cached = cache_get(cache_key)
        if cached is not None:
            # 检查缓存数据是否有均线列，没有则补充并更新缓存
            if 'ma5' not in cached.columns:
                cached = _add_ma_columns(cached)
                cache_set(cache_key, cached)  # 更新缓存（写入带均线的版本）
            all_parts.append(cached)
        else:
            need_fetch_codes.append(code)
    
    # 需要拉取的股票按批次拉取
    if need_fetch_codes:
        chunks = [need_fetch_codes[i : i + 80] for i in range(0, len(need_fetch_codes), 80)]
        for ci, chunk in enumerate(chunks):
            try:
                df = pro.daily(ts_code=",".join(chunk), start_date=start, end_date=end)
                if df is not None and not df.empty:
                    # 按股票分开缓存
                    for code in chunk:
                        code_df = df[df['ts_code'] == code].copy()
                        if not code_df.empty:
                            # 先计算均线，再缓存（一次缓存永久可用）
                            code_df = _add_ma_columns(code_df)
                            cache_key = f"daily_kline_{code}_{start}_{end}"
                            cache_set(cache_key, code_df)
                            all_parts.append(code_df)
                time.sleep(0.2)
            except Exception as e:
                print(f"[KLine] 批次 {ci + 1}/{len(chunks)} 失败: {e}")
                time.sleep(0.5)
    
    df = pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()
    return df


def get_index_kline(ts_code="000300.SH", start=None, end=None):
    if start is None:
        start = START_DATE
    if end is None:
        end = TRADE_DATE
    cached = cache_get("idx_kline", ts_code=ts_code, start=start, end=end)
    
    # 检查缓存数据是否包含最新日期（避免缓存昨天的数据）
    if cached is not None:
        if 'trade_date' in cached.columns:
            max_date = cached['trade_date'].max()
            if max_date == end:
                print(f"[Index] 缓存命中且包含最新数据: {ts_code}")
                return cached
            else:
                print(f"[Index] 缓存数据过期（最新日期: {max_date}, 需要: {end}），重新拉取")
        else:
            print(f"[Index] 缓存数据格式异常，重新拉取")
    
    if pro is None:
        return pd.DataFrame()
    try:
        print(f"[Index] 拉取 {ts_code} 数据: {start} ~ {end}")
        df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    time.sleep(0.1)
    if df is not None and not df.empty:
        cache_set("idx_kline", df, ts_code=ts_code, start=start, end=end)
        print(f"[Index] 数据已缓存")
    return df


def match_theme_stocks(hot_themes, dc_df, stock_basic_df):
    stock_basic_industry = {}
    name_map_basic = {}
    for _, row in stock_basic_df.iterrows():
        stock_basic_industry[row["ts_code"]] = row.get("industry", "")
        name_map_basic[row["ts_code"]] = row.get("name", "")

    # 拆分东财数据为行业和概念
    stock_concepts = defaultdict(list)
    stock_dc_industries = defaultdict(list)
    if dc_df is not None and not dc_df.empty:
        for _, r in dc_df.iterrows():
            con_code = r["con_code"]
            concept_name = r["concept_name"]
            if con_code and concept_name:
                # 判断是行业板块还是概念板块（通过名称特征）
                is_industry = ('行业' in concept_name or 'Ⅱ' in concept_name or 'Ⅲ' in concept_name or 'Ⅰ' in concept_name)
                if is_industry:
                    stock_dc_industries[con_code].append(concept_name)
                else:
                    stock_concepts[con_code].append(concept_name)

    theme_stock_map = {}
    for theme_name, cfg in hot_themes.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        exclude_keywords = cfg.get("exclude_keywords", [])

        matched = {}
        
        # 方式1：东财行业板块匹配（优先）
        for code, dc_inds in stock_dc_industries.items():
            hit = False
            for ind in dc_inds:
                if ind in industry_list or any(v in ind for v in industry_list):
                    hit = True
                    break
            if hit:
                matched[code] = {"via": "dc_industry", "industry_match": True}
        
        # 方式2：东财概念板块匹配
        for code, concepts in stock_concepts.items():
            hit = False
            for c in concept_list:
                if c in concepts:
                    hit = True
                    break
            if hit:
                if code in matched:
                    matched[code]["via"] = matched[code]["via"] + "+dc_concept"
                else:
                    matched[code] = {"via": "dc_concept", "industry_match": False}
        
        # 方式3：stock_basic行业兜底匹配
        for code, ind in stock_basic_industry.items():
            if ind and _in_industry_list(ind, industry_list):
                if code not in matched:
                    matched[code] = {"via": "stock_basic_industry", "industry_match": True}
        
        # 应用exclude_keywords过滤
        if exclude_keywords:
            to_remove = []
            for code in matched:
                stock_name = name_map_basic.get(code, "")
                concepts = stock_concepts.get(code, [])
                skip = False
                for ek in exclude_keywords:
                    if ek in stock_name:
                        skip = True
                        break
                    for c in concepts:
                        if c.startswith(ek):
                            skip = True
                            break
                    if skip:
                        break
                if skip:
                    to_remove.append(code)
            
            for code in to_remove:
                del matched[code]

        theme_stock_map[theme_name] = matched
    return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts


def per_stock_features(df_one):
    if df_one is None or df_one.empty or len(df_one) < 6:
        return None

    df_one = df_one.sort_values("trade_date").reset_index(drop=True)
    close = df_one["close"].astype(float).values
    high = df_one["high"].astype(float).values
    low = df_one["low"].astype(float).values
    vol = df_one["vol"].astype(float).values
    pct = df_one["pct_chg"].astype(float).values

    n = len(close)
    last = n - 1

    def safe_pct(a, b):
        return (a / b - 1.0) * 100.0 if b and b > 0 else 0.0

    def calc_slope(prices):
        if len(prices) < 3:
            return 0.0
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        slope_norm = (slope / np.mean(prices)) * 100 if np.mean(prices) > 0 else 0
        return slope_norm

    ret_5 = safe_pct(close[last], close[last - 5]) if last - 5 >= 0 else safe_pct(close[last], close[0])
    ret_10 = safe_pct(close[last], close[last - 10]) if last - 10 >= 0 else safe_pct(close[last], close[0])
    ret_20 = safe_pct(close[last], close[last - 20]) if last - 20 >= 0 else safe_pct(close[last], close[0])

    ma5 = close[max(0, last - 4) : last + 1].mean()
    ma10 = close[max(0, last - 9) : last + 1].mean()
    ma20 = close[max(0, last - 19) : last + 1].mean()
    ma60 = close[max(0, last - 59) : last + 1].mean() if n >= 60 else ma20
    ma240 = close[max(0, last - 239) : last + 1].mean() if n >= 240 else ma60
    ma5_b = (close[last] / ma5 - 1) * 100 if ma5 > 0 else 0
    ma10_b = (close[last] / ma10 - 1) * 100 if ma10 > 0 else 0
    ma20_b = (close[last] / ma20 - 1) * 100 if ma20 > 0 else 0
    ma60_b = (close[last] / ma60 - 1) * 100 if ma60 > 0 else 0
    ma240_b = (close[last] / ma240 - 1) * 100 if ma240 > 0 else 0

    win10 = close[max(0, last - 9) : last + 1]
    slope10 = calc_slope(win10)
    win60 = close[max(0, last - 59) : last + 1]
    slope60 = calc_slope(win60)
    win240 = close[max(0, last - 239) : last + 1]
    slope240 = calc_slope(win240)

    acc_5_10 = ret_5 - ret_10

    v5 = vol[max(0, last - 4) : last + 1].mean()
    v20 = vol[max(0, last - 19) : last + 1].mean()
    vol_ratio = v5 / v20 if v20 > 0 else 1.0

    win10 = close[max(0, last - 9) : last + 1]
    if len(win10) > 1:
        running_max = np.maximum.accumulate(win10)
        drawdown = (win10 / running_max - 1.0)
        max_dd_10 = drawdown.min() * 100
    else:
        max_dd_10 = 0.0

    zt_flag = 1 if (pct[last] is not None and pct[last] >= 9.5) else 0
    strong_flag = 1 if (pct[last] is not None and pct[last] >= 5.0) else 0

    amount_latest = float(df_one.iloc[last].get("amount", 0) or 0) / 100000

    lb_height = 0
    for j in range(last, -1, -1):
        p = float(pct[j]) if pct[j] is not None else 0
        if p >= 9.5:
            lb_height += 1
        else:
            break

    return {
        "ret_5": ret_5, "ret_10": ret_10, "ret_20": ret_20,
        "ma5_b": ma5_b, "ma10_b": ma10_b, "ma20_b": ma20_b,
        "ma60_b": ma60_b, "ma240_b": ma240_b,
        "slope_10": slope10, "slope_60": slope60, "slope_240": slope240,
        "acc_5_10": acc_5_10, "vol_ratio": vol_ratio, "max_dd_10": max_dd_10,
        "zt_flag": zt_flag, "strong_flag": strong_flag,
        "pct_chg": float(pct[last]) if pct[last] is not None else 0.0,
        "turnover": float(df_one.iloc[last].get("turnover_rate", 0) or 0),
        "amount_latest": amount_latest, "lb_height": lb_height,
    }


def sigmoid(x, k=0.15, c=0.0):
    try:
        return 1.0 / (1.0 + np.exp(-k * (x - c)))
    except Exception:
        return 0.5


def linear(x, lo, hi, out_lo=0.0, out_hi=1.0):
    if hi == lo:
        return out_lo
    v = (x - lo) / (hi - lo)
    v = max(0.0, min(1.0, v))
    return out_lo + v * (out_hi - out_lo)


def calc_trend_score(stock_feats, market_index_ret):
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    avg_ret_5 = np.mean([s["ret_5"] for s in stock_feats])
    avg_ret_10 = np.mean([s["ret_10"] for s in stock_feats])
    avg_ret_20 = np.mean([s["ret_20"] for s in stock_feats])

    ret_score = (linear(avg_ret_5, -10, 15) * 0.5 + linear(avg_ret_10, -15, 25) * 0.3 + linear(avg_ret_20, -25, 40) * 0.2)

    pct_above_ma5 = sum(1 for s in stock_feats if s["ma5_b"] > 0) / n
    pct_above_ma10 = sum(1 for s in stock_feats if s["ma10_b"] > 0) / n
    pct_above_ma20 = sum(1 for s in stock_feats if s["ma20_b"] > 0) / n
    pct_above_ma60 = sum(1 for s in stock_feats if s["ma60_b"] > 0) / n
    pct_above_ma240 = sum(1 for s in stock_feats if s["ma240_b"] > 0) / n
    ma_score = pct_above_ma5 * 0.30 + pct_above_ma10 * 0.25 + pct_above_ma20 * 0.20 + pct_above_ma60 * 0.15 + pct_above_ma240 * 0.10

    avg_slope10 = np.mean([s["slope_10"] for s in stock_feats])
    avg_slope60 = np.mean([s["slope_60"] for s in stock_feats])
    avg_slope240 = np.mean([s["slope_240"] for s in stock_feats])
    slope_score = sigmoid(avg_slope10, k=0.3, c=0) * 0.4 + sigmoid(avg_slope60, k=0.25, c=0) * 0.35 + sigmoid(avg_slope240, k=0.2, c=0) * 0.25

    avg_acc = np.mean([s["acc_5_10"] for s in stock_feats])
    acc_score = sigmoid(avg_acc, k=0.3, c=0)

    pcts = sorted([s["pct_chg"] for s in stock_feats], reverse=True)
    top3 = pcts[: min(3, len(pcts))]
    top3_avg = np.mean(top3) if top3 else 0
    leader_score = linear(top3_avg, -5, 15)

    avg_dd = np.mean([s["max_dd_10"] for s in stock_feats])
    dd_score = linear(-avg_dd, -2, 10)

    rel_ret = avg_ret_10 - market_index_ret
    rel_score = sigmoid(rel_ret, k=0.2, c=0)

    # =========================
    # 当日动量（新增）：考虑当日涨跌对趋势的影响（权重较小，避免过度反应）
    # =========================
    pcts_today = [s["pct_chg"] for s in stock_feats]
    avg_pct_today = np.mean(pcts_today)
    up_n = sum(1 for p in pcts_today if p > 0)
    down_n = sum(1 for p in pcts_today if p < 0)
    breadth_today = up_n / n if n > 0 else 0.5
    
    # 当日动量分：综合考虑平均涨幅和上涨比例
    today_momentum_score = linear(avg_pct_today, -3, 3) * 0.6 + linear(breadth_today, 0.2, 0.8) * 0.4
    
    # 当日分歧微调（温和调整，不误判趋势结束）
    # 只有在大幅下跌+普跌时才轻微下调，否则基本不变
    if avg_pct_today < -2.0 and breadth_today < 0.3:
        today_adjust = 0.92  # 大幅分歧，小幅下调8%
    elif avg_pct_today < -1.0 and breadth_today < 0.4:
        today_adjust = 0.96  # 中等分歧，小幅下调4%
    else:
        today_adjust = 1.0  # 正常波动，不影响趋势分

    # 严格趋势判断：
    # 1. 60日和240日斜率必须是正的（向上趋势）
    # 2. 10日趋势斜率也要是正的
    # 3. 中期收益为正
    mid_trend_ok = (avg_slope60 > 0) and (avg_slope240 >= 0) and (avg_slope10 > 0) and (avg_ret_20 >= 0)

    # 最终评分：当日动量权重较小（8%），保持趋势分稳定
    score01 = (
        ret_score * 0.26 +           # 收益分
        ma_score * 0.22 +            # 均线分
        slope_score * 0.18 +         # 斜率分
        acc_score * 0.06 +           # 加速度分
        leader_score * 0.06 +        # 龙头分
        dd_score * 0.05 +            # 回撤分
        rel_score * 0.09 +           # 相对强度分
        today_momentum_score * 0.08  # 当日动量分（权重较小，避免过度反应）
    ) * today_adjust  # 温和调整
    
    score01 = max(0.0, min(1.0, score01))

    detail = {
        "avg_ret_5": round(avg_ret_5, 2), "avg_ret_10": round(avg_ret_10, 2), "avg_ret_20": round(avg_ret_20, 2),
        "pct_above_ma5": round(pct_above_ma5 * 100, 1), "pct_above_ma10": round(pct_above_ma10 * 100, 1),
        "pct_above_ma20": round(pct_above_ma20 * 100, 1), "pct_above_ma60": round(pct_above_ma60 * 100, 1),
        "pct_above_ma240": round(pct_above_ma240 * 100, 1),
        "avg_slope_10": round(avg_slope10, 3), "avg_slope_60": round(avg_slope60, 3), "avg_slope_240": round(avg_slope240, 3),
        "avg_acc_5_10": round(avg_acc, 2), "top3_avg_pct": round(top3_avg, 2),
        "avg_max_dd_10": round(avg_dd, 2), "rel_ret_10": round(rel_ret, 2), "mid_trend_ok": 1 if mid_trend_ok else 0,
        "avg_pct_today": round(avg_pct_today, 2), "breadth_today": round(breadth_today * 100, 1), "today_adjust": today_adjust,
    }
    return round(score01 * 100, 1), detail


def calc_sentiment_score(stock_feats, market_index_ret):
    if not stock_feats:
        return 0.0, {}

    n = len(stock_feats)
    pcts = [s["pct_chg"] for s in stock_feats]
    up_n = sum(1 for p in pcts if p > 0)
    down_n = sum(1 for p in pcts if p < 0)
    zt_n = sum(1 for s in stock_feats if s["zt_flag"] == 1)
    strong_n = sum(1 for s in stock_feats if s["strong_flag"] == 1)

    breadth = up_n / n
    breadth_score = linear(breadth, 0.2, 0.85)
    zt_ratio = zt_n / n
    zt_score = linear(zt_ratio, 0, 0.15)
    strong_ratio = strong_n / n
    strong_score = linear(strong_ratio, 0, 0.30)
    avg_vol_ratio = np.mean([s["vol_ratio"] for s in stock_feats])
    vol_score = linear(avg_vol_ratio, 0.6, 1.8)
    avg_turnover = np.mean([s["turnover"] for s in stock_feats])
    turnover_score = linear(avg_turnover, 1.0, 8.0)
    median_pct = float(np.median(pcts))
    mean_pct = float(np.mean(pcts))
    profit_score = sigmoid(median_pct * 0.6 + mean_pct * 0.4, k=0.25, c=0)
    top1 = max(pcts) if pcts else 0
    resonance = 1.0 if (zt_n >= 1 and top1 >= 7) else 0.0
    if zt_n >= 2 and top1 >= 9:
        resonance = 1.2
    resonance_score = min(resonance, 1.0)

    score01 = breadth_score * 0.25 + zt_score * 0.20 + strong_score * 0.15 + vol_score * 0.10 + turnover_score * 0.10 + profit_score * 0.10 + resonance_score * 0.10
    score01 = max(0.0, min(1.0, score01))

    detail = {
        "up_ratio": round(breadth * 100, 1), "down_ratio": round(down_n / n * 100, 1),
        "zt_count": zt_n, "zt_ratio": round(zt_ratio * 100, 1), "strong_ratio": round(strong_ratio * 100, 1),
        "avg_vol_ratio": round(avg_vol_ratio, 2), "avg_turnover": round(avg_turnover, 2),
        "median_pct": round(median_pct, 2), "mean_pct": round(mean_pct, 2), "top1_pct": round(top1, 2), "resonance": round(resonance, 2),
    }
    return round(score01 * 100, 1), detail


def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = deltas[deltas > 0]
    losses = -deltas[deltas < 0]
    avg_gain = np.mean(gains[:period]) if len(gains) >= period else 0.0001
    avg_loss = np.mean(losses[:period]) if len(losses) >= period else 0.0001
    for i in range(period, len(deltas)):
        delta = deltas[i]
        gain = delta if delta > 0 else 0
        loss = -delta if delta < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    rs = avg_gain / max(avg_loss, 0.0001)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_theme_state(r, prev_data=None):
    """判断主题状态
    
    状态定义：
    - 启动：趋势分从低位快速上升，情绪分开始活跃
    - 主升：趋势分>=60且持续上升，情绪分>=60
    - 高潮：趋势分>=70，情绪分>=85，涨停数>=5
    - 分歧：趋势分>=50但当日下跌，情绪分下降
    - 分歧转一致：前期分歧后趋势分回升，情绪分回升，上涨比例>60%
    - 退潮：趋势分持续下降，情绪分<40
    
    Args:
        r: 当前主题数据
        prev_data: 前一日主题数据（用于判断趋势变化）
    
    Returns:
        state: 主题状态字符串
    """
    t_score = r.get("trend_score", 0)
    s_score = r.get("sentiment_score", 0)
    td = r.get("trend_detail", {}) or {}
    sd = r.get("sentiment_detail", {}) or {}
    
    avg_ret_5 = td.get("avg_ret_5", 0)
    avg_ret_10 = td.get("avg_ret_10", 0)
    avg_pct_today = td.get("avg_pct_today", 0)
    pct_above_ma5 = td.get("pct_above_ma5", 0)
    avg_slope_10 = td.get("avg_slope_10", 0)
    mid_trend_ok = td.get("mid_trend_ok", 0)
    
    zt_count = sd.get("zt_count", 0)
    up_ratio = sd.get("up_ratio", 0)
    
    # 获取前一日数据
    prev_t_score = 0
    prev_s_score = 0
    prev_up_ratio = 0
    if prev_data:
        prev_t_score = prev_data.get("trend_score", 0)
        prev_s_score = prev_data.get("sentiment_score", 0)
        prev_sd = prev_data.get("sentiment_detail", {}) or {}
        prev_up_ratio = prev_sd.get("up_ratio", 0)
    
    # 1. 高潮：趋势分>=70，情绪分>=85，涨停数>=5
    if t_score >= 70 and s_score >= 85 and zt_count >= 5:
        return "高潮"
    
    # 2. 退潮：趋势分持续下降，情绪分<40
    if t_score < 50 and s_score < 40 and avg_slope_10 < 0:
        return "退潮"
    
    # 3. 分歧转一致：前期分歧后趋势分回升
    # 条件：
    # - 前一日趋势分>=50且<65（分歧状态）
    # - 当日趋势分回升（t_score > prev_t_score）
    # - 情绪分回升（s_score > prev_s_score）
    # - 上涨比例>60%
    # - 涨停数>=3
    if (prev_data and 
        50 <= prev_t_score < 65 and 
        t_score > prev_t_score and 
        s_score > prev_s_score and 
        up_ratio > 60 and 
        zt_count >= 3):
        return "分歧转一致"
    
    # 4. 分歧：趋势分>=50但当日下跌，情绪分下降
    if (t_score >= 50 and 
        avg_pct_today < 0 and 
        up_ratio < 50 and 
        s_score < prev_s_score):
        return "分歧"
    
    # 5. 启动：趋势分从低位快速上升
    # 条件：
    # - 趋势分>=45且<60
    # - 5日涨幅从负转正或快速上升
    # - 情绪分开始活跃（涨停数>=3）
    # - 成交量放大
    if (45 <= t_score < 60 and 
        avg_ret_5 > 0 and 
        avg_ret_10 < 0 and 
        zt_count >= 3 and 
        t_score > prev_t_score):
        return "启动"
    
    # 6. 主升：趋势分>=60且持续上升，情绪分>=60
    if (t_score >= 60 and 
        s_score >= 60 and 
        avg_slope_10 > 0 and 
        pct_above_ma5 >= 60 and 
        mid_trend_ok == 1):
        return "主升"
    
    # 默认：根据趋势分和情绪分判断
    if t_score >= 60:
        return "强势"
    elif t_score >= 50:
        return "震荡"
    else:
        return "弱势"


def calc_turnover_percentile(turnover_list, current_turnover):
    if not turnover_list or len(turnover_list) == 0:
        return 50.0
    sorted_list = sorted(turnover_list)
    idx = sum(1 for x in sorted_list if x <= current_turnover)
    return (idx / len(sorted_list)) * 100


def generate_trading_signals(results, rows_per_theme, kline_groups):
    signals = {"buy": [], "sell": [], "hold": [], "climax_warning": [], "dip_buy": []}

    trend_sorted = sorted([r for r in results if r["n_stocks"] >= MIN_STOCKS], key=lambda x: x["trend_score"], reverse=True)
    top_trend = trend_sorted[:TOP_TREND_N]
    top_trend_names = set([r["theme"] for r in top_trend])

    valid_results = [r for r in results if r["n_stocks"] >= MIN_STOCKS]
    sentiment_sorted = sorted(valid_results, key=lambda x: x["sentiment_score"], reverse=True)
    sentiment_rank1 = sentiment_sorted[0]["theme"] if sentiment_sorted else None

    climax_theme_names = set()

    for r in results:
        theme = r["theme"]
        td = r.get("trend_detail", {}) or {}
        sd = r.get("sentiment_detail", {}) or {}

        stock_feats = rows_per_theme.get(theme, [])
        if not stock_feats:
            signals["hold"].append({"theme": theme, "trend_score": 0.0, "sentiment_score": 0.0, "rsi": 50.0, "turnover_percentile": 50.0, "in_top_trend": False, "reason": "成份股不足"})
            continue

        all_prices = []
        all_turnovers = []
        for sf in stock_feats:
            code = sf["ts_code"]
            kdf = kline_groups.get(code)
            if kdf is None or kdf.empty:
                continue
            kdf_sorted = kdf.sort_values("trade_date")
            closes = kdf_sorted["close"].astype(float).values
            turnovers = kdf_sorted.get("turnover_rate", pd.Series([])).astype(float).values
            all_prices.append(closes)
            all_turnovers.append(turnovers)

        if all_prices:
            min_len = min(len(p) for p in all_prices)
            aligned_prices = [p[-min_len:] for p in all_prices]
            avg_prices = np.mean(aligned_prices, axis=0)
            rsi = calc_rsi(avg_prices, 14)
        else:
            rsi = 50.0

        avg_turnover = sd.get("avg_turnover", 0.0)
        turnover_history = []
        for t_list in all_turnovers:
            turnover_history.extend(t_list)
        turnover_percentile = calc_turnover_percentile(turnover_history, avg_turnover)

        pct_above_ma5 = td.get("pct_above_ma5", 0.0)
        pct_above_ma10 = td.get("pct_above_ma10", 0.0)
        above_ma_both = pct_above_ma5 >= 50 and pct_above_ma10 >= 50
        below_ma_both = pct_above_ma5 < 30 and pct_above_ma10 < 30

        signal_detail = {"theme": theme, "trend_score": r["trend_score"], "sentiment_score": r["sentiment_score"], "rsi": round(rsi, 1), "turnover_percentile": round(turnover_percentile, 1), "in_top_trend": theme in top_trend_names}

        is_rank1 = (theme == sentiment_rank1 and CLIMAX_SENTIMENT_RANK1)
        is_climax_sentiment = r["sentiment_score"] >= CLIMAX_SENTIMENT_THRESHOLD
        is_climax_trend = r["trend_score"] >= CLIMAX_TREND_THRESHOLD
        is_climax = (is_climax_trend and is_climax_sentiment) or (is_rank1 and is_climax_trend)
        if is_climax:
            climax_theme_names.add(theme)
            signals["climax_warning"].append({"theme": theme, "trend_score": r["trend_score"], "sentiment_score": r["sentiment_score"], "composite_score": r["composite_score"],
                                              "zt_count": sd.get("zt_count", 0), "zt_ratio": sd.get("zt_ratio", 0), "up_ratio": sd.get("up_ratio", 0), "rsi": round(rsi, 1),
                                              "turnover_percentile": round(turnover_percentile, 1), "is_rank1": is_rank1, "reason": f"情绪分过高({r['sentiment_score']:.0f})+趋势良好({r['trend_score']:.0f})，如明日继续冲高应止盈减仓！"})

        mid_trend_ok = td.get("mid_trend_ok", 0) == 1
        # 低吸条件更严格：
        # 1. 趋势分 >= 55
        # 2. 情绪分 < 50
        # 3. mid_trend_ok 为真（趋势向上）
        # 4. 至少有50%的股票在MA10或MA20上方（均线支撑）
        pct_above_ma10 = td.get("pct_above_ma10", 0)
        pct_above_ma20 = td.get("pct_above_ma20", 0)
        has_ma_support = (pct_above_ma10 >= 50) or (pct_above_ma20 >= 50)
        is_dip = (r["trend_score"] >= DIP_TREND_THRESHOLD and 
                  r["sentiment_score"] < DIP_SENTIMENT_CEILING and 
                  mid_trend_ok and 
                  has_ma_support)
        if is_dip:
            signals["dip_buy"].append({"theme": theme, "trend_score": r["trend_score"], "sentiment_score": r["sentiment_score"], "composite_score": r["composite_score"],
                                        "rsi": round(rsi, 1), "turnover_percentile": round(turnover_percentile, 1), "reason": f"趋势向上({r['trend_score']:.0f})情绪回调中({r['sentiment_score']:.0f})，均线有支撑，可低吸博弈情绪回升"})

        is_emotion_ok = (rsi < RSI_BUY_THRESHOLD) and (r["sentiment_score"] < CLIMAX_SENTIMENT_THRESHOLD)
        not_in_climax = theme not in climax_theme_names
        if theme in top_trend_names and is_emotion_ok and above_ma_both and not_in_climax:
            signal_detail["reason"] = f"趋势排名前{TOP_TREND_N} + RSI({rsi:.1f})<{RSI_BUY_THRESHOLD} + 情绪分<{CLIMAX_SENTIMENT_THRESHOLD} + 站上均线"
            signals["buy"].append(signal_detail)
        elif turnover_percentile >= TURNOVER_SELL_PERCENTILE or below_ma_both:
            if turnover_percentile >= TURNOVER_SELL_PERCENTILE:
                signal_detail["reason"] = f"换手率({avg_turnover:.2f}%)处于历史{turnover_percentile:.1f}%分位，情绪极端"
            else:
                signal_detail["reason"] = "跌破短期均线，趋势转弱"
            signals["sell"].append(signal_detail)
        else:
            signal_detail["reason"] = "观望"
            signals["hold"].append(signal_detail)

    signals["buy"].sort(key=lambda x: x["trend_score"], reverse=True)
    signals["sell"].sort(key=lambda x: x["turnover_percentile"], reverse=True)
    signals["climax_warning"].sort(key=lambda x: (x["trend_score"] + x["sentiment_score"]), reverse=True)
    signals["dip_buy"].sort(key=lambda x: x["trend_score"], reverse=True)

    return signals


def print_trading_signals(signals):
    print("\n" + "=" * 80)
    print("板块买卖判断（战术层面）")
    print("=" * 80)

    print(f"\n🚨【高潮警示】（情绪分>={CLIMAX_SENTIMENT_THRESHOLD}或情绪分第一名 + 趋势>={CLIMAX_TREND_THRESHOLD} = 警惕冲高回落/止盈减仓）")
    if signals["climax_warning"]:
        print("-" * 80)
        for i, w in enumerate(signals["climax_warning"], 1):
            rank1_tag = " 👑情绪分NO.1" if w.get("is_rank1") else ""
            print(f"  ⚠️ {i:2d}. {w['theme']:14s}{rank1_tag} | 趋势:{w['trend_score']:5.1f} 情绪:{w['sentiment_score']:5.1f} 综合:{w['composite_score']:5.1f}")
            print(f"       涨停:{w['zt_count']}家({w['zt_ratio']:.1f}%) 上涨:{w['up_ratio']:.1f}% RSI:{w['rsi']:5.1f} 换手分位:{w['turnover_percentile']:.1f}%")
            print(f"       → {w['reason']}")
        print("-" * 80)
    else:
        print("  暂无（当前无高情绪+高趋势共振主题）")

    print(f"\n💎【低吸博弈】（趋势>={DIP_TREND_THRESHOLD} + 情绪分<{DIP_SENTIMENT_CEILING} = 趋势良好情绪回调，可低吸）")
    if signals["dip_buy"]:
        print("-" * 80)
        for i, d in enumerate(signals["dip_buy"], 1):
            print(f"  💎 {i:2d}. {d['theme']:14s} | 趋势:{d['trend_score']:5.1f} 情绪:{d['sentiment_score']:5.1f} 综合:{d['composite_score']:5.1f} RSI:{d['rsi']:5.1f}")
            print(f"       → {d['reason']}")
        print("-" * 80)
    else:
        print("  暂无（当前无趋势良好+情绪偏低的低吸机会）")

    print(f"\n【买入信号】（趋势前{TOP_TREND_N} + RSI<{RSI_BUY_THRESHOLD} + 均线多头）")
    if signals["buy"]:
        for i, s in enumerate(signals["buy"], 1):
            print(f"  {i:2d}. {s['theme']:14s} | 趋势分:{s['trend_score']:5.1f} 情绪分:{s['sentiment_score']:5.1f} RSI:{s['rsi']:5.1f} | {s['reason']}")
    else:
        print("  暂无")

    print(f"\n【卖出/减仓信号】（换手率>{TURNOVER_SELL_PERCENTILE}%分位 OR 跌破均线）")
    if signals["sell"]:
        for i, s in enumerate(signals["sell"], 1):
            print(f"  {i:2d}. {s['theme']:14s} | 趋势分:{s['trend_score']:5.1f} 情绪分:{s['sentiment_score']:5.1f} 换手分位:{s['turnover_percentile']:5.1f}% | {s['reason']}")
    else:
        print("  暂无")

    print("\n【观望信号】")
    if signals["hold"]:
        for i, s in enumerate(signals["hold"][:5], 1):
            print(f"  {i:2d}. {s['theme']:14s} | {s['reason']}")
        if len(signals["hold"]) > 5:
            print(f"  ... 还有 {len(signals['hold'])-5} 个")
    else:
        print("  暂无")
    print("\n" + "=" * 80)


def save_to_csv(results):
    flat = []
    for r in results:
        climax_warning = 1 if (r["trend_score"] >= 70 and r["sentiment_score"] >= 85) else 0
        row = {"rank": r["rank"], "theme": r["theme"], "n_stocks": r["n_stocks"], "trend_score": r["trend_score"],
               "sentiment_score": r["sentiment_score"], "composite_score": r["composite_score"], "climax_warning": climax_warning,
               "leader_name": r.get("leader_name", ""), "leader_code": r.get("leader_code", ""), "leader_score": r.get("leader_score", 0),
               "core_name": r.get("core_name", ""), "core_code": r.get("core_code", ""), "core_score": r.get("core_score", 0)}
        row.update({f"t_{k}": v for k, v in (r.get("trend_detail") or {}).items()})
        row.update({f"s_{k}": v for k, v in (r.get("sentiment_detail") or {}).items()})
        flat.append(row)
    pd.DataFrame(flat).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")


def save_to_sqlite(results):
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 先删除该日期的旧数据
    cur.execute("DELETE FROM theme_scores WHERE trade_date = ?", (TRADE_DATE,))
    
    # 创建表（如果不存在）或添加新列（如果表已存在但缺少列）
    cur.execute("""CREATE TABLE IF NOT EXISTS theme_scores (
        rank INTEGER, theme TEXT, n_stocks INTEGER, trend_score REAL, sentiment_score REAL, composite_score REAL,
        climax_warning INTEGER DEFAULT 0, leader_name TEXT, leader_code TEXT, leader_score REAL,
        core_name TEXT, core_code TEXT, core_score REAL, ret_5 REAL, ret_10 REAL, ret_20 REAL, up_ratio REAL, zt_count INTEGER, 
        theme_state TEXT, trade_date TEXT
    )""")
    
    # 检查表结构，如果缺少theme_state列则添加
    cur.execute("PRAGMA table_info(theme_scores)")
    columns = [row[1] for row in cur.fetchall()]
    if "theme_state" not in columns:
        cur.execute("ALTER TABLE theme_scores ADD COLUMN theme_state TEXT DEFAULT '弱势'")
    
    # 获取更新后的列
    cur.execute("PRAGMA table_info(theme_scores)")
    columns = [row[1] for row in cur.fetchall()]
    placeholders = ','.join(['?' for _ in columns])
    col_str = ', '.join(columns)
    
    # 插入数据
    for r in results:
        td = r.get("trend_detail", {}) or {}
        sd = r.get("sentiment_detail", {}) or {}
        climax_warning = 1 if (r["trend_score"] >= 70 and r["sentiment_score"] >= 85) else 0
        theme_state = r.get("theme_state", "弱势")
        
        values = (
            r["rank"], r["theme"], r["n_stocks"], r["trend_score"], r["sentiment_score"], r["composite_score"], climax_warning,
            r.get("leader_name", ""), r.get("leader_code", ""), r.get("leader_score", 0),
            r.get("core_name", ""), r.get("core_code", ""), r.get("core_score", 0),
            td.get("avg_ret_5", 0), td.get("avg_ret_10", 0), td.get("avg_ret_20", 0), sd.get("up_ratio", 0), sd.get("zt_count", 0),
            theme_state, TRADE_DATE
        )
        cur.execute(f"INSERT INTO theme_scores ({col_str}) VALUES ({placeholders})", values)
    conn.commit()
    conn.close()


def save_report_text(results, signals):
    """输出精简版分析报告"""
    report_path = os.path.join(REPORT_DIR, f"theme_analysis_{TRADE_DATE}.txt")

    buf = []
    def w(s=""):
        buf.append(s)

    w("=" * 80)
    w(f"  主题趋势分析报告 - {TRADE_DATE}")
    w("=" * 80)
    w()

    # ========== 重点机会：分歧转一致 ==========
    divergence_to_consensus = [r for r in results if r.get("theme_state") == "分歧转一致"]
    if divergence_to_consensus:
        w("★ 分歧转一致（重点关注）★")
        w("-" * 60)
        for r in divergence_to_consensus:
            sd = r.get("sentiment_detail", {}) or {}
            td = r.get("trend_detail", {}) or {}
            w(f"  {r['theme']:<12} 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 上涨:{sd.get('up_ratio', 0):.0f}%")
            w(f"              龙头:{r.get('leader_name', '')}")
        w()

    # ========== 重点机会：启动/主升 ==========
    start_rising = [r for r in results if r.get("theme_state") in ["启动", "主升"]]
    if start_rising:
        w("☆ 启动/主升（趋势向好）☆")
        w("-" * 60)
        for r in start_rising[:5]:
            sd = r.get("sentiment_detail", {}) or {}
            w(f"  {r['theme']:<12} 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 龙头:{r.get('leader_name', '')}")
        w()

    # ========== 风险警示：高潮/退潮 ==========
    climax = [r for r in results if r.get("theme_state") == "高潮"]
    retreat = [r for r in results if r.get("theme_state") == "退潮"]
    if climax or retreat:
        w("⚠️ 风险提示")
        w("-" * 60)
        for r in climax:
            sd = r.get("sentiment_detail", {}) or {}
            w(f"  {r['theme']:<12} 高潮⚠️ 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} 涨停:{sd.get('zt_count', 0)}家 → 注意止盈")
        for r in retreat:
            w(f"  {r['theme']:<12} 退潮 趋势:{r['trend_score']:5.1f} 情绪:{r['sentiment_score']:5.1f} → 规避")
        w()

    # ========== 主题排名表（精简版）==========
    w("-" * 80)
    w(f"{'排名':<3} {'主题':<12} {'趋势':<6} {'情绪':<6} {'综合':<6} {'状态':<10}")
    w("-" * 80)
    for r in results:
        theme_state = r.get("theme_state", "弱势")
        state_icon = ""
        if theme_state == "高潮": state_icon = "高潮⚠️"
        elif theme_state == "分歧转一致": state_icon = "转一致⭐"
        elif theme_state == "主升": state_icon = "主升↑"
        elif theme_state == "启动": state_icon = "启动↑"
        elif theme_state == "分歧": state_icon = "分歧~"
        elif theme_state == "退潮": state_icon = "退潮↓"
        else: state_icon = theme_state
        w(f"{r['rank']:<3} {r['theme']:<12} {r['trend_score']:<6.1f} {r['sentiment_score']:<6.1f} {r['composite_score']:<6.1f} {state_icon:<10}")
    w("-" * 80)
    w()

    # ========== 重点主题龙头 ==========
    top_themes = [r for r in results if r.get("theme_state") in ["分歧转一致", "主升", "启动", "高潮"]]
    if top_themes:
        w("【重点主题龙头股】")
        w("-" * 60)
        for r in top_themes[:6]:
            ld = f"{r.get('leader_name', '')}({r.get('leader_code', '')})" if r.get("leader_name") else "-"
            cd = f"{r.get('core_name', '')}({r.get('core_code', '')})" if r.get("core_name") else "-"
            w(f"  {r['theme']:<12} 龙头:{ld:<16} 中军:{cd:<16}")
        w()

    w("=" * 80)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(buf))
    print(f"[Save] 分析报告: {report_path}")


def main():
    print("=" * 60)
    print("主题趋势 + 情绪 评分系统（自建'行业最强'算法）")
    print("=" * 60)

    hot_themes = load_theme_json()
    print(f"[Theme] 加载 {len(hot_themes)} 个主题")

    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    print(f"[Data] stock_basic: {len(stock_basic)}  daily_basic: {len(daily_basic)}")

    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)

    all_codes = set()
    for tn, m in theme_stock_map.items():
        all_codes.update(m.keys())
    print(f"[Match] 全市场命中成份股去重: {len(all_codes)} 只")

    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
    print(f"[KLine] 拉取 {len(kline_df)} 条 K 线记录")

    idx_df = get_index_kline("000300.SH")
    market_ret_10 = 0.0
    if idx_df is not None and not idx_df.empty:
        idx_df = idx_df.sort_values("trade_date")
        closes = idx_df["close"].astype(float).values
        if len(closes) >= 11:
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100
    print(f"[Index] 沪深300 近10日收益: {market_ret_10:+.2f}%")

    # 获取前一日主题数据（用于判断状态变化）
    prev_theme_data = get_prev_day_theme_data()
    if prev_theme_data:
        print(f"[State] 获取前一日 {len(prev_theme_data)} 个主题数据")

    kline_groups = {}
    if not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub

    results = []
    rows_per_theme = {}
    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            results.append({"theme": theme_name, "n_stocks": 0, "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
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

            # 合并换手率（从daily_basic获取）
            if not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)

            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            if _in_industry_list(stock_industry.get(code, ""), industry_list):
                purity += 1

            mv = mcap_dict.get(code, {}).get("total_mv", 0) or 0
            feat["ts_code"] = code
            feat["name"] = name_map_basic.get(code, code)
            feat["purity"] = purity
            feat["total_mv"] = mv
            feat["industry_match"] = meta.get("industry_match", False)
            rows.append(feat)

        if len(rows) < MIN_STOCKS:
            results.append({"theme": theme_name, "n_stocks": len(rows), "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
            rows_per_theme[theme_name] = []
            continue

        # =========================
        # 统计全成份股的涨停数（用于情绪分计算）
        # =========================
        all_rows = rows  # 全部成份股
        all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
        all_up_count = sum(1 for r in all_rows if r.get("pct_chg", 0) > 0)
        all_down_count = sum(1 for r in all_rows if r.get("pct_chg", 0) < 0)
        all_total = len(all_rows)

        # =========================
        # 按市值权重排序，取前30只用于趋势分计算
        # =========================
        for r in rows:
            r["mcap_w"] = (r["total_mv"] / 10000) ** 0.5 * 0.8 + r["purity"] * 2
            r["mcap_w"] *= 1.0 if r["industry_match"] else 0.5
        rows.sort(key=lambda x: x["mcap_w"], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]  # 前30只用于趋势分计算

        t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
        s_score, s_detail = calc_sentiment_score(all_rows, market_ret_10)  # 情绪分用全成份股计算
        
        composite = round(0.55 * t_score + 0.45 * s_score, 1)

        # 判断主题状态
        theme_result = {
            "theme": theme_name, "n_stocks": all_total, "trend_score": t_score, "sentiment_score": s_score,
            "composite_score": composite, "trend_detail": t_detail, "sentiment_detail": s_detail,
        }
        prev_data = prev_theme_data.get(theme_name)
        theme_state = calc_theme_state(theme_result, prev_data)
        theme_result["theme_state"] = theme_state

        leader_scores = []
        for r in top_rows:
            lb = r.get("lb_height", 0)
            pct = abs(r.get("pct_chg", 0))
            amt = r.get("amount_latest", 0)
            purity = r.get("purity", 0)
            ls = 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(purity * 20, 100)
            leader_scores.append((r, ls))
        leader_scores.sort(key=lambda x: x[1], reverse=True)
        leader_stock = leader_scores[0][0] if leader_scores else None
        leader_name = leader_stock["name"] if leader_stock else ""
        leader_code = leader_stock["ts_code"] if leader_stock else ""

        core_candidates = [r for r in top_rows if r.get("total_mv", 0) > 2000000 and r.get("purity", 0) >= 1]
        core_scores = []
        for r in core_candidates:
            amt = r.get("amount_latest", 0)
            mv = r.get("total_mv", 0) / 10000
            pct = abs(r.get("pct_chg", 0))
            cs = 0.5 * min(amt * 2, 100) + 0.3 * min(mv / 10, 100) + 0.2 * min(pct * 5, 100)
            core_scores.append((r, cs))
        core_scores.sort(key=lambda x: x[1], reverse=True)
        core_stock = core_scores[0][0] if core_scores else None
        core_name = core_stock["name"] if core_stock else ""
        core_code = core_stock["ts_code"] if core_stock else ""

        # 添加龙头股和中军股信息
        theme_result["leader_name"] = leader_name
        theme_result["leader_code"] = leader_code
        theme_result["leader_score"] = round(leader_scores[0][1], 1) if leader_scores else 0
        theme_result["core_name"] = core_name
        theme_result["core_code"] = core_code
        theme_result["core_score"] = round(core_scores[0][1], 1) if core_scores else 0

        results.append(theme_result)
        rows_per_theme[theme_name] = top_rows

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    climax_themes = set()
    for r in results:
        if r["trend_score"] >= 70 and r["sentiment_score"] >= 85:
            climax_themes.add(r["theme"])

    print("\n" + "=" * 110)
    print(f"{'排名':<4}{'主题':<14}{'成份':<6}{'趋势分':<8}{'情绪分':<8}{'综合分':<8}{'5日%':<7}{'10日%':<7}{'20日%':<7}{'上涨%':<6}{'涨停':<6}{'共振':<6}{'状态'}")
    print("-" * 110)
    for r in results:
        td = r.get("trend_detail", {}) or {}
        sd = r.get("sentiment_detail", {}) or {}
        if r["theme"] in climax_themes:
            status = "⚠️高潮"
        elif r["trend_score"] >= 70:
            status = "🟢强"
        elif r["trend_score"] >= 50:
            status = "🟡中"
        else:
            status = "⚪弱"
        print(f"{r['rank']:<4}{r['theme']:<14}{r['n_stocks']:<6}{r['trend_score']:<8}{r['sentiment_score']:<8}{r['composite_score']:<8}"
              f"{td.get('avg_ret_5', 0):<7}{td.get('avg_ret_10', 0):<7}{td.get('avg_ret_20', 0):<7}"
              f"{sd.get('up_ratio', 0):<6}{sd.get('zt_count', 0):<6}{sd.get('resonance', 0):<6}{status}")
    print("=" * 110)

    print("\n" + "=" * 110)
    print("主题龙头/中军一览")
    print("=" * 110)
    print(f"{'排名':<4}{'主题':<14}{'龙头':<18}{'龙头评分':<10}{'中军':<18}{'中军评分':<10}")
    print("-" * 110)
    for r in results[:15]:
        ld = f"{r.get('leader_name', '')}({r.get('leader_code', '')})" if r.get("leader_name") else "-"
        cd = f"{r.get('core_name', '')}({r.get('core_code', '')})" if r.get("core_name") else "-"
        print(f"{r['rank']:<4}{r['theme']:<14}{ld:<18}{r.get('leader_score', 0):<10}{cd:<18}{r.get('core_score', 0):<10}")
    print("=" * 110)

    signals = generate_trading_signals(results, rows_per_theme, kline_groups)
    print_trading_signals(signals)

    save_to_csv(results)
    save_to_sqlite(results)
    save_report_text(results, signals)
    print(f"\n[Save] CSV: {OUTPUT_CSV}")
    print(f"[Save] DB : {OUTPUT_DB}")


def run_theme_analysis():
    """供外部调用的主题分析入口，返回 (results, signals)"""
    hot_themes = load_theme_json()
    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)

    all_codes = set()
    for tn, m in theme_stock_map.items():
        all_codes.update(m.keys())

    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
    idx_df = get_index_kline("000300.SH")
    market_ret_10 = 0.0
    if idx_df is not None and not idx_df.empty:
        idx_df = idx_df.sort_values("trade_date")
        closes = idx_df["close"].astype(float).values
        if len(closes) >= 11:
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100

    kline_groups = {}
    if not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub

    results = []
    rows_per_theme = {}
    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            results.append({"theme": theme_name, "n_stocks": 0, "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
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

            # 合并换手率（从daily_basic获取）
            if not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)

            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            if _in_industry_list(stock_industry.get(code, ""), industry_list):
                purity += 1

            mv = mcap_dict.get(code, {}).get("total_mv", 0) or 0
            feat["ts_code"] = code
            feat["name"] = name_map_basic.get(code, code)
            feat["purity"] = purity
            feat["total_mv"] = mv
            feat["industry_match"] = meta.get("industry_match", False)
            rows.append(feat)

        if len(rows) < MIN_STOCKS:
            results.append({"theme": theme_name, "n_stocks": len(rows), "trend_score": 0.0, "sentiment_score": 0.0, "composite_score": 0.0})
            rows_per_theme[theme_name] = []
            continue

        # =========================
        # 统计全成份股的涨停数（用于情绪分计算）
        # =========================
        all_rows = rows  # 全部成份股
        all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
        all_up_count = sum(1 for r in all_rows if r.get("pct_chg", 0) > 0)
        all_down_count = sum(1 for r in all_rows if r.get("pct_chg", 0) < 0)
        all_total = len(all_rows)

        # =========================
        # 按市值权重排序，取前30只用于趋势分计算
        # =========================
        for r in rows:
            r["mcap_w"] = (r["total_mv"] / 10000) ** 0.5 * 0.8 + r["purity"] * 2
            r["mcap_w"] *= 1.0 if r["industry_match"] else 0.5
        rows.sort(key=lambda x: x["mcap_w"], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]  # 前30只用于趋势分计算

        t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
        s_score, s_detail = calc_sentiment_score(all_rows, market_ret_10)  # 情绪分用全成份股计算
        
        composite = round(0.55 * t_score + 0.45 * s_score, 1)

        results.append({
            "theme": theme_name, "n_stocks": all_total, "trend_score": t_score, "sentiment_score": s_score,
            "composite_score": composite, "trend_detail": t_detail, "sentiment_detail": s_detail
        })
        rows_per_theme[theme_name] = top_rows

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    signals = generate_trading_signals(results, rows_per_theme, kline_groups)

    return results, signals


def get_prev_day_theme_data():
    """获取前一日的主题数据（用于判断状态变化）"""
    if not os.path.exists(OUTPUT_DB):
        return {}
    
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 获取最新的交易日期（排除当天）
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 2")
    dates = [row[0] for row in cur.fetchall()]
    
    if len(dates) < 2:
        conn.close()
        return {}
    
    prev_date = dates[1]  # 前一日
    
    # 检查表结构，获取所有列名
    cur.execute("PRAGMA table_info(theme_scores)")
    columns = [row[1] for row in cur.fetchall()]
    
    # 选择基本列
    select_cols = ["theme", "trend_score", "sentiment_score", "ret_5", "ret_10", "ret_20", "up_ratio", "zt_count"]
    if "theme_state" in columns:
        select_cols.append("theme_state")
    
    # 构建查询
    col_str = ", ".join(select_cols)
    cur.execute(f"SELECT {col_str} FROM theme_scores WHERE trade_date = ?", (prev_date,))
    rows = cur.fetchall()
    
    prev_data = {}
    for row in rows:
        theme = row[0]
        theme_state = row[-1] if "theme_state" in columns else "弱势"
        prev_data[theme] = {
            "trend_score": row[1],
            "sentiment_score": row[2],
            "trend_detail": {
                "avg_ret_5": row[3],
                "avg_ret_10": row[4],
                "avg_ret_20": row[5],
            },
            "sentiment_detail": {
                "up_ratio": row[6],
                "zt_count": row[7],
            },
            "theme_state": theme_state,
        }
    
    conn.close()
    return prev_data


def get_60day_avg_trend_score():
    """
    从SQLite数据库读取历史数据，计算每个主题的前60个交易日趋势分平均值
    
    Returns:
        dict: {theme_name: avg_trend_score}
    """
    import sqlite3
    from collections import defaultdict
    
    if not os.path.exists(OUTPUT_DB):
        print(f"[60天平均] 数据库不存在: {OUTPUT_DB}")
        return {}
    
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    # 获取所有可用的交易日期（按倒序）
    cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC")
    dates = [row[0] for row in cur.fetchall()]
    
    if not dates:
        print("[60天平均] 数据库中无数据")
        conn.close()
        return {}
    
    # 取最新的60个交易日
    recent_dates = dates[:60]
    print(f"[60天平均] 使用 {len(recent_dates)} 个交易日的数据")
    
    # 查询这些日期的所有主题趋势分
    placeholders = ','.join(['?' for _ in recent_dates])
    cur.execute(f"SELECT theme, trend_score FROM theme_scores WHERE trade_date IN ({placeholders})", recent_dates)
    rows = cur.fetchall()
    
    # 计算每个主题的平均趋势分
    theme_scores = defaultdict(list)
    for theme, score in rows:
        if score is not None and score > 0:
            theme_scores[theme].append(score)
    
    # 计算平均值
    theme_avg = {}
    for theme, scores in theme_scores.items():
        if len(scores) >= 10:  # 至少要有10天数据才有效
            avg = sum(scores) / len(scores)
            theme_avg[theme] = avg
            print(f"   {theme}: {avg:.2f} ({len(scores)}天)")
    
    conn.close()
    return theme_avg


def main_for_date(target_date, hot_themes, dc_df, stock_basic, daily_basic, theme_stock_map, name_map_basic, stock_industry, stock_concepts):
    """
    为指定日期运行分析（用于批量回溯，复用主题和成分股对应关系）
    """
    global TRADE_DATE, START_DATE
    
    # 保存原始日期
    original_date = TRADE_DATE
    
    try:
        # 设置目标日期
        TRADE_DATE = target_date
        START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
        print(f"\n{'='*60}")
        print(f"处理日期: {TRADE_DATE}")
        print(f"{'='*60}")
        
        # 获取K线数据
        all_codes = set()
        for tn, m in theme_stock_map.items():
            all_codes.update(m.keys())
        kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
        
        # 获取指数数据
        idx_df = get_index_kline("000300.SH")
        market_ret_10 = 0.0
        if idx_df is not None and not idx_df.empty:
            idx_df = idx_df.sort_values('trade_date')
            closes = idx_df['close'].astype(float).values
            if len(closes) >= 11:
                market_ret_10 = (closes[-1] / closes[-11] - 1) * 100
        
        kline_groups = {}
        if not kline_df.empty:
            for code, sub in kline_df.groupby('ts_code'):
                kline_groups[code] = sub
        
        results = []
        rows_per_theme = {}
        
        for theme_name, cfg in hot_themes.items():
            matched = theme_stock_map.get(theme_name, {})
            if not matched:
                results.append({'theme': theme_name, 'n_stocks': 0, 'trend_score': 0.0, 'sentiment_score': 0.0, 'composite_score': 0.0})
                continue
            
            mcap_dict = {}
            if not daily_basic.empty:
                mcap_dict = {r['ts_code']: r for _, r in daily_basic.iterrows()}
            
            industry_list = cfg.get('industry', [])
            concept_list = cfg.get('concept', [])
            keyword_list = cfg.get('keywords', [])
            
            rows = []
            for code, meta in matched.items():
                kdf = kline_groups.get(code)
                if kdf is None or len(kdf) < 6:
                    continue
                feat = per_stock_features(kdf)
                if feat is None:
                    continue
                
                # 合并换手率（从daily_basic获取）
                if not daily_basic.empty:
                    db_one = daily_basic[daily_basic['ts_code'] == code]
                    if not db_one.empty:
                        turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                        feat['turnover'] = float(turnover)
                
                concepts = stock_concepts.get(code, [])
                concepts_str = "|".join(concepts)
                purity = 0
                for kw in keyword_list:
                    if kw in concepts_str:
                        purity += 1
                for c in concept_list:
                    if c in concepts:
                        purity += 1
                if _in_industry_list(stock_industry.get(code, ""), industry_list):
                    purity += 1
                
                mv = mcap_dict.get(code, {}).get('total_mv', 0) or 0
                feat['ts_code'] = code
                feat['name'] = name_map_basic.get(code, code)
                feat['purity'] = purity
                feat['total_mv'] = mv
                feat['industry_match'] = meta.get('industry_match', False)
                rows.append(feat)
            
            if len(rows) < MIN_STOCKS:
                results.append({'theme': theme_name, 'n_stocks': len(rows), 'trend_score': 0.0, 'sentiment_score': 0.0, 'composite_score': 0.0})
                rows_per_theme[theme_name] = []
                continue
            
            # =========================
            # 统计全成份股的涨停数（用于情绪分计算）
            # =========================
            all_rows = rows  # 全部成份股
            all_zt_count = sum(1 for r in all_rows if r.get("zt_flag") == 1)
            all_up_count = sum(1 for r in all_rows if r.get("pct_chg", 0) > 0)
            all_down_count = sum(1 for r in all_rows if r.get("pct_chg", 0) < 0)
            all_total = len(all_rows)

            # =========================
            # 按市值权重排序，取前30只用于趋势分计算
            # =========================
            for r in rows:
                r['mcap_w'] = (r['total_mv'] / 10000) ** 0.5 * 0.8 + r['purity'] * 2
                r['mcap_w'] *= 1.0 if r['industry_match'] else 0.5
            rows.sort(key=lambda x: x['mcap_w'], reverse=True)
            top_rows = rows[:TOP_N_PER_THEME]  # 前30只用于趋势分计算
            
            t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
            s_score, s_detail = calc_sentiment_score(all_rows, market_ret_10)  # 情绪分用全成份股计算
            
            composite = round(0.55 * t_score + 0.45 * s_score, 1)
            
            leader_scores = []
            for r in top_rows:
                lb = r.get('lb_height', 0)
                pct = abs(r.get('pct_chg', 0))
                amt = r.get('amount_latest', 0)
                purity = r.get('purity', 0)
                ls = 0.4 * min(lb * 20, 100) + 0.3 * min(pct * 5, 100) + 0.2 * min(amt * 2, 100) + 0.1 * min(purity * 20, 100)
                leader_scores.append((r, ls))
            leader_scores.sort(key=lambda x: x[1], reverse=True)
            leader_stock = leader_scores[0][0] if leader_scores else None
            leader_name = leader_stock['name'] if leader_stock else ""
            leader_code = leader_stock['ts_code'] if leader_stock else ""
            
            core_candidates = [r for r in top_rows if r.get('total_mv', 0) > 2000000 and r.get('purity', 0) >= 1]
            core_scores = []
            for r in core_candidates:
                amt = r.get('amount_latest', 0)
                mv = r.get('total_mv', 0) / 10000
                pct = abs(r.get('pct_chg', 0))
                cs = 0.5 * min(amt * 2, 100) + 0.3 * min(mv / 10, 100) + 0.2 * min(pct * 5, 100)
                core_scores.append((r, cs))
            core_scores.sort(key=lambda x: x[1], reverse=True)
            core_stock = core_scores[0][0] if core_scores else None
            core_name = core_stock['name'] if core_stock else ""
            core_code = core_stock['ts_code'] if core_stock else ""
            
            results.append({
                'theme': theme_name, 'n_stocks': all_total, 'trend_score': t_score, 'sentiment_score': s_score,
                'composite_score': composite, 'trend_detail': t_detail, 'sentiment_detail': s_detail,
                'leader_name': leader_name, 'leader_code': leader_code, 'leader_score': round(leader_scores[0][1], 1) if leader_scores else 0,
                'core_name': core_name, 'core_code': core_code, 'core_score': round(core_scores[0][1], 1) if core_scores else 0,
            })
            rows_per_theme[theme_name] = top_rows
        
        results.sort(key=lambda x: x['composite_score'], reverse=True)
        for i, r in enumerate(results, 1):
            r['rank'] = i
        
        # 保存到数据库
        save_to_sqlite(results)
        print(f"[保存完成] {TRADE_DATE} 数据已保存到数据库")
        
    finally:
        # 恢复原始日期
        TRADE_DATE = original_date


def backfill_last_n_days(n_days=60):
    """
    批量回溯最近N个交易日的数据
    
    Args:
        n_days: 回溯天数
    """
    print("=" * 80)
    print(f"批量回溯最近 {n_days} 个交易日")
    print("=" * 80)
    
    # 步骤1: 获取交易日历
    end_date = datetime.strptime(TRADE_DATE, "%Y%m%d")
    start_cal_date = end_date - timedelta(days=n_days * 2)  # 多取一些天数以防节假日
    
    cal = pro.trade_cal(exchange='', start_date=start_cal_date.strftime("%Y%m%d"), end_date=TRADE_DATE)
    cal = cal[cal['is_open'] == 1]
    trade_dates = sorted(cal['cal_date'].tolist(), reverse=True)[:n_days]
    trade_dates.reverse()  # 从旧到新处理
    
    print(f"待处理的 {len(trade_dates)} 个交易日: {trade_dates[0]} 到 {trade_dates[-1]}")
    
    # 步骤2: 只执行一次主题和成分股对应关系计算
    print("\n[初始化] 计算主题和成分股对应关系（只需一次）")
    hot_themes = load_theme_json()
    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)
    
    # 步骤3: 逐个日期处理
    print(f"\n[开始处理] 共 {len(trade_dates)} 个交易日")
    for i, target_date in enumerate(trade_dates, 1):
        print(f"\n[{i}/{len(trade_dates)}] 处理 {target_date}")
        try:
            main_for_date(target_date, hot_themes, dc_df, stock_basic, daily_basic, 
                          theme_stock_map, name_map_basic, stock_industry, stock_concepts)
        except Exception as e:
            print(f"处理 {target_date} 时出错: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n[全部完成] 共处理 {len(trade_dates)} 个交易日")


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        if sys.argv[1] == "backfill":
            # 批量回溯模式
            n_days = int(sys.argv[2]) if len(sys.argv) >= 3 else 60
            backfill_last_n_days(n_days)
        else:
            # 单个日期回溯模式
            TRADE_DATE = sys.argv[1]
            START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
            print(f"[Backfill] 回溯模式: {TRADE_DATE}")
            main()
    else:
        main()
