
# -*- coding: utf-8 -*-
"""调试位置因子和新的V7.5评分公式"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import calc_dual_layer_score_v75, get_hist_data, calc_position_factor


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
    from tushare_quant import calc_dual_layer_score_v6
    v6_result = calc_dual_layer_score_v6(df, ts_code)
    
    trend_strength = float(v6_result.get('趋势强度', 0.5))
    trend_probability = float(v6_result.get('趋势概率', 0.5))
    fail_prob = float(v6_result.get('失败概率', 0.5))
    breakout_strength = float(v6_result.get('突破强度', 0.5))
    money_momentum = float(v6_result.get('资金动量', 0.5))
    trend_stability = float(v6_result.get('趋势稳定', 0.5))
    volume_explosion = float(v6_result.get('量能爆发', 0.5))
    compression_score = float(v6_result.get('压缩度', 0.5))
    
    # 获取主题相关数据
    theme_confidence = result.get('主题纯度', 30)
    theme_rank_bonus = result.get('主题排名加成', 0)
    leader_factor = result.get('龙头因子', 0.5)
    
    # 计算位置因子
    position_factor = calc_position_factor(df)
    
    print(f"\n【各因子原始值】")
    print(f"  trend_strength    : {trend_strength:.4f}")
    print(f"  trend_probability : {trend_probability:.4f}")
    print(f"  money_momentum    : {money_momentum:.4f}")
    print(f"  breakout_strength : {breakout_strength:.4f}")
    print(f"  volume_explosion  : {volume_explosion:.4f}")
    print(f"  trend_stability   : {trend_stability:.4f}")
    print(f"  compression_score : {compression_score:.4f}")
    print(f"  leader_factor     : {leader_factor:.4f}")
    print(f"  theme_confidence  : {theme_confidence:.4f}")
    print(f"  theme_rank_bonus  : {theme_rank_bonus:.4f}")
    print(f"  fail_prob         : {fail_prob:.4f}")
    print(f"  position_factor   : {position_factor:.4f}")
    
    print(f"\n【基础分计算】")
    print(f"  trend_strength * 20          = {trend_strength * 20:.2f}")
    print(f"  trend_probability * 15       = {trend_probability * 15:.2f}")
    print(f"  money_momentum * 15          = {money_momentum * 15:.2f}")
    print(f"  breakout_strength * 12       = {breakout_strength * 12:.2f}")
    print(f"  volume_explosion * 10        = {volume_explosion * 10:.2f}")
    print(f"  trend_stability * 8          = {trend_stability * 8:.2f}")
    print(f"  compression_score * 5        = {compression_score * 5:.2f}")
    print(f"  leader_factor * 10           = {leader_factor * 10:.2f}")
    print(f"  theme_confidence * 0.5       = {theme_confidence * 0.5:.2f}")
    print(f"  theme_rank_bonus             = {theme_rank_bonus:.2f}")
    
    score = (
        trend_strength * 20 +
        trend_probability * 15 +
        money_momentum * 15 +
        breakout_strength * 12 +
        volume_explosion * 10 +
        trend_stability * 8 +
        compression_score * 5 +
        leader_factor * 10 +
        theme_confidence * 0.5 +
        theme_rank_bonus
    )
    
    print(f"  基础分合计                    = {score:.2f}")
    
    # 应用位置因子
    score_after_pos = score * (0.8 + position_factor * 0.4)
    print(f"\n  应用位置因子后 (score * (0.8 + position_factor*0.4)): {score:.2f} * (0.8 + {position_factor:.4f}*0.4) = {score_after_pos:.2f}")
    
    # 减去失败概率惩罚
    score_after_fail = score_after_pos - fail_prob * 5
    print(f"  减去失败概率惩罚 (fail_prob * 5): {score_after_pos:.2f} - {fail_prob:.4f} * 5 = {score_after_fail:.2f}")
    
    # 放大评分
    final_score = score_after_fail * 1.3
    print(f"  放大1.3倍后                  = {final_score:.2f}")
    
    return result


def main():
    stock_list = [
        ('300726.SZ', '宏达电子'),
        ('601138.SH', '工业富联'),
        ('002747.SZ', '埃斯顿'),
        ('300814.SZ', '中富电路'),
        ('301205.SZ', '联特科技'),
    ]
    
    results = []
    for ts_code, name in stock_list:
        try:
            df = get_hist_data(ts_code)
            if df is not None and len(df) >= 120:
                result = analyze_score_detail(df, ts_code, name)
                results.append({
                    'code': ts_code,
                    'name': name,
                    'score': result.get('V7总评分', 0),
                    'fail_prob': result.get('失败概率', 0),
                    'pos_factor': result.get('位置因子', 0),
                })
            else:
                print(f"\n股票 {ts_code} 数据不足")
        except Exception as e:
            print(f"\n分析 {ts_code} 出错: {e}")
            import traceback
            traceback.print_exc()
    
    # 排序并显示
    print("\n" + "="*80)
    print("【最终排名】")
    print("="*80)
    results_sorted = sorted(results, key=lambda x: x['score'], reverse=True)
    for i, r in enumerate(results_sorted, 1):
        print(f"  {i}. {r['name']} ({r['code']}) - 评分: {r['score']:.2f}, 失败概率: {r['fail_prob']:.2%}, 位置因子: {r['pos_factor']:.2f}")


if __name__ == "__main__":
    main()

