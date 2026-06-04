#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 daily_analysis_summarizer 的修改
"""

import sys
import os

# 添加当前目录到路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

print("=" * 70)
print("测试 daily_analysis_summarizer 的修改")
print("=" * 70)

# 首先测试读取60日趋势平均分
from daily_analysis_summarizer import read_60day_avg_trend_scores

print("\n1. 测试读取60日趋势平均分:")
try:
    avg_data = read_60day_avg_trend_scores()
    if avg_data and 'themes' in avg_data:
        print(f"✅ 成功读取 {len(avg_data['themes'])} 个主题的趋势平均分")
        print("\n前5个主题:")
        for i, theme in enumerate(avg_data['themes'][:5]):
            print(f"{i+1}. {theme['theme_name']}: 平均 {theme['avg_trend_score']:.1f} ({theme['day_count']}天)")
    else:
        print("⚠️ 未读取到60日趋势平均分数据")
except Exception as e:
    print(f"❌ 读取失败: {e}")

# 然后测试读取主题分析
from daily_analysis_summarizer import read_theme_analysis

print("\n2. 测试读取主题分析:")
try:
    theme_data = read_theme_analysis()
    if theme_data and 'themes' in theme_data:
        print(f"✅ 成功读取 {len(theme_data['themes'])} 个主题")
        print("\n前几个主题:")
        for i, theme in enumerate(theme_data['themes'][:5]):
            print(f"{i+1}. {theme['theme_name']}: 趋势 {theme['trend_score']:.1f}, 情绪 {theme['sentiment_score']:.1f}")
    else:
        print("⚠️ 未读取到主题分析数据")
except Exception as e:
    print(f"❌ 读取失败: {e}")

# 模拟一些有 theme_type 的数据来测试
print("\n3. 测试个股分组显示:")
mock_stock_data = {
    'stocks': [
        {'ts_code': '000001.SZ', 'name': '平安银行', 'close': 12.34, 'pct_chg': 2.3, 'market_cap': 1000, 
         'theme': 'AI算力链', 'theme_type': '中期趋势', 'buy_type': '中军', 'reason': '突破箱体'},
        {'ts_code': '000002.SZ', 'name': '万科A', 'close': 18.50, 'pct_chg': -1.2, 'market_cap': 2000, 
         'theme': '半导体', 'theme_type': '中期趋势', 'buy_type': '龙头首阴', 'reason': '首阴'},
        {'ts_code': '000004.SZ', 'name': '国华网安', 'close': 8.90, 'pct_chg': 5.6, 'market_cap': 500, 
         'theme': '电力链', 'theme_type': '短线主线', 'buy_type': '中军', 'reason': '均线温和'},
        {'ts_code': '000005.SZ', 'name': '世纪星源', 'close': 2.34, 'pct_chg': -0.5, 'market_cap': 100, 
         'theme': '煤炭链', 'theme_type': '短线主线', 'buy_type': '龙头首阴', 'reason': '首阴'},
    ]
}

from daily_analysis_summarizer import generate_summary
import datetime

# 模拟其他数据
mock_market_data = {
    'indices': [
        {'index_name': '上证指数', 'trend_status': '震荡偏弱', 'trend_score': 45.5, 
         'sentiment_status': '情绪温和', 'sentiment_score': 55.5, 'close_price': 3200.0, 'pct_change': 0.5}
    ],
    'overall': {'position': 30, 'reason': '测试数据'}
}

mock_theme_data = {
    'themes': [
        {'theme_name': 'AI算力链', 'trend_score': 65.5, 'sentiment_score': 45.5, 
         'trend_status': '上升趋势', 'sentiment_status': '情绪低迷', 'change': 2.5, 'volume_ratio': 1.2},
        {'theme_name': '电力链', 'trend_score': 60.0, 'sentiment_score': 50.0,
         'trend_status': '上升趋势', 'sentiment_status': '情绪温和', 'change': 1.5, 'volume_ratio': 1.1}
    ]
}

mock_avg_data = {
    'themes': [
        {'theme_name': 'AI算力链', 'avg_trend_score': 62.0, 'day_count': 60},
        {'theme_name': '半导体', 'avg_trend_score': 58.0, 'day_count': 60},
        {'theme_name': '电力链', 'avg_trend_score': 50.0, 'day_count': 60}
    ]
}

print("\n生成测试报告:")
try:
    report = generate_summary(mock_market_data, mock_theme_data, mock_stock_data, mock_avg_data, '20260603')
    print(report)
    print("\n✅ generate_summary 函数正常工作！")
except Exception as e:
    import traceback
    print(f"❌ 生成报告失败: {e}")
    print(traceback.format_exc())

print("\n" + "=" * 70)
print("测试完成！")
print("=" * 70)
