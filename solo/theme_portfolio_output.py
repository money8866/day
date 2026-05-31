#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题投资组合策略分析 - 输出主题和成分股
使用 theme_portfolio_strategy_cached.py 的核心逻辑
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(os.path.dirname(BASE_DIR), "config", ".env")
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

TRADE_DATE = datetime.now().strftime('%Y%m%d')


class CacheManager:
    """通用缓存管理器"""
    
    def __init__(self, cache_dir, expire_minutes=240):
        self.cache_dir = cache_dir
        self.expire_minutes = expire_minutes
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_key(self, func_name, **kwargs):
        key_parts = [func_name]
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}_{v}")
        return "_".join(key_parts)
    
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
                
                cache_time = cache_data.get('timestamp', 0)
                current_time = time.time()
                
                if current_time - cache_time < self.expire_minutes * 60:
                    return cache_data.get('data')
            except:
                pass
        
        return None
    
    def set(self, func_name, data, **kwargs):
        cache_key = self._get_cache_key(func_name, **kwargs)
        cache_file = self._get_cache_file(cache_key)
        
        cache_data = {
            'timestamp': time.time(),
            'data': data
        }
        
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception as e:
            print(f"   缓存保存失败: {e}")

cache_manager = CacheManager(CACHE_DIR, expire_minutes=240)


def load_hot_themes():
    """从theme.json加载热点主题定义"""
    json_path = os.path.join(BASE_DIR, "theme.json")
    
    if not os.path.exists(json_path):
        print(f"警告: 未找到 {json_path}")
        return get_default_themes()
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        themes = data.get('HOT_THEMES', {})
        
        if not themes:
            print("警告: JSON文件中HOT_THEMES为空")
            return get_default_themes()
        
        print(f"✓ 从JSON加载了 {len(themes)} 个热点主题")
        return themes
        
    except Exception as e:
        print(f"加载JSON失败: {e}")
        return get_default_themes()


def get_default_themes():
    """默认热点主题定义"""
    return {
        "AI芯片": {
            "industry": ["半导体", "计算机设备", "通信设备"],
            "concept": ["AI芯片", "算力", "GPU"],
            "keywords": ["人工智能", "AI芯片", "大模型", "GPU", "算力"]
        },
        "半导体国产替代": {
            "industry": ["半导体", "电子化学品"],
            "concept": ["半导体", "芯片"],
            "keywords": ["半导体", "芯片", "集成电路", "国产替代"]
        },
        "AI算力": {
            "industry": ["通信设备", "计算机设备"],
            "concept": ["算力", "CPO", "光模块"],
            "keywords": ["算力", "光模块", "CPO", "数据中心"]
        },
        "人形机器人": {
            "industry": ["自动化设备", "专用设备"],
            "concept": ["机器人"],
            "keywords": ["机器人", "人形机器人", "工业自动化"]
        },
        "存储芯片": {
            "industry": ["半导体"],
            "concept": ["存储芯片"],
            "keywords": ["存储芯片", "内存", "闪存", "DRAM"]
        },
        "新能源": {
            "industry": ["电力设备", "汽车整车", "电池"],
            "concept": ["新能源车", "光伏", "储能"],
            "keywords": ["新能源车", "光伏", "储能"]
        }
    }


def get_concept_map():
    """获取东财概念板块映射"""
    print("\n[1/3] 加载东财概念板块映射...")
    
    cache_file = os.path.join(CACHE_DIR, "dc_concept_members.pkl")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 条记录")
                return build_maps_from_df(df)
        except Exception as e:
            print(f"   缓存读取失败: {e}")
    
    print("   缓存不存在，调用Tushare API获取东财概念板块数据...")
    
    try:
        concept_df = pro.dc_index()
        time.sleep(0.1)
        
        if concept_df.empty:
            print("   未能获取到东财概念板块列表")
            return {}, {}
        
        concept_df = concept_df[concept_df['idx_type'] == '概念板块']
        print(f"   共找到 {len(concept_df)} 个概念板块")
        
        all_concept_members = []
        
        for _, row in concept_df.iterrows():
            concept_code = row['ts_code']
            concept_name = row['name']
            
            try:
                members = pro.dc_member(index_code=concept_code)
                if not members.empty:
                    members['concept_name'] = concept_name
                    all_concept_members.append(members)
                time.sleep(0.05)
            except Exception as e:
                continue
        
        if all_concept_members:
            df = pd.concat(all_concept_members, ignore_index=True)
            df.to_pickle(cache_file)
            print(f"   成功获取 {len(df)} 条概念成份股记录")
            
            return build_maps_from_df(df)
        else:
            print("   未能获取到概念板块数据")
    
    except Exception as e:
        print(f"   调用Tushare API失败: {e}")
    
    return {}, {}


def is_performance_sector(sector_name):
    performance_keywords = [
        '年报', '一季报', '半年报', '三季报', '季报',
        '扭亏', '预增', '预减', '预盈', '预亏', '业绩', '中报'
    ]
    for keyword in performance_keywords:
        if keyword in sector_name:
            return True
    return False


def build_maps_from_df(df):
    concept_map = {}
    name_map = {}
    
    print(f"   正在构建映射，共 {df['concept_name'].nunique()} 个板块...")
    
    for concept_name, group in df.groupby('concept_name'):
        if is_performance_sector(concept_name):
            continue
        
        stocks = group['con_code'].unique().tolist()
        if len(stocks) >= 5:
            concept_map[concept_name] = stocks
        
        for _, row in group.drop_duplicates('con_code').iterrows():
            stock_code = row['con_code']
            stock_name = row.get('name', stock_code)
            if stock_code not in name_map:
                name_map[stock_code] = stock_name
    
    print(f"   找到 {len(concept_map)} 个有效概念板块，{len(name_map)} 只股票")
    
    return concept_map, name_map


def get_stock_list():
    """获取股票基础信息"""
    print("\n[2/3] 获取股票基础信息...")
    
    func_name = "stock_basic"
    cached_data = cache_manager.get(func_name)
    if cached_data is not None:
        print(f"   从缓存获取股票基础信息")
        return cached_data
    
    print(f"   调用Tushare API: stock_basic")
    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,industry,list_date'
    )
    time.sleep(0.1)
    
    cache_manager.set(func_name, df)
    print(f"   获取到 {len(df)} 只股票信息")
    return df


def match_theme_sectors(concept_map, stock_list_df, hot_themes):
    """匹配热点主题板块"""
    print("\n[3/3] 匹配热点主题板块...")
    
    theme_sectors = {}
    
    stock_industry = {}
    stock_name_dict = {}
    if not stock_list_df.empty:
        for _, row in stock_list_df.iterrows():
            stock_industry[row['ts_code']] = row.get('industry', '')
            stock_name_dict[row['ts_code']] = row.get('name', '')
    
    for theme_name, theme_config in hot_themes.items():
        matched_sectors = []
        matched_stocks = set()
        
        industries = theme_config.get('industry', [])
        concepts = theme_config.get('concept', [])
        keywords = theme_config.get('keywords', [])
        exclude_keywords = theme_config.get('exclude_keywords', [])
        core_companies = theme_config.get('core_companies', [])
        
        # 通过concept匹配概念板块
        for concept_name in concepts:
            for sector_name in concept_map.keys():
                if concept_name in sector_name:
                    matched_sectors.append(sector_name)
                    matched_stocks.update(concept_map[sector_name])
        
        # 通过keywords匹配概念板块
        for sector_name in concept_map.keys():
            if sector_name not in matched_sectors:
                for keyword in keywords:
                    if keyword in sector_name:
                        exclude = False
                        for exclude_keyword in exclude_keywords:
                            if exclude_keyword in sector_name:
                                exclude = True
                                break
                        if not exclude:
                            matched_sectors.append(sector_name)
                            matched_stocks.update(concept_map[sector_name])
                            break
        
        # 通过industry匹配股票
        if industries:
            for ts_code, industry in stock_industry.items():
                if industry in industries:
                    matched_stocks.add(ts_code)
        
        # 添加核心公司
        if core_companies:
            for company_name in core_companies:
                for ts_code, name in stock_name_dict.items():
                    if name == company_name:
                        matched_stocks.add(ts_code)
                        break
        
        if matched_sectors or matched_stocks:
            theme_sectors[theme_name] = {
                'sectors': matched_sectors,
                'stocks': list(matched_stocks),
                'core_companies': core_companies,
                'etf': theme_config.get('etf', '')
            }
            print(f"   {theme_name}: {len(matched_sectors)}个板块, {len(matched_stocks)}只股票")
        else:
            print(f"   {theme_name}: 未匹配到")
    
    return theme_sectors


def main():
    """主函数"""
    print("="*80)
    print("🚀 主题投资组合分析 - 输出主题和成分股")
    print("="*80)
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. 加载热点主题
    hot_themes = load_hot_themes()
    
    # 2. 获取概念板块映射
    concept_map, name_map = get_concept_map()
    
    # 3. 获取股票列表
    stock_list_df = get_stock_list()
    
    # 4. 匹配主题板块
    theme_sectors = match_theme_sectors(concept_map, stock_list_df, hot_themes)
    
    # 5. 输出结果
    print("\n" + "="*80)
    print("📊 主题和成分股汇总")
    print("="*80)
    
    for theme_name, theme_data in theme_sectors.items():
        print(f"\n\n{'='*60}")
        print(f"🏷️ 主题: {theme_name}")
        print(f"{'='*60}")
        
        if theme_data.get('etf'):
            print(f"📈 对应ETF: {theme_data['etf']}")
        
        print(f"\n📋 关联板块 ({len(theme_data['sectors'])}个):")
        for i, sector in enumerate(theme_data['sectors'], 1):
            print(f"   {i}. {sector}")
        
        print(f"\n🔬 核心公司 ({len(theme_data.get('core_companies', []))}个):")
        for i, company in enumerate(theme_data.get('core_companies', []), 1):
            print(f"   {i}. {company}")
        
        print(f"\n📝 成分股列表 ({len(theme_data['stocks'])}只):")
        stock_names = []
        for ts_code in theme_data['stocks'][:20]:
            stock_names.append(name_map.get(ts_code, ts_code))
        
        print(f"   {', '.join(stock_names)}")
        if len(theme_data['stocks']) > 20:
            print(f"   ...等共 {len(theme_data['stocks'])} 只股票")
    
    print("\n\n" + "="*80)
    print("✅ 分析完成")
    print("="*80)


if __name__ == "__main__":
    main()
