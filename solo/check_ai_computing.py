#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import tushare as ts
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', '.env'))

pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_backbone_tushare')

print("="*80)
print("检查AI算力板块的真实盘面数据 (20260529)")
print("="*80)

# 1. 读取主题投资组合
csv_pattern = os.path.join(CACHE_DIR, "theme_portfolio_*.csv")
import glob
csv_files = glob.glob(csv_pattern)
if csv_files:
    latest_file = max(csv_files, key=os.path.getmtime)
    print(f"读取主题组合: {latest_file}")
    df = pd.read_csv(latest_file, encoding='utf-8-sig')
    
    # 找出AI算力板块的股票
    ai_computing_stocks = df[df['themes'] == 'AI算力']['ts_code'].tolist()
    print(f"\nAI算力板块包含 {len(ai_computing_stocks)} 只股票")
    
    # 2. 获取涨跌停数据
    trade_date = '20260529'
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
    
    # 3. 检查AI算力板块的真实表现
    print(f"\n{'='*80}")
    print(f"AI算力板块个股表现:")
    print(f"{'='*80}")
    
    stock_changes = []
    zt_count = 0
    up_count = 0
    strong_count = 0
    dt_count = 0
    total_count = 0
    
    for ts_code in ai_computing_stocks[:30]:
        try:
            df = pro.daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
            if not df.empty and len(df) > 0:
                pct_chg = df['pct_chg'].iloc[0]
                stock_changes.append(pct_chg)
                total_count += 1
                
                is_zt = ts_code in zt_stocks
                is_dt = ts_code in dt_stocks
                
                if is_zt:
                    zt_count += 1
                    print(f"  [涨停] {ts_code}: {pct_chg:+.2f}%")
                elif is_dt:
                    dt_count += 1
                    print(f"  [跌停] {ts_code}: {pct_chg:+.2f}%")
                elif pct_chg > 0:
                    up_count += 1
                    if pct_chg >= 5:
                        strong_count += 1
                        print(f"  [强势] {ts_code}: {pct_chg:+.2f}%")
                    else:
                        print(f"  [上涨] {ts_code}: {pct_chg:+.2f}%")
                else:
                    print(f"  [下跌] {ts_code}: {pct_chg:+.2f}%")
        except Exception as e:
            continue
    
    # 计算统计数据
    print(f"\n{'='*80}")
    print(f"统计数据:")
    print(f"{'='*80}")
    avg_change = np.mean(stock_changes)
    up_ratio = up_count / total_count if total_count > 0 else 0
    strong_ratio = strong_count / total_count if total_count > 0 else 0
    
    print(f"平均涨跌幅: {avg_change:+.2f}%")
    print(f"涨停家数: {zt_count}")
    print(f"跌停家数: {dt_count}")
    print(f"上涨家数: {up_count} / {total_count} ({up_ratio:.1%})")
    print(f"强势股(>=5%): {strong_count} / {total_count} ({strong_ratio:.1%})")
    
    # 手动计算一下评分看看
    base_score = 50
    change_score = avg_change * 12  # 权重最大
    zt_score = min(zt_count * 6, 30)
    up_ratio_score = min(up_ratio * 60, 35)
    strong_score = min(strong_ratio * 90, 25)
    dt_penalty = min(dt_count * 10, 25)
    
    final_score = max(0, base_score + change_score + zt_score + up_ratio_score + strong_score - dt_penalty)
    
    print(f"\n{'='*80}")
    print(f"评分计算:")
    print(f"{'='*80}")
    print(f"基础分: 50")
    print(f"涨跌幅贡献: {change_score:+.1f}")
    print(f"涨停贡献: +{zt_score}")
    print(f"上涨占比贡献: +{up_ratio_score}")
    print(f"强势股贡献: +{strong_score}")
    print(f"跌停惩罚: -{dt_penalty}")
    print(f"{'='*80}")
    print(f"最终评分: {final_score:.1f}")
