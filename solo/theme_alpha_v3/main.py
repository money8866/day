#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 主程序
目标：寻找未来5~20个交易日最可能成为市场主线的主题
"""
import os
import sys
import json
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, BASE_DIR)

warnings.filterwarnings("ignore")

import config
import cache
import theme_builder
import data_loader
import trend
import capital
import sentiment
import persistence
import lifecycle
import leader
import risk
import composite

def main(trade_date=None):
    """主函数"""
    print("=" * 80)
    print("Theme Alpha Engine V3.0")
    print("=" * 80)
    
    if trade_date is None:
        trade_date = theme_builder.get_trade_date()
    
    print(f"\n[Date] 交易日: {trade_date}")
    
    # ==================== 第一步：构建主题池 ====================
    print(f"\n[Step 1] 构建主题池...")
    theme_universe = theme_builder.build_theme_universe()
    
    # ==================== 第二步：加载数据 ====================
    print(f"\n[Step 2] 加载数据...")
    
    # 日期范围
    dt = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (dt - timedelta(days=120)).strftime("%Y%m%d")
    
    # 所有主题股票
    all_theme_stocks = []
    for theme, stock_list in theme_universe.items():
        for s in stock_list:
            if isinstance(s, dict):
                all_theme_stocks.append(s.get("code", ""))
            else:
                all_theme_stocks.append(s)
    all_theme_stocks = list(set(all_theme_stocks))
    valid_theme_stocks = all_theme_stocks
    print(f"[Data] 主题股票: {len(valid_theme_stocks)}只")
    
    # 加载数据
    daily_df = data_loader.get_daily_data(valid_theme_stocks, start_date, trade_date)
    print(f"[Data] 日线数据: {len(daily_df)}条")
    
    basic_df = data_loader.get_daily_basic(valid_theme_stocks, trade_date)
    print(f"[Data] 基础数据: {len(basic_df)}条")
    
    moneyflow_df = pd.DataFrame()
    # moneyflow_df = data_loader.get_moneyflow(valid_theme_stocks, start_date, trade_date)
    # print(f"[Data] 资金流数据: {len(moneyflow_df)}条")
    
    limit_df = data_loader.get_limit_list(trade_date)
    print(f"[Data] 涨跌停数据: {len(limit_df)}条")
    
    top_df = data_loader.get_top_list(trade_date)
    print(f"[Data] 龙虎榜数据: {len(top_df)}条")
    
    hs300_df = data_loader.get_index_daily("000300.SH", start_date, trade_date)
    index_return = hs300_df.iloc[-1]['pct_chg'] if not hs300_df.empty else 0
    print(f"[Data] 沪深300当日收益: {index_return:.2f}%")
    
    # 计算全市场成交额
    all_market_turnover = 0
    if not daily_df.empty and 'amount' in daily_df.columns:
        all_market_turnover = daily_df['amount'].sum() / 100000000
    print(f"[Data] 全市场成交额: {all_market_turnover:.1f}亿元")
    
    # ==================== 第三步：逐个主题计算 ====================
    print(f"\n[Step 3] 计算主题评分...")
    
    results = []
    
    for i, (theme_name, stock_list) in enumerate(theme_universe.items()):
        if (i + 1) % 5 == 0:
            print(f"  进度: {i+1}/{len(theme_universe)}...")
        
        valid_theme_stocks = []
        for s in stock_list:
            if isinstance(s, dict):
                valid_theme_stocks.append(s.get("code", ""))
            else:
                valid_theme_stocks.append(s)
        
        if len(valid_theme_stocks) < 3:
            continue
        
        # 计算各维度评分
        trend_score = trend.calculate_trend_score(daily_df, valid_theme_stocks)
        capital_score = capital.calculate_capital_score(daily_df, moneyflow_df, valid_theme_stocks, all_market_turnover)
        sentiment_score = sentiment.calculate_sentiment_score(daily_df, limit_df, valid_theme_stocks, index_return)
        persistence_score = persistence.calculate_persistence_score(daily_df, valid_theme_stocks)
        risk_score = risk.calculate_risk_score(daily_df, valid_theme_stocks)
        
        # 识别龙头
        leader_code, leader_score = leader.identify_leader(daily_df, top_df, valid_theme_stocks)
        
        # 识别生命周期
        stage = lifecycle.identify_lifecycle_stage(
            trend_score, sentiment_score, capital_score
        )
        lifecycle_bonus = lifecycle.calculate_lifecycle_score(stage)
        
        # 综合评分
        composite_score = composite.calculate_composite_score(
            trend_score, capital_score, sentiment_score,
            persistence_score, lifecycle_bonus, leader_score, risk_score
        )
        
        # 交易信号
        trade_signal = composite.generate_trade_signal(
            composite_score, capital_score, trend_score, stage
        )
        
        # 置信度
        confidence = composite.calculate_confidence(composite_score, trend_score, capital_score)
        
        results.append({
            "theme": theme_name,
            "stage": stage,
            "leader": leader_code,
            "trend_score": round(trend_score, 2),
            "capital_score": round(capital_score, 2),
            "sentiment_score": round(sentiment_score, 2),
            "persistence_score": round(persistence_score, 2),
            "lifecycle_score": lifecycle_bonus,
            "risk_score": round(risk_score, 2),
            "leader_score": round(leader_score, 2),
            "composite_score": round(composite_score, 2),
            "confidence": round(confidence, 2),
            "trade_signal": trade_signal
        })
    
    # ==================== 第四步：排序输出 ====================
    print(f"\n[Step 4] 排序输出...")
    
    if results:
        df = pd.DataFrame(results)
        df = df.sort_values('composite_score', ascending=False).reset_index(drop=True)
        
        # 输出JSON
        with open(config.OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(df.to_dict('records'), f, ensure_ascii=False, indent=2)
        
        # 输出CSV
        df.to_csv(config.OUTPUT_CSV, index=False, encoding='utf-8-sig')
        
        print(f"\n[Output] 结果已保存: {config.OUTPUT_JSON}")
        print(f"[Output] 结果已保存: {config.OUTPUT_CSV}")
        
        # 打印TOP10
        print(f"\n{'='*80}")
        print(f"TOP10 主题排名")
        print(f"{'='*80}")
        
        for i, row in df.head(10).iterrows():
            print(f"{i+1:2d}. {row['theme']:<20} {row['stage']:<15} "
                  f"综合: {row['composite_score']:5.1f} 信号: {row['trade_signal']}")
        
        print(f"\n{'='*80}")
        print(f"共分析: {len(df)}个主题")
        print(f"{'='*80}")
    else:
        print(f"未找到有效主题结果")
    
    print(f"\n[Done] 运行完成!")
    return results

if __name__ == "__main__":
    results = main()
