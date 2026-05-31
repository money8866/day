#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题投资组合策略分析 - 缓存增强版
根据申万行业和东财概念，筛选热点主题，选取100亿以上机构股
支持SQLite数据库存储
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
print(BASE_DIR)
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
print(TUSHARE_TOKEN)

pro = ts.pro_api(TUSHARE_TOKEN)

# =========================
# 缓存管理器
# =========================
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

# =========================
# 双创股票识别
# =========================
def is_chuangye_stock(ts_code):
    """判断是否为创业板股票（300开头）"""
    if ts_code is None:
        return False
    code = ts_code.split('.')[0] if '.' in ts_code else ts_code
    return code.startswith('300')

def is_kechuang_stock(ts_code):
    """判断是否为科创板股票（688开头）"""
    if ts_code is None:
        return False
    code = ts_code.split('.')[0] if '.' in ts_code else ts_code
    return code.startswith('688')

def is_shuangchuang_stock(ts_code):
    """判断是否为双创股票（科创板或创业板）"""
    return is_chuangye_stock(ts_code) or is_kechuang_stock(ts_code)

def get_stock_market_type(ts_code):
    """获取股票市场类型"""
    if is_kechuang_stock(ts_code):
        return '科创板'
    elif is_chuangye_stock(ts_code):
        return '创业板'
    else:
        return '主板'

# =========================
# 行业细分龙头识别
# =========================
def is_industry_leader(stock_name):
    """通过股票名称识别行业龙头"""
    if stock_name is None:
        return False
    
    leader_keywords = [
        '龙头', '细分龙头', '行业龙头', '全球领先', 
        '国内领先', '龙头企业', '龙头股', '核心龙头',
        '第一股', '龙头标的', '标杆企业', '领军企业'
    ]
    
    for keyword in leader_keywords:
        if keyword in stock_name:
            return True
    return False

# =========================
# 负面事件筛选
# =========================
def check_negative_events(ts_code):
    """检查股票是否有负面事件"""
    func_name = "negative_events"
    cached_data = cache_manager.get(func_name, ts_code=ts_code)
    if cached_data is not None:
        return cached_data
    
    results = {
        'has_st': False,
        'has_suspended': False,
        'has_limit_down': False
    }
    
    # 检查ST
    try:
        df = pro.namechange(ts_code=ts_code, fields='ts_code,name,change_reason,ann_date')
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                reason = str(row.get('change_reason', ''))
                if 'ST' in reason or '*ST' in reason:
                    results['has_st'] = True
                if '暂停上市' in reason:
                    results['has_suspended'] = True
    except:
        pass
    
    # 检查跌停
    try:
        limit_df = pro.limit_list_d(trade_date=TRADE_DATE)
        if limit_df is not None and not limit_df.empty:
            if ts_code in limit_df['ts_code'].values:
                results['has_limit_down'] = True
    except:
        pass
    
    cache_manager.set(func_name, results, ts_code=ts_code)
    return results

def is_negative_stock(ts_code):
    """判断股票是否为负面股票"""
    events = check_negative_events(ts_code)
    if events is None:
        return False
    return events.get('has_st', False) or events.get('has_suspended', False)

# =========================
# 加载热点主题定义（从JSON文件）
# =========================
def load_hot_themes():
    """从theme.json加载热点主题定义"""
    import json
    
    json_path = os.path.join(BASE_DIR, "theme.json")
    
    if not os.path.exists(json_path):
        print(f"警告: 未找到 {json_path}，使用默认主题定义")
        return get_default_themes()
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        themes = data.get('HOT_THEMES', {})
        
        if not themes:
            print("警告: JSON文件中HOT_THEMES为空，使用默认主题")
            return get_default_themes()
        
        print(f"✓ 从JSON加载了 {len(themes)} 个热点主题")
        return themes
        
    except Exception as e:
        print(f"加载JSON失败: {e}，使用默认主题定义")
        return get_default_themes()

def get_default_themes():
    """默认热点主题定义"""
    return {
        "AI芯片": {
            "industry": ["半导体", "计算机设备", "通信设备"],
            "keywords": ["人工智能", "AI芯片", "ChatGPT", "大模型", "GPU", "算力", "英伟达概念"]
        },
        "半导体国产替代": {
            "industry": ["半导体", "电子化学品"],
            "keywords": ["半导体", "芯片", "集成电路", "IC", "国产替代", "EDA", "光刻机", "晶圆"]
        },
        "新能源": {
            "industry": ["电力设备", "汽车整车", "电池"],
            "keywords": ["新能源车", "新能源", "锂电池", "宁德时代概念", "光伏", "储能", "氢能源"]
        },
        "高端制造": {
            "industry": ["自动化设备", "专用设备", "通用设备"],
            "keywords": ["高端制造", "工业母机", "机器人", "智能制造", "工业4.0"]
        },
        "数字经济": {
            "industry": ["IT服务", "软件开发", "互联网服务"],
            "keywords": ["数字经济", "信创", "云计算", "大数据", "数据要素"]
        },
        "消费复苏": {
            "industry": ["食品饮料", "旅游酒店", "零售", "家电"],
            "keywords": ["消费", "白酒", "食品饮料", "旅游", "免税", "消费电子"]
        },
        "医药生物": {
            "industry": ["化学制药", "生物制品", "医疗器械"],
            "keywords": ["医药", "生物医药", "创新药", "医疗器械", "CXO"]
        },
        "金融科技": {
            "industry": ["证券", "银行", "保险", "IT服务"],
            "keywords": ["金融科技", "银行", "证券", "保险", "区块链", "数字货币"]
        },
        "新能源材料": {
            "industry": ["能源金属", "小金属", "工业金属"],
            "keywords": ["锂矿", "稀土永磁", "有色金属", "新材料"]
        },
        "军工": {
            "industry": ["军工电子", "航空装备", "船舶制造"],
            "keywords": ["军工", "国防军工", "军民融合", "航空航天"]
        }
    }

# =========================
# 获取最近交易日
# =========================
def get_last_trade_date():
    now = datetime.now()
    
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    func_name = "get_last_trade_date"
    cached_data = cache_manager.get(func_name, query_date=query_date)
    if cached_data:
        return cached_data
    
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    result = str(last_trade_date)
    
    cache_manager.set(func_name, result, query_date=query_date)
    return result

TRADE_DATE = get_last_trade_date()
print("当前交易日:", TRADE_DATE)

# =========================
# 多因子评分模型
# =========================
def calculate_technical_factor(ts_code, recent_data):
    """技术面因子计算 (40%)"""
    score = 0
    
    # 5日涨幅因子 (20分)
    change_5 = recent_data.get('change_5', 0)
    if change_5 > 20:
        score += 20
    elif change_5 > 10:
        score += 18
    elif change_5 > 5:
        score += 15
    elif change_5 > 0:
        score += 10
    elif change_5 > -5:
        score += 5
    else:
        score += 0
    
    # 20日涨幅因子 (15分)
    change_20 = recent_data.get('change_20', 0)
    if change_20 > 50:
        score += 15
    elif change_20 > 30:
        score += 12
    elif change_20 > 15:
        score += 10
    elif change_20 > 0:
        score += 8
    elif change_20 > -10:
        score += 4
    else:
        score += 0
    
    # 5日乖离率因子 (5分)
    ma5_biased = recent_data.get('ma5_biased', 0)
    if 3 >= ma5_biased >= -3:
        score += 5
    elif 5 >= ma5_biased >= -5:
        score += 3
    else:
        score += 1
    
    return score

def calculate_fundamental_factor(ts_code, market_data):
    """基本面因子计算 (40%)"""
    score = 0
    
    # 市值因子 (20分)
    market_cap = market_data.get('market_cap', 0)
    if market_cap >= 5000:
        score += 20
    elif market_cap >= 2000:
        score += 18
    elif market_cap >= 1000:
        score += 15
    elif market_cap >= 500:
        score += 12
    elif market_cap >= 200:
        score += 8
    elif market_cap >= 100:
        score += 5
    else:
        score += 2
    
    # 估值因子 (PE) (10分)
    pe = market_data.get('pe', 0)
    if pe is None or pe <= 0:
        score += 5
    elif pe <= 20:
        score += 10
    elif pe <= 50:
        score += 8
    elif pe <= 100:
        score += 5
    else:
        score += 2
    
    # 估值因子 (PB) (5分)
    pb = market_data.get('pb', 0)
    if pb is None or pb <= 0:
        score += 2
    elif pb <= 3:
        score += 5
    elif pb <= 5:
        score += 4
    elif pb <= 10:
        score += 2
    else:
        score += 1
    
    # 流动性因子 (5分)
    turnover = market_data.get('turnover_rate', 0)
    if 2 <= turnover <= 10:
        score += 5
    elif 1 <= turnover <= 15:
        score += 4
    elif 0.5 <= turnover <= 20:
        score += 3
    else:
        score += 1
    
    return score

def calculate_theme_relevance_factor(ts_code, theme_name, related_sectors, concept_map, core_companies):
    """主题相关性因子计算 (20%)"""
    score = 0
    
    # 核心公司因子 (10分)
    if core_companies:
        for company in core_companies:
            if company in related_sectors:
                score += 10
                break
    
    # 关联板块数量因子 (10分)
    sector_count = len(related_sectors)
    if sector_count >= 3:
        score += 10
    elif sector_count >= 2:
        score += 8
    elif sector_count >= 1:
        score += 6
    else:
        score += 4
    
    return score

def calculate_stock_multi_factor_score(
    ts_code, 
    market_data, 
    recent_data, 
    theme_name, 
    related_sectors, 
    concept_map, 
    core_companies,
    stock_name=None
):
    """多因子综合评分计算（增加双创弹性和龙头加分）"""
    
    # 技术面 (40%)
    technical_score = calculate_technical_factor(ts_code, recent_data)
    
    # 基本面 (40%)
    fundamental_score = calculate_fundamental_factor(ts_code, market_data)
    
    # 主题相关性 (20%)
    relevance_score = calculate_theme_relevance_factor(
        ts_code, theme_name, related_sectors, concept_map, core_companies
    )
    
    # 双创股票弹性加分
    elasticity_bonus = 0
    if is_kechuang_stock(ts_code):
        elasticity_bonus = 15
    elif is_chuangye_stock(ts_code):
        elasticity_bonus = 12
    
    # 行业龙头加分
    leader_bonus = 0
    if stock_name and is_industry_leader(stock_name):
        leader_bonus = 10
    
    total_score = technical_score + fundamental_score + relevance_score + elasticity_bonus + leader_bonus
    
    return {
        'total_score': total_score,
        'technical_score': technical_score,
        'fundamental_score': fundamental_score,
        'relevance_score': relevance_score,
        'elasticity_bonus': elasticity_bonus,
        'leader_bonus': leader_bonus,
        'market_type': get_stock_market_type(ts_code),
        'is_leader': is_industry_leader(stock_name) if stock_name else False
    }

def get_stock_recent_data(ts_code, trade_date):
    """获取股票近期技术数据"""
    func_name = "stock_recent"
    cache_key = f"{ts_code}_{trade_date}"
    cached_data = cache_manager.get(func_name, key=cache_key)
    if cached_data is not None:
        return cached_data
    
    try:
        end_date = trade_date
        start_date = (pd.to_datetime(trade_date) - pd.Timedelta(days=40)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        time.sleep(0.02)
        
        if df.empty or len(df) < 5:
            return {}
        
        df = df.sort_values('trade_date', ascending=False).reset_index(drop=True)
        
        # 计算指标
        close_price = df['close'].iloc[0]
        ma5 = df['close'].head(5).mean()
        ma10 = df['close'].head(10).mean()
        ma20 = df['close'].head(20).mean()
        
        # 避免除零
        change_5 = ((close_price / df['close'].iloc[5] - 1) * 100) if len(df) > 5 else 0
        change_20 = ((close_price / df['close'].iloc[min(20, len(df)-1)] - 1) * 100) if len(df) > 20 else 0
        ma5_biased = ((close_price / ma5 - 1) * 100) if ma5 > 0 else 0
        
        recent_data = {
            'change_5': change_5,
            'change_20': change_20,
            'ma5_biased': ma5_biased,
            'ma10_biased': ((close_price / ma10 - 1) * 100) if ma10 > 0 else 0,
            'ma20_biased': ((close_price / ma20 - 1) * 100) if ma20 > 0 else 0
        }
        
        cache_manager.set(func_name, recent_data, key=cache_key)
        return recent_data
    except:
        return {}

# =========================
# 缓存的API调用
# =========================
def cached_trade_cal(start_date, end_date):
    func_name = "trade_cal"
    cached_data = cache_manager.get(func_name, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        print(f"   从缓存获取交易日历")
        return cached_data
    
    print(f"   调用Tushare API: trade_cal")
    df = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    time.sleep(0.1)
    
    cache_manager.set(func_name, df, start_date=start_date, end_date=end_date)
    return df

def cached_daily(ts_code, start_date, end_date):
    func_name = "daily"
    cached_data = cache_manager.get(func_name, ts_code=ts_code, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        return cached_data
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        time.sleep(0.02)
        cache_manager.set(func_name, df, ts_code=ts_code, start_date=start_date, end_date=end_date)
        return df
    except:
        return pd.DataFrame()

def cached_daily_basic(trade_date):
    func_name = "daily_basic"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        print(f"   从缓存获取每日基础数据: {trade_date}")
        return cached_data
    
    print(f"   调用Tushare API: daily_basic")
    df = pro.daily_basic(
        trade_date=trade_date,
        fields='ts_code,total_mv,circ_mv,turnover_rate,pe,pb'
    )
    time.sleep(0.1)
    
    cache_manager.set(func_name, df, trade_date=trade_date)
    return df

def cached_stock_basic():
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
    return df

# =========================
# 获取概念板块映射（同花顺）
# =========================
def get_concept_map():
    print("\n[1/5] 加载同花顺概念板块映射...")

    cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")

    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从缓存加载成功，共 {len(df)} 条记录")
                return build_maps_from_df(df)
        except Exception as e:
            print(f"   缓存读取失败: {e}")

    print("   缓存不存在，正在调用Tushare API获取同花顺概念板块数据...")

    try:
        print("   获取同花顺概念板块列表...")
        concept_df = pro.ths_index(
            ts_code="",
            exchange="A",
            type="N",
            name="",
            limit="",
            offset=""
        )
        time.sleep(0.1)

        if concept_df.empty:
            print("   未能获取到同花顺概念板块列表")
            return {}, {}

        print(f"   共找到 {len(concept_df)} 个概念板块")

        all_concept_members = []

        for idx, row in concept_df.iterrows():
            concept_code = row['ts_code']
            concept_name = row['name']

            try:
                members = pro.ths_member(
                    ts_code=concept_code,
                    con_code="",
                    offset="",
                    limit=""
                )
                if not members.empty:
                    members['concept_name'] = concept_name
                    all_concept_members.append(members)
                time.sleep(0.05)
            except Exception as e:
                print(f"   获取概念 {concept_name} 成份股失败: {e}")
                continue

            if idx % 100 == 0:
                print(f"   已处理 {idx+1}/{len(concept_df)} 个概念板块")

        if all_concept_members:
            df = pd.concat(all_concept_members, ignore_index=True)

            df.to_pickle(cache_file)
            print(f"   成功获取 {len(df)} 条概念成份股记录，已保存到缓存")

            return build_maps_from_df(df)
        else:
            print("   未能获取到概念板块数据")

    except Exception as e:
        print(f"   调用Tushare API失败: {e}")

    return {}, {}

def is_performance_sector(sector_name):
    performance_keywords = [
        '年报', '一季报', '半年报', '三季报', '季报',
        '扭亏', '预增', '预减', '预盈', '预亏',
        '业绩', '中报'
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
        if len(stocks) >= 10:
            concept_map[concept_name] = stocks

        for _, row in group.drop_duplicates('con_code').iterrows():
            stock_code = row['con_code']
            stock_name = row['con_name']
            if stock_code not in name_map:
                name_map[stock_code] = stock_name

    print(f"   找到 {len(concept_map)} 个有效概念板块，{len(name_map)} 只股票")

    return concept_map, name_map

# =========================
# 匹配主题板块（支持industry和keywords）
# =========================
def match_theme_sectors(concept_map, stock_list_df, hot_themes):
    print("\n[2/5] 匹配热点主题板块...")
    
    theme_sectors = {}
    
    # 构建股票行业映射
    stock_industry = {}
    stock_name_dict = {}
    if not stock_list_df.empty:
        for _, row in stock_list_df.iterrows():
            stock_industry[row['ts_code']] = row.get('industry', '')
            stock_name_dict[row['ts_code']] = row.get('name', '')
    
    for theme_name, theme_config in hot_themes.items():
        matched_sectors = []
        matched_stocks = set()
        
        # 获取配置
        industries = theme_config.get('industry', [])
        concepts = theme_config.get('concept', [])
        keywords = theme_config.get('keywords', [])
        exclude_keywords = theme_config.get('exclude_keywords', [])
        core_companies = theme_config.get('core_companies', [])
        
        # 1. 通过concept匹配概念板块（优先级最高）
        for concept_name in concepts:
            for sector_name in concept_map.keys():
                if concept_name in sector_name:
                    matched_sectors.append(sector_name)
                    matched_stocks.update(concept_map[sector_name])
        
        # 2. 通过keywords匹配概念板块
        for sector_name in concept_map.keys():
            if sector_name not in matched_sectors:
                for keyword in keywords:
                    if keyword in sector_name:
                        # 检查排除关键词
                        exclude = False
                        for exclude_keyword in exclude_keywords:
                            if exclude_keyword in sector_name:
                                exclude = True
                                break
                        if not exclude:
                            matched_sectors.append(sector_name)
                            matched_stocks.update(concept_map[sector_name])
                            break
        
        # 3. 通过industry匹配股票
        if industries:
            for ts_code, industry in stock_industry.items():
                if industry in industries:
                    matched_stocks.add(ts_code)
        
        # 4. 添加核心公司
        if core_companies:
            for company_name in core_companies:
                # 根据公司名称查找股票代码
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
            print(f"   {theme_name}: 匹配到 {len(matched_sectors)} 个板块, {len(matched_stocks)} 只股票")
        else:
            print(f"   {theme_name}: 未匹配到")
    
    return theme_sectors

# =========================
# 获取市值数据（使用缓存）
# =========================
def get_market_cap_data():
    print("\n[3/5] 获取市值数据...")
    
    # 使用缓存获取市值数据
    daily_basic = cached_daily_basic(TRADE_DATE)
    
    if daily_basic is not None and not daily_basic.empty:
        print(f"   获取到 {len(daily_basic)} 只股票的市值数据")
        return daily_basic
    else:
        print(f"   未获取到市值数据，尝试获取前一交易日")
        cal = pro.trade_cal(exchange='', start_date='20250101', end_date=TRADE_DATE)
        cal = cal[cal['is_open'] == 1].sort_values('cal_date', ascending=False)
        
        for date in cal['cal_date'].head(5):
            daily_basic = pro.daily_basic(
                trade_date=date,
                fields='ts_code,total_mv,circ_mv,turnover_rate,pe,pb'
            )
            if daily_basic is not None and not daily_basic.empty:
                print(f"   使用 {date} 数据，共 {len(daily_basic)} 只")
                return daily_basic
    
    return pd.DataFrame()

# =========================
# 获取股票基础信息（使用缓存）
# =========================
def get_stock_list():
    print("\n[4/5] 获取股票基础信息...")
    
    stock_list = cached_stock_basic()
    
    if stock_list is not None and not stock_list.empty:
        print(f"   获取到 {len(stock_list)} 只股票信息")
        return stock_list
    else:
        print(f"   获取股票列表失败")
        return pd.DataFrame()

# =========================
# 构建主题投资组合
# =========================
def build_theme_portfolio(theme_sectors, concept_map, name_map, market_cap_df, stock_list_df):
    print("\n[5/5] 构建主题投资组合（多因子评分模式）...")
    
    portfolio = []
    
    market_cap_dict = {}
    if not market_cap_df.empty:
        market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}
    
    stock_info_dict = {}
    if not stock_list_df.empty:
        stock_info_dict = {row['ts_code']: row for _, row in stock_list_df.iterrows()}
    
    for theme_name, theme_data in theme_sectors.items():
        print(f"\n处理主题: {theme_name}")
        
        sector_names = theme_data.get('sectors', [])
        theme_stocks = set(theme_data.get('stocks', []))
        core_companies = theme_data.get('core_companies', [])
        
        # 从板块补充股票
        for sector_name in sector_names:
            if sector_name in concept_map:
                theme_stocks.update(concept_map[sector_name])
        
        print(f"   共涉及 {len(theme_stocks)} 只股票，正在计算多因子评分...")
        
        selected_stocks = []
        stock_count = 0
        negative_count = 0
        
        for ts_code in theme_stocks:
            if ts_code in market_cap_dict:
                mv = market_cap_dict[ts_code]['total_mv']
                
                # 负面筛选：排除ST、暂停上市等问题股票
                if is_negative_stock(ts_code):
                    negative_count += 1
                    continue
                
                # 市值筛选：双创股票允许更低市值
                market_type = get_stock_market_type(ts_code)
                if market_type in ['科创板', '创业板']:
                    min_market_cap = 500000  # 50亿
                else:
                    min_market_cap = 1000000  # 100亿
                
                if mv is not None and mv >= min_market_cap:
                    stock_count += 1
                    if stock_count % 20 == 0:
                        print(f"   已处理 {stock_count} 只股票...")
                    
                    related_sectors = []
                    for s in sector_names:
                        if s in concept_map and ts_code in concept_map[s]:
                            related_sectors.append(s)
                            if len(related_sectors) >= 5:
                                break
                    
                    # 获取股票名称
                    stock_name = name_map.get(ts_code, stock_info_dict.get(ts_code, {}).get('name', ts_code))
                    
                    # 获取技术数据
                    recent_data = get_stock_recent_data(ts_code, TRADE_DATE)
                    
                    # 构建市场数据
                    market_data = {
                        'market_cap': mv / 10000,
                        'circ_mv': market_cap_dict[ts_code].get('circ_mv', 0) / 10000 if market_cap_dict[ts_code].get('circ_mv') else 0,
                        'turnover_rate': market_cap_dict[ts_code].get('turnover_rate', 0),
                        'pe': market_cap_dict[ts_code].get('pe', 0),
                        'pb': market_cap_dict[ts_code].get('pb', 0)
                    }
                    
                    # 计算多因子评分（传入股票名称用于龙头识别）
                    factor_score = calculate_stock_multi_factor_score(
                        ts_code,
                        market_data,
                        recent_data,
                        theme_name,
                        related_sectors,
                        concept_map,
                        core_companies,
                        stock_name
                    )
                    
                    stock_info = {
                        'ts_code': ts_code,
                        'name': stock_name,
                        'market_cap': market_data['market_cap'],
                        'circ_mv': market_data['circ_mv'],
                        'turnover_rate': market_data['turnover_rate'],
                        'pe': market_data['pe'],
                        'pb': market_data['pb'],
                        'industry': stock_info_dict.get(ts_code, {}).get('industry', ''),
                        'list_date': stock_info_dict.get(ts_code, {}).get('list_date', ''),
                        'themes': theme_name,
                        'sectors': '|'.join(related_sectors),
                        'total_score': factor_score['total_score'],
                        'technical_score': factor_score['technical_score'],
                        'fundamental_score': factor_score['fundamental_score'],
                        'relevance_score': factor_score['relevance_score'],
                        'elasticity_bonus': factor_score['elasticity_bonus'],
                        'leader_bonus': factor_score['leader_bonus'],
                        'market_type': factor_score['market_type'],
                        'is_leader': factor_score['is_leader'],
                        'change_5': recent_data.get('change_5', 0),
                        'change_20': recent_data.get('change_20', 0),
                        'ma5_biased': recent_data.get('ma5_biased', 0)
                    }
                    selected_stocks.append(stock_info)
        
        # 按多因子综合评分排序
        selected_stocks.sort(key=lambda x: x['total_score'], reverse=True)
        selected_stocks = selected_stocks[:20]
        
        print(f"   筛选出 {len(selected_stocks)} 只股票（按多因子评分排序，排除负面股票 {negative_count} 只）")
        
        # 打印TOP 5的详细评分
        if selected_stocks:
            print("   TOP 5股票多因子评分详情:")
            for i, stock in enumerate(selected_stocks[:5], 1):
                print(f"     {i}. {stock['name']:10s} 总分:{stock['total_score']:3.0f} 技术:{stock['technical_score']:2.0f} 基本面:{stock['fundamental_score']:2.0f} 相关性:{stock['relevance_score']:2.0f} 弹性:{stock['elasticity_bonus']:2.0f} 龙头:{stock['leader_bonus']:2.0f} [{stock['market_type']}]")
        
        portfolio.extend(selected_stocks)
    
    return portfolio

# =========================
# 保存结果
# =========================
def save_portfolio_to_sqlite(portfolio, hot_themes):
    """将主题和成份股数据保存到SQLite数据库"""
    if not portfolio:
        print("\n未生成有效投资组合")
        return
    
    # 创建数据库连接
    db_path = os.path.join(CACHE_DIR, "theme_portfolio.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建主题表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme_name TEXT UNIQUE NOT NULL,
            industry TEXT,
            concept TEXT,
            keywords TEXT,
            exclude_keywords TEXT,
            core_companies TEXT,
            etf TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    
    # 创建成份股表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS theme_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme_name TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            name TEXT NOT NULL,
            industry TEXT,
            market_cap REAL,
            circ_mv REAL,
            turnover_rate REAL,
            pe REAL,
            pb REAL,
            list_date TEXT,
            sectors TEXT,
            total_score REAL,
            technical_score REAL,
            fundamental_score REAL,
            relevance_score REAL,
            change_5 REAL,
            change_20 REAL,
            ma5_biased REAL,
            trade_date TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (theme_name) REFERENCES themes(theme_name),
            UNIQUE(theme_name, ts_code, trade_date)
        )
    ''')
    
    # 尝试添加新列（如果表已存在）
    try:
        cursor.execute('ALTER TABLE theme_stocks ADD COLUMN elasticity_bonus REAL')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE theme_stocks ADD COLUMN leader_bonus REAL')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE theme_stocks ADD COLUMN market_type TEXT')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE theme_stocks ADD COLUMN is_leader INTEGER')
    except:
        pass
    
    # 保存主题配置
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for theme_name, theme_config in hot_themes.items():
        industry = json.dumps(theme_config.get('industry', []), ensure_ascii=False)
        concept = json.dumps(theme_config.get('concept', []), ensure_ascii=False)
        keywords = json.dumps(theme_config.get('keywords', []), ensure_ascii=False)
        exclude_keywords = json.dumps(theme_config.get('exclude_keywords', []), ensure_ascii=False)
        core_companies = json.dumps(theme_config.get('core_companies', []), ensure_ascii=False)
        etf = theme_config.get('etf', '')
        
        cursor.execute('''
            INSERT OR REPLACE INTO themes 
            (theme_name, industry, concept, keywords, exclude_keywords, core_companies, etf, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (theme_name, industry, concept, keywords, exclude_keywords, core_companies, etf, now, now))
    
    # 保存成份股数据
    df = pd.DataFrame(portfolio)
    for _, row in df.iterrows():
        cursor.execute('''
            INSERT OR REPLACE INTO theme_stocks (
                theme_name, ts_code, name, industry, market_cap, circ_mv, turnover_rate,
                pe, pb, list_date, sectors, total_score, technical_score, fundamental_score,
                relevance_score, elasticity_bonus, leader_bonus, market_type, is_leader,
                change_5, change_20, ma5_biased, trade_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            row.get('themes', ''),
            row.get('ts_code', ''),
            row.get('name', ''),
            row.get('industry', ''),
            row.get('market_cap', None),
            row.get('circ_mv', None),
            row.get('turnover_rate', None),
            row.get('pe', None),
            row.get('pb', None),
            row.get('list_date', ''),
            row.get('sectors', ''),
            row.get('total_score', None),
            row.get('technical_score', None),
            row.get('fundamental_score', None),
            row.get('relevance_score', None),
            row.get('elasticity_bonus', None),
            row.get('leader_bonus', None),
            row.get('market_type', ''),
            1 if row.get('is_leader', False) else 0,
            row.get('change_5', None),
            row.get('change_20', None),
            row.get('ma5_biased', None),
            TRADE_DATE,
            now
        ))
    
    conn.commit()
    conn.close()
    print(f"\n✓ 主题投资组合已保存至SQLite数据库: {db_path}")
    print(f"  - 主题配置: {len(hot_themes)} 个")
    print(f"  - 成份股记录: {len(df)} 条")


def save_portfolio(portfolio, hot_themes):
    if not portfolio:
        print("\n未生成有效投资组合")
        return
    
    df = pd.DataFrame(portfolio)
    
    columns = [
        'themes', 'ts_code', 'name', 'industry', 'market_type', 'is_leader',
        'market_cap', 'circ_mv', 'turnover_rate', 'pe', 'pb', 'list_date', 'sectors',
        'total_score', 'technical_score', 'fundamental_score', 'relevance_score',
        'elasticity_bonus', 'leader_bonus',
        'change_5', 'change_20', 'ma5_biased'
    ]
    # 处理可能不存在的列
    available_columns = [col for col in columns if col in df.columns]
    df = df[available_columns]
    
    csv_file = os.path.join(CACHE_DIR, f"theme_portfolio_{TRADE_DATE}.csv")
    df.to_csv(csv_file, index=False, encoding='utf-8-sig')
    print(f"\n✓ 主题投资组合已保存至: {csv_file}")
    
    # 同时保存到SQLite数据库
    save_portfolio_to_sqlite(portfolio, hot_themes)
    
    print("\n" + "="*80)
    print("主题投资组合汇总（多因子评分 + 双创弹性 + 龙头识别）")
    print("="*80)
    
    for theme_name in hot_themes.keys():
        theme_df = df[df['themes'] == theme_name]
        if len(theme_df) > 0:
            print(f"\n【{theme_name}】 ({len(theme_df)}只)")
            for _, row in theme_df.head(10).iterrows():
                market_type = row.get('market_type', '')
                leader_mark = '*' if row.get('is_leader', False) else ''
                print(f"  {row['name']:10s}{leader_mark} ({row['ts_code']:10s}) 评分:{row['total_score']:3.0f} 市值:{row['market_cap']:6.1f}亿 [{market_type}]")
            if len(theme_df) > 10:
                print(f"  ... 还有 {len(theme_df)-10} 只")
    
    print("\n" + "="*80)
    print(f"总计: {len(df)} 只股票，覆盖 {df['themes'].nunique()} 个主题")
    print("  * 标记表示行业龙头股票")
    print("="*80)

# =========================
# 主函数
# =========================
def main():
    print("="*80)
    print("主题投资组合策略分析 (缓存增强版)")
    print(f"日期: {TRADE_DATE}")
    print("="*80)
    
    # 加载热点主题定义
    hot_themes = load_hot_themes()
    
    if not hot_themes:
        print("未加载到热点主题定义")
        return
    
    concept_map, name_map = get_concept_map()
    
    if not concept_map:
        print("未获取到概念板块数据")
        return
    
    stock_list_df = get_stock_list()
    
    theme_sectors = match_theme_sectors(concept_map, stock_list_df, hot_themes)
    
    if not theme_sectors:
        print("未匹配到任何主题板块")
        return
    
    market_cap_df = get_market_cap_data()
    
    portfolio = build_theme_portfolio(
        theme_sectors, concept_map, name_map, 
        market_cap_df, stock_list_df
    )
    
    save_portfolio(portfolio, hot_themes)

if __name__ == "__main__":
    main()
