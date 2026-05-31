#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块轮动分析 V2 - 专注寻找近期10-20天最强板块
优化点：
1. 获取近N日日线数据
2. 计算板块N日累计涨幅
3. 识别持续强势板块
4. 输出唯一最强板块
"""

import os
import sys
import io
import time
import pickle
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict

import tushare as ts
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# =========================
# 编码修复（Windows PowerShell）
# =========================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# =========================
# 参数配置
# =========================
LOOKBACK_DAYS = 10      # 回看天数（10-20天）
MIN_STOCKS = 10         # 板块最小股票数
TOP_K = 5               # 输出TOP K板块
MOMENTUM_W = 0.6        # 动量权重
ACC_W = 0.4             # 加速度权重

# =========================
# Tushare配置
# =========================
load_dotenv("config/.env")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# =========================
# 路径配置
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "hot_sector.db")
os.makedirs(CACHE_DIR, exist_ok=True)

# =========================
# 缓存文件路径
# =========================
CONCEPT_LIST_PATH = os.path.join(CACHE_DIR, "ths_concept_list.csv")
CONCEPT_DETAIL_PATH = os.path.join(CACHE_DIR, "ths_concept_detail.pkl")
STOCK_CONCEPT_PATH = os.path.join(CACHE_DIR, "stock_concept_map.pkl")
CONCEPT_STOCK_PATH = os.path.join(CACHE_DIR, "concept_stock_map.pkl")


# =========================================================
# 工具函数
# =========================================================

def get_trade_dates(end_date, n_days=10):
    """获取最近N个交易日"""
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=end_date
    )
    cal = cal[cal['is_open'] == 1]
    cal = cal[cal['cal_date'] <= end_date]
    trade_dates = cal['cal_date'].tolist()[-n_days:]
    return trade_dates


def get_last_trade_date():
    """获取最近交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_date)


# =========================================================
# 多日数据获取
# =========================================================

def get_multi_day_daily(trade_dates):
    """获取多日日线数据"""
    all_data = []
    
    for i, date in enumerate(trade_dates):
        cache_file = os.path.join(CACHE_DIR, f"daily_{date}.csv")
        
        # 优先读取缓存
        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file, dtype={'ts_code': str})
            df['trade_date'] = date
            all_data.append(df)
            print(f"[{i+1}/{len(trade_dates)}] {date}: 缓存读取")
        else:
            # 从Tushare下载
            try:
                df = pro.daily(trade_date=date)
                if not df.empty:
                    df['amount'] = df['amount'] / 100000  # 千元转亿元
                    df['trade_date'] = date
                    df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                    all_data.append(df)
                    print(f"[{i+1}/{len(trade_dates)}] {date}: 下载完成")
                time.sleep(0.3)
            except Exception as e:
                print(f"[{i+1}/{len(trade_dates)}] {date}: 下载失败 {e}")
    
    if not all_data:
        return pd.DataFrame()
    
    result = pd.concat(all_data, ignore_index=True)
    print(f"\n✅ 获取到 {len(trade_dates)} 天数据，共 {len(result)} 条记录")
    return result


# =========================================================
# 板块成分股获取
# =========================================================

def load_theme_map():
    """加载主题配置"""
    file_path = os.path.join(BASE_DIR, "theme_map.json")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"配置不存在: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        theme_map = json.load(f)
    print("主题配置加载完成")
    return theme_map


def get_concept_maps():
    """获取概念映射（带缓存）"""
    # 下载概念列表
    if not os.path.exists(CONCEPT_LIST_PATH):
        print("获取同花顺概念列表...")
        df = pro.ths_index(exchange='A', type='N')
        df.to_csv(CONCEPT_LIST_PATH, index=False, encoding='utf-8-sig')
        concept_df = df
    else:
        concept_df = pd.read_csv(CONCEPT_LIST_PATH, encoding="utf-8-sig")
    
    # 下载概念成分股
    if not os.path.exists(CONCEPT_DETAIL_PATH):
        print("下载概念成分股...")
        all_rows = []
        for i, row in concept_df.iterrows():
            ts_code = row["ts_code"]
            name = row["name"]
            try:
                df = pro.ths_member(ts_code=ts_code)
                if df is not None and not df.empty:
                    df["concept_name"] = name
                    all_rows.append(df)
                time.sleep(0.25)
            except:
                pass
        member_df = pd.concat(all_rows, ignore_index=True)
        with open(CONCEPT_DETAIL_PATH, "wb") as f:
            pickle.dump(member_df, f)
    else:
        with open(CONCEPT_DETAIL_PATH, "rb") as f:
            member_df = pickle.load(f)
    
    # 构建映射
    if not os.path.exists(CONCEPT_STOCK_PATH):
        concept_map = defaultdict(list)
        for _, row in member_df.iterrows():
            code = row.get("con_code", "")
            concept = row.get("concept_name", "")
            if code and concept:
                concept_map[concept].append(code)
        concept_map = {k: sorted(set(v)) for k, v in concept_map.items()}
        with open(CONCEPT_STOCK_PATH, "wb") as f:
            pickle.dump(concept_map, f)
    else:
        with open(CONCEPT_STOCK_PATH, "rb") as f:
            concept_map = pickle.load(f)
    
    return concept_map


def get_sw_industry_map():
    """获取申万行业分类"""
    cache_file = os.path.join(CACHE_DIR, "sw_map.csv")
    if os.path.exists(cache_file):
        return pd.read_csv(cache_file, dtype=str)
    
    df = pro.index_member_all(is_new='Y')
    df.to_csv(cache_file, index=False)
    return df


# =========================================================
# 板块评分（多日版本）
# =========================================================

def calc_sector_score_multiday(df, trade_dates):
    """
    计算板块多日综合评分
    
    参数:
        df: 板块内所有股票的多日数据
        trade_dates: 交易日列表
    
    返回:
        dict: {
            '累计涨幅': float,
            '日均涨幅': float,
            '动量': float,
            '加速度': float,
            '持续强势天数': int,
            '龙头涨幅': float,
            '综合评分': float
        }
    """
    if df.empty or len(trade_dates) < 2:
        return None
    
    # 按日期分组计算板块涨幅
    daily_returns = []
    for date in trade_dates:
        day_df = df[df['trade_date'] == date]
        if not day_df.empty:
            avg_pct = day_df['pct_chg'].mean()
            daily_returns.append(avg_pct)
    
    if len(daily_returns) < 2:
        return None
    
    # 累计涨幅
    cumulative_return = sum(daily_returns)
    
    # 日均涨幅
    avg_return = cumulative_return / len(daily_returns)
    
    # 动量（最近3日涨幅 - 前3日涨幅）
    if len(daily_returns) >= 6:
        momentum = sum(daily_returns[-3:]) - sum(daily_returns[:3])
    elif len(daily_returns) >= 3:
        momentum = sum(daily_returns[-2:]) - sum(daily_returns[:2])
    else:
        momentum = daily_returns[-1] - daily_returns[0]
    
    # 加速度
    if len(daily_returns) >= 3:
        acc = (daily_returns[-1] - daily_returns[-2]) - (daily_returns[-2] - daily_returns[-3])
    else:
        acc = 0
    
    # 持续强势天数（连续上涨）
    strong_days = 0
    for r in reversed(daily_returns):
        if r > 0:
            strong_days += 1
        else:
            break
    
    # 龙头涨幅（最近一日涨幅最大的股票）
    latest_date = trade_dates[-1]
    latest_df = df[df['trade_date'] == latest_date]
    if not latest_df.empty:
        leader_pct = latest_df['pct_chg'].max()
    else:
        leader_pct = 0
    
    # 综合评分
    score = (
        cumulative_return * 1.5 +           # 累计涨幅权重最高
        avg_return * 10 +                   # 日均涨幅
        momentum * 0.6 +                    # 动量
        acc * 0.4 +                         # 加速度
        strong_days * 5 +                   # 持续强势奖励
        leader_pct * 0.8                    # 龙头涨幅
    )
    
    return {
        '累计涨幅': round(cumulative_return, 2),
        '日均涨幅': round(avg_return, 2),
        '动量': round(momentum, 2),
        '加速度': round(acc, 2),
        '持续强势天数': strong_days,
        '龙头涨幅': round(leader_pct, 2),
        '综合评分': round(score, 2)
    }


# =========================================================
# 板块分析（多日版本）
# =========================================================

def analyze_sectors_multiday(multi_day_df, trade_dates, concept_map, industry_df, theme_map):
    """分析所有板块（多日数据）"""
    results = []
    
    # 1. 概念板块分析
    print("\n[1/3] 分析概念板块...")
    daily_codes = set(multi_day_df[multi_day_df['trade_date'] == trade_dates[-1]]['ts_code'].unique())
    
    for concept_name, stocks in concept_map.items():
        # 过滤有效股票
        valid_stocks = [s for s in stocks if s in daily_codes]
        if len(valid_stocks) < MIN_STOCKS:
            continue
        
        # 获取板块数据
        sector_df = multi_day_df[multi_day_df['ts_code'].isin(valid_stocks)]
        if sector_df.empty:
            continue
        
        # 计算评分
        scores = calc_sector_score_multiday(sector_df, trade_dates)
        if scores is None:
            continue
        
        results.append({
            '类型': '概念',
            '板块': concept_name,
            '成分股数': len(valid_stocks),
            **scores
        })
    
    print(f"   概念板块: {len(results)} 个")
    
    # 2. 行业板块分析
    print("\n[2/3] 分析行业板块...")
    industry_results = []
    
    for level in ['l1_name', 'l2_name', 'l3_name']:
        if level not in industry_df.columns:
            continue
        
        for name, g in industry_df.groupby(level):
            stocks = g['ts_code'].dropna().unique().tolist()
            valid_stocks = [s for s in stocks if s in daily_codes]
            
            if len(valid_stocks) < MIN_STOCKS:
                continue
            
            sector_df = multi_day_df[multi_day_df['ts_code'].isin(valid_stocks)]
            if sector_df.empty:
                continue
            
            scores = calc_sector_score_multiday(sector_df, trade_dates)
            if scores is None:
                continue
            
            industry_results.append({
                '类型': f'行业({level})',
                '板块': name,
                '成分股数': len(valid_stocks),
                **scores
            })
    
    results.extend(industry_results)
    print(f"   行业板块: {len(industry_results)} 个")
    
    # 3. 主题板块分析
    print("\n[3/3] 分析主题板块...")
    theme_results = []
    
    for theme, cfg in theme_map.items():
        # 行业匹配
        industry_mask = industry_df.apply(
            lambda x: (x.get("l2_name") in cfg["industry"]) or (x.get("l3_name") in cfg["industry"]),
            axis=1
        )
        
        # 概念关键词匹配
        keyword_mask = industry_df.apply(
            lambda x: any(kw in str(x.get("concept", "")) for kw in cfg["keywords"]),
            axis=1
        )
        
        mask = industry_mask | keyword_mask
        sub = industry_df[mask]
        stocks = sub['ts_code'].dropna().unique().tolist()
        valid_stocks = [s for s in stocks if s in daily_codes]
        
        if len(valid_stocks) < MIN_STOCKS:
            continue
        
        sector_df = multi_day_df[multi_day_df['ts_code'].isin(valid_stocks)]
        if sector_df.empty:
            continue
        
        scores = calc_sector_score_multiday(sector_df, trade_dates)
        if scores is None:
            continue
        
        theme_results.append({
            '类型': '主题',
            '板块': theme,
            '成分股数': len(valid_stocks),
            **scores
        })
    
    results.extend(theme_results)
    print(f"   主题板块: {len(theme_results)} 个")
    
    # 转换为DataFrame并排序
    if not results:
        return pd.DataFrame()
    
    df = pd.DataFrame(results)
    df = df.sort_values('综合评分', ascending=False)
    df.reset_index(drop=True, inplace=True)
    
    return df


# =========================================================
# 龙头识别
# =========================================================

def find_sector_leader(sector_df, latest_date):
    """识别板块龙头"""
    latest_df = sector_df[sector_df['trade_date'] == latest_date]
    if latest_df.empty:
        return None, None, None
    
    # 按涨幅排序
    leader = latest_df.sort_values('pct_chg', ascending=False).iloc[0]
    
    return leader['ts_code'], leader.get('name', leader['ts_code']), leader['pct_chg']


# =========================================================
# 主函数
# =========================================================

def find_strongest_sector(lookback_days=10):
    """
    寻找近N日最强板块
    
    参数:
        lookback_days: 回看天数（10-20）
    
    返回:
        DataFrame: TOP K 最强板块
    """
    print(f"\n{'='*60}")
    print(f"板块轮动分析 V2 - 寻找近 {lookback_days} 日最强板块")
    print(f"{'='*60}\n")
    
    # 1. 获取交易日列表
    end_date = get_last_trade_date()
    print(f"当前交易日: {end_date}")
    
    trade_dates = get_trade_dates(end_date, n_days=lookback_days)
    print(f"分析区间: {trade_dates[0]} ~ {trade_dates[-1]} ({len(trade_dates)} 天)")
    
    # 2. 获取多日数据
    multi_day_df = get_multi_day_daily(trade_dates)
    if multi_day_df.empty:
        print("❌ 无法获取数据")
        return pd.DataFrame()
    
    # 3. 获取板块映射
    print("\n加载板块配置...")
    concept_map = get_concept_maps()
    industry_df = get_sw_industry_map()
    theme_map = load_theme_map()
    
    # 4. 分析板块
    result_df = analyze_sectors_multiday(
        multi_day_df, 
        trade_dates, 
        concept_map, 
        industry_df, 
        theme_map
    )
    
    if result_df.empty:
        print("❌ 无有效板块")
        return pd.DataFrame()
    
    # 5. 输出结果
    print(f"\n{'='*60}")
    print(f"TOP {TOP_K} 最强板块（近 {lookback_days} 日）")
    print(f"{'='*60}\n")
    
    top_df = result_df.head(TOP_K)
    
    for i, row in top_df.iterrows():
        print(f"[{i+1}] {row['板块']} ({row['类型']})")
        print(f"    累计涨幅: {row['累计涨幅']:+.2f}%")
        print(f"    日均涨幅: {row['日均涨幅']:+.2f}%")
        print(f"    持续强势: {row['持续强势天数']} 天")
        print(f"    龙头涨幅: {row['龙头涨幅']:+.2f}%")
        print(f"    综合评分: {row['综合评分']:.2f}")
        print()
    
    # 6. 最强板块详情
    strongest = result_df.iloc[0]
    print(f"\n{'='*60}")
    print(f"🎯 最强板块: {strongest['板块']}")
    print(f"{'='*60}")
    print(f"类型: {strongest['类型']}")
    print(f"成分股数: {strongest['成分股数']}")
    print(f"累计涨幅: {strongest['累计涨幅']:+.2f}%")
    print(f"日均涨幅: {strongest['日均涨幅']:+.2f}%")
    print(f"动量: {strongest['动量']:+.2f}")
    print(f"加速度: {strongest['加速度']:+.2f}")
    print(f"持续强势: {strongest['持续强势天数']} 天")
    print(f"龙头涨幅: {strongest['龙头涨幅']:+.2f}%")
    print(f"综合评分: {strongest['综合评分']:.2f}")
    
    return result_df


# =========================================================
# 运行
# =========================================================

if __name__ == "__main__":
    # 默认分析近10日
    df = find_strongest_sector(lookback_days=10)
    
    # 也可以分析近20日
    # df = find_strongest_sector(lookback_days=20)
