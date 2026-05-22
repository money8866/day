import os
import time
import random
import pickle
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

load_dotenv()

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

    file_path = os.path.join(
        BASE_DIR,
        "theme_map.json"
    )

    if not os.path.exists(file_path):

        raise FileNotFoundError(
            f"配置不存在: {file_path}"
        )

    with open(file_path, "r", encoding="utf-8") as f:

        theme_map = json.load(f)

    print("主题配置加载完成")

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
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()

#TRADE_DATE = "20260520" # for test

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
# =========================================================
# 构建 股票 -> 概念（带缓存）
# =========================================================
def build_stock_concept_map(member_df):

    # ========= 缓存命中 =========
    if os.path.exists(STOCK_CONCEPT_PATH):
        print(f"读取缓存: {STOCK_CONCEPT_PATH}")

        with open(STOCK_CONCEPT_PATH, "rb") as f:
            return pickle.load(f)

    # ========= 重新生成 =========
    stock_map = defaultdict(list)

    for _, row in member_df.iterrows():

        ts_code = row["ts_code"]
        concept = row["concept_name"]

        stock_map[ts_code].append(concept)

    stock_map = {
        k: ";".join(sorted(set(v)))
        for k, v in stock_map.items()
    }

    # ========= 写缓存 =========
    with open(STOCK_CONCEPT_PATH, "wb") as f:
        pickle.dump(stock_map, f)

    print(f"股票概念映射已保存: {STOCK_CONCEPT_PATH}")

    return stock_map

# =========================================================
# 构建 概念 -> 股票
# =========================================================
# =========================================================
# 构建 概念 -> 股票（带缓存）
# =========================================================
def build_concept_stock_map(member_df):

    # ========= 缓存命中 =========
    if os.path.exists(CONCEPT_STOCK_PATH):
        print(f"读取缓存: {CONCEPT_STOCK_PATH}")

        with open(CONCEPT_STOCK_PATH, "rb") as f:
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
    with open(CONCEPT_STOCK_PATH, "wb") as f:
        pickle.dump(concept_map, f)

    print(f"概念股票映射已保存: {CONCEPT_STOCK_PATH}")

    return concept_map


# =========================================================
# 读取股票概念缓存
# =========================================================
def load_stock_concept_map():

    with open(STOCK_CONCEPT_PATH, "rb") as f:

        return pickle.load(f)


# =========================================================
# 读取概念股票缓存
# =========================================================
def load_concept_stock_map():

    with open(CONCEPT_STOCK_PATH, "rb") as f:

        return pickle.load(f)

# =========================================================
# 主线分析（行业 + 概念关键词）
# =========================================================
def analyze_themes1(

    daily_df,

    industry_df,

    min_stocks=5
):

    result = []

    for theme, cfg in THEME_MAP.items():

        # -------------------------------------------------
        # 行业匹配
        # -------------------------------------------------
        industry_mask = industry_df.apply(

            lambda x:

                (x.get("l2_name") in cfg["industry"])

                or

                (x.get("l3_name") in cfg["industry"]),

            axis=1
        )

        # -------------------------------------------------
        # 概念关键词匹配
        # -------------------------------------------------
        keyword_mask = industry_df.apply(

            lambda x:

                any(

                    kw in str(x.get("concept", ""))

                    for kw in cfg["keywords"]
                ),

            axis=1
        )

        # -------------------------------------------------
        # 双融合
        # -------------------------------------------------
        mask = industry_mask | keyword_mask

        sub = industry_df[mask]

        stocks = sub["ts_code"].dropna().unique().tolist()

        if len(stocks) < min_stocks:
            continue

        df = daily_df[
            daily_df["ts_code"].isin(stocks)
        ]

        if df.empty:
            continue

        # -------------------------------------------------
        # 板块评分
        # -------------------------------------------------
        score = calc_sector_score(df)

        # 龙头
        leader = (
            df.sort_values(
                "pct_chg",
                ascending=False
            )
            .iloc[0]
        )

        result.append({

            "主线": theme,

            "评分": round(score, 2),

            "成分股数": len(stocks),

            "龙头代码": leader["ts_code"],

            "龙头涨幅": round(
                leader["pct_chg"],
                2
            )
        })

    result = pd.DataFrame(result)

    if not result.empty:

        result = result.sort_values(
            "评分",
            ascending=False
        )

    return result


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

def calc_sector_score(df):

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
# 龙头识别（V5）
# =========================================================
def calc_stock_strength(stock_df):

    return (

        stock_df["pct_chg"].iloc[-1] * 2
        + stock_df["amount"].sum() / 1e8
    )

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

    best_code = None
    best_name = None
    best_score = -1

    for ts_code, g in sector_df.groupby("ts_code"):

        score = calc_stock_strength(g)

        if score > best_score:

            best_score = score
            best_code = ts_code

            row = g.iloc[-1]

            best_name = row["name"] if "name" in row else ts_code

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


# =========================================================
# 更新主线状态（V5）
# =========================================================
def update_state(name, score):

    state = sector_state[name]

    state["history"].append(score)

    if len(state["history"]) > 10:
        state["history"].pop(0)

    if len(state["history"]) >= 3:

        state["momentum"] = (
            state["history"][-1]
            - state["history"][-3]
        )

    if len(state["history"]) >= 4:

        state["acc"] = (
            (state["history"][-1] - state["history"][-2])
            - (state["history"][-2] - state["history"][-3])
        )

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

            score = calc_sector_score(df)

            state = update_state(name, score)

            strength = calc_strength(score, state)

            
            leader_code, leader_name, leader_score = find_leader(df)
            state["leader"] = leader_code

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
                "是否退潮": is_decline(state),
                "成分股数": len(stocks)                
            })

    return result


# =========================================================
# 主题分析（替代概念）
# =========================================================
def analyze_themes(daily_df, industry_df):

    result = []

    for theme, cfg in THEME_MAP.items():

        # -------------------------------------------------
        # 行业匹配
        # -------------------------------------------------
        industry_mask = industry_df.apply(

            lambda x:

                (x.get("l2_name") in cfg["industry"])

                or

                (x.get("l3_name") in cfg["industry"]),

            axis=1
        )

        # -------------------------------------------------
        # 概念关键词匹配
        # -------------------------------------------------
        keyword_mask = industry_df.apply(

            lambda x:

                any(

                    kw in str(x.get("concept", ""))

                    for kw in cfg["keywords"]
                ),

            axis=1
        )
        # =========================
        # 双融合
        # =========================
        mask = industry_mask | keyword_mask
        

        sub = industry_df[mask]

        stocks = sub["ts_code"].dropna().unique().tolist()

        if len(stocks) < MIN_STOCKS:
            continue

        df = daily_df[daily_df["ts_code"].isin(stocks)]

        if df.empty:
            continue

        score = calc_sector_score(df)

        state = update_state(theme, score)

        strength = calc_strength(score, state)

        leader_code, leader_name, leader_score = find_leader(df)

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
            acc REAL
        )

    """)

    conn.commit()

    conn.close()

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
            (date, rank, type, name, score, leader_code,leader_name, leader_score, momentum, acc)

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,?)

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
            getattr(row, "加速度", 0)

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

    theme_res = analyze_themes(daily_df, industry_df)

    all_res = industry_res + theme_res

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

    

