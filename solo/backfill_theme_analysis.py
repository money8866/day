#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题趋势与情绪评分回溯脚本
回溯生成过去N个交易日的主题评分数据
"""
import os
import sys
import time
import sqlite3
import numpy as np
import pandas as pd

# 配置路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# Patch tushare path
original_expanduser = os.path.expanduser
safe_cache_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
os.makedirs(safe_cache_dir, exist_ok=True)

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(safe_cache_dir, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

import tushare as ts
from dotenv import load_dotenv
from datetime import datetime, timedelta

env_path = os.path.join(os.path.dirname(BASE_DIR), "config", ".env")
load_dotenv(env_path)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def get_trade_dates(start_date, end_date):
    """获取交易日列表"""
    cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    cal = cal[cal['is_open'] == 1]
    return list(cal['cal_date'].astype(str))

def get_last_trade_date():
    """获取最近交易日"""
    today = datetime.now().strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='', start_date=today, end_date=today)
    if cal is None or cal.empty:
        cal = pro.trade_cal(exchange='', start_date=(datetime.now() - timedelta(days=7)).strftime('%Y%m%d'), end_date=today)
    if cal is not None and not cal.empty:
        return str(cal[cal['is_open'] == 1].iloc[-1]['cal_date'])
    return today

def load_theme_json():
    """加载主题配置"""
    theme_file = os.path.join(os.path.dirname(BASE_DIR), "theme.json")
    import json
    with open(theme_file, 'r', encoding='utf-8') as f:
        return json.load(f)['HOT_THEMES']

def get_dc_members():
    """获取东财成份股"""
    cache_file = os.path.join(safe_cache_dir, 'dc_members_cache.csv')
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        if not df.empty and 'trade_date' in df.columns:
            return df
    df = pro.dc_member(ts_code='all')
    if df is not None and not df.empty:
        df.to_csv(cache_file, index=False)
    return df

def get_stock_basic():
    """获取股票基本信息"""
    cache_file = os.path.join(safe_cache_dir, 'stock_basic_cache.csv')
    if os.path.exists(cache_file):
        return pd.read_csv(cache_file)
    df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
    if df is not None and not df.empty:
        df.to_csv(cache_file, index=False)
    return df

def match_theme_stocks(hot_themes, dc_df, stock_basic):
    """匹配主题和成份股"""
    theme_stock_map = {}
    name_map = {}
    stock_industry = {}
    stock_concepts = {}
    
    if dc_df is None or dc_df.empty:
        return theme_stock_map, name_map, stock_industry, stock_concepts
    
    for theme_name, theme_cfg in hot_themes.items():
        matched_stocks = {}
        
        industry_list = theme_cfg.get('industry', [])
        concept_list = theme_cfg.get('concept', [])
        keywords_list = theme_cfg.get('keywords', [])
        exclude_keywords = theme_cfg.get('exclude_keywords', [])
        core_companies = theme_cfg.get('core_companies', [])
        
        # 核心公司
        for company in core_companies:
            matched = stock_basic[stock_basic['name'] == company]
            if not matched.empty:
                ts_code = matched.iloc[0]['ts_code']
                matched_stocks[ts_code] = {'source': 'core', 'purity': 5}
                name_map[ts_code] = company
        
        # 东财板块匹配
        for _, row in dc_df.iterrows():
            ts_code = row['ts_code']
            name = row.get('name', '')
            concepts = row.get('concept_name', '')
            industry = row.get('industry_name', '')
            
            if not isinstance(concepts, str):
                concepts = ''
            if not isinstance(industry, str):
                industry = ''
            
            # 检查排除关键词
            if any(kw in name or kw in concepts or kw in industry for kw in exclude_keywords):
                continue
            
            # 匹配行业
            ind_matched = any(ind in industry for ind in industry_list if industry)
            
            # 匹配概念
            concept_matched = any(c in concepts for c in concept_list if concepts)
            
            # 匹配关键词
            kw_matched = sum(1 for kw in keywords_list if kw in name or kw in concepts)
            
            if ind_matched or concept_matched or kw_matched > 0:
                purity = (1 if ind_matched else 0) + (2 if concept_matched else 0) + kw_matched
                if ts_code not in matched_stocks or matched_stocks[ts_code]['purity'] < purity:
                    matched_stocks[ts_code] = {'source': 'concept', 'purity': purity}
                
                if ts_code not in name_map:
                    name_map[ts_code] = name
                stock_industry[ts_code] = industry
                stock_concepts[ts_code] = [c.strip() for c in concepts.split('|') if c.strip()]
        
        theme_stock_map[theme_name] = matched_stocks
    
    return theme_stock_map, name_map, stock_industry, stock_concepts

def get_daily_kline(codes, start_date, end_date):
    """获取日线K线"""
    if not codes:
        return pd.DataFrame()
    
    all_dfs = []
    for code in codes[:500]:  # 限制数量
        try:
            df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                all_dfs.append(df)
            time.sleep(0.02)
        except:
            continue
    
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()

def calc_simple_trend_score(stock_feats):
    """简化趋势评分"""
    if not stock_feats:
        return 50.0
    
    # 平均涨幅
    avg_ret_5 = np.mean([f['ret_5'] for f in stock_feats])
    avg_ret_10 = np.mean([f['ret_10'] for f in stock_feats])
    avg_ret_20 = np.mean([f['ret_20'] for f in stock_feats])
    
    # 多头占比
    up_ratio = np.mean([f['up_ratio'] for f in stock_feats])
    
    # 综合评分
    score = 50 + avg_ret_5 * 2 + avg_ret_10 * 1.5 + avg_ret_20 + (up_ratio - 50) * 0.3
    return max(0, min(100, score))

def calc_simple_sentiment_score(stock_feats):
    """简化情绪评分"""
    if not stock_feats:
        return 50.0
    
    # 平均量比
    avg_vol_ratio = np.mean([f['vol_ratio'] for f in stock_feats])
    
    # 涨停数量
    total_zt = sum(f['zt_count'] for f in stock_feats)
    
    # 综合评分
    score = 50 + (avg_vol_ratio - 1) * 20 + total_zt * 2
    return max(0, min(100, score))

def run_theme_analysis_for_date(trade_date):
    """针对特定日期运行主题分析"""
    print(f"\n{'='*80}")
    print(f"📅 分析日期: {trade_date}")
    print(f"{'='*80}")
    
    try:
        # 获取分析区间
        analysis_start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
        
        # 获取主题配置
        hot_themes = load_theme_json()
        
        # 获取成份股
        dc_df = get_dc_members()
        stock_basic = get_stock_basic()
        
        # 匹配主题成份股
        theme_stock_map, name_map, stock_industry, stock_concepts = match_theme_stocks(
            hot_themes, dc_df, stock_basic
        )
        
        # 获取所有股票代码
        all_codes = set()
        for stocks in theme_stock_map.values():
            all_codes.update(stocks.keys())
        
        print(f"   待分析股票: {len(all_codes)} 只")
        
        # 获取K线数据
        kline_df = get_daily_kline(list(all_codes), analysis_start, trade_date)
        
        if kline_df.empty:
            print(f"   ❌ K线数据为空")
            return None
        
        # 过滤到指定日期
        kline_df['trade_date'] = kline_df['trade_date'].astype(str)
        kline_df = kline_df[kline_df['trade_date'] <= trade_date]
        
        # 按主题计算评分
        results = []
        for theme_name, theme_cfg in hot_themes.items():
            stocks = theme_stock_map.get(theme_name, {})
            
            if not stocks:
                continue
            
            # 获取主题成份股的K线
            theme_codes = list(stocks.keys())
            theme_kline = kline_df[kline_df['ts_code'].isin(theme_codes)]
            
            if len(theme_kline) < 10:
                continue
            
            # 计算主题评分
            stock_feats = []
            for code in theme_codes:
                stock_kline = theme_kline[theme_kline['ts_code'] == code].sort_values('trade_date')
                if len(stock_kline) < 20:
                    continue
                
                closes = stock_kline['close'].astype(float).values
                vols = stock_kline['vol'].astype(float).values
                pct_chgs = stock_kline['pct_chg'].astype(float).values
                
                ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
                ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) >= 11 else 0
                ret_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
                
                vol_ratio = vols[-1] / vols[-5:].mean() if vols[-5:].mean() > 0 else 1
                
                # 计算涨停数量
                zt_count = sum(1 for p in pct_chgs[-5:] if p >= 9.5)
                
                # 上涨家数
                up_count = sum(1 for p in pct_chgs[-5:] if p > 0)
                up_ratio = up_count / 5 * 100
                
                stock_feats.append({
                    'ret_5': ret_5, 'ret_10': ret_10, 'ret_20': ret_20,
                    'vol_ratio': vol_ratio, 'zt_count': zt_count, 'up_ratio': up_ratio
                })
            
            if not stock_feats:
                continue
            
            # 计算主题评分
            trend_score = calc_simple_trend_score(stock_feats)
            sentiment_score = calc_simple_sentiment_score(stock_feats)
            composite_score = trend_score * 0.6 + sentiment_score * 0.4
            
            avg_ret_5 = np.mean([f['ret_5'] for f in stock_feats])
            avg_ret_10 = np.mean([f['ret_10'] for f in stock_feats])
            avg_ret_20 = np.mean([f['ret_20'] for f in stock_feats])
            avg_up_ratio = np.mean([f['up_ratio'] for f in stock_feats])
            total_zt = sum(f['zt_count'] for f in stock_feats)
            
            results.append({
                'theme': theme_name,
                'trend_score': trend_score,
                'sentiment_score': sentiment_score,
                'composite_score': composite_score,
                'avg_ret_5': avg_ret_5,
                'avg_ret_10': avg_ret_10,
                'avg_ret_20': avg_ret_20,
                'n_stocks': len(stock_feats),
                'zt_count': total_zt,
                'up_ratio': avg_up_ratio
            })
        
        # 按综合评分排序
        results.sort(key=lambda x: x['composite_score'], reverse=True)
        for i, r in enumerate(results):
            r['rank'] = i + 1
        
        return results
        
    except Exception as e:
        print(f"   ❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def save_theme_results(trade_date, results):
    """保存主题分析结果到数据库"""
    if not results:
        return
    
    db_path = os.path.join(safe_cache_dir, "theme_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS theme_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                theme TEXT NOT NULL,
                trend_score REAL,
                sentiment_score REAL,
                composite_score REAL,
                avg_ret_5 REAL,
                avg_ret_10 REAL,
                avg_ret_20 REAL,
                n_stocks INTEGER,
                zt_count INTEGER,
                up_ratio REAL,
                rank INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, theme)
            )
        ''')
        
        for r in results:
            cursor.execute('''
                INSERT OR REPLACE INTO theme_analysis 
                (trade_date, theme, trend_score, sentiment_score, composite_score,
                 avg_ret_5, avg_ret_10, avg_ret_20, n_stocks, zt_count, up_ratio, rank)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                r['theme'],
                r['trend_score'],
                r['sentiment_score'],
                r['composite_score'],
                r['avg_ret_5'],
                r['avg_ret_10'],
                r['avg_ret_20'],
                r['n_stocks'],
                r['zt_count'],
                r['up_ratio'],
                r['rank']
            ))
        
        conn.commit()
        print(f"   ✅ {trade_date} 数据已保存 ({len(results)} 个主题)")
        
    except Exception as e:
        print(f"   ❌ {trade_date} 保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def process_dates(trade_dates):
    """处理多个日期"""
    print(f"\n📊 开始回溯 {len(trade_dates)} 个交易日...")
    
    for i, trade_date in enumerate(trade_dates):
        print(f"\n[{i+1}/{len(trade_dates)}] 处理日期: {trade_date}")
        
        results = run_theme_analysis_for_date(trade_date)
        
        if results:
            save_theme_results(trade_date, results)
            
            # 打印前3名
            print(f"   📈 趋势TOP3:")
            for r in results[:3]:
                print(f"      {r['theme']}: 趋势{r['trend_score']:.1f} 情绪{r['sentiment_score']:.1f}")
        
        time.sleep(0.3)  # 避免请求过快
    
    print(f"\n🎉 回溯完成！共处理 {len(trade_dates)} 个交易日")

def main():
    """主函数"""
    if len(sys.argv) >= 3:
        # 指定日期范围
        start_date = sys.argv[1]
        end_date = sys.argv[2]
        print(f"🔄 正在回溯生成 {start_date} ~ {end_date} 的数据...")
        trade_dates = get_trade_dates(start_date, end_date)
    elif len(sys.argv) == 2:
        # 指定天数
        days = int(sys.argv[1])
        end_date = get_last_trade_date()
        start_date = (datetime.now() - timedelta(days=days + 10)).strftime('%Y%m%d')
        trade_dates = get_trade_dates(start_date, end_date)
        trade_dates = trade_dates[-days:]  # 只取最后N天
    else:
        # 默认60天
        days = 60
        end_date = get_last_trade_date()
        start_date = (datetime.now() - timedelta(days=days + 10)).strftime('%Y%m%d')
        trade_dates = get_trade_dates(start_date, end_date)
        trade_dates = trade_dates[-days:]  # 只取最后N天
    
    print(f"📅 目标日期: {trade_dates[0]} ~ {trade_dates[-1]} (共 {len(trade_dates)} 天)")
    
    process_dates(trade_dates)

if __name__ == '__main__':
    main()
