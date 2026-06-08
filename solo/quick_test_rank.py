
# -*- coding: utf-8 -*-
"""快速测试排名脚本
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, calc_hot_money_open_score


def test_rank():
    print("快速测试排名逻辑")
    
    # 手动构造一些数据来测试排名
    test_stocks = [
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
    
    for stock in test_stocks:
        code = stock['code']
        name = stock['name']
        
        df = get_hist_data(code)
        if df is not None and len(df) &gt;= 20:
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
            })
    
    # 排序并显示
    results_sorted = sorted(results, key=lambda x: x['open_score'], reverse=True)
    
    print("\n最终排名：")
    for i, r in enumerate(results_sorted, 1):
        print(f"\n{i}. {r['name']} ({r['code']})")
        print(f"   主题: {r['theme']} (纯度: {r['theme_confidence']}%)")
        print(f"   开仓分: {r['open_score']:.2f}")
        print(f"   结构: {r['structure_type']}")


if __name__ == "__main__":
    test_rank()
