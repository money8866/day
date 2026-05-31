#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位动态优化示例
展示如何使用开盘30分钟情绪分析优化盘后仓位建议
"""

import sys
sys.path.insert(0, '/workspace')

from position_optimizer import PositionOptimizer, TradingTimeHelper
from config import Config
from tdx_data import TdxData
from technical_analysis import calculate_change_percent, analyze_concept_intraday


def main():
    print("=" * 60)
    print("仓位动态优化示例")
    print("=" * 60)
    
    # 1. 初始化
    config = Config()
    tdx = TdxData()
    optimizer = PositionOptimizer()
    
    # 2. 检查是否在交易时间
    if not TradingTimeHelper.is_trading_time():
        print("\n当前不在交易时间，示例使用模拟数据")
        run_mock_example()
        return
    
    print("\n当前在交易时间，正在获取真实数据...")
    
    # 3. 连接通达信
    if not tdx.connect():
        print("连接失败，使用模拟数据")
        run_mock_example()
        return
    
    print("连接成功！")
    
    # 4. 获取概念板块股票
    concepts = config.load_concepts()
    stock_list = []
    concept_stock_map = {}
    
    for concept in concepts:
        concept_stocks = tdx.get_concept_stocks(concept)
        if concept_stocks:
            concept_stock_map[concept] = concept_stocks
            stock_list.extend(concept_stocks)
    
    # 5. 获取实时行情
    quotes = tdx.get_stock_quotes(stock_list)
    if not quotes:
        print("获取行情失败，使用模拟数据")
        tdx.disconnect()
        run_mock_example()
        return
    
    # 6. 准备行情数据
    quote_data = []
    for q in quotes:
        change_pct = calculate_change_percent(q)
        quote_data.append({
            "code": q.get("code", ""),
            "name": q.get("name", ""),
            "price": q.get("price", 0),
            "change_pct": change_pct
        })
    
    # 7. 分析板块
    concept_analysis = None
    if concepts and concept_stock_map:
        first_concept = concepts[0]
        first_concept_stocks = concept_stock_map.get(first_concept, [])
        
        concept_quotes = []
        for market, code in first_concept_stocks:
            for q in quote_data:
                if q["code"] == code:
                    concept_quotes.append(q)
        
        if concept_quotes:
            concept_analysis = analyze_concept_intraday(concept_quotes, first_concept)
    
    # 8. 优化仓位（假设盘后建议是50%仓位）
    base_position = 0.5
    print(f"\n盘后建议基础仓位: {base_position * 100:.1f}%")
    
    result = optimizer.optimize_position(base_position, quote_data, concept_analysis)
    
    # 9. 输出结果
    print("\n" + "=" * 60)
    print("仓位优化结果")
    print("=" * 60)
    print(f"情绪评分: {result['sentiment_score']} ({result['market_type']})")
    print(f"基础仓位: {result['base_position'] * 100:.1f}%")
    print(f"情绪调整: {result['position_adjustment'] * 100:+.1f}%")
    print(f"板块加成: {result['concept_bonus'] * 100:+.1f}%")
    print(f"最终仓位: {result['final_position'] * 100:.1f}%")
    print(f"\n操作建议: {result['suggestion']}")
    
    print("\n市场数据:")
    sentiment = result['sentiment_data']
    print(f"  上涨家数: {sentiment['up_count']}")
    print(f"  下跌家数: {sentiment['down_count']}")
    print(f"  涨跌比: {sentiment['up_down_ratio']:.2f}")
    print(f"  涨停家数: {sentiment['limit_up_count']}")
    print(f"  跌停家数: {sentiment['limit_down_count']}")
    print(f"  平均涨跌幅: {sentiment['avg_change']:.2f}%")
    
    if concept_analysis:
        print(f"\n板块分析 ({concept_analysis['concept_name']}):")
        print(f"  板块平均涨幅: {concept_analysis['avg_change']:.2f}%")
        print(f"  涨停家数: {concept_analysis['limit_up_count']}")
        print(f"  龙头: {concept_analysis['leader']['name']} {concept_analysis['leader']['change_pct']:.2f}%")
    
    tdx.disconnect()
    print("\n分析完成！")


def run_mock_example():
    """使用模拟数据运行示例"""
    print("\n" + "=" * 60)
    print("模拟数据示例")
    print("=" * 60)
    
    optimizer = PositionOptimizer()
    
    # 模拟强势市场行情
    mock_quotes = []
    for i in range(100):
        if i < 60:  # 60%上涨
            mock_quotes.append({
                "code": f"000{i:03d}",
                "name": f"股票{i}",
                "price": 10,
                "change_pct": 3 if i < 30 else 1
            })
        elif i < 80:  # 20%涨停
            mock_quotes.append({
                "code": f"000{i:03d}",
                "name": f"股票{i}",
                "price": 10,
                "change_pct": 10
            })
        else:  # 20%下跌
            mock_quotes.append({
                "code": f"000{i:03d}",
                "name": f"股票{i}",
                "price": 10,
                "change_pct": -1
            })
    
    # 模拟强势板块
    mock_concept = {
        "concept_name": "AI概念",
        "avg_change": 4.5,
        "limit_up_count": 8,
        "strong_stock_count": 15,
        "leader": {"name": "AI龙头", "code": "000001", "change_pct": 10},
        "top_5": []
    }
    
    # 优化仓位
    base_position = 0.5
    result = optimizer.optimize_position(base_position, mock_quotes, mock_concept)
    
    # 输出结果
    print(f"盘后建议基础仓位: {base_position * 100:.1f}%")
    print(f"\n情绪评分: {result['sentiment_score']} ({result['market_type']})")
    print(f"最终仓位: {result['final_position'] * 100:.1f}%")
    print(f"\n操作建议: {result['suggestion']}")
    
    print("\n模拟市场数据:")
    print(f"  上涨家数: 80, 下跌家数: 20")
    print(f"  涨停家数: 20, 跌停家数: 0")
    print(f"  平均涨跌幅: 2.5%")
    
    print("\n" + "=" * 60)
    print("不同情绪场景示例")
    print("=" * 60)
    
    scenarios = [
        ("强势市场", 0.6, 75),
        ("震荡市场", 0.5, 10),
        ("弱势市场", 0.4, -60)
    ]
    
    for scenario, base_pos, score in scenarios:
        mock_quote = [{"change_pct": i * 0.5} for i in range(-10, 20)]
        result = optimizer.optimize_position(base_pos, mock_quote)
        print(f"\n{scenario}:")
        print(f"  基础仓位: {base_pos * 100:.0f}%")
        print(f"  最终仓位: {result['final_position'] * 100:.0f}%")
        print(f"  建议: {result['suggestion']}")


if __name__ == "__main__":
    main()
