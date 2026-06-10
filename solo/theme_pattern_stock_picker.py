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

import os
import sys
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

# 加载环境变量
load_dotenv(os.path.join(BASE_DIR, '.env'))
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')
DAILY_CACHE_DIR = r"d:\mystock\cache_daily"
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# 初始化Tushare
pro = ts.pro_api(TS_TOKEN)

# 交易日期（默认使用昨天，因为当天数据还未完全更新）
if len(sys.argv) > 1:
    TRADE_DATE = sys.argv[1]
else:
    # 获取昨天的日期
    yesterday = datetime.now() - timedelta(days=1)
    TRADE_DATE = yesterday.strftime('%Y%m%d')

# 计算开始日期（TRADE_DATE前推60个交易日，约3个月）
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
    
    # 获取行业和概念成分股
    dc_df = theme_score.get_dc_members()
    
    # 获取股票列表
    stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry,market,list_date')
    stock_basic = stock_basic[~stock_basic['name'].str.contains('ST|退', na=False)].copy()
    
    # 获取市值数据
    mcap_date = TRADE_DATE
    daily_basic = pd.DataFrame()
    try:
        daily_basic = pro.daily_basic(trade_date=mcap_date, fields='ts_code,close,pe,total_mv,circ_mv,turnover_rate,volume_ratio')
        if daily_basic.empty:
            print(f"   {mcap_date}市值数据为空，尝试获取前几个交易日...")
    except Exception as e:
        print(f"   {mcap_date}市值数据获取失败: {e}，尝试获取前几个交易日...")
    
    if daily_basic.empty:
        dt_mcap = datetime.strptime(TRADE_DATE, '%Y%m%d')
        for offset in range(1, 10):
            prev_date = (dt_mcap - timedelta(days=offset)).strftime('%Y%m%d')
            try:
                daily_basic = pro.daily_basic(trade_date=prev_date, fields='ts_code,close,pe,total_mv,circ_mv,turnover_rate,volume_ratio')
                if not daily_basic.empty:
                    mcap_date = prev_date
                    print(f"   成功获取{mcap_date}的市值数据，共{len(daily_basic)}条")
                    break
            except:
                continue
        if daily_basic.empty:
            print(f"   警告: 无法获取市值数据，将使用0值")
            daily_basic = pd.DataFrame(columns=['ts_code', 'close', 'pe', 'total_mv', 'circ_mv', 'turnover_rate', 'volume_ratio'])
    
    print(f"   市值数据: {len(daily_basic)}条，有效市值: {(daily_basic['total_mv'] > 0).sum() if not daily_basic.empty else 0}条")
    
    # 匹配主题成分股
    theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)
    
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
                ts_list = ",".join(batch)
                df = pro.daily(
                    ts_code=ts_list,
                    start_date=START_DATE,
                    end_date=TRADE_DATE
                )
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
        
        # 获取该主题趋势评分
        theme_score_val = 0
        if not theme_scores.empty and len(theme_scores[theme_scores['theme'] == theme_name]) > 0:
            theme_score_val = theme_scores[theme_scores['theme'] == theme_name]['composite_score'].values[0]
        
        # 确定主题类型
        is_mid_trend = False
        is_short_trend = False
        
        if len(theme_scores) > 0:
            # 计算60日均线斜率（中期趋势用）
            avg_scores = {}
            for idx, row in theme_scores.iterrows():
                theme_name_iter = row['theme']
                if 't_avg_slope_60' in row:
                    avg_scores[theme_name_iter] = row['t_avg_slope_60']
            
            if avg_scores:
                sorted_avg = sorted(avg_scores.items(), key=lambda x: -x[1])
                top_5_avg = [x[0] for x in sorted_avg[:5]]
                if theme_name in top_5_avg: 
                    is_mid_trend = True
            
            # 当日趋势分排序
            today_scores = {}
            for idx, row in theme_scores.iterrows():
                theme_name_iter = row['theme']
                today_scores[theme_name_iter] = row.get('trend_score', 0)
            
            sorted_today = sorted(today_scores.items(), key=lambda x: -x[1])
            top_5_today = [x[0] for x in sorted_today[:5]]
            if theme_name in top_5_today:   
                is_short_trend = True
        
        if not is_mid_trend and not is_short_trend:
            continue
        
        print(f"\n   【{theme_name}】({'中期趋势' if is_mid_trend else '短线主线'}):")
        
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
            
            # 1. 主题类型（20分）
            if is_mid_trend or is_short_trend:
                total_score += 20
            else:
                # 非TOP5主题直接跳过
                continue
            
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
                'theme_name': theme_name,
                'theme_type': '中期趋势' if is_mid_trend else '短线主线',
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
            
            # 基础条件检查（快速筛选）
            # 1. 排除当日涨停股
            pct_chg_today = s.get('pct_chg', 0)
            if pct_chg_today >= 9.5:
                debug_stats['zt_filter'] += 1
                continue
            
            # 2. 市值限制100-2000亿（放宽下限）
            mcap = s.get('mcap', 0)
            if not mcap or mcap <= 0 or mcap < 100 or mcap > 2000:
                debug_stats['mcap_filter'] += 1
                continue
            
            # 3. 成交额>=5亿（放宽下限）
            recent_20 = df_sorted.iloc[-21:-1] if len(df_sorted) >= 21 else df_sorted
            avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000
            if avg_amount_20 < 5:
                debug_stats['amount_filter'] += 1
                continue
            
            # 4. 排除近期涨幅过大的股票（5日涨幅超过20%）
            if len(df_sorted) >= 6:
                close_today = df_sorted.iloc[-1]['close']
                close_5d_ago = df_sorted.iloc[-6]['close']
                if close_5d_ago > 0:
                    pct_5d = (close_today - close_5d_ago) / close_5d_ago * 100
                    if pct_5d > 20:
                        debug_stats['pct5d_filter'] += 1
                        continue
            
            # 5. 均线趋势检查：股价站上五日线且十日线向上运行
            if len(closes) >= 25:
                ma5_vals = pd.Series(closes).rolling(5).mean().values
                ma10_vals = pd.Series(closes).rolling(10).mean().values
                ma20_vals = pd.Series(closes).rolling(20).mean().values
                
                # 条件1：股价必须站上五日线
                close = closes[-1]
                ma5 = ma5_vals[-1]
                if close <= ma5:
                    debug_stats['trend_filter'] += 1
                    continue
                
                # 条件2：十日线必须向上运行（近5日斜率为正）
                if len(ma10_vals) >= 5:
                    ma10_slope = (ma10_vals[-1] - ma10_vals[-5]) / ma10_vals[-5] * 100
                    if ma10_slope <= 0:
                        debug_stats['trend_filter'] += 1
                        continue
                
                # 条件3：MA20不能持续向下
                if len(ma20_vals) >= 5:
                    ma20_slope = (ma20_vals[-1] - ma20_vals[-5]) / ma20_vals[-5] * 100
                    if ma20_slope < -2:
                        debug_stats['trend_filter'] += 1
                        continue
            
            # 使用高级检测器分析
            # 获取第一个中军作为对比基准
            zhongjun_df = None
            if zhongjun_data_dict:
                first_zj_code = next(iter(zhongjun_data_dict.keys()))
                zhongjun_df = zhongjun_data_dict[first_zj_code]
            
            analysis_result = buzhang_detector.analyze_stock(df_sorted, zhongjun_df, s.get('mcap', 0), s.get('turnover_rate', 0))
            
            if not analysis_result.get('valid', False):
                continue
            
            # 综合评分（提高到50）
            overall_score = analysis_result.get('overall_score', 0)
            if overall_score < 50:
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
        
        # 补涨中军（成交额大的优先，取2个，且不与中军重复）
        buzhang_sorted = sorted(buzhang_candidates, key=lambda x: (-x.get('avg_amount_20', 0), -x.get('buzhang_score', 0)))[:2]
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
                'type': '中期趋势' if is_mid_trend else '短线主线',
                'score': theme_score_val
            })
    
    # 按主题类型排序，方便显示
    def sort_key(stock):
        # 优先级：中期趋势 > 短线主线 > 补充
        theme_order = {
            '中期趋势': 0,
            '短线主线': 1,
            '补充': 2
        }
        return (
            theme_order.get(stock.get('theme_type', '补充'), 2),
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
    
    # 按主题类型分组
    mid_term_candidates = [c for c in candidates if c.get('theme_type') == '中期趋势']
    short_term_candidates = [c for c in candidates if c.get('theme_type') == '短线主线']
    supplement_candidates = [c for c in candidates if c.get('theme_type') == '补充']
    
    # 第一部分：中期趋势主题（60日趋势平均分TOP5）
    if mid_term_candidates:
        print("中期趋势主题（60日趋势平均分TOP5）")
        print("-" * 120)
        
        mid_term_zhongjun = [c for c in mid_term_candidates if c.get('buy_type') == '中军']
        mid_term_buzhang = [c for c in mid_term_candidates if c.get('buy_type') == '补涨中军']
        
        if mid_term_zhongjun:
            print("中军")
            print(f"{'代码':<14}{'名称':<10}{'主题':<10}{'价格':>8}{'涨跌%':>8}{'换手%':>8}{'市值亿':>10}  {'推荐理由'}")
            print("-" * 100)
            for stock in mid_term_zhongjun:
                mcap_display = f"{stock['mcap']:.1f}" if stock.get('has_real_mcap', False) else "--"
                close_val = stock.get('close', 0) or 0
                pct_val = stock.get('pct_chg', 0) or 0
                turnover_val = stock.get('turnover_rate', 0) or 0
                theme_val = stock.get('theme_name', '') or ''
                reason_val = stock.get('reason', '') or ''
                print(f"{stock['code']:<14}{stock['name']:<10}{theme_val:<10}{close_val:>8.2f}{pct_val:>8.2f}{turnover_val:>8.2f}{mcap_display:>10}  {reason_val}")
            print()
        
        if mid_term_buzhang:
            print("补涨中军")
            print(f"{'代码':<14}{'名称':<10}{'主题':<10}{'价格':>8}{'涨跌%':>8}{'换手%':>8}{'市值亿':>10}  {'推荐理由'}")
            print("-" * 100)
            for stock in mid_term_buzhang:
                mcap_display = f"{stock['mcap']:.1f}" if stock.get('has_real_mcap', False) else "--"
                close_val = stock.get('close', 0) or 0
                pct_val = stock.get('pct_chg', 0) or 0
                turnover_val = stock.get('turnover_rate', 0) or 0
                theme_val = stock.get('theme_name', '') or ''
                reason_val = stock.get('reason', '') or ''
                print(f"{stock['code']:<14}{stock['name']:<10}{theme_val:<10}{close_val:>8.2f}{pct_val:>8.2f}{turnover_val:>8.2f}{mcap_display:>10}  {reason_val}")
            print()
    
    # 第二部分：短线主线（当日最强主线5）
    if short_term_candidates:
        print("短线主线（当日最强主线TOP5）")
        print("-" * 120)
        
        short_term_zhongjun = [c for c in short_term_candidates if c.get('buy_type') == '中军']
        short_term_buzhang = [c for c in short_term_candidates if c.get('buy_type') == '补涨中军']
        
        if short_term_zhongjun:
            print("中军")
            print(f"{'代码':<14}{'名称':<10}{'主题':<10}{'价格':>8}{'涨跌%':>8}{'换手%':>8}{'市值亿':>10}  {'推荐理由'}")
            print("-" * 100)
            for stock in short_term_zhongjun:
                mcap_display = f"{stock['mcap']:.1f}" if stock.get('has_real_mcap', False) else "--"
                close_val = stock.get('close', 0) or 0
                pct_val = stock.get('pct_chg', 0) or 0
                turnover_val = stock.get('turnover_rate', 0) or 0
                theme_val = stock.get('theme_name', '') or ''
                reason_val = stock.get('reason', '') or ''
                print(f"{stock['code']:<14}{stock['name']:<10}{theme_val:<10}{close_val:>8.2f}{pct_val:>8.2f}{turnover_val:>8.2f}{mcap_display:>10}  {reason_val}")
            print()
        
        if short_term_buzhang:
            print("补涨中军")
            print(f"{'代码':<14}{'名称':<10}{'主题':<10}{'价格':>8}{'涨跌%':>8}{'换手%':>8}{'市值亿':>10}  {'推荐理由'}")
            print("-" * 100)
            for stock in short_term_buzhang:
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
    print("中期趋势主题：基于60日趋势平均分TOP5，适合中线布局")
    print("短线主题：基于当日综合分TOP3，适合短线操作")
    print("趋势中军：满足以下分数制条件的个股，按综合评分排序取TOP10")
    print("  评分规则（总分100分，60分以上为中军）：")
    print("  ├─ 主题类型（20分）：必须是TOP5中期趋势或短线主线")
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

if __name__ == "__main__":
    main()
