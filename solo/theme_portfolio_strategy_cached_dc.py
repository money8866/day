#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题投资组合策略分析 - 东方财富概念版
使用东财 dc_index / dc_member 接口（概念+行业板块），批量拉取提升速度。
行业匹配来源：stock_basic + 东财板块分类（已移除申万行业依赖）。
"""
import os
import sys
import json
import pickle
import warnings
import time
import sqlite3
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
# 缓存管理器（按交易日过期）
# =========================
class CacheManager:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.current_trade_date = None

    def _get_trade_date(self):
        if self.current_trade_date:
            return self.current_trade_date
        now = datetime.now()
        if now.hour < 15:
            query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
        else:
            query_date = now.strftime('%Y%m%d')
        try:
            cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
            cal = cal[cal['is_open'] == 1]
            self.current_trade_date = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())
        except:
            self.current_trade_date = query_date
        return self.current_trade_date

    def _get_cache_key(self, func_name, **kwargs):
        return "_".join([func_name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])

    def _get_cache_file(self, cache_key):
        safe_key = cache_key.replace("/", "_").replace(":", "_")
        return os.path.join(self.cache_dir, f"cache_{safe_key}.pkl")

    def get(self, func_name, **kwargs):
        cache_key = self._get_cache_key(func_name, **kwargs)
        cache_file = self._get_cache_file(cache_key)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'rb') as f:
                    cache_data = pickle.load(f)
                if cache_data.get('trade_date', '') == self._get_trade_date():
                    return cache_data.get('data')
            except:
                pass
        return None

    def set(self, func_name, data, **kwargs):
        cache_key = self._get_cache_key(func_name, **kwargs)
        cache_file = self._get_cache_file(cache_key)
        cache_data = {'trade_date': self._get_trade_date(), 'timestamp': time.time(), 'data': data}
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception as e:
            print(f"缓存保存失败: {e}")

cache_manager = CacheManager(CACHE_DIR)

# =========================
# 负面事件筛选
# =========================
def _strip_ii(name):
    """去掉行业名称中的Ⅱ后缀，只保留前面内容"""
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


def is_negative_stock(ts_code):
    func_name = "negative_stock"
    cached_data = cache_manager.get(func_name, ts_code=ts_code)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.namechange(ts_code=ts_code, fields='change_reason')
        if not df.empty:
            for _, row in df.iterrows():
                reason = str(row.get('change_reason', ''))
                if 'ST' in reason or '*ST' in reason or '暂停上市' in reason:
                    cache_manager.set(func_name, True, ts_code=ts_code)
                    return True
    except:
        pass
    cache_manager.set(func_name, False, ts_code=ts_code)
    return False

# =========================
# 加载热点主题定义
# =========================
def load_hot_themes():
    json_path = os.path.join(BASE_DIR, "theme.json")
    if not os.path.exists(json_path):
        print(f"警告: 未找到 {json_path}，使用默认主题")
        return get_default_themes()

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        themes = data.get('HOT_THEMES', {})
        if not themes:
            return get_default_themes()
        print(f"✓ 从JSON加载了 {len(themes)} 个热点主题")
        return themes
    except Exception as e:
        print(f"加载JSON失败: {e}")
        return get_default_themes()

def get_default_themes():
    return {
        "AI芯片": {"industry": ["半导体"], "keywords": ["AI芯片", "GPU", "算力"]},
        "新能源": {"industry": ["电力设备"], "keywords": ["新能源", "锂电池", "光伏"]}
    }

# =========================
# 获取交易日
# =========================
def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    func_name = "get_last_trade_date"
    cached = cache_manager.get(func_name, query_date=query_date)
    if cached:
        return cached

    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    result = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())
    cache_manager.set(func_name, result, query_date=query_date)
    return result

TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)

# =========================
# 获取概念+行业板块映射（东方财富 dc_index + dc_member）
# =========================
def get_concept_and_stock_info():
    print("\n[1/5] 加载东方财富概念+行业板块映射...")
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

        # 获取概念板块列表
        concept_df = pro.dc_index(trade_date=TRADE_DATE, idx_type='概念板块')
        time.sleep(0.2)
        # 获取行业板块列表
        industry_df = pro.dc_index(trade_date=TRADE_DATE, idx_type='行业板块')
        time.sleep(0.2)

        # 合并所有板块
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

        # 批量拉取成分股（25个板块/次，单次约 25*150=3750 条，在5000限制内）
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
            print(f"   成功获取 {len(df)} 条成份股记录（共{total_boards}个板块）")
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
# 缓存的API调用
# =========================
def cached_daily_basic(trade_date):
    func_name = "daily_basic"
    cached = cache_manager.get(func_name, trade_date=trade_date)
    if cached is not None:
        print(f"   从缓存获取每日基础数据: {trade_date}")
        return cached
    print(f"   调用Tushare API: daily_basic")
    df = pro.daily_basic(trade_date=trade_date, fields='ts_code,total_mv,circ_mv,turnover_rate,pe,pb')
    time.sleep(0.1)
    cache_manager.set(func_name, df, trade_date=trade_date)
    return df

def cached_stock_basic():
    func_name = "stock_basic"
    cached = cache_manager.get(func_name)
    if cached is not None:
        print(f"   从缓存获取股票基础信息")
        return cached
    print(f"   调用Tushare API: stock_basic")
    df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,industry,list_date')
    time.sleep(0.1)
    cache_manager.set(func_name, df)
    return df

# =========================
# 构建主题投资组合（简化版）
# =========================
def build_theme_portfolio(hot_themes, concept_map, name_map, stock_concepts, market_cap_df, stock_list_df):
    print("\n[5/5] 构建主题投资组合（简化策略）...")

    portfolio = []

    # 构建字典
    market_cap_dict = {}
    if not market_cap_df.empty:
        market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}

    stock_info_dict = {}
    if not stock_list_df.empty:
        stock_info_dict = {row['ts_code']: row for _, row in stock_list_df.iterrows()}

    # 构建股票行业映射（来自 stock_basic，交易所行业分类）
    stock_industry_dict = {}
    if not stock_list_df.empty:
        for _, row in stock_list_df.iterrows():
            stock_industry_dict[row['ts_code']] = row.get('industry', '')

    # 收集所有需要处理的股票代码
    theme_stock_map = {}

    for theme_name, theme_data in hot_themes.items():
        industry_list = theme_data.get('industry', [])
        concept_list = theme_data.get('concept', [])

        matched_stocks = set()

        # 按 stock_basic 行业匹配（忽略Ⅱ后缀）
        for ts_code, ind_basic in stock_industry_dict.items():
            if ind_basic and _in_industry_list(ind_basic, industry_list):
                matched_stocks.add(ts_code)

        # 按东财概念/行业板块匹配（匹配 concept 列表 + industry 列表中的板块名称）
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

        theme_stock_map[theme_name] = matched_stocks

        print(f"   主题 '{theme_name}': 匹配 {len(matched_stocks)} 只股票")

    for theme_name, theme_data in hot_themes.items():
        print(f"\n处理主题: {theme_name}")

        theme_stocks = theme_stock_map.get(theme_name, set())
        industry_list = theme_data.get('industry', [])
        keyword_list = theme_data.get('keywords', [])
        concept_list = theme_data.get('concept', [])
        exclude_keywords = theme_data.get('exclude_keywords', [])

        print(f"   共涉及 {len(theme_stocks)} 只股票")

        filtered_stocks = []

        for ts_code in theme_stocks:
            if ts_code not in market_cap_dict:
                continue

            mv = market_cap_dict[ts_code]['total_mv']
            turnover = market_cap_dict[ts_code].get('turnover_rate', 0)
            mcap = mv / 10000

            # 按市值分级的换手率门槛（大盘蓝筹换手率自然低）
            if mcap > 5000:
                min_turnover = 0.1
            elif mcap > 1000:
                min_turnover = 0.2
            elif mcap > 200:
                min_turnover = 0.5
            else:
                min_turnover = 1.0
            if turnover < min_turnover:
                continue

            is_st = is_negative_stock(ts_code)
            if is_st and mv < 100000:
                continue

            # exclude_keywords 过滤（概念名开头匹配+股票名匹配）
            concepts = stock_concepts.get(ts_code, [])
            concepts_str = '|'.join(concepts)
            stock_name = name_map.get(ts_code, stock_info_dict.get(ts_code, {}).get('name', ts_code))
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
                continue

            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            industry = stock_industry_dict.get(ts_code, '')
            if industry and _in_industry_list(industry, industry_list):
                purity += 1

            mcap = mv / 10000

            # 行业相关性判定（东财行业板块 + stock_basic，忽略Ⅱ后缀）
            ind_basic = stock_industry_dict.get(ts_code, '')
            industry_match = ind_basic and _in_industry_list(ind_basic, industry_list)

            # 评分仅基于：市值规模 + 题材纯度（去掉趋势/成交额）
            base_score = (mcap ** 0.5) * 0.8 + purity * 2

            # 行业不匹配惩罚：非本行业股票评分大幅降低
            relevance_penalty = 1.0 if industry_match else 0.15
            composite_score = base_score * relevance_penalty

            filtered_stocks.append({
                'ts_code': ts_code,
                'name': stock_name,
                'mcap': mcap,
                'turnover': turnover,
                'purity': purity,
                'is_st': is_st,
                'theme_name': theme_name,
                'composite_score': composite_score,
                'industry_match': industry_match
            })

        filtered_stocks.sort(key=lambda x: x['composite_score'], reverse=True)

        selected_stocks = []

        if filtered_stocks:
            # 只选择核心成份股：按综合评分排序，取前30只
            core_stocks = filtered_stocks[:30]
            for s in core_stocks:
                s['layer'] = 'core'
            
            selected_stocks = core_stocks

            print(f"   筛选结果: 核心成份股 {len(selected_stocks)} 只")
            print("   核心成份股:", [s['name'] for s in selected_stocks])

        portfolio.extend(selected_stocks)

    return portfolio

# =========================
# 保存结果
# =========================
def save_portfolio_to_sqlite(portfolio, hot_themes):
    if not portfolio:
        print("\n未生成有效投资组合")
        return

    db_path = os.path.join(CACHE_DIR, "theme_portfolio.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('DROP TABLE IF EXISTS themes')
    cursor.execute('DROP TABLE IF EXISTS portfolio')

    cursor.execute('''CREATE TABLE themes (id INTEGER PRIMARY KEY, theme_name TEXT UNIQUE, industry TEXT, keywords TEXT)''')
    cursor.execute('''CREATE TABLE portfolio (id INTEGER PRIMARY KEY, ts_code TEXT, name TEXT, theme_name TEXT, layer TEXT, mcap REAL, turnover REAL, purity INTEGER, trade_date TEXT)''')

    for theme_name, theme_data in hot_themes.items():
        industry = ','.join(theme_data.get('industry', []))
        keywords = ','.join(theme_data.get('keywords', []))
        cursor.execute('INSERT OR REPLACE INTO themes (theme_name, industry, keywords) VALUES (?, ?, ?)',
                       (theme_name, industry, keywords))

    for stock in portfolio:
        cursor.execute(
            'INSERT INTO portfolio (ts_code, name, theme_name, layer, mcap, turnover, purity, trade_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (stock.get('ts_code', ''), stock.get('name', ''), stock.get('theme_name', ''),
             stock.get('layer', ''), stock.get('mcap', 0), stock.get('turnover', 0),
             stock.get('purity', 0), TRADE_DATE))

    conn.commit()
    conn.close()
    print(f"\n投资组合已保存到数据库: {db_path}")

# =========================
# 主函数
# =========================
def main():
    print("=" * 60)
    print("主题投资组合策略分析 - 东方财富概念版")
    print("=" * 60)

    hot_themes = load_hot_themes()
    concept_map, name_map, stock_concepts = get_concept_and_stock_info()

    print("\n[2/5] 获取股票基础信息...")
    stock_list_df = cached_stock_basic()

    print("\n[3/5] 获取市场数据...")
    market_cap_df = cached_daily_basic(TRADE_DATE)

    portfolio = build_theme_portfolio(hot_themes, concept_map, name_map, stock_concepts, market_cap_df, stock_list_df)

    save_portfolio_to_sqlite(portfolio, hot_themes)

    print("\n" + "=" * 60)
    print("主题投资组合分析完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
