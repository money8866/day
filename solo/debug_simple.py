
# -*- coding: utf-8 -*-
"""简单对比脚本
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, calc_dual_layer_score_v75, calc_hot_money_open_score


def main():
    print("对比埃斯顿和中富电路")
    
    stock1 = ('002747.SZ', '埃斯顿')
    stock2 = ('300814.SZ', '中富电路')
    
    results = []
    
    for code, name in [stock1, stock2]:
        df = get_hist_data(code)
        if df is not None and len(df) >= 60:
            v7_result = calc_dual_layer_score_v75(df, code)
            open_score, structure_type, recommendation = calc_hot_money_open_score(
                v7_result, df, None, v7_result.get('所属主题', None)
            )
            
            results.append({
                'name': name,
                'code': code,
                'open_score': open_score,
                'v7_result': v7_result,
                'structure_type': structure_type,
                'recommendation': recommendation,
            })
    
    # 排序并显示
    results_sorted = sorted(results, key=lambda x: x['open_score'], reverse=True)
    for i, r in enumerate(results_sorted, 1):
        print(f"\n{i}. {r['name']} ({r['code']})")
        print(f"   开仓评分: {r['open_score']}")
        print(f"   结构类型: {r['structure_type']}")
        print(f"   主题纯度: {r['v7_result'].get('主题纯度', 30)}")
        print(f"   所属主题: {r['v7_result'].get('所属主题', None)}")
        print(f"   失败概率: {r['v7_result'].get('失败概率', 0.5):.4f}")
        print(f"   推荐理由: {r['recommendation']}")


if __name__ == "__main__":
    main()
