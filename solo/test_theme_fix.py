
# -*- coding: utf-8 -*-
"""测试主题修复脚本
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, calc_hot_money_open_score


def test_theme_fix():
    print("测试主题修复")
    
    # 构造一些测试数据
    stock_list = [
        {
            'code': '002747.SZ',
            'name': '埃斯顿',
            'theme': '人形机器人',
            'theme_confidence': 100.0,
        },
        {
            'code': '300814.SZ',
            'name': '中富电路',
            'theme': 'AI算力链',
            'theme_confidence': 80.0,
        },
        {
            'code': '301205.SZ',
            'name': '联特科技',
            'theme': 'AI算力链',
            'theme_confidence': 80.0,
        },
    ]
    
    results = []
    
    for stock in stock_list:
        code = stock['code']
        name = stock['name']
        
        df = get_hist_data(code)
        if df is not None and len(df) &gt;= 60:
            # 构造v7_result
            v7_result = {
                '代码': code,
                '名称': name,
                '现价': df['close'].iloc[-1],
                '涨跌幅': ((df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100) if len(df) &gt;= 2 else 0,
                '所属主题': stock['theme'],
                'V7总评分': 60.0,
                '趋势概率': 0.5,
                '失败概率': 0.35,
                '洗盘概率': 0.5,
                '趋势强度': 0.8,
                '趋势稳定': 0.6,
                '资金动量': 0.45,
                '突破强度': 0.35,
                '压缩度': 0.3,
                '量能爆发': 0.35,
                '主题纯度': stock['theme_confidence'],
            }
            
            open_score, structure_type, recommendation = calc_hot_money_open_score(
                v7_result, df, None, stock['theme']
            )
            
            results.append({
                'name': name,
                'code': code,
                'theme': stock['theme'],
                'theme_confidence': stock['theme_confidence'],
                'open_score': open_score,
                'structure_type': structure_type,
                'recommendation': recommendation,
            })
    
    # 排序并显示
    results_sorted = sorted(results, key=lambda x: x['open_score'], reverse=True)
    
    print("\n排序结果：")
    for i, r in enumerate(results_sorted, 1):
        print(f"\n{i}. {r['name']} ({r['code']})")
        print(f"   所属主题: {r['theme']}")
        print(f"   主题纯度: {r['theme_confidence']}")
        print(f"   开仓评分: {r['open_score']}")
        print(f"   推荐理由: {r['recommendation']}")


if __name__ == "__main__":
    test_theme_fix()
