#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题中军选股程序 - 趋势中军池策略
筛选逻辑：只做中军选股，必须满足以下条件
1. 主线容量中军：主题类型为中期趋势或短线主线
2. avg_amount_20 >= 15亿：20日平均成交额不低于15亿元
3. close > MA5 > MA10 > MA20：均线多头排列
4. MA20向上：10日均线斜率为正
5. close >= HHV60 * 0.95：接近60日新高
6. RS20 >= 5：个股20日涨幅 - 主题20日涨幅 >= 5
7. 20日涨停数 <= 2：近20日内涨停次数不超过2次
8. 近5日未跌破MA10：最近5日最低价未跌破10日均线
综合评分公式：score = 0.35 * theme_score + 0.25 * trend_score + 0.20 * RS20_score + 0.20 * amount_score

最终输出：TOP10 趋势中军
"""
import sys

# Windows GBK 控制台输出修复:安全方式（Python 3.7+）
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import time
import sqlite3
import numpy as np
import pandas as pd
import tushare as ts
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

# 导入高级补涨中军检测器
from advanced_buzhang_analysis import AdvancedBuzhangDetector

# =================
# 环境配置
# =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)

# Patch tushare path issue
original_expanduser = os.path.expanduser
os.path.expanduser = lambda path: original_expanduser(path).replace('\\', '/')

# 加载环境变量（优先从根目录config读取）
env_paths = [
    os.path.join(parent_dir, 'config', '.env'),
    os.path.join(BASE_DIR, '.env'),
]
for env_path in env_paths:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')
DAILY_CACHE_DIR = r"d:\mystock\cache_daily"
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# 初始化Tushare
pro = ts.pro_api(TS_TOKEN)

# =================
# DataFetcher 统一缓存接入
# =================
sys.path.insert(0, os.path.join(BASE_DIR, 'multi_factor_picker'))
from data_fetcher import DataFetcher

_df_singleton = None
def _get_df():
    global _df_singleton
    if _df_singleton is not None:
        return _df_singleton
    try:
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            env_path = os.path.join(BASE_DIR, '.env')
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('TUSHARE_TOKEN=') and not line.startswith('#'):
                            token = line.split('=', 1)[1].strip()
                            break
        if not token:
            return None
        config = {'cache': {'enabled': True, 'dir': os.path.join(BASE_DIR, 'multi_factor_picker', 'cache'), 'expire_hours': 168}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
        _df_singleton = DataFetcher(token, config)
    except Exception:
        return None
    return _df_singleton

# =========================
# 获取最近交易日
# =========================

def get_last_trade_date():
    """获取最近的交易日"""

    now = datetime.now()

    # =========================
    # 9点前：视为上一自然日
    # =========================
    if now.hour < 15:

        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')

    else:

        query_date = now.strftime('%Y%m%d')

    # =========================
    # 如果没有 DataFetcher，根据当前时间计算交易日
    # =========================
    _df = _get_df()
    if _df is None:
        # 简单处理：跳过周末
        from datetime import date
        d = date.today()
        if d.weekday() == 5:  # 周六
            d = d - timedelta(days=1)
        elif d.weekday() == 6:  # 周日
            d = d - timedelta(days=2)
        return d.strftime('%Y%m%d')

    # =========================
    # 获取交易日历
    # =========================
    cal = _df.get_trade_cal(start_date='20200101', end_date=query_date)

    # 只保留开市日
    cal = cal[cal['is_open'] == 1]

    # 最近交易日
    last_trade_date = cal[
        cal['cal_date'] <= query_date
    ]['cal_date'].max()

    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
dt = datetime.strptime(TRADE_DATE, '%Y%m%d')
start_dt = dt - timedelta(days=90)
START_DATE = start_dt.strftime('%Y%m%d')

print(f"[Init] 交易日期: {TRADE_DATE}  K线区间: {START_DATE} ~ {TRADE_DATE}")

# =================
# 工具函数
# =================
def cache_get(key):
    """读取缓存"""
    cache_file = os.path.join(CACHE_DIR, f'{key}.pkl')
    if os.path.exists(cache_file):
        return pd.read_pickle(cache_file)
    return None

def cache_save(key, data):
    """保存缓存"""
    cache_file = os.path.join(CACHE_DIR, f'{key}.pkl')
    data.to_pickle(cache_file)

def is_same_day(d1, d2):
    return str(d1)[:8] == str(d2)[:8]

# =================
# 步骤1：获取主题分析数据
# =================
def get_theme_data():
    """从theme_trend_sentiment_score获取主题数据"""
    import theme_trend_sentiment_score as theme_score
    
    hot_themes = theme_score.load_theme_json()
    
    # 首先尝试从 SQLite 数据库读取指定日期的数据
    theme_scores = None
    db_path = os.path.join(CACHE_DIR, 'theme_trend_sentiment.db')
    
    if os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            # 查询指定日期的主题数据
            query = "SELECT * FROM theme_scores WHERE trade_date = ?"
            theme_scores = pd.read_sql(query, conn, params=(TRADE_DATE,))
            conn.close()
            
            if not theme_scores.empty:
                print(f"[Theme] 从数据库读取 {TRADE_DATE} 主题数据成功")
                # 重命名列以匹配代码逻辑
                if 'trade_date' in theme_scores.columns:
                    theme_scores = theme_scores.drop(columns=['trade_date'])
                # 检查是否有 t_avg_slope_60 列，如果没有需要计算
                if 't_avg_slope_60' not in theme_scores.columns:
                    # 从数据库获取历史数据计算60日平均
                    try:
                        conn = sqlite3.connect(db_path)
                        # 获取所有历史日期
                        cur = conn.cursor()
                        cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 60")
                        all_dates = [row[0] for row in cur.fetchall()]
                        
                        if len(all_dates) > 0:
                            # 获取这些日期的所有数据
                            placeholders = ','.join(['?' for _ in all_dates])
                            query = f"SELECT theme, trend_score FROM theme_scores WHERE trade_date IN ({placeholders})"
                            all_history = pd.read_sql(query, conn, params=all_dates)
                            
                            # 计算每个主题的平均趋势分
                            theme_avg = all_history.groupby('theme')['trend_score'].mean().reset_index()
                            theme_avg.columns = ['theme', 't_avg_slope_60']
                            
                            # 合并到当前数据
                            theme_scores = pd.merge(theme_scores, theme_avg, on='theme', how='left')
                            print(f"[Theme] 计算得到60日平均趋势分")
                        conn.close()
                    except Exception as e:
                        print(f"[Theme] 计算60日平均失败: {e}")
        except Exception as e:
            print(f"[Theme] 从数据库读取失败: {e}")
    
    # 如果数据库没有，尝试读取带日期的 CSV 文件
    if theme_scores is None or theme_scores.empty:
        theme_scores_file_dated = os.path.join(CACHE_DIR, f'theme_trend_sentiment_{TRADE_DATE}.csv')
        theme_scores_file = os.path.join(CACHE_DIR, 'theme_trend_sentiment.csv')
        
        if os.path.exists(theme_scores_file_dated):
            theme_scores = pd.read_csv(theme_scores_file_dated)
            print(f"[Theme] 从文件读取 {theme_scores_file_dated}")
        elif os.path.exists(theme_scores_file):
            theme_scores = pd.read_csv(theme_scores_file)
            print(f"[Theme] 从文件读取 {theme_scores_file}")
        else:
            print("[Error] 主题分析数据不存在，请先运行 theme_trend_sentiment_score.py")
            return None, None
    
    return hot_themes, theme_scores

# =================
# 步骤2：获取成分股和基本面数据
# =================
def get_stock_data(hot_themes):
    """获取成分股列表和基本面数据"""
    import theme_trend_sentiment_score as theme_score

    _df = _get_df()

    # 获取股票列表
    if _df is not None:
        stock_basic = _df.get_stock_list(list_status='L')
    else:
        stock_basic = pd.DataFrame(columns=['ts_code', 'name', 'industry', 'market', 'list_date'])
    stock_basic = stock_basic[~stock_basic['name'].str.contains('ST|退', na=False)].copy()

    # 获取市值数据
    mcap_date = TRADE_DATE
    daily_basic = pd.DataFrame()
    if _df is not None:
        try:
            daily_basic = _df.get_daily_basic(trade_date=mcap_date)
            if daily_basic.empty:
                print(f"   {mcap_date}市值数据为空，尝试获取前几个交易日...")
        except Exception as e:
            print(f"   {mcap_date}市值数据获取失败: {e}，尝试获取前几个交易日...")

        if daily_basic.empty:
            dt_mcap = datetime.strptime(TRADE_DATE, '%Y%m%d')
            for offset in range(1, 10):
                prev_date = (dt_mcap - timedelta(days=offset)).strftime('%Y%m%d')
                try:
                    daily_basic = _df.get_daily_basic(trade_date=prev_date)
                    if not daily_basic.empty:
                        mcap_date = prev_date
                        print(f"   成功获取{mcap_date}的市值数据，共{len(daily_basic)}条")
                        break
                except:
                    continue
            if daily_basic.empty:
                print(f"   警告: 无法获取市值数据，将使用0值")
                daily_basic = pd.DataFrame(columns=['ts_code', 'close', 'pe', 'total_mv', 'circ_mv', 'turnover_rate', 'volume_ratio'])
    else:
        print(f"   警告: DataFetcher 不可用，无法获取市值数据")
        daily_basic = pd.DataFrame(columns=['ts_code', 'close', 'pe', 'total_mv', 'circ_mv', 'turnover_rate', 'volume_ratio'])
    
    print(f"   市值数据: {len(daily_basic)}条，有效市值: {(daily_basic['total_mv'] > 0).sum() if not daily_basic.empty else 0}条")
    
    # 从 JSON 缓存加载主题-个股映射（由 build_theme_stock_map.py 生成）
    theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_score.load_theme_stock_map_from_json()
    
    # 创建市值映射（单位：亿）
    mcap_map = {}
    # 创建换手率映射
    turnover_map = {}
    if not daily_basic.empty:
        for _, row in daily_basic.iterrows():
            ts_code = row['ts_code']
            total_mv = row.get('total_mv', 0)
            if total_mv and total_mv > 0:
                mcap_map[ts_code] = total_mv / 10000  # 万元转换为亿元
            # 获取换手率
            turnover_rate = row.get('turnover_rate', 0)
            if turnover_rate and turnover_rate > 0:
                turnover_map[ts_code] = turnover_rate
    
    return stock_basic, daily_basic, theme_stock_map, name_map_basic, mcap_map, turnover_map

# =================
# 步骤3：获取K线数据并计算指标
# =================
def get_kline_data(stock_codes):
    """获取K线数据（优先读取共享日线缓存，不足时批量补充）"""
    kline_data = {}
    missing_codes = []
    
    # 第一遍：从共享缓存读取
    for code in stock_codes:
        cache_file = os.path.join(DAILY_CACHE_DIR, f'{code}.csv')
        if os.path.exists(cache_file):
            try:
                df_cache = pd.read_csv(cache_file)
                df_cache['trade_date'] = df_cache['trade_date'].astype(str)
                # 只保留目标日期之前的数据
                df_cache = df_cache[df_cache['trade_date'] <= TRADE_DATE].copy()
                if len(df_cache) >= 60:
                    df_cache = df_cache.sort_values('trade_date').reset_index(drop=True)
                    kline_data[code] = df_cache
                    continue
            except Exception:
                pass
        missing_codes.append(code)
    
    # 批量补充缺失的数据
    if missing_codes:
        print(f"  [get_kline_data] 需批量下载 {len(missing_codes)} 只股票的日线数据")
        batch_size = 200
        total_downloaded = 0
        for i in range(0, len(missing_codes), batch_size):
            batch = missing_codes[i:i + batch_size]
            try:
                _df_local = _get_df()
                if _df_local is not None:
                    # 使用 DataFetcher 逐只查询（带统一缓存）
                    _frames = []
                    for _code in batch:
                        _stock_df = _df_local.get_daily_by_code(
                            ts_code=_code,
                            start_date=START_DATE,
                            end_date=TRADE_DATE
                        )
                        if _stock_df is not None and not _stock_df.empty:
                            _frames.append(_stock_df)
                    df = pd.concat(_frames, ignore_index=True) if _frames else pd.DataFrame()
                else:
                    df = pd.DataFrame()
                if df is not None and not df.empty:
                    for ts_code in batch:
                        stock_df = df[df['ts_code'] == ts_code]
                        if not stock_df.empty:
                            stock_df = stock_df.sort_values('trade_date')
                            save_path = os.path.join(DAILY_CACHE_DIR, f"{ts_code}.csv")
                            # 合并已有缓存（保留更早的历史数据）
                            if os.path.exists(save_path):
                                try:
                                    old_df = pd.read_csv(save_path)
                                    old_df['trade_date'] = old_df['trade_date'].astype(str)
                                    stock_df['trade_date'] = stock_df['trade_date'].astype(str)
                                    merged = pd.concat([old_df, stock_df], ignore_index=True)
                                    merged = merged.drop_duplicates(subset=['trade_date'], keep='last')
                                    merged = merged.sort_values('trade_date').reset_index(drop=True)
                                    merged.to_csv(save_path, index=False)
                                except Exception:
                                    stock_df.to_csv(save_path, index=False)
                            else:
                                stock_df.to_csv(save_path, index=False)
                    total_downloaded += len(df['ts_code'].unique())
                time.sleep(0.1)
            except Exception as e:
                print(f"  [get_kline_data] 批次下载失败: {e}")
        print(f"  [get_kline_data] 批量下载完成，共 {total_downloaded} 只")
        
        # 第二遍：从缓存读取之前缺失的代码
        for code in missing_codes:
            if code in kline_data:
                continue
            cache_file = os.path.join(DAILY_CACHE_DIR, f'{code}.csv')
            if os.path.exists(cache_file):
                try:
                    df_cache = pd.read_csv(cache_file)
                    df_cache['trade_date'] = df_cache['trade_date'].astype(str)
                    df_cache = df_cache[df_cache['trade_date'] <= TRADE_DATE].copy()
                    if len(df_cache) >= 60:
                        df_cache = df_cache.sort_values('trade_date').reset_index(drop=True)
                        kline_data[code] = df_cache
                except Exception:
                    pass
    
    return kline_data

# =================
# 步骤4：计算技术指标和筛选
# =================
def calculate_and_filter(theme_stock_map, kline_data, hot_themes, theme_scores, name_map_basic, mcap_map, turnover_map):
    """计算技术指标并筛选股票"""
    final_candidates = []
    good_themes = []
    
    for theme_name, stock_info in theme_stock_map.items():
        if theme_name not in hot_themes:
            continue
        
        theme_cfg = hot_themes[theme_name]
        
        # 获取该主题趋势评分和状态
        theme_score_val = 0
        theme_state = "弱势"
        if not theme_scores.empty and len(theme_scores[theme_scores['theme'] == theme_name]) > 0:
            theme_row = theme_scores[theme_scores['theme'] == theme_name].iloc[0]
            theme_score_val = theme_row['composite_score']
            theme_state = theme_row.get('theme_state', '弱势')
        
        # 可交易状态：强趋势、震荡
        tradeable_states = {"强趋势", "震荡"}
        
        if theme_state not in tradeable_states:
            continue
        
        print(f"\n   【{theme_name}】({theme_state}):")
        
        theme_codes = list(stock_info.keys())
        all_scored = []
        
        # 计算该主题涨跌幅作为基准
        theme_close_list = []
        for code in theme_codes:
            if code in kline_data:
                df = kline_data[code]
                if len(df) >= 20:
                    df_sorted = df.sort_values('trade_date')
                    closes = df_sorted['close'].astype(float).values
                    if len(closes) >= 20:
                        theme_close_list.append(closes[-20:])
        
        theme_hhv_list = [max(closes) for closes in theme_close_list if len(closes) >= 20]
        if theme_hhv_list:
            theme_hhv_avg = np.mean(theme_hhv_list)
        else:
            theme_hhv_avg = 1
        
        # 处理每只股票
        for code in theme_codes:
            if code not in kline_data:
                continue
            
            df = kline_data[code]
            if len(df) < 60:
                continue
            
            df_sorted = df.sort_values('trade_date')
            closes = df_sorted['close'].astype(float).values
            vols = df_sorted['vol'].astype(float).values
            amounts = df_sorted['amount'].astype(float).values
            pct_changes = df_sorted['pct_chg'].astype(float).values
            
            # 计算均线
            ma5 = pd.Series(closes).rolling(5).mean().values[-1]
            ma10 = pd.Series(closes).rolling(10).mean().values[-1]
            ma20 = pd.Series(closes).rolling(20).mean().values[-1]
            ma5_vals = pd.Series(closes).rolling(5).mean().values
            ma10_vals = pd.Series(closes).rolling(10).mean().values
            ma20_vals = pd.Series(closes).rolling(20).mean().values
            
            # 基础数据
            close = closes[-1]
            pct_today = pct_changes[-1] if len(pct_changes) > 0 else 0
            
            # 获取市值数据和换手率
            mcap = mcap_map.get(code, 0)
            name = name_map_basic.get(code, code)
            turnover = turnover_map.get(code, 0)
            
            # 计算技术指标
            avg_amount_20 = amounts[-20:].mean() / 100000 if len(amounts) >= 20 else 0
            hhv60 = max(closes[-60:]) if len(closes) >= 60 else closes[-1]
            
            # 计算RS20（相对强度）
            stock_ret_20 = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 21 else 0
            
            # 简化处理，这里省略详细RS计算
            RS20 = stock_ret_20
            
            # 计算MA20斜率
            ma20_slope = (ma20 - ma20_vals[-5]) / ma20_vals[-5] * 100 if len(ma20_vals) > 5 and ma20_vals[-5] > 0 else 0
            
            # 检查20日涨停数
            zt_count_20 = sum(1 for x in pct_changes[-20:] if x >= 9.5)
            
            # 检查近5日最低价是否跌破MA10
            ma10_broken = False
            if len(ma10_vals) >= 6 and len(df_sorted) >= 6:
                lows = df_sorted['low'].astype(float).values
                for i in range(-5, 0):
                    if lows[i] < ma10_vals[i]:
                        ma10_broken = True
                        break
            
            # =================
            # 中军筛选条件（改为分数制）
            # =================
            # 总分100分，60分以上为中军
            total_score = 0
            
            # 1. 主题状态（20分）- 根据状态给予不同分数
            if theme_state == "强趋势":
                total_score += 18
            elif theme_state == "震荡":
                total_score += 14
            else:
                total_score += 10  # 其他可交易状态
            
            # 2. 日均成交额（20分）- 放宽到8亿
            if avg_amount_20 >= 15:
                total_score += 20
            elif avg_amount_20 >= 8:
                total_score += 15
            elif avg_amount_20 >= 5:
                total_score += 10
            else:
                total_score += 5  # 允许较低成交额但扣分
            
            # 3. 均线多头（15分）
            if close > ma5 and ma5 > ma10 and ma10 > ma20:
                total_score += 15  # 完美多头
            elif close > ma20:
                total_score += 10  # 至少在20日均线上
            else:
                total_score += 5   # 低于20日均线但仍考虑
            
            # 4. MA20向上（15分）
            if ma20_slope > 1:
                total_score += 15
            elif ma20_slope > 0:
                total_score += 10
            elif ma20_slope > -1:
                total_score += 5    # 轻微下降也允许
            
            # 5. 接近新高（15分）- 放宽到80%
            if close >= hhv60 * 0.95:
                total_score += 15
            elif close >= hhv60 * 0.90:
                total_score += 12
            elif close >= hhv60 * 0.85:
                total_score += 8
            elif close >= hhv60 * 0.80:
                total_score += 5     # 放宽到80%
            
            # 6. RS20相对强度（10分）- 大幅放宽，允许负值
            if RS20 >= 5:
                total_score += 10
            elif RS20 >= 2:
                total_score += 8
            elif RS20 >= 0:
                total_score += 5
            elif RS20 >= -5:
                total_score += 2     # 小幅跑输也允许
            else:
                total_score += 0
            
            # 7. 20日涨停数（3分）
            if zt_count_20 == 0:
                total_score += 3
            elif zt_count_20 == 1:
                total_score += 2
            elif zt_count_20 == 2:
                total_score += 1
            
            # 8. 近5日未跌破MA10（2分）
            if not ma10_broken:
                total_score += 2
            
            # 判断是否为中军（60分阈值）
            is_zhongjun = total_score >= 60
            
            # 判断主题类型（中期趋势 vs 短线主线）
            # 基于60日平均趋势分，大于50分为中期趋势，否则为短线主线
            is_mid_trend = False
            if theme_scores is not None and not theme_scores.empty:
                theme_row = theme_scores[theme_scores['theme'] == theme_name]
                if not theme_row.empty:
                    t_avg_slope_60 = theme_row.iloc[0].get('t_avg_slope_60', 0)
                    is_mid_trend = t_avg_slope_60 >= 50
            
            # 计算中军综合评分（用于排序）
            zhongjun_score = 0
            if is_zhongjun:
                # 主题分
                theme_score_part = min(theme_score_val / 80 * 100, 100)
                # 趋势分
                trend_score_part = min(ma20_slope / 2 * 100, 100)
                # RS20分（放宽后的计算）
                RS20_score_part = min(max(RS20 / 10 * 100, 0), 100)
                # 成交分
                amount_score_part = min(avg_amount_20 / 30 * 100, 100)
                
                zhongjun_score = (
                    0.35 * theme_score_part +
                    0.25 * trend_score_part +
                    0.20 * RS20_score_part +
                    0.20 * amount_score_part
                )
            
            # 保存股票数据
            stock_dict = {
                'code': code,
                'name': name,
                'close': close,
                'pct_chg': pct_today,
                'turnover_rate': turnover,
                'mcap': mcap,
                'avg_amount_20': avg_amount_20,
                'RS20': RS20,
                'ma20_slope': ma20_slope,
                'ma5': ma5,
                'ma10': ma10,
                'ma20': ma20,
                'theme_name': theme_name,
                'theme_type': '中期趋势' if is_mid_trend else '短线主线',
                'theme_state': theme_state,
                'theme_score': theme_score_val,
                'is_zhongjun': is_zhongjun,
                'final_score': zhongjun_score,
                'has_real_mcap': mcap > 0
            }
            all_scored.append(stock_dict)
        
        # 按评分排序
        all_scored.sort(key=lambda x: -x.get('final_score', 0))
        
        # =================
        # 选择中军
        # =================
        zhongjun_candidates = []
        for s in all_scored[:10]:  # 取前10名
            if s['is_zhongjun']:
                # 生成推荐理由
                reason_parts = []
                if s['RS20'] >= 10:
                    reason_parts.append(f"RS强势({s['RS20']:.1f})")
                elif s['RS20'] >= 5:
                    reason_parts.append(f"RS强势({s['RS20']:.1f})")
                
                if s['close'] >= hhv60 * 0.98:
                    reason_parts.append("逼近新高")
                elif s['close'] >= hhv60 * 0.95:
                    reason_parts.append("接近新高")
                
                if s['ma20_slope'] > 1:
                    reason_parts.append(f"MA20向上({s['ma20_slope']:.2f}%)")
                
                if s['avg_amount_20'] >= 20:
                    reason_parts.append(f"成交活跃({s['avg_amount_20']:.0f}亿)")
                elif s['avg_amount_20'] >= 15:
                    reason_parts.append(f"成交活跃({s['avg_amount_20']:.0f}亿)")
                
                reason_detail = "; ".join(reason_parts) if reason_parts else "趋势中军"
                
                s['buy_type'] = '中军'
                s['buy_type_detail'] = '趋势中军'
                s['reason'] = reason_detail
                zhongjun_candidates.append(s)
        
        # =================
        # 第四步：补涨中军筛选（使用高级形态识别算法）
        # =================
        buzhang_pool = []
        buzhang_detector = AdvancedBuzhangDetector()
        
        # 获取该主题的核心公司列表（用于豁免成交额要求）
        core_companies = theme_cfg.get('core_companies', [])
        
        # 获取该主题的中军（用于相对强度分析）
        theme_zhongjun_codes = [s['code'] for s in zhongjun_candidates]
        zhongjun_data_dict = {}
        for zj_code in theme_zhongjun_codes:
            cache_file = os.path.join(DAILY_CACHE_DIR, f"{zj_code}.csv")
            if os.path.exists(cache_file):
                zj_df = pd.read_csv(cache_file)
                if len(zj_df) >= 20:
                    zhongjun_data_dict[zj_code] = zj_df
        
        # 调试统计
        debug_stats = {
            'total': 0,
            'no_data': 0,
            'zt_filter': 0,
            'mcap_filter': 0,
            'amount_filter': 0,
            'pct5d_filter': 0,
            'trend_filter': 0,  # 均线趋势过滤
            'score_filter': 0,
            'valid': 0
        }
        
        for s in all_scored:
            code = s['code']
            df = kline_data.get(code)
            debug_stats['total'] += 1
            
            if df is None or len(df) < 25:
                debug_stats['no_data'] += 1
                continue
            
            df_sorted = df.sort_values('trade_date')
            volumes = df_sorted['vol'].astype(float).values
            closes = df_sorted['close'].astype(float).values
            
            # 获取股票名称（用于核心公司判断）
            stock_name = s.get('name', '')
            is_core_company = any(company in stock_name for company in core_companies)
            
            # 基础条件检查（快速筛选）
            # 1. 排除当日涨停股（涨停股不应出现在补涨中军）
            #    核心公司：排除当日涨停即可，不排除近1-2日涨停
            #    普通公司：排除当日涨停
            pct_chg_today = s.get('pct_chg', 0)
            if pct_chg_today >= 9.5:
                debug_stats['zt_filter'] += 1
                continue
            
            # 检查近1日是否有涨停（普通公司排除，核心公司允许）
            if not is_core_company and len(df_sorted) >= 2:
                pct_chg_1d_ago = df_sorted.iloc[-2]['pct_chg']
                if pct_chg_1d_ago >= 9.5:
                    debug_stats['zt_filter'] += 1
                    continue
            
            # 2. 市值限制100-3000亿（放宽上限，核心公司不限制）
            mcap = s.get('mcap', 0)
            if not is_core_company:
                if not mcap or mcap <= 0 or mcap < 100 or mcap > 3000:
                    debug_stats['mcap_filter'] += 1
                    continue
            else:
                if not mcap or mcap <= 0 or mcap < 50:
                    debug_stats['mcap_filter'] += 1
                    continue
            
            # 3. 成交额>=3亿（放宽下限），核心公司豁免此要求
            recent_20 = df_sorted.iloc[-21:-1] if len(df_sorted) >= 21 else df_sorted
            avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000  # tushare amount单位是元，转亿元
            
            if not is_core_company and avg_amount_20 < 3:
                debug_stats['amount_filter'] += 1
                continue
            
            # 4. 排除短期涨幅过大的股票（大幅放宽）
            # 10天内最高价比最低价超过50% -> 过滤（原30%）
            close_today = df_sorted.iloc[-1]['close']  # 保存今日收盘价供后续使用
            if len(df_sorted) >= 10:
                recent_10d = df_sorted.iloc[-10:]
                high_10d = recent_10d['high'].max()
                low_10d = recent_10d['low'].min()
                if low_10d > 0:
                    range_ratio = (high_10d - low_10d) / low_10d * 100
                    if range_ratio > 50:
                        debug_stats['pct5d_filter'] += 1
                        continue
            
            # 排除中期涨幅过大的股票（10日涨幅超过80% -> 过滤，原50%）
            if len(df_sorted) >= 11:
                close_10d_ago = df_sorted.iloc[-11]['close']
                if close_10d_ago > 0:
                    pct_10d = (close_today - close_10d_ago) / close_10d_ago * 100
                    if pct_10d > 80:
                        debug_stats['pct5d_filter'] += 1
                        continue
            
            # 排除长期涨幅过大的股票（20日涨幅超过120% -> 过滤，原80%）
            if len(df_sorted) >= 21:
                close_20d_ago = df_sorted.iloc[-21]['close']
                if close_20d_ago > 0:
                    pct_20d = (close_today - close_20d_ago) / close_20d_ago * 100
                    if pct_20d > 120:
                        debug_stats['pct5d_filter'] += 1
                        continue
            
            # 5. 均线趋势检查（大幅放宽）
            # 核心公司：只要求股价在MA20上方
            # 普通公司：股价站上MA20或MA5，十日线斜率>-0.5%
            if len(closes) >= 25:
                ma5_vals = pd.Series(closes).rolling(5).mean().values
                ma10_vals = pd.Series(closes).rolling(10).mean().values
                ma20_vals = pd.Series(closes).rolling(20).mean().values
                
                close = closes[-1]
                
                if is_core_company:
                    # 核心公司：只需股价在MA20上方，MA20不持续向下
                    ma20 = ma20_vals[-1]
                    if close <= ma20:
                        debug_stats['trend_filter'] += 1
                        continue
                    # MA20斜率
                    if len(ma20_vals) >= 5:
                        ma20_slope = (ma20_vals[-1] - ma20_vals[-5]) / ma20_vals[-5] * 100
                        if ma20_slope < -3:
                            debug_stats['trend_filter'] += 1
                            continue
                else:
                    # 普通公司：股价站上MA5或MA10（任一满足即可）
                    ma5 = ma5_vals[-1]
                    ma10 = ma10_vals[-1]
                    if close <= ma5 and close <= ma10:
                        debug_stats['trend_filter'] += 1
                        continue
                    
                    # 十日线斜率>-0.5%（允许小幅走平）
                    if len(ma10_vals) >= 5:
                        ma10_slope = (ma10_vals[-1] - ma10_vals[-5]) / ma10_vals[-5] * 100
                        if ma10_slope <= -0.5:
                            debug_stats['trend_filter'] += 1
                            continue
                    
                    # MA20不持续向下
                    if len(ma20_vals) >= 5:
                        ma20_slope = (ma20_vals[-1] - ma20_vals[-5]) / ma20_vals[-5] * 100
                        if ma20_slope < -3:
                            debug_stats['trend_filter'] += 1
                            continue
            
            # 使用高级检测器分析
            zhongjun_df = None
            if zhongjun_data_dict:
                first_zj_code = next(iter(zhongjun_data_dict.keys()))
                zhongjun_df = zhongjun_data_dict[first_zj_code]
            
            analysis_result = buzhang_detector.analyze_stock(df_sorted, zhongjun_df, s.get('mcap', 0), s.get('turnover_rate', 0))
            
            if not analysis_result.get('valid', False):
                continue
            
            # 综合评分：核心公司35分通过，普通公司40分通过（原50分）
            overall_score = analysis_result.get('overall_score', 0)
            pass_score = 35 if is_core_company else 40
            if overall_score < pass_score:
                debug_stats['score_filter'] += 1
                continue
            
            debug_stats['valid'] += 1
            
            # 计算量能放大比例用于显示
            vol_ratio = 1.0
            if len(volumes) >= 23:
                recent_vol_avg = volumes[-3:].mean()
                baseline_vol_avg = volumes[-23:-3].mean()
                if baseline_vol_avg > 0:
                    vol_ratio = recent_vol_avg / baseline_vol_avg
            
            # 收集检测到的形态
            detected_patterns = analysis_result.get('detected_patterns', [])
            pattern_descriptions = {
                'big_amount': '大成交额',
                'big_market_cap': '大市值',
                'price_trend': '价格健康',
                'volume_coordination': '量价配合',
                'technicals': '技术面良好'
            }
            
            # 构建补涨中军记录
            buzhang_stock = s.copy()
            buzhang_stock['avg_amount_20'] = round(avg_amount_20, 2)
            # 计算近3天平均成交量
            recent_3_volumes = volumes[-3:] if len(volumes) >= 3 else volumes
            avg_volume_3 = recent_3_volumes.mean() if len(recent_3_volumes) > 0 else 0
            buzhang_stock['avg_volume_3'] = avg_volume_3
            buzhang_stock['vol_ratio'] = round(vol_ratio, 2)
            buzhang_stock['buzhang_score'] = round(overall_score, 2)
            buzhang_stock['final_score'] = round(overall_score, 2)
            buzhang_stock['detected_patterns'] = detected_patterns
            buzhang_stock['pattern_names'] = [
                pattern_descriptions.get(p, p) 
                for p in detected_patterns
            ]
            
            buzhang_pool.append(buzhang_stock)
        
        # 打印调试信息
        print(f"       补涨筛选: 总{debug_stats['total']}只 | 无数据{debug_stats['no_data']} | 涨停{debug_stats['zt_filter']} | "
              f"市值{debug_stats['mcap_filter']} | 成交{debug_stats['amount_filter']} | 涨幅{debug_stats['pct5d_filter']} | "
              f"趋势{debug_stats['trend_filter']} | 评分{debug_stats['score_filter']} | 通过{debug_stats['valid']}只")
        
        # 按3天平均成交量排序
        buzhang_pool.sort(key=lambda x: (-x.get('avg_volume_3', 0), -x.get('buzhang_score', 0)))
        
        # 生成补涨中军候选
        buzhang_candidates = []
        for s in buzhang_pool[:5]:  # 取前5名
            code = s['code']
            
            # 生成买入理由
            reason_parts = []
            
            # 优先显示检测到的形态
            pattern_names = s.get('pattern_names', [])
            if pattern_names:
                reason_parts.extend(pattern_names[:2])  # 最多显示2个形态
            
            if s.get('vol_ratio', 0) >= 1.5:
                reason_parts.append(f"量能放大({s['vol_ratio']:.2f}倍)")
            if s.get('avg_amount_20', 0) >= 15:
                reason_parts.append(f"成交活跃({s['avg_amount_20']:.0f}亿)")
            
            reason_detail = "; ".join(reason_parts) if reason_parts else "补涨中军"
            
            s['buy_type'] = '补涨中军'
            s['buy_type_detail'] = '补涨中军'
            s['reason'] = reason_detail
            buzhang_candidates.append(s)
        
        # 第五步：按成交额优先排序输出（每个主题最多2只中军 + 2只补涨中军）
        selected_codes = set()
        theme_count = 0
        
        # 中军（按综合评分排序取前2只）
        zhongjun_sorted = sorted(zhongjun_candidates, key=lambda x: -x.get('final_score', 0))[:2]
        for candidate in zhongjun_sorted:
            if candidate['code'] not in selected_codes:
                final_candidates.append(candidate)
                selected_codes.add(candidate['code'])
                buy_type_display = candidate.get('buy_type_detail', '中军')
                print(f"     中军: {candidate['name']} (评分{candidate['final_score']:.1f}) - {buy_type_display} - 市值{candidate['mcap']}亿 - {candidate['reason']}")
                theme_count += 1
        
        # 补涨中军（成交额大的优先，取3个，且不与中军重复）
        buzhang_sorted = sorted(buzhang_candidates, key=lambda x: (-x.get('avg_amount_20', 0), -x.get('buzhang_score', 0)))[:3]
        for candidate in buzhang_sorted:
            if candidate['code'] not in selected_codes:
                final_candidates.append(candidate)
                selected_codes.add(candidate['code'])
                print(f"     补涨中军: {candidate['name']} (评分{candidate['buzhang_score']:.1f}) - 市值{candidate['mcap']}亿 - {candidate['reason']}")
                theme_count += 1
        
        if theme_count == 0:
            print(f"     本主题暂未找到符合标准的中军")
        
        # 记录好主题
        if theme_count > 0:
            good_themes.append({
                'name': theme_name,
                'state': theme_state,
                'score': theme_score_val
            })
    
    # 按主题状态排序，方便显示
    def sort_key(stock):
        # 优先级：抱团主升 > 强趋势 > 主升 > 分歧转一致 > 启动 > 其他
        state_order = {
            '抱团主升': 0,
            '强趋势': 1,
            '主升': 2,
            '分歧转一致': 3,
            '启动': 4
        }
        return (
            state_order.get(stock.get('theme_state', ''), 5),
            -stock.get('core_score', 0)
        )
    
    final_candidates.sort(key=sort_key)
    
    return final_candidates, good_themes

# =================
# 输出结果
# =================
def print_results(candidates):
    print("\n" + "=" * 120)
    print("主题中军选股结果 - 趋势中军池策略")
    print("=" * 120)
    
    if not candidates:
        print("   没有符合条件的股票")
        return
    
    print(f"共筛选出 {len(candidates)} 只符合条件的股票\n")
    
    # 按主题状态分组
    state_order = ['强趋势', '震荡']
    state_groups = {}
    for state in state_order:
        state_groups[state] = [c for c in candidates if c.get('theme_state') == state]
    
    # 按状态优先级输出
    for state in state_order:
        state_candidates = state_groups[state]
        if not state_candidates:
            continue
        
        state_icon = {
            '强趋势': '↑',
            '震荡': '↔'
        }.get(state, '')
        
        print(f"{state}{state_icon} 主题")
        print("-" * 120)
        
        zhongjun = [c for c in state_candidates if c.get('buy_type') == '中军']
        buzhang = [c for c in state_candidates if c.get('buy_type') == '补涨中军']
        
        if zhongjun:
            print("中军")
            print(f"{'代码':<14}{'名称':<10}{'主题':<10}{'价格':>8}{'涨跌%':>8}{'换手%':>8}{'市值亿':>10}  {'推荐理由'}")
            print("-" * 100)
            for stock in zhongjun:
                mcap_display = f"{stock['mcap']:.1f}" if stock.get('has_real_mcap', False) else "--"
                close_val = stock.get('close', 0) or 0
                pct_val = stock.get('pct_chg', 0) or 0
                turnover_val = stock.get('turnover_rate', 0) or 0
                theme_val = stock.get('theme_name', '') or ''
                reason_val = stock.get('reason', '') or ''
                print(f"{stock['code']:<14}{stock['name']:<10}{theme_val:<10}{close_val:>8.2f}{pct_val:>8.2f}{turnover_val:>8.2f}{mcap_display:>10}  {reason_val}")
            print()
        
        if buzhang:
            print("补涨中军")
            print(f"{'代码':<14}{'名称':<10}{'主题':<10}{'价格':>8}{'涨跌%':>8}{'换手%':>8}{'市值亿':>10}  {'推荐理由'}")
            print("-" * 100)
            for stock in buzhang:
                mcap_display = f"{stock['mcap']:.1f}" if stock.get('has_real_mcap', False) else "--"
                close_val = stock.get('close', 0) or 0
                pct_val = stock.get('pct_chg', 0) or 0
                turnover_val = stock.get('turnover_rate', 0) or 0
                theme_val = stock.get('theme_name', '') or ''
                reason_val = stock.get('reason', '') or ''
                print(f"{stock['code']:<14}{stock['name']:<10}{theme_val:<10}{close_val:>8.2f}{pct_val:>8.2f}{turnover_val:>8.2f}{mcap_display:>10}  {reason_val}")
            print()
    
    # 标准说明
    print("=" * 120)
    print("趋势中军池标准说明")
    print("=" * 120)
    print("主题状态筛选：只选择可交易状态的主题（强趋势、震荡）")
    print("趋势中军：满足以下分数制条件的个股，按综合评分排序取TOP10")
    print("  评分规则（总分100分，60分以上为中军）：")
    print("  ├─ 主题状态（20分）：强趋势=18分，震荡=14分")
    print("  ├─ 日均成交额（20分）：≥15亿=20分，≥8亿=15分，≥5亿=10分")
    print("  ├─ 均线多头（15分）：完美多头=15分，在MA20上=10分")
    print("  ├─ MA20向上（15分）：>1%=15分，>0=10分，>-1%=5分")
    print("  ├─ 接近新高（15分）：≥95%=15分，≥80%=5分")
    print("  ├─ RS20（10分）：≥5=10分，≥2=8分，≥0=5分，≥-5=2分")
    print("  ├─ 20日涨停数（3分）：0次=3分，1次=2分，2次=1分")
    print("  └─ 近5日未破MA10（2分）")
    print("综合评分 = 0.35 * theme_score + 0.25 * trend_score + 0.20 * RS20_score + 0.20 * amount_score")
    print()
    print("补涨中军：关注大容量、大成交、基本面健康")
    print("  1. 大成交额（权重40%）：近20日平均成交额高，表明资金关注")
    print("  2. 大市值（权重20%）：市值大，表明机构认可度高")
    print("  3. 价格趋势健康（权重15%）：MA20向上，价格在MA5之上")
    print("  4. 量价配合（权重15%）：量价齐升，温和放量")
    print("  5. 技术面健康（权重10%）：均线多头排列，成交量活跃")
    print("基础条件：排除涨停股、市值100-2000亿、成交额>=5亿、5日涨幅<=20%")
    print("均线条件：股价站上五日线、十日线向上运行、MA20不持续向下")
    print("最终输出：TOP10 趋势中军 + TOP5 补涨中军")
    print("=" * 120)

# =================
# 数据库操作
# =================
def init_zhongjun_db():
    """初始化中军数据库"""
    db_path = os.path.join(CACHE_DIR, 'zhongjun_history.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS zhongjun_daily (
            trade_date TEXT,
            ts_code TEXT,
            name TEXT,
            theme_name TEXT,
            buy_type TEXT,
            buy_type_detail TEXT,
            close REAL,
            pct_chg REAL,
            turnover_rate REAL,
            mcap REAL,
            reason TEXT,
            final_score REAL,
            buzhang_score REAL,
            ma5 REAL,
            ma10 REAL,
            ma20 REAL,
            PRIMARY KEY (trade_date, ts_code)
        )
    """)
    
    conn.commit()
    conn.close()
    return db_path

def save_to_db(candidates, db_path):
    """保存中军数据到数据库"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    for c in candidates:
        cur.execute("""
            INSERT OR REPLACE INTO zhongjun_daily 
            (trade_date, ts_code, name, theme_name, buy_type, buy_type_detail,
             close, pct_chg, turnover_rate, mcap, reason, final_score, buzhang_score,
             ma5, ma10, ma20)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            TRADE_DATE,
            c.get('code', ''),
            c.get('name', ''),
            c.get('theme_name', ''),
            c.get('buy_type', ''),
            c.get('buy_type_detail', ''),
            c.get('close', 0),
            c.get('pct_chg', 0),
            c.get('turnover_rate', 0),
            c.get('mcap', 0),
            c.get('reason', ''),
            c.get('final_score', 0),
            c.get('buzhang_score', 0),
            c.get('ma5', 0),
            c.get('ma10', 0),
            c.get('ma20', 0),
        ))
    
    conn.commit()
    conn.close()
    print(f"中军数据已保存到数据库: {db_path}")

def _compute_indicators(df):
    """从 OHLCV 自包含计算技术指标（不依赖外部库）"""
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)

    # 均线
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2

    # RSI (6)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/6, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/6, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi6 = (100 - (100 / (1 + rs))).fillna(50)

    # KDJ (9, 3, 3)
    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    rsv = ((close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100).fillna(50)
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    j = 3 * k - 2 * d

    return {
        'ma5': ma5.values, 'ma10': ma10.values, 'ma20': ma20.values,
        'ma60': ma60.values, 'ma120': ma120.values,
        'macd': macd.values, 'dif': dif.values, 'dea': dea.values,
        'rsi6': rsi6.values,
        'k': k.values, 'd': d.values, 'j': j.values,
    }


def detect_stabilize_signal(kline_data):
    """检测中军股票企稳信号 — 机构级算法 V2.0

    借鉴顶尖机构模型：
      - William O'Neil：回调健康度 + 二次测试买点
      - Mark Minervini VCP：量能收缩 + 波动收敛
      - Linda Raschke：K线反转形态（锤子线/吞没/十字星）
      - Stan Weinstein：趋势结构 + Stage 1→2 过渡
      - Stanley Druckenmiller：资金面确认

    总分 100，7 维评分：
      1. 回调健康度 (20分): 回撤幅度 + 回调天数 + 无放量破位
      2. 量能收缩   (20分): 5/20日均量比 + 反转日量比 + 量价背离
      3. K线反转    (15分): 锤子线/吞没/十字星 + 收盘分位
      4. 动能背离   (15分): MACD/RSI/KDJ 底背离
      5. 支撑位     (15分): MA20/MA60 + 距离 + 试探次数
      6. 趋势结构   (10分): MA60向上 + MA20走平 + 上一波强度
      7. 资金面     (5分):  量价共振 + 当日确认

    阈值：≥75 完美企稳 | 60-74 合格企稳 | <60 未企稳
    位置门槛：close 必须在 MA5*1.05 与 MA20*0.95 之间（真正企稳区）
    """
    db_path = os.path.join(CACHE_DIR, 'zhongjun_history.db')
    if not os.path.exists(db_path):
        print("   中军历史数据库不存在，跳过企稳检测")
        return []

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 取过去10个交易日的历史中军（扩大样本，覆盖回调周期）
    cur.execute("SELECT DISTINCT trade_date FROM zhongjun_daily ORDER BY trade_date DESC LIMIT 10")
    recent_dates = [row[0] for row in cur.fetchall()]

    if not recent_dates:
        conn.close()
        print("   无历史中军数据，跳过企稳检测")
        return []

    cur.execute(f"""
        SELECT DISTINCT ts_code, name
        FROM zhongjun_daily
        WHERE trade_date IN ({','.join(['?']*len(recent_dates))})
    """, recent_dates)
    recent_stocks = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()

    signals = []
    for ts_code, name in recent_stocks.items():
        if ts_code not in kline_data:
            continue

        df = kline_data[ts_code].copy()
        if len(df) < 60:  # 需要足够数据计算 MA60/指标
            continue

        df = df.sort_values('trade_date').reset_index(drop=True)
        try:
            ind = _compute_indicators(df)
        except Exception:
            continue

        i = len(df) - 1
        close = float(df.iloc[i]['close'])
        open_ = float(df.iloc[i]['open'])
        high = float(df.iloc[i]['high'])
        low = float(df.iloc[i]['low'])
        vol = float(df.iloc[i]['vol'])
        pct_chg = float(df.iloc[i].get('pct_chg', 0) or 0)

        ma5 = float(ind['ma5'][i] or 0)
        ma10 = float(ind['ma10'][i] or 0)
        ma20 = float(ind['ma20'][i] or 0)
        ma60 = float(ind['ma60'][i] or 0)
        rsi6 = float(ind['rsi6'][i] or 50)
        kdj_j = float(ind['j'][i] or 50)

        if close <= 0 or ma20 <= 0 or ma5 <= 0:
            continue

        # ===== 位置门槛：close 必须在企稳区 =====
        # 太高（>MA5*1.05）= 已经反弹，不是企稳买点
        # 太低（<MA20*0.95）= 破位，不是企稳
        if close > ma5 * 1.05 or close < ma20 * 0.95:
            continue

        # ===== 1. 回调健康度 (20分) — O'Neil =====
        lookback = min(30, i + 1)
        df_lb = df.iloc[i - lookback + 1: i + 1]
        high_idx_in_lb = int(df_lb['high'].idxmax())
        high_30 = float(df_lb['high'].max())
        pullback_days = i - high_idx_in_lb
        pullback_pct = (high_30 - close) / high_30 * 100 if high_30 > 0 else 0.0

        # 上一波涨幅（从高点往前找最低点）
        if high_idx_in_lb > 5:
            df_prev = df.iloc[max(0, high_idx_in_lb - 30):high_idx_in_lb]
            low_prev = float(df_prev['low'].min())
            prev_wave_gain = (high_30 - low_prev) / low_prev * 100 if low_prev > 0 else 0.0
        else:
            prev_wave_gain = 0.0

        score_ph = 0
        # 1a 回撤幅度
        if 15 <= pullback_pct <= 25:
            score_ph += 12
        elif 10 <= pullback_pct < 15 or 25 < pullback_pct <= 35:
            score_ph += 8
        elif 5 <= pullback_pct < 10 or 35 < pullback_pct <= 45:
            score_ph += 4
        # 1b 回调天数
        if 5 <= pullback_days <= 20:
            score_ph += 5
        elif 3 <= pullback_days < 5 or 20 < pullback_days <= 30:
            score_ph += 3
        else:
            score_ph += 1
        # 1c 回调过程无放量破位
        if pullback_days > 0:
            df_pb = df.iloc[high_idx_in_lb: i + 1]
            max_drop = df_pb['pct_chg'].min()
            avg_vol_pb = df_pb['vol'].mean()
            avg_vol_pre = df.iloc[max(0, high_idx_in_lb - 20):high_idx_in_lb]['vol'].mean()
            vol_ratio_pb = avg_vol_pb / avg_vol_pre if avg_vol_pre > 0 else 1.0
            if max_drop > -5 and vol_ratio_pb < 1.2:
                score_ph += 3
            elif max_drop > -8:
                score_ph += 1
        score_ph = max(0, min(20, score_ph))

        # ===== 2. 量能收缩 (20分) — Minervini VCP =====
        vol_5_avg = df['vol'].iloc[i - 4: i + 1].mean()
        vol_20_avg = df['vol'].iloc[i - 19: i + 1].mean()
        vol_5_to_20 = vol_5_avg / vol_20_avg if vol_20_avg > 0 else 1.0
        vol_ratio_today = vol / vol_5_avg if vol_5_avg > 0 else 1.0

        # 量价背离：近5日下跌天数多但5日均量 < 20日均量
        df_5d = df.iloc[i - 4: i + 1]
        down_days = (df_5d['pct_chg'] < 0).sum()
        vol_price_divergence = down_days >= 3 and vol_5_to_20 < 1.0

        score_vc = 0
        if vol_5_to_20 < 0.6:
            score_vc += 8
        elif vol_5_to_20 < 0.8:
            score_vc += 6
        elif vol_5_to_20 < 1.0:
            score_vc += 4
        elif vol_5_to_20 < 1.1:
            score_vc += 2
        elif vol_5_to_20 >= 1.3:
            score_vc -= 2

        if vol_ratio_today > 1.5:
            score_vc += 6
        elif vol_ratio_today > 1.2:
            score_vc += 4
        elif vol_ratio_today > 1.0:
            score_vc += 2

        if vol_price_divergence:
            score_vc += 6
        score_vc = max(0, min(20, score_vc))

        # ===== 3. K线反转形态 (15分) — Raschke =====
        body = close - open_
        body_abs = abs(body)
        range_ = high - low
        upper_shadow = high - max(close, open_)
        lower_shadow = min(close, open_) - low

        reversal_pattern = "无"
        score_kr = 0

        if range_ > 0:
            # 锤子线：下影线 > 实体2倍，上影线短
            if lower_shadow > body_abs * 2 and upper_shadow < body_abs * 0.5 and body_abs > 0:
                reversal_pattern = "锤子线"
                score_kr += 10
            # 看涨吞没：前日阴线，当日阳线包住前日实体
            elif i >= 1:
                prev_open = float(df.iloc[i - 1]['open'])
                prev_close = float(df.iloc[i - 1]['close'])
                if (prev_close < prev_open and close > open_
                        and close > prev_open and open_ < prev_close):
                    reversal_pattern = "看涨吞没"
                    score_kr += 10
            # 十字星：实体很小
            if body_abs / range_ < 0.1:
                if reversal_pattern == "无":
                    reversal_pattern = "十字星"
                    score_kr += 6
                else:
                    score_kr += 2  # 形态叠加

            # 收盘价日内分位
            close_pct = (close - low) / range_ * 100
            if close_pct > 70:
                score_kr += 5
            elif close_pct > 50:
                score_kr += 3

            if pct_chg > 0:
                score_kr += 3
        score_kr = max(0, min(15, score_kr))

        # ===== 4. 动能底背离 (15分) — 机构标准 =====
        score_md = 0
        divergence_type = "无"

        if i >= 19:
            price_low_now = float(df['low'].iloc[i - 9: i + 1].min())
            price_low_prev = float(df['low'].iloc[i - 19: i - 9].min())
            # MACD 底背离：价格新低但 MACD 未新低
            macd_low_now = float(np.nanmin(ind['macd'][i - 9: i + 1]))
            macd_low_prev = float(np.nanmin(ind['macd'][i - 19: i - 9]))
            if price_low_now < price_low_prev and macd_low_now > macd_low_prev:
                score_md += 6
                divergence_type = "MACD"
            # RSI 底背离
            rsi_low_now = float(np.nanmin(ind['rsi6'][i - 9: i + 1]))
            rsi_low_prev = float(np.nanmin(ind['rsi6'][i - 19: i - 9]))
            if price_low_now < price_low_prev and rsi_low_now > rsi_low_prev:
                score_md += 5
                divergence_type = (divergence_type + "+RSI") if divergence_type != "无" else "RSI"

        # KDJ J 值从超卖区拐头
        if i >= 2:
            j_now = float(ind['j'][i] or 50)
            j_prev = float(ind['j'][i - 1] or 50)
            j_prev2 = float(ind['j'][i - 2] or 50)
            if j_prev < 20 and j_now > j_prev and j_prev <= j_prev2:
                score_md += 4
                divergence_type = (divergence_type + "+KDJ") if divergence_type != "无" else "KDJ"
        score_md = max(0, min(15, score_md))

        # ===== 5. 关键支撑位 (15分) — Livermore Pivot =====
        score_sp = 0
        if close > ma20:
            score_sp += 5
        if ma60 > 0 and close > ma60:
            score_sp += 4
        # 距 MA20 距离
        dist_ma20 = (close - ma20) / ma20 * 100
        if 0 <= dist_ma20 <= 3:
            score_sp += 4
        elif -2 <= dist_ma20 < 0:
            score_sp += 3  # 微破但接近
        elif 3 < dist_ma20 <= 5:
            score_sp += 2
        # 近5日试探 MA20 不破
        if i >= 5 and ma20 > 0:
            test_count = 0
            for k in range(5):
                day_low = float(df.iloc[i - k]['low'])
                day_ma20 = float(ind['ma20'][i - k] or 0)
                if day_ma20 > 0 and day_low <= day_ma20 * 1.005 and day_low >= day_ma20 * 0.99:
                    test_count += 1
            if test_count >= 2:
                score_sp += 2
        score_sp = max(0, min(15, score_sp))

        # ===== 6. 趋势结构 (10分) — Weinstein =====
        score_ts = 0
        # MA60 不下行（>0 或基本走平 ≥-0.5%）
        if i >= 10 and ma60 > 0:
            prev_ma60 = float(ind['ma60'][i - 10] or 0)
            if prev_ma60 > 0:
                ma60_chg = (ma60 - prev_ma60) / prev_ma60 * 100
                if ma60_chg > 0.5:
                    score_ts += 4
                elif ma60_chg >= -0.5:
                    score_ts += 3  # 走平容忍
        # MA20 斜率走平或拐头
        if i >= 10 and ma20 > 0:
            ma20_now = ma20
            ma20_5d_ago = float(ind['ma20'][i - 5] or 0)
            ma20_10d_ago = float(ind['ma20'][i - 10] or 0)
            slope_recent = ma20_now - ma20_5d_ago
            slope_prev = ma20_5d_ago - ma20_10d_ago
            if slope_recent > 0 and slope_recent > slope_prev:
                score_ts += 3  # 拐头向上
            elif slope_recent >= 0 and slope_prev < 0:
                score_ts += 3  # 走平
            elif slope_recent > slope_prev:
                score_ts += 1  # 边际改善
        # 上一波涨幅 ≥ 25%（用户要求）
        if prev_wave_gain >= 25:
            score_ts += 3
        elif prev_wave_gain >= 15:
            score_ts += 1
        score_ts = max(0, min(10, score_ts))

        # ===== 7. 资金面确认 (5分) — Druckenmiller =====
        score_cf = 0
        if i >= 5:
            df_5d_ = df.iloc[i - 4: i + 1]
            up_vol = df_5d_[df_5d_['pct_chg'] > 0]['vol'].mean()
            dn_vol = df_5d_[df_5d_['pct_chg'] < 0]['vol'].mean()
            if up_vol > 0 and dn_vol > 0 and up_vol / dn_vol >= 1.2:
                score_cf += 2
            elif up_vol > 0 and dn_vol == 0:
                score_cf += 2
        # 当日上涨即给分（原要求 pct_chg>0 AND vol_ratio>1.0 过严）
        if pct_chg > 0:
            score_cf += 2
        elif pct_chg > -2:  # 微跌也算稳
            score_cf += 1
        # 大单活跃（amount/vol 比率 = 平均成交单价，今日高于5日均值为大单活跃）
        if i >= 0 and vol > 0:
            avg_price_today = float(df.iloc[i].get('amount', 0) or 0) / vol
            vol_5_sum = float(df['vol'].iloc[i - 4: i + 1].sum())
            amt_5_sum = float(df['amount'].iloc[i - 4: i + 1].sum())
            avg_price_5d = amt_5_sum / vol_5_sum if vol_5_sum > 0 else 0
            if avg_price_5d > 0 and avg_price_today > avg_price_5d * 1.05:
                score_cf += 1
        score_cf = max(0, min(5, score_cf))

        # ===== 总分 =====
        total_score = int(score_ph + score_vc + score_kr + score_md + score_sp + score_ts + score_cf)

        # ===== 状态判断 =====
        if total_score >= 75:
            stabilize_type = "完美企稳"
            is_stabilized = True
        elif total_score >= 60:
            stabilize_type = "合格企稳"
            is_stabilized = True
        else:
            stabilize_type = "未企稳"
            is_stabilized = False

        if is_stabilized:
            signals.append({
                'ts_code': ts_code,
                'name': name,
                'close': close,
                'ma5': ma5,
                'ma10': ma10,
                'ma20': ma20,
                'ma60': ma60,
                'distance_to_ma5': (close - ma5) / ma5 * 100,
                'distance_to_ma20': (close - ma20) / ma20 * 100,
                'stabilize_score': total_score,
                'is_stabilized': is_stabilized,
                'stabilize_type': stabilize_type,
                'sub_scores': {
                    '回调健康': score_ph,
                    '量能收缩': score_vc,
                    'K线反转': score_kr,
                    '动能背离': score_md,
                    '支撑位': score_sp,
                    '趋势结构': score_ts,
                    '资金面': score_cf,
                },
                'pullback_pct': round(pullback_pct, 2),
                'pullback_days': pullback_days,
                'prev_wave_gain': round(prev_wave_gain, 2),
                'reversal_pattern': reversal_pattern,
                'divergence_type': divergence_type,
                'vol_5_to_20': round(vol_5_to_20, 2),
                'vol_ratio_today': round(vol_ratio_today, 2),
                'rsi6': round(rsi6, 1),
                'kdj_j': round(kdj_j, 1),
            })

    # 按企稳分排序
    signals.sort(key=lambda x: x['stabilize_score'], reverse=True)
    return signals

def print_stabilize_signals(signals):
    """输出企稳信号 — 机构级算法 V2.0"""
    if not signals:
        return

    print("\n" + "=" * 140)
    print("📊 中军股票企稳信号 — 机构级算法 V2.0（7维评分）")
    print("=" * 140)
    print(f"{'代码':<14}{'名称':<10}{'价格':>8}{'企稳分':>8}{'类型':<10}{'回撤%':>8}{'天':>5}{'量缩比':>8}{'形态':<10}{'背离':<14}{'距MA20%':>10}")
    print("-" * 140)

    for s in signals:
        print(f"{s['ts_code']:<14}{s['name']:<10}{s['close']:>8.2f}{s['stabilize_score']:>8}"
              f"{s['stabilize_type']:<10}{s['pullback_pct']:>8.2f}{s['pullback_days']:>5}"
              f"{s['vol_5_to_20']:>8.2f}{s['reversal_pattern']:<10}{s['divergence_type']:<14}"
              f"{s['distance_to_ma20']:>10.2f}")

    # 完美企稳的子分明细
    perfect = [s for s in signals if s['stabilize_type'] == "完美企稳"]
    if perfect:
        print(f"\n  📋 完美企稳 — 7 维子分明细（共 {len(perfect)} 只）")
        print(f"  {'代码':<14}{'名称':<10}{'回调':>6}{'量缩':>6}{'K线':>6}{'背离':>6}{'支撑':>6}{'趋势':>6}{'资金':>6}{'上一波%':>10}")
        print("  " + "-" * 90)
        for s in perfect:
            ss = s['sub_scores']
            print(f"  {s['ts_code']:<14}{s['name']:<10}{ss['回调健康']:>6}{ss['量能收缩']:>6}{ss['K线反转']:>6}"
                  f"{ss['动能背离']:>6}{ss['支撑位']:>6}{ss['趋势结构']:>6}{ss['资金面']:>6}{s['prev_wave_gain']:>10.2f}")

    # 评分标准说明
    print("\n  📖 评分标准（总分100）")
    print("    1. 回调健康度(20): 回撤15-25%最佳 + 回调5-20天 + 无放量破位  [O'Neil]")
    print("    2. 量能收缩  (20): 5/20均量比<0.7 + 反转日量比>1.5 + 量价背离 [Minervini VCP]")
    print("    3. K线反转   (15): 锤子线/看涨吞没/十字星 + 收盘日内分位      [Raschke]")
    print("    4. 动能背离   (15): MACD/RSI/KDJ 底背离                        [机构标准]")
    print("    5. 支撑位     (15): close>MA20/MA60 + 距MA20≤3% + 试探不破     [Livermore]")
    print("    6. 趋势结构   (10): MA60向上 + MA20走平/拐头 + 上一波≥25%      [Weinstein]")
    print("    7. 资金面     (5):  量价共振 + 当日确认 + 大单活跃              [Druckenmiller]")
    print("    阈值: ≥75 完美企稳 | 60-74 合格企稳 | <60 不输出")
    print("    位置门槛: close 必须在 MA5*1.05 与 MA20*0.95 之间（真正企稳区）")
    print("=" * 140)

# =================
# 保存结果
# =================
def save_results(candidates):
    df = pd.DataFrame(candidates)
    # 写带日期的副本到 report_daily（用于历史查阅）
    output_file = os.path.join(REPORT_DIR, f'theme_pattern_stocks_{TRADE_DATE}.csv')
    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存(带日期): {output_file}")
    # 写无日期的副本到 cache_backbone_tushare（供 tushare_quant.py 读取）
    cache_file = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'theme_pattern_stocks.csv')
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    df.to_csv(cache_file, index=False, encoding='utf-8-sig')
    print(f"结果已同步到缓存: {cache_file}")
    
    # 保存到数据库
    db_path = init_zhongjun_db()
    save_to_db(candidates, db_path)
    
    return output_file

# =================
# 主函数
# =================
def main():
    print("=" * 80)
    print("主题中军选股程序 - 趋势中军池策略")
    print("=" * 80)
    
    print("\n[Step 1] 获取主题趋势和情绪分数...")
    hot_themes, theme_scores = get_theme_data()
    if hot_themes is None:
        return
    
    print("\n[Step 2] 获取成分股和基本面数据...")
    stock_basic, daily_basic, theme_stock_map, name_map_basic, mcap_map, turnover_map = get_stock_data(hot_themes)
    
    print("\n[Step 3] 获取K线数据...")
    all_codes = []
    for theme_name, stock_info in theme_stock_map.items():
        all_codes.extend(list(stock_info.keys()))
    all_codes = list(set(all_codes))
    print(f"   待分析股票: {len(all_codes)}只")
    kline_data = get_kline_data(all_codes)
    print(f"   成功获取K线: {len(kline_data)}只")
    
    print("\n[Step 4] 计算技术指标和筛选...")
    candidates, good_themes = calculate_and_filter(theme_stock_map, kline_data, hot_themes, theme_scores, name_map_basic, mcap_map, turnover_map)
    
    print_results(candidates)
    
    if candidates:
        output_file = save_results(candidates)
        print(f"\n完成！已选出 {len(candidates)} 只股票")
    else:
        print("\n没有找到符合条件的股票")
    
    print("\n[Step 5] 检测中军股票企稳信号...")
    signals = detect_stabilize_signal(kline_data)
    print_stabilize_signals(signals)

if __name__ == "__main__":
    main()
