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

# 多因子验证权重配置
FACTOR_WEIGHTS = {
    'industry': 30,      # 行业匹配权重
    'concept': 20,       # 概念标签权重
    'business': 30,      # 业务收入权重
    'news': 20           # 新闻热度权重
}

MIN_RELEVANCE_SCORE = 60  # 最低相关性分数

##=========== TUshare
load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

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
CONCEPT_LIST_PATH = os.path.join(CACHE_DIR, "dc_concept_list.csv")
CONCEPT_DETAIL_PATH = os.path.join(CACHE_DIR, "dc_concept_detail.pkl")
STOCK_CONCEPT_PATH = os.path.join(CACHE_DIR, "stock_concept_map.pkl")
CONCEPT_STOCK_PATH = os.path.join(CACHE_DIR, "concept_stock_map.pkl")

# =========================================================
# 主题映射（替代概念）
# =========================================================
def load_theme_map():
    file_path = os.path.join(BASE_DIR, "theme_map.json")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"配置不存在: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        theme_map = json.load(f)
    
    print("主题配置加载完成")
    return theme_map

THEME_MAP = load_theme_map()

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

# TRADE_DATE = get_last_trade_date()
TRADE_DATE = "20260521"  # 使用已有数据的日期进行测试

# =========================================================
# 下载东方财富概念列表（带缓存）
# =========================================================
def download_dc_concepts():
    print("获取东方财富概念列表...")
    
    if os.path.exists(CONCEPT_LIST_PATH):
        print(f"读取缓存: {CONCEPT_LIST_PATH}")
        return pd.read_csv(CONCEPT_LIST_PATH, encoding="utf-8-sig")
    
    df = pro.dc_concept()
    
    if df is None or df.empty:
        print("警告: 未获取到东方财富概念列表")
        return pd.DataFrame()
    
    df.to_csv(CONCEPT_LIST_PATH, index=False, encoding='utf-8-sig')
    print(f"概念列表已保存: {CONCEPT_LIST_PATH}")
    
    return df

# =========================================================
# 下载东方财富概念成分股（带缓存）
# =========================================================
def download_dc_members(concept_df, limit=100):
    if os.path.exists(CONCEPT_DETAIL_PATH):
        print(f"读取缓存: {CONCEPT_DETAIL_PATH}")
        with open(CONCEPT_DETAIL_PATH, "rb") as f:
            return pickle.load(f)
    
    all_rows = []
    total = min(len(concept_df), limit)
    concept_df = concept_df.head(limit)
    
    for i, row in concept_df.iterrows():
        theme_code = row["theme_code"]
        name = row["name"]
        
        print(f"[{i+1}/{total}] 下载: {name}")
        
        try:
            df = pro.dc_concept_cons(concept_code=theme_code)
            
            if df is None or df.empty:
                continue
            
            df["concept_name"] = name
            df["theme_code"] = theme_code
            all_rows.append(df)
            
            time.sleep(0.1)
            
        except Exception as e:
            print(f"失败: {name} {e}")
    
    if not all_rows:
        return pd.DataFrame()
    
    result = pd.concat(all_rows, ignore_index=True)
    
    with open(CONCEPT_DETAIL_PATH, "wb") as f:
        pickle.dump(result, f)
    
    print(f"概念成分股已保存: {CONCEPT_DETAIL_PATH}")
    return result

# =========================================================
# 构建 股票 -> 概念（带缓存）
# =========================================================
def build_stock_concept_map(member_df):
    if os.path.exists(STOCK_CONCEPT_PATH):
        print(f"读取缓存: {STOCK_CONCEPT_PATH}")
        with open(STOCK_CONCEPT_PATH, "rb") as f:
            return pickle.load(f)
    
    stock_map = defaultdict(list)
    for _, row in member_df.iterrows():
        ts_code = row["ts_code"]
        concept = row["concept_name"]
        stock_map[ts_code].append(concept)
    
    stock_map = {k: ";".join(sorted(set(v))) for k, v in stock_map.items()}
    
    with open(STOCK_CONCEPT_PATH, "wb") as f:
        pickle.dump(stock_map, f)
    
    print(f"股票概念映射已保存: {STOCK_CONCEPT_PATH}")
    return stock_map

# =========================================================
# 构建 概念 -> 股票（带缓存）
# =========================================================
def build_concept_stock_map(member_df):
    if os.path.exists(CONCEPT_STOCK_PATH):
        print(f"读取缓存: {CONCEPT_STOCK_PATH}")
        with open(CONCEPT_STOCK_PATH, "rb") as f:
            return pickle.load(f)
    
    concept_map = defaultdict(list)
    for _, row in member_df.iterrows():
        ts_code = row["ts_code"]
        concept = row["concept_name"]
        concept_map[concept].append(ts_code)
    
    concept_map = {k: sorted(set(v)) for k, v in concept_map.items()}
    
    with open(CONCEPT_STOCK_PATH, "wb") as f:
        pickle.dump(concept_map, f)
    
    print(f"概念股票映射已保存: {CONCEPT_STOCK_PATH}")
    return concept_map

# =========================================================
# 从东方财富接口获取主题成分股
# =========================================================
def get_theme_stocks_from_dc(theme_name, concept_stock_map, keywords=None):
    """从东方财富概念成分股中获取主题成分股"""
    # 1. 精确匹配
    if theme_name in concept_stock_map:
        return concept_stock_map[theme_name]
    
    # 2. 模糊匹配主题名
    matched_stocks = []
    for concept_name, stocks in concept_stock_map.items():
        if theme_name in concept_name or concept_name in theme_name:
            matched_stocks.extend(stocks)
    
    # 3. 使用关键词匹配概念名
    if keywords:
        for concept_name, stocks in concept_stock_map.items():
            for kw in keywords:
                if kw in concept_name:
                    matched_stocks.extend(stocks)
                    break
    
    # 去重
    return list(set(matched_stocks))

# =========================================================
# 加权算法补充成分股
# =========================================================
def supplement_with_algorithm(industry_df, cfg, stock_concept_map):
    """使用加权评分算法补充成分股"""
    scores = pd.Series(0, index=industry_df.index)
    
    # 1. 行业匹配（高权重）
    industry_mask = industry_df.apply(
        lambda x: (x.get("l2_name") in cfg.get("industry", [])) or 
                  (x.get("l3_name") in cfg.get("industry", [])), axis=1
    )
    scores[industry_mask] += FACTOR_WEIGHTS['industry']
    
    # 2. 概念关键词匹配（中权重）
    keywords = cfg.get("keywords", [])
    for ts_code in industry_df["ts_code"].dropna().unique():
        concepts = stock_concept_map.get(ts_code, "")
        for kw in keywords:
            if kw in concepts:
                idx = industry_df[industry_df["ts_code"] == ts_code].index
                scores.loc[idx] += FACTOR_WEIGHTS['concept']
    
    # 3. 业务描述匹配（低权重）- 如果有该字段
    if "business_desc" in industry_df.columns:
        desc_mask = industry_df["business_desc"].str.contains(
            "|".join(keywords), na=False
        )
        scores[desc_mask] += FACTOR_WEIGHTS['business'] // 2
    
    # 筛选高得分股票
    high_score_idx = scores[scores >= MIN_RELEVANCE_SCORE // 2].index
    stocks = industry_df.loc[high_score_idx, "ts_code"].dropna().unique().tolist()
    
    return stocks

# =========================================================
# 多因子验证
# =========================================================
def validate_theme_relevance(ts_code, cfg, stock_concept_map, industry_df):
    """验证股票与主题的相关性，返回0-100分"""
    score = 0
    keywords = cfg.get("keywords", [])
    industries = cfg.get("industry", [])
    
    # 1. 行业分类验证
    industry_row = industry_df[industry_df["ts_code"] == ts_code]
    if not industry_row.empty:
        l2_name = industry_row.iloc[0].get("l2_name", "")
        l3_name = industry_row.iloc[0].get("l3_name", "")
        if l2_name in industries or l3_name in industries:
            score += FACTOR_WEIGHTS['industry']
    
    # 2. 概念标签验证
    concepts = stock_concept_map.get(ts_code, "")
    for kw in keywords:
        if kw in concepts:
            score += FACTOR_WEIGHTS['concept']
            break  # 只加一次
    
    # 3. 业务描述验证
    if not industry_row.empty and "business_desc" in industry_df.columns:
        business_desc = industry_row.iloc[0].get("business_desc", "")
        if any(kw in business_desc for kw in keywords):
            score += FACTOR_WEIGHTS['business']
    
    # 4. 新闻热度验证（简化版：通过概念热门度判断）
    # 实际应用中可以调用新闻接口或使用概念热门度数据
    if concepts:
        score += FACTOR_WEIGHTS['news'] // 2
    
    return min(score, 100)

# =========================================================
# 板块评分
# =========================================================
def calc_sector_score(df):
    if df.empty:
        return 0
    
    # 涨幅因子
    pct_mean = df["pct_chg"].mean()
    pct_std = df["pct_chg"].std()
    momentum_score = pct_mean - 0.5 * pct_std
    
    # 成交额因子
    amount_mean = df["amount"].mean()
    acc_score = np.log(amount_mean + 1)
    
    # 上涨比例因子
    up_ratio = (df["pct_chg"] > 0).mean()
    
    # 综合评分
    score = momentum_score * MOMENTUM_W + acc_score * ACC_W + up_ratio * 10
    
    return max(0, score)

# =========================================================
# 主线分析（组合方案：专业数据源 + 多因子验证）
# =========================================================
def analyze_themes_advanced(
    daily_df,
    industry_df,
    stock_concept_map,
    concept_stock_map,
    min_stocks=5
):
    result = []
    
    for theme, cfg in THEME_MAP.items():
        print(f"分析主题: {theme}")
        
        # 1. 优先从东方财富概念成分股获取（使用主题名和关键词双重匹配）
        keywords = cfg.get("keywords", [])
        theme_stocks = get_theme_stocks_from_dc(theme, concept_stock_map, keywords)
        print(f"  - 从东方财富获取: {len(theme_stocks)} 只")
        
        # 2. 如果数量不足，使用算法补充
        if len(theme_stocks) < min_stocks:
            supplemented = supplement_with_algorithm(industry_df, cfg, stock_concept_map)
            # 合并去重
            theme_stocks = list(set(theme_stocks + supplemented))
            print(f"  - 算法补充后: {len(theme_stocks)} 只")
        
        # 3. 使用多因子验证过滤边缘标的
        validated_stocks = []
        for ts_code in theme_stocks:
            relevance = validate_theme_relevance(ts_code, cfg, stock_concept_map, industry_df)
            if relevance >= MIN_RELEVANCE_SCORE:
                validated_stocks.append(ts_code)
        
        print(f"  - 验证通过: {len(validated_stocks)} 只")
        
        if len(validated_stocks) < min_stocks:
            print(f"  - 跳过：成分股不足")
            continue
        
        # 4. 获取成分股行情数据
        df = daily_df[daily_df["ts_code"].isin(validated_stocks)]
        
        if df.empty:
            print(f"  - 跳过：无行情数据")
            continue
        
        # 5. 板块评分
        score = calc_sector_score(df)
        
        # 6. 龙头股（今日涨幅最高）
        leader = df.sort_values("pct_chg", ascending=False).iloc[0]
        
        # 7. 中军股（流通市值大、涨幅稳健）
        mid_cap = df[(df["pct_chg"] > 0) & (df["amount"] > df["amount"].median())]
        if not mid_cap.empty:
            middle_stock = mid_cap.sort_values("amount", ascending=False).iloc[0]
        else:
            middle_stock = leader
        
        # 8. 双创弹性标的（创业板/科创板，波动大）
        innovation_stocks = df[df["ts_code"].str.startswith(("300", "688"))]
        if not innovation_stocks.empty:
            elastic_stock = innovation_stocks.sort_values("pct_chg", ascending=False).iloc[0]
        else:
            elastic_stock = None
        
        result.append({
            "主线": theme,
            "评分": round(score, 2),
            "成分股数": len(validated_stocks),
            "龙头代码": leader["ts_code"],
            "龙头涨幅": round(leader["pct_chg"], 2),
            "中军代码": middle_stock["ts_code"],
            "中军涨幅": round(middle_stock["pct_chg"], 2),
            "双创弹性代码": elastic_stock["ts_code"] if elastic_stock else None,
            "双创弹性涨幅": round(elastic_stock["pct_chg"], 2) if elastic_stock else None,
            "平均涨幅": round(df["pct_chg"].mean(), 2),
            "上涨比例": round((df["pct_chg"] > 0).mean() * 100, 2)
        })
    
    result = pd.DataFrame(result)
    
    if not result.empty:
        result = result.sort_values("评分", ascending=False).head(TOP_K)
    
    return result

# =========================================================
# 初始化概念缓存
# =========================================================
def init_concept_cache():
    concept_df = download_dc_concepts()
    member_df = download_dc_members(concept_df)
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
        rows.append({"ts_code": ts_code, "concept": concept})
    return pd.DataFrame(rows)

# =========================================================
# 日线数据
# =========================================================
def get_daily_df():
    print("读取全市场行情...")
    
    cache_file = os.path.join(CACHE_DIR, f"daily_{TRADE_DATE}.csv")
    
    if os.path.exists(cache_file):
        print(f"读取缓存: {cache_file}")
        df = pd.read_csv(cache_file, dtype={'ts_code': str})
        return df
    
    print("缓存不存在，开始从Tushare下载...")
    
    df = pro.daily(trade_date=TRADE_DATE)
    
    if df.empty:
        return pd.DataFrame()
    
    df['amount'] = df['amount'] / 100000
    
    df.to_csv(cache_file, index=False, encoding='utf-8-sig')
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
    
    print("下载申万行业分类...")
    
    df = pro.sw_index_member(trade_date=TRADE_DATE)
    
    if df.empty:
        return pd.DataFrame()
    
    df.to_csv(cache_file, index=False, encoding='utf-8-sig')
    print(f"行业分类已保存: {cache_file}")
    
    return df

# =========================================================
# 主函数
# =========================================================
def main():
    print(f"分析日期: {TRADE_DATE}")
    print("="*50)
    
    # 1. 初始化概念缓存
    stock_concept_map, concept_stock_map = init_concept_cache()
    
    # 2. 获取日线数据
    daily_df = get_daily_df()
    if daily_df.empty:
        print("错误: 无法获取日线数据")
        return
    
    # 3. 获取行业数据
    industry_df = get_sw_industry_map()
    if industry_df.empty:
        print("警告: 无法获取行业数据，使用空DataFrame")
        industry_df = pd.DataFrame(columns=["ts_code", "l2_name", "l3_name"])
    
    # 4. 执行主题分析
    result = analyze_themes_advanced(
        daily_df,
        industry_df,
        stock_concept_map,
        concept_stock_map,
        min_stocks=MIN_STOCKS
    )
    
    # 5. 输出结果
    print("\n" + "="*50)
    print("主题主线分析结果")
    print("="*50)
    
    if result.empty:
        print("未找到符合条件的主题主线")
        return
    
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)
    print(result)
    
    # 6. 保存结果
    result_file = os.path.join(CACHE_DIR, f"theme_result_{TRADE_DATE}.csv")
    result.to_csv(result_file, index=False, encoding='utf-8-sig')
    print(f"\n分析结果已保存: {result_file}")

if __name__ == "__main__":
    main()
