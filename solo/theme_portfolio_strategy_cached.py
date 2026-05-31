#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题投资组合策略分析 - 缓存增强版（简化版）
根据申万行业和东财概念，筛选热点主题，选取股票
支持SQLite数据库存储
"""
import os
import sys
import json
import pickle
import warnings
import time
import glob
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
    
    theme_mtime = os.path.getmtime(json_path)
    theme_record_file = os.path.join(CACHE_DIR, "theme_mtime.txt")
    need_clear_cache = False
    if os.path.exists(theme_record_file):
        try:
            with open(theme_record_file, 'r') as f:
                if float(f.read().strip()) < theme_mtime:
                    need_clear_cache = True
                    print("检测到 theme.json 已更新，清除相关缓存...")
        except:
            need_clear_cache = True
    else:
        need_clear_cache = True
    
    if need_clear_cache:
        #for f in glob.glob(os.path.join(CACHE_DIR, "ths_concept_*.pkl")):
        ##    try: os.remove(f) 
         #   except: pass
        with open(theme_record_file, 'w') as f:
            f.write(str(theme_mtime))
    
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
# 批量获取日线数据
# =========================
def batch_get_daily_data(ts_codes, trade_date):
    if not ts_codes:
        return pd.DataFrame()
    func_name = "batch_daily"
    cache_key = f"batch_{trade_date}_{len(ts_codes)}"
    cached = cache_manager.get(func_name, key=cache_key)
    if cached is not None:
        return cached

    print(f"   批量获取 {len(ts_codes)} 只股票的日线数据...")
    end_date = trade_date
    start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=20)).strftime('%Y%m%d')
    all_dfs = []

    for i in range(0, len(ts_codes), 100):
        batch_codes = ts_codes[i:i+100]
        try:
            df = pro.daily(ts_code=','.join(batch_codes), start_date=start_date, end_date=end_date)
            if not df.empty:
                all_dfs.append(df)
            time.sleep(0.2)
        except:
            pass

    if all_dfs:
        result = pd.concat(all_dfs, ignore_index=True)
        result = result.drop_duplicates(subset=['ts_code', 'trade_date'])
        cache_manager.set(func_name, result, key=cache_key)
        return result
    return pd.DataFrame()

# =========================
# 获取概念板块映射
# =========================
def get_concept_and_stock_info():
    print("\n[1/5] 加载同花顺概念板块映射...")
    cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 条记录")
                return build_maps_from_df(df)
        except:
            pass
    
    print("   正在调用Tushare API获取同花顺概念板块数据...")
    try:
        concept_df = pro.ths_index(exchange="A", type="N")
        time.sleep(0.1)
        if concept_df.empty:
            return {}, {}, pd.DataFrame()
        print(f"   共找到 {len(concept_df)} 个概念板块")
        
        all_members = []
        for idx, row in concept_df.iterrows():
            try:
                members = pro.ths_member(ts_code=row['ts_code'])
                if not members.empty:
                    members['concept_name'] = row['name']
                    all_members.append(members)
                time.sleep(0.05)
            except:
                continue
            if idx % 100 == 0:
                print(f"   已处理 {idx+1}/{len(concept_df)} 个概念板块")
        
        if all_members:
            df = pd.concat(all_members, ignore_index=True)
            df.to_pickle(cache_file)
            print(f"   成功获取 {len(df)} 条概念成份股记录")
            return build_maps_from_df(df)
    except Exception as e:
        print(f"调用Tushare API失败: {e}")
    return {}, {}, pd.DataFrame()

def build_maps_from_df(df):
    concept_map = {}
    name_map = {}
    stock_concepts = {}
    
    for _, row in df.iterrows():
        ts_code = row['con_code']
        concept_name = row['concept_name']
        stock_name = row.get('con_name', '')
        
        if concept_name not in concept_map:
            concept_map[concept_name] = set()
        concept_map[concept_name].add(ts_code)
        
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

def cached_sw_industry():
    func_name = "sw_industry"
    cached = cache_manager.get(func_name)
    if cached is not None:
        print(f"   从缓存获取申万行业数据")
        return cached
    print(f"   调用Tushare API: index_member_all")
    df = pro.index_member_all(is_new='Y')
    time.sleep(0.2)
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
    
    # 构建股票行业映射（来自 stock_basic，L1行业）
    stock_industry_dict = {}
    if not stock_list_df.empty:
        for _, row in stock_list_df.iterrows():
            stock_industry_dict[row['ts_code']] = row.get('industry', '')
    
    # 加载申万三级行业数据（用于精准 L1/L2/L3 行业匹配）
    sw_df = cached_sw_industry()
    stock_sw_l1 = {}
    stock_sw_l2 = {}
    stock_sw_l3 = {}
    if not sw_df.empty:
        for _, row in sw_df.iterrows():
            ts_code = row['ts_code']
            stock_sw_l1[ts_code] = row.get('l1_name', '')
            stock_sw_l2[ts_code] = row.get('l2_name', '')
            stock_sw_l3[ts_code] = row.get('l3_name', '')
    
    # 收集所有需要处理的股票代码
    all_theme_stocks = set()
    theme_stock_map = {}
    
    for theme_name, theme_data in hot_themes.items():
        industry_list = theme_data.get('industry', [])
        concept_list = theme_data.get('concept', [])
        
        matched_stocks = set()
        
        # 按行业匹配（同时匹配 L1/L2/L3 行业名称）
        all_ts_codes = set(stock_industry_dict.keys()) | set(stock_sw_l1.keys())
        for ts_code in all_ts_codes:
            ind_l1 = stock_sw_l1.get(ts_code, '')
            ind_l2 = stock_sw_l2.get(ts_code, '')
            ind_l3 = stock_sw_l3.get(ts_code, '')
            ind_basic = stock_industry_dict.get(ts_code, '')
            # 检查行业是否匹配 L1/L2/L3 或 stock_basic
            for ind in [ind_l1, ind_l2, ind_l3, ind_basic]:
                if ind in industry_list:
                    matched_stocks.add(ts_code)
                    break
        
        # 按概念匹配（仅精确匹配 concept 列表，keywords 仅用于纯度评分）
        for ts_code, concepts in stock_concepts.items():
            for c in concept_list:
                if c in concepts:
                    matched_stocks.add(ts_code)
                    break
        
        theme_stock_map[theme_name] = matched_stocks
        all_theme_stocks.update(matched_stocks)
        
        print(f"   主题 '{theme_name}': 匹配 {len(matched_stocks)} 只股票")
    
    # 批量获取所有股票的日线数据
    print(f"\n预加载 {len(all_theme_stocks)} 只股票的技术数据...")
    daily_df = batch_get_daily_data(list(all_theme_stocks), TRADE_DATE)
    print(f"已加载 {len(daily_df)} 条日线记录")

    # 预计算股票的成交额、波动率和趋势
    stock_amount_dict = {}
    stock_volatility_dict = {}
    stock_trend_dict = {}

    if not daily_df.empty:
        for ts_code, group in daily_df.groupby('ts_code'):
            group = group.sort_values('trade_date', ascending=False)
            if len(group) >= 5:
                stock_amount_dict[ts_code] = group['amount'].head(5).mean() / 100000
                pct_chg = group['pct_chg'].head(10)
                stock_volatility_dict[ts_code] = pct_chg.std() if len(pct_chg) >= 5 else 0
                stock_trend_dict[ts_code] = (group['close'].iloc[0] / group['close'].iloc[4] - 1) * 100

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
            amount = stock_amount_dict.get(ts_code, 0)
            mcap = mv / 10000
            
            if amount < 0.3:
                continue
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
            
            # exclude_keywords 过滤（匹配概念名或股票名称）
            concepts = stock_concepts.get(ts_code, [])
            concepts_str = '|'.join(concepts)
            stock_name = name_map.get(ts_code, stock_info_dict.get(ts_code, {}).get('name', ts_code))
            skip = False
            for ek in exclude_keywords:
                if ek in concepts_str or ek in stock_name:
                    skip = True
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
            if industry in industry_list:
                purity += 1
            # 申万行业匹配加分
            if stock_sw_l1.get(ts_code, '') in industry_list or \
               stock_sw_l2.get(ts_code, '') in industry_list or \
               stock_sw_l3.get(ts_code, '') in industry_list:
                purity += 2
            
            trend = stock_trend_dict.get(ts_code, 0)
            volatility = stock_volatility_dict.get(ts_code, 0)
            mcap = mv / 10000
            
            # 行业相关性判定（SW L1/L2/L3 + stock_basic）
            sw_l1 = stock_sw_l1.get(ts_code, '')
            sw_l2 = stock_sw_l2.get(ts_code, '')
            sw_l3 = stock_sw_l3.get(ts_code, '')
            ind_basic = stock_industry_dict.get(ts_code, '')
            industry_match = sw_l1 in industry_list or sw_l2 in industry_list or sw_l3 in industry_list or ind_basic in industry_list
            
            # 基础评分：sqrt市值减少极端大市值碾压 + 趋势 + 流动性 + 题材纯度
            base_score = (mcap ** 0.5) * 0.8 + max(trend, 0) / 10 + turnover / 20 + purity * 2
            
            # 行业不匹配惩罚：非本行业股票评分大幅降低
            relevance_penalty = 1.0 if industry_match else 0.15
            composite_score = base_score * relevance_penalty
            
            filtered_stocks.append({
                'ts_code': ts_code,
                'name': stock_name,
                'mcap': mcap,
                'turnover': turnover,
                'amount': amount,
                'purity': purity,
                'trend': trend,
                'volatility': volatility,
                'is_st': is_st,
                'theme_name': theme_name,
                'composite_score': composite_score,
                'industry_match': industry_match
            })
        
        filtered_stocks.sort(key=lambda x: x['composite_score'], reverse=True)
        
        selected_stocks = []
        leader_count = 0
        core_count = 0
        follower_count = 0
        
        if filtered_stocks:
            # 龙头筛选：从top开始，跳过趋势下跌或纯度太低或行业不匹配的股票
            leader = None
            for candidate in filtered_stocks:
                if candidate['trend'] > -3 and candidate['purity'] >= 1 and candidate['industry_match']:
                    leader = candidate
                    break
            if leader is None:
                leader = filtered_stocks[0]
            leader['layer'] = 'leader'
            leader_count = 1
            
            remaining = filtered_stocks[1:]
            core_stocks = sorted(remaining, key=lambda x: (x['industry_match'], x['mcap'] + x['amount'] * 10), reverse=True)[:6]
            for s in core_stocks:
                s['layer'] = 'core'
            core_count = len(core_stocks)
            
            core_ts_codes = set(s['ts_code'] for s in core_stocks)
            remaining = [s for s in remaining if s['ts_code'] not in core_ts_codes]
            follower_stocks = sorted(remaining, key=lambda x: (x['industry_match'], x['composite_score']), reverse=True)[:20]
            for s in follower_stocks:
                s['layer'] = 'follower'
            follower_count = len(follower_stocks)
            
            selected_stocks = [leader] + core_stocks + follower_stocks
            
            print(f"   筛选结果: 龙头{leader_count}只, 核心{core_count}只, 跟随{follower_count}只, 共{len(selected_stocks)}只")
            print("   龙头:", leader['name'])
            print("   核心:", [s['name'] for s in core_stocks])
            print("   跟随:", [s['name'] for s in follower_stocks])
        
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
    cursor.execute('''CREATE TABLE portfolio (id INTEGER PRIMARY KEY, ts_code TEXT, name TEXT, theme_name TEXT, layer TEXT, mcap REAL, turnover REAL, amount REAL, purity INTEGER, trend REAL, volatility REAL, trade_date TEXT)''')
    
    for theme_name, theme_data in hot_themes.items():
        industry = ','.join(theme_data.get('industry', []))
        keywords = ','.join(theme_data.get('keywords', []))
        cursor.execute('INSERT OR REPLACE INTO themes (theme_name, industry, keywords) VALUES (?, ?, ?)', (theme_name, industry, keywords))
    
    for stock in portfolio:
        cursor.execute('INSERT INTO portfolio (ts_code, name, theme_name, layer, mcap, turnover, amount, purity, trend, volatility, trade_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (stock.get('ts_code', ''), stock.get('name', ''), stock.get('theme_name', ''), stock.get('layer', ''), stock.get('mcap', 0), stock.get('turnover', 0), stock.get('amount', 0), stock.get('purity', 0), stock.get('trend', 0), stock.get('volatility', 0), TRADE_DATE))
    
    conn.commit()
    conn.close()
    print(f"\n投资组合已保存到数据库: {db_path}")

# =========================
# 主函数
# =========================
def main():
    print("=" * 60)
    print("主题投资组合策略分析 - 简化版")
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
