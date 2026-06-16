#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题枢纽股多主题评分器
识别全市场约500个枢纽股（跨多个主题的股票），并计算它们在各主题中的评分
输出JSON格式：股票代码 -> {主题名: 评分, ...}
"""
import os
import sys
import json
import pickle
import warnings
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts

warnings.filterwarnings('ignore')

# =========================
# 环境变量
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# =========================
# 获取交易日
# =========================
def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    result = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())
    return result

TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)

# =========================
# 辅助函数
# =========================
def _strip_ii(name):
    """去掉行业名称中的Ⅱ后缀"""
    if not isinstance(name, str) or not name:
        return ''
    for suffix in ["Ⅱ"]:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name

def _in_industry_list(name, industry_list):
    """行业名称匹配，忽略Ⅱ后缀"""
    if not isinstance(name, str) or not name:
        return False
    stripped = _strip_ii(name)
    for ind in industry_list:
        if isinstance(ind, str) and _strip_ii(ind) == stripped:
            return True
    return False

# =========================
# 加载热点主题定义
# =========================
def load_hot_themes():
    json_path = os.path.join(BASE_DIR, "theme.json")
    if not os.path.exists(json_path):
        print(f"错误: 未找到 {json_path}")
        return {}

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        themes = data.get('HOT_THEMES', {})
        print(f"✓ 从JSON加载了 {len(themes)} 个热点主题")
        return themes
    except Exception as e:
        print(f"加载JSON失败: {e}")
        return {}

# =========================
# 获取概念+行业板块映射
# =========================
def get_concept_and_stock_info():
    print("\n[1/4] 加载东方财富概念+行业板块映射...")
    cache_file = os.path.join(CACHE_DIR, "dc_all_members.pkl")

    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 条记录")
                return build_maps_from_df(df)
        except:
            pass

    print("   正在调用Tushare API获取东方财富板块数据...")
    try:
        all_members = []
        total_boards = 0

        concept_df = pro.dc_index(trade_date=TRADE_DATE, idx_type='概念板块')
        time.sleep(0.2)
        industry_df = pro.dc_index(trade_date=TRADE_DATE, idx_type='行业板块')
        time.sleep(0.2)

        all_boards = []
        if not concept_df.empty:
            all_boards.append(concept_df[['ts_code', 'name']])
            total_boards += len(concept_df)
            print(f"   概念板块 {len(concept_df)} 个")
        if not industry_df.empty:
            all_boards.append(industry_df[['ts_code', 'name']])
            total_boards += len(industry_df)
            print(f"   行业板块 {len(industry_df)} 个")

        if not all_boards:
            return {}, {}, pd.DataFrame()

        boards_df = pd.concat(all_boards, ignore_index=True)
        board_name_map = dict(zip(boards_df['ts_code'], boards_df['name']))
        all_bk_codes = boards_df['ts_code'].tolist()
        print(f"   共 {total_boards} 个板块")

        batch_size = 25
        for i in range(0, len(all_bk_codes), batch_size):
            batch = all_bk_codes[i:i+batch_size]
            try:
                members = pro.dc_member(trade_date=TRADE_DATE, ts_code=','.join(batch))
                if not members.empty:
                    members['concept_name'] = members['ts_code'].map(board_name_map)
                    members = members.dropna(subset=['concept_name'])
                    all_members.append(members)
                time.sleep(0.15)
            except Exception as e:
                print(f"   跳过批次 {i//batch_size+1}: {e}")
                continue
            if (i // batch_size + 1) % 5 == 0:
                print(f"   已处理 {min((i//batch_size+1)*batch_size, total_boards)}/{total_boards} 个板块")

        if all_members:
            df = pd.concat(all_members, ignore_index=True)
            df = df.drop_duplicates(subset=['con_code', 'concept_name'])
            df.to_pickle(cache_file)
            print(f"   成功获取 {len(df)} 条成份股记录")
            return build_maps_from_df(df)
    except Exception as e:
        print(f"调用 dc_index/dc_member API失败: {e}")
    return {}, {}, pd.DataFrame()

def build_maps_from_df(df):
    concept_map = {}
    name_map = {}
    stock_concepts = {}

    for _, row in df.iterrows():
        ts_code = row['con_code']
        concept_name = row['concept_name']
        stock_name = row.get('name', '')

        if concept_name not in concept_map:
            concept_map[concept_name] = set()
        concept_map[concept_name].add(ts_code)

        if ts_code not in name_map or not name_map[ts_code]:
            name_map[ts_code] = stock_name

        if ts_code not in stock_concepts:
            stock_concepts[ts_code] = []
        stock_concepts[ts_code].append(concept_name)

    concept_map = {k: list(v) for k, v in concept_map.items()}
    return concept_map, name_map, stock_concepts

# =========================
# 获取股票基础信息
# =========================
def get_stock_basic():
    print("\n[2/4] 获取股票基础信息...")
    cache_file = os.path.join(CACHE_DIR, "stock_basic.pkl")

    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 只股票")
                return df
        except:
            pass

    print("   正在调用Tushare API获取股票基础信息...")
    df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,industry,list_date')
    time.sleep(0.1)
    df.to_pickle(cache_file)
    print(f"   成功获取 {len(df)} 只股票")
    return df

# =========================
# 获取市场数据
# =========================
def get_daily_basic():
    print("\n[3/4] 获取市场数据...")
    cache_file = os.path.join(CACHE_DIR, f"daily_basic_{TRADE_DATE}.pkl")

    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 只股票")
                return df
        except:
            pass

    print("   正在调用Tushare API获取市场数据...")
    df = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,circ_mv,turnover_rate,pe,pb')
    time.sleep(0.1)
    df.to_pickle(cache_file)
    print(f"   成功获取 {len(df)} 只股票")
    return df

# =========================
# 计算股票在主题中的评分
# =========================
def calculate_theme_score(stock_data, theme_data, stock_concepts, stock_industry):
    """
    计算股票在某个主题中的评分

    评分公式：
    - 基础分 = (市值^0.5) * 0.8 + 纯度 * 2
    - 行业匹配系数 = 1.0 (匹配) / 0.15 (不匹配)
    - 综合分 = 基础分 * 行业匹配系数
    """
    ts_code = stock_data['ts_code']
    mcap = stock_data['mcap']
    turnover = stock_data['turnover']

    # 换手率过滤（低换手率股票降低评分）
    if mcap > 5000:
        min_turnover = 0.1
    elif mcap > 1000:
        min_turnover = 0.2
    elif mcap > 200:
        min_turnover = 0.5
    else:
        min_turnover = 1.0

    turnover_factor = min(1.0, turnover / min_turnover) if min_turnover > 0 else 0

    # 计算纯度（关键词匹配 + 概念匹配 + 行业匹配）
    keyword_list = theme_data.get('keywords', [])
    concept_list = theme_data.get('concept', [])
    industry_list = theme_data.get('industry', [])

    concepts = stock_concepts.get(ts_code, [])
    concepts_str = '|'.join(concepts)

    purity = 0
    for kw in keyword_list:
        if kw in concepts_str:
            purity += 1
    for c in concept_list:
        if c in concepts:
            purity += 1

    # 行业匹配
    industry = stock_industry.get(ts_code, '')
    industry_match = industry and _in_industry_list(industry, industry_list)
    if industry_match:
        purity += 1

    # 基础评分
    base_score = (mcap ** 0.5) * 0.8 + purity * 2

    # 行业匹配系数（降低惩罚力度）
    relevance_penalty = 1.0 if industry_match else 0.5

    # 综合评分（加入换手率因子，但降低影响）
    composite_score = base_score * relevance_penalty * (0.5 + 0.5 * turnover_factor)

    # 归一化到0-100范围
    normalized_score = min(100, max(0, composite_score * 0.8))

    return normalized_score

# =========================
# 识别枢纽股并计算多主题评分
# =========================
def identify_hub_stocks_and_score(hot_themes, stock_concepts, stock_list_df, market_cap_df):
    print("\n[4/4] 识别枢纽股并计算多主题评分...")

    # 构建股票基础信息字典
    market_cap_dict = {}
    if not market_cap_df.empty:
        market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}

    stock_info_dict = {}
    stock_industry_dict = {}
    if not stock_list_df.empty:
        for _, row in stock_list_df.iterrows():
            stock_info_dict[row['ts_code']] = row
            stock_industry_dict[row['ts_code']] = row.get('industry', '')

    # 第一步：为每只股票计算在所有主题中的评分
    stock_theme_scores = {}  # {ts_code: {theme_name: score, ...}}

    for theme_name, theme_data in hot_themes.items():
        print(f"   处理主题: {theme_name}")

        # 匹配该主题的股票
        industry_list = theme_data.get('industry', [])
        concept_list = theme_data.get('concept', [])
        core_companies = theme_data.get('core_companies', [])

        matched_stocks = set()

        # 按 stock_basic 行业匹配
        for ts_code, ind_basic in stock_industry_dict.items():
            if ind_basic and _in_industry_list(ind_basic, industry_list):
                matched_stocks.add(ts_code)

        # 按东财概念/行业板块匹配
        for ts_code, concepts in stock_concepts.items():
            matched = False
            for c in concept_list:
                if c in concepts:
                    matched_stocks.add(ts_code)
                    matched = True
                    break
            if not matched:
                for ind in industry_list:
                    stripped_ind = _strip_ii(ind)
                    for c in concepts:
                        if _strip_ii(c) == stripped_ind:
                            matched_stocks.add(ts_code)
                            break
                    if ts_code in matched_stocks:
                        break

        # core_companies 强制纳入
        if core_companies:
            for ts_code, row in stock_info_dict.items():
                stock_name = row.get('name', '')
                if any(company in stock_name for company in core_companies):
                    matched_stocks.add(ts_code)

        # 为每只匹配的股票计算评分
        for ts_code in matched_stocks:
            if ts_code not in market_cap_dict:
                continue

            # 构建股票数据
            stock_data = {
                'ts_code': ts_code,
                'mcap': market_cap_dict[ts_code]['total_mv'] / 10000,
                'turnover': market_cap_dict[ts_code].get('turnover_rate', 0)
            }

            # 计算评分
            score = calculate_theme_score(stock_data, theme_data, stock_concepts, stock_industry_dict)

            # 只保留评分 >= 20 的股票（进一步降低阈值）
            if score >= 20:
                if ts_code not in stock_theme_scores:
                    stock_theme_scores[ts_code] = {}
                stock_theme_scores[ts_code][theme_name] = int(score)

    # 第二步：识别枢纽股（跨多个主题的股票）
    # 降低门槛：只要在至少1个主题中评分>=30就算枢纽股
    hub_stocks = {}
    for ts_code, theme_scores in stock_theme_scores.items():
        theme_count = len(theme_scores)
        if theme_count >= 1:  # 至少跨1个主题（降低门槛）
            stock_name = stock_info_dict.get(ts_code, {}).get('name', ts_code)
            hub_stocks[ts_code] = {
                'name': stock_name,
                'theme_count': theme_count,
                'theme_scores': theme_scores
            }

    # 按主题数量降序排序
    hub_stocks_sorted = sorted(
        hub_stocks.items(),
        key=lambda x: (x[1]['theme_count'], sum(x[1]['theme_scores'].values())),
        reverse=True
    )

    # 取前500只枢纽股
    top_hub_stocks = dict(hub_stocks_sorted[:500])

    print(f"   识别到 {len(top_hub_stocks)} 只枢纽股（跨多个主题）")

    # 打印前10只枢纽股
    print("\n   TOP10 枢纽股:")
    for i, (ts_code, data) in enumerate(list(top_hub_stocks.items())[:10], 1):
        print(f"     {i}. {ts_code} {data['name']} - {data['theme_count']}个主题")
        for theme, score in sorted(data['theme_scores'].items(), key=lambda x: x[1], reverse=True):
            print(f"        {theme}: {score}")

    # 第三步：构建输出格式
    output = {}
    for ts_code, data in top_hub_stocks.items():
        output[ts_code] = data['theme_scores']

    return output

# =========================
# 保存结果
# =========================
def save_hub_stocks_json(hub_stocks):
    output_file = os.path.join(BASE_DIR, "hub_stocks_scores.json")

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(hub_stocks, f, ensure_ascii=False, indent=2)

    print(f"\n枢纽股评分已保存到: {output_file}")
    print(f"共 {len(hub_stocks)} 只枢纽股")

# =========================
# 主函数
# =========================
def main():
    print("=" * 60)
    print("主题枢纽股多主题评分器")
    print("=" * 60)

    # 加载主题定义
    hot_themes = load_hot_themes()
    if not hot_themes:
        print("错误: 未加载到主题定义")
        return

    # 获取数据
    concept_map, name_map, stock_concepts = get_concept_and_stock_info()
    stock_list_df = get_stock_basic()
    market_cap_df = get_daily_basic()

    # 识别枢纽股并计算评分
    hub_stocks = identify_hub_stocks_and_score(
        hot_themes, stock_concepts, stock_list_df, market_cap_df
    )

    # 保存结果
    save_hub_stocks_json(hub_stocks)

    print("\n" + "=" * 60)
    print("枢纽股识别完成")
    print("=" * 60)

if __name__ == "__main__":
    main()