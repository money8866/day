
# -*- coding: utf-8 -*-
"""对比埃斯顿和中富电路的开仓评分细节
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, calc_dual_layer_score_v75, calc_hot_money_open_score


def compare_stocks():
    stock_list = [
        ('002747.SZ', '埃斯顿'),
        ('300814.SZ', '中富电路'),
        ('301205.SZ', '联特科技'),
    ]

    for code, name in stock_list:
        print(f"\n{'='*80}")
        print(f"{name} ({code})")
        print(f"{'='*80}")

        # 获取数据
        df = get_hist_data(code)
        if df is None or len(df) &lt; 60:
            print("数据不足")
            continue

        # 计算V7.5评分
        v7_result = calc_dual_layer_score_v75(df, code)

        print("\n【V7.5评分结果：")
        for k, v in v7_result.items():
            print(f"  {k}: {v}")

        # 计算游资开仓评分
        open_score, structure_type, recommendation = calc_hot_money_open_score(
            v7_result, df, None, v7_result.get('所属主题', None)
        )

        print(f"\n【游资开仓评分】")
        print(f"  开仓评分: {open_score}")
        print(f"  结构类型: {structure_type}")
        print(f"  推荐理由: {recommendation}")

        # 显示关键指标
        theme_name = v7_result.get('所属主题', None)
        theme_confidence = v7_result.get('主题纯度', 30)
        fail_prob = v7_result.get('失败概率', 0.5)

        print(f"\n【关键主题指标：")
        print(f"  所属主题: {theme_name}")
        print(f"  主题纯度: {theme_confidence}")
        print(f"  失败概率: {fail_prob:.4f}")


if __name__ == "__main__":
    compare_stocks()
