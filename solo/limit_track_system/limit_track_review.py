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

# 接入 DataFetcher 统一缓存
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'multi_factor_picker'))
from data_fetcher import DataFetcher

_df_singleton = None
def _get_df():
    global _df_singleton
    if _df_singleton is not None:
        return _df_singleton
    try:
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            # 从 d:\mystock\solo\.env 读取
            env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('TUSHARE_TOKEN=') and not line.startswith('#'):
                            token = line.split('=', 1)[1].strip()
                            break
        if not token:
            return None
        config = {'cache': {'enabled': True, 'dir': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'multi_factor_picker', 'cache'), 'expire_hours': 168}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
        _df_singleton = DataFetcher(token, config)
    except Exception:
        return None
    return _df_singleton

def _get_stock_basic_df(ts_code):
    """通过DataFetcher获取单只股票基本信息DataFrame（get_stock_list全量+本地过滤），失败降级到pro"""
    fetcher = _get_df()
    if fetcher is not None:
        try:
            sl = fetcher.get_stock_list()
            if sl is not None and not sl.empty:
                sub = sl[sl['ts_code'] == ts_code]
                if not sub.empty:
                    return sub
                return pd.DataFrame()
        except Exception:
            pass
    # 降级fallback
    try:
        return pro.stock_basic(ts_code=ts_code)
    except Exception:
        return None

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
            df = _get_stock_basic_df(ts_code)
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
                            _fetcher = _get_df()
                            if _fetcher is not None:
                                daily_basic = _fetcher.get_daily_basic_by_code(ts_code=ts_code, start_date=check_date, end_date=check_date)
                            else:
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
    分析调整企稳信号

    严格过滤：
    - 当天涨停/跌停（|pct_chg| >= 9%）→ 不是企稳，直接排除
    - 当天涨幅 > 5% → 不是企稳，是拉升中
    - 当天跌幅 > 7% → 破位下跌，不是企稳

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

        # ═══ 核心过滤：排除当天极端涨跌 ═══
        latest_chg = daily_df['pct_chg'].iloc[-1] if 'pct_chg' in daily_df.columns else 0
        latest_close = daily_df['close'].iloc[-1]

        # 涨停/跌停 → 直接排除
        if latest_chg >= 9.0:
            return {'score': 0, 'features': [], 'description': f'当天涨停(+{latest_chg:.1f}%)，非企稳'}
        if latest_chg <= -9.0:
            return {'score': 0, 'features': [], 'description': f'当天跌停({latest_chg:.1f}%)，非企稳'}
        # 大跌也不企稳
        if latest_chg <= -7.0:
            return {'score': 0, 'features': [], 'description': f'当天大跌({latest_chg:.1f}%)，破位'}
        # 大涨也不企稳
        if latest_chg >= 5.0:
            return {'score': 0, 'features': [], 'description': f'当天大涨({latest_chg:.1f}%)，拉升中非企稳'}

        score = 0
        features = []
        description = []

        recent_k = daily_df.tail(15).copy()

        # ======== 特征1：低位下影小阳线 =====
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

                if close_p > open_p and body_size < 0.03 and lower_shadow > 0.015 and body_size < lower_shadow:
                    score += 25
                    features.append(f"低位下影小阳线（{kline['trade_date']}）")
                    description.append("出现企稳下影线")
                    break

        # ======== 特征2：连续小阳线 =====
        positive_count = 0
        for i in range(-6, 0):
            if len(recent_k) > abs(i):
                kline = recent_k.iloc[i]
                kline_chg = kline.get('pct_chg', 0)
                # 必须是小阳线（涨幅<4%），且不是涨停日
                if kline['close'] > kline['open'] and 0 < kline_chg < 4:
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
                    features.append("企稳十字星")
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

            if len(recent_macd) >= 5:
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

        # ======== 特征7：价格在合理回调区间（10-25%）====
        if zt_history:
            zt_close = zt_history[0].get('close', 0)
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
        _fetcher = _get_df()
        if _fetcher is not None:
            df = _fetcher.get_trade_cal(start_date=date_str, end_date=date_str, is_open='1')
            if not df.empty:
                return True
            return False
        else:
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
            _fetcher = _get_df()
            if _fetcher is not None:
                df = _fetcher.get_limit_list_ths(trade_date=trade_date)
                # DataFetcher未传limit_type，需本地过滤涨停池
                if df is not None and not df.empty and 'limit_type' in df.columns:
                    df = df[df['limit_type'] == '涨停池']
            else:
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
            _fetcher = _get_df()
            if _fetcher is not None:
                df = _fetcher.get_daily_by_code(ts_code=ts_code, start_date=start_date, end_date=end_date)
            else:
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
        df = _get_stock_basic_df(ts_code)
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


def analyze_with_deepseek_bulk(candidates, hot_sectors, trade_date):
    """
    批量AI分析：将技术面筛选的候选股票 + 热点板块提交给DeepSeek，
    由AI评选出二波机会最大/空间最大/基本面无雷风险最低的TOP5股票

    参数:
        candidates: 技术面筛选的候选股票列表
        hot_sectors: 热点板块DataFrame
        trade_date: 交易日期

    返回:
        tuple: (top5列表, AI原始回复文本)
    """
    if not candidates or not DEEPSEEK_KEY:
        return [], ""

    # 构建候选股票文本
    stock_lines = []
    for i, s in enumerate(candidates, 1):
        features_str = " | ".join(s.get('features', [])) if s.get('features') else s.get('breakout_detail', '')
        stock_lines.append(
            f"{i}. {s['name']}({s['ts_code']}) "
            f"行业:{s.get('industry','')} "
            f"调整{s['adjustment_days']}天 "
            f"信号:{s['signal_type']} "
            f"回调{s.get('callback_pct',0):.1f}% "
            f"现价:{s['latest_close']} "
            f"企稳分:{s.get('stabilization_score',0)} "
            f"突破分:{s.get('breakout_score',0)} "
            f"特征:{features_str}"
        )

    stocks_text = "\n".join(stock_lines)

    # 构建热点板块文本
    sector_text = "无板块数据"
    if hot_sectors is not None and not hot_sectors.empty:
        sector_lines = []
        for _, r in hot_sectors.head(15).iterrows():
            sector_lines.append(
                f"  {r.get('主线','')} 强度{r.get('主线强度',0):.0f} "
                f"龙头{r.get('龙头名称','')}"
            )
        sector_text = "\n".join(sector_lines)

    prompt = f"""你是一位顶尖的A股游资量化分析师。以下是技术面筛选出的候选股票列表和当前热点板块。

=== 候选股票（技术面已通过企稳/突破信号筛选）===
{stocks_text}

=== 当前热点板块 TOP15 ===
{sector_text}

请从以上候选股票中，属于上面的TOP15热点板块的股票,从中评选出二波机会最大、上涨空间最大、基本面雷区风险最低的TOP5股票。

对每只入选股票，请严格按以下格式输出（每只股票一行，用|分隔各字段）：

股票代码|股票名称|调整天数|信号类别|介入价格建议|止损价格建议|空间分析|逻辑分析

要求：
1. 调整天数 = 涨停后至今的交易日数
2. 信号类别从以下选择：企稳信号 / 放量突破 / 企稳+突破共振
3. 介入价格 = 具体数字（元），结合现价和支撑位给出
4. 止损价格 = 具体数字（元），支撑位下方3-5%
5. 空间分析 = 一句话描述目标价和上涨空间（如"目标12元,空间25%"）
6. 逻辑分析 = 一句话说明入选逻辑（结合热点板块、技术形态、回调充分度）

最后输出一行 ===END=== 作为结束。"""
    
    headers = {
        'Authorization': f'Bearer {DEEPSEEK_KEY}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一位专业的A股游资量化分析师，擅长抓热点,分析首板后二次启动机会。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1500
    }
    
    try:
        print("  正在调用DeepSeek API进行批量分析...")
        resp = requests.post('https://api.deepseek.com/v1/chat/completions',
                            headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        raw_content = result['choices'][0]['message']['content']
        print(f"  DeepSeek分析完成")
        
        # 解析AI返回结果
        top5 = []
        lines = raw_content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('===') or line.startswith('END'):
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 8:
                entry = {
                    'ts_code': parts[0],
                    'name': parts[1],
                    'adjustment_days': parts[2],
                    'signal_type': parts[3],
                    'entry_price': parts[4],
                    'stop_loss': parts[5],
                    'space_analysis': parts[6],
                    'logic': parts[7],
                    'raw_line': line
                }
                top5.append(entry)
        
        # 如果AI返回的格式不对（没按|分割），尝试重新解析
        if not top5:
            import re
            for line in lines:
                line = line.strip()
                if not line or line.startswith('===') or line.startswith('END'):
                    continue
                match = re.match(r'^\d+[\.\s]+(\d{6}\.\w{2})', line)
                if match:
                    entry = {
                        'ts_code': match.group(1),
                        'name': '',
                        'adjustment_days': '',
                        'signal_type': '',
                        'entry_price': '',
                        'stop_loss': '',
                        'space_analysis': '',
                        'logic': line,
                        'raw_line': line
                    }
                    top5.append(entry)
        
        print(f"  AI返回TOP5数量: {len(top5)}")
        return top5, raw_content
        
    except Exception as e:
        print(f"  DeepSeek批量分析失败: {e}")
        return [], ""


def generate_review_report(trade_date, technical_candidates, top5, hot_sectors):
    """生成每日复盘报告 - 筛选概况 + AI TOP5推荐"""
    report = []
    report.append("="*70)
    report.append(f"涨停二波追踪 | {trade_date}")
    report.append("="*70)
    report.append("")

    # ===== 1. 热点板块 =====
    if hot_sectors is not None and not hot_sectors.empty:
        report.append("🏆 热点板块 TOP 10")
        top10 = hot_sectors.head(10)
        for i, row in top10.iterrows():
            sector_name = row.get('主线', '未知')
            sector_score = row.get('主线强度', 0)
            sector_type = row.get('类型', '未知')
            leader_name = row.get('龙头名称', '')
            decline_flag = ' ↓' if row.get('是否退潮', False) else ''
            line = f"  {i+1}. {sector_type} | {sector_name}"
            if leader_name:
                line += f" ({leader_name})"
            line += f" | 强度: {sector_score:.0f}{decline_flag}"
            report.append(line)
        report.append("")

    # ===== 2. 技术面筛选概况 =====
    report.append("📊 技术面筛选概况")
    report.append(f"  分析标的: {len(technical_candidates)} 只通过技术面筛选")
    stab_count = sum(1 for s in technical_candidates if s['signal_type'] == '企稳信号' or s['signal_type'] == '企稳+突破共振')
    break_count = sum(1 for s in technical_candidates if '突破' in s['signal_type'])
    report.append(f"  企稳信号: {stab_count}只 | 放量突破: {break_count}只 | 共振: {len(technical_candidates) - stab_count - (break_count - stab_count)}只")
    report.append("")

    # 候选列表
    if technical_candidates:
        report.append("📋 技术面候选列表")
        for i, s in enumerate(technical_candidates, 1):
            features_str = " | ".join(s.get('features', [])) if s.get('features') else s.get('breakout_detail', '')[:40]
            report.append(f"  {i}. {s['name']}({s['ts_code']}) 调整{s['adjustment_days']}天 {s['signal_type']} 回调{s.get('callback_pct',0):.1f}% 现价{s['latest_close']}")
            if features_str:
                report.append(f"     {features_str}")
        report.append("")

    # ===== 3. AI TOP5 推荐 =====
    report.append("="*70)
    report.append("🌟 AI评选 TOP5 二波机会股")
    report.append("="*70)
    report.append("")

    if top5:
        for i, stock in enumerate(top5, 1):
            # 尝试从technical_candidates中补全name
            name = stock.get('name', '')
            if not name or name == stock.get('ts_code', ''):
                match = next((c for c in technical_candidates if c['ts_code'] == stock.get('ts_code', '')), None)
                if match:
                    name = match['name']

            report.append(f"{'='*60}")
            report.append(f"  #{i}  {name} {stock.get('ts_code', '')}")
            report.append(f"  {'='*60}")

            adj = stock.get('adjustment_days', '')
            sig = stock.get('signal_type', '')
            entry = stock.get('entry_price', '')
            stop = stock.get('stop_loss', '')
            space = stock.get('space_analysis', '')
            logic = stock.get('logic', '')

            report.append(f"  调整天数: {adj}")
            report.append(f"  信号类别: {sig}")
            report.append(f"  介入价格: {entry}")
            report.append(f"  止损价格: {stop}")
            report.append(f"  空间分析: {space}")
            report.append(f"  逻辑分析: {logic}")
            report.append("")

        # 简版列表
        report.append(f"{'='*60}")
        report.append("📌 TOP5 速览")
        report.append(f"{'='*60}")
        for i, stock in enumerate(top5, 1):
            name = stock.get('name', stock.get('ts_code', ''))
            report.append(f"  {i}. {name} {stock.get('entry_price','')} → {stock.get('stop_loss','')}(止损) {stock.get('space_analysis','')}")
    else:
        report.append("  ⚠ 无AI推荐结果（请检查DeepSeek API配置）")
        report.append("")

    # ===== 4. 风险提示 =====
    report.append("")
    report.append("-"*60)
    report.append("⚠️ 风险提示")
    report.append("  • 热点切换快，注意节奏，控制仓位30%以内")
    report.append("  • 严格按止损价执行，亏损超过5%果断离场")
    report.append("  • 免责声明：仅供学习参考，不构成投资建议")
    report.append("-"*60)

    return '\n'.join(report)


def build_wechat_push_content(top5, candidates):
    """生成简洁的微信推送内容，只展示AI TOP5个股"""
    lines = []
    lines.append("🌟 AI精选 TOP5")
    lines.append("")

    # 通过候选列表补全股票名称
    name_map = {c['ts_code']: c['name'] for c in candidates}

    for i, stock in enumerate(top5, 1):
        ts_code = stock.get('ts_code', '')
        name = stock.get('name', '') or name_map.get(ts_code, '')
        entry = stock.get('entry_price', '')
        stop = stock.get('stop_loss', '')
        space = stock.get('space_analysis', '')
        logic = stock.get('logic', '')

        lines.append(f"─" * 35)
        lines.append(f"  #{i}  {name}  {ts_code}")
        lines.append(f"  介入: {entry}  止损: {stop}")
        if space:
            lines.append(f"  空间: {space}")
        if logic:
            lines.append(f"  逻辑: {logic}")

    lines.append("")
    lines.append("─" * 35)
    lines.append("⚠️ 严格止损，仅供参考")

    return '\n'.join(lines)


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
    """每日涨停跟踪主函数 - 简化版：纯技术面筛选 + AI批量分析TOP5"""
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
    
    # 5. 复盘前20天涨停股票（纯技术面筛选）
    print("\n[5/6] 复盘前20天涨停股票（技术面筛选）...")
    
    start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=20)).strftime('%Y%m%d')
    recent_stocks, stock_history = get_stocks_zt_in_range(
        start_date,
        (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d'),
        lookback_days=19
    )
    
    print(f"前19天涨停过的股票总数（排除当天）: {len(recent_stocks)}")
    
    # 技术面筛选：分析每只股票的企稳信号和放量突破信号
    technical_candidates = []  # 传给AI的技术面候选
    for ts_code in recent_stocks[:50]:
        zt_history = stock_history.get(ts_code, [])
        if not zt_history:
            continue
        
        # 获取股票名称/行业
        name = ts_code
        industry = ""
        try:
            basic = _get_stock_basic_df(ts_code)
            if basic is not None and not basic.empty:
                name = basic.iloc[0].get('name', ts_code)
                industry = basic.iloc[0].get('industry', '')
                name_upper = name.upper()
                if 'ST' in name_upper or '*' in name_upper or '退市' in name_upper:
                    continue
        except:
            pass
        
        # 获取日线数据
        daily_start = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
        daily_df = get_stock_daily_data(ts_code, daily_start, trade_date)
        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            continue
        
        daily_df = daily_df.sort_values('trade_date').reset_index(drop=True)
        latest_price = daily_df['close'].iloc[-1]

        # ═══ 兜底过滤：当天极端涨跌一律排除 ═══
        latest_chg = daily_df['pct_chg'].iloc[-1] if 'pct_chg' in daily_df.columns else 0
        if abs(latest_chg) >= 9.0:
            continue
        # 当天大涨>5%或大跌>7%也不是"涨停后回调"信号
        if latest_chg >= 5.0 or latest_chg <= -7.0:
            continue

        # 如果今天就是涨停日 → 排除
        zt_date = zt_history[0]['date']
        if zt_date == daily_df['trade_date'].iloc[-1]:
            continue

        # 计算涨停后调整天数（交易日）
        adjustment_days = count_trading_days_between(zt_date, trade_date)

        # 计算涨停日收盘价和回调幅度
        zt_row = daily_df[daily_df['trade_date'] == zt_date]
        zt_close = 0
        if not zt_row.empty:
            zt_close = zt_row.iloc[0]['close']
        callback_pct = (zt_close - latest_price) / zt_close * 100 if zt_close > 0 else 0
        
        # ── 企稳信号分析 ──
        stab = analyze_stabilization_signal(daily_df, zt_history)
        stab_score = stab.get('score', 0)
        stab_features = stab.get('features', [])
        
        # ── 放量突破信号分析 ──
        breakout = detect_volume_breakout(daily_df, zt_history)
        has_breakout = breakout.get('is_breakout', False)
        breakout_score = breakout.get('score', 0)
        breakout_type = breakout.get('signal_type', '')
        breakout_detail = breakout.get('detail', '')
        
        # 只要满足任一技术面条件即纳入候选
        if stab_score >= 25 or has_breakout:
            signal_type = ""
            if stab_score >= 25 and has_breakout:
                signal_type = "企稳+突破共振"
            elif stab_score >= 25:
                signal_type = "企稳信号"
            else:
                signal_type = breakout_type
            
            entry_price = round(latest_price, 2)
            
            technical_candidates.append({
                'ts_code': ts_code,
                'name': name,
                'industry': industry,
                'adjustment_days': adjustment_days,
                'signal_type': signal_type,
                'stabilization_score': stab_score,
                'breakout_score': breakout_score,
                'callback_pct': round(callback_pct, 1),
                'latest_close': entry_price,
                'features': stab_features[:3],
                'breakout_detail': breakout_detail
            })
    
    print(f"技术面筛选通过: {len(technical_candidates)} 只（将提交AI分析）")
    
    # 5.5 热点板块数据
    print("\n[5.5/6] 加载热点板块数据...")
    hot_sectors = None
    try:
        hot_sectors = load_hot_sectors_from_db(trade_date)
        if hot_sectors.empty:
            print("  ⚠ 数据库无板块数据，运行block.py分析...")
            original_cwd = os.getcwd()
            os.chdir(MYSTOCK_DIR)
            import block as blk
            hot_sectors = blk.analyze_hot_sectors()
            os.chdir(original_cwd)
    except Exception as e:
        print(f"  ⚠ 加载板块数据失败: {e}")
    
    # 6. AI批量分析
    print("\n[6/6] AI批量分析 - 评选TOP5...")
    if technical_candidates and DEEPSEEK_KEY:
        top5, _ = analyze_with_deepseek_bulk(technical_candidates, hot_sectors, trade_date)
    else:
        top5 = []
        if not DEEPSEEK_KEY:
            print("  ⚠ 未配置DEEPSEEK_KEY，跳过AI分析")
        if not technical_candidates:
            print("  ⚠ 无技术面候选股票")
    
    # 生成报告（保存到文件）
    report = generate_review_report(trade_date, technical_candidates, top5, hot_sectors)
    
    report_file = os.path.join(REVIEW_DIR, f"review_{trade_date}.txt")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"✓ 报告已保存: {report_file}")
    
    # 7. 推送微信（简洁版：只推TOP5）
    print("\n📱 推送微信通知...")
    title = f"📊 涨停二波追踪 {trade_date}"
    push_content = build_wechat_push_content(top5, technical_candidates) if top5 else "今日无AI推荐结果"
    success = send_to_wechat(title, push_content)
    
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


# ============ 新增信号扫描功能 ============

HOT_SECTOR_DB = os.path.join(MYSTOCK_DIR, "cache_daily", "hot_sector.db")


def count_trading_days_between(start_date, end_date):
    """计算两个日期之间的交易日数（含end_date, 不含start_date）"""
    if start_date >= end_date:
        return 0
    count = 0
    d = datetime.strptime(start_date, '%Y%m%d')
    end = datetime.strptime(end_date, '%Y%m%d')
    while d < end:
        d += timedelta(days=1)
        if is_trading_day(d.strftime('%Y%m%d')):
            count += 1
    return count


def load_main_sector_names_from_block_db(trade_date):
    """
    从block.py的分析结果数据库中读取当日TOP板块名称
    用于判断个股是否属于主线板块

    返回:
        set: 板块名称集合
    """
    if not os.path.exists(HOT_SECTOR_DB):
        return set()
    try:
        conn = sqlite3.connect(HOT_SECTOR_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM hot_sector WHERE date=? AND rank<=15 ORDER BY rank ASC", (trade_date,))
        names = {row[0] for row in cursor.fetchall()}
        conn.close()
        return names
    except Exception:
        return set()


def load_hot_sectors_from_db(trade_date):
    """
    从block.py的分析结果数据库中读取当日热门板块
    避免重新运行analyze_hot_sectors()（很慢）

    返回:
        pd.DataFrame: 与 analyze_hot_sectors() 相同格式, 失败则返回空DataFrame
    """
    if not os.path.exists(HOT_SECTOR_DB):
        print(f"⚠ block板块数据库不存在: {HOT_SECTOR_DB}")
        return pd.DataFrame()

    try:
        conn = sqlite3.connect(HOT_SECTOR_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM hot_sector WHERE date=?", (trade_date,))
        count = cursor.fetchone()[0]
        if count == 0:
            print(f"⚠ {trade_date} 无板块数据，请先运行 block.py")
            conn.close()
            return pd.DataFrame()

        cursor.execute("""
            SELECT rank, type, name, score, leader_code, leader_name, leader_score, momentum, acc, retreat
            FROM hot_sector WHERE date=? ORDER BY rank ASC
        """, (trade_date,))
        rows = cursor.fetchall()
        conn.close()

        records = []
        for r in rows:
            records.append({
                "类型": r[1],
                "主线": r[2],
                "主线强度": r[3],
                "龙头代码": r[4],
                "龙头名称": r[5],
                "龙头强度": r[6],
                "动量": r[7],
                "加速度": r[8],
                "是否退潮": bool(r[9])
            })

        df = pd.DataFrame(records)
        print(f"✅ 从数据库加载 {len(df)} 个板块数据 ({trade_date})")
        return df

    except Exception as e:
        print(f"⚠ 读取板块数据库失败: {e}")
        return pd.DataFrame()

def detect_volume_breakout(daily_df, zt_history):
    """
    检测放量突破信号 - 涨停回调后的首日放量上攻

    判断逻辑:
    1. 涨停后至少调整3天
    2. 今天不是涨停/跌停/大涨大跌日
    3. 今日涨幅 > 3% 且量比 > 1.5（首日放量上攻）
    4. 或今日放量突破5日线（量比 > 2.0 且站上MA5）

    参数:
        daily_df: 日线数据（已排序）
        zt_history: 涨停历史

    返回:
        dict: 信号分析结果
    """
    try:
        if daily_df is None or daily_df.empty or len(daily_df) < 10:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': '数据不足'}

        daily_df = daily_df.sort_values('trade_date').copy()
        recent = daily_df.tail(10)
        latest = recent.iloc[-1]
        latest_close = latest['close']
        latest_chg = latest.get('pct_chg', 0)

        # ═══ 核心过滤：今天的表现 ═══
        # 涨停/跌停 → 不是突破信号（涨停本身就是极限，跌停是崩溃）
        if latest_chg >= 9.0:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': f'当天涨停(+{latest_chg:.1f}%)，已到极限'}
        if latest_chg <= -9.0:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': f'当天跌停({latest_chg:.1f}%)，破位'}
        # 涨幅过大不是突破（已接近涨停，次日接力困难）
        if latest_chg >= 7.0:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': f'当天大涨(+{latest_chg:.1f}%)，非突破启动'}
        # 跌幅过大不是突破（破位了）
        if latest_chg <= -5.0:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': f'当天大跌({latest_chg:.1f}%)，破位下行'}

        # 计算均线
        daily_df['ma5'] = daily_df['close'].rolling(5).mean()
        daily_df['ma10'] = daily_df['close'].rolling(10).mean()
        daily_df['ma20'] = daily_df['close'].rolling(20).mean()

        latest_ma5 = daily_df['ma5'].iloc[-1]
        latest_ma10 = daily_df['ma10'].iloc[-1]

        # 成交量分析：今天量 vs 之前5日均量
        vol = recent['vol'].values
        avg_vol_5 = vol[-5:].mean()
        avg_vol_prev5 = vol[-10:-5].mean() if len(vol) >= 10 else avg_vol_5
        today_vol_ratio = latest['vol'] / avg_vol_prev5 if avg_vol_prev5 > 0 else 1

        # 距今涨停天数（交易日）
        days_since_zt = 99
        if zt_history:
            last_zt_date = zt_history[0]['date']
            # 如果今天就是涨停日 → 排除
            if last_zt_date == daily_df['trade_date'].iloc[-1]:
                return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': '今天刚涨停，需等待回调'}
            try:
                from datetime import datetime as dt
                zt_dt = dt.strptime(last_zt_date, '%Y%m%d')
                today_dt = dt.strptime(daily_df['trade_date'].iloc[-1], '%Y%m%d')
                days_since_zt = (today_dt - zt_dt).days
            except:
                pass

        # 涨停后至少调整3天
        if days_since_zt < 3:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': f'涨停后仅{days_since_zt}天，调整不足'}

        score = 0
        signals = []

        # ── 信号A：首日放量上攻 ──
        if 3 < latest_chg < 7 and today_vol_ratio >= 1.5:
            score += 40
            signals.append(f"首日放量上攻(+{latest_chg:.1f}%,量比{today_vol_ratio:.1f})")

        # ── 信号B：放量突破5日线 ──
        if latest_close > latest_ma5 and today_vol_ratio >= 2.0:
            score += 35
            signals.append(f"放量突破5日线(量比{today_vol_ratio:.1f})")

        # ── 信号C：放量突破10日线 ──
        if latest_close > latest_ma10 and today_vol_ratio >= 2.5:
            score += 30
            signals.append(f"放量突破10日线(量比{today_vol_ratio:.1f})")

        # ── 信号D：温和放量站上MA5 ──
        if 1.3 <= today_vol_ratio < 2.0 and latest_close > latest_ma5 and 2 < latest_chg < 5:
            score += 25
            signals.append(f"温和放量站上MA5(+{latest_chg:.1f}%)")

        # ── 信号E：量价齐升突破前高 ──
        if len(recent) >= 5:
            high_5 = recent['high'].iloc[-5:].max()
            if latest_close >= high_5 * 0.99 and today_vol_ratio >= 1.8 and latest_chg < 7:
                score += 30
                signals.append("放量突破近期高点")

        # 涨停后回调幅度合理（10-25%最佳）
        if zt_history:
            zt_close = zt_history[0].get('close', 0)
            if zt_close > 0:
                callback = (zt_close - latest_close) / zt_close * 100
                if 8 <= callback <= 22:
                    score += 15
                elif 3 <= callback < 8:
                    score += 8

        if not signals:
            return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': '无放量突破信号'}

        signal_type = signals[0]
        if score >= 50:
            level = "强信号"
        elif score >= 30:
            level = "中信号"
        else:
            level = "弱信号"

        return {
            'is_breakout': True,
            'score': min(score, 100),
            'signal_type': signal_type,
            'detail': '; '.join(signals),
            'level': level,
            'vol_ratio': today_vol_ratio,
            'days_since_zt': days_since_zt,
            'latest_chg': latest_chg,
            'latest_close': latest_close
        }
    except Exception as e:
        return {'is_breakout': False, 'score': 0, 'signal_type': '', 'detail': f'分析失败: {str(e)}'}


def scan_stock_signals(end_date=None, max_stocks=200):
    """
    扫描过去20天涨停股票，找出企稳信号和放量突破信号
    
    参数:
        end_date: 截止日期(YYYYMMDD)，默认今天
        max_stocks: 最多扫描股票数
        
    返回:
        dict: 扫描结果
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    
    if not is_trading_day(end_date):
        end_date = get_last_n_trading_days(1)[0] if get_last_n_trading_days(1) else end_date
    
    print("="*70)
    print(f"🔍 涨停后信号扫描 | {end_date}")
    print("="*70)
    
    # 1. 从DB获取过去20天涨停股票
    start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=25)).strftime('%Y%m%d')
    recent_stocks, stock_history = get_stocks_zt_in_range(start_date, end_date, lookback_days=20)
    
    if not recent_stocks:
        print("⚠ 过去20天无涨停记录，请先运行 --backtrack")
        return {}
    
    print(f"过去20天涨停过的股票: {len(recent_stocks)} 只（扫描前{max_stocks}只）")
    
    # 2. 逐只扫描信号
    results = {
        'stabilization': [],   # 企稳信号
        'volume_breakout': [], # 放量突破
        'total_scanned': 0
    }
    
    scanned = 0
    for ts_code in recent_stocks[:max_stocks]:
        scanned += 1
        zt_history = stock_history.get(ts_code, [])
        if not zt_history:
            continue
        
        # 获取日线数据
        daily_start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
        daily_df = get_stock_daily_data(ts_code, daily_start, end_date)
        if daily_df is None or daily_df.empty or len(daily_df) < 20:
            continue
        
        daily_df = daily_df.sort_values('trade_date').reset_index(drop=True)
        
        # 获取名称
        name = ts_code
        try:
            basic = _get_stock_basic_df(ts_code)
            if basic is not None and not basic.empty:
                name = basic.iloc[0].get('name', ts_code)
                name_upper = name.upper()
                if 'ST' in name_upper or '*' in name_upper or '退市' in name_upper:
                    continue
        except:
            pass
        
        # ── 企稳信号分析 ──
        stab = analyze_stabilization_signal(daily_df, zt_history)
        if stab.get('score', 0) >= 25:
            latest_price = daily_df['close'].iloc[-1]
            zt_close = zt_history[0].get('close', 0) if zt_history else 0
            callback = (zt_close - latest_price) / zt_close * 100 if zt_close > 0 else 0
            
            results['stabilization'].append({
                'ts_code': ts_code,
                'name': name,
                'score': stab['score'],
                'features': stab['features'],
                'description': stab['description'],
                'callback': callback,
                'latest_close': latest_price
            })
        
        # ── 放量突破信号分析 ──
        breakout = detect_volume_breakout(daily_df, zt_history)
        if breakout.get('is_breakout', False):
            results['volume_breakout'].append({
                'ts_code': ts_code,
                'name': name,
                'score': breakout['score'],
                'level': breakout.get('level', ''),
                'signal_type': breakout['signal_type'],
                'detail': breakout['detail'],
                'vol_ratio': breakout.get('vol_ratio', 0),
                'latest_chg': breakout.get('latest_chg', 0),
                'days_since_zt': breakout.get('days_since_zt', 0),
                'latest_close': breakout.get('latest_close', 0)
            })
        
        if scanned % 20 == 0:
            print(f"  已扫描 {scanned}/{min(len(recent_stocks), max_stocks)} 只..."
                  f" 企稳{len(results['stabilization'])} 突破{len(results['volume_breakout'])}")
    
    results['total_scanned'] = scanned
    print(f"\n✅ 扫描完成: 扫描{scanned}只, "
          f"企稳信号{len(results['stabilization'])}只, "
          f"放量突破{len(results['volume_breakout'])}只")
    
    # 排序
    results['stabilization'].sort(key=lambda x: x['score'], reverse=True)
    results['volume_breakout'].sort(key=lambda x: x['score'], reverse=True)
    
    return results


def print_signal_scan_report(results):
    """打印信号扫描报告"""
    if not results or not results.get('stabilization') and not results.get('volume_breakout'):
        print("\n⚠ 未扫描到任何信号")
        return
    
    print("\n" + "="*70)
    print("📋 涨停后信号扫描报告")
    print("="*70)
    print(f"扫描总数: {results.get('total_scanned', 0)} 只")
    print(f"企稳信号: {len(results['stabilization'])} 只")
    print(f"放量突破: {len(results['volume_breakout'])} 只")
    
    # ── 企稳信号 ──
    stab = results['stabilization']
    if stab:
        print("\n" + "-"*70)
        print(f"🟢 一、企稳信号 (共{len(stab)}只, 展示前20)")
        print("-"*70)
        
        # 按信号特征分组显示
        for i, s in enumerate(stab[:20], 1):
            features_str = " | ".join(s['features'][:3])
            callback_str = f"回调{s['callback']:.1f}%" if s['callback'] != 0 else ""
            print(f"\n{i:2d}. {s['name']:8s}({s['ts_code']}) 评分{s['score']:.0f} {callback_str}")
            print(f"    信号: {features_str}")
    
    # ── 放量突破 ──
    breakout = results['volume_breakout']
    if breakout:
        print("\n" + "-"*70)
        print(f"🔴 二、放量突破 (共{len(breakout)}只, 展示前20)")
        print("-"*70)
        
        # 按信号强弱分组
        strong = [b for b in breakout if b.get('level') == '强信号']
        medium = [b for b in breakout if b.get('level') == '中信号']
        weak = [b for b in breakout if b.get('level') == '弱信号']
        
        def print_breakout_group(label, items):
            if items:
                print(f"\n  [{label}]")
                for s in items[:10]:
                    detail = s['detail'][:60]
                    print(f"    {s['name']:8s}({s['ts_code']}) 评分{s['score']:.0f} "
                          f"涨幅+{s['latest_chg']:.1f}% 量比{s['vol_ratio']:.1f} 调整{s['days_since_zt']}天")
                    print(f"      {detail}")
        
        print_breakout_group("🔥 强信号", strong)
        print_breakout_group("📈 中信号", medium)
        print_breakout_group("🔸 弱信号", weak)
    
    # ── 汇总推荐 ──
    print("\n" + "="*70)
    print("🎯 综合推荐（企稳+突破信号共振）")
    print("="*70)
    ts_codes_stab = {s['ts_code'] for s in stab}
    ts_codes_break = {b['ts_code'] for b in breakout}
    both = ts_codes_stab & ts_codes_break
    
    if both:
        for ts_code in both:
            s = next((x for x in stab if x['ts_code'] == ts_code), None)
            b = next((x for x in breakout if x['ts_code'] == ts_code), None)
            if s and b:
                print(f"  {s['name']:8s}({ts_code}) 企稳{s['score']:.0f}+突破{b['score']:.0f}")
    else:
        # 各自取前3作为推荐
        print("  [企稳信号 TOP 3]")
        for s in stab[:3]:
            print(f"  {s['name']:8s}({s['ts_code']}) 评分{s['score']:.0f} {' | '.join(s['features'][:2])}")
        print("  [放量突破 TOP 3]")
        for b in breakout[:3]:
            print(f"  {b['name']:8s}({b['ts_code']}) 评分{b['score']:.0f} {b['detail'][:50]}")
    
    print(f"\n{'='*70}")
    print("信号扫描完成")
    print(f"{'='*70}")


# ============ 信号扫描功能结束 ============


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
    parser.add_argument('--scan-signals', action='store_true',
                       help='扫描涨停后企稳/突破信号')
    
    args = parser.parse_args()
    
    # 初始化数据库
    init_sqlite_db()
    
    # 清理缓存模式
    if args.clear_cache:
        clear_cache()
        sys.exit(0)
    
    # 信号扫描模式
    if args.scan_signals:
        init_sqlite_db()
        end = args.trade_date or args.end_date or datetime.now().strftime('%Y%m%d')
        results = scan_stock_signals(end_date=end, max_stocks=200)
        print_signal_scan_report(results)
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
