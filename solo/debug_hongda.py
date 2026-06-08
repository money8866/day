
# -*- coding: utf-8 -*-
"""调试宏达电子评分细节"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import calc_dual_layer_score_v75, get_hist_data


def analyze_score_detail(df, ts_code, name):
    """详细分析评分计算过程"""
    print("\n" + "="*80)
    print(f"股票: {ts_code} ({name})")
    print("="*80)
    
    # 获取评分结果
    result = calc_dual_layer_score_v75(df, ts_code)
    
    print("\n【V7.5评分结果】")
    for key, value in result.items():
        print(f"  {key}: {value}")
    
    print("\n" + "="*80)
    print("【详细计算分解】")
    print("="*80)
    
    # 手动重复V7.5的计算逻辑，展示每一步
    C = df['close']
    
    from tushare_quant import calc_dual_layer_score_v6
    v6_result = calc_dual_layer_score_v6(df, ts_code)
    
    trend_probability = float(v6_result.get('趋势概率', 0.5))
    fail_prob = float(v6_result.get('失败概率', 0.5))
    breakout_strength = float(v6_result.get('突破强度', 0.5))
    money_momentum = float(v6_result.get('资金动量', 0.5))
    trend_stability = float(v6_result.get('趋势稳定', 0.5))
    volume_explosion = float(v6_result.get('量能爆发', 0.5))
    compression_score = float(v6_result.get('压缩度', 0.5))
    trend_strength = float(v6_result.get('趋势强度', 0.5))
    
    print(f"\n【V6技术指标】")
    print(f"  趋势强度: {trend_strength:.4f}")
    print(f"  趋势概率: {trend_probability:.4f}")
    print(f"  资金动量: {money_momentum:.4f}")
    print(f"  突破强度: {breakout_strength:.4f}")
    print(f"  量能爆发: {volume_explosion:.4f}")
    print(f"  趋势稳定: {trend_stability:.4f}")
    print(f"  压缩度: {compression_score:.4f}")
    print(f"  失败概率: {fail_prob:.4f}")
    
    # 模拟计算主题相关
    theme_confidence = result.get('主题纯度', 30)
    
    # 计算龙头因子
    leader_factor = (
        trend_strength * 0.40 +
        money_momentum * 0.35 +
        trend_probability * 0.25
    )
    print(f"\n【主题与龙头】")
    print(f"  主题真实性/纯度: {theme_confidence:.2f}")
    print(f"  龙头因子: {leader_factor:.4f}")
    
    # 计算基础分
    print(f"\n【基础分计算】")
    print(f"  1. 趋势强度      : {trend_strength * 100 * 15:.2f} = {trend_strength:.4f} × 100 × 15")
    print(f"  2. 趋势概率      : {trend_probability * 100 * 15:.2f} = {trend_probability:.4f} × 100 × 15")
    print(f"  3. 主题真实性    : {theme_confidence * 15:.2f} = {theme_confidence:.2f} × 15")
    print(f"  4. 主题纯度      : {theme_confidence * 12:.2f} = {theme_confidence:.2f} × 12")
    print(f"  5. 龙头因子      : {leader_factor * 100 * 12:.2f} = {leader_factor:.4f} × 100 × 12")
    print(f"  6. 资金动量      : {money_momentum * 100 * 10:.2f} = {money_momentum:.4f} × 100 × 10")
    print(f"  7. 突破强度      : {breakout_strength * 100 * 8:.2f} = {breakout_strength:.4f} × 100 × 8")
    print(f"  8. 量能爆发      : {volume_explosion * 100 * 8:.2f} = {volume_explosion:.4f} × 100 × 8")
    print(f"  9. 趋势稳定      : {trend_stability * 100 * 3:.2f} = {trend_stability:.4f} × 100 × 3")
    print(f" 10. 压缩度        : {compression_score * 100 * 2:.2f} = {compression_score:.4f} × 100 × 2")
    
    base_score_raw = (
        trend_strength * 100 * 15 +
        trend_probability * 100 * 15 +
        theme_confidence * 15 +
        theme_confidence * 12 +
        leader_factor * 100 * 12 +
        money_momentum * 100 * 10 +
        breakout_strength * 100 * 8 +
        volume_explosion * 100 * 8 +
        trend_stability * 100 * 3 +
        compression_score * 100 * 2
    )
    base_score = base_score_raw / 100
    
    print(f"\n  基础分(未归一): {base_score_raw:.2f}")
    print(f"  基础分(归一化): {base_score:.2f}")
    
    # 失败概率惩罚
    failure_penalty = fail_prob * 20
    print(f"\n  失败概率惩罚: -{failure_penalty:.2f} = {fail_prob:.4f} × 20")
    
    # 总分计算
    v75_total = (base_score - failure_penalty) * 1.3
    print(f"\n  总分(前): {base_score - failure_penalty:.2f}")
    print(f"  总分(×1.3): {v75_total:.2f}")
    
    return result


def main():
    stock_list = [
        ('300726.SZ', '宏达电子'),
        ('601138.SH', '工业富联'),
        ('002747.SZ', '埃斯顿'),
    ]
    
    for ts_code, name in stock_list:
        try:
            df = get_hist_data(ts_code)
            if df is not None and len(df) >= 60:
                analyze_score_detail(df, ts_code, name)
            else:
                print(f"\n股票 {ts_code} 数据不足")
        except Exception as e:
            print(f"\n分析 {ts_code} 出错: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

