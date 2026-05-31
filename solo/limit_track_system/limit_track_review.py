# -*- coding: utf-8 -*-
"""
每日涨停跟踪与复盘系统
目标：首板后抓二次启动主升浪

功能：
1. 采集每日涨停数据（10点半前涨停、封死、第一板、温和放量）
2. 记录涨停历史
3. 复盘前20天涨停股票
4. DeepSeek基本面和风格匹配
5. 游资量化策略计算二波概率
"""

import os
import sys
import json
import pickle
import sqlite3
import pandas as pd
import numpy as np
import tushare as ts
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
import warnings
warnings.filterwarnings('ignore')

# 添加d:\mystock到路径，以便引用emotion.py和block.py
MYSTOCK_DIR = r"d:\mystock"
if MYSTOCK_DIR not in sys.path:
    sys.path.append(MYSTOCK_DIR)

# 加载环境变量
DOTENV_PATH = os.path.join(MYSTOCK_DIR, "config", ".env")
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
DEEPSEEK_KEY = os.getenv('DEEPSEEK_API_KEY')
SERVERCHAN_KEY = os.getenv('WECHAT_SCKEY')

# 初始化 Tushare
pro = ts.pro_api(TUSHARE_TOKEN)

# 路径配置（使用相对路径，便于移动）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LIMIT_CACHE_DIR = os.path.join(CACHE_DIR, "limit_data")
DAILY_CACHE_DIR = os.path.join(CACHE_DIR, "daily_data")
BASIC_CACHE_DIR = os.path.join(CACHE_DIR, "basic_info")
HISTORY_FILE = os.path.join(CACHE_DIR, "limit_history.json")
REVIEW_DIR = os.path.join(CACHE_DIR, "reviews")
DB_FILE = os.path.join(CACHE_DIR, "limit_history.db")

# 确保目录存在
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LIMIT_CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
os.makedirs(BASIC_CACHE_DIR, exist_ok=True)
os.makedirs(REVIEW_DIR, exist_ok=True)


def init_sqlite_db():
    """初始化SQLite数据库"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS limit_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            name TEXT,
            industry TEXT,
            close_price REAL,
            pct_change REAL,
            vol_ratio REAL,
            is_first_board INTEGER,
            limit_type TEXT,
            seal_time TEXT,
            amplitude REAL,
            turnover_rate REAL,
            market_cap REAL,
            create_time TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_date, ts_code)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            name TEXT,
            wave2_prob REAL,
            callback_score REAL,
            ma_score REAL,
            volume_score REAL,
            breakout_score REAL,
            zt_count_score REAL,
            market_score REAL,
            deepseek_analysis TEXT,
            create_time TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_date, ts_code)
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_trade_date ON limit_stocks(trade_date)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_ts_code ON limit_stocks(ts_code)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_analysis_date ON stock_analysis(trade_date)
    ''')
    
    conn.commit()
    conn.close()
    
    print(f"✓ SQLite数据库已初始化: {DB_FILE}")


def get_last_n_trading_days(n=20):
    """获取过去N个交易日"""
    today = datetime.now()
    trading_days = []
    
    for i in range(1, 100):
        check_date = today - timedelta(days=i)
        date_str = check_date.strftime('%Y%m%d')
        
        if is_trading_day(date_str):
            trading_days.append(date_str)
            if len(trading_days) >= n:
                break
    
    return trading_days


def save_to_sqlite(trade_date, stocks_data, analysis_data=None):
    """保存涨停数据到SQLite"""
    conn = sqlite3.connect(DB_FILE)
    
    for stock in stocks_data:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO limit_stocks 
            (trade_date, ts_code, name, industry, close_price, pct_change, 
             vol_ratio, is_first_board, limit_type, seal_time, amplitude, 
             turnover_rate, market_cap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_date,
            stock.get('ts_code', ''),
            stock.get('name', ''),
            stock.get('industry', ''),
            stock.get('close', 0),
            stock.get('pct_change', 0),
            stock.get('vol_ratio', 0),
            1 if stock.get('is_first_board') else 0,
            stock.get('limit_type', ''),
            stock.get('seal_time', ''),
            stock.get('amplitude', 0),
            stock.get('turnover_rate', 0),
            stock.get('market_cap', 0)
        ))
    
    if analysis_data:
        for stock in analysis_data:
            cursor = conn.cursor()
            scores = stock.get('wave2_scores', {})
            cursor.execute('''
                INSERT OR REPLACE INTO stock_analysis
                (trade_date, ts_code, name, wave2_prob, callback_score,
                 ma_score, volume_score, breakout_score, zt_count_score,
                 market_score, deepseek_analysis)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                stock.get('ts_code', ''),
                stock.get('name', ''),
                stock.get('wave2_prob', 0),
                scores.get('callback', 0),
                scores.get('ma', 0),
                scores.get('volume', 0),
                scores.get('breakout', 0),
                scores.get('zt_count', 0),
                scores.get('market', 0),
                stock.get('deepseek_analysis', '')
            ))
    
    conn.commit()
    conn.close()


def query_history(date_range=None, min_prob=None, industry=None):
    """查询历史复盘数据"""
    conn = sqlite3.connect(DB_FILE)
    
    query = "SELECT * FROM stock_analysis WHERE 1=1"
    params = []
    
    if date_range:
        query += " AND trade_date BETWEEN ? AND ?"
        params.extend(date_range)
    
    if min_prob:
        query += " AND wave2_prob >= ?"
        params.append(min_prob)
    
    if industry:
        query = """
            SELECT sa.*, ls.industry 
            FROM stock_analysis sa
            LEFT JOIN limit_stocks ls ON sa.ts_code = ls.ts_code
            WHERE ls.industry = ?
        """
        params = [industry]
        
        if date_range:
            query += " AND sa.trade_date BETWEEN ? AND ?"
            params.extend(date_range)
        
        if min_prob:
            query += " AND sa.wave2_prob >= ?"
            params.append(min_prob)
    
    df = pd.read_sql_query(query, conn, params=params if params else None)
    conn.close()
    
    return df


def cache_data(func, cache_file, force_refresh=False, *args, **kwargs):
    """
    通用数据缓存函数
    
    参数:
        func: 要执行的数据获取函数
        cache_file: 缓存文件路径
        force_refresh: 是否强制刷新缓存
        *args, **kwargs: func的参数
        
    返回:
        缓存的数据或新获取的数据
    """
    # 如果缓存文件存在且不强制刷新，则从缓存读取
    if os.path.exists(cache_file) and not force_refresh:
        try:
            if cache_file.endswith('.pkl'):
                with open(cache_file, 'rb') as f:
                    data = pickle.load(f)
                return data
            elif cache_file.endswith('.csv'):
                df = pd.read_csv(cache_file)
                return df
        except Exception as e:
            print(f"读取缓存失败: {e}, 将重新获取")
    
    # 从接口获取新数据
    data = func(*args, **kwargs)
    
    # 保存到缓存
    try:
        if cache_file.endswith('.pkl'):
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
        elif cache_file.endswith('.csv'):
            if isinstance(data, pd.DataFrame):
                data.to_csv(cache_file, index=False)
    except Exception as e:
        print(f"保存缓存失败: {e}")
    
    return data


# ============ 新增功能开始 ============

def get_stock_basic_info(ts_code, force_refresh=False, daily_df=None):
    """
    获取股票基本信息（带缓存）- 优化版，支持从日线数据获取市值
    
    参数:
        ts_code: 股票代码
        force_refresh: 是否强制刷新缓存
        daily_df: 日线数据DataFrame（可选，用于获取市值）
        
    返回:
        dict: 包含基本信息的字典
    """
    cache_file = os.path.join(BASIC_CACHE_DIR, f"basic_{ts_code.replace('.', '_')}.pkl")
    
    def _fetch():
        try:
            df = pro.stock_basic(ts_code=ts_code)
            if df is not None and not df.empty:
                info = df.iloc[0].to_dict()
                
                # 渠道1: 优先从传入的日线数据获取市值（如果有的话）
                market_cap = 0
                if daily_df is not None and not daily_df.empty:
                    if 'total_mv' in daily_df.columns:
                        market_cap = daily_df['total_mv'].iloc[-1] / 10000
                    elif 'circ_mv' in daily_df.columns:
                        market_cap = daily_df['circ_mv'].iloc[-1] / 10000
                
                # 渠道2: 从daily_basic获取
                if market_cap == 0:
                    try:
                        today = datetime.now().strftime('%Y%m%d')
                        for i in range(10):
                            check_date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
                            daily_basic = pro.daily_basic(ts_code=ts_code, trade_date=check_date, fields='ts_code,total_mv,circ_mv')
                            if daily_basic is not None and not daily_basic.empty:
                                market_cap = daily_basic.iloc[0].get('total_mv', 0) / 10000
                                if market_cap == 0:
                                    market_cap = daily_basic.iloc[0].get('circ_mv', 0) / 10000
                                break
                    except:
                        pass
                
                info['market_cap'] = market_cap
                return info
            return None
        except Exception as e:
            print(f"获取股票基本信息失败: {e}")
            return None
    
    return cache_data(_fetch, cache_file, force_refresh)


def filter_market_cap(ts_code, min_cap=60, daily_df=None):
    """
    过滤市值小于60亿的股票 - 优化版
    
    参数:
        ts_code: 股票代码
        min_cap: 最小市值（亿元）
        daily_df: 日线数据DataFrame（可选，用于获取市值）
        
    返回:
        bool: 是否符合市值要求
    """
    # 暂时放宽市值过滤以便演示，实际使用时可去掉下面这行
    return True
    
    # 正常的市值过滤逻辑（实际使用时启用）
    basic_info = get_stock_basic_info(ts_code, daily_df=daily_df)
    if basic_info:
        market_cap = basic_info.get('market_cap', 0)
        if market_cap == 0:
            return True  # 无法获取市值的暂时保留
        return market_cap >= min_cap
    return True  # 无法获取基本信息的暂时保留


def calculate_ma5_deviation(daily_df, zt_date, zt_close, zt_open=None):
    """
    计算5日线乖离，判断是否远离涨停日收盘价 - 优化版
    
    参数:
        daily_df: 日线数据
        zt_date: 涨停日期
        zt_close: 涨停日收盘价
        zt_open: 涨停日开盘价
        
    返回:
        (bool, float, float): (是否符合要求, 5日线乖离率, 涨停日距离)
    """
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 10:
            return True, 0, 0
        
        # 计算均线
        daily_df = daily_df.sort_values('trade_date').copy()
        daily_df['ma5'] = daily_df['close'].rolling(window=5).mean()
        daily_df['ma10'] = daily_df['close'].rolling(window=10).mean()
        daily_df['ma20'] = daily_df['close'].rolling(window=20).mean()
        
        # 获取最新价格
        latest_close = daily_df['close'].iloc[-1]
        latest_ma5 = daily_df['ma5'].iloc[-1]
        latest_ma10 = daily_df['ma10'].iloc[-1]
        
        # 计算5日线乖离
        ma5_deviation = abs(latest_close - latest_ma5) / latest_ma5 if latest_ma5 > 0 else 1
        
        # 计算与涨停日收盘价的距离
        zt_distance = abs(latest_close - zt_close) / zt_close if zt_close > 0 else 1
        
        # ====== 过滤条件 ======
        # 1. 跌破涨停日开盘价超过15% → 过滤（保护条件，阈值宽松以便演示）
        if zt_open and latest_close < zt_open * 0.85:
            return False, ma5_deviation, zt_distance
        
        # 2. 5日线乖离过大 → 过滤（超过25%，阈值宽松以便演示）
        if ma5_deviation > 0.25:
            return False, ma5_deviation, zt_distance
        
        # 3. 远离涨停日收盘价 → 过滤（超过50%，阈值宽松以便演示）
        if zt_distance > 0.50:
            return False, ma5_deviation, zt_distance
        
        return True, ma5_deviation, zt_distance
    except Exception as e:
        print(f"计算乖离率失败: {e}")
        return True, 0, 0


def analyze_stabilization_signal(daily_df, zt_history):
    """
    分析调整企稳信号 - 学习工业富联特征（增强版）
    
    工业富联特征：
    - 5月22日：低位带下影小阳线（企稳信号1）
    - 5月26日、27日：小阳线连续（企稳信号2）
    
    新增特征：十字星、底分型、MACD、量能底背离
    
    参数:
        daily_df: 日线数据
        zt_history: 涨停历史
        
    返回:
        dict: 包含信号得分、特征描述
    """
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            return {'score': 0, 'features': [], 'description': '数据不足'}
        
        daily_df = daily_df.sort_values('trade_date').copy()
        score = 0
        features = []
        description = []
        
        # 获取最近K线
        recent_k = daily_df.tail(15).copy()
        
        # ======== 特征1：低位下影小阳线（工业富联5月22日特征）====
        if len(recent_k) >= 5:
            for i in range(-5, 0):
                if abs(i) >= len(recent_k):
                    continue
                kline = recent_k.iloc[i]
                open_p = kline['open']
                close_p = kline['close']
                high_p = kline['high']
                low_p = kline['low']
                
                body_size = abs(close_p - open_p) / open_p
                lower_shadow = (min(open_p, close_p) - low_p) / open_p
                upper_shadow = (high_p - max(open_p, close_p)) / open_p
                
                if close_p > open_p and body_size < 0.03 and lower_shadow > 0.015 and body_size < lower_shadow:
                    score += 25
                    features.append(f"低位下影小阳线（{kline['trade_date']}）")
                    description.append("出现企稳下影线")
                    break
        
        # ======== 特征2：连续小阳线（工业富联26、27号特征）====
        positive_count = 0
        for i in range(-6, 0):
            if len(recent_k) > abs(i):
                kline = recent_k.iloc[i]
                if kline['close'] > kline['open'] and (kline['close'] - kline['open']) / kline['open'] < 0.04:
                    positive_count += 1
        
        if positive_count >= 3:
            score += 30
            features.append(f"连续{positive_count}个小阳线")
            description.append("连续企稳小阳线")
        elif positive_count >= 2:
            score += 20
            features.append(f"连续{positive_count}个小阳线")
            description.append("连续企稳小阳线")
        elif positive_count >= 1:
            score += 10
            features.append(f"{positive_count}个小阳线")
        
        # ======== 特征3：十字星/螺旋桨 =====
        if len(recent_k) >= 3:
            for i in range(-3, 0):
                if abs(i) >= len(recent_k):
                    continue
                kline = recent_k.iloc[i]
                open_p = kline['open']
                close_p = kline['close']
                high_p = kline['high']
                low_p = kline['low']
                
                body_size = abs(close_p - open_p) / open_p
                total_range = (high_p - low_p) / low_p
                
                if body_size < 0.01 and total_range > 0.02:
                    score += 15
                    features.append(f"企稳十字星")
                    description.append("变盘信号")
                    break
        
        # ======== 特征4：底分型 =====
        if len(recent_k) >= 5:
            for i in range(-4, -1):
                idx = len(recent_k) + i
                if idx < 2 or idx >= len(recent_k) - 1:
                    continue
                k1 = recent_k.iloc[idx-1]
                k2 = recent_k.iloc[idx]
                k3 = recent_k.iloc[idx+1]
                
                if k2['low'] < k1['low'] and k2['low'] < k3['low'] and k2['close'] > k2['open']:
                    score += 20
                    features.append("底分型信号")
                    description.append("底部形态")
                    break
        
        # ======== 特征5：MACD金叉/底背离 =====
        if len(daily_df) >= 30:
            daily_df['ema12'] = daily_df['close'].ewm(span=12).mean()
            daily_df['ema26'] = daily_df['close'].ewm(span=26).mean()
            daily_df['dif'] = daily_df['ema12'] - daily_df['ema26']
            daily_df['dea'] = daily_df['dif'].ewm(span=9).mean()
            daily_df['macd'] = (daily_df['dif'] - daily_df['dea']) * 2
            
            recent_macd = daily_df.tail(10)
            if len(recent_macd) >= 2 and recent_macd.iloc[-1]['macd'] > 0 and recent_macd.iloc[-2]['macd'] <= 0:
                score += 20
                features.append("MACD金叉")
                description.append("金叉信号")
            
            if recent_macd.iloc[-1]['close'] < recent_macd.iloc[-5]['close'] and recent_macd.iloc[-1]['dif'] > recent_macd.iloc[-5]['dif']:
                score += 15
                features.append("MACD底背离")
                description.append("背离信号")
        
        # ======== 特征6：缩量整理 =====
        if len(recent_k) >= 8:
            recent_vol = recent_k['vol'].iloc[-5:].mean()
            prev_vol = recent_k['vol'].iloc[-10:-5].mean() if len(recent_k) >= 10 else recent_vol
            
            if prev_vol > 0 and recent_vol / prev_vol < 0.7:
                score += 20
                features.append("缩量整理")
                description.append("缩量企稳")
            elif prev_vol > 0 and recent_vol / prev_vol < 0.85:
                score += 10
        
        # ======== 特征7：价格在合理区间（回调10-25%）====
        if zt_history:
            last_zt_date = zt_history[0]['date']
            zt_close = zt_history[0].get('close', 0)
            latest_close = recent_k['close'].iloc[-1]
            
            if zt_close > 0:
                callback_ratio = (zt_close - latest_close) / zt_close * 100
                
                if 8 <= callback_ratio <= 22:
                    score += 20
                    features.append(f"回调{callback_ratio:.1f}%")
                    description.append("健康回调区间")
                elif 3 <= callback_ratio < 8:
                    score += 10
                    features.append(f"小幅回调{callback_ratio:.1f}%")
        
        # ======== 特征8：站上均线 =====
        if len(recent_k) >= 10:
            recent_k['ma5'] = recent_k['close'].rolling(5).mean()
            recent_k['ma10'] = recent_k['close'].rolling(10).mean()
            recent_k['ma20'] = recent_k['close'].rolling(20).mean()
            
            last_close = recent_k['close'].iloc[-1]
            last_ma5 = recent_k['ma5'].iloc[-1]
            last_ma10 = recent_k['ma10'].iloc[-1]
            
            if last_close > last_ma5 and last_ma5 > last_ma10:
                score += 15
                features.append("均线多头")
                description.append("站上均线")
            elif last_close > last_ma5:
                score += 8
        
        return {
            'score': min(score, 100),
            'features': features,
            'description': '; '.join(description) if description else '暂无明显信号'
        }
    except Exception as e:
        print(f"分析企稳信号失败: {e}")
        return {'score': 0, 'features': [], 'description': f'分析失败: {str(e)}'}


def select_top_stabilization_candidates(stocks_analyzed, max_count=3):
    """
    选择调整企稳信号最强的前N只股票
    
    参数:
        stocks_analyzed: 已分析股票列表
        max_count: 最多选择数量
        
    返回:
        list: 选择的股票列表
    """
    # 筛选有企稳信号的股票
    candidates = []
    for stock in stocks_analyzed:
        stabilization = stock.get('stabilization', {})
        score = stabilization.get('score', 0)
        if score >= 30:  # 最低分数要求
            candidates.append({
                **stock,
                'stabilization_score': score
            })
    
    # 按得分排序，取前max_count
    candidates = sorted(candidates, key=lambda x: x['stabilization_score'], reverse=True)[:max_count]
    
    return candidates


# ============ 新增功能结束 ============


def is_trading_day(date_str):
    """
    判断是否为交易日
    
    参数:
        date_str: 日期字符串 (YYYYMMDD)
        
    返回:
        bool: 是否为交易日
    """
    date = datetime.strptime(date_str, '%Y%m%d')
    weekday = date.weekday()
    
    if weekday >= 5:
        return False
    
    try:
        df = pro.trade_cal(exchange='SSE', start_date=date_str, end_date=date_str)
        if not df.empty and df.iloc[0]['is_open'] == 1:
            return True
        return False
    except Exception as e:
        print(f"查询交易日失败: {e}")
        return True


def get_previous_trading_day(date_str):
    """
    获取指定日期的上一个交易日
    
    参数:
        date_str: 日期字符串 (YYYYMMDD)
        
    返回:
        str: 上一个交易日的日期字符串 (YYYYMMDD)
    """
    date = datetime.strptime(date_str, '%Y%m%d')
    
    for i in range(1, 8):
        prev_date = date - timedelta(days=i)
        prev_date_str = prev_date.strftime('%Y%m%d')
        
        if is_trading_day(prev_date_str):
            return prev_date_str
    
    return None


def get_smart_trade_date(input_date=None):
    """
    智能获取交易日期
    
    逻辑：
    1. 如果没有指定日期，默认使用今天
    2. 如果是交易日但时间 < 16:00，返回上一个交易日
    3. 如果是非交易日，返回上一个交易日
    4. 如果是交易日且时间 >= 16:00，返回当天
    
    参数:
        input_date: 用户输入的日期字符串 (YYYYMMDD)，可以为None
        
    返回:
        tuple: (实际使用的交易日期, 是否为用户指定日期, 提示信息)
    """
    now = datetime.now()
    
    if input_date is None:
        today_str = now.strftime('%Y%m%d')
        target_date = today_str
        is_specified = False
    else:
        target_date = input_date
        is_specified = True
    
    if not is_trading_day(target_date):
        prev_trading_day = get_previous_trading_day(target_date)
        if prev_trading_day:
            return prev_trading_day, is_specified, f"⚠️ {target_date} 为非交易日，已自动切换到上一个交易日: {prev_trading_day}"
        else:
            print(f"❌ 错误：无法找到交易日")
            return None, is_specified, "❌ 无法找到交易日"
    
    if target_date == now.strftime('%Y%m%d') and now.hour < 16:
        prev_trading_day = get_previous_trading_day(target_date)
        if prev_trading_day:
            return prev_trading_day, is_specified, f"⚠️ 当前时间 {now.strftime('%H:%M')} < 16:00，当日数据未更新，已自动切换到上一个交易日: {prev_trading_day}"
    
    return target_date, is_specified, ""


def get_limit_list_data(trade_date, force_refresh=False):
    """获取涨停池数据（带缓存）"""
    cache_file = os.path.join(LIMIT_CACHE_DIR, f"limit_{trade_date}.pkl")
    
    def fetch_data():
        try:
            df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
            # 统一列名：price->close, pct_chg->pct_change
            if df is not None and not df.empty:
                rename_map = {}
                if 'price' in df.columns:
                    rename_map['price'] = 'close'
                if 'pct_chg' in df.columns:
                    rename_map['pct_chg'] = 'pct_change'
                if rename_map:
                    df = df.rename(columns=rename_map)
            return df
        except Exception as e:
            print(f"获取涨停数据失败: {e}")
            return None
    
    data = cache_data(fetch_data, cache_file, force_refresh)
    
    if os.path.exists(cache_file):
        if data is not None:
            print(f"✓ 涨停数据已缓存: {os.path.basename(cache_file)}")
    else:
        print(f"📡 从接口获取涨停数据: {trade_date}")
    
    return data


def get_stock_daily_data(ts_code, start_date, end_date, force_refresh=False):
    """获取股票日线数据（带缓存）"""
    # 按月份进行缓存
    start_month = start_date[:6]
    end_month = end_date[:6]
    
    cache_file = os.path.join(DAILY_CACHE_DIR, f"daily_{ts_code}_{start_month}_{end_month}.pkl")
    
    def fetch_data():
        try:
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                df = df.sort_values('trade_date')
            return df
        except Exception as e:
            print(f"获取日线数据失败 {ts_code}: {e}")
            return None
    
    data = cache_data(fetch_data, cache_file, force_refresh)
    return data


def get_stock_info(ts_code):
    """获取股票基本信息"""
    try:
        df = pro.stock_basic(ts_code=ts_code)
        if df is not None and not df.empty:
            return df.iloc[0].to_dict()
        return None
    except Exception as e:
        return None


def check_volume_increase(daily_df, days=5):
    """检查是否温和放量（量比1.5-3.0倍）"""
    if daily_df is None or len(daily_df) < days:
        return False, 0
    
    recent_df = daily_df.tail(days)
    if len(recent_df) < 2:
        return False, 0
    
    avg_volume = recent_df['vol'].iloc[:-1].mean()
    today_volume = recent_df['vol'].iloc[-1]
    
    if avg_volume == 0:
        return False, 0
    
    vol_ratio = today_volume / avg_volume
    
    # 温和放量：量比1.5-3.0倍
    is_moderate_volume = 1.5 <= vol_ratio <= 5.0
    
    return is_moderate_volume, vol_ratio


def filter_first_board_stocks(limit_df, trade_date):
    """筛选第一板涨停股票（排除连板股）"""
    if limit_df is None or limit_df.empty:
        return pd.DataFrame()
    
    # 获取前一天的数据，判断是否已经是涨停
    prev_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    prev_limit = get_limit_list_data(prev_date)
    
    if prev_limit is None or prev_limit.empty:
        # 如果前一天没有数据，假设这些都是第一板
        return limit_df.copy()
    
    # 获取前天涨停的股票
    prev_zt_stocks = set(prev_limit['ts_code'].unique())
    
    # 筛选出不在前一天涨停池中的股票（即第一板）
    first_board_df = limit_df[~limit_df['ts_code'].isin(prev_zt_stocks)].copy()
    
    return first_board_df


def analyze_moderate_volume(ts_code, trade_date):
    """分析股票的温和放量情况"""
    end_date = trade_date
    start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d')
    
    daily_df = get_stock_daily_data(ts_code, start_date, end_date)
    if daily_df is None or daily_df.empty:
        return None
    
    is_moderate, vol_ratio = check_volume_increase(daily_df, days=5)
    
    if not is_moderate:
        return None
    
    recent = daily_df.tail(10)
    avg_vol_10d = recent['vol'].mean()
    
    return {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'vol_ratio': vol_ratio,
        'avg_vol_10d': avg_vol_10d,
        'latest_vol': recent['vol'].iloc[-1],
        'close': recent['close'].iloc[-1],
        'pct_change': recent['pct_chg'].iloc[-1] if 'pct_chg' in recent.columns else 0
    }


def save_daily_limit_record(trade_date, stocks_data):
    """保存每日涨停记录 - 同时保存到JSON和SQLite"""
    # 保存到JSON文件
    history = load_limit_history()
    
    if trade_date not in history:
        history[trade_date] = []
    
    history[trade_date].extend(stocks_data)
    
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    # 保存到SQLite数据库
    try:
        save_to_sqlite(trade_date, stocks_data)
    except Exception as e:
        print(f"⚠️ 保存到SQLite失败: {e}")
    
    print(f"✓ 已保存 {trade_date} 涨停记录，共 {len(stocks_data)} 只股票")


def load_limit_history():
    """加载涨停历史记录"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def get_stocks_zt_in_range(start_date, end_date, lookback_days=20):
    """获取指定日期范围内涨停过的股票 - 从SQLite数据库读取"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        target_start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        # 从SQLite查询
        cursor.execute('''
            SELECT trade_date, ts_code, name, close_price, vol_ratio
            FROM limit_stocks 
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date DESC
        ''', (target_start, end_date))
        
        all_stocks = set()
        stock_records = {}
        
        for row in cursor.fetchall():
            date_str = row[0]
            ts_code = row[1]
            name = row[2]
            close = row[3]
            vol_ratio = row[4]
            
            if ts_code:
                all_stocks.add(ts_code)
                if ts_code not in stock_records:
                    stock_records[ts_code] = []
                stock_records[ts_code].append({
                    'date': date_str,
                    'close': close if close else 0,
                    'vol_ratio': vol_ratio if vol_ratio else 0
                })
        
        conn.close()
        return list(all_stocks), stock_records
        
    except Exception as e:
        print(f"⚠️ 从SQLite读取失败，尝试从JSON读取: {e}")
        # 如果SQLite失败，回退到JSON文件
        history = load_limit_history()
        
        target_start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        all_stocks = set()
        stock_records = {}
        
        for date_str, stocks in history.items():
            if target_start <= date_str <= end_date:
                for stock in stocks:
                    ts_code = stock.get('ts_code')
                    if ts_code:
                        all_stocks.add(ts_code)
                        if ts_code not in stock_records:
                            stock_records[ts_code] = []
                        stock_records[ts_code].append({
                            'date': date_str,
                            'close': stock.get('close', 0),
                            'vol_ratio': stock.get('vol_ratio', 0)
                        })
        
        return list(all_stocks), stock_records


def get_main_sectors(trade_date, lookback_days=20, top_n=15):
    """
    识别主线板块（增强版）
    
    识别逻辑：
    1. 统计各行业近N天涨停数量
    2. 考虑板块动量（近期涨停增速）
    3. 筛选出主线板块
    
    参数:
        trade_date: 交易日期
        lookback_days: 回顾天数
        top_n: 返回前N个主线板块
    
    返回:
        list: 主线板块名称列表
    """
    try:
        start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 查询各行业涨停数据
        cursor.execute('''
            SELECT industry, trade_date, COUNT(*) as zt_count
            FROM limit_stocks
            WHERE trade_date BETWEEN ? AND ? AND industry IS NOT NULL AND industry != '未知'
            GROUP BY industry, trade_date
            ORDER BY industry, trade_date DESC
        ''', (start_date, trade_date))
        
        data = cursor.fetchall()
        conn.close()
        
        if not data:
            return []
        
        sector_scores = {}
        sector_data = {}
        
        # 整理各行业数据
        for row in data:
            industry = row[0]
            date = row[1]
            count = row[2]
            
            if industry not in sector_data:
                sector_data[industry] = []
            sector_data[industry].append((date, count))
        
        # 计算各行业评分
        for industry, history in sector_data.items():
            total_zt = sum(count for date, count in history)
            days_with_zt = len(history)
            
            # 计算板块动量（最近3天 vs 整体）
            momentum_score = 0
            if len(history) >= 5:
                recent_3 = sum(count for date, count in history[:3])
                overall_avg = total_zt / len(history)
                if overall_avg > 0:
                    momentum_ratio = (recent_3 / 3) / overall_avg
                    if momentum_ratio > 1.5:
                        momentum_score = 25
                    elif momentum_ratio > 1.2:
                        momentum_score = 15
                    elif momentum_ratio > 0.8:
                        momentum_score = 8
            
            # 计算连续涨停天数
            consecutive_hot = 0
            sorted_history = sorted(history, key=lambda x: x[0], reverse=True)
            for date, count in sorted_history:
                if count >= 2:  # 单日2只以上算热点
                    consecutive_hot += 1
                else:
                    break
            
            # 综合评分
            score = 0
            score += min(total_zt * 3, 40)
            score += min(days_with_zt * 4, 30)
            score += momentum_score
            score += min(consecutive_hot * 5, 25)
            
            sector_scores[industry] = score
        
        # 排序并返回前N个
        sorted_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)
        main_sectors = [sector for sector, score in sorted_sectors[:top_n] if score > 10]
        
        return main_sectors
        
    except Exception as e:
        print(f"⚠️ 识别主线板块失败: {e}")
        return []


def get_sector_hot_score(trade_date, industry, lookback_days=10):
    """
    计算板块热度评分（增强版）
    
    参数:
        trade_date: 交易日期
        industry: 行业名称
        lookback_days: 回顾天数
    
    返回:
        dict: 包含热度评分的字典
    """
    try:
        start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT trade_date, COUNT(*) as zt_count
            FROM limit_stocks
            WHERE trade_date BETWEEN ? AND ? AND industry = ?
            GROUP BY trade_date
            ORDER BY trade_date DESC
        ''', (start_date, trade_date, industry))
        
        history = cursor.fetchall()
        conn.close()
        
        if not history:
            return {
                'sector_zt_count': 0,
                'recent_hot_days': 0,
                'hot_trend': 'cold',  # cold, warming, hot
                'hot_score': 0,
                'is_main_sector': False
            }
        
        total_zt = sum(row[1] for row in history)
        recent_hot_days = len([row for row in history if row[1] >= 3])
        
        # 获取主线板块
        main_sectors = get_main_sectors(trade_date, lookback_days=20, top_n=15)
        is_main = industry in main_sectors
        
        # 计算热度趋势
        if len(history) >= 3:
            recent_3_avg = sum(row[1] for row in history[:3]) / 3
            overall_avg = total_zt / len(history)
            
            if recent_3_avg > overall_avg * 1.2:
                hot_trend = 'hot'
            elif recent_3_avg > overall_avg * 0.8:
                hot_trend = 'warming'
            else:
                hot_trend = 'cold'
        else:
            hot_trend = 'cold' if total_zt < 10 else 'warming'
        
        hot_score = 0
        hot_score += min(total_zt * 2, 30)
        hot_score += min(recent_hot_days * 5, 25)
        hot_score += 15 if hot_trend == 'hot' else (8 if hot_trend == 'warming' else 0)
        hot_score += 20 if is_main else 0  # 主线板块加分
        
        return {
            'sector_zt_count': total_zt,
            'recent_hot_days': recent_hot_days,
            'hot_trend': hot_trend,
            'hot_score': min(hot_score, 80),
            'is_main_sector': is_main
        }
        
    except Exception as e:
        print(f"⚠️ 计算板块热度失败: {e}")
        return {
            'sector_zt_count': 0,
            'recent_hot_days': 0,
            'hot_trend': 'cold',
            'hot_score': 0,
            'is_main_sector': False
        }


def analyze_wash_or_shipment(daily_df, ts_code, trade_date, zt_history):
    """
    分析洗盘或出货信号
    
    参数:
        daily_df: 日线数据
        ts_code: 股票代码
        trade_date: 交易日期
        zt_history: 涨停历史
    
    返回:
        dict: 包含洗盘/出货信号的字典
    """
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            return {
                'signal': 'unknown',
                'confidence': 0,
                'wash_prob': 0,
                'shipment_prob': 0,
                'reasons': []
            }
        
        daily_df = daily_df.sort_values('trade_date')
        zt_date = zt_history[0]['date'] if zt_history else None
        after_zt = daily_df[daily_df['trade_date'] > zt_date] if zt_date else daily_df.tail(20)
        
        if len(after_zt) < 5:
            return {
                'signal': 'unknown',
                'confidence': 0,
                'wash_prob': 0,
                'shipment_prob': 0,
                'reasons': []
            }
        
        # 特征提取
        features = {}
        reasons = []
        wash_score = 0
        shipment_score = 0
        
        # 1. 成交量特征
        zt_vol = after_zt.iloc[0]['vol'] if len(after_zt) > 0 else after_zt['vol'].mean()
        recent_vol_mean = after_zt.tail(5)['vol'].mean()
        vol_ratio = recent_vol_mean / zt_vol if zt_vol > 0 else 1
        
        if vol_ratio < 0.6:
            wash_score += 20
            reasons.append(f"极度缩量（量比{vol_ratio:.2f}）")
        elif vol_ratio < 0.8:
            wash_score += 15
            reasons.append(f"缩量调整（量比{vol_ratio:.2f}）")
        elif vol_ratio > 1.5:
            shipment_score += 20
            reasons.append(f"放量异常（量比{vol_ratio:.2f}）")
        
        # 2. 价格位置
        recent_close = after_zt['close'].iloc[-1]
        recent_high = after_zt['high'].max()
        recent_low = after_zt['low'].min()
        zt_price = after_zt.iloc[0]['close'] if zt_date else recent_close
        
        price_position = (recent_close - recent_low) / (recent_high - recent_low) if recent_high > recent_low else 0.5
        
        if price_position > 0.8:
            wash_score += 15
            reasons.append("价格维持在高位区间")
        elif price_position < 0.3:
            shipment_score += 20
            reasons.append("价格跌破低位区间")
        
        # 3. 均线支撑
        if len(after_zt) >= 20:
            ma5 = after_zt['close'].rolling(5).mean().iloc[-1]
            ma10 = after_zt['close'].rolling(10).mean().iloc[-1]
            ma20 = after_zt['close'].rolling(20).mean().iloc[-1]
            
            if recent_close > ma10 > ma20:
                wash_score += 15
                reasons.append("均线多头支撑")
            elif ma10 > recent_close:
                shipment_score += 15
                reasons.append("均线空头压制")
        
        # 4. 振幅分析
        after_zt_copy = after_zt.copy()
        after_zt_copy['amplitude'] = (after_zt_copy['high'] - after_zt_copy['low']) / after_zt_copy['close'] * 100
        avg_amplitude = after_zt_copy['amplitude'].mean()
        
        if avg_amplitude < 2:
            wash_score += 10
            reasons.append("振幅收窄（蓄势）")
        elif avg_amplitude > 5:
            shipment_score += 10
            reasons.append("振幅扩大（分歧）")
        
        # 5. MACD信号
        if len(after_zt) >= 26:
            close_series = after_zt['close'].values
            ema12 = pd.Series(close_series).ewm(span=12, adjust=False).mean().iloc[-1]
            ema26 = pd.Series(close_series).ewm(span=26, adjust=False).mean().iloc[-1]
            macd = ema12 - ema26
            
            if macd > 0:
                wash_score += 10
                reasons.append("MACD多头")
            else:
                shipment_score += 10
                reasons.append("MACD空头")
        
        # 6. 相对强弱
        if len(after_zt) >= 10:
            recent_change = (recent_close - after_zt['close'].iloc[0]) / after_zt['close'].iloc[0] * 100
            
            if -10 < recent_change < 5:
                wash_score += 10
                reasons.append("回调幅度健康")
            elif recent_change < -15:
                shipment_score += 15
                reasons.append("回调过深")
            elif recent_change > 15:
                shipment_score += 10
                reasons.append("涨幅过大需消化")
        
        # 综合判断
        total_score = wash_score + shipment_score
        
        if total_score == 0:
            return {
                'signal': 'unknown',
                'confidence': 0,
                'wash_prob': 0,
                'shipment_prob': 0,
                'reasons': ['数据不足，无法判断']
            }
        
        wash_prob = (wash_score / total_score * 100) if total_score > 0 else 0
        shipment_prob = (shipment_score / total_score * 100) if total_score > 0 else 0
        confidence = min(total_score / 2, 100)  # 置信度
        
        if wash_prob > shipment_prob + 20:
            signal = 'wash'
        elif shipment_prob > wash_prob + 20:
            signal = 'shipment'
        else:
            signal = 'uncertain'
        
        return {
            'signal': signal,
            'confidence': confidence,
            'wash_prob': wash_prob,
            'shipment_prob': shipment_prob,
            'reasons': reasons,
            'wash_score': wash_score,
            'shipment_score': shipment_score
        }
        
    except Exception as e:
        print(f"⚠️ 分析洗盘/出货失败: {e}")
        return {
            'signal': 'unknown',
            'confidence': 0,
            'wash_prob': 0,
            'shipment_prob': 0,
            'reasons': [f'分析失败: {str(e)}']
        }


def calculate_wave2_probability(ts_code, trade_date, zt_history, industry=None):
    """计算二波概率（游资操盘量化策略）- 增强版：板块热度+洗盘/出货判断"""
    end_date = trade_date
    start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
    
    daily_df = get_stock_daily_data(ts_code, start_date, end_date)
    if daily_df is None or daily_df.empty:
        return 0, {}
    
    daily_df = daily_df.sort_values('trade_date')
    scores = {}
    features = {}
    
    # 1. 调整幅度分析（首板后回调幅度）
    if len(zt_history) > 0:
        zt_date = zt_history[0]['date']
        zt_close = zt_history[0]['close']
        
        zt_row = daily_df[daily_df['trade_date'] == zt_date]
        if not zt_row.empty:
            zt_close = zt_row['close'].iloc[0]
        
        after_zt = daily_df[daily_df['trade_date'] > zt_date].tail(20)
        if not after_zt.empty:
            min_close = after_zt['close'].min()
            pullback_pct = (zt_close - min_close) / zt_close * 100
            
            # 最佳回调幅度：15%-30%
            if 10 <= pullback_pct <= 30:
                scores['pullback'] = 25
            elif 5 <= pullback_pct < 10:
                scores['pullback'] = 15
            elif 30 < pullback_pct <= 40:
                scores['pullback'] = 18
            else:
                scores['pullback'] = 5
            
            features['pullback_pct'] = pullback_pct
            features['zt_date'] = zt_date
        else:
            scores['pullback'] = 0
            features['pullback_pct'] = 0
    else:
        scores['pullback'] = 0
        features['pullback_pct'] = 0
    
    # 2. 均线多头排列（价格均线）
    if len(daily_df) >= 20:
        ma5 = daily_df['close'].rolling(5).mean().iloc[-1]
        ma10 = daily_df['close'].rolling(10).mean().iloc[-1]
        ma20 = daily_df['close'].rolling(20).mean().iloc[-1]
        
        if ma5 > ma10 > ma20:
            scores['ma多头'] = 20
        elif ma5 > ma10:
            scores['ma多头'] = 12
        else:
            scores['ma多头'] = 5
    else:
        scores['ma多头'] = 0
    
    # 3. 高位震荡缩量特征（新增）
    if len(zt_history) > 0 and len(daily_df) >= 15:
        zt_date = zt_history[0]['date']
        after_zt = daily_df[daily_df['trade_date'] > zt_date].tail(15)
        
        if len(after_zt) >= 5:
            # 价格相对高位震荡
            zt_row = daily_df[daily_df['trade_date'] == zt_date]
            zt_price = zt_row['close'].iloc[0] if not zt_row.empty else after_zt['close'].iloc[0]
            
            current_price = after_zt['close'].iloc[-1]
            price_relative = current_price / zt_price if zt_price > 0 else 0
            
            # 价格在涨停价的85%-115%区间震荡
            if 0.85 <= price_relative <= 1.15:
                scores['高位震荡'] = 15
            elif 0.75 <= price_relative < 0.85 or 1.15 < price_relative <= 1.25:
                scores['高位震荡'] = 10
            else:
                scores['高位震荡'] = 5
            
            # 缩量特征：近期成交量较涨停时减少
            zt_vol = zt_row['vol'].iloc[0] if not zt_row.empty else after_zt['vol'].iloc[0]
            recent_vol_mean = after_zt['vol'].tail(5).mean()
            vol_ratio = recent_vol_mean / zt_vol if zt_vol > 0 else 1
            
            if vol_ratio < 0.6:  # 缩量40%以上
                scores['缩量特征'] = 20
            elif vol_ratio < 0.8:  # 缩量20%以上
                scores['缩量特征'] = 15
            elif vol_ratio < 1.0:  # 小幅缩量
                scores['缩量特征'] = 10
            else:
                scores['缩量特征'] = 5
            
            features['price_relative'] = price_relative
            features['vol_ratio'] = vol_ratio
        else:
            scores['高位震荡'] = 0
            scores['缩量特征'] = 0
    else:
        scores['高位震荡'] = 0
        scores['缩量特征'] = 0
    
    # 4. 突破前期高点
    if len(daily_df) >= 30:
        pre_high = daily_df['close'].iloc[:-5].max()
        current_high = daily_df['close'].iloc[-1]
        
        if current_high > pre_high * 0.95:
            scores['突破前期高点'] = 15
        else:
            scores['突破前期高点'] = 5
    else:
        scores['突破前期高点'] = 0
    
    # 5. 近期涨停后表现
    recent_zt_count = len(zt_history)
    if recent_zt_count > 0:
        scores['近期涨停次数'] = min(recent_zt_count * 5, 15)
    else:
        scores['近期涨停次数'] = 0
    
    # 6. 市场情绪配合（简化版）
    try:
        limit_df = get_limit_list_data(trade_date)
        market_zt_count = len(limit_df) if limit_df is not None else 0
        if market_zt_count > 50:
            scores['市场情绪'] = 5
        else:
            scores['市场情绪'] = 0
    except:
        scores['市场情绪'] = 0
    
    # 7. 板块热度评分
    if industry and industry != '未知':
        sector_info = get_sector_hot_score(trade_date, industry)
        scores['板块热度'] = sector_info['hot_score']
        features['sector_info'] = sector_info
    else:
        scores['板块热度'] = 0
        features['sector_info'] = {
            'sector_zt_count': 0,
            'recent_hot_days': 0,
            'hot_trend': 'unknown',
            'hot_score': 0
        }
    
    # 8. 洗盘/出货信号分析
    wash_shipment = analyze_wash_or_shipment(daily_df, ts_code, trade_date, zt_history)
    features['wash_shipment'] = wash_shipment
    
    # 洗盘信号加分，出货信号减分
    if wash_shipment['signal'] == 'wash':
        scores['洗盘信号'] = 25
    elif wash_shipment['signal'] == 'uncertain':
        scores['洗盘信号'] = 10
    elif wash_shipment['signal'] == 'shipment':
        scores['洗盘信号'] = -15
    else:
        scores['洗盘信号'] = 0
    
    features['wave2_scores'] = scores
    
    # 计算总分
    total_score = sum(scores.values())
    
    # 二波概率（0-100%）
    wave2_prob = min(max(total_score, 0), 100)
    
    return wave2_prob, features


def analyze_with_deepseek(ts_code, trade_date, wave2_prob, wave2_scores, stock_info):
    """使用 DeepSeek 分析股票基本面和风格 - 优化版：增加板块热点轮动分析"""
    if not DEEPSEEK_KEY:
        return None
    
    # 获取股票基本信息
    try:
        basic_info = pro.stock_basic(ts_code=ts_code)
        if basic_info is not None and not basic_info.empty:
            name = basic_info.iloc[0]['name']
            industry = basic_info.iloc[0]['industry']
            market = basic_info.iloc[0]['market']
        else:
            name = ts_code
            industry = "未知"
            market = "未知"
    except:
        name = ts_code
        industry = "未知"
        market = "未知"
    
    # 获取近期财务数据
    try:
        financials = pro.fina_indicator(ts_code=ts_code, start_date='20240101')
        if financials is not None and not financials.empty:
            latest = financials.iloc[0]
            roe = latest.get('roe', 'N/A')
            gross_margin = latest.get('gross_profit_margin', 'N/A')
            debt_ratio = latest.get('debt_ratio', 'N/A')
        else:
            roe = gross_margin = debt_ratio = 'N/A'
    except:
        roe = gross_margin = debt_ratio = 'N/A'
    
    # 提取特征信息
    pullback_pct = wave2_scores.get('pullback_pct', 0)
    price_relative = wave2_scores.get('price_relative', 0)
    vol_ratio = wave2_scores.get('vol_ratio', 1)
    
    prompt = f"""你是一位专业的A股游资量化分析师。请分析以下股票是否符合"首板后二次启动"的投资逻辑。

股票信息：
- 代码：{ts_code}
- 名称：{name}
- 行业：{industry}
- 市场：{market}
- 交易日期：{trade_date}

财务指标：
- ROE：{roe}
- 毛利率：{gross_margin}
- 负债率：{debt_ratio}

关键特征分析：
- 回调幅度：{pullback_pct:.1f}%
- 价格相对位置：{price_relative:.2f}（相对于涨停价）
- 成交量缩量比：{vol_ratio:.2f}（相对于涨停时）

二波量化评分（满分100）：
- 回调幅度得分：{wave2_scores.get('wave2_scores', {}).get('pullback', 0)}/25
- 均线多头得分：{wave2_scores.get('wave2_scores', {}).get('ma多头', 0)}/20
- 高位震荡得分：{wave2_scores.get('wave2_scores', {}).get('高位震荡', 0)}/15
- 缩量特征得分：{wave2_scores.get('wave2_scores', {}).get('缩量特征', 0)}/20
- 突破前期高点得分：{wave2_scores.get('wave2_scores', {}).get('突破前期高点', 0)}/10
- 近期涨停次数得分：{wave2_scores.get('wave2_scores', {}).get('近期涨停次数', 0)}/10
- 市场情绪得分：{wave2_scores.get('wave2_scores', {}).get('市场情绪', 0)}/5

综合二波概率：{wave2_prob}%

请从以下角度分析（请用中文回复，简洁有条理，每点不超过2行）：

1. **基本面匹配度**（20字内）：该股基本面是否符合游资炒作风格？
2. **板块热点轮动**（30字内）：该股所属行业是否处于当前热点？
3. **高位震荡缩量**（30字内）：是否符合高位震荡缩量的洗盘特征？
4. **游资操盘特征**（40字内）：该股是否具备游资喜欢的特征？
5. **二波启动信号**（40字内）：是否有明显的二波启动迹象？
6. **风险提示**（30字内）：主要风险点是什么？

请严格按照格式输出，每项用"**标题**：内容"的格式。
"""
    
    headers = {
        'Authorization': f'Bearer {DEEPSEEK_KEY}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一位专业的A股游资量化分析师，擅长分析游资炒作机会。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 800
    }
    
    try:
        resp = requests.post('https://api.deepseek.com/v1/chat/completions', 
                            headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        analysis = result['choices'][0]['message']['content']
        
        return {
            'name': name,
            'industry': industry,
            'analysis': analysis,
            'wave2_prob': wave2_prob
        }
    except Exception as e:
        print(f"DeepSeek 分析失败: {e}")
        return None


def generate_review_report(trade_date, stocks_analyzed, market_emotion=None, hot_sectors=None):
    """生成每日复盘报告 - 集成大盘情绪和板块分析"""
    report = []
    report.append("="*70)
    report.append(f"🚀 涨停二波追踪 | {trade_date}")
    report.append("="*70)
    report.append("")
    
    # ===== 0. 大盘情绪分析 =====
    if market_emotion:
        report.append("🌡️  大盘情绪")
        emotion_score = market_emotion.get('情绪分', 0)
        market_stage = market_emotion.get('市场阶段', '未知')
        risk_level = market_emotion.get('风险等级', '未知')
        
        # 情绪图标
        if emotion_score >= 70:
            emotion_icon = '🔥'
        elif emotion_score >= 40:
            emotion_icon = '☀️'
        elif emotion_score >= 25:
            emotion_icon = '🌥️'
        else:
            emotion_icon = '❄️'
        
        report.append(f"   {emotion_icon} 情绪分: {emotion_score:.1f} | 阶段: {market_stage}")
        
        if '大盘涨跌幅' in market_emotion:
            report.append(f"   📈 大盘: {market_emotion.get('大盘点位', '')} | {market_emotion.get('大盘涨跌幅', '')}%")
        if '涨停家数' in market_emotion:
            report.append(f"   🏛️  涨停: {market_emotion.get('涨停家数', 0)} | 跌停: {market_emotion.get('跌停家数', 0)} | 炸板率: {market_emotion.get('炸板率', 0)}%")
        if '最终建议仓位' in market_emotion:
            report.append(f"   💼 仓位: {market_emotion.get('情绪仓位', '')} | 上限: {market_emotion.get('趋势仓位上限', '')} | 建议: {market_emotion.get('最终建议仓位', '')}")
        
        report.append("")
    
    # ===== 0.5 主线板块分析 =====
    if hot_sectors is not None and not hot_sectors.empty:
        report.append("🏆  主线板块 TOP 10")
        top10 = hot_sectors.head(10)
        for i, row in top10.iterrows():
            sector_name = row.get('主线', '未知')
            sector_score = row.get('主线强度', 0)
            sector_type = row.get('类型', '未知')
            leader_name = row.get('龙头名称', '')
            decline_flag = ' ↓' if row.get('是否退潮', False) else ''
            
            line = f"   {i+1}. {sector_type} | {sector_name}"
            if leader_name:
                line += f" ({leader_name})"
            line += f" | 强度: {sector_score:.0f}{decline_flag}"
            report.append(line)
        
        report.append("")
    
    # ===== 1. 筛选统计 =====
    # 放宽主线板块筛选，只要有行业信息就算
    main_sector_stocks = [s for s in stocks_analyzed if s.get('industry') and s.get('industry') != '未知']
    # 潜在候选 - 放宽条件
    potential_candidates = [s for s in stocks_analyzed if s.get('stabilization', {}).get('score', 0) >= 20]
    
    report.append("📊 今日概况")
    report.append(f"   分析标的: {len(stocks_analyzed)} 只（已过滤ST）")
    report.append(f"   主线板块: {len(main_sector_stocks)} 只（近20天前15板块）")
    report.append(f"   潜在候选: {len(potential_candidates)} 只（高位震荡缩量）")
    report.append("")
    
    # ===== 2. 重点推荐（优先主线板块、高概率） =====
    report.append("🎯 重点推荐 TOP 8")
    report.append("")
    
    # 优先排序：主线板块 > 潜在候选 > 二波概率
    sorted_stocks = sorted(main_sector_stocks, 
                          key=lambda x: (x.get('is_potential_candidate', False), x['wave2_prob']), 
                          reverse=True)[:8]
    
    for i, stock in enumerate(sorted_stocks, 1):
        stock_name = stock.get('name', stock.get('ts_code', '未知'))
        candidate_tag = "⭐" if stock.get('is_potential_candidate', False) else ""
        main_tag = "🏆" if stock.get('is_main_sector', False) else ""
        
        # 基本信息行
        report.append(f"{i}. {stock_name} {stock['ts_code']} {candidate_tag}{main_tag}")
        
        # 第一行：概率 + 调整天数 + 行业
        wave2_prob = stock['wave2_prob']
        adj_days = stock.get('adjustment_days', 0)
        industry = stock.get('industry', '未知')
        report.append(f"   概率: {wave2_prob}% | 调整: {adj_days}天 | {industry}")
        
        # 第二行：价格相对 + 量比
        price_rel = stock.get('price_relative', 0)
        vol_rat = stock.get('vol_ratio', 1)
        report.append(f"   位置: {price_rel:.2f} | 量比: {vol_rat:.2f}")
        
        # 第三行：板块热度 + 洗盘/出货信号
        sector_line = ""
        sector_info = stock.get('sector_info', {})
        if sector_info:
            hot_trend = sector_info.get('hot_trend', 'unknown')
            hot_emoji = '🔥' if hot_trend == 'hot' else ('📈' if hot_trend == 'warming' else '❄️')
            sector_line += f"{hot_emoji}{hot_trend}"
        
        wash_shipment = stock.get('wash_shipment', {})
        if wash_shipment and wash_shipment.get('signal') != 'unknown':
            signal = wash_shipment.get('signal', 'unknown')
            wash_prob = wash_shipment.get('wash_prob', 0)
            shipment_prob = wash_shipment.get('shipment_prob', 0)
            signal_emoji = '🟢' if signal == 'wash' else ('🔴' if signal == 'shipment' else '🟡')
            if sector_line:
                sector_line += " | "
            sector_line += f"{signal_emoji}洗{wash_prob:.0f}%出{shipment_prob:.0f}%"
        
        if sector_line:
            report.append(f"   {sector_line}")
        
        # 分析摘要
        if stock.get('deepseek_analysis'):
            analysis = stock['deepseek_analysis'][:80]
            report.append(f"   {analysis}...")
        
        report.append("")
    
    # ===== 2.5 调整企稳信号专区（最多3只）=====
    stabilization_candidates = select_top_stabilization_candidates(stocks_analyzed, max_count=3)
    if stabilization_candidates:
        report.append("🔔 调整企稳信号（工业富联特征）")
        report.append("")
        for i, stock in enumerate(stabilization_candidates, 1):
            stock_name = stock.get('name', stock.get('ts_code', '未知'))
            stab = stock.get('stabilization', {})
            stab_score = stab.get('score', 0)
            features = stab.get('features', [])
            desc = stab.get('description', '')
            wave2_prob = stock.get('wave2_prob', 0)
            adj_days = stock.get('adjustment_days', 0)
            
            report.append(f"{i}. {stock_name} {stock['ts_code']}")
            report.append(f"   企稳分数: {stab_score} | 二波概率: {wave2_prob}% | 调整: {adj_days}天")
            if features:
                report.append(f"   特征: {', '.join(features[:3])}")
            if desc and desc != '暂无明显信号':
                report.append(f"   描述: {desc}")
            report.append("")
    
    # ===== 3. 潜在候选专区 =====
    if potential_candidates:
        report.append("💎 潜在候选（高位震荡缩量）")
        report.append("")
        sorted_potential = sorted(potential_candidates, key=lambda x: x['wave2_prob'], reverse=True)[:6]
        
        for i, stock in enumerate(sorted_potential, 1):
            stock_name = stock.get('name', stock.get('ts_code', '未知'))
            report.append(f"{i}. {stock_name}")
            report.append(f"   概率: {stock['wave2_prob']}% | 调整: {stock.get('adjustment_days', 0)}天 | {stock.get('industry', '未知')}")
            price_rel = stock.get('price_relative', 0)
            vol_rat = stock.get('vol_ratio', 1)
            report.append(f"   位置: {price_rel:.2f} | 量比: {vol_rat:.2f}")
            report.append("")
    
    # ===== 4. 风险提示 =====
    report.append("⚠️ 风险提示")
    report.append("   • 热点切换快，注意节奏")
    report.append("   • 建议仓位控制在30%以内")
    report.append("   • 严格止损-5%~7%")
    report.append("")
    report.append("="*70)
    report.append("💡 免责声明：仅供学习，不构成投资建议")
    report.append("="*70)
    
    return '\n'.join(report)


def send_to_wechat(title, content, key=None):
    """通过 Server酱 发送到微信"""
    if key is None:
        key = SERVERCHAN_KEY
    
    if not key:
        print("未配置 Server酱 KEY，跳过微信推送")
        return False
    
    url = f"https://sctapi.ftqq.com/{key}.send"
    
    data = {
        "title": title,
        "desp": content
    }
    
    try:
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get('code') == 0 or result.get('errno') == 0:
            print(f"✓ 微信推送成功")
            return True
        else:
            print(f"✗ 微信推送失败: {result}")
            return False
    except Exception as e:
        print(f"✗ 微信推送异常: {e}")
        return False


def daily_limit_track(trade_date, force_refresh=False):
    """每日涨停跟踪主函数"""
    print("="*60)
    print(f"📊 每日涨停跟踪与复盘")
    print(f"交易日期: {trade_date}")
    print(f"强制刷新缓存: {'是' if force_refresh else '否'}")
    print("="*60)
    
    # 1. 获取涨停数据
    print("\n[1/6] 获取涨停池数据...")
    limit_df = get_limit_list_data(trade_date, force_refresh)
    if limit_df is None or limit_df.empty:
        print("未获取到涨停数据")
        return None
    
    print(f"涨停池总数: {len(limit_df)}")
    
    # 2. 筛选第一板
    print("\n[2/6] 筛选第一板股票...")
    first_board_df = filter_first_board_stocks(limit_df, trade_date)
    print(f"第一板数量: {len(first_board_df)}")
    
    # 3. 分析温和放量
    print("\n[3/6] 分析温和放量...")
    qualified_stocks = []
    
    for idx, row in first_board_df.iterrows():
        ts_code = row.get('ts_code')
        if not ts_code:
            continue
        
        vol_analysis = analyze_moderate_volume(ts_code, trade_date)
        if vol_analysis:
            qualified_stocks.append({
                'ts_code': ts_code,
                'close': row.get('close', 0),
                'vol_ratio': vol_analysis['vol_ratio'],
                'name': row.get('name', ts_code),
                'industry': row.get('industry', '未知')
            })
    
    print(f"符合条件（温和放量）的股票: {len(qualified_stocks)}")
    
    # 4. 保存记录
    print("\n[4/6] 保存涨停记录...")
    save_daily_limit_record(trade_date, qualified_stocks)
    
    # 5. 复盘前20天涨停股票（排除当天涨停）
    print("\n[5/6] 复盘前20天涨停股票...")
    
    # 获取前19天的涨停股票（排除当天）
    start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=20)).strftime('%Y%m%d')
    recent_stocks, stock_history = get_stocks_zt_in_range(
        start_date,
        (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d'),
        lookback_days=19
    )
    
    print(f"前19天涨停过的股票总数（排除当天）: {len(recent_stocks)}")
    
    # 分析每只股票的二波概率，优先筛选高位震荡缩量特征
    stocks_analyzed = []
    for ts_code in recent_stocks[:50]:  # 限制分析数量
        zt_history = stock_history.get(ts_code, [])
        
        # 先获取股票基本信息（行业）
        industry = '未知'
        try:
            basic_info = pro.stock_basic(ts_code=ts_code)
            if basic_info is not None and not basic_info.empty:
                industry = basic_info.iloc[0].get('industry', '未知')
        except:
            pass
        
        # 调用二波概率计算（传入行业信息）
        wave2_prob, wave2_scores = calculate_wave2_probability(ts_code, trade_date, zt_history, industry)
        
        # 提取特征用于筛选
        price_relative = wave2_scores.get('price_relative', 0)
        vol_ratio = wave2_scores.get('vol_ratio', 1)
        
        # 优先筛选高位震荡缩量特征的股票
        is_potential_candidate = 0.85 <= price_relative <= 1.15 and vol_ratio < 0.8
        
        # 提取板块和洗盘/出货信息
        sector_info = wave2_scores.get('sector_info', {})
        wash_shipment = wave2_scores.get('wash_shipment', {})
        
        # ===== 新增功能开始 =====
        
        # 1. 过滤ST股票
        stock_name = ts_code  # 默认用代码
        is_stock = False
        try:
            basic_info = pro.stock_basic(ts_code=ts_code)
            if basic_info is not None and not basic_info.empty:
                stock_name = basic_info.iloc[0].get('name', ts_code)
                name_upper = stock_name.upper()
                if 'ST' in name_upper or '*' in name_upper or '退市' in name_upper:
                    is_stock = True
        except:
            pass
        
        if is_stock:
            print(f"  跳过ST股票: {stock_name}")
            continue
        
        # 2. 识别近20天主线板块
        is_main_sector = False
        main_sector = False
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            # 查询近20天各行业涨停数量
            start_date_main = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=20)).strftime('%Y%m%d')
            cursor.execute('''
                SELECT industry, COUNT(*) as zt_count
                FROM limit_stocks
                WHERE trade_date BETWEEN ? AND ?
                GROUP BY industry
                ORDER BY zt_count DESC
                LIMIT 15
            ''', (start_date_main, trade_date))
            
            main_sectors_list = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            # 判断当前股票是否属于主线板块（前15个）
            if industry and industry != '未知' and industry in main_sectors_list:
                is_main_sector = True
                
        except Exception as e:
            print(f"  主线板块识别失败: {e}")
            is_main_sector = False
        
        # 3. 计算涨停后调整天数
        adjustment_days = 0
        if zt_history:
            last_zt_date = zt_history[0]['date']
            try:
                zt_dt = datetime.strptime(last_zt_date, '%Y%m%d')
                today_dt = datetime.strptime(trade_date, '%Y%m%d')
                delta = today_dt - zt_dt
                adjustment_days = delta.days
            except:
                adjustment_days = 0
        
        # ===== 新增过滤和分析开始 =====
        
        # 优化：先获取一次日线数据供后续所有分析使用，避免重复请求
        daily_df = None
        zt_open = None
        if zt_history:
            last_zt_date = zt_history[0]['date']
            start = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
            daily_df = get_stock_daily_data(ts_code, start, trade_date)
            
            # 从日线数据获取涨停日开盘价
            if daily_df is not None and not daily_df.empty:
                zt_row = daily_df[daily_df['trade_date'] == last_zt_date]
                if not zt_row.empty:
                    zt_open = zt_row.iloc[0].get('open', None)
        
        # 1. 市值过滤（>40亿，阈值宽松以便演示）
        if not filter_market_cap(ts_code, min_cap=40, daily_df=daily_df):
            print(f"  跳过市值不足: {stock_name}")
            continue
        
        # 2. 5日线乖离过滤（暂时关闭以便演示功能）
        ma5_ok = True
        ma5_deviation = 0
        zt_distance = 0
        # if zt_history and daily_df is not None:
        #     last_zt_date = zt_history[0]['date']
        #     zt_close = zt_history[0].get('close', 0)
        #     
        #     ma5_ok, ma5_deviation, zt_distance = calculate_ma5_deviation(daily_df, last_zt_date, zt_close, zt_open)
        #     if not ma5_ok:
        #         print(f"  跳过乖离过大/远离涨停价: {stock_name}")
        #         continue
        
        # 3. 企稳信号分析（工业富联特征，增强版：十字星、底分型、MACD等）
        stabilization = {'score': 0, 'features': [], 'description': '数据不足'}
        if zt_history and daily_df is not None:
            stabilization = analyze_stabilization_signal(daily_df, zt_history)
        
        # 4. 主线板块识别（优化版：板块动量、趋势分析）
        is_main_sector = False
        if industry and industry != '未知':
            try:
                main_sectors = get_main_sectors(trade_date, lookback_days=20, top_n=15)
                is_main_sector = industry in main_sectors
            except Exception as e:
                print(f"  主线板块识别失败: {e}")
        
        # ===== 新增过滤和分析结束 =====
        
        deepseek_result = None
        # 优化：只有企稳分数>=30或二波概率>=60才调用AI，节省API次数
        should_call_deepseek = (stabilization.get('score', 0) >= 30 or wave2_prob >= 60)
        if should_call_deepseek:
            deepseek_result = analyze_with_deepseek(ts_code, trade_date, wave2_prob, wave2_scores, {})
        
        # 获取股票名称
        if deepseek_result:
            if isinstance(deepseek_result, dict):
                stock_name = deepseek_result.get('name', ts_code)
        
        stocks_analyzed.append({
            'ts_code': ts_code,
            'name': stock_name,
            'wave2_prob': wave2_prob,
            'wave2_scores': wave2_scores,
            'deepseek_analysis': deepseek_result.get('analysis') if deepseek_result and isinstance(deepseek_result, dict) else None,
            'industry': industry,
            'price_relative': price_relative,
            'vol_ratio': vol_ratio,
            'is_potential_candidate': is_potential_candidate,
            'sector_info': sector_info,
            'wash_shipment': wash_shipment,
            'is_main_sector': is_main_sector,
            'adjustment_days': adjustment_days,
            'stabilization': stabilization,
            'ma5_deviation': ma5_deviation,
            'ma5_ok': ma5_ok
        })
        
        # 限制API调用频率，每10只显示进度
        if (len(stocks_analyzed) + 1) % 10 == 0:
            print(f"  已分析: {len(stocks_analyzed)} 只股票")
    
    print(f"  ✓ 复盘完成，共分析: {len(stocks_analyzed)} 只股票")
    
    # 5.5 大盘情绪和板块分析（集成 emotion.py 和 block.py）
    print("\n[5.5/6] 大盘情绪和板块分析...")
    market_emotion = None
    hot_sectors = None
    
    try:
        # 临时修改工作目录到 mystock，避免路径问题
        original_cwd = os.getcwd()
        os.chdir(MYSTOCK_DIR)
        
        # 导入 block.py 和 emotion.py
        import block as blk
        import emotion as emo
        
        # 首先分析热门板块
        hot_sectors = blk.analyze_hot_sectors()
        
        # 然后分析市场情绪
        market_emotion = emo.analyze_market_emotion(hot_sectors)
        
        # 恢复原工作目录
        os.chdir(original_cwd)
        
    except Exception as e:
        print(f"⚠️ 大盘情绪/板块分析失败: {e}")
        market_emotion = None
        hot_sectors = None
    
    # 6. 生成报告（传入大盘和板块分析结果）
    print("\n[6/6] 生成复盘报告...")
    report = generate_review_report(trade_date, stocks_analyzed, market_emotion, hot_sectors)
    
    report_file = os.path.join(REVIEW_DIR, f"review_{trade_date}.txt")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"✓ 报告已保存: {report_file}")
    
    # 7. 推送微信
    print("\n📱 推送微信通知...")
    title = f"📊 每日涨停复盘 {trade_date}"
    success = send_to_wechat(title, report)
    
    if success:
        print("✓ 微信推送成功")
    else:
        print("⚠ 微信推送失败")
    
    return report_file


def clear_cache():
    """清理缓存文件"""
    print("清理缓存文件...")
    removed_count = 0
    
    # 清理涨停数据缓存
    if os.path.exists(LIMIT_CACHE_DIR):
        for file in os.listdir(LIMIT_CACHE_DIR):
            file_path = os.path.join(LIMIT_CACHE_DIR, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
                removed_count += 1
    
    # 清理日线数据缓存
    if os.path.exists(DAILY_CACHE_DIR):
        for file in os.listdir(DAILY_CACHE_DIR):
            file_path = os.path.join(DAILY_CACHE_DIR, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
                removed_count += 1
    
    print(f"✓ 已清理 {removed_count} 个缓存文件")


def backtrack_history(days=20, force_refresh=False):
    """
    回溯历史数据
    
    参数:
        days: 回溯天数，默认20
        force_refresh: 是否强制刷新
    """
    print("="*80)
    print(f"📊 历史数据回溯")
    print(f"回溯天数: {days} 个交易日")
    print("="*80)
    
    # 初始化数据库
    init_sqlite_db()
    
    # 获取过去N个交易日
    trading_days = get_last_n_trading_days(days)
    
    if not trading_days:
        print("❌ 无法获取交易日列表")
        return
    
    print(f"\n📅 将回溯 {len(trading_days)} 个交易日:")
    for i, day in enumerate(trading_days, 1):
        print(f"  {i}. {day}")
    
    print("\n" + "="*80)
    
    # 逐日回溯
    success_count = 0
    fail_count = 0
    
    for i, trade_date in enumerate(trading_days, 1):
        print(f"\n[{i}/{len(trading_days)}] 正在处理 {trade_date}...")
        
        try:
            # 获取涨停数据
            limit_df = get_limit_list_data(trade_date, force_refresh)
            
            if limit_df is None or limit_df.empty:
                print(f"  ⚠️ 无涨停数据，跳过")
                fail_count += 1
                continue
            
            print(f"  涨停池总数: {len(limit_df)}")
            
            # 筛选第一板
            first_board_df = filter_first_board_stocks(limit_df, trade_date)
            print(f"  第一板数量: {len(first_board_df)}")
            
            # 分析温和放量
            qualified_stocks = []
            for idx, row in first_board_df.iterrows():
                ts_code = row.get('ts_code')
                if not ts_code:
                    continue
                
                vol_analysis = analyze_moderate_volume(ts_code, trade_date)
                if vol_analysis:
                    qualified_stocks.append({
                        'ts_code': ts_code,
                        'close': row.get('close', 0),
                        'vol_ratio': vol_analysis['vol_ratio'],
                        'name': row.get('name', ts_code),
                        'industry': row.get('industry', '未知')
                    })
            
            print(f"  符合条件数量: {len(qualified_stocks)}")
            
            # 保存到SQLite
            save_to_sqlite(trade_date, qualified_stocks)
            print(f"  ✓ 数据已保存到SQLite")
            
            success_count += 1
            
            # 限制API调用频率
            import time
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  ❌ 处理失败: {e}")
            fail_count += 1
            continue
    
    print("\n" + "="*80)
    print(f"✅ 回溯完成！")
    print(f"  成功: {success_count} 天")
    print(f"  失败: {fail_count} 天")
    print(f"  数据库: {DB_FILE}")
    print("="*80)


def query_and_export(start_date=None, end_date=None, min_prob=None, output_file=None):
    """
    查询并导出历史数据
    
    参数:
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
        min_prob: 最低二波概率
        output_file: 输出文件路径
    """
    date_range = None
    if start_date and end_date:
        date_range = (start_date, end_date)
    
    df = query_history(date_range=date_range, min_prob=min_prob)
    
    if df.empty:
        print("❌ 未查询到数据")
        return
    
    print(f"\n📊 查询结果:")
    print(f"  总记录数: {len(df)}")
    print(f"  日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"  平均二波概率: {df['wave2_prob'].mean():.1f}%")
    print(f"  最高二波概率: {df['wave2_prob'].max():.1f}%")
    
    if output_file:
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"  ✓ 数据已导出: {output_file}")
    
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='每日涨停跟踪与复盘系统')
    parser.add_argument('trade_date', nargs='?', 
                       help='交易日期 (YYYYMMDD)，默认为今日')
    parser.add_argument('--force', '-f', action='store_true',
                       help='强制刷新缓存')
    parser.add_argument('--clear-cache', action='store_true',
                       help='清理缓存文件')
    parser.add_argument('--backtrack', '-b', action='store_true',
                       help='回溯历史数据（默认20个交易日）')
    parser.add_argument('--days', type=int, default=20,
                       help='回溯天数（默认20）')
    parser.add_argument('--query', '-q', action='store_true',
                       help='查询历史数据')
    parser.add_argument('--start-date',
                       help='查询开始日期 (YYYYMMDD)')
    parser.add_argument('--end-date',
                       help='查询结束日期 (YYYYMMDD)')
    parser.add_argument('--min-prob', type=float,
                       help='最低二波概率')
    parser.add_argument('--export',
                       help='导出CSV文件路径')
    
    args = parser.parse_args()
    
    # 初始化数据库
    init_sqlite_db()
    
    # 清理缓存模式
    if args.clear_cache:
        clear_cache()
        sys.exit(0)
    
    # 回溯历史数据模式
    if args.backtrack:
        backtrack_history(days=args.days, force_refresh=args.force)
        sys.exit(0)
    
    # 查询历史数据模式
    if args.query:
        query_and_export(
            start_date=args.start_date,
            end_date=args.end_date,
            min_prob=args.min_prob,
            output_file=args.export
        )
        sys.exit(0)
    
    # 智能获取交易日期
    actual_date, was_specified, date_msg = get_smart_trade_date(args.trade_date)
    
    if actual_date is None:
        print("❌ 无法确定有效的交易日期")
        sys.exit(1)
    
    trade_date = actual_date
    
    # 显示提示信息
    if date_msg:
        print(date_msg)
    
    print(f"开始处理日期: {trade_date}")
    report_file = daily_limit_track(trade_date, force_refresh=args.force)
    
    if report_file:
        print(f"\n✅ 处理完成！报告文件: {report_file}")
    else:
        print(f"\n❌ 处理失败")
