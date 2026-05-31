#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import tushare as ts
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', '.env'))

pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_backbone_tushare')


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


def get_trade_dates(n_days=5):
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20250101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    cal = cal.sort_values('cal_date', ascending=False)
    trade_dates = cal['cal_date'].head(n_days).tolist()
    trade_dates.reverse()
    return [str(d) for d in trade_dates]


def calculate_today_theme_scores(trade_date='20260529'):
    """
    完全基于今日盘面数据重新计算主题评分
    评分逻辑：
    1. 今日板块平均涨跌幅（权重最大）
    2. 今日涨停家数
    3. 今日板块强势度（涨停占比、上涨占比）
    4. 炸板率惩罚
    """
    import glob
    
    print(f"\n{'='*80}")
    print(f"重新计算主题评分 - 完全基于 {trade_date} 今日盘面数据")
    print(f"{'='*80}")
    
    # 1. 加载主题投资组合
    theme_stocks_map, name_map = load_theme_portfolio_from_csv()
    
    # 2. 获取今日涨跌停数据
    print("\n获取今日涨跌停数据...")
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
    
    # 3. 计算每个主题的今日表现
    theme_scores = {}
    theme_details = {}
    
    for theme, stocks in theme_stocks_map.items():
        if len(stocks) < 3:
            continue
            
        stock_changes = []
        zt_count = 0
        up_count = 0
        total_count = 0
        
        for ts_code in stocks[:30]:  # 每个主题只看前30只
            try:
                df = pro.daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
                if not df.empty and len(df) > 0:
                    pct_chg = df['pct_chg'].iloc[0]
                    stock_changes.append(pct_chg)
                    total_count += 1
                    
                    if ts_code in zt_stocks:
                        zt_count += 1
                    if pct_chg > 0:
                        up_count += 1
            except Exception as e:
                continue
        
        if len(stock_changes) < 3:
            continue
            
        avg_change = np.mean(stock_changes)
        up_ratio = up_count / total_count if total_count > 0 else 0
        zt_ratio = zt_count / total_count if total_count > 0 else 0
        
        # ========== 游资风格的评分逻辑 ==========
        # 核心：今日涨跌幅权重最大
        
        base_score = 50
        
        # 1. 今日板块平均涨跌幅（核心权重）
        change_score = avg_change * 10
        base_score += change_score
        
        # 2. 涨停家数加分
        zt_score = min(zt_count * 5, 25)
        base_score += zt_score
        
        # 3. 板块上涨占比加分
        up_ratio_score = min(up_ratio * 50, 30)
        base_score += up_ratio_score
        
        # 4. 强势股加分（涨幅超过5%的比例）
        strong_stocks = sum(1 for x in stock_changes if x >= 5)
        strong_ratio = strong_stocks / total_count if total_count > 0 else 0
        strong_score = min(strong_ratio * 80, 20)
        base_score += strong_score
        
        # 5. 如果有跌停要扣分
        dt_in_theme = sum(1 for s in stocks if s in dt_stocks)
        dt_penalty = min(dt_in_theme * 8, 20)
        base_score -= dt_penalty
        
        theme_scores[theme] = max(0, base_score)
        theme_details[theme] = {
            'avg_change': avg_change,
            'zt_count': zt_count,
            'up_ratio': up_ratio,
            'strong_ratio': strong_ratio,
            'dt_count': dt_in_theme,
            'total_stocks': total_count
        }
    
    # 排序
    ranked_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"今日主题TOP 10（完全基于盘面数据）")
    print(f"{'='*80}")
    
    for rank, (theme, score) in enumerate(ranked_themes[:10], 1):
        detail = theme_details[theme]
        print(f"\n{rank}. 【{theme}】")
        print(f"   评分: {score:.1f}")
        print(f"   今日平均涨跌幅: {detail['avg_change']:+.2f}%")
        print(f"   涨停: {detail['zt_count']} 家 | 上涨占比: {detail['up_ratio']:.1%}")
        print(f"   强势股(>=5%): {detail['strong_ratio']:.1%} | 跌停: {detail['dt_count']} 家")
    
    return ranked_themes, theme_scores, theme_details, theme_stocks_map, name_map


if __name__ == "__main__":
    calculate_today_theme_scores()
