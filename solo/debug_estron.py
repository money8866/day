
# -*- coding: utf-8 -*-
"""调试埃斯顿开仓评分数据"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import get_hist_data, calc_dual_layer_score_v75, calc_hot_money_open_score


def debug_estron():
    """调试埃斯顿的开仓评分"""
    print("\n" + "="*80)
    print("调试埃斯顿 (002747.SZ) 开仓评分")
    print("="*80)
    
    # 获取数据
    df = get_hist_data('002747.SZ')
    if df is None or len(df) < 60:
        print("数据不足")
        return
    
    # 计算V7.5评分
    v7_result = calc_dual_layer_score_v75(df, '002747.SZ')
    
    print("\n【V7.5评分结果】")
    for key, value in v7_result.items():
        print(f"  {key}: {value}")
    
    # 计算游资开仓评分
    open_score, structure_type, recommendation = calc_hot_money_open_score(
        v7_result, df, None, v7_result.get('所属主题', '')
    )
    
    print("\n【游资开仓评分结果】")
    print(f"  开仓评分: {open_score}")
    print(f"  结构类型: {structure_type}")
    print(f"  推荐理由: {recommendation}")
    
    # 获取今日涨幅
    today_pct = v7_result.get('涨跌幅', 0)
    print(f"\n【关键数据】")
    print(f"  今日涨幅: {today_pct:.2f}%")
    print(f"  是否涨停: {today_pct >= 9.9}%")
    
    # 检查20日高点
    close = df['close']
    high = df['high']
    current_price = close.iloc[-1]
    HHV20 = high.tail(20).max()
    
    print(f"\n【结构分析】")
    print(f"  当前价格: {current_price:.2f}")
    print(f"  20日最高价: {HHV20:.2f}")
    print(f"  价格/20日高点: {current_price / HHV20:.2%}")
    
    # 检查条件
    print(f"\n【启动型判断条件】")
    print(f"  current_price >= HHV20 * 0.95: {current_price >= HHV20 * 0.95}")
    print(f"  1 < today_pct <= 10: {1 < today_pct <= 10}")
    
    # 显示近期K线
    print(f"\n【近期K线】")
    print(df[['date', 'open', 'high', 'low', 'close', 'vol', 'pct_change']].tail(5))
    
    # 检查数据源
    print(f"\n【数据源检查】")
    print(f"  df类型: {type(df)}")
    print(f"  df长度: {len(df)}")
    print(f"  最新日期: {df['date'].iloc[-1]}")
    print(f"  最新收盘价: {df['close'].iloc[-1]:.2f}")
    print(f"  最新涨幅: {df['pct_change'].iloc[-1]:.2f}%")
    print(f"  最高价: {df['high'].iloc[-1]:.2f}")


if __name__ == "__main__":
    debug_estron()
