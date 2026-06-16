#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题股池生成与演化引擎 V3 (Theme Pool Engine V3)

核心功能：
1. 动态构建主题股池（核心/扩展/潜伏三层）
2. 计算主题归属评分（ThemeScore）
3. 角色分类（龙头/中军/补涨/预期）
4. 主题生命周期判断
5. 每日更新与演化

数据来源：
- 东方财富板块成分股（复用solo目录缓存）
- 股票行情数据
"""
import sys
import os
import json
import time
import sqlite3
import warnings
from datetime import datetime, timedelta
from collections import defaultdict
from io import StringIO

import numpy as np
import pandas as pd
import tushare as ts
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

# 路径配置
THEME_DIR = os.path.dirname(os.path.abspath(__file__))
SOLO_DIR = os.path.join(os.path.dirname(THEME_DIR), "solo")
CACHE_DIR = os.path.join(SOLO_DIR, "cache_backbone_tushare")
POOL_DIR = os.path.join(THEME_DIR, "pools")

os.makedirs(POOL_DIR, exist_ok=True)

# 导入solo目录下的缓存函数
sys.path.insert(0, SOLO_DIR)

# SQLite缓存路径
DB_PATH = os.path.join(CACHE_DIR, 'cache.db')

# 加载环境变量
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None


# ==================== 缓存函数（复用solo目录） ====================

def get_longterm_cache(key):
    """读取长效缓存"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT data, expire_time FROM cache_data WHERE key = ?', (key,))
        row = cursor.fetchone()
        if row:
            data_str, expire_time = row[0], row[1]
            if expire_time and expire_time > 0 and int(time.time()) > expire_time:
                cursor.execute('DELETE FROM cache_data WHERE key = ?', (key,))
                conn.commit()
                return None
            return pd.read_csv(StringIO(data_str))
    except Exception:
        pass
    finally:
        conn.close()
    return None


def save_longterm_cache(key, data, expire_days=7):
    """保存长效缓存"""
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
    except Exception:
        pass
    finally:
        conn.close()


def get_dc_members():
    """
    获取东方财富板块数据（成份股）
    复用solo目录的长效缓存
    """
    dc_cache_key = "dc_all_members_longterm"
    
    # 尝试读取缓存
    df = get_longterm_cache(dc_cache_key)
    if df is not None and not df.empty and "is_industry" in df.columns:
        print(f"[DC] 使用长效缓存: {len(df)} 条记录")
        return df
    
    if pro is None:
        return pd.DataFrame()
    
    print("[DC] 调用 Tushare dc_index / dc_member...")
    
    # 获取当前交易日
    trade_date = get_last_trade_date()
    
    concept_df = pro.dc_index(trade_date=trade_date, idx_type="概念板块")
    time.sleep(0.15)
    industry_df = pro.dc_index(trade_date=trade_date, idx_type="行业板块")
    time.sleep(0.15)
    
    industry_board_codes = set(industry_df["ts_code"].tolist())
    
    boards = pd.concat([concept_df[["ts_code", "name"]], industry_df[["ts_code", "name"]]], ignore_index=True)
    name_map = dict(zip(boards["ts_code"], boards["name"]))
    codes = boards["ts_code"].tolist()
    
    all_members = []
    total = len(codes)
    
    for i, code in enumerate(codes):
        try:
            m = pro.dc_member(trade_date=trade_date, ts_code=code)
            if m is not None and not m.empty:
                m["concept_name"] = m["ts_code"].map(name_map)
                m["is_industry"] = code in industry_board_codes
                m = m.dropna(subset=["concept_name"])
                all_members.append(m)
            if (i + 1) % 100 == 0:
                print(f"[DC] 进度: {i+1}/{total}")
            time.sleep(0.15)
        except Exception:
            pass
    
    if not all_members:
        return pd.DataFrame()
    
    df = pd.concat(all_members, ignore_index=True).drop_duplicates(subset=["con_code", "concept_name"])
    
    # 保存为长效缓存
    save_longterm_cache(dc_cache_key, df, expire_days=7)
    print(f"[DC] 拉取完成: {len(df)} 条 (已保存7天长效缓存)")
    return df


def get_stock_basic():
    """获取股票基本信息（复用solo目录缓存）"""
    df = get_longterm_cache("stock_basic_longterm")
    if df is not None and not df.empty:
        print(f"[StockBasic] 使用长效缓存: {len(df)} 只股票")
        return df
    
    if pro is None:
        return pd.DataFrame()
    
    df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    time.sleep(0.15)
    
    save_longterm_cache("stock_basic_longterm", df, expire_days=7)
    print(f"[StockBasic] 拉取完成: {len(df)} 只 (已保存7天长效缓存)")
    return df


def get_daily_basic(trade_date):
    """获取每日基本面数据"""
    cache_key = f"daily_basic_{trade_date}"
    df = get_longterm_cache(cache_key)
    if df is not None and not df.empty:
        return df
    
    if pro is None:
        return pd.DataFrame()
    
    df = pro.daily_basic(trade_date=trade_date, fields="ts_code,total_mv,circ_mv,turnover_rate,pe,pb")
    time.sleep(0.15)
    
    save_longterm_cache(cache_key, df, expire_days=1)
    return df


def get_daily_quotes(trade_date):
    """获取每日行情数据"""
    cache_key = f"daily_quotes_{trade_date}"
    df = get_longterm_cache(cache_key)
    if df is not None and not df.empty:
        return df
    
    if pro is None:
        return pd.DataFrame()
    
    df = pro.daily(trade_date=trade_date)
    time.sleep(0.15)
    
    save_longterm_cache(cache_key, df, expire_days=1)
    return df


def get_last_trade_date():
    """获取最近交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    if pro is None:
        d = datetime.now().date()
        if d.weekday() == 5:
            d = d - timedelta(days=1)
        elif d.weekday() == 6:
            d = d - timedelta(days=2)
        return d.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)


# ==================== 主题评分模型 ====================

def calc_theme_score(stock_info, theme_config, dc_df):
    """
    计算主题归属评分 ThemeScore
    
    ThemeScore = 
      0.40 * 业务/概念匹配度
    + 0.25 * 资金动量
    + 0.20 * 产业链角色匹配度
    + 0.10 * 市场认知度
    + 0.05 * 行业一致性
    """
    ts_code = stock_info.get("ts_code", "")
    stock_name = stock_info.get("name", "")
    stock_industry = stock_info.get("industry", "")
    
    # 1. 业务/概念匹配度 (0-100)
    concept_match = calc_concept_match(ts_code, theme_config, dc_df)
    
    # 2. 资金动量 (0-100)
    momentum = calc_momentum_score(stock_info)
    
    # 3. 产业链角色匹配度 (0-100)
    chain_role = calc_chain_role_score(stock_info, theme_config)
    
    # 4. 市场认知度 (0-100)
    market_recognition = calc_market_recognition(stock_info, theme_config)
    
    # 5. 行业一致性 (0-100)
    industry_consistency = calc_industry_consistency(stock_industry, theme_config)
    
    # 加权计算
    theme_score = (
        0.40 * concept_match +
        0.25 * momentum +
        0.20 * chain_role +
        0.10 * market_recognition +
        0.05 * industry_consistency
    )
    
    return round(theme_score, 2), {
        "concept_match": concept_match,
        "momentum": momentum,
        "chain_role": chain_role,
        "market_recognition": market_recognition,
        "industry_consistency": industry_consistency
    }


def calc_concept_match(ts_code, theme_config, dc_df):
    """计算概念匹配度"""
    if dc_df is None or dc_df.empty:
        return 50
    
    # 获取股票所属的所有概念
    stock_concepts = dc_df[dc_df["con_code"] == ts_code]["concept_name"].tolist()
    
    if not stock_concepts:
        return 30  # 无概念数据
    
    # 主题配置的概念板块
    theme_concepts = theme_config.get("concept_boards", [])
    
    # 计算匹配度
    matched = sum(1 for c in stock_concepts if any(tc in c or c in tc for tc in theme_concepts))
    
    if matched == 0:
        return 40
    elif matched == 1:
        return 60
    elif matched == 2:
        return 75
    else:
        return 90


def calc_momentum_score(stock_info):
    """计算资金动量评分"""
    pct_chg = stock_info.get("pct_chg", 0)
    turnover = stock_info.get("turnover_rate", 0)
    amount = stock_info.get("amount", 0)
    
    score = 50
    
    # 涨幅贡献
    if pct_chg > 9.9:
        score += 25  # 涨停
    elif pct_chg > 5:
        score += 15
    elif pct_chg > 2:
        score += 5
    elif pct_chg < -5:
        score -= 10
    
    # 换手率贡献
    if turnover > 10:
        score += 15
    elif turnover > 5:
        score += 8
    elif turnover > 2:
        score += 3
    
    # 成交额贡献
    if amount > 100000:  # 10亿
        score += 10
    elif amount > 50000:  # 5亿
        score += 5
    
    return max(0, min(100, score))


def calc_chain_role_score(stock_info, theme_config):
    """计算产业链角色匹配度"""
    # 简化版：根据市值和行业判断
    total_mv = stock_info.get("total_mv", 0)
    
    if total_mv > 1000:  # 大盘股
        return 70
    elif total_mv > 300:  # 中盘股
        return 60
    else:
        return 50


def calc_market_recognition(stock_info, theme_config):
    """计算市场认知度"""
    stock_name = stock_info.get("name", "")
    core_companies = theme_config.get("core_companies", [])
    
    # 检查是否为核心公司
    for core in core_companies:
        if core in stock_name:
            return 90
    
    return 50


def calc_industry_consistency(stock_industry, theme_config):
    """计算行业一致性"""
    industry_filter = theme_config.get("industry_filter", [])
    
    if not industry_filter:
        return 50
    
    stock_industry = str(stock_industry) if pd.notna(stock_industry) else ""
    
    for ind in industry_filter:
        if ind in stock_industry or stock_industry in ind:
            return 80
    
    return 30


# ==================== 角色分类 ====================

def classify_role(stock_info, theme_score, all_stocks_in_theme):
    """
    分类股票角色：龙头/中军/补涨/预期
    
    龙头判定：
    - 涨幅/连板领先 + 成交额领先 + 资金集中
    
    中军：
    - 市值较大 + 稳定趋势 + 持续换手
    
    补涨：
    - 低位 + 低涨幅 + 同主题关联
    
    预期：
    - ThemeScore低但逻辑存在
    """
    pct_chg = stock_info.get("pct_chg", 0)
    total_mv = stock_info.get("total_mv", 0)
    turnover = stock_info.get("turnover_rate", 0)
    amount = stock_info.get("amount", 0)
    
    # 计算相对排名
    all_amounts = [s.get("amount", 0) for s in all_stocks_in_theme]
    all_pct = [s.get("pct_chg", 0) for s in all_stocks_in_theme]
    
    amount_rank = sum(1 for a in all_amounts if a > amount) + 1
    pct_rank = sum(1 for p in all_pct if p > pct_chg) + 1
    
    total = len(all_stocks_in_theme)
    
    # 龙头判定
    if (pct_chg > 5 and amount_rank <= 3) or (pct_chg > 9.5 and amount_rank <= 5):
        return "龙头", "涨幅领先+成交活跃"
    
    # 中军判定
    if total_mv > 200 and turnover > 2 and theme_score >= 60:
        return "中军", "市值较大+稳定换手"
    
    # 补涨判定
    if pct_chg < 3 and theme_score >= 50:
        return "补涨", "低位+主题关联"
    
    # 预期判定
    if theme_score < 50:
        return "预期", "逻辑存在但未启动"
    
    return "弹性", "主题关联"


# ==================== 股池划分 ====================

def assign_pool(theme_score, role, stock_info):
    """
    分配股池：核心/扩展/潜伏
    
    核心股池：ThemeScore >= 70 或具备龙头特征
    扩展股池：50 <= ThemeScore < 70
    潜伏股池：25 <= ThemeScore < 50
    """
    if theme_score >= 70 or role == "龙头":
        return "core"
    elif theme_score >= 50:
        return "expansion"
    elif theme_score >= 25:
        return "latent"
    else:
        return "exclude"


# ==================== 主题生命周期 ====================

def determine_theme_stage(pool_stats):
    """
    判断主题生命周期
    
    启动期：少数龙头 + 板块扩散初期
    发酵期：扩展股池活跃
    主升期：核心股池强一致上涨
    分歧期：核心股池分化
    退潮期：扩展池弱化，潜伏池占比上升
    """
    core_count = pool_stats.get("core_count", 0)
    expansion_count = pool_stats.get("expansion_count", 0)
    latent_count = pool_stats.get("latent_count", 0)
    
    core_avg_pct = pool_stats.get("core_avg_pct", 0)
    expansion_avg_pct = pool_stats.get("expansion_avg_pct", 0)
    
    total = core_count + expansion_count + latent_count
    if total == 0:
        return "启动期"
    
    core_ratio = core_count / total
    expansion_ratio = expansion_count / total
    latent_ratio = latent_count / total
    
    # 判断阶段
    if core_count <= 2 and expansion_count <= 3:
        return "启动期"
    elif core_avg_pct > 3 and expansion_avg_pct > 2:
        return "主升期"
    elif core_avg_pct > 0 and expansion_avg_pct < 0:
        return "分歧期"
    elif latent_ratio > 0.5:
        return "退潮期"
    elif expansion_ratio > 0.4:
        return "发酵期"
    else:
        return "发酵期"


# ==================== 主题强度评分 ====================

def calc_theme_strength(pool_stats):
    """计算主题强度评分"""
    core_count = pool_stats.get("core_count", 0)
    expansion_count = pool_stats.get("expansion_count", 0)
    core_avg_pct = pool_stats.get("core_avg_pct", 0)
    total_amount = pool_stats.get("total_amount", 0)
    
    score = 50
    
    # 核心股数量贡献
    if core_count >= 3:
        score += 15
    elif core_count >= 2:
        score += 10
    elif core_count >= 1:
        score += 5
    
    # 扩展股数量贡献
    if expansion_count >= 5:
        score += 10
    elif expansion_count >= 3:
        score += 5
    
    # 核心股涨幅贡献
    if core_avg_pct > 5:
        score += 15
    elif core_avg_pct > 2:
        score += 8
    elif core_avg_pct < -2:
        score -= 10
    
    # 成交额贡献
    if total_amount > 500000:  # 50亿
        score += 10
    elif total_amount > 200000:  # 20亿
        score += 5
    
    return max(0, min(100, score))


# ==================== 次日信号 ====================

def generate_next_day_signal(pool_stats, theme_stage):
    """生成次日交易信号"""
    core_avg_pct = pool_stats.get("core_avg_pct", 0)
    expansion_avg_pct = pool_stats.get("expansion_avg_pct", 0)
    core_count = pool_stats.get("core_count", 0)
    
    # 判断方向
    if core_avg_pct > 3 and expansion_avg_pct > 2:
        direction = "strength"
    elif core_avg_pct < -2 or expansion_avg_pct < -2:
        direction = "weakness"
    else:
        direction = "transition"
    
    # 判断关注池
    if theme_stage == "启动期":
        focus_pool = "core"
    elif theme_stage == "发酵期":
        focus_pool = "expansion"
    elif theme_stage == "主升期":
        focus_pool = "core"
    else:
        focus_pool = "latent"
    
    return {
        "direction": direction,
        "focus_pool": focus_pool
    }


# ==================== 主引擎 ====================

class ThemePoolEngine:
    """主题股池生成引擎"""
    
    def __init__(self, trade_date=None):
        self.trade_date = trade_date or get_last_trade_date()
        self.dc_df = None
        self.stock_basic = None
        self.daily_basic = None
        self.daily_quotes = None
        self.theme_graph = None
        
    def load_data(self):
        """加载所有必要数据"""
        print(f"[Engine] 加载数据，交易日: {self.trade_date}")
        
        # 加载东财板块数据
        self.dc_df = get_dc_members()
        
        # 加载股票基本信息
        self.stock_basic = get_stock_basic()
        
        # 加载每日基本面
        self.daily_basic = get_daily_basic(self.trade_date)
        
        # 加载每日行情
        self.daily_quotes = get_daily_quotes(self.trade_date)
        
        # 加载主题配置
        theme_graph_path = os.path.join(THEME_DIR, "theme_graph_v3.json")
        with open(theme_graph_path, "r", encoding="utf-8") as f:
            self.theme_graph = json.load(f)
        
        print(f"[Engine] 数据加载完成: DC={len(self.dc_df)}, 股票={len(self.stock_basic)}")
        
    def generate_pool_for_theme(self, macro_theme, sub_theme_name, sub_theme_config):
        """为单个二级主题生成股池"""
        print(f"[Pool] 生成股池: {sub_theme_name}")
        
        # 获取概念板块成员
        concept_boards = sub_theme_config.get("concept_boards", [])
        industry_filter = sub_theme_config.get("industry_filter", [])
        exclude_keywords = sub_theme_config.get("exclude_keywords", [])
        core_companies = sub_theme_config.get("core_companies", [])
        
        # 从东财数据获取候选股票
        candidate_codes = set()
        for board in concept_boards:
            if self.dc_df is not None and not self.dc_df.empty:
                board_members = self.dc_df[self.dc_df["concept_name"].str.contains(board, na=False)]["con_code"].tolist()
                candidate_codes.update(board_members)
        
        # 如果没有概念匹配，使用行业过滤
        if not candidate_codes and industry_filter:
            for ind in industry_filter:
                if self.stock_basic is not None and not self.stock_basic.empty:
                    industry_members = self.stock_basic[self.stock_basic["industry"].str.contains(ind, na=False)]["ts_code"].tolist()
                    candidate_codes.update(industry_members)
        
        # 构建股票信息
        stocks_info = []
        for code in candidate_codes:
            # 获取股票基本信息
            basic = self.stock_basic[self.stock_basic["ts_code"] == code]
            if basic.empty:
                continue
            
            stock_name = basic.iloc[0]["name"]
            stock_industry = basic.iloc[0]["industry"]
            
            # 排除关键词过滤
            excluded = False
            for ek in exclude_keywords:
                if ek in str(stock_name) or ek in str(stock_industry):
                    excluded = True
                    break
            
            if excluded:
                continue
            
            # 获取行情数据
            quote = self.daily_quotes[self.daily_quotes["ts_code"] == code] if self.daily_quotes is not None else pd.DataFrame()
            daily = self.daily_basic[self.daily_basic["ts_code"] == code] if self.daily_basic is not None else pd.DataFrame()
            
            stock_info = {
                "ts_code": code,
                "name": stock_name,
                "industry": stock_industry,
                "pct_chg": quote.iloc[0]["pct_chg"] if not quote.empty else 0,
                "close": quote.iloc[0]["close"] if not quote.empty else 0,
                "amount": quote.iloc[0]["amount"] if not quote.empty else 0,
                "turnover_rate": daily.iloc[0]["turnover_rate"] if not daily.empty else 0,
                "total_mv": daily.iloc[0]["total_mv"] if not daily.empty else 0,
            }
            
            stocks_info.append(stock_info)
        
        if not stocks_info:
            return None
        
        # 计算每只股票的主题评分
        for stock in stocks_info:
            score, detail = calc_theme_score(stock, sub_theme_config, self.dc_df)
            stock["theme_score"] = score
            stock["score_detail"] = detail
            
            # 分类角色
            role, reason = classify_role(stock, score, stocks_info)
            stock["role"] = role
            stock["role_reason"] = reason
            
            # 分配股池
            pool = assign_pool(score, role, stock)
            stock["pool"] = pool
        
        # 构建三层股池
        core_pool = [s for s in stocks_info if s["pool"] == "core"]
        expansion_pool = [s for s in stocks_info if s["pool"] == "expansion"]
        latent_pool = [s for s in stocks_info if s["pool"] == "latent"]
        
        # 排序
        core_pool.sort(key=lambda x: x["theme_score"], reverse=True)
        expansion_pool.sort(key=lambda x: x["theme_score"], reverse=True)
        latent_pool.sort(key=lambda x: x["theme_score"], reverse=True)
        
        # 计算池统计
        pool_stats = {
            "core_count": len(core_pool),
            "expansion_count": len(expansion_pool),
            "latent_count": len(latent_pool),
            "core_avg_pct": np.mean([s["pct_chg"] for s in core_pool]) if core_pool else 0,
            "expansion_avg_pct": np.mean([s["pct_chg"] for s in expansion_pool]) if expansion_pool else 0,
            "total_amount": sum(s["amount"] for s in stocks_info)
        }
        
        # 判断主题阶段
        theme_stage = determine_theme_stage(pool_stats)
        
        # 计算主题强度
        theme_strength = calc_theme_strength(pool_stats)
        
        # 生成次日信号
        next_day_signal = generate_next_day_signal(pool_stats, theme_stage)
        
        # 确定龙头
        leader_stock = core_pool[0]["name"] if core_pool else ""
        secondary_leaders = [s["name"] for s in core_pool[1:3]] if len(core_pool) > 1 else []
        
        # 构建结果
        result = {
            "theme_name": sub_theme_name,
            "macro_theme": macro_theme,
            "trade_date": self.trade_date,
            "theme_stage": theme_stage,
            "theme_strength_score": theme_strength,
            "core_pool": [{
                "stock_code": s["ts_code"],
                "stock_name": s["name"],
                "theme_score": s["theme_score"],
                "role": s["role"],
                "reason": s["role_reason"],
                "pct_chg": s["pct_chg"],
                "amount": s["amount"],
                "total_mv": s["total_mv"]
            } for s in core_pool[:10]],
            "expansion_pool": [{
                "stock_code": s["ts_code"],
                "stock_name": s["name"],
                "theme_score": s["theme_score"],
                "role": s["role"],
                "reason": s["role_reason"],
                "pct_chg": s["pct_chg"]
            } for s in expansion_pool[:15]],
            "latent_pool": [{
                "stock_code": s["ts_code"],
                "stock_name": s["name"],
                "theme_score": s["theme_score"],
                "role": s["role"]
            } for s in latent_pool[:10]],
            "leader_stock": leader_stock,
            "secondary_leaders": secondary_leaders,
            "next_day_signal": next_day_signal,
            "pool_stats": pool_stats
        }
        
        return result
    
    def generate_all_pools(self):
        """生成所有主题的股池"""
        self.load_data()
        
        macro_themes = self.theme_graph.get("macro_themes", {})
        results = {}
        
        for macro_name, macro_config in macro_themes.items():
            sub_themes = macro_config.get("sub_themes", {})
            
            for sub_name, sub_config in sub_themes.items():
                pool = self.generate_pool_for_theme(macro_name, sub_name, sub_config)
                if pool:
                    results[sub_name] = pool
                    
                    # 保存到单独文件（替换文件名中的非法字符）
                    safe_sub_name = sub_name.replace("/", "或").replace("\\", "或").replace(":", "：").replace("*", "×").replace("?", "？").replace("\"", "'").replace("<", "《").replace(">", "》").replace("|", "｜")
                    pool_path = os.path.join(POOL_DIR, f"{safe_sub_name}.json")
                    with open(pool_path, "w", encoding="utf-8") as f:
                        json.dump(pool, f, ensure_ascii=False, indent=2)
                    
                    print(f"[Pool] {sub_name}: 核心={len(pool['core_pool'])} 扩展={len(pool['expansion_pool'])} 潜伏={len(pool['latent_pool'])}")
        
        # 保存汇总文件
        summary_path = os.path.join(THEME_DIR, f"pools_summary_{self.trade_date}.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"[Engine] 完成，共 {len(results)} 个主题股池")
        return results


# ==================== 主函数 ====================

def main():
    """主函数"""
    engine = ThemePoolEngine()
    results = engine.generate_all_pools()
    
    # 打印摘要
    print("\n" + "=" * 60)
    print("主题股池摘要")
    print("=" * 60)
    
    for theme_name, pool in sorted(results.items(), key=lambda x: x[1]["theme_strength_score"], reverse=True)[:10]:
        print(f"\n{theme_name} [{pool['theme_stage']}] 强度={pool['theme_strength_score']}")
        print(f"  龙头: {pool['leader_stock']}")
        print(f"  核心: {len(pool['core_pool'])} 扩展: {len(pool['expansion_pool'])} 潜伏: {len(pool['latent_pool'])}")
        print(f"  次日信号: {pool['next_day_signal']['direction']} 关注{pool['next_day_signal']['focus_pool']}")


if __name__ == "__main__":
    main()
