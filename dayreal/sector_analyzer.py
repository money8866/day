#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块市场情绪分析器
基于本地缓存的板块数据计算市场情绪
"""
import sys
import os
import pickle
from datetime import datetime, timedelta

def get_sector_market_sentiment(trade_date=None):
    """
    基于板块数据计算市场情绪
    
    参数:
        trade_date: 交易日期，格式 YYYYMMDD，默认昨天
    
    返回:
        dict: {
            'up_ratio': 上涨比例 (0-1),
            'up_count': 上涨板块数,
            'down_count': 下跌板块数,
            'strong_count': 强势板块数 (涨幅>=3%),
            'weak_count': 弱势板块数 (跌幅>=3%),
            'total': 总板块数,
            'avg_change': 平均涨跌幅,
            'sentiment_score': 情绪评分 (-100 到 +100)
        }
    """
    try:
        # 直接读取缓存文件，不调用 Tushare
        if trade_date is None:
            # 默认使用昨天的数据
            yesterday = datetime.now() - timedelta(days=1)
            trade_date = yesterday.strftime('%Y%m%d')
        
        cache_dir = os.path.join(os.path.dirname(__file__), '..', 'dragon', 'cache')
        cache_file = os.path.join(cache_dir, f'ths_all_concepts_{trade_date}.pkl')
        
        if not os.path.exists(cache_file):
            print(f"[板块分析] 警告: 缓存文件不存在 {cache_file}")
            return None
        
        # 读取缓存
        with open(cache_file, 'rb') as f:
            concepts = pickle.load(f)
        
        print(f"[板块分析] 从缓存加载: {cache_file}")
        print(f"[板块分析] 板块数量: {len(concepts)}")
        
        if concepts is None or len(concepts) == 0:
            print("[板块分析] 警告: 缓存数据为空")
            return None
        
        # 计算上涨/下跌数量
        up_count = len(concepts[concepts['pct_change'] > 0])
        down_count = len(concepts[concepts['pct_change'] < 0])
        flat_count = len(concepts) - up_count - down_count
        total = len(concepts)
        
        # 计算上涨比例
        up_ratio = up_count / total if total > 0 else 0.5
        
        # 计算强势/弱势板块数量
        strong_count = len(concepts[concepts['pct_change'] >= 3.0])
        weak_count = len(concepts[concepts['pct_change'] <= -3.0])
        
        # 计算平均涨跌幅
        avg_change = concepts['pct_change'].mean()
        
        # 计算情绪评分 (-100 到 +100)
        # 基于上涨比例和平均涨跌幅
        sentiment_score = (up_ratio - 0.5) * 200 + avg_change * 10
        sentiment_score = max(-100, min(100, sentiment_score))
        
        result = {
            'up_ratio': up_ratio,
            'up_count': up_count,
            'down_count': down_count,
            'flat_count': flat_count,
            'strong_count': strong_count,
            'weak_count': weak_count,
            'total': total,
            'avg_change': avg_change,
            'sentiment_score': sentiment_score
        }
        
        print(f"\n[板块分析] 市场情绪统计:")
        print(f"  总板块数: {total}")
        print(f"  上涨: {up_count} ({up_ratio*100:.1f}%)")
        print(f"  下跌: {down_count} ({(1-up_ratio)*100:.1f}%)")
        print(f"  平盘: {flat_count}")
        print(f"  强势板块(>=3%): {strong_count}")
        print(f"  弱势板块(<=-3%): {weak_count}")
        print(f"  平均涨跌幅: {avg_change:.2f}%")
        print(f"  情绪评分: {sentiment_score:.1f}")
        
        return result
        
    except Exception as e:
        print(f"[板块分析] 错误: {e}")
        import traceback
        traceback.print_exc()
        return None

def calculate_composite_sentiment(index_sentiment, sector_sentiment):
    """
    综合指数情绪和板块情绪
    
    参数:
        index_sentiment: 指数情绪评分 (-100 到 +100)
        sector_sentiment: 板块情绪评分 (-100 到 +100)
    
    返回:
        float: 综合情绪评分 (-100 到 +100)
    """
    if sector_sentiment is None:
        print("[综合情绪] 板块数据缺失，仅使用指数情绪")
        return index_sentiment
    
    # 权重分配
    index_weight = 0.4   # 指数占40%
    sector_weight = 0.6   # 板块占60%
    
    composite = index_sentiment * index_weight + sector_sentiment['sentiment_score'] * sector_weight
    
    print(f"\n[综合情绪] 评分计算:")
    print(f"  指数情绪: {index_sentiment:.1f} (权重 {index_weight*100:.0f}%)")
    print(f"  板块情绪: {sector_sentiment['sentiment_score']:.1f} (权重 {sector_weight*100:.0f}%)")
    print(f"  综合评分: {composite:.1f}")
    
    return composite

if __name__ == '__main__':
    # 测试
    print("=" * 70)
    print("测试：板块市场情绪分析")
    print("=" * 70)
    
    result = get_sector_market_sentiment()
    
    if result:
        print("\n" + "=" * 70)
        print("测试结果：")
        print("=" * 70)
        for key, value in result.items():
            print(f"  {key}: {value}")
