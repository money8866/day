# -*- coding: utf-8 -*-
"""板块强度系统 - 主线识别（完全切换同花顺）"""
import pandas as pd
import numpy as np
from config import SECTOR
from data_engine import (
    fetch_all_concept_boards_daily, fetch_all_industry_boards_daily,  # 同花顺板块
    fetch_ths_member,  # 同花顺成分股
    fetch_stock_basic
)

def analyze_sector_strength(trade_date=None):
    """板块强度分析（同花顺数据源）
    返回：板块排行 + 主线板块（294个概念板块）
    """
    concepts = fetch_all_concept_boards_daily(trade_date=trade_date, use_cache=True)
    industries = fetch_all_industry_boards_daily(trade_date=trade_date, use_cache=True)

    concept_rank = _rank_boards(concepts, "concept")
    industry_rank = _rank_boards(industries, "industry")

    # 识别主线: 概念排名前N
    main_themes = []
    if len(concept_rank) > 0:
        top_n = min(SECTOR["top_concepts"], len(concept_rank))
        for i in range(top_n):
            row = concept_rank.iloc[i]
            main_themes.append({
                "type": "concept",
                "code": row.get("ts_code", ""),  # 同花顺代码 885xxx.TI
                "name": row.get("name", ""),
                "rank": i + 1,
                "score": row.get("composite_score", 0),
                "pct_change": row.get("pct_change", 0),
            })

    # 行业主线
    if len(industry_rank) > 0:
        top_n = min(SECTOR["top_industries"], len(industry_rank))
        for i in range(top_n):
            row = industry_rank.iloc[i]
            main_themes.append({
                "type": "industry",
                "code": row.get("ts_code", ""),
                "name": row.get("name", ""),
                "rank": i + 1,
                "score": row.get("composite_score", 0),
                "pct_change": row.get("pct_change", 0),
            })

    return {
        "concepts": concept_rank,
        "industries": industry_rank,
        "main_themes": main_themes,
    }

def _rank_boards(df, board_type):
    """
    板块综合评分排名(同花顺版本)
    因子: 涨幅 + 活跃度(换手率)
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()

    result = df.copy()
    # 过滤无效名称
    result = result[result['name'].notna()]
    
    w = SECTOR

    # 涨幅因子 (标准化 0-100)
    if 'pct_change' in result.columns:
        pct_min = result['pct_change'].min()
        pct_max = result['pct_change'].max()
        pct_range = pct_max - pct_min
        if pct_range > 0:
            result['f_momentum'] = (result['pct_change'] - pct_min) / pct_range * 100
        else:
            result['f_momentum'] = 50
    else:
        result['f_momentum'] = 0

    # 活跃度因子 (换手率标准化)
    if 'turnover_rate' in result.columns:
        tr_min = result['turnover_rate'].min()
        tr_max = result['turnover_rate'].max()
        tr_range = tr_max - tr_min
        if tr_range > 0:
            result['f_activity'] = (result['turnover_rate'] - tr_min) / tr_range * 100
        else:
            result['f_activity'] = 50
    else:
        result['f_activity'] = 0

    # 资金流因子暂缺(同花顺接口无此字段)
    result['f_capital'] = 0
    
    # 板块宽度因子暂缺
    result['f_breadth'] = 0

    # 综合评分
    result['composite_score'] = (
        w["momentum_weight"] * result['f_momentum'] +
        w["capital_flow_weight"] * result['f_capital'] +
        w["breadth_weight"] * result['f_breadth']
    )

    result = result.sort_values('composite_score', ascending=False).reset_index(drop=True)
    return result

def get_main_theme_stocks(main_themes, top_n_boards=3):
    """
    获取主线板块的成分股（同花顺接口）
    返回: 合并去重后的候选股票DataFrame
    """
    all_stocks = []
    count = 0
    for theme in main_themes:
        if count >= top_n_boards:
            break
        board_code = theme.get("code", "")  # 同花顺代码 885xxx.TI
        if not board_code:
            continue
        stocks = fetch_ths_member(board_code, use_cache=True)
        if stocks is not None and len(stocks) > 0:
            # 同花顺返回: ts_code, con_code(000519.SZ), con_name, is_new
            # 提取纯代码（去掉.SZ/.SH后缀）
            stocks['code'] = stocks['con_code'].str.split('.').str[0]
            stocks['name'] = stocks['con_name']
            stocks['board_name'] = theme["name"]
            stocks['board_rank'] = theme["rank"]
            stocks['board_score'] = theme["score"]
            all_stocks.append(stocks)
        count += 1

    if not all_stocks:
        return pd.DataFrame()

    merged = pd.concat(all_stocks, ignore_index=True)
    # 去重(保留评分最高的板块)
    merged = merged.sort_values('board_score', ascending=False)
    merged = merged.drop_duplicates(subset='code', keep='first')
    return merged.reset_index(drop=True)


def track_history_themes(trade_date, top5_boards):
    """
    记录历史主线板块（过去9个交易日）
    trade_date: 当前交易日 YYYYMMDD
    top5_boards: 当日TOP5板块名称列表
    返回: 更新后的历史数据（最近9个交易日）
    """
    import json
    import os
    from datetime import datetime, timedelta
    
    CACHE_DIR = r"C:\Users\kongx\mystock\dragon\cache"
    HISTORY_FILE = os.path.join(CACHE_DIR, "history_themes.json")
    
    # 读取历史
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
    else:
        history = []
    
    # 添加今日数据
    history.append({
        "date": trade_date,
        "top5": top5_boards
    })
    
    # 只保留最近9个交易日
    if len(history) > 9:
        history = history[-9:]
    
    # 保存
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    return history


def find_recurring_themes(history_data, min_count=3):
    """
    识别反复活跃的板块（出现在≥min_count个交易日）
    history_data: track_history_themes()返回的历史数据
    返回: 反复活跃板块列表 [{"name": "PCB概念", "count": 5}, ...]
    """
    if not history_data or len(history_data) < min_count:
        return []
    
    # 统计每个板块出现次数
    theme_count = {}
    for day_data in history_data:
        for theme_name in day_data["top5"]:
            theme_count[theme_name] = theme_count.get(theme_name, 0) + 1
    
    # 筛选出现≥min_count次的板块
    recurring = [
        {"name": name, "count": count}
        for name, count in theme_count.items()
        if count >= min_count
    ]
    
    # 按出现次数降序
    recurring.sort(key=lambda x: x["count"], reverse=True)
    
    return recurring


def load_recurring_themes():
    """
    读取当前缓存的反复活跃板块
    返回: 板块名称集合 set()
    """
    import json
    import os
    
    CACHE_DIR = r"C:\Users\kongx\mystock\dragon\cache"
    HISTORY_FILE = os.path.join(CACHE_DIR, "history_themes.json")
    
    if not os.path.exists(HISTORY_FILE):
        return set()
    
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        history = json.load(f)
    
    recurring = find_recurring_themes(history, min_count=3)
    return set(item["name"] for item in recurring)
