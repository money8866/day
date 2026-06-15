#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
明日可交易主题决策引擎 V8 (实盘版)

核心目标: 生成【次日可执行交易计划】

输入数据:
- theme_graph_v3.json (一级产业 + 二级主题配置)
- theme/pools/*.json (已生成的主题股池数据)
- 东方财富板块数据 (涨跌/资金/涨停/成交额)
- 个股行情数据 (价格/均线/成交量/涨跌幅)

支持功能:
- 实时分析：默认分析最近交易日
- 历史回溯：支持指定历史日期进行复盘分析
  用法: python theme_trend_sentiment_v7.py --date 20260601

五级评分体系 (V8融合):
  ① macro_score: 一级产业趋势分 (0-100)
  ② theme_score: 二级主题强度分 (0-100)
  ③ emotion_score: 情绪分 (0-100)
  ④ leader_stability_score: 龙头稳定性分 (0-100) - 新增核心
  ⑤ cycle_score: 周期位置分 (0-100)
  ⑥ crowding_score: 拥挤度分 (0-100)

最终评分公式 (V8):
  final_score = 0.25*macro + 0.30*theme + 0.15*emotion + 0.15*leader_stability + 0.15*cycle - 0.15*crowding

双周期交易模型:
  - SHORT (1~5天): 情绪驱动 + 爆发 + 加速
  - MID (5~30天): 产业趋势 + 主线行情

主题筛选规则:
  - final_score >= 55
  - 至少1个稳定龙头 (stability >= 0.7)
  - 成交额 > 5日均值
  - 非退潮周期

输出:
  - 只输出 TOP 5 可交易主题
  - 每个主题最多 3 只股票
  - 每只股票包含交易信号 (Entry/Trigger/StopLoss)
  - 明确 SHORT / MID 模式
"""
import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import os
import json
import time
import sqlite3
import warnings
import argparse
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

# 尝试从 theme_trend_sentiment_score.py 导入可复用的函数
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

# 导入 tushare_quant 模块
try:
    import tushare_quant as tq
    TQ_AVAILABLE = True
except ImportError:
    TQ_AVAILABLE = False
    print("[Warning] tushare_quant 模块未找到")

# SQLite 缓存
DB_PATH = os.path.join(CACHE_DIR, 'cache.db')

def init_db():
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

init_db()

REPORT_DIR = os.path.join(os.path.dirname(BASE_DIR), "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)

# Patch os.path.expanduser
if not hasattr(os, '_original_expanduser'):
    os._original_expanduser = os.path.expanduser

def safe_expanduser(path):
    if 'tk.csv' in path:
        return os.path.join(CACHE_DIR, 'tk.csv')
    return os._original_expanduser(path)

os.path.expanduser = safe_expanduser

import tushare as ts

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None

OUTPUT_DB = os.path.join(CACHE_DIR, "theme_trend_sentiment_v7.db")

# Deepseek API配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

def deepseek_analyze(prompt):
    """调用Deepseek API进行分析"""
    if not DEEPSEEK_API_KEY:
        print("[Deepseek] API Key未配置，跳过AI分析")
        return ""
    
    try:
        import requests
        
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "system",
                    "content": "你是一位资深的A股量化投资分析师，擅长将复杂的量化数据转化为通俗易懂的投资建议。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "extra_body": [{"enable_search": True}]
        }
        
        r = requests.post(url, headers=headers, json=data, timeout=60)
        
        if r.status_code != 200:
            print(f"[Deepseek] API调用失败: {r.status_code}")
            return ""
        
        return r.json()['choices'][0]['message']['content']
    
    except Exception as e:
        print(f"[Deepseek] 调用异常: {e}")
        import traceback
        traceback.print_exc()
        return ""

N_DAYS = 60
TOP_N_PER_THEME = 30
MIN_STOCKS = 3
TOP_N_RESULTS = 8  # 主题输出数量限制
SKIP_AI_ANALYSIS = False  # 是否跳过AI分析
TRADE_MODE_FILTER = 'ALL'  # 交易模式筛选: 'ALL', 'SHORT', 'MID'

# ==================== 辅助函数 ====================

def _strip_ii(name):
    if not isinstance(name, str) or not name:
        return ""
    for suf in ("Ⅱ",):
        if name.endswith(suf):
            return name[:-len(suf)]
    return name

def _in_industry_list(name, industry_list):
    if not isinstance(name, str) or not name:
        return False
    stripped = _strip_ii(name)
    for ind in industry_list:
        if isinstance(ind, str) and _strip_ii(ind) == stripped:
            return True
    return False

def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    if pro is None:
        from datetime import date
        d = date.today()
        if d.weekday() == 5:
            d = d - timedelta(days=1)
        elif d.weekday() == 6:
            d = d - timedelta(days=2)
        return d.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
print(f"[Init] 交易日: {TRADE_DATE}  K线区间: {START_DATE} ~ {TRADE_DATE}")

def cache_get(name, **kwargs):
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{TRADE_DATE}"
    
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
    except Exception as e:
        pass
    finally:
        conn.close()
    return None

def cache_set(name, data, expire_hours=None, **kwargs):
    key = "_".join([name] + [f"{k}_{v}" for k, v in sorted(kwargs.items())])
    safe = key.replace("/", "_").replace(":", "_")
    cache_key = f"tsc_{safe}_{TRADE_DATE}"
    
    if expire_hours and expire_hours > 0:
        expire_time = int(time.time()) + expire_hours * 3600
    else:
        expire_time = 0
    
    from io import StringIO
    buffer = StringIO()
    data.to_csv(buffer, index=False)
    data_str = buffer.getvalue()
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        ''', (cache_key, data_str, expire_time, int(time.time())))
        conn.commit()
    except Exception as e:
        pass
    finally:
        conn.close()

# ==================== 数据获取函数 ====================

def load_theme2_json():
    """加载 theme_graph_v3.json (macro_themes 结构)"""
    # 尝试从 theme 目录加载
    theme_dir = os.path.join(os.path.dirname(BASE_DIR), "theme")
    path = os.path.join(theme_dir, "theme_graph_v3.json")
    
    if not os.path.exists(path):
        # 备用路径：当前目录
        path = os.path.join(BASE_DIR, "theme_graph_v3.json")
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到 {path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 转换为 HOT_THEMES 格式
    hot_themes = {}
    theme_to_category = {}
    macro_themes = data.get("macro_themes", {})
    
    for macro_name, macro_data in macro_themes.items():
        sub_themes = macro_data.get("sub_themes", {})
        for sub_name, sub_data in sub_themes.items():
            # 转换配置格式
            hot_themes[sub_name] = {
                "industry": sub_data.get("industry_filter", []),
                "concept": sub_data.get("concept_boards", []),
                "keywords": sub_data.get("keywords", []),
                "exclude_keywords": sub_data.get("exclude_keywords", []),
                "core_companies": sub_data.get("core_companies", []),
                "leader_companies": sub_data.get("core_companies", []),  # 复用core_companies
                "description": sub_data.get("description", "")
            }
            theme_to_category[sub_name] = macro_data.get("name", macro_name)
    
    return hot_themes, theme_to_category

def load_theme_pools(trade_date=None):
    """
    加载已生成的主题股池数据
    从 theme/pools/ 目录读取所有主题的股池文件
    """
    theme_dir = os.path.join(os.path.dirname(BASE_DIR), "theme")
    pools_dir = os.path.join(theme_dir, "pools")
    
    if not os.path.exists(pools_dir):
        print(f"[Pool] 股池目录不存在: {pools_dir}")
        return None
    
    theme_pool_map = {}
    
    import glob
    pool_files = glob.glob(os.path.join(pools_dir, "*.json"))
    
    for pool_file in pool_files:
        try:
            with open(pool_file, "r", encoding="utf-8") as f:
                pool_data = json.load(f)
            
            theme_name = pool_data.get("theme_name")
            if theme_name:
                theme_pool_map[theme_name] = pool_data
        except Exception as e:
            print(f"[Pool] 加载股池文件失败 {pool_file}: {e}")
    
    print(f"[Pool] 加载 {len(theme_pool_map)} 个主题股池")
    return theme_pool_map

def get_dc_members():
    """
    获取东方财富板块数据（成份股）
    板块数据变化很小，使用7天长效缓存
    """
    # 使用长效缓存（7天），避免每天重复下载
    dc_cache_key = "dc_all_members_longterm"
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data FROM cache_data WHERE key = ?', (dc_cache_key,))
        row = cursor.fetchone()
        if row:
            from io import StringIO
            df = pd.read_csv(StringIO(row[0]))
            if df is not None and not df.empty and "is_industry" in df.columns:
                print(f"[DC] 使用长效缓存: {len(df)} 条记录")
                return df
    except Exception:
        pass
    finally:
        conn.close()
    
    if pro is None:
        return pd.DataFrame()
    
    print("[DC] 调用 Tushare dc_index / dc_member...")
    concept_df = pro.dc_index(trade_date=TRADE_DATE, idx_type="概念板块")
    time.sleep(0.15)
    industry_df = pro.dc_index(trade_date=TRADE_DATE, idx_type="行业板块")
    time.sleep(0.15)
    
    industry_board_codes = set(industry_df["ts_code"].tolist())
    
    boards = pd.concat([concept_df[["ts_code", "name"]], industry_df[["ts_code", "name"]]], ignore_index=True)
    name_map = dict(zip(boards["ts_code"], boards["name"]))
    codes = boards["ts_code"].tolist()
    
    all_members = []
    fetched_codes = set()
    total = len(codes)
    
    for i, code in enumerate(codes):
        try:
            m = pro.dc_member(trade_date=TRADE_DATE, ts_code=code)
            if m is not None and not m.empty:
                m["concept_name"] = m["ts_code"].map(name_map)
                m["is_industry"] = code in industry_board_codes
                m = m.dropna(subset=["concept_name"])
                all_members.append(m)
                fetched_codes.add(code)
            if (i + 1) % 100 == 0:
                print(f"[DC] 进度: {i+1}/{total}")
            time.sleep(0.15)
        except Exception:
            pass
    
    if not all_members:
        return pd.DataFrame()
    df = pd.concat(all_members, ignore_index=True).drop_duplicates(subset=["con_code", "concept_name"])
    
    # 保存为长效缓存（7天）
    save_longterm_cache("dc_all_members_longterm", df, expire_days=7)
    print(f"[DC] 拉取完成: {len(df)} 条 (已保存7天长效缓存)")
    return df


def save_longterm_cache(key, data, expire_days=7):
    """保存长效缓存（指定天数）"""
    from io import StringIO
    buffer = StringIO()
    data.to_csv(buffer, index=False)
    data_str = buffer.getvalue()
    expire_time = int(time.time()) + expire_days * 86400
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cache_data (key, data, expire_time, created_at)
            VALUES (?, ?, ?, ?)
        ''', (key, data_str, expire_time, int(time.time())))
        conn.commit()
    except Exception as e:
        pass
    finally:
        conn.close()

def get_stock_basic():
    """
    获取股票基本信息
    股票列表变化很小，使用7天长效缓存
    """
    # 尝试读取长效缓存
    df = get_longterm_cache("stock_basic_longterm")
    if df is not None and not df.empty:
        print(f"[StockBasic] 使用长效缓存: {len(df)} 只股票")
        return df
    
    if pro is None:
        return pd.DataFrame()
    df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    time.sleep(0.15)
    
    # 保存为长效缓存
    save_longterm_cache("stock_basic_longterm", df, expire_days=7)
    print(f"[StockBasic] 拉取完成: {len(df)} 只 (已保存7天长效缓存)")
    return df


def get_longterm_cache(key):
    """读取长效缓存"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data, expire_time FROM cache_data WHERE key = ?', (key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row[0], row[1]
            # 检查是否过期
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                cursor.execute('DELETE FROM cache_data WHERE key = ?', (key,))
                conn.commit()
                return None
            from io import StringIO
            return pd.read_csv(StringIO(data_str))
    except Exception:
        pass
    finally:
        conn.close()
    return None

def get_daily_basic(trade_date=None):
    if trade_date is None:
        trade_date = TRADE_DATE
    cached = cache_get("daily_basic", trade_date=trade_date)
    if cached is not None:
        return cached
    if pro is None:
        return pd.DataFrame()
    df = pro.daily_basic(trade_date=trade_date, fields="ts_code,total_mv,circ_mv,turnover_rate,pe,pb")
    time.sleep(0.15)
    cache_set("daily_basic", df, trade_date=trade_date)
    return df

def _add_ma_columns(df):
    if df is None or df.empty:
        return df
    df = df.sort_values('trade_date').copy()
    if 'ma5' not in df.columns:
        df['ma5'] = df['close'].rolling(5).mean().bfill()
    if 'ma10' not in df.columns:
        df['ma10'] = df['close'].rolling(10).mean().bfill()
    if 'ma20' not in df.columns:
        df['ma20'] = df['close'].rolling(20).mean().bfill()
    if 'ma60' not in df.columns and len(df) >= 60:
        df['ma60'] = df['close'].rolling(60).mean().bfill()
    return df

def get_daily_kline(ts_codes, start, end):
    if pro is None or not ts_codes:
        return pd.DataFrame()
    
    all_parts = []
    need_fetch_codes = []
    LOCAL_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache_daily")
    
    for code in ts_codes:
        csv_path = os.path.join(LOCAL_CACHE_DIR, f"{code}.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                if not df.empty:
                    df['trade_date'] = df['trade_date'].astype(str)
                    df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
                    if not df.empty:
                        df = _add_ma_columns(df)
                        all_parts.append(df)
                        continue
            except Exception:
                pass
        
        cached = cache_get(f"daily_kline_{code}_{start}_{end}")
        if cached is not None:
            if 'ma5' not in cached.columns:
                cached = _add_ma_columns(cached)
                cache_set(f"daily_kline_{code}_{start}_{end}", cached)
            all_parts.append(cached)
        else:
            need_fetch_codes.append(code)
    
    if need_fetch_codes and TQ_AVAILABLE:
        print(f"[KLine] 使用 tushare_quant 批量预取 {len(need_fetch_codes)} 只股票")
        try:
            tq.batch_prefetch_hist_data(need_fetch_codes, start_date=start)
            for code in need_fetch_codes[:]:
                csv_path = os.path.join(LOCAL_CACHE_DIR, f"{code}.csv")
                if os.path.exists(csv_path):
                    try:
                        df = pd.read_csv(csv_path)
                        if not df.empty:
                            df['trade_date'] = df['trade_date'].astype(str)
                            df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
                            if not df.empty:
                                df = _add_ma_columns(df)
                                all_parts.append(df)
                                need_fetch_codes.remove(code)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[KLine] tushare_quant 调用失败: {e}")
    
    if need_fetch_codes:
        chunks = [need_fetch_codes[i:i+80] for i in range(0, len(need_fetch_codes), 80)]
        for ci, chunk in enumerate(chunks):
            try:
                df = pro.daily(ts_code=",".join(chunk), start_date=start, end_date=end)
                if df is not None and not df.empty:
                    for code in chunk:
                        code_df = df[df['ts_code'] == code].copy()
                        if not code_df.empty:
                            code_df = _add_ma_columns(code_df)
                            cache_set(f"daily_kline_{code}_{start}_{end}", code_df)
                            all_parts.append(code_df)
                time.sleep(0.15)
            except Exception as e:
                print(f"[KLine] 批次 {ci+1} 失败: {e}")
    
    return pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()

def get_index_kline(ts_code="000300.SH", start=None, end=None):
    if start is None:
        start = START_DATE
    if end is None:
        end = TRADE_DATE
    cached = cache_get("idx_kline", ts_code=ts_code, start=start, end=end)
    
    if cached is not None:
        if 'trade_date' in cached.columns:
            max_date = str(cached['trade_date'].max())
            if max_date == str(end):
                return cached
    
    if pro is None:
        return pd.DataFrame()
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    time.sleep(0.15)
    if df is not None and not df.empty:
        cache_set("idx_kline", df, ts_code=ts_code, start=start, end=end)
    return df

# ==================== 主题匹配函数 (复用) ====================

def _has_concept_overlap(code, stock_concepts, theme_concept_list, theme_keywords, stock_dc_industries=None):
    """检查股票的概念是否与主题的概念或关键词有重叠（子串匹配）
    
    额外检查：如果股票所在的 DC 行业板块名与 theme concept 精确匹配，直接通过。
    这解决了行业板块（如"半导体材料"）的成员在概念标签中不包含该名的问题。
    """
    # 检查 DC 行业板块名是否与 theme concept 精确匹配
    if stock_dc_industries and theme_concept_list:
        inds = stock_dc_industries.get(code, [])
        for ind in inds:
            if ind in theme_concept_list:
                return True
    
    concepts = stock_concepts.get(code, [])
    if not concepts:
        return False  # 无概念数据 → 不通过概念重叠检查（靠行业匹配+关键词才能进入）
    
    # 如果主题没有配置 concept，检查是否有关键词匹配
    if not theme_concept_list:
        # 如果有关键词，需要至少一个关键词匹配才能通过
        if theme_keywords:
            for kw in theme_keywords:
                for c in concepts:
                    if kw in c:
                        return True
                # 也检查股票名称
                #（注意：股票名称匹配在 _compute_chain_score 中单独处理）
            return False  # 没有关键词匹配，不通过概念重叠检查
        return True  # 既无概念也无关键词，纯行业匹配主题
    
    all_theme_terms = list(theme_concept_list) + list(theme_keywords)
    all_theme_terms = [t for t in all_theme_terms if t]
    if not all_theme_terms:
        return True  # 主题无概念/关键词时不阻截
    
    for sc in concepts:
        for tt in all_theme_terms:
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
    """检查股票是否应被排除（跳过强制纳入名单）"""
    if not exclude_keywords:
        return False
    is_force, _ = _is_force_include(code, stock_name, core_companies, leader_companies)
    if is_force:
        return False
    return _match_exclude(code, stock_name, concepts, exclude_keywords)


def _match_exclude(code, stock_name, concepts, exclude_keywords):
    """检查股票是否匹配排除关键词（子串匹配）"""
    for ek in exclude_keywords:
        if ek in stock_name:
            return True
        for c in concepts:
            if ek in c:
                return True
    return False


def _compute_chain_score(code, stock_name, concepts, info, concept_list, keyword_list,
                         core_companies, leader_companies, chain_distance):
    """
    产业链约束匹配评分
    
    score = industry_base + concept_bonus + keyword_bonus + leader_proximity - chain_penalty
    
    规则：
    - industry_base:   DC行业板块匹配+10, stock_basic行业匹配+5
    - concept_bonus:   股票概念与theme concept精确匹配, +5/个
    - keyword_bonus:   关键词在股票名中出现+2/个, 在概念中出现+1/个
    - leader_proximity: leader_companies +15, core_companies +10, 有概念重叠+3
    - chain_penalty:   chain_distance==1 时 -5
    """
    score = 0

    # 1) industry_base
    source = info.get("source", "")
    if source == "dc_industry_board" or source == "dc_industry":
        score += 10
    elif source == "stock_basic_industry":
        score += 5
    elif source == "concept_as_industry":
        score += 8
    # concept_only gets no industry base

    # 2) concept_bonus: 股票东财概念标签与 theme concept 精确匹配
    concept_matched = 0
    for cc in concepts:
        if cc in concept_list:
            concept_matched += 1
    score += concept_matched * 5

    # 3) keyword_bonus: 关键词匹配
    kw_name_count = sum(1 for kw in keyword_list if kw in stock_name)
    score += kw_name_count * 2
    kw_concept_count = 0
    for kw in keyword_list:
        if kw not in stock_name:  # 避免重复计数
            for c in concepts:
                if kw in c:
                    kw_concept_count += 1
                    break
    score += kw_concept_count * 1

    # 4) leader_proximity
    is_force, force_type = _is_force_include(code, stock_name, core_companies, leader_companies)
    if is_force:
        if force_type == "leader_company":
            score += 15
        else:
            score += 10
    elif concept_matched > 0:
        score += 3  # 概念重叠的邻近加分

    # 5) chain_penalty
    if chain_distance == 1:
        score -= 5

    return max(score, 0)

def match_theme_stocks_v2(hot_themes, dc_df, stock_basic_df):
    """
    ===== 产业链约束匹配模型 =====
    
    匹配原则：
    1. Industry Gate：股票必须通过行业板块匹配（东财行业板块 or stock_basic），否则直接排除
    2. Chain Distance 分层（0=核心, 1=上下游, 2+/3=排除）：
       - 0 (核心产业链)：industry match + 概念/关键词重叠 或 龙头/核心公司
       - 1 (上下游)：industry match only，无概念重叠但有部分关键词关联
       - 2+：纯行业关联无验证信息 → 排除
    3. exclude_keywords 硬过滤（跳过强制纳入公司）
    4. leader_companies 锚定：龙头公司强制 chain_distance=0，最高评分
    5. 最终评分：industry_base + concept_bonus + keyword_bonus + leader_proximity - chain_penalty
    
    输出每只股票的：
    - via: 匹配路径
    - industry_match: 是否行业匹配
    - chain_distance: 产业链层级 (0/1)
    - score: 综合评分
    """
    stock_basic_industry = {}
    name_map_basic = {}
    if stock_basic_df is not None and not stock_basic_df.empty:
        for _, row in stock_basic_df.iterrows():
            stock_basic_industry[row["ts_code"]] = row.get("industry", "")
            name_map_basic[row["ts_code"]] = row.get("name", "")

    # 拆分东财数据为行业和概念
    stock_concepts = defaultdict(list)          # code -> [概念板块名, ...]
    stock_dc_industries = defaultdict(list)     # code -> [行业板块名, ...]
    dc_concept_board_members = defaultdict(set)   # 概念板块名 -> {code, ...}
    dc_industry_board_members = defaultdict(set)  # 行业板块名 -> {code, ...}
    if dc_df is not None and not dc_df.empty:
        for _, r in dc_df.iterrows():
            con_code = r["con_code"]
            board_name = r["concept_name"]
            if con_code and board_name:
                is_industry = r.get("is_industry", False)
                if is_industry:
                    stock_dc_industries[con_code].append(board_name)
                    dc_industry_board_members[board_name].add(con_code)
                else:
                    stock_concepts[con_code].append(board_name)
                    dc_concept_board_members[board_name].add(con_code)

    theme_stock_map = {}
    
    for theme_name, cfg in hot_themes.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        exclude_keywords = cfg.get("exclude_keywords", [])
        core_companies = cfg.get("core_companies", [])
        leader_companies = cfg.get("leader_companies", [])

        # ====================================================================
        # Phase 1: Industry Gate — 股票必须通过行业匹配进入候选池
        # ====================================================================
        candidates = {}  # code -> {industry_match, source}

        # 方式A（最强）：industry 列表中的名称直接匹配东财行业板块
        for ind_name in industry_list:
            if ind_name in dc_industry_board_members:
                for code in dc_industry_board_members[ind_name]:
                    if code not in candidates:
                        candidates[code] = {"industry_match": True, "source": "dc_industry_board"}

        # 方式B（强）：股票所属东财行业板块与 theme industry 匹配
        for code, industries in stock_dc_industries.items():
            if code not in candidates:
                for ind in industries:
                    if _in_industry_list(ind, industry_list):
                        candidates[code] = {"industry_match": True, "source": "dc_industry"}
                        break

        # 方式C（中）：stock_basic 行业匹配（单一行业标签）
        for code, ind in stock_basic_industry.items():
            if code not in candidates and ind:
                if _in_industry_list(ind, industry_list):
                    candidates[code] = {"industry_match": True, "source": "stock_basic_industry"}

        # 方式D（兜底）：theme 无 industry 配置 → 用 concept 板块成员作为候选（标记为 industry_match=False）
        if not industry_list:
            for conc_name in concept_list:
                if conc_name in dc_concept_board_members:
                    for code in dc_concept_board_members[conc_name]:
                        if code not in candidates:
                            candidates[code] = {"industry_match": False, "source": "concept_only"}
                # 如果 concept 名恰好是行业板块名
                if conc_name in dc_industry_board_members:
                    for code in dc_industry_board_members[conc_name]:
                        if code not in candidates:
                            candidates[code] = {"industry_match": True, "source": "concept_as_industry"}

        # ====================================================================
        # Phase 2: Chain Distance 计算 + 评分
        # ====================================================================
        matched = {}
        for code, info in candidates.items():
            stock_name = name_map_basic.get(code, "")
            concepts = stock_concepts.get(code, [])

            # --- 2a) exclude_keywords 硬过滤（跳过强制纳入公司）---
            if _should_exclude(code, stock_name, concepts, exclude_keywords, core_companies, leader_companies):
                continue

            # --- 2b) 概念重叠检查 ---
            has_concept_overlap_flag = _has_concept_overlap(
                code, stock_concepts, concept_list, keyword_list, stock_dc_industries
            )

            # --- 2c) 关键词检查（在股票名或概念标签中）---
            kw_matches = []
            for kw in keyword_list:
                if kw in stock_name:
                    kw_matches.append(kw)
                else:
                    for c in concepts:
                        if kw in c:
                            kw_matches.append(kw)
                            break

            # --- 2d) 强制纳入检查 ---
            is_force, force_type = _is_force_include(code, stock_name, core_companies, leader_companies)

            # --- 2e) 判定 chain_distance ---
            # 关键优化：当主题同时有industry和concept配置时，
            # 只有industry匹配但无concept/keyword验证的股票应该被排除
            has_theme_validation = concept_list or keyword_list  # 主题是否有concept或keyword配置
            has_industry_match_only = info.get("industry_match", False) and not (has_concept_overlap_flag or kw_matches)
            
            if is_force:
                chain_distance = 0
            elif has_concept_overlap_flag:
                chain_distance = 0      # 核心产业链：行业+概念双重确认
            elif kw_matches:
                chain_distance = 1      # 上下游：行业确认 + 关键词提示
            elif info.get("source") == "concept_only":
                chain_distance = 1      # 无行业配置的主题概念匹配 → 弱关联
            elif has_industry_match_only and has_theme_validation:
                # 【关键】主题有concept/keyword但股票只有industry匹配 → 排除
                chain_distance = 2      # 纯行业匹配无验证 → 外延收益 → 排除
            else:
                chain_distance = 2      # 纯行业匹配无验证 → 外延收益 → 排除

            if chain_distance >= 2:
                continue

            # --- 2f) 计算综合评分 ---
            score = _compute_chain_score(
                code, stock_name, concepts, info,
                concept_list, keyword_list,
                core_companies, leader_companies,
                chain_distance
            )

            # --- 2g) 构建 meta 信息 ---
            via = info.get("source", "unknown")
            if is_force:
                via = force_type

            matched[code] = {
                "via": via,
                "industry_match": info.get("industry_match", False),
                "chain_distance": chain_distance,
                "score": score
            }

        # ====================================================================
        # Phase 3: 强制纳入龙头/核心公司（即使无行业匹配）
        # ====================================================================
        for code, name in name_map_basic.items():
            is_leader = leader_companies and any(c in name for c in leader_companies)
            is_core = core_companies and any(c in name for c in core_companies)
            if (is_leader or is_core) and code not in matched:
                score = 25 if is_leader else 20
                matched[code] = {
                    "via": "leader_company" if is_leader else "core_company",
                    "industry_match": True,
                    "chain_distance": 0,
                    "score": score
                }

        theme_stock_map[theme_name] = matched

    # ====================================================================
    # Phase 4: 多主题去重（基于新评分体系）
    # ====================================================================
    theme_stock_map = _disambiguate_multi_theme(theme_stock_map, hot_themes, stock_concepts)

    return theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts


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

        # 如果股票在所有主题都是核心产业链(chain=0)或强制纳入 → 跳过不去重
        all_exempt = all(is_cc or is_f for _, _, is_cc, is_f, _, _ in theme_entries)
        if all_exempt:
            continue

        # 强制纳入的公司保留
        forced_keep = {t for t, _, _, is_f, _, _ in theme_entries if is_f}

        # 按 score 降序
        theme_scores = sorted(theme_entries, key=lambda x: -x[4])
        best_score = theme_scores[0][4]

        keep_themes = set(forced_keep)
        for t, _, is_cc, is_f, sc, im in theme_scores:
            if t in forced_keep:
                continue
            if sc == best_score:
                keep_themes.add(t)
            elif best_score - sc <= 3 and im and not theme_scores[0][5]:
                # 分数相近且当前最佳无行业匹配 → 保留有行业匹配的
                keep_themes.add(t)

        for theme_name, _, is_cc, is_f, _, _ in theme_entries:
            if theme_name not in keep_themes and not is_cc and not is_f:
                del theme_stock_map[theme_name][code]
                removed_count += 1

    if removed_count:
        print(f"[Match] 多主题去重: {removed_count} 条（跨主题股票配到最佳主题）")

    return theme_stock_map

# ==================== 特征计算函数 ====================

def per_stock_features(df_one):
    if df_one is None or df_one.empty or len(df_one) < 6:
        return None
    
    df_one = df_one.sort_values("trade_date").reset_index(drop=True)
    close = df_one["close"].astype(float).values
    high = df_one["high"].astype(float).values
    low = df_one["low"].astype(float).values
    vol = df_one["vol"].astype(float).values
    pct = df_one["pct_chg"].astype(float).values
    
    n = len(close)
    last = n - 1
    
    def safe_pct(a, b):
        return (a / b - 1.0) * 100.0 if b and b > 0 else 0.0
    
    def calc_slope(prices):
        if len(prices) < 3:
            return 0.0
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        slope_norm = (slope / np.mean(prices)) * 100 if np.mean(prices) > 0 else 0
        return slope_norm
    
    ret_5 = safe_pct(close[last], close[last - 5]) if last - 5 >= 0 else safe_pct(close[last], close[0])
    ret_10 = safe_pct(close[last], close[last - 10]) if last - 10 >= 0 else safe_pct(close[last], close[0])
    ret_20 = safe_pct(close[last], close[last - 20]) if last - 20 >= 0 else safe_pct(close[last], close[0])
    
    ma5 = close[max(0, last - 4):last + 1].mean()
    ma10 = close[max(0, last - 9):last + 1].mean()
    ma20 = close[max(0, last - 19):last + 1].mean()
    ma60 = close[max(0, last - 59):last + 1].mean() if n >= 60 else ma20
    ma240 = close[max(0, last - 239):last + 1].mean() if n >= 240 else ma60
    
    ma5_b = (close[last] / ma5 - 1) * 100 if ma5 > 0 else 0
    ma10_b = (close[last] / ma10 - 1) * 100 if ma10 > 0 else 0
    ma20_b = (close[last] / ma20 - 1) * 100 if ma20 > 0 else 0
    ma60_b = (close[last] / ma60 - 1) * 100 if ma60 > 0 else 0
    ma240_b = (close[last] / ma240 - 1) * 100 if ma240 > 0 else 0
    
    win10 = close[max(0, last - 9):last + 1]
    slope10 = calc_slope(win10)
    win60 = close[max(0, last - 59):last + 1]
    slope60 = calc_slope(win60)
    win240 = close[max(0, last - 239):last + 1]
    slope240 = calc_slope(win240)
    
    acc_5_10 = ret_5 - ret_10
    
    v5 = vol[max(0, last - 4):last + 1].mean()
    v20 = vol[max(0, last - 19):last + 1].mean()
    vol_ratio = v5 / v20 if v20 > 0 else 1.0
    
    if len(win10) > 1:
        running_max = np.maximum.accumulate(win10)
        drawdown = (win10 / running_max - 1.0)
        max_dd_10 = drawdown.min() * 100
    else:
        max_dd_10 = 0.0
    
    zt_flag = 1 if (pct[last] is not None and pct[last] >= 9.5) else 0
    strong_flag = 1 if (pct[last] is not None and pct[last] >= 5.0) else 0
    
    amount_latest = float(df_one.iloc[last].get("amount", 0) or 0) / 100000
    
    lb_height = 0
    for j in range(last, -1, -1):
        p = float(pct[j]) if pct[j] is not None else 0
        if p >= 9.5:
            lb_height += 1
        else:
            break
    
    # ========== V9 所需字段 ==========
    
    # 近3日涨停次数
    zt_count_3d = 0
    for j in range(max(0, last - 2), last + 1):
        p = float(pct[j]) if pct[j] is not None else 0
        if p >= 9.5:
            zt_count_3d += 1
    
    # 趋势状态判断
    if slope10 > 0.5 and slope60 > 0.2:
        trend_status = "上升"
    elif slope10 > 0.2 and slope60 > 0:
        trend_status = "初升"
    elif slope10 < -0.5 and slope60 < -0.2:
        trend_status = "下降"
    elif abs(slope10) < 0.3:
        trend_status = "震荡"
    else:
        trend_status = "震荡"
    
    # 加速指标 (0-100): 基于5日涨幅和动量
    # ret_5=25% → acceleration=85(临界), ret_5=10% → acceleration=84(未超限)
    acceleration = min(100, max(0, ret_5 * 3.4 + 50))
    
    # 风险分 (0-100): 基于最大回撤和波动率
    volatility = np.std(pct[max(0, last-9):last+1]) if len(pct) >= 10 else 10
    risk_score = min(100, max(0, abs(max_dd_10) * 2 + volatility * 3))
    
    # 放量突破：今日成交量 > 5日均量 * 1.5 且 涨幅 > 1%
    volume_breakout = vol_ratio > 1.5 and pct[last] > 1.0 if pct[last] is not None else False
    
    # 二次启动：近10日有回调后再次放量上涨
    second_start = False
    if last >= 10:
        # 检查是否有回调（近5日有2日下跌）然后反弹
        down_days = sum(1 for j in range(last - 4, last + 1) if pct[j] < 0)
        if down_days >= 2 and pct[last] > 2.0 and vol_ratio > 1.3:
            second_start = True
    
    return {
        "ret_5": ret_5, "ret_10": ret_10, "ret_20": ret_20,
        "ma5_b": ma5_b, "ma10_b": ma10_b, "ma20_b": ma20_b,
        "ma60_b": ma60_b, "ma240_b": ma240_b,
        "slope_10": slope10, "slope_60": slope60, "slope_240": slope240,
        "acc_5_10": acc_5_10, "vol_ratio": vol_ratio, "max_dd_10": max_dd_10,
        "zt_flag": zt_flag, "strong_flag": strong_flag,
        "pct_chg": float(pct[last]) if pct[last] is not None else 0.0,
        "turnover": float(df_one.iloc[last].get("turnover_rate", 0) or 0),
        "amount_latest": amount_latest, "lb_height": lb_height,
        "close": close[last], "ma20": ma20, "ma60": ma60,
        # V9 新增字段
        "pct_chg_5d": ret_5,  # 5日涨幅
        "zt_count_3d": zt_count_3d,  # 近3日涨停次数
        "trend_status": trend_status,  # 趋势状态
        "acceleration": acceleration,  # 加速指标
        "risk_score": risk_score,  # 风险分
        "volume_breakout": volume_breakout,  # 放量突破
        "second_start": second_start,  # 二次启动
    }

def sigmoid(x, k=0.15, c=0.0):
    try:
        return 1.0 / (1.0 + np.exp(-k * (x - c)))
    except Exception:
        return 0.5

def linear(x, lo, hi, out_lo=0.0, out_hi=1.0):
    if hi == lo:
        return out_lo
    v = (x - lo) / (hi - lo)
    v = max(0.0, min(1.0, v))
    return out_lo + v * (out_hi - out_lo)

# ==================== V7 评分函数 ====================

def calc_trend_score(stock_feats, market_index_ret):
    """计算趋势分 (复用原有逻辑)"""
    if not stock_feats:
        return 0.0, {}
    
    n = len(stock_feats)
    avg_ret_5 = np.mean([s["ret_5"] for s in stock_feats])
    avg_ret_10 = np.mean([s["ret_10"] for s in stock_feats])
    avg_ret_20 = np.mean([s["ret_20"] for s in stock_feats])
    
    ret_score = (linear(avg_ret_5, -10, 15) * 0.5 + linear(avg_ret_10, -15, 25) * 0.3 + linear(avg_ret_20, -25, 40) * 0.2)
    
    pct_above_ma5 = sum(1 for s in stock_feats if s["ma5_b"] > 0) / n
    pct_above_ma10 = sum(1 for s in stock_feats if s["ma10_b"] > 0) / n
    pct_above_ma20 = sum(1 for s in stock_feats if s["ma20_b"] > 0) / n
    pct_above_ma60 = sum(1 for s in stock_feats if s["ma60_b"] > 0) / n
    pct_above_ma240 = sum(1 for s in stock_feats if s["ma240_b"] > 0) / n
    ma_score = pct_above_ma5 * 0.30 + pct_above_ma10 * 0.25 + pct_above_ma20 * 0.20 + pct_above_ma60 * 0.15 + pct_above_ma240 * 0.10
    
    avg_slope10 = np.mean([s["slope_10"] for s in stock_feats])
    avg_slope60 = np.mean([s["slope_60"] for s in stock_feats])
    avg_slope240 = np.mean([s["slope_240"] for s in stock_feats])
    slope_score = sigmoid(avg_slope10, k=0.3, c=0) * 0.4 + sigmoid(avg_slope60, k=0.25, c=0) * 0.35 + sigmoid(avg_slope240, k=0.2, c=0) * 0.25
    
    avg_acc = np.mean([s["acc_5_10"] for s in stock_feats])
    acc_score = sigmoid(avg_acc, k=0.3, c=0)
    
    pcts = sorted([s["pct_chg"] for s in stock_feats], reverse=True)
    top3 = pcts[:min(3, len(pcts))]
    top3_avg = np.mean(top3) if top3 else 0
    leader_score = linear(top3_avg, -5, 15)
    
    avg_dd = np.mean([s["max_dd_10"] for s in stock_feats])
    dd_score = linear(-avg_dd, -2, 10)
    
    rel_ret = avg_ret_10 - market_index_ret
    rel_score = sigmoid(rel_ret, k=0.2, c=0)
    
    pcts_today = [s["pct_chg"] for s in stock_feats]
    avg_pct_today = np.mean(pcts_today)
    up_n = sum(1 for p in pcts_today if p > 0)
    breadth_today = up_n / n if n > 0 else 0.5
    today_momentum_score = linear(avg_pct_today, -3, 3) * 0.6 + linear(breadth_today, 0.2, 0.8) * 0.4
    
    if avg_pct_today < -2.0 and breadth_today < 0.3:
        today_adjust = 0.92
    elif avg_pct_today < -1.0 and breadth_today < 0.4:
        today_adjust = 0.96
    else:
        today_adjust = 1.0
    
    mid_trend_ok = (avg_slope60 > 0) and (avg_slope240 >= 0) and (avg_slope10 > 0) and (avg_ret_20 >= 0)
    
    score01 = (
        ret_score * 0.26 +
        ma_score * 0.22 +
        slope_score * 0.18 +
        acc_score * 0.06 +
        leader_score * 0.06 +
        dd_score * 0.05 +
        rel_score * 0.09 +
        today_momentum_score * 0.08
    ) * today_adjust
    
    score01 = max(0.0, min(1.0, score01))
    
    detail = {
        "avg_ret_5": round(avg_ret_5, 2), "avg_ret_10": round(avg_ret_10, 2), "avg_ret_20": round(avg_ret_20, 2),
        "pct_above_ma5": round(pct_above_ma5 * 100, 1), "pct_above_ma10": round(pct_above_ma10 * 100, 1),
        "pct_above_ma20": round(pct_above_ma20 * 100, 1), "pct_above_ma60": round(pct_above_ma60 * 100, 1),
        "avg_slope_10": round(avg_slope10, 3), "avg_slope_60": round(avg_slope60, 3),
        "avg_pct_today": round(avg_pct_today, 2), "breadth_today": round(breadth_today * 100, 1),
        "mid_trend_ok": 1 if mid_trend_ok else 0,
    }
    return round(score01 * 100, 1), detail

def calc_sentiment_score(stock_feats, market_index_ret):
    """计算情绪分"""
    if not stock_feats:
        return 0.0, {}
    
    n = len(stock_feats)
    pcts = [s["pct_chg"] for s in stock_feats]
    up_n = sum(1 for p in pcts if p > 0)
    down_n = sum(1 for p in pcts if p < 0)
    zt_n = sum(1 for s in stock_feats if s["zt_flag"] == 1)
    strong_n = sum(1 for s in stock_feats if s["strong_flag"] == 1)
    
    breadth = up_n / n
    breadth_score = linear(breadth, 0.2, 0.85)
    zt_ratio = zt_n / n
    zt_score = linear(zt_ratio, 0, 0.15)
    strong_ratio = strong_n / n
    strong_score = linear(strong_ratio, 0, 0.30)
    avg_vol_ratio = np.mean([s["vol_ratio"] for s in stock_feats])
    vol_score = linear(avg_vol_ratio, 0.6, 1.8)
    avg_turnover = np.mean([s["turnover"] for s in stock_feats])
    turnover_score = linear(avg_turnover, 1.0, 8.0)
    median_pct = float(np.median(pcts))
    mean_pct = float(np.mean(pcts))
    profit_score = sigmoid(median_pct * 0.6 + mean_pct * 0.4, k=0.25, c=0)
    top1 = max(pcts) if pcts else 0
    resonance = 1.0 if (zt_n >= 1 and top1 >= 7) else 0.0
    if zt_n >= 2 and top1 >= 9:
        resonance = 1.2
    resonance_score = min(resonance, 1.0)
    
    score = (
        breadth_score * 0.25 +
        zt_score * 0.15 +
        strong_score * 0.15 +
        vol_score * 0.15 +
        turnover_score * 0.10 +
        profit_score * 0.10 +
        resonance_score * 0.10
    )
    score = max(0.0, min(1.0, score))
    
    detail = {
        "up_ratio": round(breadth * 100, 1),
        "zt_count": zt_n,
        "strong_count": strong_n,
        "avg_vol_ratio": round(avg_vol_ratio, 2),
        "avg_turnover": round(avg_turnover, 2),
        "median_pct": round(median_pct, 2),
        "mean_pct": round(mean_pct, 2),
        "resonance_score": round(resonance_score, 2),
    }
    return round(score * 100, 1), detail

def calc_crowding_score(stock_feats, theme_amount, market_total_amount):
    """计算拥挤度分 (0-100，越高越拥挤)"""
    if not stock_feats:
        return 50.0, {}
    
    n = len(stock_feats)
    
    # 1. 涨幅集中度：top3涨幅占比越高越拥挤
    pcts = sorted([s["pct_chg"] for s in stock_feats], reverse=True)
    top3_pct = np.mean(pcts[:min(3, len(pcts))]) if pcts else 0
    all_pct = np.mean(pcts) if pcts else 0
    concentration = top3_pct / (all_pct + 0.01)
    concentration_score = min(concentration * 30, 100) if all_pct > 0 else 50
    
    # 2. 涨停占比
    zt_ratio = sum(1 for s in stock_feats if s["zt_flag"] == 1) / n
    zt_crowding = linear(zt_ratio, 0, 0.15) * 100
    
    # 3. 成交额集中度：板块成交额占市场比例
    amount_ratio = theme_amount / (market_total_amount + 1)
    amount_crowding = linear(amount_ratio, 0, 0.05) * 100  # 占市场5%以上算非常拥挤
    
    # 4. 换手率异常
    avg_turnover = np.mean([s["turnover"] for s in stock_feats])
    turnover_crowding = linear(avg_turnover, 1, 10) * 100
    
    crowding = (concentration_score * 0.25 + zt_crowding * 0.30 + amount_crowding * 0.25 + turnover_crowding * 0.20)
    crowding = max(0, min(100, crowding))
    
    detail = {
        "concentration_score": round(concentration_score, 1),
        "zt_crowding": round(zt_crowding, 1),
        "amount_crowding": round(amount_crowding, 1),
        "turnover_crowding": round(turnover_crowding, 1),
        "amount_ratio": round(amount_ratio * 100, 2),
    }
    return round(crowding, 1), detail

def calc_cycle_score(theme_state, rotation_cycle):
    """计算周期位置分 (基于主题状态和轮动周期)"""
    # 周期分映射
    cycle_bonus = {
        "启动": 20,
        "发酵": 15,
        "主升": 10,
        "分歧转一致": 18,
        "分歧": -5,
        "高潮": -15,
        "退潮": -50,
        "弱势": -20,
        "震荡": 0,
        "强势": 5,
    }
    
    # 轮动周期加权
    rotation_bonus = {
        "短期爆发": 15,
        "中期持续": 10,
        "上升中": 12,
        "高潮风险": -20,
        "退潮回避": -40,
        "未知": 0,
    }
    
    state_score = cycle_bonus.get(theme_state, 0)
    rotation_score = rotation_bonus.get(rotation_cycle, 0)
    
    # 基础分 50，加上状态和轮动调整
    base = 50
    final = base + state_score + rotation_score
    return max(0, min(100, final))

def calc_quality_score(stock_feat, theme_avg_ret):
    """计算个股质量分 (quality_score)
    
    过滤规则:
    - 下降趋势 (MA20 < MA60 且斜率负) → 直接剔除
    - quality_score < 60 → 剔除
    """
    if not stock_feat:
        return 0, {}
    
    # 1. 趋势分 (MA20 > MA60 + 上升斜率)
    trend_score = 0
    if stock_feat.get("ma20_b", 0) > 0 and stock_feat.get("ma60_b", 0) > 0:
        trend_score += 30
    if stock_feat.get("slope_10", 0) > 0:
        trend_score += 20
    if stock_feat.get("slope_60", 0) > 0:
        trend_score += 15
    
    # 2. 量能分 (放量突破)
    vol_score = 0
    if stock_feat.get("vol_ratio", 1) >= 1.5:
        vol_score += 20
    elif stock_feat.get("vol_ratio", 1) >= 1.2:
        vol_score += 10
    
    # 当日涨幅 > 2% 且放量
    if stock_feat.get("pct_chg", 0) > 2 and stock_feat.get("vol_ratio", 1) > 1.2:
        vol_score += 10
    
    # 3. 资金分 (相对强度)
    money_score = 0
    rel_ret = stock_feat.get("ret_10", 0) - theme_avg_ret
    if rel_ret > 5:
        money_score += 15
    elif rel_ret > 0:
        money_score += 10
    elif rel_ret < -5:
        money_score -= 10
    
    # 4. 相对强度分
    strength_score = 0
    if stock_feat.get("pct_chg", 0) > 5:
        strength_score += 15
    elif stock_feat.get("pct_chg", 0) > 2:
        strength_score += 10
    
    # 5. 惩罚项
    penalty = 0
    
    # 下降趋势惩罚
    if stock_feat.get("ma20", 0) < stock_feat.get("ma60", 0) and stock_feat.get("slope_10", 0) < 0:
        penalty += 40
    
    # 高位滞涨惩罚
    if stock_feat.get("ma20_b", 0) > 20 and stock_feat.get("pct_chg", 0) < 0:
        penalty += 15
    
    # 近期大阴线惩罚 (当日跌幅 > 5%)
    if stock_feat.get("pct_chg", 0) < -5:
        penalty += 20
    
    quality = trend_score + vol_score + money_score + strength_score - penalty
    quality = max(0, min(100, quality))
    
    detail = {
        "trend_score": trend_score,
        "vol_score": vol_score,
        "money_score": money_score,
        "strength_score": strength_score,
        "penalty": penalty,
    }
    return quality, detail


# ==================== V8 新增核心模块 ====================

def calc_leader_stability_score(stock_feat, theme_feats):
    """计算龙头稳定性分 (V8核心)
    
    leader_stability_score:
    - 是否连续3日领涨
    - 是否跑赢板块指数
    - 是否资金持续流入
    - 是否新高结构
    
    规则:
    - stable >= 0.7 → 真龙头
    - 0.5~0.7 → 观察龙头
    - <0.5 → 伪龙头(剔除)
    """
    if not stock_feat or not theme_feats:
        return 0.0, {}
    
    score = 0
    detail = {}
    
    # 1. 连续领涨能力 (30分)
    if stock_feat.get("pct_chg", 0) > 0:
        # 连续3日领涨
        if stock_feat.get("ret_3", 0) > 0 and stock_feat.get("pct_chg", 0) > 3:
            score += 30
            detail["连续领涨"] = "是"
        elif stock_feat.get("ret_3", 0) > 0:
            score += 20
            detail["连续领涨"] = "弱"
        else:
            score += 5
            detail["连续领涨"] = "否"
    
    # 2. 跑赢板块 (25分)
    if theme_feats:
        theme_avg_ret = sum(f["pct_chg"] for f in theme_feats) / len(theme_feats)
        stock_ret = stock_feat.get("pct_chg", 0)
        
        if stock_ret > theme_avg_ret + 2:
            score += 25
            detail["跑赢板块"] = "强"
        elif stock_ret > theme_avg_ret:
            score += 15
            detail["跑赢板块"] = "是"
        else:
            score += 5
            detail["跑赢板块"] = "否"
        detail["相对板块收益"] = round(stock_ret - theme_avg_ret, 2)
    
    # 3. 资金持续流入 (25分)
    if stock_feat.get("vol_ratio", 1) > 1.2:
        score += 15
        if stock_feat.get("amount_latest", 0) > stock_feat.get("amount_5d_avg", 0) * 1.5:
            score += 10
            detail["资金流入"] = "强"
        else:
            detail["资金流入"] = "中"
    elif stock_feat.get("vol_ratio", 1) > 1.0:
        score += 10
        detail["资金流入"] = "弱"
    else:
        score += 5
        detail["资金流入"] = "否"
    
    # 4. 新高结构 (20分)
    if stock_feat.get("high_20d", 0) >= stock_feat.get("close", 0) * 0.95:
        score += 20
        detail["新高结构"] = "是"
    elif stock_feat.get("high_60d", 0) >= stock_feat.get("close", 0) * 0.9:
        score += 10
        detail["新高结构"] = "接近"
    else:
        score += 0
        detail["新高结构"] = "否"
    
    # 5. 趋势健康度惩罚
    if stock_feat.get("ma20", 0) < stock_feat.get("ma60", 0):
        score -= 15
        detail["趋势惩罚"] = "MA20<MA60"
    if stock_feat.get("slope_10", 0) < -0.5:
        score -= 10
        detail["趋势惩罚"] = "短期斜率负"
    
    score = max(0, min(100, score))
    
    # 判断龙头类型
    if score >= 70:
        detail["龙头类型"] = "真龙头"
    elif score >= 50:
        detail["龙头类型"] = "观察龙头"
    else:
        detail["龙头类型"] = "伪龙头"
    
    return round(score, 1), detail


def calc_short_score(theme_data, stock_feats):
    """计算短线交易评分 (SHORT: 1~5天)
    
    short_score = 0.4*emotion + 0.3*3日动量 + 0.2*leader_strength + 0.1*资金加速度
    
    条件:
    - emotion_score 高
    - 资金快速流入
    - 龙头刚确认
    - 波动率上升
    """
    emotion_score = theme_data.get("emotion_score", 0)
    leader_strength = theme_data.get("leader_stability_score", 0)
    
    # 3日动量
    if stock_feats:
        avg_ret_3 = np.mean([s.get("ret_3", 0) for s in stock_feats])
    else:
        avg_ret_3 = 0
    
    # 资金加速度
    if stock_feats:
        avg_vol_ratio = np.mean([s.get("vol_ratio", 1) for s in stock_feats])
        money_accel = avg_vol_ratio - 1  # 相对于均量的增量
    else:
        money_accel = 0
    
    short_score = (
        emotion_score * 0.4 +
        avg_ret_3 * 0.3 +
        leader_strength * 0.02 +  # 转换到0-100范围
        min(money_accel * 50, 100) * 0.1
    )
    
    return max(0, min(100, short_score))


def calc_mid_score(theme_data, stock_feats):
    """计算中线交易评分 (MID: 5~30天)
    
    mid_score = 0.35*macro_trend + 0.35*theme_strength + 0.2*leader_stability + 0.1*资金持续性
    
    条件:
    - 一级产业强
    - 龙头稳定
    - 成交持续放大
    """
    macro_score = theme_data.get("macro_score", 0)
    theme_score = theme_data.get("theme_score", 0)
    leader_stability = theme_data.get("leader_stability_score", 0)
    
    # 资金持续性
    if stock_feats:
        amounts = [s.get("amount_latest", 0) for s in stock_feats]
        if amounts:
            avg_amount = np.mean(amounts)
            # 计算成交额相对于5日均值的比例
            money_sustain = np.mean([s.get("amount_latest", 0) / max(s.get("amount_5d_avg", 1), 1) for s in stock_feats])
        else:
            money_sustain = 1.0
    else:
        money_sustain = 1.0
    
    mid_score = (
        macro_score * 0.35 +
        theme_score * 0.35 +
        leader_stability * 0.2 +
        min(money_sustain * 50, 100) * 0.1
    )
    
    return max(0, min(100, mid_score))


def calc_final_score_v8(macro_score, theme_score, emotion_score, leader_stability_score, cycle_score, crowding_score):
    """V8最终评分公式
    
    final_score = 0.25*macro + 0.30*theme + 0.15*emotion + 0.15*leader_stability + 0.15*cycle - 0.15*crowding
    """
    score = (
        macro_score * 0.25 +
        theme_score * 0.30 +
        emotion_score * 0.15 +
        leader_stability_score * 0.15 +
        cycle_score * 0.15 -
        crowding_score * 0.15
    )
    return max(0, min(100, score))

def calc_theme_state_v2(r, prev_data=None):
    """判断主题状态 (V2版本)"""
    t_score = r.get("trend_score", 0)
    s_score = r.get("sentiment_score", 0)
    td = r.get("trend_detail", {}) or {}
    sd = r.get("sentiment_detail", {}) or {}
    
    avg_ret_5 = td.get("avg_ret_5", 0)
    avg_ret_10 = td.get("avg_ret_10", 0)
    avg_pct_today = td.get("avg_pct_today", 0)
    avg_slope_10 = td.get("avg_slope_10", 0)
    
    zt_count = sd.get("zt_count", 0)
    up_ratio = sd.get("up_ratio", 0)
    
    prev_t_score = prev_data.get("trend_score", 0) if prev_data else 0
    prev_s_score = prev_data.get("sentiment_score", 0) if prev_data else 0
    prev_sd = {}
    if prev_data:
        prev_sd = prev_data.get("sentiment_detail", {}) or {}
    prev_up_ratio = prev_sd.get("up_ratio", 0)
    
    # 高潮
    if t_score >= 70 and s_score >= 85 and zt_count >= 5:
        return "高潮"
    
    # 退潮
    if t_score < 50 and s_score < 40 and avg_slope_10 < 0:
        return "退潮"
    
    # 分歧转一致
    if (prev_data and 50 <= prev_t_score < 65 and t_score > prev_t_score and 
        s_score > prev_s_score and up_ratio > 60 and zt_count >= 3):
        return "分歧转一致"
    
    # 分歧
    if (t_score >= 50 and avg_pct_today < 0 and up_ratio < 50 and s_score < prev_s_score):
        return "分歧"
    
    # 启动
    if (45 <= t_score < 60 and avg_ret_5 > 0 and avg_ret_10 < 0 and zt_count >= 3 and t_score > prev_t_score):
        return "启动"
    
    # 主升
    if (t_score >= 60 and s_score >= 60 and avg_slope_10 > 0 and td.get("pct_above_ma5", 0) >= 60):
        return "主升"
    
    # 默认
    if t_score >= 60:
        return "强势"
    elif t_score >= 50:
        return "震荡"
    else:
        return "弱势"

# ==================== 交易信号生成 ====================

def generate_trading_signal_v8(stock_feat, theme_state, quality_score, trade_mode):
    """生成V8交易信号 (增强版)
    
    Returns:
        dict: {
            "entry": float,          # 突破价/回踩价
            "trigger": str,          # 触发信号
            "stop_loss": float,      # 止损价
            "expected_3d": float,    # 3日预期收益
            "expected_5d": float,    # 5日预期收益
            "expected_10d": float,   # 10日预期收益
            "next_day_positive_prob": float,  # 次日上涨概率
            "3_day_win_prob": float,          # 3日胜率
            "5_day_win_prob": float,          # 5日胜率 (新增)
            "pattern": str,          # 结构类型
            "hold_days": str,        # 持有周期建议
        }
    """
    close = stock_feat.get("close", 0)
    ma5 = stock_feat.get("ma5_b", 0)
    ma10 = stock_feat.get("ma10_b", 0)
    ma20 = stock_feat.get("ma20_b", 0)
    pct_chg = stock_feat.get("pct_chg", 0)
    vol_ratio = stock_feat.get("vol_ratio", 1)
    leader_stability = stock_feat.get("leader_stability", 0)
    
    # 判断结构类型
    if theme_state in ("启动", "分歧转一致"):
        # 启动型：回调至MA5/MA10附近
        pattern = "启动型"
        entry = close * 0.99  # 回踩1%以内
        trigger = "放量突破" if vol_ratio >= 1.3 else "缩量回踩确认"
    elif theme_state == "主升":
        # 加速型：MA5上方加速
        pattern = "加速型"
        entry = close
        trigger = "放量加速" if pct_chg > 2 else "强势整理"
    elif theme_state == "分歧":
        # 蓄势型：等待分歧转一致
        pattern = "蓄势型"
        entry = close * 0.98
        trigger = "缩量止跌" if vol_ratio < 1.0 else "放量承接"
    else:
        # 震荡型
        pattern = "震荡型"
        entry = close
        trigger = "区间突破"
    
    # 止损价：跌破MA20 或 -5%~8%
    if trade_mode == "SHORT":
        stop_loss = close * 0.92  # -8%止损 (短线更严格)
    else:
        stop_loss = close * 0.95  # -5%止损 (中线)
    
    # 预期收益 (基于质量和状态)
    if quality_score >= 80:
        expected_3d = 8.0
        expected_5d = 15.0
        expected_10d = 25.0
        next_day_prob = 0.75
        day3_prob = 0.72
        day5_prob = 0.68
    elif quality_score >= 70:
        expected_3d = 5.0
        expected_5d = 10.0
        expected_10d = 18.0
        next_day_prob = 0.68
        day3_prob = 0.65
        day5_prob = 0.62
    elif quality_score >= 65:
        expected_3d = 3.0
        expected_5d = 6.0
        expected_10d = 10.0
        next_day_prob = 0.60
        day3_prob = 0.58
        day5_prob = 0.55
    else:
        expected_3d = 1.5
        expected_5d = 3.0
        expected_10d = 5.0
        next_day_prob = 0.52
        day3_prob = 0.50
        day5_prob = 0.48
    
    # 根据交易模式调整
    if trade_mode == "SHORT":
        expected_3d *= 1.1
        expected_5d *= 0.8  # 短线5日预期降低
        expected_10d *= 0.5  # 短线10日预期大幅降低
    else:
        expected_3d *= 0.9
        expected_5d *= 1.1
        expected_10d *= 1.2
    
    # 根据主题状态调整
    if theme_state == "高潮":
        expected_3d *= 0.7
        expected_5d *= 0.7
        next_day_prob -= 0.1
        day3_prob -= 0.08
        day5_prob -= 0.08
    elif theme_state == "分歧转一致":
        expected_3d *= 1.2
        expected_5d *= 1.15
        next_day_prob += 0.05
        day3_prob += 0.04
        day5_prob += 0.03
    
    # 根据龙头稳定性调整
    if leader_stability >= 70:
        next_day_prob += 0.05
        day3_prob += 0.05
        day5_prob += 0.05
    elif leader_stability < 50:
        next_day_prob -= 0.05
        day3_prob -= 0.05
        day5_prob -= 0.05
    
    # 持有周期建议
    if trade_mode == "SHORT":
        hold_days = "1~3天"
    else:
        hold_days = "5~20天"
    
    return {
        "entry": round(entry, 2),
        "trigger": trigger,
        "stop_loss": round(stop_loss, 2),
        "expected_3d": round(expected_3d, 1),
        "expected_5d": round(expected_5d, 1),
        "expected_10d": round(expected_10d, 1),
        "next_day_positive_prob": round(next_day_prob, 2),
        "3_day_win_prob": round(day3_prob, 2),
        "5_day_win_prob": round(day5_prob, 2),
        "pattern": pattern,
        "hold_days": hold_days,
    }


# ==================== V9 核心函数 ====================

def calc_v9_stock_filter(stock_feat):
    """
    V9 个股过滤模型 - 判断个股是否"可交易"
    
    返回: (is_tradable, entry_type, filter_reason)
    
    Entry_Type:
        🟢 试错启动（第一次放量）
        🟡 轮动切入（未加速）
        🔵 主升延续（低风险）
        🔴 不可交易（剔除）
    """
    pct_chg = stock_feat.get("pct_chg", 0)           # 当日涨幅
    pct_chg_5d = stock_feat.get("pct_chg_5d", 0)    # 5日涨幅
    zt_count_3d = stock_feat.get("zt_count_3d", 0)  # 近3日涨停次数
    trend_status = stock_feat.get("trend_status", "震荡")  # 趋势状态
    acceleration = stock_feat.get("acceleration", 50) # 加速指标 0-100
    risk_score = stock_feat.get("risk_score", 0)     # 风险分 0-100
    volume_breakout = stock_feat.get("volume_breakout", False)  # 放量突破
    second_start = stock_feat.get("second_start", False)  # 二次启动
    
    # === V9 强制剔除规则 ===
    
    # 1. 5日涨幅 > 25% 的补涨股（已兑现）
    if pct_chg_5d > 25:
        return False, "🔴不可交易", "5日涨幅过大(已兑现)"
    
    # 2. 连续涨停 >= 2 的个股（情绪尾部）
    if zt_count_3d >= 2:
        return False, "🔴不可交易", "连续涨停(情绪尾部)"
    
    # 3. 趋势下降 + 当日大涨（诱多）
    if trend_status == "下降" and pct_chg > 3:
        return False, "🔴不可交易", "下降趋势诱多"
    
    # 4. 已处于"加速>85"的龙头（末端）
    if acceleration > 85:
        return False, "🔴不可交易", "已加速末端"
    
    # 5. 风险分 > 50 且涨幅 > 10%（高位博弈）
    if risk_score > 50 and pct_chg > 10:
        return False, "🔴不可交易", "高位博弈风险大"
    
    # === V9 保留条件检查 ===
    
    # 最优交易区：5日涨幅 0~15%
    in_optimal_zone = 0 <= pct_chg_5d <= 15
    
    # 趋势：震荡 → 初升
    good_trend = trend_status in ["震荡", "初升", "上升"]
    
    # 结构：首次放量 / 二次启动
    good_structure = volume_breakout or second_start
    
    # === 判断 Entry_Type ===
    
    if acceleration < 50 and volume_breakout and in_optimal_zone and good_trend:
        # 🟢 试错启动（第一次放量）
        entry_type = "🟢试错启动"
    elif acceleration < 60 and in_optimal_zone and good_trend:
        # 🟡 轮动切入（未加速）
        entry_type = "🟡轮动切入"
    elif acceleration < 75 and pct_chg_5d < 15 and risk_score < 50:
        # 🔵 主升延续（低风险）
        entry_type = "🔵主升延续"
    else:
        # 不符合条件
        return False, "🔴不可交易", "未满足交易条件"
    
    # 最终检查：主线强度代理（使用质量分）
    quality_score = stock_feat.get("quality_score", 50)
    if quality_score < 40:
        return False, "🔴不可交易", "质量分不足"
    
    return True, entry_type, "可交易"


def calc_v9_theme_score(theme_data, stock_feats, t_detail, s_detail, c_detail, parent_industry_status=None):
    """
    V9.1 主题评分模型（新增一级产业传导机制）
    
    Theme_Score = 
        0.25 * 主线强度 
      + 0.20 * 开仓价值 
      + 0.15 * 切换概率 
      + 0.15 * 动量 slope（新增关键）
      + 0.15 * 轮动/确认强度 
      + 0.10 * (1 - 风险归一化)
      + Parent_Trend_Boost（一级传导修正）
    
    底部回暖激活机制：
    - 如果一级=底部回暖，使用Bottom_Activation_Score
    - 允许低分主题进入候选池（主线分≥30，slope>0）
    """
    trend_score = theme_data.get("trend_score", 50)
    sentiment_score = theme_data.get("sentiment_score", 50)
    crowding_score = c_detail.get("crowding_score", 50) if c_detail else 50
    
    # 主线强度
    main_strength = trend_score
    
    # 开仓价值 - 用质量分和趋势结合
    entry_score = sum(s.get("quality_score", 50) for s in stock_feats) / len(stock_feats) if stock_feats else 50
    
    # 切换概率 - 用情绪分
    switch_prob = sentiment_score
    
    # 轮动强度 - 用拥挤度代理（低拥挤=高轮动机会）
    rotation_strength = 100 - crowding_score
    
    # 确认强度 - 用上涨家数比例
    confirmation = s_detail.get("up_ratio", 50) if s_detail else 50
    
    # 风险归一化
    risk_normalized = crowding_score / 100
    
    # === V9.1新增：动量 slope ===
    avg_slope = 0
    if stock_feats:
        slope_10_list = [s.get("slope_10", 0) for s in stock_feats]
        avg_slope = np.mean(slope_10_list) if slope_10_list else 0
    # 将slope归一化到0-100
    slope_normalized = sigmoid(avg_slope * 10, k=0.5, c=50) if avg_slope != 0 else 50
    
    # === V9.1新增：Parent_Trend_Boost（一级产业传导修正）===
    parent_boost = 0
    if parent_industry_status:
        status = parent_industry_status.get("status", "")
        industry_score = parent_industry_status.get("industry_score", 50)
        
        if status == "底部回暖":
            # 底部回暖：+20%权重给slope>0的低位主题
            if avg_slope > 0:
                parent_boost = 15 * (industry_score / 100)  # 最高+15
        elif status == "启动":
            # 启动：+10%权重给龙头+中军
            parent_boost = 10 * (industry_score / 100)
        elif status == "主升":
            # 主升：强化趋势龙头（限制加速股已在个股过滤中处理）
            parent_boost = 5 * (industry_score / 100)
        elif status == "高潮":
            # 高潮：-30%二评评分（防追高）
            parent_boost = -15
        elif status == "退潮":
            # 退潮：直接降低优先级
            parent_boost = -20
    
    # === 底部回暖激活机制 ===
    is_bottom_activation = parent_industry_status and parent_industry_status.get("status") == "底部回暖"
    
    if is_bottom_activation:
        # Bottom_Activation_Score：低位主题激活评分
        # 0.4*slope + 0.3*成交活跃度 + 0.3*相对低位性
        volume_activity = entry_score  # 用质量分代理成交活跃度
        # 相对低位性：price处于历史低位（用ma20斜率代理）
        low_position = max(0, min(100, 50 + avg_slope * 20)) if avg_slope is not None else 50
        
        bottom_score = (
            0.4 * slope_normalized +
            0.3 * volume_activity +
            0.3 * low_position
        )
        
        # 允许低分主题进入：主线分≥30即可，slope>0必须
        if main_strength >= 30 and avg_slope > 0:
            # 使用bottom_score作为最终分，但加上parent_boost
            base_score = bottom_score * 0.7 + parent_boost
        else:
            base_score = -100  # 不满足底部激活条件
    else:
        # 正常评分
        base_score = (
            0.25 * main_strength +
            0.20 * entry_score +
            0.15 * switch_prob +
            0.15 * slope_normalized +  # V9.1新增slope
            0.15 * confirmation +
            0.10 * (1 - risk_normalized) * 100
        ) + parent_boost
    
    # === 结构健康度：未加速成份股比例 ===
    non_accelerated = sum(1 for s in stock_feats if s.get("acceleration", 50) < 70)
    structure_health = (non_accelerated / len(stock_feats) * 100) if stock_feats else 50
    
    # === 可交易个股质量分 ===
    tradable_quality = sum(s.get("quality_score", 50) for s in stock_feats) / len(stock_feats) if stock_feats else 50
    
    # 最终分 = 基础分 + 结构健康度加成（修正：使用加权而不是简单相加）
    # 结构健康度权重20%，可交易质量权重10%
    final_score = base_score * 0.70 + structure_health * 0.20 + tradable_quality * 0.10
    
    # V9.1新增：返回额外诊断信息
    diagnostics = {
        "parent_boost": parent_boost,
        "slope_normalized": slope_normalized,
        "avg_slope": avg_slope,
        "is_bottom_activation": is_bottom_activation,
        "structure_health": round(structure_health, 1),
        "tradable_quality": round(tradable_quality, 1),
    }
    
    return max(0, min(100, final_score)), diagnostics


def calc_v9_entry_type(theme_data, stock_feats, parent_industry_status=None):
    """
    V9.1 计算主题整体 Entry_Type
    基于主题内可交易个股的Entry_Type分布 + 一级产业状态
    
    新增"底部启动"类型：当一级=底部回暖 且主题slope>0时
    """
    if not stock_feats:
        return "🔴不可交易"
    
    entry_types = [s.get("entry_type", "🔴不可交易") for s in stock_feats]
    
    # 统计各类型数量
    trial_start = sum(1 for t in entry_types if "试错" in t)
    rotation = sum(1 for t in entry_types if "轮动" in t)
    main_cont = sum(1 for t in entry_types if "主升" in t)
    not_tradable = sum(1 for t in entry_types if "不可" in t)
    
    total = len(entry_types)
    
    # === V9.1新增：一级产业底部回暖检测 ===
    is_bottom = parent_industry_status and parent_industry_status.get("status") == "底部回暖"
    avg_slope = 0
    if stock_feats:
        slopes = [s.get("slope_10", 0) for s in stock_feats]
        avg_slope = np.mean(slopes) if slopes else 0
    
    # 判断主题整体Entry_Type
    if not_tradable == total:
        return "🔴不可交易"
    elif is_bottom and avg_slope > 0 and (trial_start + rotation) >= total * 0.4:
        # 底部回暖 + slope>0 + 有试错/轮动股 → 底部启动
        return "🟢底部启动"
    elif trial_start + rotation >= total * 0.6:
        return "🟢试错启动" if trial_start >= rotation else "🟡轮动切入"
    elif main_cont >= total * 0.5:
        return "🔵主升延续"
    elif rotation >= total * 0.4:
        return "🟡轮动切入"
    else:
        return "🟡轮动切入"


def filter_v9_stocks(stock_feats):
    """
    V9 个股筛选 - 返回可交易的股票列表
    """
    tradable = []
    for s in stock_feats:
        is_tradable, entry_type, reason = calc_v9_stock_filter(s)
        if is_tradable:
            s["entry_type"] = entry_type
            s["filter_reason"] = reason
            tradable.append(s)
    
    # 按质量分排序
    tradable.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
    
    # === V9 选股策略 ===
    
    # 1. 龙头候选：成份内涨幅排名Top 1~2，但必须满足5日涨幅 < 20%
    leaders = [s for s in tradable if s.get("pct_chg_5d", 0) < 20][:2]
    
    # 2. 中军：评分 Top 30%，且趋势非下降
    mid_tier = [s for s in tradable 
                if s.get("trend_status", "震荡") != "下降"
                and s not in leaders][:max(1, len(tradable) // 3)]
    
    # 3. 补涨：未加速，近3日未涨停
    catch_up = [s for s in tradable 
                if s.get("acceleration", 50) < 60
                and s.get("zt_count_3d", 0) == 0
                and s not in leaders
                and s not in mid_tier]
    
    # 合并结果，最多3只
    result = leaders + mid_tier + catch_up
    return result[:3]


def get_prev_day_theme_data():
    """获取前一日主题数据"""
    if not os.path.exists(OUTPUT_DB):
        return {}
    
    try:
        conn = sqlite3.connect(OUTPUT_DB)
        cur = conn.cursor()
        cur.execute("""
            SELECT theme, trend_score, sentiment_score, trend_detail, sentiment_detail
            FROM theme_scores
            WHERE trade_date = (
                SELECT MAX(trade_date) FROM theme_scores WHERE trade_date < ?
            )
        """, (TRADE_DATE,))
        rows = cur.fetchall()
        conn.close()
        
        result = {}
        for row in rows:
            theme, trend_score, sentiment_score = row[0], row[1], row[2]
            result[theme] = {
                "trend_score": trend_score,
                "sentiment_score": sentiment_score,
            }
        return result
    except Exception:
        return {}

def save_to_sqlite_v7(results):
    """保存到SQLite数据库"""
    conn = sqlite3.connect(OUTPUT_DB)
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS theme_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            theme TEXT,
            macro_score REAL,
            theme_score REAL,
            emotion_score REAL,
            cycle_score REAL,
            crowding_score REAL,
            final_score REAL,
            theme_state TEXT,
            rotation_cycle TEXT,
            n_stocks INTEGER,
            UNIQUE(trade_date, theme)
        )
    ''')
    
    for r in results:
        cur.execute('''
            INSERT OR REPLACE INTO theme_scores 
            (trade_date, theme, macro_score, theme_score, emotion_score, cycle_score, crowding_score, final_score, theme_state, rotation_cycle, n_stocks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            TRADE_DATE, r["theme"], r.get("macro_score", 0), r.get("theme_score", 0),
            r.get("emotion_score", 0), r.get("cycle_score", 0), r.get("crowding_score", 0),
            r.get("final_score", 0), r.get("theme_state", ""), r.get("rotation_cycle", ""),
            r.get("n_stocks", 0)
        ))
    
    conn.commit()
    conn.close()


def get_industry_trend_analysis(results):
    """获取一级产业评分动态分析"""
    try:
        # 从当前结果中提取一级产业信息（去重）
        industry_scores = defaultdict(dict)  # 使用dict去重
        for r in results:
            category = r.get("category", "其他")
            theme_name = r.get("theme", "")
            if category and theme_name:
                # 使用theme_name作为key，避免重复
                industry_scores[category][theme_name] = {
                    "theme": theme_name,
                    "macro_score": r.get("macro_score", 0),
                    "theme_score": r.get("theme_score", 0),
                    "final_score": r.get("final_score", 0),
                    "emotion_score": r.get("emotion_score", 0),
                }
        
        # 转换为列表格式
        industry_scores = {k: list(v.values()) for k, v in industry_scores.items()}
        
        # 从数据库读取历史数据
        industry_history = defaultdict(list)
        if os.path.exists(OUTPUT_DB):
            conn = sqlite3.connect(OUTPUT_DB)
            try:
                # 获取最近20个交易日的数据
                trade_dates_df = pd.read_sql(
                    "SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 20",
                    conn
                )
                trade_dates = trade_dates_df['trade_date'].tolist()
                
                # 需要从theme_graph_v3.json获取主题到一级产业的映射
                _, theme_to_category = load_theme2_json()
                
                # 统计每个一级产业的历史评分
                for td in trade_dates:
                    day_df = pd.read_sql(
                        f"SELECT theme, macro_score, theme_score, final_score, emotion_score FROM theme_scores WHERE trade_date = '{td}'",
                        conn
                    )
                    if not day_df.empty:
                        for _, row in day_df.iterrows():
                            theme = row['theme']
                            cat = theme_to_category.get(theme, "其他")
                            industry_history[cat].append({
                                "trade_date": td,
                                "macro_score": row.get("macro_score", 0),
                                "theme_score": row.get("theme_score", 0),
                                "final_score": row.get("final_score", 0),
                                "emotion_score": row.get("emotion_score", 0),
                            })
            except Exception as e:
                print(f"[产业分析] 读取历史数据失败: {e}")
            finally:
                conn.close()
        
        # 计算每个一级产业的综合评分和趋势
        industry_analysis = []
        for industry, themes in industry_scores.items():
            if industry == "其他":
                continue
            
            # 当前评分
            avg_macro = np.mean([t["macro_score"] for t in themes])
            avg_theme = np.mean([t["theme_score"] for t in themes])
            avg_final = np.mean([t["final_score"] for t in themes])
            avg_emotion = np.mean([t["emotion_score"] for t in themes])
            
            # 历史趋势
            history = industry_history.get(industry, [])
            trend_5d = "→"
            trend_20d = "→"
            
            if len(history) >= 5:
                recent_5 = [h["final_score"] for h in history[:5]]
                prev_5 = [h["final_score"] for h in history[5:10]] if len(history) >= 10 else recent_5
                if len(prev_5) > 0:
                    change = np.mean(recent_5) - np.mean(prev_5)
                    if change > 3:
                        trend_5d = "↑"
                    elif change < -3:
                        trend_5d = "↓"
            
            if len(history) >= 20:
                recent_10 = [h["final_score"] for h in history[:10]]
                prev_10 = [h["final_score"] for h in history[10:20]]
                if len(prev_10) > 0:
                    change = np.mean(recent_10) - np.mean(prev_10)
                    if change > 5:
                        trend_20d = "↑↑"
                    elif change > 2:
                        trend_20d = "↑"
                    elif change < -5:
                        trend_20d = "↓↓"
                    elif change < -2:
                        trend_20d = "↓"
            
            # 判断产业状态
            if avg_final >= 60 and trend_5d in ["↑", "↑↑"]:
                status = "强势上升"
            elif avg_final >= 55:
                status = "稳健运行"
            elif trend_5d in ["↑", "↑↑"]:
                status = "底部回暖"
            elif trend_5d in ["↓", "↓↓"]:
                status = "高位回落"
            else:
                status = "震荡整理"
            
            industry_analysis.append({
                "industry": industry,
                "theme_count": len(themes),
                "avg_macro": round(avg_macro, 1),
                "avg_theme": round(avg_theme, 1),
                "avg_final": round(avg_final, 1),
                "avg_emotion": round(avg_emotion, 1),
                "trend_5d": trend_5d,
                "trend_20d": trend_20d,
                "status": status,
                "themes": [t["theme"] for t in themes],
            })
        
        # 按综合评分排序
        industry_analysis.sort(key=lambda x: x["avg_final"], reverse=True)
        
        return industry_analysis
    
    except Exception as e:
        print(f"[产业分析] 计算失败: {e}")
        return []


def save_report_v8(results):
    """保存V8分析报告到文件（TXT格式）"""
    report_path = os.path.join(REPORT_DIR, f"theme_v8_report_{TRADE_DATE}.txt")
    
    buf = []
    def w(s=""):
        buf.append(s)
    
    w("="*80)
    w("                    明日可交易主题决策引擎 V8")
    w("="*80)
    w(f"报告日期: {TRADE_DATE}")
    w("")
    
    # 统计信息
    w(f"【概览】")
    w(f"  可交易主题数量: {len(results)} 个")
    w("")
    
    # 一级产业评分动态分析
    w(f"【一级产业评分动态分析】")
    industry_analysis = get_industry_trend_analysis(results)
    if industry_analysis:
        w("-"*80)
        w(f"排名  一级产业         主题数  产业分  主题分  情绪分  综合分  5日趋势  20日趋势  状态")
        w("-"*80)
        for i, ind in enumerate(industry_analysis, 1):
            trend_5d = ind["trend_5d"]
            trend_20d = ind["trend_20d"]
            # 趋势符号
            trend_5d_icon = {"↑↑": "UP2", "↑": "UP ", "→": "-- ", "↓": "DN ", "↓↓": "DN2"}.get(trend_5d, "-- ")
            trend_20d_icon = {"↑↑": "UP2", "↑": "UP ", "→": "-- ", "↓": "DN ", "↓↓": "DN2"}.get(trend_20d, "-- ")
            # 状态标签
            status_icon = {
                "强势上升": "强势上升",
                "稳健运行": "稳健运行",
                "底部回暖": "底部回暖",
                "高位回落": "高位回落",
                "震荡整理": "震荡整理"
            }.get(ind["status"], ind["status"])
            
            w(f"{i:2d}    {ind['industry']:14s}  {ind['theme_count']:4d}   {ind['avg_macro']:5.1f}  {ind['avg_theme']:5.1f}  {ind['avg_emotion']:5.1f}  {ind['avg_final']:5.1f}    {trend_5d_icon}      {trend_20d_icon}     {status_icon}")
        w("")
        
        # 产业详细分析
        w("【产业趋势解读】")
        for ind in industry_analysis[:5]:  # 只显示前5个产业
            w(f"  [{ind['industry']}] ({ind['status']})")
            w(f"    综合评分: {ind['avg_final']} | 产业分: {ind['avg_macro']} | 情绪分: {ind['avg_emotion']}")
            w(f"    5日趋势: {ind['trend_5d']} | 20日趋势: {ind['trend_20d']}")
            w(f"    关联主题: {', '.join(ind['themes'][:3])}")
            w("")
    else:
        w("  暂无产业分析数据")
        w("")
    
    w("="*80)
    w("")
    
    # 详细主题信息
    for i, r in enumerate(results, 1):
        theme_state = r["theme_state"]
        crowding = r["crowding_score"]
        trade_mode = r.get("trade_mode", "MID")
        mode_tag = "[SHORT]" if trade_mode == "SHORT" else "[MID  ]"
        
        w(f"{i}. {r['theme']} {mode_tag}")
        w("-"*60)
        
        # 评分详情
        w("【评分详情】")
        w(f"  综合评分       : {r['final_score']}")
        w(f"  产业趋势分     : {r['macro_score']}")
        w(f"  主题强度分     : {r['theme_score']}")
        w(f"  情绪分         : {r['emotion_score']}")
        w(f"  龙头稳定性分   : {r.get('leader_stability_score', 0)}")
        w(f"  周期位置分     : {r['cycle_score']}")
        w(f"  拥挤度分       : {r['crowding_score']}")
        w(f"  阶段           : {r['theme_state']}")
        w(f"  模式           : {trade_mode}")
        w("")
        
        # 风险提示
        if crowding > 85:
            w(f"【风险警告】拥挤度过高 ({crowding})，不建议追高")
        elif crowding > 70:
            w(f"【风险提示】拥挤度偏高 ({crowding})，谨慎参与")
        
        # 最优3股
        w("【最优3股】")
        stock_list = r.get("top_stocks", [])[:3]
        if stock_list:
            w(f"  排名  角色    股票名称      代码       现价    涨幅    换手率  量比   质量分  稳定性  结构形态  交易类型")
            w(f"  ----  ----    --------      ----       ----   ----    ------  ----  ------  ------  --------  --------")
            for j, s in enumerate(stock_list, 1):
                role = "龙头" if j == 1 else ("中军" if j == 2 else "弹性")
                stability = s.get("leader_stability", 0)
                stability_tag = "PASS" if stability >= 70 else ("WARN" if stability >= 50 else "FAIL")
                entry_type = s.get("entry_type", "🔴不可交易")  # V9 Entry_Type
                # 简化entry_type显示（去掉emoji）
                entry_short = entry_type.replace("🟢", "").replace("🟡", "").replace("🔵", "").replace("🔴", "")
                w(f"  {j:2d}    {role:4s}    {s['name']:10s}  {s['ts_code']:10s}  {s['close']:6.2f}  {s['pct_chg']:+.2f}%   {s['turnover']:5.2f}%  {s['vol_ratio']:4.2f}  {s['quality_score']:5d}   {stability_tag}    {s['pattern']}  {entry_short}")
        w("")
        
        # 交易计划
        w("【交易计划】")
        if stock_list:
            lead = stock_list[0]
            w(f"  买入点         : {lead['entry']:.2f} (现价 {lead['close']:.2f})")
            w(f"  触发条件       : {lead['trigger']}")
            w(f"  止损位         : {lead['stop_loss']:.2f}")
            w(f"  持有周期       : {lead.get('hold_days', '5~20天')}")
            w("")
            w("【概率】")
            w(f"  1日胜率        : {lead['next_day_positive_prob']*100:.0f}%")
            w(f"  3日胜率        : {lead.get('3_day_win_prob', lead['next_day_positive_prob'])*100:.0f}%")
            w(f"  5日胜率        : {lead.get('5_day_win_prob', lead['next_day_positive_prob'])*100:.0f}%")
            w("")
            w("【预期收益】")
            w(f"  3日预期        : {lead['expected_3d']}%")
            w(f"  5日预期        : {lead['expected_5d']}%")
            w(f"  10日预期       : {lead['expected_10d']}%")
        else:
            w("  暂无合适标的")
        w("")
        w("-"*60)
        w("")
    
    w("="*80)
    w(f"报告生成完成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w("="*80)
    
    report_content = "\n".join(buf)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    print(f"[报告已保存] {report_path}")
    return report_content


def generate_ai_report(results, raw_report, industry_analysis=None):
    """使用Deepseek对报告进行AI分析，生成阅读性更强的报告"""
    if not raw_report:
        print("[AI报告] 原始报告为空，跳过AI分析")
        return ""
    
    prompt = f"""
以下是一份A股量化交易决策引擎生成的分析报告，请你将其转化为一份阅读性更强、更专业的投资分析报告：

---原始数据---
{raw_report}
---原始数据结束---

请按照以下要求进行分析和重写：

## 格式要求：
1. 使用Markdown格式输出
2. 标题清晰，层次分明
3. 使用表格展示数据
4. 语言简洁专业但易于理解

## 分析要求：
1. **市场概览**：总结当前市场环境和整体趋势
2. **主题分析**：
   - 对每个主题进行深度分析
   - 分析主题的阶段（启动/主升/高潮/分歧/退潮）
   - 评估风险等级（基于拥挤度）
   - 给出投资建议
3. **个股推荐**：
   - 对每只股票进行简要分析
   - 说明买点和止损策略
   - 评估上涨潜力
4. **风险提示**：列出主要风险点和注意事项

## 输出结构：
```markdown
# 📊 明日可交易主题分析报告

## 一、市场概览
- 日期：{TRADE_DATE}
- 可交易主题数量：X个
- 市场整体风险等级：低/中/高

## 二、主题深度分析

### 🥇 [主题名称]
| 指标 | 数值 |
|------|------|
| 综合评分 | X分 |
| 阶段 | 启动/主升/高潮/分歧/退潮 |
| 拥挤度 | X分 |
| 风险等级 | 低/中/高 |

**投资建议**：[简要说明]

**TOP3标的**：
1. [股票名] - [买点建议] - [止损位]
2. [股票名] - [买点建议] - [止损位]
3. [股票名] - [买点建议] - [止损位]

...（其他主题）

## 三、投资策略总结
- 整体仓位建议：X%
- 重点关注主题：[主题列表]
- 风险提示：[注意事项]
```

请直接输出报告内容，不要包含其他说明。
"""
    
    print("\n[Deepseek] 正在生成AI分析报告...")
    ai_report = deepseek_analyze(prompt)
    
    if ai_report:
        # 保存AI分析报告 (MD格式)
        ai_report_path = os.path.join(REPORT_DIR, f"theme_v7_ai_report_{TRADE_DATE}.md")
        with open(ai_report_path, "w", encoding="utf-8") as f:
            f.write(ai_report)
        print(f"[Save] AI分析报告(MD): {ai_report_path}")
        
        # 生成HTML格式报告（包含一级产业分析）
        html_report = generate_html_report(ai_report, results, industry_analysis)
        html_report_path = os.path.join(REPORT_DIR, f"theme_v7_ai_report_{TRADE_DATE}.html")
        with open(html_report_path, "w", encoding="utf-8") as f:
            f.write(html_report)
        print(f"[Save] AI分析报告(HTML): {html_report_path}")
        
        # 打印AI报告摘要
        print("\n" + "=" * 80)
        print("【AI分析报告摘要】")
        print("=" * 80)
        print(ai_report[:2000] + "..." if len(ai_report) > 2000 else ai_report)
        print("\n" + "=" * 80)
    
    return ai_report


def generate_html_report(md_content, results, industry_analysis=None):
    """将MD格式报告转换为HTML格式"""
    
    # 生成产业分析HTML
    industry_html = ""
    if industry_analysis:
        industry_rows = ""
        for i, ind in enumerate(industry_analysis, 1):
            trend_5d_icon = {"↑↑": "⬆⬆", "↑": "⬆", "→": "➡", "↓": "⬇", "↓↓": "⬇⬇"}.get(ind["trend_5d"], "➡")
            trend_20d_icon = {"↑↑": "⬆⬆", "↑": "⬆", "→": "➡", "↓": "⬇", "↓↓": "⬇⬇"}.get(ind["trend_20d"], "➡")
            status_class = {
                "强势上升": "status-up2",
                "稳健运行": "status-stable",
                "底部回暖": "status-up",
                "高位回落": "status-down",
                "震荡整理": "status-flat"
            }.get(ind["status"], "status-flat")
            industry_rows += f"""
            <tr>
                <td><strong>{i}</strong></td>
                <td><strong>{ind['industry']}</strong></td>
                <td>{ind['theme_count']} 个</td>
                <td>{ind['avg_macro']:.1f}</td>
                <td>{ind['avg_theme']:.1f}</td>
                <td>{ind['avg_emotion']:.1f}</td>
                <td><strong>{ind['avg_final']:.1f}</strong></td>
                <td>{trend_5d_icon}</td>
                <td>{trend_20d_icon}</td>
                <td><span class="{status_class}">{ind['status']}</span></td>
            </tr>"""
        
        industry_html = f"""
        <div class="section">
            <h2 class="section-title">🏭 一级产业评分动态分析</h2>
            <table>
                <thead>
                    <tr>
                        <th>排名</th>
                        <th>一级产业</th>
                        <th>主题数</th>
                        <th>产业分</th>
                        <th>主题分</th>
                        <th>情绪分</th>
                        <th>综合分</th>
                        <th>5日趋势</th>
                        <th>20日趋势</th>
                        <th>状态</th>
                    </tr>
                </thead>
                <tbody>
                    {industry_rows}
                </tbody>
            </table>
        </div>
        """
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>明日可交易主题分析报告 - {TRADE_DATE}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%); min-height: 100vh; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 15px; margin-bottom: 25px; box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3); }}
        .header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .header .date {{ font-size: 16px; opacity: 0.9; }}
        .stats {{ display: flex; gap: 20px; margin-top: 15px; }}
        .stat-box {{ background: rgba(255,255,255,0.2); padding: 12px 20px; border-radius: 8px; }}
        .stat-label {{ font-size: 12px; opacity: 0.8; }}
        .stat-value {{ font-size: 20px; font-weight: bold; }}
        
        .section {{ background: white; border-radius: 15px; padding: 25px; margin-bottom: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.08); }}
        .section-title {{ color: #333; font-size: 20px; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 3px solid #667eea; position: relative; }}
        .section-title::after {{ content: ''; position: absolute; bottom: -3px; left: 0; width: 60px; height: 3px; background: #764ba2; }}
        
        .theme-card {{ border: 2px solid #e0e5ec; border-radius: 12px; padding: 20px; margin-bottom: 20px; transition: all 0.3s ease; }}
        .theme-card:hover {{ border-color: #667eea; box-shadow: 0 5px 20px rgba(102, 126, 234, 0.2); }}
        .theme-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .theme-name {{ font-size: 18px; font-weight: bold; color: #333; }}
        .theme-mode {{ padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; }}
        .mode-short {{ background: #fff3cd; color: #856404; }}
        .mode-mid {{ background: #d4edda; color: #155724; }}
        
        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; color: #666; font-weight: 600; }}
        tr:hover {{ background: #f8f9fa; }}
        
        .stock-row {{ display: flex; gap: 20px; padding: 12px 0; border-bottom: 1px dashed #eee; }}
        .stock-info {{ flex: 1; }}
        .stock-name {{ font-weight: bold; color: #333; }}
        .stock-code {{ color: #999; font-size: 14px; }}
        .stock-meta {{ display: flex; gap: 15px; margin-top: 5px; font-size: 14px; }}
        .meta-item {{ color: #666; }}
        .meta-value {{ font-weight: bold; }}
        
        .risk-low {{ color: #28a745; }}
        .risk-mid {{ color: #ffc107; }}
        .risk-high {{ color: #dc3545; }}
        
        .status-up2 {{ background: #d4edda; color: #155724; padding: 3px 8px; border-radius: 4px; }}
        .status-up {{ background: #e8f5e9; color: #2e7d32; padding: 3px 8px; border-radius: 4px; }}
        .status-stable {{ background: #e3f2fd; color: #1565c0; padding: 3px 8px; border-radius: 4px; }}
        .status-down {{ background: #fff3e0; color: #e65100; padding: 3px 8px; border-radius: 4px; }}
        .status-flat {{ background: #f5f5f5; color: #616161; padding: 3px 8px; border-radius: 4px; }}
        
        .summary {{ background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); border-left: 5px solid #667eea; padding: 20px; border-radius: 0 10px 10px 0; }}
        .summary h3 {{ margin-bottom: 10px; color: #333; }}
        .summary ul {{ padding-left: 20px; }}
        .summary li {{ margin-bottom: 8px; color: #555; }}
        
        .footer {{ text-align: center; padding: 30px; color: #999; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 明日可交易主题分析报告</h1>
            <div class="date">报告日期：{TRADE_DATE}</div>
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-label">可交易主题</div>
                    <div class="stat-value">{len(results)} 个</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">分析时间</div>
                    <div class="stat-value">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
                </div>
            </div>
        </div>
        
        {industry_html}
        
        <div class="section">
            <h2 class="section-title">📈 主题列表</h2>
            {generate_html_theme_list(results)}
        </div>
        
        <div class="section">
            <h2 class="section-title">📋 投资策略总结</h2>
            <div class="summary">
                <h3>📌 核心观点</h3>
                <ul>
                    <li>当前共 <strong>{len(results)} 个</strong> 可交易主题</li>
                    <li>建议重点关注高评分主题，控制仓位</li>
                    <li>严格执行止损策略，控制风险</li>
                </ul>
            </div>
        </div>
        
        <div class="section">
            <h2 class="section-title">⚠️ 风险提示</h2>
            <div style="color: #666; line-height: 1.8;">
                <p>本报告仅供参考，不构成任何投资建议。</p>
                <p>市场有风险，投资需谨慎。</p>
                <p>请投资者根据自身风险承受能力做出投资决策。</p>
            </div>
        </div>
        
        <div class="footer">
            <p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>数据来源：Tushare、东方财富</p>
        </div>
    </div>
</body>
</html>"""
    return html_content


def generate_html_theme_list(results):
    """生成主题列表的HTML内容"""
    html = ""
    for i, r in enumerate(results, 1):
        trade_mode = r.get("trade_mode", "MID")
        mode_class = "mode-short" if trade_mode == "SHORT" else "mode-mid"
        mode_text = "短线" if trade_mode == "SHORT" else "中线"
        risk_level = "低" if r["crowding_score"] < 50 else ("中" if r["crowding_score"] < 70 else "高")
        risk_class = f"risk-{risk_level}"
        
        html += f"""
        <div class="theme-card">
            <div class="theme-header">
                <span class="theme-name">{i}. {r['theme']}</span>
                <span class="theme-mode {mode_class}">{mode_text}</span>
            </div>
            
            <table>
                <tr><th>指标</th><th>数值</th></tr>
                <tr><td>综合评分</td><td><strong>{r['final_score']}</strong></td></tr>
                <tr><td>产业趋势分</td><td>{r['macro_score']}</td></tr>
                <tr><td>主题强度分</td><td>{r['theme_score']}</td></tr>
                <tr><td>情绪分</td><td>{r['emotion_score']}</td></tr>
                <tr><td>阶段</td><td>{r['theme_state']}</td></tr>
                <tr><td>拥挤度</td><td>{r['crowding_score']}</td></tr>
                <tr><td>风险等级</td><td><span class="{risk_class}"><strong>{risk_level}</strong></span></td></tr>
            </table>
            
            <h4 style="margin-top: 15px; margin-bottom: 10px; color: #333;">⭐ 最优3股</h4>
        """
        
        for j, s in enumerate(r.get("top_stocks", [])[:3], 1):
            role = "龙头" if j == 1 else ("中军" if j == 2 else "弹性")
            html += f"""
            <div class="stock-row">
                <div class="stock-info">
                    <div class="stock-name">{role}: {s['name']} <span class="stock-code">({s['ts_code']})</span></div>
                    <div class="stock-meta">
                        <span class="meta-item">现价: <span class="meta-value">{s['close']:.2f}</span></span>
                        <span class="meta-item">涨幅: <span class="meta-value">{s['pct_chg']:+.2f}%</span></span>
                        <span class="meta-item">质量分: <span class="meta-value">{s['quality_score']}</span></span>
                    </div>
                </div>
            </div>
            """
        
        html += f"""
            <div style="margin-top: 15px; padding: 12px; background: #f8f9fa; border-radius: 8px;">
                <strong>🎯 交易计划：</strong><br>
                买入点: {r['top_stocks'][0]['entry']:.2f} | 触发条件: {r['top_stocks'][0]['trigger']} | 止损位: {r['top_stocks'][0]['stop_loss']:.2f}
            </div>
        </div>
        """
    
    return html

# ==================== 主函数 ====================

def main(trade_date=None):
    # 如果没有传入日期，使用全局日期
    global TRADE_DATE, START_DATE, TRADE_MODE_FILTER, SKIP_AI_ANALYSIS, TOP_N_RESULTS
    
    if trade_date is None:
        trade_date = TRADE_DATE
    else:
        TRADE_DATE = trade_date
        START_DATE = (datetime.strptime(TRADE_DATE, "%Y%m%d") - timedelta(days=N_DAYS + 30)).strftime("%Y%m%d")
    
    print("=" * 80)
    print("明日可交易主题决策引擎 V8 (实盘版)")
    print(f"分析日期: {TRADE_DATE}")
    print("=" * 80)
    
    # 1. 加载 theme_graph_v3.json（已转换为 HOT_THEMES 格式）
    hot_themes, theme_to_category = load_theme2_json()
    print(f"[Theme3] 加载 {len(hot_themes)} 个二级主题")
    
    # 2. 加载已生成的主题股池数据
    theme_pool_map = load_theme_pools(TRADE_DATE)
    
    # 3. 获取市场数据
    dc_df = get_dc_members()
    stock_basic = get_stock_basic()
    daily_basic = get_daily_basic()
    print(f"[Data] stock_basic: {len(stock_basic)}  daily_basic: {len(daily_basic)}")
    
    # 4. 从股池数据构建主题-股票映射（替代 match_theme_stocks_v2）
    # 格式: {theme_name: {code: {score, role, ...}, ...}}
    theme_stock_map = {}
    name_map_basic = {}
    stock_industry = {}
    stock_concepts = defaultdict(list)
    
    if stock_basic is not None and not stock_basic.empty:
        for _, row in stock_basic.iterrows():
            name_map_basic[row["ts_code"]] = row.get("name", "")
            stock_industry[row["ts_code"]] = row.get("industry", "")
    
    # 从股池数据提取成份股
    all_codes = set()
    if theme_pool_map:
        for theme_name, pool_data in theme_pool_map.items():
            if theme_name not in hot_themes:
                continue
            
            theme_stock_map[theme_name] = {}
            # 合并核心池、扩展池、潜伏池
            for pool_type in ["core_pool", "expansion_pool", "latent_pool"]:
                pool = pool_data.get(pool_type, [])
                for stock in pool:
                    code = stock.get("stock_code")
                    if code:
                        theme_stock_map[theme_name][code] = {
                            "score": stock.get("theme_score", 50),
                            "role": stock.get("role", ""),
                            "via": "pool",
                            "chain_distance": 0 if pool_type == "core_pool" else 1
                        }
                        all_codes.add(code)
        print(f"[Pool] 从股池数据加载 {len(all_codes)} 只成份股")
    else:
        # 备用方案：使用传统匹配算法
        print("[Pool] 未找到股池数据，使用传统匹配算法")
        theme_stock_map, name_map_basic, stock_industry, stock_concepts = match_theme_stocks_v2(hot_themes, dc_df, stock_basic)
        
        for tn, m in theme_stock_map.items():
            all_codes.update(m.keys())
        print(f"[Match] 命中成份股去重: {len(all_codes)} 只")
    
    # 5. 获取K线数据
    kline_df = get_daily_kline(list(all_codes), START_DATE, TRADE_DATE)
    print(f"[KLine] {len(kline_df)} 条 K 线记录")
    
    # 6. 获取市场基准
    idx_df = get_index_kline("000300.SH")
    market_ret_10 = 0.0
    if idx_df is not None and not idx_df.empty:
        idx_df = idx_df.sort_values("trade_date")
        closes = idx_df["close"].astype(float).values
        if len(closes) >= 11:
            market_ret_10 = (closes[-1] / closes[-11] - 1) * 100
    print(f"[Index] 沪深300 近10日收益: {market_ret_10:+.2f}%")
    
    # 7. 获取前一日数据
    prev_theme_data = get_prev_day_theme_data()
    if prev_theme_data:
        print(f"[State] 获取前一日 {len(prev_theme_data)} 个主题数据")
    
    # 8. 按类别分组计算 macro_score
    kline_groups = {}
    if not kline_df.empty:
        for code, sub in kline_df.groupby("ts_code"):
            kline_groups[code] = sub
    
    # 计算每个主题的评分
    results = []
    theme_stock_details = {}  # 存储每只股票的质量分和特征
    
    # V9.1: 一级产业状态映射（将在循环中逐步填充）
    industry_status_map = {}
    
    for theme_name, cfg in hot_themes.items():
        matched = theme_stock_map.get(theme_name, {})
        if not matched:
            continue
        
        mcap_dict = {}
        if not daily_basic.empty:
            mcap_dict = {r["ts_code"]: r for _, r in daily_basic.iterrows()}
        
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        
        rows = []
        theme_total_amount = 0  # 板块总成交额
        
        for code, meta in matched.items():
            kdf = kline_groups.get(code)
            if kdf is None or len(kdf) < 6:
                continue
            feat = per_stock_features(kdf)
            if feat is None:
                continue
            
            if not daily_basic.empty:
                db_one = daily_basic[daily_basic['ts_code'] == code]
                if not db_one.empty:
                    turnover = db_one.iloc[0].get('turnover_rate', 0) or 0
                    feat['turnover'] = float(turnover)
            
            concepts = stock_concepts.get(code, [])
            concepts_str = "|".join(concepts)
            purity = 0
            for kw in keyword_list:
                if kw in concepts_str:
                    purity += 1
            for c in concept_list:
                if c in concepts:
                    purity += 1
            if _in_industry_list(stock_industry.get(code, ""), industry_list):
                purity += 1
            
            mv = mcap_dict.get(code, {}).get("total_mv", 0) or 0
            feat["ts_code"] = code
            feat["name"] = name_map_basic.get(code, code)
            feat["purity"] = purity
            feat["total_mv"] = mv
            feat["industry_match"] = meta.get("industry_match", False)
            feat["chain_distance"] = meta.get("chain_distance", 2)
            rows.append(feat)
            theme_total_amount += feat.get("amount_latest", 0)
        
        if len(rows) < MIN_STOCKS:
            continue
        
        # 全成份股统计
        all_zt_count = sum(1 for r in rows if r.get("zt_flag") == 1)
        all_up_count = sum(1 for r in rows if r.get("pct_chg", 0) > 0)
        all_total = len(rows)
        
        # 按市值权重排序，取前30只
        for r in rows:
            r["mcap_w"] = (r["total_mv"] / 10000) ** 0.5 * 0.8 + r["purity"] * 2
            r["mcap_w"] *= 1.0 if r["industry_match"] else 0.5
        rows.sort(key=lambda x: x["mcap_w"], reverse=True)
        top_rows = rows[:TOP_N_PER_THEME]
        
        # 计算趋势分和情绪分
        t_score, t_detail = calc_trend_score(top_rows, market_ret_10)
        s_score, s_detail = calc_sentiment_score(rows, market_ret_10)
        
        # 计算市场总成交额 (估算)
        market_total_amount = 15000  # 万亿，简化处理
        
        # 计算拥挤度分
        c_score, c_detail = calc_crowding_score(top_rows, theme_total_amount, market_total_amount)
        
        # 判断主题状态
        theme_result = {
            "theme": theme_name,
            "n_stocks": all_total,
            "trend_score": t_score,
            "sentiment_score": s_score,
            "trend_detail": t_detail,
            "sentiment_detail": s_detail,
        }
        prev_data = prev_theme_data.get(theme_name)
        theme_state = calc_theme_state_v2(theme_result, prev_data)
        
        # 轮动周期 (简化版)
        rotation_cycle = "上升中" if t_score >= 60 else ("启动" if t_score >= 45 else "退潮回避")
        
        # 计算周期分
        cycle_score = calc_cycle_score(theme_state, rotation_cycle)
        
        # 计算一级产业趋势分 (简化：使用主题趋势分作为代理)
        category_name = theme_to_category.get(theme_name, "")
        macro_score = t_score  # 简化处理
        
        # V8: 计算每只股票的质量分和龙头稳定性
        quality_stocks = []
        leader_stability_scores = []
        
        for r in top_rows:
            quality, q_detail = calc_quality_score(r, t_detail.get("avg_ret_10", 0))
            if quality >= 60:  # V8: 质量分>=60才保留
                # V8: 计算龙头稳定性
                leader_stability, ls_detail = calc_leader_stability_score(r, rows)
                r["quality_score"] = quality
                r["quality_detail"] = q_detail
                r["leader_stability"] = leader_stability
                r["leader_stability_detail"] = ls_detail
                quality_stocks.append(r)
                leader_stability_scores.append(leader_stability)
        
        # V8: 主题龙头稳定性分 (取最高的稳定龙头分)
        leader_stability_score = max(leader_stability_scores) if leader_stability_scores else 0
        
        # V8: 计算最终分 (使用新公式)
        final_score = calc_final_score_v8(
            macro_score, t_score, s_score, leader_stability_score, cycle_score, c_score
        )
        
        # V8: 判断交易模式
        short_score = calc_short_score({
            "emotion_score": s_score,
            "leader_stability_score": leader_stability_score
        }, rows)
        
        mid_score = calc_mid_score({
            "macro_score": macro_score,
            "theme_score": t_score,
            "leader_stability_score": leader_stability_score
        }, rows)
        
        trade_mode = "SHORT" if short_score > mid_score else "MID"
        
        # 存储详细结果
        result = {
            "theme": theme_name,
            "category": category_name,
            "n_stocks": all_total,
            "macro_score": round(macro_score, 1),
            "theme_score": round(t_score, 1),
            "emotion_score": round(s_score, 1),
            "leader_stability_score": round(leader_stability_score, 1),
            "cycle_score": round(cycle_score, 1),
            "crowding_score": round(c_score, 1),
            "final_score": round(final_score, 1),
            "theme_state": theme_state,
            "rotation_cycle": rotation_cycle,
            "trade_mode": trade_mode,
            "short_score": round(short_score, 1),
            "mid_score": round(mid_score, 1),
            "trend_detail": t_detail,
            "sentiment_detail": s_detail,
            "crowding_detail": c_detail,
        }
        
        # ========== V9.1: 一级产业传导评分修正 ==========
        # 获取对应的一级产业状态
        parent_industry_status = industry_status_map.get(category_name)
        if not parent_industry_status:
            # 尝试从theme_to_category映射获取
            parent_industry_status = industry_status_map.get(category_name)
        
        # 计算V9.1主题评分（包含Parent_Trend_Boost和Bottom_Activation_Score）
        theme_data_v9 = {
            "trend_score": t_score,
            "sentiment_score": s_score,
        }
        v9_score, v9_diagnostics = calc_v9_theme_score(
            theme_data_v9, quality_stocks, t_detail, s_detail, c_detail, parent_industry_status
        )
        
        # 计算V9.1 Entry_Type（包含"底部启动"类型）
        entry_type = calc_v9_entry_type(theme_data_v9, quality_stocks, parent_industry_status)
        
        # 用V9评分覆盖原评分
        result["v9_final_score"] = round(v9_score, 1)
        result["v9_entry_type"] = entry_type
        result["v9_diagnostics"] = v9_diagnostics
        result["parent_industry_status"] = parent_industry_status
        # 如果V9分数比原分数更合理，使用V9分数
        if v9_score > 0:
            result["final_score"] = round(v9_score, 1)
        
        # V8.1: 按质量分排序，取前10只 (要求quality_score >= 50，放宽标准)
        quality_stocks.sort(key=lambda x: x["quality_score"], reverse=True)
        
        # ========== V9: 应用个股过滤 ==========
        # 对每只股票进行V9过滤，标记Entry_Type
        v9_tradable_stocks = []
        for s in quality_stocks:
            is_tradable, entry_type, filter_reason = calc_v9_stock_filter(s)
            s["is_v9_tradable"] = is_tradable
            s["entry_type"] = entry_type
            s["v9_filter_reason"] = filter_reason
            if is_tradable:
                v9_tradable_stocks.append(s)
        
        # V9: 使用过滤后的可交易股票，取最多3只
        top3_stocks = [s for s in v9_tradable_stocks if s["quality_score"] >= 50][:3]
        
        # 如果V9过滤后不足3只，尝试放宽条件
        if len(top3_stocks) < 3:
            fallback_stocks = [s for s in v9_tradable_stocks if s not in top3_stocks]
            top3_stocks.extend(fallback_stocks[:3 - len(top3_stocks)])
        
        # 生成每只股票的交易信号 (V8增强版)
        stock_signals = []
        for s in top3_stocks:
            signal = generate_trading_signal_v8(s, theme_state, s["quality_score"], trade_mode)
            stock_signals.append({
                "ts_code": s["ts_code"],
                "name": s["name"],
                "quality_score": s["quality_score"],
                "leader_stability": s.get("leader_stability", 0),
                "pct_chg": s.get("pct_chg", 0),
                "close": s.get("close", 0),
                "turnover": s.get("turnover", 0),
                "vol_ratio": s.get("vol_ratio", 1),
                "entry_type": s.get("entry_type", "🔴不可交易"),  # V9
                "v9_filter_reason": s.get("v9_filter_reason", ""),  # V9
                **signal
            })
        
        result["top_stocks"] = stock_signals
        result["has_stable_leader"] = any(s.get("leader_stability", 0) >= 70 for s in quality_stocks)
        
        # V8: 成交额检查 (是否大于5日均值)
        avg_amount_5d = t_detail.get("avg_amount_5d", 0)
        result["amount_ok"] = theme_total_amount > avg_amount_5d * 0.8
        
        results.append(result)
        
        # 保存到DB
        save_to_sqlite_v7(results)
    
    # 9. V8.1 主题筛选规则 - 动态阈值 + 双池输出 + Fallback机制
    
    # 9.1 计算市场热度
    def calc_market_hot(results):
        """计算市场整体热度"""
        if not results:
            return 50
        emotion_scores = [r["emotion_score"] for r in results]
        avg_emotion = sum(emotion_scores) / len(emotion_scores)
        return min(100, max(0, avg_emotion))
    
    market_hot = calc_market_hot(results)
    print(f"[V8.1] 市场热度: {market_hot:.1f}")
    
    # 9.2 动态调整阈值
    if market_hot > 70:
        final_threshold = 55
    elif market_hot > 50:
        final_threshold = 50
    else:
        final_threshold = 45
    
    print(f"[V8.1] 动态阈值: {final_threshold}")
    
    # 9.3 初始筛选（宽松版）
    filtered_results = []
    for r in results:
        # 退潮周期直接排除（除非有强资金）
        if r["theme_state"] == "退潮":
            # 退潮但有资金流入的可以保留作为反弹机会
            if r.get("amount_ok", False) and r["emotion_score"] > 50:
                r["theme_state"] = "退潮反弹"
            else:
                continue
        
        # 无股票的排除
        if not r.get("top_stocks"):
            continue
        
        filtered_results.append(r)
    
    # 按 final_score 排序
    filtered_results.sort(key=lambda x: x["final_score"], reverse=True)
    
    # 9.4 双池分离
    short_pool = [r for r in filtered_results if r["trade_mode"] == "SHORT"]
    mid_pool = [r for r in filtered_results if r["trade_mode"] == "MID"]
    
    print(f"[V8.1] SHORT池: {len(short_pool)} 个, MID池: {len(mid_pool)} 个")
    
    # 9.5 强制保证双池都有输出
    # 如果SHORT池为空，从MID池中挑选情绪高的补充
    if not short_pool and filtered_results:
        candidates = sorted(filtered_results, key=lambda x: x["emotion_score"], reverse=True)
        short_pool = [candidates[0]] if candidates else []
        print(f"[V8.1] SHORT池为空，补充1个情绪最高主题")
    
    # 如果MID池为空，从SHORT池中挑选趋势好的补充
    if not mid_pool and filtered_results:
        candidates = sorted(filtered_results, key=lambda x: x["theme_score"], reverse=True)
        mid_pool = [candidates[0]] if candidates else []
        print(f"[V8.1] MID池为空，补充1个趋势最强主题")
    
    # 9.6 合并双池，保证至少3个主题
    final_results = short_pool + mid_pool
    
    # 如果总数不足3个，触发Fallback机制
    if len(final_results) < 3:
        print(f"[V8.1] 当前主题数 {len(final_results)} < 3，触发Fallback机制")
        
        # Fallback候选：emotion高或成交额突增的主题
        fallback_candidates = []
        for r in filtered_results:
            if r not in final_results:
                # 情绪高或5日动量强
                if r["emotion_score"] >= 60 or r["theme_score"] >= 55:
                    fallback_candidates.append(r)
        
        # 按综合分排序补充
        fallback_candidates.sort(key=lambda x: x["final_score"], reverse=True)
        needed = 3 - len(final_results)
        final_results.extend(fallback_candidates[:needed])
    
    # 9.7 最终筛选：确保总数在3-8个之间
    final_results = final_results[:8]  # 最多8个
    if len(final_results) < 3:
        # 最后兜底：直接取评分最高的主题
        all_sorted = sorted(filtered_results, key=lambda x: x["final_score"], reverse=True)
        final_results = all_sorted[:max(3, len(all_sorted))]
    
    # 9.8 去重：确保没有重复的主题
    seen_themes = set()
    unique_results = []
    for r in final_results:
        theme_name = r.get("theme", "")
        if theme_name not in seen_themes:
            seen_themes.add(theme_name)
            unique_results.append(r)
    final_results = unique_results
    
    print(f"[V8.1] 最终可交易主题: {len(final_results)} 个")
    
    # ========== V9.1: 一级产业传导评分修正 ==========
    # 先计算industry_analysis（用于获取一级产业状态）
    industry_analysis = get_industry_trend_analysis(filtered_results)
    industry_status_map = {ind["industry"]: ind for ind in industry_analysis}
    
    # 对final_results应用V9.1 Parent_Trend_Boost修正
    for r in final_results:
        category_name = r.get("category", "")
        parent_industry_status = industry_status_map.get(category_name)
        
        if parent_industry_status:
            # 计算V9.1主题评分
            stock_feats = r.get("top_stocks", [])
            t_detail = r.get("trend_detail", {})
            s_detail = r.get("sentiment_detail", {})
            c_detail = r.get("crowding_detail", {})
            
            theme_data_v9 = {
                "trend_score": r.get("theme_score", 50),
                "sentiment_score": r.get("emotion_score", 50),
            }
            
            v9_score, v9_diagnostics = calc_v9_theme_score(
                theme_data_v9, stock_feats, t_detail, s_detail, c_detail, parent_industry_status
            )
            
            # 计算V9.1 Entry_Type
            entry_type = calc_v9_entry_type(theme_data_v9, stock_feats, parent_industry_status)
            
            # 更新result
            r["v9_final_score"] = round(v9_score, 1)
            r["v9_entry_type"] = entry_type
            r["v9_diagnostics"] = v9_diagnostics
            r["parent_industry_status"] = parent_industry_status
            
            # 如果V9分数更合理，使用V9分数
            if v9_score > 0:
                r["final_score"] = round(v9_score, 1)
    
    # 9.8 优化每主题的股票选择（V8.1规则）
    for r in final_results:
        trade_mode = r["trade_mode"]
        
        # SHORT模式：stable >= 45 即可
        # MID模式：stable >= 65
        min_stable = 45 if trade_mode == "SHORT" else 65
        
        # 筛选股票：质量分要求降低，允许更多机会
        quality_stocks = r.get("top_stocks", [])
        
        # 按稳定性和质量分综合排序
        quality_stocks.sort(key=lambda x: (
            x.get("leader_stability", 0) >= min_stable,
            x["quality_score"],
            x.get("leader_stability", 0)
        ), reverse=True)
        
        # 取前3只，质量分放宽到 >= 50
        final_stocks = [s for s in quality_stocks if s["quality_score"] >= 50][:3]
        
        # 如果还是不够，继续放宽
        if len(final_stocks) < 3:
            final_stocks = quality_stocks[:3]
        
        # 标记龙头
        for j, s in enumerate(final_stocks):
            stability = s.get("leader_stability", 0)
            if j == 0:
                if stability >= min_stable:
                    s["role"] = "龙头"
                else:
                    s["role"] = "准龙头"
            elif j == 1:
                s["role"] = "中军"
            else:
                s["role"] = "弹性"
        
        r["top_stocks"] = final_stocks
    
    # 10. 输出结果
    print("\n" + "=" * 80)
    print(f"【明日可交易主题决策引擎 V9.1】")
    print(f"共 {len(final_results)} 个可交易主题")
    print("=" * 80)
    
    for i, r in enumerate(final_results, 1):
        theme_state = r["theme_state"]
        crowding = r["crowding_score"]
        trade_mode = r.get("trade_mode", "MID")
        
        # V9.1: 获取一级产业状态
        parent_status = r.get("parent_industry_status", {})
        parent_status_text = parent_status.get("status", "") if parent_status else ""
        parent_industry = r.get("category", "")
        
        # 状态标签 (避免emoji)
        state_label = {
            "启动": "[START]", "主升": "[RISE]", "高潮": "[PEAK]",
            "分歧": "[SPLIT]", "分歧转一致": "[SPLIT->一致]", "退潮": "[FADE]",
            "退潮反弹": "[REBOUND]", "强势": "[STRONG]", "震荡": "[SWING]", "弱势": "[WEAK]"
        }.get(theme_state, "")
        
        # 拥挤度警告
        crowding_warn = "[WARN:拥挤]" if crowding > 70 else ""
        
        mode_icon = "🚀" if trade_mode == "SHORT" else "📈"
        
        # V9.1: 显示Entry_Type
        v9_entry_type = r.get("v9_entry_type", "🔴不可交易")
        v9_diag = r.get("v9_diagnostics", {})
        parent_boost = v9_diag.get("parent_boost", 0)
        is_bottom = v9_diag.get("is_bottom_activation", False)
        
        print(f"\n{'─' * 80}")
        print(f"{i}. {r['theme']}  {mode_icon} [{trade_mode}] {state_label} {crowding_warn}")
        print(f"   [V9.1] 一级产业: {parent_industry} | 状态: {parent_status_text} | Entry_Type: {v9_entry_type}")
        print(f"   final_score = {r['final_score']} (V9评分={r.get('v9_final_score', r['final_score'])})")
        if parent_boost != 0:
            print(f"   Parent_Trend_Boost: {parent_boost:+.1f} | 底部激活: {'是' if is_bottom else '否'}")
        print(f"   macro={r['macro_score']} theme={r['theme_score']} emotion={r['emotion_score']} leader={r.get('leader_stability_score', 0)} cycle={r['cycle_score']} crowding={r['crowding_score']}")
        print(f"   阶段={r['theme_state']}  模式={trade_mode}")
        
        print(f"\n   ⭐ 最优3股:")
        for j, s in enumerate(r.get("top_stocks", []), 1):
            role = s.get("role", "弹性")
            stability = s.get("leader_stability", 0)
            entry_type = s.get("entry_type", "🔴不可交易")  # V9 Entry_Type
            min_stable = 45 if trade_mode == "SHORT" else 65
            stability_tag = "✅" if stability >= min_stable else ("⚠️" if stability >= 45 else "❌")
            print(f"   {j}. [{role}] {s['name']} ({s['ts_code']}) {entry_type}")
            print(f"      现价:{s['close']:.2f} 涨幅:{s['pct_chg']:+.2f}% 换手:{s['turnover']:.2f}% 量比:{s['vol_ratio']:.2f}")
            print(f"      质量分={s['quality_score']} 稳定性={stability_tag}{stability} 结构={s['pattern']}")
            # V9: 显示过滤原因
            if s.get("v9_filter_reason"):
                print(f"      V9过滤: {s['v9_filter_reason']}")
        
        print(f"\n   🎯 交易计划:")
        if r.get("top_stocks"):
            lead = r["top_stocks"][0]
            print(f"   Entry: {lead['entry']:.2f} (现价{lead['close']:.2f})")
            print(f"   Trigger: {lead['trigger']}")
            print(f"   StopLoss: {lead['stop_loss']:.2f}")
            print(f"   Hold: {lead.get('hold_days', '5~20天')}")
            print(f"\n   📊 概率:")
            print(f"   1日胜率: {lead['next_day_positive_prob']*100:.0f}%")
            print(f"   3日胜率: {lead.get('3_day_win_prob', lead['next_day_positive_prob'])*100:.0f}%")
            print(f"   5日胜率: {lead.get('5_day_win_prob', lead['next_day_positive_prob'])*100:.0f}%")
    
    print(f"\n{'=' * 80}")
    print(f"[完成] {TRADE_DATE} 可交易主题分析完成")
    
    # 保存分析报告 (V8.1版本)
    raw_report = save_report_v8(final_results)
    
    # 调用Deepseek进行AI分析，生成阅读性更强的报告
    industry_analysis = get_industry_trend_analysis(final_results)
    global SKIP_AI_ANALYSIS
    if not SKIP_AI_ANALYSIS:
        generate_ai_report(final_results, raw_report, industry_analysis)
    else:
        print("[Info] 已跳过AI分析")
    
    # 无论是否AI分析，都生成包含产业分析的HTML报告
    html_report = generate_html_report("", final_results, industry_analysis)
    html_report_path = os.path.join(REPORT_DIR, f"theme_v7_ai_report_{TRADE_DATE}.html")
    with open(html_report_path, "w", encoding="utf-8") as f:
        f.write(html_report)
    print(f"[Save] HTML报告(含产业分析): {html_report_path}")
    
    return final_results


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='明日可交易主题决策引擎 V8',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python theme_trend_sentiment_v7.py              # 分析最近交易日
  python theme_trend_sentiment_v7.py --date 20260601  # 回溯分析指定日期
  python theme_trend_sentiment_v7.py -d 20260601 -n 10  # 回溯并限制TOP10主题
  python theme_trend_sentiment_v7.py --list          # 列出最近可用的交易日
        """
    )
    
    parser.add_argument(
        '-d', '--date',
        type=str,
        default=None,
        help='指定分析日期 (格式: YYYYMMDD，例如: 20260601)'
    )
    
    parser.add_argument(
        '-n', '--num',
        type=int,
        default=None,
        help='限制输出主题数量 (默认: 8)'
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='列出最近可用的交易日'
    )
    
    parser.add_argument(
        '--no-ai',
        action='store_true',
        help='跳过AI分析生成'
    )
    
    parser.add_argument(
        '-m', '--mode',
        type=str,
        choices=['SHORT', 'MID', 'ALL'],
        default='ALL',
        help='交易模式筛选: SHORT(短线), MID(中线), ALL(全部, 默认)'
    )
    
    return parser.parse_args()


def list_available_dates(n=10):
    """列出最近可用的交易日"""
    print("=" * 60)
    print("最近可用交易日 (最近 {} 个):".format(n))
    print("=" * 60)
    
    if pro is None:
        print("[Error] Tushare API 未初始化")
        return
    
    try:
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        
        cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
        cal = cal[cal['is_open'] == 1].sort_values('cal_date', ascending=False)
        
        dates = cal['cal_date'].tolist()[:n]
        
        for i, d in enumerate(dates, 1):
            date_obj = datetime.strptime(d, '%Y%m%d')
            weekday = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][date_obj.weekday()]
            is_today = (d == datetime.now().strftime('%Y%m%d'))
            today_mark = " [今日]" if is_today else ""
            
            print(f"  {i}. {d} {weekday}{today_mark}")
        
        print("=" * 60)
        print(f"提示: 使用 --date YYYYMMDD 进行历史回溯")
        print(f"      例如: python theme_trend_sentiment_v7.py --date {dates[0] if dates else '20260601'}")
        
    except Exception as e:
        print(f"[Error] 获取交易日历失败: {e}")


def validate_date(date_str):
    """验证日期格式是否正确"""
    if date_str is None:
        return None
    
    try:
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        
        # 检查是否是未来日期
        if date_obj > datetime.now():
            print(f"[Warning] 日期 {date_str} 是未来日期，将使用最近交易日")
            return None
        
        # 验证是否是交易日
        if pro is not None:
            cal = pro.trade_cal(exchange='', start_date=date_str, end_date=date_str)
            if cal.empty or cal[cal['is_open'] == 1].empty:
                print(f"[Warning] 日期 {date_str} 不是交易日，将使用最近的前一个交易日")
                # 找到最近的前一个交易日
                end_date = (date_obj - timedelta(days=1)).strftime('%Y%m%d')
                start_date = (date_obj - timedelta(days=30)).strftime('%Y%m%d')
                cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
                cal = cal[cal['is_open'] == 1].sort_values('cal_date', ascending=False)
                if not cal.empty:
                    return cal.iloc[0]['cal_date']
                return None
        
        return date_str
        
    except ValueError:
        print(f"[Error] 日期格式错误: {date_str}，正确格式: YYYYMMDD (例如: 20260601)")
        return None


def get_trade_date_for_analysis(target_date=None):
    """获取用于分析的交易日"""
    if target_date:
        validated = validate_date(target_date)
        if validated:
            return validated
        print("[Info] 将使用最近交易日进行分析")
    
    return get_last_trade_date()


if __name__ == "__main__":
    args = parse_args()
    
    # 列出可用日期
    if args.list:
        list_available_dates()
        sys.exit(0)
    
    # 获取分析日期
    trade_date = get_trade_date_for_analysis(args.date)
    
    # 设置全局变量
    if args.num:
        TOP_N_RESULTS = args.num
    SKIP_AI_ANALYSIS = args.no_ai
    TRADE_MODE_FILTER = args.mode
    
    # 运行分析
    print(f"[Info] 分析日期: {trade_date}")
    if args.date and args.date != trade_date:
        print(f"[Info] 历史回溯模式")
    
    main(trade_date=trade_date)