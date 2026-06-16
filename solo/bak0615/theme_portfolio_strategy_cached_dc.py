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

# SQLite 缓存（与theme_trend_sentiment_score.py共用）
DB_PATH = os.path.join(CACHE_DIR, 'cache.db')

def _init_sqlite_cache():
    """初始化SQLite缓存表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache_data (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            expire_time INTEGER,
            created_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def _sqlite_cache_get(cache_key):
    """从SQLite缓存读取DataFrame"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data, expire_time FROM cache_data WHERE key = ?', (cache_key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                cursor.execute('DELETE FROM cache_data WHERE key = ?', (cache_key,))
                conn.commit()
                return None
            from io import StringIO
            return pd.read_csv(StringIO(data_str))
    except:
        pass
    finally:
        conn.close()
    return None

def _sqlite_cache_set(cache_key, df):
    """写入SQLite缓存"""
    from io import StringIO
    conn = sqlite3.connect(DB_PATH)
    try:
        buffer = StringIO()
        df.to_csv(buffer, index=False)
        data_str = buffer.getvalue()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        ''', (cache_key, data_str, 0, int(time.time())))
        conn.commit()
    except:
        pass
    finally:
        conn.close()

_init_sqlite_cache()

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
    sqlite_key = f"tsc_dc_all_members_{TRADE_DATE}"

    # 1) 优先读 SQLite 缓存（与 theme_trend_sentiment_score.py 共用）
    sqlite_df = _sqlite_cache_get(sqlite_key)
    if sqlite_df is not None and not sqlite_df.empty and "is_industry" in sqlite_df.columns:
        print(f"   从SQLite缓存加载成功，共 {len(sqlite_df)} 条记录")
        return build_maps_from_df(sqlite_df)

    # 2) 回退到 pickle 缓存
    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                print(f"   从pickle缓存加载成功，共 {len(df)} 条记录")
                # 同步写入 SQLite 缓存（下次直接命中）
                if "is_industry" not in df.columns:
                    df["is_industry"] = False
                _sqlite_cache_set(sqlite_key, df)
                return build_maps_from_df(df)
        except:
            pass

    print("   正在调用Tushare API获取东方财富板块数据...")
    try:
        all_members = []
        total_boards = 0

        # 获取概念板块列表
        concept_df = pro.dc_index(trade_date=TRADE_DATE, idx_type='概念板块')
        time.sleep(0.15)
        # 获取行业板块列表
        industry_df = pro.dc_index(trade_date=TRADE_DATE, idx_type='行业板块')
        time.sleep(0.15)

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
            return {}, {}, {}, {}, {}

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
            # 标记行业/概念板块
            industry_ts_codes = set(industry_df['ts_code'].tolist())
            df['is_industry'] = df['ts_code'].isin(industry_ts_codes)
            df.to_pickle(cache_file)
            _sqlite_cache_set(sqlite_key, df)  # 同步写入 SQLite 缓存
            print(f"   成功获取 {len(df)} 条成份股记录（共{total_boards}个板块）")
            return build_maps_from_df(df)
    except Exception as e:
        print(f"调用 dc_index/dc_member API失败: {e}")
    return {}, {}, {}, {}, {}

def build_maps_from_df(df):
    dc_concept_map = {}       # 概念板块名 -> [股票代码]
    dc_industry_map = {}      # 行业板块名 -> [股票代码]
    name_map = {}
    stock_concepts = {}       # 股票代码 -> [概念板块名]
    stock_dc_industries = {}  # 股票代码 -> [行业板块名]

    for _, row in df.iterrows():
        ts_code = row['con_code']
        board_name = row['concept_name']
        stock_name = row.get('name', '')
        is_industry = row.get('is_industry', False)

        if is_industry:
            if board_name not in dc_industry_map:
                dc_industry_map[board_name] = set()
            dc_industry_map[board_name].add(ts_code)
            if ts_code not in stock_dc_industries:
                stock_dc_industries[ts_code] = []
            stock_dc_industries[ts_code].append(board_name)
        else:
            if board_name not in dc_concept_map:
                dc_concept_map[board_name] = set()
            dc_concept_map[board_name].add(ts_code)
            if ts_code not in stock_concepts:
                stock_concepts[ts_code] = []
            stock_concepts[ts_code].append(board_name)

        if ts_code not in name_map or not name_map[ts_code]:
            name_map[ts_code] = stock_name

    dc_concept_map = {k: list(v) for k, v in dc_concept_map.items()}
    dc_industry_map = {k: list(v) for k, v in dc_industry_map.items()}
    return dc_concept_map, dc_industry_map, name_map, stock_concepts, stock_dc_industries

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
    time.sleep(0.15)
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
    time.sleep(0.15)
    cache_manager.set(func_name, df)
    return df

def _has_concept_overlap(code, stock_concepts, stock_dc_industries, theme_concept_list, theme_keywords):
    """检查股票的概念是否与主题的概念或关键词有重叠（子串匹配）
    额外检查：股票 DC 行业板块名与 theme concept 精确匹配则直接通过
    """
    if theme_concept_list and stock_dc_industries:
        inds = stock_dc_industries.get(code, [])
        for ind in inds:
            if ind in theme_concept_list:
                return True

    concepts = stock_concepts.get(code, [])
    if not concepts:
        return False  # 无概念数据 → 不通过概念重叠检查（靠行业匹配+关键词才能进入）
    if not theme_concept_list:
        return True
    all_terms = [t for t in list(theme_concept_list) + list(theme_keywords) if t]
    if not all_terms:
        return True
    for sc in concepts:
        for tt in all_terms:
            if tt in sc or sc in tt:
                return True
    return False


def _is_force_include(code, stock_name, core_companies, leader_companies):
    """判断股票是否属于强制纳入名单（龙头/核心公司）"""
    if leader_companies and any(c in stock_name for c in leader_companies):
        return True, "leader_company"
    if core_companies and any(c in stock_name for c in core_companies):
        return True, "core_company"
    return False, ""


def _should_exclude(code, stock_name, concepts, exclude_keywords, core_companies, leader_companies):
    """检查股票是否应被排除（跳过强制纳入公司）"""
    if not exclude_keywords:
        return False
    is_force, _ = _is_force_include(code, stock_name, core_companies, leader_companies)
    if is_force:
        return False
    for ek in exclude_keywords:
        if ek in stock_name:
            return True
        for c in concepts:
            if ek in c:
                return True
    return False


# =========================
# 构建主题投资组合（简化版）
# =========================
def build_theme_portfolio(hot_themes, dc_concept_map, dc_industry_map, name_map, stock_concepts, stock_dc_industries, market_cap_df, stock_list_df):
    """
    产业链约束匹配模型（同步 theme_trend_sentiment_score.py 算法）
    
    1. Industry Gate：必须通过行业匹配进入候选池
    2. Chain Distance: 0=核心(行业+概念确认), 1=上下游(行业+关键词), 2+=排除
    3. exclude_keywords 硬过滤（跳过强制纳入公司）
    4. leader_companies 锚定
    """
    print("\n[5/5] 构建主题投资组合...")

    portfolio = []

    market_cap_dict = {}
    if not market_cap_df.empty:
        market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}

    stock_info_dict = {}
    if not stock_list_df.empty:
        stock_info_dict = {row['ts_code']: row for _, row in stock_list_df.iterrows()}

    stock_industry_dict = {}
    if not stock_list_df.empty:
        for _, row in stock_list_df.iterrows():
            stock_industry_dict[row['ts_code']] = row.get('industry', '')

    # === Phase 1-3: Industry Gate + Chain Distance + Score ===
    theme_stock_map = {}

    for theme_name, theme_data in hot_themes.items():
        industry_list = theme_data.get('industry', [])
        concept_list = theme_data.get('concept', [])
        keyword_list = theme_data.get('keywords', [])
        exclude_keywords = theme_data.get('exclude_keywords', [])
        core_companies = theme_data.get('core_companies', [])
        leader_companies = theme_data.get('leader_companies', [])

        # ===== Phase 1: Industry Gate =====
        candidates = {}  # code -> {industry_match, source}

        # 方式A: industry 名直接匹配东财行业板块
        for ind_name in industry_list:
            if ind_name in dc_industry_map:
                for code in dc_industry_map[ind_name]:
                    if code not in candidates:
                        candidates[code] = {"industry_match": True, "source": "dc_industry_board"}

        # 方式B: 股票东财行业板块名与 theme industry 匹配
        for code, industries in stock_dc_industries.items():
            if code not in candidates:
                for ind in industries:
                    if _in_industry_list(ind, industry_list):
                        candidates[code] = {"industry_match": True, "source": "dc_industry"}
                        break

        # 方式C: stock_basic 行业匹配
        for code, ind in stock_industry_dict.items():
            if code not in candidates and ind:
                if _in_industry_list(ind, industry_list):
                    candidates[code] = {"industry_match": True, "source": "stock_basic_industry"}

        # 方式D: 无 industry 配置 → concept 板块兜底
        if not industry_list:
            for conc_name in concept_list:
                if conc_name in dc_concept_map:
                    for code in dc_concept_map[conc_name]:
                        if code not in candidates:
                            candidates[code] = {"industry_match": False, "source": "concept_only"}
                if conc_name in dc_industry_map:
                    for code in dc_industry_map[conc_name]:
                        if code not in candidates:
                            candidates[code] = {"industry_match": True, "source": "concept_as_industry"}

        # ===== Phase 2-3: Chain Distance + Score =====
        matched = {}
        for code, info in candidates.items():
            stock_name = name_map.get(code, stock_info_dict.get(code, {}).get('name', code))
            concepts = stock_concepts.get(code, [])

            # 硬过滤
            if _should_exclude(code, stock_name, concepts, exclude_keywords, core_companies, leader_companies):
                continue

            # Concept overlap
            has_overlap = _has_concept_overlap(code, stock_concepts, stock_dc_industries, concept_list, keyword_list)

            # 关键词匹配
            kw_matches = []
            for kw in keyword_list:
                if kw in stock_name:
                    kw_matches.append(kw)
                else:
                    for c in concepts:
                        if kw in c:
                            kw_matches.append(kw)
                            break

            # Chain distance
            is_force, force_type = _is_force_include(code, stock_name, core_companies, leader_companies)
            if is_force:
                chain_distance = 0
            elif has_overlap:
                chain_distance = 0
            elif kw_matches:
                chain_distance = 1
            elif info.get("source") == "concept_only":
                chain_distance = 1
            else:
                chain_distance = 2

            if chain_distance >= 2:
                continue

            # Score
            score = _compute_chain_score(code, stock_name, concepts, info, concept_list, keyword_list,
                                         core_companies, leader_companies, chain_distance)

            via = info.get("source", "unknown")
            if is_force:
                via = force_type

            matched[code] = {
                "via": via,
                "industry_match": info.get("industry_match", False),
                "chain_distance": chain_distance,
                "score": score,
            }

        # Phase 3: 强制纳入龙头/核心公司
        for code, row in stock_info_dict.items():
            stock_name = row.get('name', '')
            is_leader = leader_companies and any(c in stock_name for c in leader_companies)
            is_core = core_companies and any(c in stock_name for c in core_companies)
            if (is_leader or is_core) and code not in matched:
                score = 25 if is_leader else 20
                matched[code] = {
                    "via": "leader_company" if is_leader else "core_company",
                    "industry_match": True,
                    "chain_distance": 0,
                    "score": score,
                }

        theme_stock_map[theme_name] = matched

    # ====================================================================
    # Phase 4: 多主题去重（基于新评分体系）
    # ====================================================================
    theme_stock_map = _disambiguate_multi_theme(theme_stock_map, hot_themes, stock_concepts)

    # ===== 筛选输出 =====
    for theme_name, theme_data in hot_themes.items():
        print(f"\n处理主题: {theme_name}")

        matched_stocks_dict = theme_stock_map.get(theme_name, {})
        industry_list = theme_data.get('industry', [])
        keyword_list = theme_data.get('keywords', [])
        concept_list = theme_data.get('concept', [])
        exclude_keywords = theme_data.get('exclude_keywords', [])

        print(f"   共涉及 {len(matched_stocks_dict)} 只股票")

        filtered_stocks = []

        for ts_code, meta in matched_stocks_dict.items():
            if ts_code not in market_cap_dict:
                continue

            mv = market_cap_dict[ts_code]['total_mv']
            turnover = market_cap_dict[ts_code].get('turnover_rate', 0)
            mcap = mv / 10000

            # 按市值分级的换手率门槛
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

            concepts = stock_concepts.get(ts_code, [])
            concepts_str = '|'.join(concepts)
            stock_name = name_map.get(ts_code, stock_info_dict.get(ts_code, {}).get('name', ts_code))

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

            industry_match = meta.get("industry_match", False)
            chain_distance = meta.get("chain_distance", 2)
            score = meta.get("score", 0)

            base_score = (mcap ** 0.5) * 0.8 + purity * 2 + score
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
# 产业链约束评分
# =========================
def _compute_chain_score(code, stock_name, concepts, info, concept_list, keyword_list,
                         core_companies, leader_companies, chain_distance):
    """产业链约束匹配评分（同步 theme_trend_sentiment_score.py）
    
    score = industry_base + concept_bonus + keyword_bonus + leader_proximity - chain_penalty
    """
    score = 0
    source = info.get("source", "")
    if source in ("dc_industry_board", "dc_industry"):
        score += 10
    elif source == "stock_basic_industry":
        score += 5
    elif source == "concept_as_industry":
        score += 8

    concept_matched = sum(1 for c in concepts if c in concept_list)
    score += concept_matched * 5

    kw_name_count = sum(1 for kw in keyword_list if kw in stock_name)
    score += kw_name_count * 2
    kw_concept_count = 0
    for kw in keyword_list:
        if kw not in stock_name:
            for c in concepts:
                if kw in c:
                    kw_concept_count += 1
                    break
    score += kw_concept_count * 1

    is_force, force_type = _is_force_include(code, stock_name, core_companies, leader_companies)
    if is_force:
        score += 15 if force_type == "leader_company" else 10
    elif concept_matched > 0:
        score += 3

    if chain_distance == 1:
        score -= 5

    return max(score, 0)


def _disambiguate_multi_theme(theme_stock_map, hot_themes, stock_concepts):
    """
    多主题去重：将出现在多个主题的股票只保留在评分最佳的主题中
    
    规则：
    1. chain_distance=0（核心产业链）的股票不参与去重
    2. 龙头/核心公司（via=leader_company/core_company）强制保留
    3. 其余按 score 分配最佳主题（保留最高分）
    4. 分数差 <= 3 且 industry_match=True 的保留
    """
    from collections import defaultdict

    stock_theme_count = defaultdict(int)
    for theme_name, stocks in theme_stock_map.items():
        for code in stocks:
            stock_theme_count[code] += 1

    multi_stocks = {code for code, cnt in stock_theme_count.items() if cnt > 1}
    if not multi_stocks:
        return theme_stock_map

    removed_count = 0
    for code in list(multi_stocks):
        theme_entries = []
        for theme_name, stocks in theme_stock_map.items():
            if code in stocks:
                meta = stocks[code]
                via = meta.get("via", "")
                is_core_chain = meta.get("chain_distance", 1) == 0
                is_force = via in ("leader_company", "core_company")
                score = meta.get("score", 0)
                im = meta.get("industry_match", False)
                theme_entries.append((theme_name, via, is_core_chain, is_force, score, im))

        all_exempt = all(is_cc or is_f for _, _, is_cc, is_f, _, _ in theme_entries)
        if all_exempt:
            continue

        forced_keep = {t for t, _, _, is_f, _, _ in theme_entries if is_f}

        theme_scores = sorted(theme_entries, key=lambda x: -x[4])
        best_score = theme_scores[0][4]

        keep_themes = set(forced_keep)
        for t, _, is_cc, is_f, sc, im in theme_scores:
            if t in forced_keep:
                continue
            if sc == best_score:
                keep_themes.add(t)
            elif best_score - sc <= 3 and im and not theme_scores[0][5]:
                keep_themes.add(t)

        for theme_name, _, is_cc, is_f, _, _ in theme_entries:
            if theme_name not in keep_themes and not is_cc and not is_f:
                del theme_stock_map[theme_name][code]
                removed_count += 1

    if removed_count:
        print(f"[Match] 多主题去重: {removed_count} 条（跨主题股票配到最佳主题）")

    return theme_stock_map


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
    dc_concept_map, dc_industry_map, name_map, stock_concepts, stock_dc_industries = get_concept_and_stock_info()

    print("\n[2/5] 获取股票基础信息...")
    stock_list_df = cached_stock_basic()

    print("\n[3/5] 获取市场数据...")
    market_cap_df = cached_daily_basic(TRADE_DATE)

    portfolio = build_theme_portfolio(hot_themes, dc_concept_map, dc_industry_map, name_map, stock_concepts, stock_dc_industries, market_cap_df, stock_list_df)

    save_portfolio_to_sqlite(portfolio, hot_themes)

    print("\n" + "=" * 60)
    print("主题投资组合分析完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
