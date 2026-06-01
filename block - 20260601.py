import os
import time
import random
import pickle
import glob
import tushare as ts
import pandas as pd
import json
from dotenv import load_dotenv
import sqlite3


from datetime import datetime, timedelta
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

import numpy as np
from collections import defaultdict

# =========================
# 参数
# =========================
LOOKBACK = 5          # 动量窗口
TOP_K = 10            # 输出主线数量

MIN_STOCKS = 10       # 板块最小股票数

MOMENTUM_W = 0.6
ACC_W = 0.4

##=========== TUshare

load_dotenv("config/.env")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")

ts.set_token(TUSHARE_TOKEN)

pro = ts.pro_api()

# ============================================
# Tushare
# ============================================


# ============================================
# 缓存目录
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")

DB_PATH = os.path.join(CACHE_DIR, "hot_sector.db")

os.makedirs(CACHE_DIR, exist_ok=True)


# =========================================================
# 文件路径
# =========================================================
CONCEPT_LIST_PATH = os.path.join(
    CACHE_DIR,
    "ths_concept_list.csv"
)

CONCEPT_DETAIL_PATH = os.path.join(
    CACHE_DIR,
    "ths_concept_detail.pkl"
)

STOCK_CONCEPT_PATH = os.path.join(
    CACHE_DIR,
    "stock_concept_map.pkl"
)

CONCEPT_STOCK_PATH = os.path.join(
    CACHE_DIR,
    "concept_stock_map.pkl"
)

# =========================================================
# 主题映射（替代概念）
# =========================================================

def load_theme_map():
    """
    加载主题配置（从 theme.json 读取）
    如果 theme.json 比缓存更新，自动清除旧缓存
    """
    theme_file = os.path.join(BASE_DIR, "theme.json")
    
    if not os.path.exists(theme_file):
        raise FileNotFoundError(f"配置不存在: {theme_file}")
    
    # 检查 theme.json 是否比缓存更新
    theme_mtime = os.path.getmtime(theme_file)
    
    old_cache_stock = os.path.join(CACHE_DIR, "stock_concept_map.pkl")
    old_cache_concept = os.path.join(CACHE_DIR, "concept_stock_map.pkl")
    
    # 旧的非日期缓存文件如果存在，说明是旧格式，需要删除
    for old_cache in [old_cache_stock, old_cache_concept]:
        if os.path.exists(old_cache):
            cache_mtime = os.path.getmtime(old_cache)
            if theme_mtime > cache_mtime:
                print(f"检测到 theme.json 已更新，清除旧缓存...")
                try:
                    os.remove(old_cache)
                except:
                    pass
                # 同时删除带日期的旧缓存
                for date_cache in glob.glob(os.path.join(CACHE_DIR, f"{os.path.basename(old_cache).split('.')[0]}_*.pkl")):
                    try:
                        os.remove(date_cache)
                    except:
                        pass

    # 读取 theme.json
    with open(theme_file, "r", encoding="utf-8") as f:
        theme_data = json.load(f)
    
    theme_map = theme_data.get("HOT_THEMES", {})
    
    print(f"主题配置加载完成，共 {len(theme_map)} 个主题")

    return theme_map


THEME_MAP = load_theme_map()


def get_last_trade_date():

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 缓存交易日历（start_date固定20200101，缓存一次永久使用）
    # =========================
    cal_cache = os.path.join(CACHE_DIR, "trade_cal.pkl")
    if os.path.exists(cal_cache):
        with open(cal_cache, "rb") as f:
            cal = pickle.load(f)
        if 'cal_date' in cal.columns and cal['cal_date'].max() >= query_date:
            cal = cal[cal['is_open'] == 1]
            last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
            return str(last_trade_date)

    # =========================
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    with open(cal_cache, "wb") as f:
        pickle.dump(cal, f)

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()

#TRADE_DATE = "20260526" # for test
print(f"板块分析日期: {TRADE_DATE}")
# =========================================================
# 下载同花顺概念列表
# =========================================================
# =========================================================
# 下载同花顺概念列表（带缓存）
# =========================================================
def download_ths_concepts():

    print("获取同花顺概念列表...")

    # ========= 缓存命中 =========
    if os.path.exists(CONCEPT_LIST_PATH):
        print(f"读取缓存: {CONCEPT_LIST_PATH}")
        return pd.read_csv(CONCEPT_LIST_PATH, encoding="utf-8-sig")

    # ========= 重新生成 =========
    df = pro.ths_index(
        exchange='A',
        type='N'
    )

    if df is None or df.empty:
        return pd.DataFrame()

    df.to_csv(
        CONCEPT_LIST_PATH,
        index=False,
        encoding='utf-8-sig'
    )

    print(f"概念列表已保存: {CONCEPT_LIST_PATH}")

    return df


# =========================================================
# 下载概念成分股（带缓存）
# =========================================================
def download_ths_members(concept_df):

    # ========= 缓存命中 =========
    if os.path.exists(CONCEPT_DETAIL_PATH):
        print(f"读取缓存: {CONCEPT_DETAIL_PATH}")

        with open(CONCEPT_DETAIL_PATH, "rb") as f:
            return pickle.load(f)

    # ========= 重新生成 =========
    all_rows = []
    total = len(concept_df)

    for i, row in concept_df.iterrows():

        ts_code = row["ts_code"]
        name = row["name"]

        print(f"[{i+1}/{total}] 下载: {name}")

        try:
            df = pro.ths_member(ts_code=ts_code)

            if df is None or df.empty:
                continue

            df["concept_name"] = name
            all_rows.append(df)

            time.sleep(0.25)

        except Exception as e:
            print(f"失败: {name} {e}")

    if not all_rows:
        return pd.DataFrame()

    result = pd.concat(all_rows, ignore_index=True)

    # ========= 写缓存 =========
    with open(CONCEPT_DETAIL_PATH, "wb") as f:
        pickle.dump(result, f)

    print(f"概念成分股已保存: {CONCEPT_DETAIL_PATH}")

    return result

# =========================================================
# 构建 股票 -> 概念
# =========================================================
# 构建 股票 -> 概念（带缓存，按天更新）
# =========================================================
def build_stock_concept_map(member_df):
    # 缓存文件名加上日期，按天更新
    cache_file = os.path.join(CACHE_DIR, f"stock_concept_map_{TRADE_DATE}.pkl")

    # ========= 缓存命中（当天） =========
    if os.path.exists(cache_file):
        print(f"读取当日缓存: {cache_file}")
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    # ========= 重新生成 =========
    stock_map = defaultdict(list)

    for _, row in member_df.iterrows():
        ts_code = row["con_code"]
        concept = row["concept_name"]
        stock_map[ts_code].append(concept)

    stock_map = {
        k: ";".join(sorted(set(v)))
        for k, v in stock_map.items()
    }

    # ========= 写缓存 =========
    with open(cache_file, "wb") as f:
        pickle.dump(stock_map, f)

    print(f"股票概念映射已保存: {cache_file}")

    return stock_map

# =========================================================
# 构建 概念 -> 股票
# =========================================================
# =========================================================
# 构建 概念 -> 股票（带缓存）
# =========================================================
# 构建 概念 -> 股票（带缓存，按天更新）
# =========================================================
def build_concept_stock_map(member_df):
    # 缓存文件名加上日期，按天更新
    cache_file = os.path.join(CACHE_DIR, f"concept_stock_map_{TRADE_DATE}.pkl")

    # ========= 缓存命中（当天） =========
    if os.path.exists(cache_file):
        print(f"读取当日缓存: {cache_file}")
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    # ========= 重新生成 =========
    concept_map = defaultdict(list)

    for _, row in member_df.iterrows():
        ts_code = row["ts_code"]
        concept = row["concept_name"]
        concept_map[concept].append(ts_code)

    concept_map = {
        k: sorted(set(v))
        for k, v in concept_map.items()
    }

    # ========= 写缓存 =========
    with open(cache_file, "wb") as f:
        pickle.dump(concept_map, f)

    print(f"概念股票映射已保存: {cache_file}")

    return concept_map


# =========================================================
# 读取股票概念缓存
# =========================================================
def load_stock_concept_map():
    cache_file = os.path.join(CACHE_DIR, f"stock_concept_map_{TRADE_DATE}.pkl")
    with open(cache_file, "rb") as f:
        return pickle.load(f)


# =========================================================
# 读取概念股票缓存
# =========================================================
def load_concept_stock_map():
    cache_file = os.path.join(CACHE_DIR, f"concept_stock_map_{TRADE_DATE}.pkl")
    with open(cache_file, "rb") as f:

        return pickle.load(f)



# =========================================================
# 初始化概念缓存
# =========================================================
def init_concept_cache():

    concept_df = download_ths_concepts()

    member_df = download_ths_members(concept_df)

    stock_map = build_stock_concept_map(member_df)

    concept_map = build_concept_stock_map(member_df)

    print("概念缓存初始化完成")

    return stock_map, concept_map



# =========================================================
# 生成 concept dataframe
# =========================================================
def build_concept_df(stock_map):

    rows = []

    for ts_code, concept in stock_map.items():

        rows.append({

            "ts_code": ts_code,

            "concept": concept
        })

    return pd.DataFrame(rows)



# =========================================================
# 日线数据
# =========================================================
def get_daily_df():

    print("读取全市场行情...")

    # ========= 缓存文件 =========
    cache_file = os.path.join(
        CACHE_DIR,
        f"daily_{TRADE_DATE}.csv"
    )

    # ========= 优先读取缓存 =========
    if os.path.exists(cache_file):

        print(f"读取缓存: {cache_file}")

        df = pd.read_csv(
            cache_file,
            dtype={
                'ts_code': str
            }
        )

        return df

    print("缓存不存在，开始从Tushare下载...")

    # ========= 下载数据 =========
    df = pro.daily(
        trade_date=TRADE_DATE
    )

    if df.empty:

        return pd.DataFrame()

    # ========= 成交额转亿 =========
    # tushare amount单位为千元
    # 亿元 = 千元 / 100000
    df['amount'] = (
        df['amount'] / 100000
    )

    # ========= 保存缓存 =========
    df.to_csv(
        cache_file,
        index=False,
        encoding='utf-8-sig'
    )

    print(f"缓存已保存: {cache_file}")

    return df

# =========================================================
# 申万行业（L2/L3）
# =========================================================
def get_sw_industry_map():

    cache_file = os.path.join(CACHE_DIR, "sw_map.csv")

    if os.path.exists(cache_file):

        df = pd.read_csv(cache_file, dtype=str)

        if not df.empty:
            return df

    df = pro.index_member_all(is_new='Y')

    df.to_csv(cache_file, index=False)

    return df

# =========================================================
# 缓存 limit_list_ths 数据
# =========================================================
def get_limit_list_ths(trade_date, limit_type):
    cache_file = os.path.join(CACHE_DIR, f"limit_list_ths_{trade_date}_{limit_type}.pkl")
    
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    
    try:
        df = pro.limit_list_ths(trade_date=trade_date, limit_type=limit_type)
        with open(cache_file, "wb") as f:
            pickle.dump(df, f)
        return df
    except Exception:
        return pd.DataFrame()

# =========================================================
# 缓存 limit_step 数据
# =========================================================
def get_limit_step(trade_date):
    cache_file = os.path.join(CACHE_DIR, f"limit_step_{trade_date}.pkl")
    
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    
    try:
        df = pro.limit_step(trade_date=trade_date)
        with open(cache_file, "wb") as f:
            pickle.dump(df, f)
        return df
    except Exception:
        return pd.DataFrame()

# =========================================================
# 龙头高度因子（连板高度、涨停占比、封板强度）
# =========================================================

# 内存缓存
_lb_height_cache = {}
_leader_factor_cache = {}

def get_stock_lb_height(ts_code):
    """
    获取单只股票的连板高度（带内存缓存）
    """
    cache_key = (str(ts_code), TRADE_DATE)
    if cache_key in _lb_height_cache:
        return _lb_height_cache[cache_key]
    
    lb_df = get_limit_step(TRADE_DATE)
    result = 1
    if lb_df is not None and not lb_df.empty:
        lb_df['ts_code'] = lb_df['ts_code'].astype(str)
        ts_code_str = str(ts_code)
        stock_lb = lb_df[lb_df['ts_code'] == ts_code_str]
        if not stock_lb.empty and 'nums' in stock_lb.columns:
            result = int(stock_lb['nums'].fillna(1).iloc[0])
    
    _lb_height_cache[cache_key] = result
    return result

def calc_leader_height_factor(sector_codes):
    """
    龙头高度因子：衡量板块内涨停股的连板高度、封板强度
    用 limit_list_ths 涨停池接口获取精确涨停数据
    返回：(高度分, 涨停占比%, 板块连板最高板)（带内存缓存）
    """
    # 使用排序后的代码作为缓存键，确保相同集合有相同键
    sorted_codes = sorted(str(c) for c in sector_codes)
    cache_key = (tuple(sorted_codes), TRADE_DATE)
    if cache_key in _leader_factor_cache:
        return _leader_factor_cache[cache_key]
    
    if not sector_codes:
        result = (0, 0, 0)
        _leader_factor_cache[cache_key] = result
        return result

    zt_df = get_limit_list_ths(TRADE_DATE, '涨停池')

    if zt_df is None or zt_df.empty:
        result = (0, 0, 0)
        _leader_factor_cache[cache_key] = result
        return result

    zt_df['ts_code'] = zt_df['ts_code'].astype(str)
    sector_codes_set = set(str(c) for c in sector_codes)

    # 筛选属于该板块的涨停股
    sector_zt = zt_df[zt_df['ts_code'].isin(sector_codes_set)]

    if sector_zt.empty:
        result = (0, 0, 0)
        _leader_factor_cache[cache_key] = result
        return result

    zt_count = len(sector_zt)
    total_count = len(sector_codes_set)
    zt_ratio = zt_count / max(total_count, 1)

    # 连板高度（核心）
    max_lb = 1
    total_lb_score = 0

    lb_df = get_limit_step(TRADE_DATE)
    if lb_df is not None and not lb_df.empty:
        lb_df['ts_code'] = lb_df['ts_code'].astype(str)
        sector_lb = lb_df[lb_df['ts_code'].isin(sector_codes_set)]
        if not sector_lb.empty and 'nums' in sector_lb.columns:
            nums = sector_lb['nums'].fillna(1).astype(int)
            max_lb = nums.max()
            for n in nums:
                if n >= 2:
                    total_lb_score += n * n

    # 龙头高度评分（非线性递增）
    if max_lb >= 7:
        height_score = 50
    elif max_lb >= 5:
        height_score = 35
    elif max_lb >= 4:
        height_score = 25
    elif max_lb >= 3:
        height_score = 18
    elif max_lb >= 2:
        height_score = 10
    else:
        height_score = 3

    ratio_score = min(zt_ratio * 100, 30)
    lb_total_score = min(total_lb_score, 40)
    leader_height = height_score + ratio_score + lb_total_score

    result = (round(leader_height, 2), round(zt_ratio * 100, 1), int(max_lb))
    _leader_factor_cache[cache_key] = result
    return result

def calc_sector_score(df, sector_codes=None):

    if df is None or len(df) == 0:
        return 0

    pct = df["pct_chg"].dropna()

    amount = df["amount"].fillna(0)

    n = len(df)

    # =====================================================
    # 1. 去极值动量
    # =====================================================
    pct_sorted = pct.sort_values()

    left = int(n * 0.1)
    right = int(n * 0.9)

    trimmed = pct_sorted.iloc[left:right]

    momentum = trimmed.mean()

    # =====================================================
    # 2. 龙头强度
    # =====================================================
    top1 = pct.max()

    top3 = pct.nlargest(min(3, n)).mean()

    leader_strength = (
        top1 * 2
        + top3 * 1.5
    )

    # =====================================================
    # 3. 扩散强度
    # =====================================================
    strong_cnt = (pct >= 5).sum()

    limit_up = (pct >= 9.5).sum()

    spread_ratio = strong_cnt / n

    spread_strength = (
        limit_up * 8
        + spread_ratio * 30
    )

    # =====================================================
    # 4. 情绪结构
    # =====================================================
    high_cnt = (pct >= 7).sum()

    mid_cnt = (
        (pct >= 3)
        & (pct < 7)
    ).sum()

    weak_cnt = (pct < 0).sum()

    emotion_strength = (
        high_cnt * 3
        + mid_cnt * 1.5
        - weak_cnt * 1.2
    )

    # =====================================================
    # 5. 资金结构
    # =====================================================
    total_amount = amount.sum()

    top5_ratio = (
        amount.nlargest(
            min(5, n)
        ).sum()
        / max(total_amount, 1)
    )

    money_spread = 1 - top5_ratio

    capital_strength = (
        total_amount / 100
        + money_spread * 20
    )

    # =====================================================
    # 6. 一致性
    # =====================================================
    consistency = max(
        0,
        10 - pct.std()
    )

    # =====================================================
    # 7. 龙头高度因子
    # =====================================================
    leader_height = 0
    if sector_codes:
        leader_height, _, _ = calc_leader_height_factor(sector_codes)

    # =====================================================
    # 综合评分（含龙头高度）
    # =====================================================
    score = (

        momentum * 1.5

        + leader_strength * 1.8

        + spread_strength * 1.5

        + emotion_strength * 1.2

        + capital_strength * 0.8

        + consistency * 2

        + leader_height * 2.0
    )

    return round(score, 2)

def calc_sector_score1(df):

    if df is None or len(df) == 0:
        return 0

    pct = df["pct_chg"]

    # =========================
    # 1. 基础动量
    # =========================
    momentum = pct.mean()

    # =========================
    # 2. 涨停强度
    # =========================
    limit_up = (pct >= 9.5).sum()

    # =========================
    # 3. 赚钱效应
    # =========================
    up_ratio = (pct > 0).mean()

    median_chg = pct.median()

    # =========================
    # 4. 资金强度
    # =========================
    money = df["amount"].sum() / 1e8

    # =========================
    # 5. 资金集中度（抱团）
    # =========================
    try:
        top5_ratio = (
            df.sort_values("amount", ascending=False)
              .head(5)["amount"].sum()
            / df["amount"].sum()
        )
    except:
        top5_ratio = 0

    # =========================
    # 6. 风险抑制
    # =========================
    limit_down = (pct <= -9.5).sum()

    # =========================
    # 综合评分（机构权重）
    # =========================
    score = (

        momentum * 1.2
        + limit_up * 6
        + up_ratio * 5
        + median_chg * 1.5
        + money * 0.8
        + top5_ratio * 8
        - limit_down * 10
    )

    return score

# =========================================================
# 龙头识别（V6 优化版）
# =========================================================

# 内存缓存：股票历史连板信息
_stock_lb_history_cache = {}

def get_stock_max_lb_history(ts_code):
    """
    获取股票历史最高连板高度（从过去10个交易日的limit_step数据）
    """
    cache_key = str(ts_code)
    if cache_key in _stock_lb_history_cache:
        return _stock_lb_history_cache[cache_key]
    
    max_lb = 1
    try:
        # 获取当前交易日之前的历史数据（最多回溯10个交易日）
        from datetime import datetime, timedelta
        current_date = datetime.strptime(TRADE_DATE, "%Y%m%d")
        
        # 回溯最多10个交易日
        for i in range(1, 11):
            check_date = (current_date - timedelta(days=i)).strftime("%Y%m%d")
            lb_df = get_limit_step(check_date)
            if lb_df is not None and not lb_df.empty:
                lb_df['ts_code'] = lb_df['ts_code'].astype(str)
                stock_lb = lb_df[lb_df['ts_code'] == str(ts_code)]
                if not stock_lb.empty and 'nums' in stock_lb.columns:
                    lb = int(stock_lb['nums'].fillna(1).iloc[0])
                    if lb > max_lb:
                        max_lb = lb
    except Exception as e:
        pass
    
    _stock_lb_history_cache[cache_key] = max_lb
    return max_lb

def calc_stock_strength(stock_df):
    """
    计算股票强度评分（V6.1优化版）
    综合考虑：历史连板高度、当前连板状态、成交额、近期涨幅
    """
    ts_code = stock_df["ts_code"].iloc[0]
    
    # 1. 历史最高连板高度权重（最重要，游资看历史地位）
    history_max_lb = get_stock_max_lb_history(ts_code)
    history_lb_score = 0
    if history_max_lb >= 6:
        history_lb_score = 400  # 6板及以上：超级龙头
    elif history_max_lb >= 5:
        history_lb_score = 300  # 5板：强龙头
    elif history_max_lb >= 4:
        history_lb_score = 220  # 4板：龙头
    elif history_max_lb >= 3:
        history_lb_score = 140  # 3板：小龙头
    elif history_max_lb >= 2:
        history_lb_score = 60   # 2板：有潜力
    
    # 2. 当前连板状态
    current_lb = get_stock_lb_height(ts_code)
    current_lb_score = 0
    if current_lb >= 5:
        current_lb_score = 250  # 当前5板：市场焦点
    elif current_lb >= 4:
        current_lb_score = 180  # 当前4板
    elif current_lb >= 3:
        current_lb_score = 120  # 当前3板
    elif current_lb >= 2:
        current_lb_score = 60   # 当前2板
    
    # 3. 成交额权重（近5日平均，反映资金关注度）
    recent_amount = stock_df["amount"].tail(5).mean() / 1e8
    amount_score = 0
    if recent_amount >= 20:
        amount_score = 100  # 20亿+：绝对焦点
    elif recent_amount >= 10:
        amount_score = 80   # 10-20亿：高关注度
    elif recent_amount >= 5:
        amount_score = 60    # 5-10亿：中等关注
    elif recent_amount >= 2:
        amount_score = 40    # 2-5亿：有资金
    
    # 4. 近期涨幅（近5日累计，反映趋势强度）
    pct_score = 0
    if len(stock_df) >= 5:
        recent_pct = (stock_df["close"].iloc[-1] / stock_df["close"].iloc[-5] - 1) * 100
        if recent_pct >= 50:
            pct_score = 80  # 50%+：超强趋势
        elif recent_pct >= 30:
            pct_score = 60  # 30-50%：强趋势
        elif recent_pct >= 20:
            pct_score = 40  # 20-30%：不错趋势
    
    # 5. 今日涨跌幅
    today_pct = stock_df["pct_chg"].iloc[-1] if not pd.isna(stock_df["pct_chg"].iloc[-1]) else 0
    today_pct_score = max(today_pct * 3, 0)
    
    # 6. 是否是当前涨停（额外加分）
    is_zt = False
    try:
        zt_df = get_limit_list_ths(TRADE_DATE, '涨停池')
        if zt_df is not None and not zt_df.empty:
            zt_codes = set(zt_df['ts_code'].astype(str).tolist())
            is_zt = str(ts_code) in zt_codes
    except:
        pass
    
    zt_bonus = 100 if is_zt else 0
    
    total_score = (
        history_lb_score +
        current_lb_score +
        amount_score +
        pct_score +
        today_pct_score +
        zt_bonus
    )
    
    return total_score

def get_stock_name_map():

    cache_file = os.path.join(CACHE_DIR, "name_map.csv")

    if os.path.exists(cache_file):

        df = pd.read_csv(cache_file, dtype=str)

        if not df.empty:
            return df

    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,name'
    )

    df.to_csv(cache_file, index=False, encoding='utf-8-sig')

    return df


def find_leader(sector_df):
    """
    寻找板块龙头：优先选择涨停的股票，再根据强度评分选择
    """
    best_code = None
    best_name = None
    best_score = -1
    is_zt_best = False

    # 获取涨停股票列表
    zt_df = get_limit_list_ths(TRADE_DATE, '涨停池')
    zt_codes = set()
    if zt_df is not None and not zt_df.empty:
        zt_codes = set(zt_df['ts_code'].astype(str).tolist())

    for ts_code, g in sector_df.groupby("ts_code"):
        ts_code_str = str(ts_code)
        score = calc_stock_strength(g)
        
        # 如果是涨停股票，给予额外加分
        if ts_code_str in zt_codes:
            score += 100  # 涨停股票优先
        
        if score > best_score:
            best_score = score
            best_code = ts_code
            row = g.iloc[-1]
            best_name = row["name"] if "name" in row else ts_code
            is_zt_best = ts_code_str in zt_codes

    return best_code, best_name, best_score


# =========================================================
# V4/V5 状态缓存
# =========================================================
sector_state = defaultdict(lambda: {

    "history": [],
    "momentum": 0,
    "acc": 0,
    "leader": None
})


def init_sector_state(days=10):
    """从数据库加载历史评分数据，初始化 sector_state"""
    global sector_state
    
    try:
        conn = sqlite3.connect(DB_PATH)
        
        query = """
            SELECT date, name, score
            FROM hot_sector
            ORDER BY date DESC
            LIMIT ?
        """
        
        df = pd.read_sql(query, conn, params=(days * 20,))
        conn.close()
        
        if len(df) == 0:
            print("[缓存] 数据库无历史数据")
            return
        
        df = df.sort_values(["name", "date"])
        
        for name, group in df.groupby("name"):
            history = group["score"].tolist()[-10:]
            sector_state[name]["history"] = history
            
            n = len(history)
            if n >= 2:
                if n >= 3:
                    sector_state[name]["momentum"] = history[-1] - history[-3]
                else:
                    sector_state[name]["momentum"] = history[-1] - history[-2]
            else:
                sector_state[name]["momentum"] = 0
            
            if n >= 3:
                sector_state[name]["acc"] = (history[-1] - history[-2]) - (history[-2] - history[-3])
            else:
                sector_state[name]["acc"] = 0
        
        loaded_count = len([k for k, v in sector_state.items() if len(v["history"]) > 0])
        print(f"[缓存] 已加载 {loaded_count} 个板块的历史数据")
        
    except Exception as e:
        print(f"[缓存] 加载历史数据失败: {e}")


# =========================================================
# 更新主线状态（V5）
# =========================================================
def update_state(name, score):

    state = sector_state[name]

    state["history"].append(score)

    if len(state["history"]) > 10:
        state["history"].pop(0)

    history = state["history"]
    n = len(history)

    if n >= 2:
        if n >= 3:
            state["momentum"] = history[-1] - history[-3]
        else:
            state["momentum"] = history[-1] - history[-2]
    else:
        state["momentum"] = 0

    if n >= 3:
        state["acc"] = (history[-1] - history[-2]) - (history[-2] - history[-3])
    else:
        state["acc"] = 0

    return state


# =========================================================
# 主线强度（V5核心）
# =========================================================
def calc_strength(score, state):

    return (

        score
        + MOMENTUM_W * state["momentum"]
        + ACC_W * state["acc"]
    )


# =========================================================
# 退潮判断
# =========================================================
def is_decline(state):

    h = state["history"]

    if len(h) < 3:
        return False

    return h[-1] < h[-2] < h[-3]


# =========================================================
# 行业分析（V4核心）
# =========================================================
def analyze_industry(daily_df, industry_df):

    result = []

    for level in ["l1_name", "l2_name", "l3_name"]:

        if level not in industry_df.columns:
            continue

        for name, g in industry_df.groupby(level):

            stocks = g["ts_code"].dropna().unique().tolist()

            if len(stocks) < MIN_STOCKS:
                continue

            df = daily_df[daily_df["ts_code"].isin(stocks)]

            if df.empty:
                continue

            leader_height, zt_ratio, _ = calc_leader_height_factor(stocks)
            score = calc_sector_score(df, stocks)

            state = update_state(name, score)

            strength = calc_strength(score, state)

            leader_code, leader_name, leader_score = find_leader(df)
            state["leader"] = leader_code
            
            # 获取龙头的连板高度（而不是板块最大连板高度）
            leader_lb_height = get_stock_lb_height(leader_code)

            result.append({

                "类型": level,
                "主线": name,
                "评分": score,
                "主线强度": strength,
                "动量": state["momentum"],
                "加速度": state["acc"],
                "龙头代码": leader_code,
                "龙头名称": leader_name,
                "龙头强度": leader_score,
            "龙头高度": leader_height,
            "涨停占比": zt_ratio,
            "连板高度": leader_lb_height,
                "是否退潮": is_decline(state),
                "成分股数": len(stocks)                
            })

    return result


# =========================================================
# 概念板块分析（直接分析同花顺概念）
# =========================================================
def analyze_concepts(daily_df):
    
    result = []
    
    if not os.path.exists(CONCEPT_DETAIL_PATH):
        print("[概念分析] 概念成分股数据不存在")
        return result
    
    with open(CONCEPT_DETAIL_PATH, "rb") as f:
        member_df = pickle.load(f)
    
    daily_codes = set(daily_df["ts_code"].unique())
    
    concept_to_stocks = defaultdict(list)
    
    for _, row in member_df.iterrows():
        stock_code = row.get("con_code", "")
        concept_name = row.get("concept_name", "")
        
        if not stock_code or not concept_name:
            continue
        
        if stock_code in daily_codes:
            concept_to_stocks[concept_name].append(stock_code)
    
    print(f"[概念分析] 有效概念总数: {len(concept_to_stocks)}")
    
    for concept_name, stocks in concept_to_stocks.items():
        
        if len(stocks) < MIN_STOCKS:
            continue
        
        df = daily_df[daily_df["ts_code"].isin(stocks)]
        
        if df.empty:
            continue
        
        leader_height, zt_ratio, _ = calc_leader_height_factor(stocks)
        score = calc_sector_score(df, stocks)
        
        state = update_state(concept_name, score)
        
        strength = calc_strength(score, state)
        
        leader_code, leader_name, leader_score = find_leader(df)
        
        # 获取龙头的连板高度（而不是板块最大连板高度）
        leader_lb_height = get_stock_lb_height(leader_code)
        
        result.append({
            "类型": "概念",
            "主线": concept_name,
            "评分": score,
            "主线强度": strength,
            "动量": state["momentum"],
            "加速度": state["acc"],
            "龙头代码": leader_code,
            "龙头名称": leader_name,
            "龙头强度": leader_score,
            "龙头高度": leader_height,
            "涨停占比": zt_ratio,
            "连板高度": leader_lb_height,
            "是否退潮": is_decline(state),
            "成分股数": len(stocks)
        })
    
    print(f"概念板块分析完成，共 {len(result)} 个概念")
    return result


# =========================================================
# 主题分析（替代概念）- 使用 theme_portfolio_strategy_cached.py 的准确匹配算法
# =========================================================
def analyze_themes(daily_df, industry_df, stock_concept_list):
    """
    分析主题板块强度
    匹配逻辑来自 theme_portfolio_strategy_cached.py 的 build_theme_portfolio
    规则：
    1. 行业匹配：SW L1/L2/L3 在 industry_list 中
    2. 概念匹配：stock_concept 精确匹配 concept_list（非 keywords）
    3. 行业匹配 OR 概念匹配 = 成份股
    4. keywords 仅用于评分排序，exclude_keywords 仅用于过滤
    """
    result = []

    # 预构建股票名称字典（加速exclude过滤）
    stock_name_dict = dict(zip(daily_df["ts_code"], daily_df["name"]))

    for theme, cfg in THEME_MAP.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        exclude_keywords = cfg.get("exclude_keywords", [])

        # ── 行业匹配（SW L1/L2/L3，保留行业df的准确映射）──
        industry_mask = industry_df.apply(
            lambda x, ind_list=industry_list: (
                (x.get("l1_name") in ind_list) or
                (x.get("l2_name") in ind_list) or
                (x.get("l3_name") in ind_list)
            ),
            axis=1
        )
        industry_stocks = set(industry_df.loc[industry_mask, "ts_code"].dropna().unique())

        # ── 概念匹配（精确匹配概念名称）──
        concept_stocks = set()
        for ts_code, concepts in stock_concept_list.items():
            for c in concept_list:
                if c in concepts:
                    concept_stocks.add(ts_code)
                    break

        stocks = list(industry_stocks | concept_stocks)

        if len(stocks) < MIN_STOCKS:
            continue

        # ── exclude_keywords过滤（与theme_portfolio策略一致）──
        if exclude_keywords:
            filtered = []
            for ts_code in stocks:
                stock_name = stock_name_dict.get(ts_code, "")
                concept_str = ";".join(stock_concept_list.get(ts_code, []))
                skip = False
                for ek in exclude_keywords:
                    if ek in concept_str or ek in stock_name:
                        skip = True
                        break
                if not skip:
                    filtered.append(ts_code)
            stocks = filtered

        if len(stocks) < MIN_STOCKS:
            continue

        df = daily_df[daily_df["ts_code"].isin(stocks)]

        if df.empty:
            continue

        leader_height, zt_ratio, _ = calc_leader_height_factor(stocks)
        score = calc_sector_score(df, stocks)

        state = update_state(theme, score)

        # 规模归一化
        size_scale = min((80 / max(len(stocks), 5)) ** 0.6, 2.5)
        strength = calc_strength(score, state) * size_scale

        leader_code, leader_name, leader_score = find_leader(df)
        leader_lb_height = get_stock_lb_height(leader_code)

        result.append({
            "类型": "主题",
            "主线": theme,
            "评分": score,
            "主线强度": strength,
            "动量": state["momentum"],
            "加速度": state["acc"],
            "龙头代码": leader_code,
            "龙头名称": leader_name,
            "龙头强度": leader_score,
            "龙头高度": leader_height,
            "涨停占比": zt_ratio,
            "连板高度": leader_lb_height,
            "是否退潮": is_decline(state),
            "成分股数": len(stocks)
        })

    return result





##==========缓存代码
def init_db():

    os.makedirs("cache", exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""

        CREATE TABLE IF NOT EXISTS hot_sector (

            date TEXT,
            rank INTEGER,
            type TEXT,
            name TEXT,
            score REAL,
            leader_code TEXT,
            leader_name TEXT,
            leader_score REAL,
            momentum REAL,
            acc REAL,
            retreat INTEGER DEFAULT 0
        )

    """)

    conn.commit()

    conn.close()

    # 兼容旧表：添加retreat列（若已存在则忽略）
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("ALTER TABLE hot_sector ADD COLUMN retreat INTEGER DEFAULT 0")
        conn.commit()
        conn.close()
    except:
        pass

def save_top20(df):

    conn = sqlite3.connect(DB_PATH)

    today = TRADE_DATE

    top20 = df.head(20).copy()

    # 清理当天旧数据（避免重复）
    conn.execute(
        "DELETE FROM hot_sector WHERE date=?",
        (today,)
    )

    for i, row in enumerate(top20.itertuples()):

        conn.execute("""

            INSERT INTO hot_sector
            (date, rank, type, name, score, leader_code,leader_name, leader_score, momentum, acc, retreat)

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        """, (

            today,
            i + 1,
            getattr(row, "类型", ""),
            getattr(row, "主线", ""),
            getattr(row, "主线强度", 0),
            getattr(row, "龙头代码", ""),
            getattr(row, "龙头名称", ""),
            getattr(row, "龙头强度", 0),
            getattr(row, "动量", 0),
            getattr(row, "加速度", 0),
            1 if getattr(row, "是否退潮", False) else 0

        ))

    conn.commit()
    conn.close()

import pandas as pd

def load_history(days=10):

    conn = sqlite3.connect(DB_PATH)

    query = """

        SELECT *
        FROM hot_sector
        ORDER BY date DESC, rank ASC

    """

    df = pd.read_sql(query, conn)

    conn.close()

    return df


# =========================================================
# 主函数（V4 + V5融合）
# =========================================================
def analyze_hot_sectors():

    print("\n=== 主线系统 V4 + V5 ===\n")

    init_sector_state(days=10)

    daily_df = get_daily_df()

    name_map = get_stock_name_map()
    
    daily_df = daily_df.merge(
        name_map,
        on="ts_code",
        how="left"
)

    # 第一次运行执行
    stock_map, concept_map = init_concept_cache()

    # -------------------------------------------------
    # 读取缓存
    # -------------------------------------------------
    stock_map = load_stock_concept_map()

    # concept dataframe
    concept_df = build_concept_df(stock_map)

    industry_df = get_sw_industry_map()

    # -------------------------------------------------
    # 合并进行业表
    # -------------------------------------------------
    industry_df = industry_df.merge(
         concept_df,
         on="ts_code",
         how="left"
    )

    industry_res = analyze_industry(daily_df, industry_df)

    # 构建主题概念匹配数据（精确匹配概念名称，非关键词）
    stock_concept_list = {k: v.split(";") for k, v in stock_map.items()}

    theme_res = analyze_themes(daily_df, industry_df, stock_concept_list)
    
    concept_res = analyze_concepts(daily_df)

    all_res = industry_res + theme_res + concept_res

    print(f"行业{len(industry_res)} + 主题{len(theme_res)} + 概念{len(concept_res)}")

    # 打印主题排名
    theme_sorted = sorted(theme_res, key=lambda x: x.get("主线强度", 0), reverse=True)
    print("主题板块强度排名:")
    for t in theme_sorted[:5]:
        print(f"  {t['主线']:16s} 强度={t['主线强度']:.1f} 评分={t['评分']:.1f} 成分股={t['成分股数']}")

    if not all_res:
        return pd.DataFrame()

    df = pd.DataFrame(all_res)

    df = df.sort_values(
        "主线强度",
        ascending=False
    )

    df.reset_index(drop=True, inplace=True)

    init_db()
    save_top20(df)

    return df

# =========================================================
# 运行
# =========================================================
if __name__ == "__main__":


    df = analyze_hot_sectors()
    
    print(df.head(20))




    # -------------------------------------------------
    # 合并进行业表
    # -------------------------------------------------
    # industry_df = industry_df.merge(
    #     concept_df,
    #     on="ts_code",
    #     how="left"
    # )

    

