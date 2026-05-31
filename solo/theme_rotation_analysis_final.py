#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日盘后复盘和热点轮动分析系统 - 最终版
主题排名 + 10日平均分 + 调整后回升概率分析 + SQLite数据库存储 + 大盘情绪分析
"""
import os
import sys
import pickle
import warnings
import time
import json
import glob
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts
import requests

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

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
def get_last_trade_date():

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = pro.trade_cal(
        exchange='',
        start_date='20200101',
        end_date=query_date
    )

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
#TRADE_DATE = "20260527" # for test

print("当前交易日:", TRADE_DATE)

# =========================
# DeepSeek 基本面估值和风险排除
# =========================
def analyze_stock_fundamental(stock_info):
    """
    使用 DeepSeek 分析股票基本面
    :param stock_info: 包含股票信息的字典
    :return: 分析结果字符串
    """
    if not DEEPSEEK_KEY:
        return None
    
    prompt = f"""你是一位专业的A股量化分析师。请分析以下股票，要求基于业绩分析和机构研报进行估值计算。

股票信息：
- 股票名称：{stock_info.get('name', 'N/A')}
- 股票代码：{stock_info.get('ts_code', 'N/A')}
- 综合评分：{stock_info.get('total_score', 0):.1f}
- 5日涨幅：{stock_info.get('change_5', 0):.1f}%
- 20日涨幅：{stock_info.get('change_20', 0):.1f}%
- 5日乖离率：{stock_info.get('ma_data', {}).get('ma5_biased', 0):.1f}%
- 量比：{stock_info.get('ma_data', {}).get('volume_ratio', 0):.2f}
- 风险等级：{stock_info.get('score_details', {}).get('回落风险等级', 'N/A')}
- 二波信号：{stock_info.get('score_details', {}).get('二波信号等级', 'N/A')}
- 所属主题：{', '.join(stock_info.get('themes', []))}

请分析：
1. 估值水平：低/中/高
2. 业绩分析：最近一年业绩增长情况、行业地位
3. 机构研报评级：机构一致预期
4. 估值波动空间：
   - 合理估值区间
   - 相对现价的上涨乐观幅度（%）
5. 风险提示
6. 中长线投资建议（推荐/谨慎/不推荐）
7. 简要理由

格式要求（必须严格按此格式）：
- 估值水平：低/中/高
- 业绩分析：...
- 机构评级：...
- 合理估值区间：XX-XX元
- 上涨乐观幅度：XX%
- 风险提示：...
- 投资建议：推荐/谨慎/不推荐
- 理由：...
"""
    
    try:
        headers = {
            'Authorization': f'Bearer {DEEPSEEK_KEY}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一位专业的A股量化分析师，擅长结合业绩数据和机构研报进行估值分析。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 800
        }
        
        resp = requests.post('https://api.deepseek.com/v1/chat/completions', 
                            headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        return result['choices'][0]['message']['content']
    except Exception as e:
        print(f"DeepSeek分析失败: {e}")
        return None

# =========================
# 简化版大盘情绪分析（不依赖emotion.py）
# =========================
def analyze_market_emotion_simple():
    try:
        query_date = TRADE_DATE

        daily_df = cached_daily_single(trade_date=query_date)
        if daily_df.empty:
            return {}

        total = len(daily_df)
        up_count = (daily_df['pct_chg'] > 0).sum()
        up_ratio = (up_count / total) * 100 if total > 0 else 0
        strong_count = (daily_df['pct_chg'] >= 5).sum()
        strong_ratio = (strong_count / total) * 100 if total > 0 else 0

        zt_count = 0
        dt_count = 0
        broken_rate = 0
        try:
            # 使用 limit_list_ths 接口获取涨停数据
            limit_df = cached_limit_list_ths(trade_date=query_date, limit_type='涨停池')
            if limit_df is not None and not limit_df.empty:
                zt_count = len(limit_df)
                # 使用 open_num 字段计算炸板率（开板次数>0的视为炸板）
                if 'open_num' in limit_df.columns:
                    broken_count = (limit_df['open_num'].fillna(0) > 0).sum()
                    broken_rate = (broken_count / zt_count) * 100 if zt_count > 0 else 0
        except Exception as e:
            print(f"获取涨停数据失败: {e}")

        try:
            # 使用 limit_list_ths 接口获取跌停数据
            limit_df_d = cached_limit_list_ths(trade_date=query_date, limit_type='跌停池')
            if limit_df_d is not None and not limit_df_d.empty:
                dt_count = len(limit_df_d)
        except Exception as e:
            print(f"获取跌停数据失败: {e}")

        max_lb = 0
        try:
            lb_df = cached_limit_step(trade_date=query_date)
            if lb_df is not None and not lb_df.empty and 'nums' in lb_df.columns:
                max_lb = int(lb_df['nums'].fillna(1).astype(int).max())
        except:
            pass

        close = 0
        pct_chg = 0
        bias20 = 0
        ma20_slope = 0
        above_ma5 = False
        ma5_slope = 0
        above_ma20 = False
        above_ma60 = False
        ma60_slope = 0
        try:
            index_df = cached_index_daily(ts_code='000001.SH', start_date='20250101', end_date=query_date)
            if not index_df.empty:
                index_df = index_df.sort_values('trade_date').tail(90)
                close = index_df['close'].iloc[-1]
                pct_chg = index_df['pct_chg'].iloc[-1]
                ma20 = index_df['close'].rolling(20).mean().iloc[-1]
                ma60 = index_df['close'].rolling(60).mean().iloc[-1]
                bias20 = ((close / ma20) - 1) * 100 if ma20 > 0 else 0

                if len(index_df) >= 25:
                    ma20_prev = index_df['close'].iloc[-25]
                    ma20_slope = 1 if ma20 > ma20_prev else -1
                above_ma20 = close >= ma20

                if len(index_df) >= 5:
                    ma5 = index_df['close'].rolling(5).mean().iloc[-1]
                    ma5_prev = index_df['close'].rolling(5).mean().iloc[-2]
                    above_ma5 = close >= ma5
                    ma5_slope = 1 if ma5 > ma5_prev else -1

                if len(index_df) >= 65:
                    ma60_prev = index_df['close'].iloc[-65]
                    ma60_slope = 1 if ma60 > ma60_prev else -1
                above_ma60 = close >= ma60
        except:
            pass
        
        # ===== 游资情绪量化分析优化 =====
        
        # 1. 涨跌停比得分（权重最大）
        zt_dt_ratio = (zt_count / (dt_count + 1)) if dt_count > 0 else zt_count
        zt_dt_score = min(35, zt_dt_ratio * 8)  # 涨停越多得分越高
        
        # 2. 炸板率惩罚
        broken_score = max(0, 15 - broken_rate * 0.25)  # 炸板率越高得分越低
        
        # 3. 连板高度得分
        lb_score = 0
        if max_lb >= 8:
            lb_score = 20
        elif max_lb >= 6:
            lb_score = 15
        elif max_lb >= 4:
            lb_score = 10
        elif max_lb >= 2:
            lb_score = 5
        
        # 4. 市场赚钱效应（上涨占比和强势股占比）
        earning_score = up_ratio * 0.4 + strong_ratio * 1.5
        
        # 5. 指数涨跌得分
        index_score = 0
        if pct_chg > 2:
            index_score = 15
        elif pct_chg > 1:
            index_score = 10
        elif pct_chg > 0:
            index_score = 5
        elif pct_chg > -1:
            index_score = -5
        elif pct_chg > -2:
            index_score = -10
        else:
            index_score = -15
        
        # 6. 均线位置得分（不再大幅惩罚）
        ma_score = 0
        if above_ma5 and above_ma20 and above_ma60:
            ma_score = 15
        elif above_ma5 and above_ma20:
            ma_score = 10
        elif above_ma5:
            ma_score = 5
        elif not above_ma5 and not above_ma20:
            ma_score = -5
        else:
            ma_score = 0
        
        # 7. 跌停惩罚
        dt_penalty = 0
        if dt_count >= 50:
            dt_penalty = 25
        elif dt_count >= 30:
            dt_penalty = 15
        elif dt_count >= 15:
            dt_penalty = 8
        
        # 综合情绪分
        emotion_score = 35 + zt_dt_score + broken_score + lb_score + earning_score + index_score + ma_score - dt_penalty
        
        # 限制在0-100之间
        emotion_score = max(0, min(100, emotion_score))
        
        # ===== 趋势分计算（简化，避免过度惩罚）=====
        trend_score = 50
        if pct_chg > 0:
            trend_score += 8
        else:
            trend_score -= 8
        
        if ma20_slope > 0:
            trend_score += 10
        else:
            trend_score -= 10
        
        if above_ma5:
            trend_score += 8
        else:
            trend_score -= 8
        
        if ma5_slope > 0:
            trend_score += 5
        else:
            trend_score -= 5
        
        if above_ma20:
            trend_score += 7
        else:
            trend_score -= 7
        
        trend_score = max(0, min(100, trend_score))
        
        if trend_score >= 70:
            trend_risk = "低风险"
        elif trend_score >= 50:
            trend_risk = "中性"
        elif trend_score >= 35:
            trend_risk = "高风险"
        else:
            trend_risk = "系统风险"
        
        # 最终情绪分 = 情绪分70% + 趋势分30%
        final_emotion = emotion_score * 0.7 + trend_score * 0.3
        final_emotion = max(0, min(100, final_emotion))
        
        # 添加均线状态描述
        ma_status = "多头排列" if (above_ma5 and above_ma20 and above_ma60) else \
                   "空头排列" if (not above_ma5 and not above_ma20 and not above_ma60) else \
                   "短期偏弱" if not above_ma5 else \
                   "中期偏弱" if not above_ma20 else \
                   "长期偏弱"
        
        # 使用游资情绪分来划分市场阶段（更符合实际）
        # 0-20: 冰点
        # 20-40: 退潮
        # 40-60: 震荡
        # 60-80: 主升
        # 80-100: 加速
        if final_emotion >= 80:
            stage = "加速"
            position = "85%"
        elif final_emotion >= 60:
            stage = "主升"
            position = "70%"
        elif final_emotion >= 40:
            stage = "震荡"
            position = "50%"
        elif final_emotion >= 20:
            stage = "退潮"
            position = "30%"
        else:
            stage = "冰点"
            position = "15%"
        
        market_amount = daily_df['amount'].sum() / 100000
        
        return {
            "情绪指数": round(final_emotion, 1),
            "大盘点位": round(close, 2) if close else 0,
            "大盘涨跌幅": round(pct_chg, 2) if pct_chg else 0,
            "全市场成交额（亿元）": round(market_amount, 2),
            "市场阶段": stage,
            "涨停家数": int(zt_count),
            "跌停家数": int(dt_count),
            "连板高度": int(max_lb),
            "炸板率": round(broken_rate, 1),
            "上涨占比": round(up_ratio, 1),
            "强势股占比": round(strong_ratio, 1),
            "指数环境": trend_risk,
            "风险等级": "低风险" if final_emotion >= 70 else "中性" if final_emotion >= 50 else "高风险",
            "20日偏离率": round(bias20, 2),
            "MA20方向": ma20_slope,
            "趋势分": round(trend_score, 1),
            "最终建议仓位": position,
            "均线状态": ma_status,
            "站上MA5": above_ma5,
            "站上MA20": above_ma20,
            "站上MA60": above_ma60,
        }
    except Exception as e:
        print(f"大盘情绪分析失败: {e}")
        import traceback
        traceback.print_exc()
        return {}

# =========================
# 缓存管理器
# =========================
class CacheManager:
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

class DailyCacheManager(CacheManager):
    def __init__(self, cache_dir):
        super().__init__(cache_dir, expire_minutes=999999999)
    
    def get(self, func_name, **kwargs):
        today = datetime.now().strftime('%Y%m%d')
        if 'end_date' in kwargs and kwargs['end_date'] == today:
            return None
        
        return super().get(func_name, **kwargs)
    
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
        cache_data = {'timestamp': time.time(), 'data': data}
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception as e:
            print(f"   缓存保存失败: {e}")

cache_manager = CacheManager(CACHE_DIR, expire_minutes=240)

# =========================
# 缓存的API调用函数
# =========================
def cached_trade_cal(start_date, end_date):
    func_name = "trade_cal"
    cached_data = cache_manager.get(func_name, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        return cached_data
    df = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    time.sleep(0.1)
    cache_manager.set(func_name, df, start_date=start_date, end_date=end_date)
    return df

def cached_daily_single(trade_date):
    func_name = "daily_single"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        return cached_data
    df = pro.daily(trade_date=trade_date)
    if df is not None and not df.empty:
        cache_manager.set(func_name, df, trade_date=trade_date)
    return df

def cached_limit_list_ths(trade_date, limit_type):
    func_name = "limit_list_ths"
    cached_data = cache_manager.get(func_name, trade_date=trade_date, limit_type=limit_type)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.limit_list_ths(trade_date=trade_date, limit_type=limit_type)
        if df is not None and not df.empty:
            cache_manager.set(func_name, df, trade_date=trade_date, limit_type=limit_type)
        return df
    except Exception:
        return pd.DataFrame()

def cached_limit_step(trade_date):
    func_name = "limit_step"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.limit_step(trade_date=trade_date)
        if df is not None and not df.empty:
            cache_manager.set(func_name, df, trade_date=trade_date)
        return df
    except Exception:
        return pd.DataFrame()

def cached_index_daily(ts_code, start_date, end_date):
    func_name = "index_daily"
    cached_data = cache_manager.get(func_name, ts_code=ts_code, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        return cached_data
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            cache_manager.set(func_name, df, ts_code=ts_code, start_date=start_date, end_date=end_date)
        return df
    except Exception:
        return pd.DataFrame()

# =========================
# SQLite数据库管理器
# =========================
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """初始化数据库，创建表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 主题打分表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS theme_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                theme_name TEXT NOT NULL,
                today_score REAL,
                avg_score_10d REAL,
                avg_rank_10d REAL,
                score_trend TEXT,
                rank_change INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, theme_name)
            )
        ''')
        
        # 龙头股打分表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leader_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT,
                theme_name TEXT,
                total_score REAL,
                change_5 REAL,
                change_20 REAL,
                ma5_biased REAL,
                ma20_biased REAL,
                volume_ratio REAL,
                pullback_prob INTEGER,
                second_wave_prob INTEGER,
                limit_up_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, ts_code, theme_name)
            )
        ''')
        
        # 策略推荐表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                name TEXT,
                total_score REAL,
                probability INTEGER,
                ma5_biased REAL,
                volume_ratio REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, strategy_type, ts_code)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_theme_scores(self, trade_date, ranked_themes, theme_summary):
        """保存主题打分数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for theme, today_score in ranked_themes:
            summary = theme_summary.get(theme, {})
            cursor.execute('''
                INSERT OR REPLACE INTO theme_scores 
                (trade_date, theme_name, today_score, avg_score_10d, avg_rank_10d, score_trend, rank_change)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                theme,
                today_score,
                summary.get('avg_score_10d', 0),
                summary.get('avg_rank_10d', 0),
                summary.get('score_trend', '未知'),
                summary.get('rank_change', 0)
            ))
        
        conn.commit()
        conn.close()
        print(f"✓ 主题打分已保存至数据库: {len(ranked_themes)} 条记录")
    
    def save_leader_scores(self, trade_date, theme_leaders, theme_summary):
        """保存龙头股打分数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        count = 0
        for theme, leaders in theme_leaders.items():
            for leader in leaders:
                details = leader['score_details']
                ma_data = leader['ma_data']
                
                cursor.execute('''
                    INSERT OR REPLACE INTO leader_scores 
                    (trade_date, ts_code, name, theme_name, total_score, change_5, change_20,
                     ma5_biased, ma20_biased, volume_ratio, pullback_prob, second_wave_prob, limit_up_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade_date,
                    leader['ts_code'],
                    leader['name'],
                    theme,
                    leader['total_score'],
                    leader['change_5'],
                    leader['change_20'],
                    ma_data['ma5_biased'] if ma_data else 0,
                    ma_data['ma20_biased'] if ma_data else 0,
                    leader['volume_ratio'],
                    details.get('冲高回落概率', 0),
                    details.get('二波启动概率', 0),
                    leader['limit_up_count']
                ))
                count += 1
        
        conn.commit()
        conn.close()
        print(f"✓ 龙头股打分已保存至数据库: {count} 条记录")
    
    def save_strategy_recommendations(self, trade_date, strategies):
        """保存策略推荐数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        strategy_names = {
            'strategy1': '强者恒强',
            'strategy2': '低吸潜伏',
            'strategy3': '轮动切换'
        }
        
        count = 0
        for strategy_key, strategy_type in strategy_names.items():
            leaders = strategies.get(strategy_key, [])
            for leader in leaders:
                ma_data = leader['ma_data']
                details = leader['score_details']
                
                cursor.execute('''
                    INSERT OR REPLACE INTO strategy_recommendations 
                    (trade_date, strategy_type, ts_code, name, total_score, probability, ma5_biased, volume_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade_date,
                    strategy_type,
                    leader['ts_code'],
                    leader['name'],
                    leader['total_score'],
                    details.get('二波启动概率', 0),
                    ma_data['ma5_biased'] if ma_data else 0,
                    leader['volume_ratio']
                ))
                count += 1
        
        conn.commit()
        conn.close()
        print(f"✓ 策略推荐已保存至数据库: {count} 条记录")
    
    def get_theme_history(self, theme_name, days=10):
        """查询主题历史打分"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT trade_date, today_score, avg_score_10d, score_trend
            FROM theme_scores
            WHERE theme_name = ?
            ORDER BY trade_date DESC
            LIMIT ?
        ''', (theme_name, days))
        
        results = cursor.fetchall()
        conn.close()
        return results
    
    def get_leader_history(self, ts_code, days=10):
        """查询龙头股历史打分"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT trade_date, name, theme_name, total_score, change_5, change_20, second_wave_prob
            FROM leader_scores
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
        ''', (ts_code, days))
        
        results = cursor.fetchall()
        conn.close()
        return results

# 初始化数据库
DB_PATH = os.path.join(CACHE_DIR, "theme_analysis.db")
db_manager = DatabaseManager(DB_PATH)

# =========================
# 缓存的API调用函数
# =========================
def cached_trade_cal(start_date, end_date):
    func_name = "trade_cal"
    cached_data = cache_manager.get(func_name, start_date=start_date, end_date=end_date)
    if cached_data is not None:
        return cached_data
    df = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    time.sleep(0.1)
    cache_manager.set(func_name, df, start_date=start_date, end_date=end_date)
    return df

def get_limit_list_d(trade_date):
    try:
        df = pro.limit_list_d(trade_date=trade_date, limit_type='U')
        return df
    except Exception as e:
        print(f"获取涨停数据失败: {e}")
        return pd.DataFrame()

def cached_limit_list_d(trade_date):
    func_name = "limit_list_d"
    cached_data = cache_manager.get(func_name, trade_date=trade_date)
    if cached_data is not None:
        return cached_data
    df = get_limit_list_d(trade_date)
    if not df.empty:
        cache_manager.set(func_name, df, trade_date=trade_date)
    return df

_stock_limit_cache = {}

def get_stock_limit_info(ts_code):
    # 先检查内存缓存
    if ts_code in _stock_limit_cache:
        return _stock_limit_cache[ts_code]
    
    # 检查文件缓存
    func_name = "stock_limit_info"
    cached_data = cache_manager.get(func_name, ts_code=ts_code)
    if cached_data is not None:
        _stock_limit_cache[ts_code] = cached_data
        return cached_data
    
    try:
        time.sleep(0.2)
        
        df = pro.stock_basic(ts_code=ts_code)
        if not df.empty:
            list_date = df.iloc[0].get('list_date', '')
            name = df.iloc[0].get('name', '')
            
            if name.startswith('*ST') or name.startswith('ST'):
                result = {'limit_up': 5.0, 'limit_down': -5.0, 'is_st': True}
            elif len(list_date) >= 8 and int(list_date) > 20230101:
                result = {'limit_up': 20.0, 'limit_down': -20.0, 'is_new': True}
            else:
                result = {'limit_up': 10.0, 'limit_down': -10.0, 'is_st': False}
            
            # 保存到缓存
            _stock_limit_cache[ts_code] = result
            cache_manager.set(func_name, result, ts_code=ts_code)
            return result
    except Exception as e:
        print(f"获取股票涨跌停信息失败: {e}")
    
    result = {'limit_up': 10.0, 'limit_down': -10.0, 'is_st': False}
    _stock_limit_cache[ts_code] = result
    return result

def is_limit_up(row, limit_info):
    pct_chg = row.get('pct_chg', 0)
    limit_up = limit_info.get('limit_up', 10.0)
    
    if limit_up == 5.0:
        return pct_chg >= 4.9
    elif limit_up == 20.0:
        return pct_chg >= 19.8
    else:
        return pct_chg >= 9.9

def _need_refresh_today_data(cached_data):
    """检查缓存数据是否需要更新当天数据"""
    if cached_data is None or cached_data.empty:
        return True
    
    now = datetime.now()
    if now.hour < 15:
        return False
    
    if 'trade_date' not in cached_data.columns:
        return True
    
    today_str = now.strftime('%Y%m%d')
    cached_dates = cached_data['trade_date'].astype(str).tolist()
    
    return today_str not in cached_dates

def cached_daily(ts_code, start_date, end_date):
    func_name = "daily"
    cached_data = cache_manager.get(func_name, ts_code=ts_code, start_date=start_date, end_date=end_date)
    
    need_refresh = _need_refresh_today_data(cached_data)
    
    if cached_data is not None and not need_refresh:
        return cached_data
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        time.sleep(0.02)
        
        cache_manager.expire_minutes = 999999999
        cache_manager.set(func_name, df, ts_code=ts_code, start_date=start_date, end_date=end_date)
        cache_manager.expire_minutes = 240
        
        if not df.empty:
            limit_info = get_stock_limit_info(ts_code)
            df['is_limit_up'] = df.apply(lambda row: is_limit_up(row, limit_info), axis=1)
        
        return df
    except Exception as e:
        print(f"获取日线数据失败: {e}")
        return pd.DataFrame()

_trade_dates_cache = None

def get_trade_dates(n_days=25):
    global _trade_dates_cache
    if _trade_dates_cache is not None:
        return _trade_dates_cache
    
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = cached_trade_cal('20250101', query_date)
    cal = cal[cal['is_open'] == 1]
    cal = cal.sort_values('cal_date', ascending=False)
    trade_dates = cal['cal_date'].head(n_days).tolist()
    trade_dates.reverse()
    _trade_dates_cache = [str(d) for d in trade_dates]
    return _trade_dates_cache

# =========================
# 从CSV文件加载主题成份股
# =========================
def load_theme_portfolio_from_csv():
    csv_pattern = os.path.join(CACHE_DIR, "theme_portfolio_*.csv")
    csv_files = glob.glob(csv_pattern)
    
    if not csv_files:
        print("未找到主题投资组合CSV文件，请先运行 theme_portfolio_strategy_cached.py")
        return {}, {}
    
    latest_file = max(csv_files, key=os.path.getmtime)
    print(f"加载主题投资组合: {latest_file}")
    
    df = pd.read_csv(latest_file, encoding='utf-8-sig')
    
    theme_stocks_map = {}
    name_map = {}
    
    for _, row in df.iterrows():
        theme = row['themes']
        ts_code = row['ts_code']
        name = row['name']
        
        if theme not in theme_stocks_map:
            theme_stocks_map[theme] = []
        theme_stocks_map[theme].append(ts_code)
        
        if ts_code not in name_map:
            name_map[ts_code] = name
    
    print(f"加载了 {len(theme_stocks_map)} 个主题，{len(name_map)} 只股票")
    return theme_stocks_map, name_map

# =========================
# 获取股票历史数据
# =========================
def get_stock_history(ts_code, n_days=25):
    trade_dates = get_trade_dates(n_days)
    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    df = cached_daily(ts_code, start_date, end_date)
    if df.empty or len(df) < 3:
        return pd.DataFrame()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df[df['trade_date'].isin(trade_dates)]
    if len(df) >= 3:
        return df.sort_values('trade_date').reset_index(drop=True)
    return pd.DataFrame()

# =========================
# 均线和乖离率计算
# =========================
def calculate_ma_and_biased(df):
    if df.empty or len(df) < 20:
        return None
    
    df = df.sort_values('trade_date').copy()
    close_prices = df['close'].values if 'close' in df.columns else df.iloc[:, 0].values
    
    ma5 = np.mean(close_prices[-5:]) if len(close_prices) >= 5 else close_prices[-1]
    ma10 = np.mean(close_prices[-10:]) if len(close_prices) >= 10 else ma5
    ma20 = np.mean(close_prices[-20:]) if len(close_prices) >= 20 else ma10
    
    current_price = close_prices[-1]
    
    ma5_biased = ((current_price - ma5) / ma5) * 100 if ma5 != 0 else 0
    ma10_biased = ((current_price - ma10) / ma10) * 100 if ma10 != 0 else 0
    ma20_biased = ((current_price - ma20) / ma20) * 100 if ma20 != 0 else 0
    
    ma5_slope = ((ma5 - np.mean(close_prices[-10:-5])) / np.mean(close_prices[-10:-5])) * 100 if len(close_prices) >= 10 and np.mean(close_prices[-10:-5]) != 0 else 0
    ma20_slope = ((ma20 - np.mean(close_prices[-25:-20])) / np.mean(close_prices[-25:-20])) * 100 if len(close_prices) >= 25 and np.mean(close_prices[-25:-20]) != 0 else 0
    
    volume = df['vol'].values if 'vol' in df.columns else df['amount'].values if 'amount' in df.columns else np.array([1]*len(df))
    avg_volume_5 = np.mean(volume[-5:]) if len(volume) >= 5 else np.mean(volume)
    avg_volume_20 = np.mean(volume[-20:]) if len(volume) >= 20 else avg_volume_5
    volume_ratio = avg_volume_5 / avg_volume_20 if avg_volume_20 != 0 else 1
    
    return {
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
        'current_price': current_price,
        'ma5_biased': ma5_biased, 'ma10_biased': ma10_biased, 'ma20_biased': ma20_biased,
        'ma5_slope': ma5_slope, 'ma20_slope': ma20_slope,
        'volume_ratio': volume_ratio, 'avg_volume_5': avg_volume_5, 'avg_volume_20': avg_volume_20
    }

# =========================
# 冲高回落概率计算
# =========================
def calculate_pullback_probability(ma_data, recent_data):
    if ma_data is None:
        return 0, "数据不足", []
    
    pullback_score = 0
    reasons = []
    
    if ma_data['ma5_biased'] > 10:
        pullback_score += 30
        reasons.append(f"5日乖离率过大({ma_data['ma5_biased']:.1f}%)")
    elif ma_data['ma5_biased'] > 5:
        pullback_score += 15
        reasons.append(f"5日乖离率偏高({ma_data['ma5_biased']:.1f}%)")
    
    if ma_data['ma20_biased'] > 20:
        pullback_score += 30
        reasons.append(f"20日乖离率过大({ma_data['ma20_biased']:.1f}%)")
    elif ma_data['ma20_biased'] > 10:
        pullback_score += 15
        reasons.append(f"20日乖离率偏高({ma_data['ma20_biased']:.1f}%)")
    
    if ma_data['volume_ratio'] < 0.7:
        pullback_score += 20
        reasons.append(f"量能萎缩({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] < 0.9:
        pullback_score += 10
        reasons.append(f"量能不足({ma_data['volume_ratio']:.2f})")
    
    if ma_data['ma5_slope'] > 10:
        pullback_score += 20
        reasons.append("5日均线上升过陡")
    
    if len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        last_change = recent_5.iloc[-1]['pct_chg'] if 'pct_chg' in recent_5.columns else 0
        if last_change > 8:
            pullback_score += 15
            reasons.append(f"单日涨幅过大({last_change:.1f}%)")
    
    pullback_probability = min(100, pullback_score)
    
    if pullback_probability >= 70:
        risk_level = "⚠️ 高风险"
    elif pullback_probability >= 50:
        risk_level = "⚡ 中风险"
    elif pullback_probability >= 30:
        risk_level = "📊 低风险"
    else:
        risk_level = "✅ 安全"
    
    return pullback_probability, risk_level, reasons

# =========================
# 短线盈利概率计算（未来10日涨幅>10%概率）
# =========================
def calculate_swing_return_probability(ma_data, recent_data):
    """
    计算未来10日涨幅>10%的概率
    核心思想：银行股虽然稳定但波动小，题材股波动大更适合低吸
    """
    if ma_data is None or recent_data is None or len(recent_data) < 10:
        return 0, "数据不足", []
    
    swing_score = 0
    reasons = []
    penalties = []
    
    # 1. 计算历史波动率（波动率是短线盈利的基础）
    if len(recent_data) >= 20:
        recent_20 = recent_data.tail(20)
        pct_changes = recent_20['pct_chg'].values
        volatility = pct_changes.std()
        
        # 波动率在2.5~6%之间最合适（太低像银行股，太高风险大）
        if 2.5 <= volatility <= 6:
            swing_score += 30
            reasons.append(f"波动率适中({volatility:.1f}%)，有空间但不极端")
        elif 6 < volatility <= 10:
            swing_score += 20
            reasons.append(f"波动率较高({volatility:.1f}%)，有机会但风险也大")
        elif volatility < 2.5:
            penalties.append(f"波动率过低({volatility:.1f}%)，不适合短线操作")
        else:
            penalties.append(f"波动率过高({volatility:.1f}%)，风险太大")
    
    # 2. 历史涨幅表现（曾经有过大涨基因）
    if len(recent_data) >= 30:
        max_20_day = recent_data.tail(30)['pct_chg'].max()
        if max_20_day >= 8:
            swing_score += 25
            reasons.append(f"历史弹性好，30日内单日曾涨{max_20_day:.1f}%")
        elif max_20_day >= 5:
            swing_score += 15
            reasons.append(f"历史弹性尚可，30日内单日曾涨{max_20_day:.1f}%")
    
    # 3. 近期调整充分但没有连续大跌
    if len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        recent_5_change = recent_5['pct_chg'].sum()
        
        if -12 <= recent_5_change <= -3:
            swing_score += 20
            reasons.append(f"近期调整幅度适中({recent_5_change:.1f}%)，有反弹空间")
        elif recent_5_change < -15:
            penalties.append(f"近期跌幅过大({recent_5_change:.1f}%)，可能趋势转坏")
    
    # 4. 乖离率支撑
    if -5 <= ma_data['ma5_biased'] <= 1:
        swing_score += 15
        reasons.append(f"乖离率处于支撑位({ma_data['ma5_biased']:.1f}%)")
    
    # 5. 量能配合
    if 0.6 <= ma_data['volume_ratio'] <= 1.2:
        swing_score += 10
        reasons.append(f"量能健康({ma_data['volume_ratio']:.2f})")
    
    # 6. 均线形态（至少MA5向上）
    if ma_data['ma5_slope'] > 0:
        swing_score += 10
        reasons.append("5日均线向上，趋势支持")
    
    if penalties:
        swing_score = max(0, swing_score - len(penalties) * 15)
    
    swing_probability = min(100, swing_score)
    
    if swing_probability >= 70:
        level = "🚀 高盈利概率"
    elif swing_probability >= 50:
        level = "📈 中等盈利概率"
    elif swing_probability >= 30:
        level = "🔄 一般盈利概率"
    else:
        level = "❌ 低盈利概率"
    
    all_reasons = reasons + penalties
    return swing_probability, level, all_reasons

# =========================
# 趋势延续概率计算（区分二波概率）
# =========================
def calculate_trend_continue_probability(ma_data, recent_data):
    """
    计算趋势延续概率（机构趋势股用这个，二波是游资题材股用的）
    """
    if ma_data is None:
        return 0, "数据不足", []
    
    trend_score = 0
    reasons = []
    penalties = []
    
    # 1. 均线多头且斜率稳定向上
    if ma_data['ma5'] > ma_data['ma10'] and ma_data['ma10'] > ma_data['ma20']:
        trend_score += 30
        reasons.append("均线多头排列")
    
    if ma_data['ma20_slope'] > 0:
        trend_score += 20
        reasons.append("20日均线向上")
    
    if ma_data['ma5_slope'] > 0:
        trend_score += 15
        reasons.append("5日均线向上")
    
    # 2. 乖离率适中（趋势股不能太偏离）
    if 0 <= ma_data['ma5_biased'] <= 8:
        trend_score += 20
        reasons.append(f"5日乖离率适中({ma_data['ma5_biased']:.1f}%)")
    
    # 3. 量能稳定
    if 0.7 <= ma_data['volume_ratio'] <= 1.4:
        trend_score += 15
        reasons.append(f"量能稳定({ma_data['volume_ratio']:.2f})")
    
    trend_probability = min(100, trend_score)
    
    if trend_probability >= 80:
        level = "🚀 强趋势延续"
    elif trend_probability >= 60:
        level = "📈 趋势延续概率高"
    elif trend_probability >= 40:
        level = "🔄 待确认"
    else:
        level = "❌ 趋势转弱概率大"
    
    return trend_probability, level, reasons

# =========================
# 二波启动概率计算（优化版）
# =========================
def calculate_second_wave_probability(ma_data, recent_data):
    if ma_data is None:
        return 0, "数据不足", []
    
    second_wave_score = 0
    reasons = []
    penalties = []
    
    # 【关键改进1】先检查是否是第一天调整
    is_first_day_correction = False
    if len(recent_data) >= 2:
        last_day = recent_data.iloc[-1]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        prev_day = recent_data.iloc[-2]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        
        # 如果前一天涨，今天跌，且跌幅较大
        if prev_day > 3 and last_day < -3:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天从大涨转为下跌，需确认")
        elif prev_day > 0 and last_day < -5:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天调整，跌幅较大")
    
    # 【关键改进2】如果是第一天调整，直接降低概率
    if is_first_day_correction:
        return 30, "🔄 待确认", penalties
    
    # 1. 乖离率健康
    if -5 <= ma_data['ma5_biased'] <= 2:
        second_wave_score += 25
        reasons.append(f"5日乖离率适中({ma_data['ma5_biased']:.1f}%)")
    elif -8 <= ma_data['ma5_biased'] < -5:
        second_wave_score += 15
        reasons.append(f"5日均线支撑({ma_data['ma5_biased']:.1f}%)")
    elif ma_data['ma5_biased'] > 5:
        penalties.append(f"5日乖离偏高({ma_data['ma5_biased']:.1f}%)")
    
    # 2. 均线多头排列
    if ma_data['ma5'] > ma_data['ma10'] and ma_data['ma10'] > ma_data['ma20']:
        second_wave_score += 20
        reasons.append("均线多头排列")
    elif ma_data['ma5'] < ma_data['ma10']:
        penalties.append("5日均线破10日线")
    
    # 3. 20日均线向上（保证中期趋势）
    if ma_data['ma20_slope'] > 0:
        second_wave_score += 10
        reasons.append("20日均线向上")
    
    # 4. 量能健康或放大
    if 0.7 <= ma_data['volume_ratio'] <= 1.3:
        second_wave_score += 20
        reasons.append(f"量能健康({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] > 1.3:
        second_wave_score += 25
        reasons.append(f"量能放大({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] < 0.6:
        penalties.append(f"量能萎缩({ma_data['volume_ratio']:.2f})")
    
    # 5. 20日乖离率健康
    if -5 <= ma_data['ma20_biased'] <= 10:
        second_wave_score += 15
        reasons.append(f"20日乖离率健康({ma_data['ma20_biased']:.1f}%)")
    elif ma_data['ma20_biased'] > 15:
        penalties.append(f"20日乖离过高({ma_data['ma20_biased']:.1f}%)")
    
    # 6. 前期有涨停或大阳
    if len(recent_data) >= 10:
        recent_10 = recent_data.tail(10)
        max_change = recent_10['pct_chg'].max() if 'pct_chg' in recent_10.columns else 0
        if max_change >= 10:
            second_wave_score += 10
            reasons.append(f"前期有涨停({max_change:.1f}%)")
    
    # 【关键改进3】检查是否有连续上涨后调整
    if len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        recent_5_changes = recent_5['pct_chg'].values if 'pct_chg' in recent_5.columns else []
        
        # 连续3天以上上涨后突然下跌
        if len(recent_5_changes) >= 4:
            if all(x > 0 for x in recent_5_changes[:-1]) and recent_5_changes[-1] < -3:
                second_wave_score = max(0, second_wave_score - 30)
                penalties.append("连续上涨后首次大跌，需要确认")
    
    second_wave_probability = min(100, second_wave_score)
    
    if second_wave_probability >= 80:
        wave_level = "🚀 强二波信号"
    elif second_wave_probability >= 60:
        wave_level = "📈 二波概率高"
    elif second_wave_probability >= 40:
        wave_level = "🔄 待确认"
    else:
        wave_level = "❌ 二波概率低"
    
    all_reasons = reasons + penalties
    return second_wave_probability, wave_level, all_reasons

# =========================
# 调整后回升概率计算（优化版）
# =========================
def calculate_rebound_probability(ma_data, recent_data, theme_avg_change):
    if ma_data is None:
        return 0, "数据不足", []
    
    rebound_score = 0
    reasons = []
    penalties = []
    
    # 【关键改进1】先检查是否是第一天调整
    is_first_day_correction = False
    if len(recent_data) >= 2:
        last_day = recent_data.iloc[-1]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        prev_day = recent_data.iloc[-2]['pct_chg'] if 'pct_chg' in recent_data.columns else 0
        
        # 如果前一天涨，今天跌，且跌幅较大
        if prev_day > 3 and last_day < -3:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天从大涨转为下跌，需确认")
        elif prev_day > 0 and last_day < -5:
            is_first_day_correction = True
            penalties.append("⚠️ 第一天调整，跌幅较大")
    
    # 【关键改进2】如果是第一天调整，降低回升概率
    if is_first_day_correction:
        rebound_score = 20
        return 20, "🔄 待确认", penalties
    
    # 1. 乖离率处于支撑位
    if -3 <= ma_data['ma5_biased'] <= 2:
        rebound_score += 20
        reasons.append(f"5日均线乖离率健康({ma_data['ma5_biased']:.1f}%)")
    elif -5 <= ma_data['ma5_biased'] < -3:
        rebound_score += 15
        reasons.append(f"5日均线乖离率偏大({ma_data['ma5_biased']:.1f}%)")
    elif ma_data['ma5_biased'] > 5:
        penalties.append(f"5日乖离偏高({ma_data['ma5_biased']:.1f}%)")
    
    if -5 <= ma_data['ma20_biased'] <= 5:
        rebound_score += 20
        reasons.append(f"20日均线乖离率健康({ma_data['ma20_biased']:.1f}%)")
    elif -10 <= ma_data['ma20_biased'] < -5:
        rebound_score += 15
        reasons.append(f"20日均线乖离率偏大({ma_data['ma20_biased']:.1f}%)")
    elif ma_data['ma20_biased'] > 12:
        penalties.append(f"20日乖离过高({ma_data['ma20_biased']:.1f}%)")
    
    # 2. 量能萎缩至地量（调整充分）
    if 0.5 <= ma_data['volume_ratio'] <= 0.8:
        rebound_score += 20
        reasons.append(f"量能萎缩至地量，调整充分({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] < 0.5:
        rebound_score += 25
        reasons.append(f"极度缩量，主力控盘({ma_data['volume_ratio']:.2f})")
    elif ma_data['volume_ratio'] > 1.5:
        penalties.append(f"量能过大({ma_data['volume_ratio']:.2f})")
    
    # 3. 均线支撑有效
    if ma_data['ma5'] > ma_data['ma10']:
        rebound_score += 15
        reasons.append("5日均线向上金叉")
    elif ma_data['ma5'] < ma_data['ma10']:
        penalties.append("5日均线破10日线")
    
    if ma_data['ma10'] > ma_data['ma20']:
        rebound_score += 15
        reasons.append("10日均线向上金叉")
    
    # 4. 近5日有调整（超跌反弹条件）
    if len(recent_data) >= 5:
        recent_5 = recent_data.tail(5)
        recent_5_change = recent_5['pct_chg'].sum() if 'pct_chg' in recent_5.columns else 0
        
        if -10 <= recent_5_change <= -5:
            rebound_score += 20
            reasons.append(f"近期调整幅度适中({recent_5_change:.1f}%)")
        elif recent_5_change < -10:
            rebound_score += 15
            reasons.append(f"近期超跌，存在反弹需求({recent_5_change:.1f}%)")
        elif recent_5_change > 0:
            # 【关键改进】近期在涨不算调整完成
            penalty = f"近期还在上涨({recent_5_change:.1f}%)，不是调整"
            penalties.append(penalty)
    
    # 5. 板块整体趋势未破
    if theme_avg_change > 0:
        rebound_score += 10
        reasons.append(f"板块整体趋势向上({theme_avg_change:.1f}%)")
    
    # 6. 量价配合
    if 0.8 <= ma_data['volume_ratio'] <= 1.2:
        rebound_score += 10
        reasons.append("量价配合良好")
    
    # 【关键改进3】有处罚项时扣分
    if penalties:
        rebound_score = max(0, rebound_score - len(penalties) * 10)
    
    rebound_probability = min(100, rebound_score)
    
    if rebound_probability >= 80:
        rebound_level = "🚀 强回升信号"
    elif rebound_probability >= 60:
        rebound_level = "📈 回升概率高"
    elif rebound_probability >= 40:
        rebound_level = "🔄 待确认"
    else:
        rebound_level = "❌ 回升概率低"
    
    all_reasons = reasons + penalties
    return rebound_probability, rebound_level, all_reasons

# =========================
# 均线轮动因子计算
# =========================
def calculate_ma_rotation_score(ma_data):
    if ma_data is None:
        return 0, {}
    
    rotation_factors = {}
    total_score = 0
    
    ma5_score = 0
    if ma_data['ma5'] > ma_data['ma10']:
        ma5_score += 20
    if ma_data['ma5'] > ma_data['ma20']:
        ma5_score += 20
    if ma_data['ma5_biased'] > 0:
        ma5_score += 10
    if ma_data['ma5_slope'] > 0:
        ma5_score += 10
    rotation_factors['ma5'] = ma5_score
    total_score += ma5_score
    
    ma20_score = 0
    if ma_data['ma20'] > ma_data['ma10']:
        ma20_score += 15
    if ma_data['ma20_biased'] > 0:
        ma20_score += 15
    if ma_data['ma20_slope'] > 0:
        ma20_score += 20
    rotation_factors['ma20'] = ma20_score
    total_score += ma20_score
    
    volume_score = 0
    if ma_data['volume_ratio'] > 1:
        volume_score += 25
    if 0.8 <= ma_data['volume_ratio'] <= 1.5:
        volume_score += 25
    rotation_factors['volume'] = volume_score
    total_score += volume_score
    
    return total_score, rotation_factors

# =========================
# 计算股票综合评分
# =========================
def calculate_comprehensive_leader_score(ts_code, name_map):
    df = get_stock_history(ts_code, 25)
    
    if df.empty or len(df) < 20:
        return None
    
    recent_5 = df.tail(5)
    recent_10 = df.tail(10)
    recent_20 = df.tail(20) if len(df) >= 20 else df
    
    ma_data = calculate_ma_and_biased(df)
    
    if ma_data is None:
        return None
    
    total_score = 0
    score_details = {}
    
    change_5 = recent_5['pct_chg'].sum()
    change_5_score = min(20, max(0, change_5 * 2))
    total_score += change_5_score
    score_details['5日涨幅'] = change_5_score
    
    change_20 = recent_20['pct_chg'].sum()
    change_20_score = min(15, max(0, change_20 * 0.6))
    total_score += change_20_score
    score_details['20日趋势'] = change_20_score
    
    max_up = recent_10['pct_chg'].max()
    strength_score = min(10, max(0, max_up * 1))
    total_score += strength_score
    score_details['强度'] = strength_score
    
    std_10 = recent_10['pct_chg'].std()
    stability_score = 10 if std_10 < 3 else 5 if std_10 < 5 else 0
    total_score += stability_score
    score_details['稳定性'] = stability_score
    
    latest = df.iloc[-1]
    amount = latest.get('amount', 0)
    amount_score = 8 if amount > 500000 else 5 if amount > 200000 else 3 if amount > 100000 else 1
    total_score += amount_score
    score_details['成交额'] = amount_score
    
    limit_info = get_stock_limit_info(ts_code)
    limit_up_count = len(df[df.apply(lambda row: is_limit_up(row, limit_info), axis=1)])
    limit_up_score = min(7, limit_up_count * 3.5)
    total_score += limit_up_score
    score_details['涨停'] = limit_up_score
    
    up_days = len(df[df['pct_chg'] > 0])
    up_ratio = up_days / len(df) if len(df) > 0 else 0
    up_ratio_score = up_ratio * 5
    total_score += up_ratio_score
    score_details['上涨占比'] = up_ratio_score
    
    ma_rotation_score, rotation_factors = calculate_ma_rotation_score(ma_data)
    total_score += ma_rotation_score
    score_details['均线轮动'] = ma_rotation_score
    
    pullback_prob, pullback_level, pullback_reasons = calculate_pullback_probability(ma_data, df)
    score_details['冲高回落概率'] = pullback_prob
    score_details['回落风险等级'] = pullback_level
    score_details['回落原因'] = pullback_reasons
    
    second_wave_prob, wave_level, wave_reasons = calculate_second_wave_probability(ma_data, df)
    score_details['二波启动概率'] = second_wave_prob
    score_details['二波信号等级'] = wave_level
    score_details['二波原因'] = wave_reasons
    
    # 计算趋势延续概率（机构股用）
    trend_continue_prob, trend_level, trend_reasons = calculate_trend_continue_probability(ma_data, df)
    score_details['趋势延续概率'] = trend_continue_prob
    score_details['趋势信号等级'] = trend_level
    score_details['趋势原因'] = trend_reasons
    
    # 计算短线盈利概率（低吸策略用）
    swing_return_prob, swing_level, swing_reasons = calculate_swing_return_probability(ma_data, df)
    score_details['短线盈利概率'] = swing_return_prob
    score_details['盈利信号等级'] = swing_level
    score_details['盈利原因'] = swing_reasons
    
    if total_score > 40:
        return {
            'ts_code': ts_code,
            'name': name_map.get(ts_code, ts_code),
            'total_score': total_score,
            'score_details': score_details,
            'ma_data': ma_data,
            'change_5': change_5,
            'change_20': change_20,
            'limit_up_count': limit_up_count,
            'volume_ratio': ma_data['volume_ratio']
        }
    
    return None

# =========================
# 计算主题历史排名和平均分（从数据库读取）
# =========================
def calculate_theme_historical_rankings(theme_stocks_map, trade_dates):
    print("\n从数据库读取近10日主题评分...")
    
    # 从数据库读取历史主题评分
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        'SELECT trade_date, theme_name, today_score FROM theme_scores ORDER BY trade_date',
        conn
    )
    conn.close()
    
    if df.empty:
        print("警告: 数据库中无历史主题评分，使用旧方法计算...")
        return calculate_theme_historical_rankings_old(theme_stocks_map, trade_dates)
    
    # 筛选需要的日期范围（最近10个交易日）
    target_dates = trade_dates[-10:] if len(trade_dates) >= 10 else trade_dates
    df_filtered = df[df['trade_date'].isin(target_dates)].copy()
    
    if df_filtered.empty:
        print("警告: 指定日期范围内无评分数据，使用旧方法计算...")
        return calculate_theme_historical_rankings_old(theme_stocks_map, trade_dates)
    
    # 按日期分组，计算每日排名
    theme_daily_scores = {theme: [] for theme in theme_stocks_map.keys()}
    
    for date in target_dates:
        daily_data = df_filtered[df_filtered['trade_date'] == date]
        if daily_data.empty:
            continue
        
        # 按当日评分排序，计算排名
        daily_sorted = daily_data.sort_values('today_score', ascending=False)
        daily_sorted['rank'] = range(1, len(daily_sorted) + 1)
        
        for _, row in daily_sorted.iterrows():
            theme = row['theme_name']
            if theme in theme_daily_scores:
                theme_daily_scores[theme].append({
                    'date': date,
                    'score': row['today_score'],
                    'rank': int(row['rank'])
                })
    
    # 计算主题摘要信息
    theme_summary = {}
    for theme, daily_records in theme_daily_scores.items():
        if daily_records and len(daily_records) >= 2:
            scores = [r['score'] for r in daily_records]
            ranks = [r['rank'] for r in daily_records]
            
            # 确保有足够的数据点
            if len(ranks) >= 2:
                # 趋势判断：看最近排名 vs 较早排名（排名数字越小越好）
                mid = len(ranks) // 2
                recent_avg_rank = sum(ranks[-mid:]) / mid if mid > 0 else ranks[-1]
                early_avg_rank = sum(ranks[:mid]) / mid if mid > 0 else ranks[0]
                
                if recent_avg_rank < early_avg_rank - 1:
                    trend = '上升'
                elif recent_avg_rank > early_avg_rank + 1:
                    trend = '下降'
                else:
                    trend = '震荡'
                
                rank_change = ranks[0] - ranks[-1] if len(ranks) >= 2 else 0
            else:
                trend = '震荡'
                rank_change = 0
            
            theme_summary[theme] = {
                'avg_score_10d': np.mean(scores),
                'avg_rank_10d': np.mean(ranks),
                'score_trend': trend,
                'rank_change': rank_change,
                'daily_scores': scores,
                'daily_ranks': ranks
            }
    
    # 补充缺失的主题（用默认值）
    for theme in theme_stocks_map.keys():
        if theme not in theme_summary:
            theme_summary[theme] = {
                'avg_score_10d': 50,
                'avg_rank_10d': 20,
                'score_trend': '震荡',
                'rank_change': 0,
                'daily_scores': [],
                'daily_ranks': []
            }
    
    return theme_summary

# =========================
# 旧方法：计算主题历史排名和平均分（备用）
# =========================
def calculate_theme_historical_rankings_old(theme_stocks_map, trade_dates):
    theme_daily_scores = {theme: [] for theme in theme_stocks_map.keys()}
    
    print("\n使用备用方法计算近10日主题排名和平均分...")
    
    for date_idx in range(len(trade_dates) - 10, len(trade_dates)):
        date = trade_dates[date_idx]
        daily_scores = {}
        
        for theme_name, theme_stocks in theme_stocks_map.items():
            stock_scores = []
            for ts_code in list(theme_stocks)[:20]:
                df = get_stock_history(ts_code, date_idx + 5)
                if not df.empty and date in df['trade_date'].values:
                    daily_data = df[df['trade_date'] == date].iloc[0]
                    score = daily_data.get('pct_chg', 0) * 2 + 50
                    stock_scores.append(score)
            
            if stock_scores:
                avg_score = np.mean(stock_scores)
                daily_scores[theme_name] = avg_score
            else:
                daily_scores[theme_name] = 50
        
        sorted_themes = sorted(daily_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (theme, score) in enumerate(sorted_themes, 1):
            theme_daily_scores[theme].append({
                'date': date,
                'score': score,
                'rank': rank
            })
    
    theme_summary = {}
    for theme, daily_records in theme_daily_scores.items():
        if daily_records:
            scores = [r['score'] for r in daily_records]
            ranks = [r['rank'] for r in daily_records]
            
            theme_summary[theme] = {
                'avg_score_10d': np.mean(scores),
                'avg_rank_10d': np.mean(ranks),
                'score_trend': '上升' if (sum(ranks[-5:])/5 < sum(ranks[:5])/5) else '下降' if (sum(ranks[-5:])/5 > sum(ranks[:5])/5) else '震荡',
                'rank_change': ranks[0] - ranks[-1] if len(ranks) >= 2 else 0,
                'daily_scores': scores,
                'daily_ranks': ranks
            }
    
    return theme_summary

# =========================
# 识别主题龙头
# =========================
def identify_theme_leaders(theme_stocks, name_map):
    leaders = []
    
    for ts_code in list(theme_stocks)[:50]:
        result = calculate_comprehensive_leader_score(ts_code, name_map)
        
        if result is not None:
            leaders.append(result)
    
    leaders.sort(key=lambda x: x['total_score'], reverse=True)
    return leaders[:10]

# =========================
# 今日主题评分与轮动分析
# =========================
def output_theme_analysis(ranked_themes, theme_summary, theme_leaders):
    print("\n\n" + "="*100)
    print("【今日主题评分与轮动分析】")
    print("="*100)
    
    # 全部主题排名（增加排名变化和涨停统计）
    print("\n📊 今日主题完整排名:")
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        avg_rank = summary.get('avg_rank_10d', 0)
        trend = summary.get('score_trend', '未知')
        rank_change = summary.get('rank_change', 0)
        
        trend_icon = "📈" if trend == "上升" else "📉" if trend == "下降" else "➡️"
        rank_change_icon = "⬆️" if rank_change > 0 else "⬇️" if rank_change < 0 else "➖"
        
        # 统计涨停龙头
        zt_stocks = []
        if theme in theme_leaders:
            for leader in theme_leaders[theme]:
                if leader['limit_up_count'] > 0:
                    zt_stocks.append(f"{leader['name']}({leader['limit_up_count']}次)")
        zt_str = f" | 涨停: {', '.join(zt_stocks[:3])}" if zt_stocks else ""
        
        print(f"\n{rank}. 【{theme}】")
        print(f"   今日评分: {today_score:.1f} | 近10日均分: {avg_10d:.1f}")
        rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
        print(f"   近10日均排: {avg_rank:.1f} | 排名变化: {rank_change_icon}{rank_change_text}{zt_str}")
        print(f"   趋势方向: {trend_icon} {trend}")
    
    print("\n\n" + "="*100)
    print("🔥 主题轮动分析")
    print("="*100)
    
    print("\n⬆️ 上升趋势板块（评分高且趋势向上）:")
    rising_themes = [(t, s) for t, s in ranked_themes if theme_summary.get(t, {}).get('score_trend') == "上升"]
    if rising_themes:
        for rank, (theme, score) in enumerate(rising_themes[:5], 1):
            rank_change = theme_summary.get(theme, {}).get('rank_change', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            print(f"   {rank}. {theme} - 今日评分: {score:.1f} (排名{rank_change_text})")
    else:
        print("   暂无可推荐板块")
    
    print("\n⬇️ 下降趋势板块（评分下降趋势）:")
    falling_themes = [(t, s) for t, s in ranked_themes if theme_summary.get(t, {}).get('score_trend') == "下降"]
    if falling_themes:
        for rank, (theme, score) in enumerate(falling_themes[:5], 1):
            rank_change = theme_summary.get(theme, {}).get('rank_change', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            print(f"   {rank}. {theme} - 今日评分: {score:.1f} (排名{rank_change_text})")
    else:
        print("   暂无可推荐板块")

# =========================
# 今日龙头评分与轮动分析
# =========================
def output_leader_analysis(theme_leaders):
    print("\n\n" + "="*100)
    print("【今日龙头评分与轮动分析】")
    print("="*100)
    
    leaders_dict = {}
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            ts_code = leader['ts_code']
            if ts_code not in leaders_dict:
                leaders_dict[ts_code] = leader.copy()
                leaders_dict[ts_code]['themes'] = []
            leaders_dict[ts_code]['themes'].append(theme)
    
    all_leaders = sorted(leaders_dict.values(), key=lambda x: x['total_score'], reverse=True)
    
    print("\n🏆 全市场综合TOP 10龙头:")
    for rank, leader in enumerate(all_leaders[:10], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        themes_str = "、".join(leader['themes'][:3])
        if len(leader['themes']) > 3:
            themes_str += f" 等{len(leader['themes'])}个主题"
        
        print(f"\n{rank}. {leader['name']:10s} ({leader['ts_code']:10s})")
        print(f"   所属主题: {themes_str}")
        print(f"   综合评分: {leader['total_score']:.1f} | 5日涨幅: {leader['change_5']:+.1f}% | 20日涨幅: {leader['change_20']:+.1f}%")
        print(f"   5日乖离: {ma_data['ma5_biased']:+.1f}% | 20日乖离: {ma_data['ma20_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
        print(f"   {details['回落风险等级']} | {details['二波信号等级']} | 涨停次数: {leader['limit_up_count']}次")
    
    print("\n📈 二波信号龙头 (强信号):")
    strong_wave_leaders = [l for l in all_leaders if l['score_details']['二波启动概率'] >= 70]
    if strong_wave_leaders:
        for rank, leader in enumerate(strong_wave_leaders[:5], 1):
            print(f"   {rank}. {leader['name']:10s} ({leader['ts_code']:10s}) - 二波概率: {leader['score_details']['二波启动概率']:.0f}%")
    else:
        print("   暂无可推荐龙头")

# =========================
# 三种策略推荐函数（优化版）
# =========================
def get_strategy_recommendations(ranked_themes, theme_leaders, theme_summary):
    """获取三种策略的TOP 3推荐"""
    
    # 收集所有龙头股
    all_leaders = []
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            leader_copy = leader.copy()
            leader_copy['theme'] = theme
            leader_copy['theme_avg_score'] = theme_summary.get(theme, {}).get('avg_score_10d', 50)
            leader_copy['theme_rank_change'] = theme_summary.get(theme, {}).get('rank_change', 0)
            all_leaders.append(leader_copy)
    
    # 策略一：强者恒强（追涨）
    # 条件：今日评分高 + 近期趋势向上 + 二波信号强
    strong_leaders = [
        l for l in all_leaders
        if l['total_score'] > 180
        and l['score_details']['二波启动概率'] >= 70
        and l['ma_data']['ma5_biased'] < 10  # 乖离率不能太高
    ]
    strong_leaders.sort(key=lambda x: (x['total_score'], x['score_details']['二波启动概率']), reverse=True)
    strategy1 = strong_leaders[:3]
    
    # 策略二：低吸潜伏（抄底）
    # 条件：近期调整充分 + 乖离率支撑位 + 短线盈利概率高 + 排除涨停股
    pullback_leaders = [
        l for l in all_leaders
        if -5 <= l['ma_data']['ma5_biased'] <= 2  # 乖离率在支撑位
        and l['score_details'].get('短线盈利概率', 0) >= 40  # 优先看短线盈利概率
        and l['ma_data']['volume_ratio'] <= 1.3  # 量能健康
        and l['limit_up_count'] == 0  # 排除今日涨停股（涨停股应归类为追涨）
    ]
    # 按短线盈利概率排序，这样能过滤掉银行股这种低波动股票
    pullback_leaders.sort(key=lambda x: x['score_details'].get('短线盈利概率', x['score_details']['二波启动概率']), reverse=True)
    strategy2 = pullback_leaders[:3]
    
    # 策略三：长线价值策略（合并中长线策略）
    # 条件：
    # 1. 上升最快的主题中
    # 2. 长期横盘后刚刚启动
    # 3. 均线多头排列（MA5 > MA10 > MA20）
    # 4. 乖离率适中（-2% ~ +6%）
    # 5. 温和上涨（5日涨幅 0-18%）
    # 6. 量能健康（量比 0.8-1.6）
    # 7. 二波信号强（>=60%）
    value_leaders = [
        l for l in all_leaders
        if l['theme_rank_change'] >= 10  # 上升最快的主题
        and l['ma_data']['ma5'] > l['ma_data']['ma10']  # MA5 > MA10
        and l['ma_data']['ma10'] > l['ma_data']['ma20']  # MA10 > MA20（均线多头排列）
        and -2 <= l['ma_data']['ma5_biased'] <= 6  # 乖离率适中
        and 0 <= l['change_5'] <= 18  # 温和上涨
        and 0.8 <= l['ma_data']['volume_ratio'] <= 1.6  # 量能健康
        and l['score_details']['二波启动概率'] >= 60  # 二波信号强
    ]
    value_leaders.sort(key=lambda x: (x['theme_rank_change'], x['score_details']['二波启动概率']), reverse=True)
    strategy3 = value_leaders[:3]
    
    return {
        'strategy1': strategy1,
        'strategy2': strategy2,
        'strategy3': strategy3
    }

# =========================
# 主线中长线潜力股分析
# =========================
def analyze_long_term_potentials(ranked_themes, theme_leaders, theme_summary, theme_stocks_cache):
    """
    分析主线中长线潜力股
    优化后的筛选条件：
    1. 十日平均分高的主题成份股
    2. 严格均线多头排列（MA5 > MA10 > MA20）
    3. 乖离率严格控制（-3% ~ +5%）
    4. 温和上涨节奏（5日涨幅 0-15%，日均 0-3%）
    5. 量能健康稳定（量比 0.8-1.5）
    6. 趋势稳定（避免大起大落）
    7. DeepSeek基本面估值和风险排除
    """
    
    # 步骤1：找出十日平均分高的主题（前5名）
    top_themes_by_avg = sorted(
        ranked_themes,
        key=lambda x: theme_summary.get(x[0], {}).get('avg_score_10d', 0),
        reverse=True
    )[:5]
    
    print("\n\n" + "="*100)
    print("【主线中长线潜力股分析】- 优化版")
    print("="*100)
    
    print(f"\n📈 十日平均分TOP 5主题:")
    for rank, (theme, score) in enumerate(top_themes_by_avg, 1):
        avg_10d = theme_summary.get(theme, {}).get('avg_score_10d', 0)
        print(f"   {rank}. 【{theme}】 - 十日平均分: {avg_10d:.1f}")
    
    # 步骤2：收集候选股票
    candidate_stocks = []
    
    for theme, _ in top_themes_by_avg:
        # 获取该主题的成份股
        if theme in theme_stocks_cache:
            stocks_info = theme_stocks_cache[theme]
            # 获取该主题的龙头股
            if theme in theme_leaders:
                for leader in theme_leaders[theme]:
                    ts_code = leader['ts_code']
                    # 筛选条件：沿着均线温和上涨，未涨停
                    ma_data = leader['ma_data']
                    
                    # === 优化后的严格筛选条件 ===
                    
                    # 1. 今日未涨停（排除加速上涨的）
                    if leader['limit_up_count'] == 0:
                        # 2. 严格均线多头排列：MA5 > MA10 > MA20（全部均线向上）
                        if ma_data['ma5'] > ma_data['ma10'] > ma_data['ma20']:
                            # 3. 乖离率严格控制（避免追高和超跌）
                            if -3 <= ma_data['ma5_biased'] <= 5:
                                # 4. 温和上涨节奏
                                change_5 = leader['change_5']
                                change_20 = leader['change_20']
                                daily_avg_change = change_5 / 5 if change_5 > 0 else 0
                                
                                # 5日涨幅温和（0-15%），日均涨幅健康（0-3%）
                                if 0 <= change_5 <= 15 and 0 <= change_20 <= 30 and 0 <= daily_avg_change <= 3:
                                    # 5. 量比健康（0.8-1.5之间，避免异常放量或缩量）
                                    if 0.8 <= ma_data['volume_ratio'] <= 1.5:
                                        # 6. 趋势稳定性评估（需要历史数据）
                                        # 这里简化处理：使用量比和乖离率综合判断
                                        volume_stability = 1.0 if 0.9 <= ma_data['volume_ratio'] <= 1.2 else 0.9
                                        bias_stability = 1.0 if -1 <= ma_data['ma5_biased'] <= 3 else 0.8
                                        stability_score = volume_stability * bias_stability
                                        
                                        # 7. 计算综合评分
                                        comprehensive_score = (
                                            leader['total_score'] * 0.4 +  # 技术面评分权重
                                            theme_summary.get(theme, {}).get('avg_score_10d', 50) * 0.3 +  # 主题稳定性
                                            stability_score * 100  # 趋势稳定性
                                        )
                                        
                                        candidate_stocks.append({
                                            **leader,
                                            'theme': theme,
                                            'theme_avg_score': theme_summary.get(theme, {}).get('avg_score_10d', 0),
                                            'themes': [theme],
                                            'stability_score': stability_score,
                                            'comprehensive_score': comprehensive_score,
                                            'daily_avg_change': daily_avg_change
                                        })
    
    # 步骤3：去重（同一股票多个主题）
    seen_codes = set()
    unique_candidates = []
    for stock in candidate_stocks:
        ts_code = stock['ts_code']
        if ts_code not in seen_codes:
            seen_codes.add(ts_code)
            unique_candidates.append(stock)
        else:
            # 更新主题列表
            for existing in unique_candidates:
                if existing['ts_code'] == ts_code:
                    if stock['theme'] not in existing['themes']:
                        existing['themes'].append(stock['theme'])
                    # 更新综合评分（取最高）
                    if stock['comprehensive_score'] > existing['comprehensive_score']:
                        existing['comprehensive_score'] = stock['comprehensive_score']
                        existing['stability_score'] = stock['stability_score']
                    break
    
    # 步骤4：排序（综合评分 = 技术面评分 + 主题稳定性 + 趋势稳定性）
    unique_candidates.sort(
        key=lambda x: x['comprehensive_score'],
        reverse=True
    )
    
    print(f"\n📊 筛选出 {len(unique_candidates)} 只候选股票")
    print(f"\n📋 筛选标准:")
    print(f"   ✅ 均线多头排列：MA5 > MA10 > MA20")
    print(f"   ✅ 乖离率控制：-3% ~ +5%")
    print(f"   ✅ 温和上涨：5日涨幅 0-15%，日均涨幅 0-3%")
    print(f"   ✅ 量能健康：量比 0.8-1.5")
    print(f"   ✅ 趋势稳定：避免大起大落")
    print(f"   ✅ 综合评分 = 技术面(40%) + 主题稳定(30%) + 趋势稳定(30%)")
    
    # 步骤3：去重（同一股票多个主题）
    seen_codes = set()
    unique_candidates = []
    for stock in candidate_stocks:
        ts_code = stock['ts_code']
        if ts_code not in seen_codes:
            seen_codes.add(ts_code)
            unique_candidates.append(stock)
        else:
            # 更新主题列表
            for existing in unique_candidates:
                if existing['ts_code'] == ts_code:
                    if stock['theme'] not in existing['themes']:
                        existing['themes'].append(stock['theme'])
                    break
    
    # 步骤4：排序（主题平均分 + 股票评分）
    unique_candidates.sort(
        key=lambda x: (x['theme_avg_score'], x['total_score']),
        reverse=True
    )
    
    print(f"\n📊 筛选出 {len(unique_candidates)} 只候选股票")
    
    # 步骤5：输出候选股票并加入DeepSeek分析
    print("\n🔍 候选股票详细分析:")
    
    results = []
    for rank, stock in enumerate(unique_candidates[:10], 1):
        ma_data = stock['ma_data']
        details = stock['score_details']
        themes_str = "、".join(stock['themes'][:3])
        
        comprehensive_score = stock.get('comprehensive_score', stock['total_score'])
        stability_score = stock.get('stability_score', 1.0)
        daily_avg = stock.get('daily_avg_change', 0)
        
        print(f"\n{rank}. {stock['name']:10s} ({stock['ts_code']:10s})")
        print(f"   所属主题: {themes_str}")
        print(f"   综合评分: {comprehensive_score:.1f} | 技术评分: {stock['total_score']:.1f} | 趋势稳定: {stability_score:.2f}")
        print(f"   5日涨幅: {stock['change_5']:+.1f}% | 20日涨幅: {stock['change_20']:+.1f}% | 日均: {daily_avg:+.1f}%")
        print(f"   均线系统: MA5={ma_data['ma5']:.2f} > MA10={ma_data['ma10']:.2f} > MA20={ma_data['ma20']:.2f}")
        print(f"   5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
        print(f"   风险等级: {details['回落风险等级']} | 二波信号: {details['二波信号等级']}")
        
        # DeepSeek 基本面分析
        fundamental_analysis = analyze_stock_fundamental(stock)
        if fundamental_analysis:
            print(f"   🤖 DeepSeek 基本面分析:")
            for line in fundamental_analysis.strip().split('\n')[:5]:
                print(f"      {line}")
            stock['fundamental_analysis'] = fundamental_analysis
        else:
            print(f"   🤖 基本面分析: 暂不可用")
        
        results.append(stock)
    
    return results

# =========================
# 短线潜力股分析（W底、揉搓线）
# =========================
def analyze_short_term_potentials(ranked_themes, theme_leaders, theme_summary, theme_stocks_cache):
    """
    分析短线潜力股
    筛选条件：
    1. 近期热点主线（十日平均分高的主题）
    2. 第一波拉升后调整5-20天
    3. 形成W底或揉搓线形态
    """
    
    # 步骤1：找出近期热点主线（前10名）
    top_themes = sorted(
        ranked_themes,
        key=lambda x: x[1],
        reverse=True
    )[:10]
    
    print("\n\n" + "="*100)
    print("【短线潜力跟踪】")
    print("="*100)
    
    print(f"\n📈 近期热点主线TOP 10:")
    for rank, (theme, score) in enumerate(top_themes, 1):
        avg_10d = theme_summary.get(theme, {}).get('avg_score_10d', 0)
        print(f"   {rank}. 【{theme}】 - 评分: {score:.1f}")
    
    # 步骤2：收集候选股票并识别形态
    candidate_stocks = []
    
    for theme, _ in top_themes:
        if theme in theme_leaders:
            for leader in theme_leaders[theme]:
                ts_code = leader['ts_code']
                name = leader['name']
                ma_data = leader['ma_data']
                details = leader['score_details']
                
                # 获取近期日线数据用于形态识别
                try:
                    end_date = datetime.now().strftime('%Y%m%d')
                    start_date = (datetime.now() - timedelta(days=40)).strftime('%Y%m%d')
                    
                    stock_df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
                    if stock_df is None or len(stock_df) < 25:
                        continue
                    
                    # 按日期排序
                    stock_df = stock_df.sort_values('trade_date')
                    
                    # 识别W底和揉搓线
                    pattern, pattern_details = identify_patterns(stock_df)
                    
                    if pattern:
                        # 计算第一波拉升后的调整天数
                        adjustment_days = calculate_adjustment_days(stock_df)
                        
                        # 筛选调整5-20天的个股
                        if 5 <= adjustment_days <= 20:
                            # 计算二波回升概率
                            rebound_prob, _, _ = calculate_rebound_probability(
                                ma_data, stock_df, leader['change_5']
                            )
                            
                            candidate_stocks.append({
                                'name': name,
                                'ts_code': ts_code,
                                'theme': theme,
                                'themes': [theme],
                                'total_score': leader['total_score'],
                                'change_5': leader['change_5'],
                                'change_20': leader['change_20'],
                                'ma_data': ma_data,
                                'pattern': pattern,
                                'pattern_details': pattern_details,
                                'adjustment_days': adjustment_days,
                                'score_details': details,
                                'rebound_probability': rebound_prob
                            })
                except Exception as e:
                    continue
    
    # 步骤3：去重和排序
    seen_codes = set()
    unique_candidates = []
    for stock in candidate_stocks:
        ts_code = stock['ts_code']
        if ts_code not in seen_codes:
            seen_codes.add(ts_code)
            unique_candidates.append(stock)
    
    # 按综合评分排序
    unique_candidates.sort(key=lambda x: x['total_score'], reverse=True)
    
    print(f"\n📊 筛选出 {len(unique_candidates)} 只短线潜力股")
    print(f"\n📋 筛选标准:")
    print(f"   ✅ 近期热点主线成份股")
    print(f"   ✅ 第一波拉升后调整5-20天")
    print(f"   ✅ 形成W底或揉搓线形态")
    
    # 步骤4：输出候选股票
    print("\n🔍 候选股票详细分析:")
    
    results = []
    for rank, stock in enumerate(unique_candidates[:8], 1):
        ma_data = stock['ma_data']
        details = stock['score_details']
        
        print(f"\n{rank}. {stock['name']:10s} ({stock['ts_code']:10s})")
        print(f"   所属主题: {stock['theme']}")
        print(f"   综合评分: {stock['total_score']:.1f}")
        print(f"   调整天数: {stock['adjustment_days']}天")
        print(f"   形态识别: {stock['pattern']} - {stock['pattern_details']}")
        print(f"   5日涨幅: {stock['change_5']:+.1f}% | 20日涨幅: {stock['change_20']:+.1f}%")
        print(f"   5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
        print(f"   二波信号: {details.get('二波信号等级', 'N/A')}")
        
        results.append(stock)
    
    return results

# =========================
# 识别W底和揉搓线形态
# =========================
def identify_patterns(df):
    """
    识别W底和揉搓线形态（优化版）
    揉搓线标准：
    1. 上下影线至少是实体的3倍
    2. 实体在0.5%~3%之间
    3. 上下影线比例接近（0.8~1.2倍）
    4. 均线趋势向上（MA5>MA10>MA20或MA5金叉MA10）
    5. 成交量温和放大
    6. 在均线附近获得支撑
    """
    if len(df) < 25:
        return None, None
    
    recent_df = df.tail(25).copy()
    closes = recent_df['close'].values
    highs = recent_df['high'].values
    lows = recent_df['low'].values
    opens = recent_df['open'].values
    vols = recent_df['vol'].values
    
    # 计算均线趋势
    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:])
    ma20 = np.mean(closes[-20:])
    
    ma_trend_up = ma5 > ma10 and ma10 > ma20
    ma_golden_cross = closes[-1] > ma5 and closes[-2] <= ma5
    
    # 计算成交量趋势
    avg_vol_5 = np.mean(vols[-5:])
    avg_vol_10 = np.mean(vols[-10:])
    vol_stable = avg_vol_5 >= avg_vol_10 * 0.8 and avg_vol_5 <= avg_vol_10 * 1.5
    
    # 识别揉搓线（最近5天内）
    for i in range(max(0, len(recent_df)-5), len(recent_df)):
        body = abs(closes[i] - opens[i])
        if body > 0:
            upper_shadow = highs[i] - max(closes[i], opens[i])
            lower_shadow = min(closes[i], opens[i]) - lows[i]
            
            body_pct = (body / closes[i]) * 100
            upper_ratio = upper_shadow / body if body > 0 else 0
            lower_ratio = lower_shadow / body if body > 0 else 0
            
            # 标准1：上下影线至少是实体的3倍
            cond1 = upper_shadow > body * 3 and lower_shadow > body * 3
            
            # 标准2：实体在0.5%~3%之间（不能太大也不能太小）
            cond2 = 0.5 <= body_pct <= 3.0
            
            # 标准3：上下影线比例接近（0.7~1.3倍）
            shadow_ratio = upper_shadow / lower_shadow if lower_shadow > 0 else 0
            cond3 = 0.7 <= shadow_ratio <= 1.3
            
            # 标准4：均线趋势向上
            cond4 = ma_trend_up or ma_golden_cross
            
            # 标准5：揉搓线在均线附近（收盘价接近MA5）
            price_to_ma5 = abs(closes[i] - ma5) / ma5 * 100
            cond5 = price_to_ma5 <= 3.0
            
            # 标准6：成交量温和（不能放巨量）
            vol_ratio = vols[i] / avg_vol_5
            cond6 = 0.6 <= vol_ratio <= 1.8
            
            # 计算揉搓线质量评分
            quality_score = 0
            if cond1:
                quality_score += 2
            if cond2:
                quality_score += 2
            if cond3:
                quality_score += 1
            if cond4:
                quality_score += 2
            if cond5:
                quality_score += 1
            if cond6:
                quality_score += 1
            
            # 只有质量评分>=6分的揉搓线才认为是有效信号
            if quality_score >= 6:
                trend_info = "均线多头" if ma_trend_up else "MA5金叉" if ma_golden_cross else "趋势偏弱"
                vol_info = "量能健康" if vol_ratio <= 1.2 else "量能放大"
                return "揉搓线", f"标准揉搓线(质量{quality_score}分), {trend_info}, {vol_info}"
    
    # 识别W底（最近20天内）- 保持原有逻辑但加入趋势要求
    if ma_trend_up or (ma5 > ma10 * 0.98):
        for i in range(3, len(closes)-3):
            for j in range(i+3, len(closes)):
                if (closes[i] == min(closes[max(0, i-2):i+3]) and 
                    closes[j] == min(closes[max(0, j-2):j+3])):
                    bottom_diff = ((closes[i] - closes[j]) / closes[i]) * 100
                    if 2 <= bottom_diff <= 15:
                        trend_info = "均线多头" if ma_trend_up else "趋势向上"
                        return "W底", f"标准W底({trend_info}), 双底相差{bottom_diff:.1f}%"
    
    return None, None

# =========================
# 计算调整天数
# =========================
def calculate_adjustment_days(df):
    """
    计算第一波拉升后的调整天数
    识别逻辑：
    1. 计算布林线上轨
    2. 找到突破布林线上轨的冲高日
    3. 从冲高日次日起，当高点低于冲高日高点时开始计算调整天数
    """
    if len(df) < 25:
        return 0
    
    df = df.sort_values('trade_date').tail(30).copy()
    closes = df['close'].values
    highs = df['high'].values
    
    if len(closes) < 25:
        return 0
    
    mean_20 = np.mean(closes[-20:])
    std_20 = np.std(closes[-20:])
    upper_band = mean_20 + 2 * std_20
    
    breakout_day = None
    breakout_high = 0
    
    for i in range(5, len(closes)-2):
        if highs[i] > upper_band:
            breakout_day = i
            breakout_high = highs[i]
            break
    
    if breakout_day is None:
        return 0
    
    for i in range(breakout_day + 1, len(closes) - 1):
        if highs[i] < breakout_high:
            adjustment_days = len(closes) - 1 - i
            if 5 <= adjustment_days <= 20:
                return adjustment_days
            elif adjustment_days > 20:
                return 0
    
    return 0

# =========================
# 明日中低风险主题和龙头推荐
# =========================
def output_tomorrow_recommendation(ranked_themes, theme_leaders, theme_summary):
    print("\n\n" + "="*100)
    print("【明日中低风险主题和龙头推荐】")
    print("="*100)
    
    print("\n🎯 推荐原则:")
    print("   ✅ 回落风险等级: 📊低风险 或 ✅安全")
    print("   ✅ 二波信号等级: 🚀强信号 或 📈概率高")
    print("   ✅ 5日乖离率适中 | 量比健康")
    
    low_risk_themes = []
    for theme, leaders in theme_leaders.items():
        safe_leaders = [l for l in leaders if l['score_details']['冲高回落概率'] < 70]
        if safe_leaders:
            avg_score = np.mean([l['total_score'] for l in safe_leaders])
            low_risk_themes.append({
                'theme': theme,
                'leaders': safe_leaders,
                'avg_score': avg_score
            })
    
    low_risk_themes.sort(key=lambda x: x['avg_score'], reverse=True)
    
    print(f"\n📊 推荐TOP {min(5, len(low_risk_themes))}个中低风险主题:")
    for rank, theme_info in enumerate(low_risk_themes[:5], 1):
        print(f"\n{rank}. 【{theme_info['theme']}】")
        
        safe_leaders = theme_info['leaders'][:3]
        print(f"   推荐龙头:")
        for i, leader in enumerate(safe_leaders, 1):
            ma_data = leader['ma_data']
            details = leader['score_details']
            
            print(f"     {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
            print(f"        评分: {leader['total_score']:.1f} | 5日: {leader['change_5']:+.1f}% | 20日: {leader['change_20']:+.1f}%")
            print(f"        回落风险: {details['回落风险等级']} | 二波信号: {details['二波信号等级']}")
            print(f"        5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    # ========== 新增：三种策略推荐 ==========
    print("\n\n" + "="*100)
    print("【三种操盘策略TOP 3推荐】")
    print("="*100)
    
    strategies = get_strategy_recommendations(ranked_themes, theme_leaders, theme_summary)
    
    # 策略一：强者恒强
    print("\n🚀 策略一：强者恒强（追涨）")
    print("   选股条件: 今日评分>180 + 二波概率>=70% + 乖离率<10%")
    print("   适用场景: 市场情绪高涨，热点明确，捕捉持续强势龙头")
    for i, leader in enumerate(strategies['strategy1'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        print(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
        print(f"      综合评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        print(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    # 策略二：低吸潜伏
    print("\n📉 策略二：低吸潜伏（抄底）")
    print("   选股条件: 乖离率支撑位(-5%~+2%) + 短线盈利概率>=40% + 量比<=1.3")
    print("   适用场景: 强势板块回调，寻找支撑位低吸机会，优先选波动率适中的股票")
    for i, leader in enumerate(strategies['strategy2'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        swing_prob = details.get('短线盈利概率', details['二波启动概率'])
        swing_level = details.get('盈利信号等级', '')
        print(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
        print(f"      综合评分: {leader['total_score']:.1f} | 短线盈利概率: {swing_prob:.0f}% {swing_level}")
        print(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    # 策略三：长线价值策略
    print("\n🎯 策略三：长线价值策略（中长线布局）")
    print("   选股条件: 上升最快主题 + 均线多头 + 乖离适中(-2%~+6%) + 温和上涨 + 二波概率>=60%")
    print("   适用场景: 捕捉长期横盘后启动的价值标的，游资机构共振")
    for i, leader in enumerate(strategies['strategy3'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        print(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s}) 【{leader['theme']}】")
        print(f"      综合评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        print(f"      5日涨: {leader['change_5']:+.1f}% | 5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
        print(f"      均线多头: MA{ma_data['ma5']:.1f}>{ma_data['ma10']:.1f}>{ma_data['ma20']:.1f}")
    
    print("\n💡 风险提示:")
    print("   1. 以上推荐基于历史数据，不构成投资建议")
    print("   2. 注意控制仓位，做好止损止盈")
    print("   3. 密切关注市场整体环境变化")

# =========================
# 保存文本复盘报告
# =========================
def save_text_report(ranked_themes, theme_leaders, theme_summary, trade_dates, market_emotion=None, fastest_rising=None, long_term_potentials=None, short_term_potentials=None):
    report_lines = []
    report_lines.append("="*100)
    report_lines.append("每日盘后复盘报告")
    report_lines.append(f"日期: {trade_dates[-1]}")
    report_lines.append("="*100)
    
    # ========== 大盘情绪分析 ==========
    if market_emotion:
        report_lines.append("\n\n【大盘情绪分析】")
        report_lines.append("="*100)
        report_lines.append(f"📊 大盘点位: {market_emotion.get('大盘点位', 'N/A')} | 涨跌幅: {market_emotion.get('大盘涨跌幅', 'N/A')}%")
        report_lines.append(f"🌡️ 市场情绪: {market_emotion.get('情绪指数', 'N/A')} ({market_emotion.get('市场阶段', 'N/A')})")
        report_lines.append(f"📈 指数环境: {market_emotion.get('指数环境', 'N/A')} | 风险等级: {market_emotion.get('风险等级', 'N/A')}")
        report_lines.append(f"💰 全市场成交额: {market_emotion.get('全市场成交额（亿元）', 'N/A')}亿元")
        report_lines.append(f"🔴 涨停家数: {market_emotion.get('涨停家数', 'N/A')} | 🔽 跌停家数: {market_emotion.get('跌停家数', 'N/A')}")
        report_lines.append(f"💥 炸板率: {market_emotion.get('炸板率', 'N/A')}% | 📊 连板高度: {market_emotion.get('连板高度', 'N/A')}")
        report_lines.append(f"📊 上涨占比: {market_emotion.get('上涨占比', 'N/A')}% | 强势股占比: {market_emotion.get('强势股占比', 'N/A')}%")
        report_lines.append(f"📉 20日偏离率: {market_emotion.get('20日偏离率', 'N/A')}% | MA20方向: {market_emotion.get('MA20方向', 'N/A')}")
        report_lines.append(f"🎯 建议仓位: {market_emotion.get('最终建议仓位', 'N/A')}")
        report_lines.append(f"📊 均线状态: {market_emotion.get('均线状态', 'N/A')} | MA5:{market_emotion.get('站上MA5', 'N/A')} MA20:{market_emotion.get('站上MA20', 'N/A')} MA60:{market_emotion.get('站上MA60', 'N/A')}")
    
    report_lines.append("\n\n【今日主题评分与轮动分析】")
    report_lines.append("="*100)
    
    # 全部主题排名（增加排名变化和涨停统计）
    report_lines.append("\n📊 今日主题完整排名:")
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        avg_rank = summary.get('avg_rank_10d', 0)
        trend = summary.get('score_trend', '未知')
        rank_change = summary.get('rank_change', 0)
        
        trend_icon = "📈" if trend == "上升" else "📉" if trend == "下降" else "➡️"
        rank_change_icon = "⬆️" if rank_change > 0 else "⬇️" if rank_change < 0 else "➖"
        
        # 统计涨停龙头
        zt_stocks = []
        if theme in theme_leaders:
            for leader in theme_leaders[theme]:
                if leader['limit_up_count'] > 0:
                    zt_stocks.append(f"{leader['name']}({leader['limit_up_count']}次)")
        zt_str = f" | 涨停: {', '.join(zt_stocks[:3])}" if zt_stocks else ""
        
        report_lines.append(f"\n{rank}. 【{theme}】")
        report_lines.append(f"   今日评分: {today_score:.1f} | 近10日均分: {avg_10d:.1f}")
        rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
        report_lines.append(f"   近10日均排: {avg_rank:.1f} | 排名变化: {rank_change_icon}{rank_change_text}{zt_str}")
        report_lines.append(f"   趋势方向: {trend_icon} {trend}")
    
    # 当日上升最快的主题（按排名变化排序）
    if fastest_rising:
        report_lines.append("\n\n" + "="*100)
        report_lines.append("🔥 主题轮动分析")
        report_lines.append("="*100)
        
        report_lines.append("\n⬆️ 上升趋势板块（评分高且趋势向上）:")
        rising_themes = [(t, s) for t, s in ranked_themes if theme_summary.get(t, {}).get('score_trend') == "上升"]
        for rank, (theme, score) in enumerate(rising_themes[:5], 1):
            rank_change = theme_summary.get(theme, {}).get('rank_change', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            report_lines.append(f"   {rank}. {theme} - 今日评分: {score:.1f} (排名{rank_change_text})")
        
        report_lines.append("\n⬇️ 下降趋势板块（评分下降趋势）:")
        falling_themes = [(t, s) for t, s in ranked_themes if theme_summary.get(t, {}).get('score_trend') == "下降"]
        for rank, (theme, score) in enumerate(falling_themes[:5], 1):
            rank_change = theme_summary.get(theme, {}).get('rank_change', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            report_lines.append(f"   {rank}. {theme} - 今日评分: {score:.1f} (排名{rank_change_text})")
        
        report_lines.append("\n\n🚀 当日上升最快的主题 TOP 5:")
        for rank, (theme, today_score, rank_change, score_change) in enumerate(fastest_rising, 1):
            summary = theme_summary.get(theme, {})
            avg_10d = summary.get('avg_score_10d', 0)
            rank_change_text = f"上升{rank_change}位" if rank_change > 0 else f"下降{-rank_change}位" if rank_change < 0 else "不变"
            report_lines.append(f"\n   {rank}. 【{theme}】")
            report_lines.append(f"      排名变化: {rank_change_text} | 评分变化: {score_change:+.1f}")
            report_lines.append(f"      今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f}")
            if theme in theme_leaders and theme_leaders[theme]:
                leader = theme_leaders[theme][0]
                report_lines.append(f"      最强龙头: {leader['name']} ({leader['ts_code']})")
    
    report_lines.append("\n\n【今日龙头评分与轮动分析】")
    report_lines.append("="*100)
    all_leaders = []
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            leader_copy = leader.copy()
            leader_copy['theme'] = theme
            all_leaders.append(leader_copy)
    all_leaders.sort(key=lambda x: x['total_score'], reverse=True)
    
    report_lines.append("\n🏆 全市场综合TOP 10龙头:")
    for rank, leader in enumerate(all_leaders[:10], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        report_lines.append(f"\n{rank}. {leader['name']:10s} ({leader['ts_code']:10s}) 【{leader['theme']}】")
        report_lines.append(f"   综合评分: {leader['total_score']:.1f} | 5日涨幅: {leader['change_5']:+.1f}% | 20日涨幅: {leader['change_20']:+.1f}%")
        report_lines.append(f"   风险等级: {details['回落风险等级']} | 二波信号: {details['二波信号等级']}")
    
    report_lines.append("\n\n【明日中低风险主题和龙头推荐】")
    report_lines.append("="*100)
    report_lines.append("\n🎯 推荐原则: 中低风险 + 强二波信号 + 均线健康")
    
    low_risk_themes = []
    for theme, leaders in theme_leaders.items():
        safe_leaders = [l for l in leaders if l['score_details']['冲高回落概率'] < 70]
        if safe_leaders:
            avg_score = np.mean([l['total_score'] for l in safe_leaders])
            low_risk_themes.append({
                'theme': theme,
                'leaders': safe_leaders,
                'avg_score': avg_score
            })
    low_risk_themes.sort(key=lambda x: x['avg_score'], reverse=True)
    
    for rank, theme_info in enumerate(low_risk_themes[:5], 1):
        report_lines.append(f"\n{rank}. 【{theme_info['theme']}】")
        safe_leaders = theme_info['leaders'][:3]
        for i, leader in enumerate(safe_leaders, 1):
            ma_data = leader['ma_data']
            details = leader['score_details']
            report_lines.append(f"     {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
            report_lines.append(f"        评分: {leader['total_score']:.1f} | 回落风险: {details['冲高回落概率']:.0f}% | 二波概率: {details['二波启动概率']:.0f}%")
    
    # ========== 新增：三种策略推荐保存 ==========
    report_lines.append("\n\n【三种操盘策略TOP 3推荐】")
    report_lines.append("="*100)
    
    strategies = get_strategy_recommendations(ranked_themes, theme_leaders, theme_summary)
    
    # 策略一：强者恒强
    report_lines.append("\n🚀 策略一：强者恒强（追涨）")
    report_lines.append("   选股条件: 今日评分>180 + 二波概率>=70% + 乖离率<10%")
    report_lines.append("   适用场景: 市场情绪高涨，热点明确，捕捉持续强势龙头")
    for i, leader in enumerate(strategies['strategy1'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        report_lines.append(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
        report_lines.append(f"      综合评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        report_lines.append(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    # 策略二：低吸潜伏
    report_lines.append("\n📉 策略二：低吸潜伏（抄底）")
    report_lines.append("   选股条件: 乖离率支撑位(-5%~+2%) + 短线盈利概率>=40% + 量比<=1.3")
    report_lines.append("   适用场景: 强势板块回调，寻找支撑位低吸机会，优先选波动率适中的股票")
    for i, leader in enumerate(strategies['strategy2'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        swing_prob = details.get('短线盈利概率', details['二波启动概率'])
        swing_level = details.get('盈利信号等级', '')
        report_lines.append(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
        report_lines.append(f"      综合评分: {leader['total_score']:.1f} | 短线盈利概率: {swing_prob:.0f}% {swing_level}")
        report_lines.append(f"      5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
    
    # 策略三：长线价值策略
    report_lines.append("\n🎯 策略三：长线价值策略（中长线布局）")
    report_lines.append("   选股条件: 上升最快主题 + 均线多头 + 乖离适中(-2%~+6%) + 温和上涨 + 二波概率>=60%")
    report_lines.append("   适用场景: 捕捉长期横盘后启动的价值标的，游资机构共振")
    for i, leader in enumerate(strategies['strategy3'], 1):
        ma_data = leader['ma_data']
        details = leader['score_details']
        report_lines.append(f"   {i}. {leader['name']:10s} ({leader['ts_code']:10s}) 【{leader['theme']}】")
        report_lines.append(f"      综合评分: {leader['total_score']:.1f} | 二波概率: {details['二波启动概率']:.0f}%")
        report_lines.append(f"      5日涨: {leader['change_5']:+.1f}% | 5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
        report_lines.append(f"      均线多头: MA{ma_data['ma5']:.1f}>{ma_data['ma10']:.1f}>{ma_data['ma20']:.1f}")
    
    # ========== 主线中长线潜力股分析 ==========
    if long_term_potentials:
        report_lines.append("\n\n【主线中长线潜力股分析】")
        report_lines.append("="*100)
        
        # 找出十日平均分高的主题
        top_themes_by_avg = sorted(
            ranked_themes,
            key=lambda x: theme_summary.get(x[0], {}).get('avg_score_10d', 0),
            reverse=True
        )[:5]
        
        report_lines.append("\n📈 十日平均分TOP 5主题:")
        for rank, (theme, score) in enumerate(top_themes_by_avg, 1):
            avg_10d = theme_summary.get(theme, {}).get('avg_score_10d', 0)
            report_lines.append(f"   {rank}. 【{theme}】 - 十日平均分: {avg_10d:.1f}")
        
        report_lines.append(f"\n📊 筛选出 {len(long_term_potentials)} 只候选股票")
        
        report_lines.append("\n🔍 候选股票详细分析:")
        for rank, stock in enumerate(long_term_potentials[:10], 1):
            ma_data = stock['ma_data']
            details = stock['score_details']
            themes_str = "、".join(stock['themes'][:3])
            
            comprehensive_score = stock.get('comprehensive_score', stock['total_score'])
            stability_score = stock.get('stability_score', 1.0)
            daily_avg = stock.get('daily_avg_change', 0)
            
            report_lines.append(f"\n{rank}. {stock['name']:10s} ({stock['ts_code']:10s})")
            report_lines.append(f"   所属主题: {themes_str}")
            report_lines.append(f"   综合评分: {comprehensive_score:.1f} | 技术评分: {stock['total_score']:.1f} | 趋势稳定: {stability_score:.2f}")
            report_lines.append(f"   5日涨幅: {stock['change_5']:+.1f}% | 20日涨幅: {stock['change_20']:+.1f}% | 日均: {daily_avg:+.1f}%")
            report_lines.append(f"   均线系统: MA5={ma_data['ma5']:.2f} > MA10={ma_data['ma10']:.2f} > MA20={ma_data['ma20']:.2f}")
            report_lines.append(f"   5日乖离: {ma_data['ma5_biased']:+.1f}% | 量比: {ma_data['volume_ratio']:.2f}")
            report_lines.append(f"   风险等级: {details['回落风险等级']} | 二波信号: {details['二波信号等级']}")
            
            # 保存基本面分析
            if 'fundamental_analysis' in stock:
                report_lines.append(f"   🤖 DeepSeek 基本面分析:")
                for line in stock['fundamental_analysis'].strip().split('\n')[:5]:
                    report_lines.append(f"      {line}")
    
    # ========== 短线潜力跟踪 ==========
    if short_term_potentials:
        report_lines.append("\n\n【短线潜力跟踪】")
        report_lines.append("="*100)
        report_lines.append("筛选条件: 近期热点主线 + 第一波拉升后调整5-20天 + W底或揉搓线形态")
        report_lines.append(f"共发现 {len(short_term_potentials)} 只候选标的")
        
        for rank, stock in enumerate(short_term_potentials[:10], 1):
            ma_data = stock['ma_data']
            themes_str = "、".join(stock['themes'][:2])
            pattern = stock.get('pattern', 'N/A')
            pattern_details = stock.get('pattern_details', '')
            adjustment_days = stock.get('adjustment_days', 0)
            
            # 添加形态标识
            pattern_emoji = "📉" if "W底" in pattern else "🔄" if "揉搓线" in pattern else "📊"
            
            report_lines.append(f"\n{rank}. {stock['name']:10s} ({stock['ts_code']:10s})")
            report_lines.append(f"   所属主题: {themes_str}")
            report_lines.append(f"   {pattern_emoji} 形态: {pattern} {pattern_details}")
            report_lines.append(f"   调整天数: {adjustment_days}天 | 5日涨幅: {stock['change_5']:+.1f}%")
            report_lines.append(f"   综合评分: {stock['total_score']:.1f} | 量比: {ma_data['volume_ratio']:.2f}")
            
            rebound_prob = stock.get('rebound_probability', 0)
            report_lines.append(f"   二波回升概率: {rebound_prob:.0f}%")
    
    report_text = "\n".join(report_lines)
    report_file = os.path.join(CACHE_DIR, f"daily_review_{trade_dates[-1]}.txt")
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(f"\n✓ 文本复盘报告已保存: {report_file}")
    
    # ========== 保存至SQLite数据库 ==========
    db_manager.save_theme_scores(trade_dates[-1], ranked_themes, theme_summary)
    db_manager.save_leader_scores(trade_dates[-1], theme_leaders, theme_summary)
    db_manager.save_strategy_recommendations(trade_dates[-1], strategies)

# =========================
# 基于今日真实盘面计算主题评分（游资风格）
# =========================
def calculate_today_market_scores(theme_stocks_map, trade_date):
    """
    完全基于今日真实盘面数据计算主题评分
    核心逻辑：
    1. 今日板块平均涨跌幅（权重最大）
    2. 今日涨停家数
    3. 上涨占比和强势股占比
    4. 跌停家数惩罚
    """
    print(f"\n{'='*80}")
    print(f"今日主题评分 - 完全基于 {trade_date} 真实盘面数据")
    print(f"{'='*80}")
    
    # 获取今日涨跌停股票列表
    zt_stocks = set()
    dt_stocks = set()
    
    try:
        zt_df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
        if zt_df is not None and not zt_df.empty:
            zt_stocks = set(zt_df['ts_code'].tolist())
            print(f"今日涨停: {len(zt_stocks)} 家")
    except Exception as e:
        print(f"获取涨停数据失败: {e}")
    
    try:
        dt_df = pro.limit_list_ths(trade_date=trade_date, limit_type='跌停池')
        if dt_df is not None and not dt_df.empty:
            dt_stocks = set(dt_df['ts_code'].tolist())
            print(f"今日跌停: {len(dt_stocks)} 家")
    except Exception as e:
        print(f"获取跌停数据失败: {e}")
    
    theme_scores = {}
    theme_real_data = {}
    
    for theme, stocks in theme_stocks_map.items():
        if len(stocks) < 3:
            continue
        
        stock_changes = []
        zt_count = 0
        up_count = 0
        strong_count = 0
        dt_count = 0
        total_count = 0
        
        for ts_code in stocks[:30]:  # 每个主题前30只
            try:
                df = pro.daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
                if not df.empty and len(df) > 0:
                    pct_chg = df['pct_chg'].iloc[0]
                    stock_changes.append(pct_chg)
                    total_count += 1
                    
                    if ts_code in zt_stocks:
                        zt_count += 1
                    if ts_code in dt_stocks:
                        dt_count += 1
                    if pct_chg > 0:
                        up_count += 1
                    if pct_chg >= 5:
                        strong_count += 1
            except Exception as e:
                continue
        
        if len(stock_changes) < 3:
            continue
        
        avg_change = np.mean(stock_changes)
        up_ratio = up_count / total_count if total_count > 0 else 0
        strong_ratio = strong_count / total_count if total_count > 0 else 0
        
        # ========== 游资风格的评分 ==========
        base_score = 50
        
        # 1. 今日板块涨跌幅（核心权重）
        change_score = avg_change * 12  # 权重最大
        base_score += change_score
        
        # 2. 涨停家数加分
        zt_score = min(zt_count * 6, 30)
        base_score += zt_score
        
        # 3. 上涨占比加分
        up_ratio_score = min(up_ratio * 60, 35)
        base_score += up_ratio_score
        
        # 4. 强势股加分（>=5%）
        strong_score = min(strong_ratio * 90, 25)
        base_score += strong_score
        
        # 5. 跌停惩罚
        dt_penalty = min(dt_count * 10, 25)
        base_score -= dt_penalty
        
        theme_scores[theme] = max(0, base_score)
        theme_real_data[theme] = {
            'avg_change': avg_change,
            'zt_count': zt_count,
            'dt_count': dt_count,
            'up_ratio': up_ratio,
            'strong_ratio': strong_ratio,
            'total_stocks': total_count
        }
    
    # 排序
    ranked_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
    
    print(f"\n今日TOP 10主题（真实盘面数据）:")
    for rank, (theme, score) in enumerate(ranked_themes[:10], 1):
        data = theme_real_data[theme]
        print(f"\n{rank}. 【{theme}】")
        print(f"   评分: {score:.1f}")
        print(f"   今日平均: {data['avg_change']:+.2f}% | 涨停: {data['zt_count']} | 跌停: {data['dt_count']}")
        print(f"   上涨占比: {data['up_ratio']:.1%} | 强势股: {data['strong_ratio']:.1%}")
    
    return ranked_themes, theme_scores, theme_real_data


# =========================
# 生成复盘报告
# =========================
def generate_report(theme_stocks_map, name_map, trade_dates, market_emotion=None):
    print("\n" + "="*100)
    print("每日盘后复盘报告 (最终版)")
    print(f"日期: {trade_dates[-1]}")
    print("="*100)
    
    # 计算主题历史排名和平均分（保留用于趋势分析）
    theme_summary = calculate_theme_historical_rankings(theme_stocks_map, trade_dates)
    
    # ========== 关键修改：使用今日真实盘面评分 ==========
    ranked_themes, theme_scores, theme_real_data = calculate_today_market_scores(
        theme_stocks_map, trade_dates[-1]
    )
    
    # ========== 仍然计算龙头股用于个股分析 ==========
    theme_leaders = {}
    for theme_name, theme_stocks in theme_stocks_map.items():
        leaders = identify_theme_leaders(list(theme_stocks), name_map)
        theme_leaders[theme_name] = leaders
    
    # 找出当日上升最快的主题（排名变化最大）
    theme_rank_changes = []
    for theme, today_score in theme_scores.items():
        summary = theme_summary.get(theme, {})
        rank_change = summary.get('rank_change', 0)
        score_change = today_score - summary.get('avg_score_10d', 0)
        theme_rank_changes.append((theme, today_score, rank_change, score_change))
    
    # 按排名变化排序，找出上升最快的
    fastest_rising = sorted(theme_rank_changes, key=lambda x: x[2], reverse=True)[:5]
    
    # 分析主线中长线潜力股
    long_term_potentials = analyze_long_term_potentials(ranked_themes, theme_leaders, theme_summary, theme_stocks_map)
    
    # 分析短线潜力股（W底、揉搓线）
    short_term_potentials = analyze_short_term_potentials(ranked_themes, theme_leaders, theme_summary, theme_stocks_map)
    
    print("\n\n" + "="*100)
    print("主题热点排名（按今日综合评分 + 近10日平均分）")
    print("="*100)
    
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        avg_rank = summary.get('avg_rank_10d', 0)
        
        print(f"\n第{rank}名: 【{theme}】")
        print(f"  今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f} | 近10日平均排名: {avg_rank:.1f}")
        print(f"  趋势: {summary.get('score_trend', '未知')} | 排名变化: {summary.get('rank_change', 0):+d}")
        
        if theme in theme_leaders and theme_leaders[theme]:
            print(f"  龙头股 TOP 3:")
            for i, leader in enumerate(theme_leaders[theme][:3], 1):
                ma_data = leader['ma_data']
                print(f"    {i}. {leader['name']:10s} ({leader['ts_code']:10s})")
                print(f"       总分:{leader['total_score']:.1f} | 5日:{leader['change_5']:+.1f}% | 20日:{leader['change_20']:+.1f}%")
                print(f"       5日乖离:{ma_data['ma5_biased']:+.1f}% | 20日乖离:{ma_data['ma20_biased']:+.1f}%")
                print(f"       {leader['score_details']['回落风险等级']} | {leader['score_details']['二波信号等级']}")
    
    # TOP 5板块调整后回升概率分析
    print("\n\n" + "="*100)
    print("TOP 5 板块调整后回升概率分析")
    print("="*100)
    
    for rank, (theme, today_score) in enumerate(ranked_themes[:5], 1):
        print(f"\n{'='*80}")
        print(f"第{rank}名: 【{theme}】")
        print(f"{'='*80}")
        
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        
        if theme in theme_leaders and theme_leaders[theme]:
            rebound_stocks = []
            
            for leader in theme_leaders[theme][:5]:
                ma_data = leader['ma_data']
                
                rebound_prob, rebound_level, rebound_reasons = calculate_rebound_probability(
                    ma_data, get_stock_history(leader['ts_code'], 25), leader['change_5']
                )
                
                rebound_stocks.append({
                    'name': leader['name'],
                    'ts_code': leader['ts_code'],
                    'rebound_prob': rebound_prob,
                    'rebound_level': rebound_level,
                    'reasons': rebound_reasons,
                    'ma5_biased': ma_data['ma5_biased'] if ma_data else 0,
                    'ma20_biased': ma_data['ma20_biased'] if ma_data else 0,
                    'volume_ratio': leader['volume_ratio'],
                    'recent_5_change': leader['change_5']
                })
            
            rebound_stocks.sort(key=lambda x: x['rebound_prob'], reverse=True)
            
            avg_rebound_prob = np.mean([s['rebound_prob'] for s in rebound_stocks])
            
            print(f"\n板块回升概率评估: {avg_rebound_prob:.0f}%")
            print(f"近10日表现: 平均分{avg_10d:.1f}，{summary.get('score_trend', '未知')}趋势")
            
            print(f"\n回升候选 TOP 3:")
            for i, stock in enumerate(rebound_stocks[:3], 1):
                print(f"  {i}. {stock['name']:10s} ({stock['ts_code']:10s})")
                print(f"     回升概率: {stock['rebound_prob']:.0f}% [{stock['rebound_level']}]")
                print(f"     5日乖离:{stock['ma5_biased']:+.1f}% | 20日乖离:{stock['ma20_biased']:+.1f}% | 量比:{stock['volume_ratio']:.2f}")
                print(f"     近5日涨跌:{stock['recent_5_change']:+.1f}%")
                if stock['reasons']:
                    print(f"     回升依据: {' | '.join(stock['reasons'][:3])}")
    
    # 当日上升最快的主题分析
    print("\n\n" + "="*100)
    print("🔥 当日上升最快的热点主题 TOP 5")
    print("="*100)
    
    for rank, (theme, today_score, rank_change, score_change) in enumerate(fastest_rising, 1):
        summary = theme_summary.get(theme, {})
        avg_10d = summary.get('avg_score_10d', 0)
        
        print(f"\n{'='*80}")
        print(f"第{rank}名: 【{theme}】")
        print(f"{'='*80}")
        print(f"  ⬆️ 排名变化: {rank_change:+d} 位 | 评分变化: {score_change:+.1f}")
        print(f"  今日评分: {today_score:.1f} | 近10日平均分: {avg_10d:.1f}")
        print(f"  趋势: {summary.get('score_trend', '未知')}")
        
        if theme in theme_leaders and theme_leaders[theme]:
            print(f"\n  🚀 当日最强龙头:")
            leader = theme_leaders[theme][0]
            ma_data = leader['ma_data']
            print(f"    {leader['name']:10s} ({leader['ts_code']:10s})")
            print(f"       总分:{leader['total_score']:.1f} | 5日:{leader['change_5']:+.1f}% | 20日:{leader['change_20']:+.1f}%")
            print(f"       {leader['score_details']['回落风险等级']} | {leader['score_details']['二波信号等级']}")
            
            print(f"\n  💡 主题成分股 TOP 3:")
            for i, stock in enumerate(theme_leaders[theme][:3], 1):
                print(f"    {i}. {stock['name']:10s} ({stock['ts_code']:10s})")
    
    save_final_results(ranked_themes, theme_leaders, theme_summary, trade_dates)
    
    # 额外输出复盘文本内容
    output_theme_analysis(ranked_themes, theme_summary, theme_leaders)
    output_leader_analysis(theme_leaders)
    output_tomorrow_recommendation(ranked_themes, theme_leaders, theme_summary)
    save_text_report(ranked_themes, theme_leaders, theme_summary, trade_dates, market_emotion, fastest_rising, long_term_potentials, short_term_potentials)
    
    return ranked_themes, theme_leaders, theme_summary

# =========================
# 保存结果
# =========================
def save_final_results(ranked_themes, theme_leaders, theme_summary, trade_dates):
    ranking_data = []
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        ranking_data.append({
            '排名': rank,
            '主题': theme,
            '今日评分': round(today_score, 2),
            '近10日平均分': round(summary.get('avg_score_10d', 0), 2),
            '近10日平均排名': round(summary.get('avg_rank_10d', 0), 1),
            '趋势': summary.get('score_trend', '未知'),
            '排名变化': summary.get('rank_change', 0)
        })
    
    ranking_df = pd.DataFrame(ranking_data)
    ranking_file = os.path.join(CACHE_DIR, f"theme_ranking_final_{trade_dates[-1]}.csv")
    ranking_df.to_csv(ranking_file, index=False, encoding='utf-8-sig')
    print(f"\n✓ 主题排名已保存: {ranking_file}")
    
    leaders_data = []
    for theme, leaders in theme_leaders.items():
        for leader in leaders:
            details = leader['score_details']
            ma_data = leader['ma_data']
            
            leaders_data.append({
                '主题': theme,
                '股票代码': leader['ts_code'],
                '股票名称': leader['name'],
                '综合评分': round(leader['total_score'], 2),
                '5日涨幅': round(leader['change_5'], 2),
                '20日涨幅': round(leader['change_20'], 2),
                '涨停次数': leader['limit_up_count'],
                '5日乖离率': round(ma_data['ma5_biased'], 2) if ma_data else 0,
                '20日乖离率': round(ma_data['ma20_biased'], 2) if ma_data else 0,
                '量比': round(leader['volume_ratio'], 2),
                '均线轮动得分': round(details['均线轮动'], 2),
                '冲高回落概率': round(details['冲高回落概率'], 0),
                '回落风险等级': details['回落风险等级'],
                '二波启动概率': round(details['二波启动概率'], 0),
                '二波信号等级': details['二波信号等级']
            })
    
    leaders_df = pd.DataFrame(leaders_data)
    leaders_file = os.path.join(CACHE_DIR, f"theme_leaders_final_{trade_dates[-1]}.csv")
    leaders_df.to_csv(leaders_file, index=False, encoding='utf-8-sig')
    print(f"✓ 龙头股列表已保存: {leaders_file}")

# =========================
# 微信推送功能
# =========================
def push_to_wechat(trade_date):
    """推送复盘报告到微信"""
    try:
        from push_review import push_daily_review
        
        report_file = os.path.join(CACHE_DIR, f"daily_review_{trade_date}.txt")
        
        if os.path.exists(report_file):
            print("\n\n" + "="*60)
            print("📱 开始推送微信通知")
            print("="*60)
            
            success = push_daily_review(report_file, trade_date)
            
            if success:
                print("✅ 微信推送成功！")
            else:
                print("⚠ 微信推送失败，请检查配置")
        else:
            print(f"\n⚠ 复盘报告不存在，跳过推送: {report_file}")
    except ImportError:
        print("\n⚠ 未找到 push_review 模块，跳过微信推送")
    except Exception as e:
        print(f"\n⚠ 微信推送异常: {e}")

# =========================
# 主函数
# =========================
def main():
    print("="*100)
    print("每日盘后复盘和热点轮动分析系统 (最终版)")
    print("主题排名 + 近10日平均分 + 调整后回升概率分析 + 大盘情绪分析")
    print("="*100)
    
    trade_dates = get_trade_dates(30)
    time.sleep(0.5)
    print(f"\n分析周期: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"共 {len(trade_dates)} 个交易日")
    
    # 获取大盘情绪分析
    print("\n" + "="*60)
    print("📊 大盘情绪分析")
    print("="*60)
    market_emotion = analyze_market_emotion_simple()
    if market_emotion:
        print(f"大盘情绪: {market_emotion.get('情绪指数', 'N/A')}")
        print(f"市场阶段: {market_emotion.get('市场阶段', 'N/A')}")
        print(f"涨停家数: {market_emotion.get('涨停家数', 'N/A')}")
    else:
        print("大盘情绪分析失败")
    
    theme_stocks_map, name_map = load_theme_portfolio_from_csv()
    if not theme_stocks_map:
        print("未获取到主题投资组合数据")
        return
    
    print(f"\n加载了 {len(theme_stocks_map)} 个主题")
    
    ranked_themes, theme_leaders, theme_summary = generate_report(
        theme_stocks_map, name_map, trade_dates, market_emotion
    )
    
    # 推送微信通知
    push_to_wechat(trade_dates[-1])
    
    print("\n\n" + "="*100)
    print("复盘分析完成！")
    print("="*100)

# =========================
# 单个交易日主题评分计算（用于回溯）
# =========================
def calculate_single_day_theme_scores(theme_stocks_map, trade_date):
    """
    仅计算和保存单个交易日的主题评分（不进行完整的复盘分析）
    
    Args:
        theme_stocks_map: 主题股票映射字典
        trade_date: 交易日期 (YYYYMMDD)
    """
    print(f"\n{'='*80}")
    print(f"📅 计算交易日: {trade_date} 的主题评分")
    print(f"{'='*80}")
    
    try:
        # 使用 calculate_today_market_scores 计算当日评分
        ranked_themes, theme_scores, theme_real_data = calculate_today_market_scores(
            theme_stocks_map, trade_date
        )
        
        # 构建一个简化的 theme_summary（仅用于数据库保存）
        theme_summary = {}
        for i, (theme, today_score) in enumerate(ranked_themes, 1):
            theme_summary[theme] = {
                'avg_score_10d': 0,
                'avg_rank_10d': 0,
                'score_trend': '未知',
                'rank_change': 0
            }
        
        # 保存到数据库
        db_manager.save_theme_scores(trade_date, ranked_themes, theme_summary)
        
        print(f"✅ 交易日 {trade_date} 主题评分计算完成")
        print(f"   共 {len(ranked_themes)} 个主题")
        if ranked_themes:
            print(f"   TOP 3: {', '.join([t for t, s in ranked_themes[:3]])}")
        
        return ranked_themes, theme_scores
        
    except Exception as e:
        print(f"❌ 计算交易日 {trade_date} 失败: {e}")
        import traceback
        traceback.print_exc()
        return None, None

# =========================
# 批量回溯过去N个交易日的主题评分
# =========================
def backfill_historical_theme_scores(days=10):
    """
    批量回溯过去N个交易日的主题评分
    
    Args:
        days: 需要回溯的天数，默认10天
    """
    print("\n" + "="*100)
    print("📊 批量回溯历史主题评分")
    print("="*100)
    
    # 获取交易日历
    trade_dates = get_trade_dates(30)
    if len(trade_dates) < days:
        print(f"⚠ 交易日历不足，仅获取 {len(trade_dates)} 个交易日")
        days = len(trade_dates)
    
    # 获取需要回溯的交易日（过去N天）
    target_dates = trade_dates[-days:] if len(trade_dates) >= days else trade_dates
    print(f"\n目标交易日: {target_dates[0]} ~ {target_dates[-1]}")
    print(f"共 {len(target_dates)} 个交易日")
    
    # 加载主题股票组合
    theme_stocks_map, name_map = load_theme_portfolio_from_csv()
    if not theme_stocks_map:
        print("❌ 未获取到主题投资组合数据")
        return
    
    print(f"\n加载了 {len(theme_stocks_map)} 个主题")
    
    # 检查数据库中已有的日期
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date')
    existing_dates = set([row[0] for row in cursor.fetchall()])
    conn.close()
    
    print(f"\n数据库中已有 {len(existing_dates)} 个交易日的评分")
    
    # 筛选需要计算的日期
    dates_to_calculate = [d for d in target_dates if d not in existing_dates]
    dates_to_skip = [d for d in target_dates if d in existing_dates]
    
    if dates_to_skip:
        print(f"\n跳过 {len(dates_to_skip)} 个已有评分的交易日:")
        for d in dates_to_skip[:5]:
            print(f"  - {d}")
        if len(dates_to_skip) > 5:
            print(f"  ... 还有 {len(dates_to_skip)-5} 个")
    
    if not dates_to_calculate:
        print("\n✅ 所有目标交易日的评分已存在，无需计算")
        return
    
    print(f"\n需要计算 {len(dates_to_calculate)} 个交易日:")
    for d in dates_to_calculate:
        print(f"  - {d}")
    
    # 开始计算
    success_count = 0
    fail_count = 0
    
    print(f"\n{'='*80}")
    print(f"开始计算...")
    print(f"{'='*80}")
    
    for i, trade_date in enumerate(dates_to_calculate, 1):
        print(f"\n[{i}/{len(dates_to_calculate)}] 计算 {trade_date}...")
        
        ranked_themes, theme_scores = calculate_single_day_theme_scores(
            theme_stocks_map, trade_date
        )
        
        if ranked_themes:
            success_count += 1
        else:
            fail_count += 1
        
        # 延迟，避免请求过快
        time.sleep(1)
    
    # 完成总结
    print(f"\n{'='*100}")
    print("📊 批量回溯完成总结")
    print("="*100)
    print(f"成功: {success_count} 个交易日")
    print(f"失败: {fail_count} 个交易日")
    print(f"跳过: {len(dates_to_skip)} 个交易日")
    print(f"总计: {len(target_dates)} 个交易日")
    
    # 显示数据库中的数据
    print(f"\n{'='*100}")
    print("📋 数据库中主题评分概览")
    print(f"{'='*100}")
    display_theme_scores_from_db(target_dates)

# =========================
# 显示数据库中的主题评分
# =========================
def display_theme_scores_from_db(dates=None):
    """
    显示数据库中的主题评分
    
    Args:
        dates: 指定日期列表，None表示显示所有日期
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            'SELECT trade_date, theme_name, today_score FROM theme_scores ORDER BY trade_date DESC, today_score DESC',
            conn
        )
        conn.close()
        
        if dates:
            df = df[df['trade_date'].isin(dates)]
        
        if df.empty:
            print("数据库中暂无主题评分数据")
            return
        
        print(f"\n共 {df['trade_date'].nunique()} 个交易日的数据:")
        
        # 按日期分组显示
        for trade_date in sorted(df['trade_date'].unique(), reverse=True):
            day_data = df[df['trade_date'] == trade_date]
            top3 = day_data.nlargest(3, 'today_score')
            
            print(f"\n{trade_date}:")
            for idx, row in enumerate(top3.itertuples(), 1):
                print(f"  {idx}. {row.theme_name}: {row.today_score:.1f}")
            if len(day_data) > 3:
                print(f"  ... 共 {len(day_data)} 个主题")
        
        print(f"\n✅ 数据库中现有数据可用于主题历史趋势分析！")
        
    except Exception as e:
        print(f"查询数据库失败: {e}")

# =========================
# 主函数
# =========================
def main():
    print("="*100)
    print("每日盘后复盘和热点轮动分析系统 (最终版)")
    print("主题排名 + 近10日平均分 + 调整后回升概率分析 + 大盘情绪分析")
    print("="*100)
    
    trade_dates = get_trade_dates(30)
    time.sleep(0.5)
    print(f"\n分析周期: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"共 {len(trade_dates)} 个交易日")
    
    # 获取大盘情绪分析
    print("\n" + "="*60)
    print("📊 大盘情绪分析")
    print("="*60)
    market_emotion = analyze_market_emotion_simple()
    if market_emotion:
        print(f"大盘情绪: {market_emotion.get('情绪指数', 'N/A')}")
        print(f"市场阶段: {market_emotion.get('市场阶段', 'N/A')}")
        print(f"涨停家数: {market_emotion.get('涨停家数', 'N/A')}")
    else:
        print("大盘情绪分析失败")
    
    theme_stocks_map, name_map = load_theme_portfolio_from_csv()
    if not theme_stocks_map:
        print("未获取到主题投资组合数据")
        return
    
    print(f"\n加载了 {len(theme_stocks_map)} 个主题")
    
    ranked_themes, theme_leaders, theme_summary = generate_report(
        theme_stocks_map, name_map, trade_dates, market_emotion
    )
    
    # 推送微信通知
    push_to_wechat(trade_dates[-1])
    
    print("\n\n" + "="*100)
    print("复盘分析完成！")
    print("="*100)

if __name__ == "__main__":
    import sys
    
    # 检查是否有命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == 'backfill':
        # 批量回溯模式
        days = 10
        if len(sys.argv) > 2 and sys.argv[2].isdigit():
            days = int(sys.argv[2])
        backfill_historical_theme_scores(days)
    elif len(sys.argv) > 1 and len(sys.argv[1]) == 8 and sys.argv[1].isdigit():
        # 指定日期运行模式
        target_date = sys.argv[1]
        print(f"指定日期运行: {target_date}")
        
        # 调用原始流程，但需要确保日期正确
        trade_dates = get_trade_dates(30)
        if target_date not in trade_dates:
            print(f"警告: {target_date} 不在交易日历中")
        
        main()
    else:
        # 正常模式（运行最新交易日）
        main()
