#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析评分系统各因子的详细值
"""
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import (
    calc_dual_layer_score_v6,
    get_hist_data,
    calc_trend_strength_v2,
    calc_trend_stability2,
    calc_volume_structure,
    calc_accumulation_factor,
    calc_big_money_factor,
)


def debug_score_detail(ts_code, name):
    print(f"\n{'='*70}")
    print(f"详细分析: {name} ({ts_code})")
    print(f"{'='*70}\n")
    
    df = get_hist_data(ts_code)
    if df is None or len(df) < 60:
        print(f"数据不足")
        return
    
    print(f"K线数据: {len(df)} 条")
    print(f"最新收盘价: {df['close'].iloc[-1]:.2f}")
    print(f"最新日期: {df['trade_date'].iloc[-1]}\n")
    
    # 调用评分
    result = calc_dual_layer_score_v6(df, ts_code)
    
    print("【完整评分结果】")
    print("-" * 70)
    for key, value in result.items():
        print(f"{key:12s}: {value}")
    print()
    
    # 详细分析各因子
    C = df['close']
    V = df['vol']
    
    print("【各辅助因子详细计算】")
    print("-" * 70)
    
    trend_strength = calc_trend_strength_v2(df)
    print(f"calc_trend_strength_v2: {trend_strength:.6f}")
    
    trend_stability = calc_trend_stability2(C, 20)
    print(f"calc_trend_stability2:  {trend_stability:.6f}")
    
    volume_structure = calc_volume_structure(df)
    print(f"calc_volume_structure:   {volume_structure:.6f}")
    
    accumulation = calc_accumulation_factor(df)
    print(f"calc_accumulation_factor:{accumulation:.6f}")
    
    big_money = calc_big_money_factor(df)
    print(f"calc_big_money_factor:   {big_money:.6f}")
    
    # 计算趋势概率
    P_up_input = (trend_strength - 0.5) * 1.5 + (trend_stability - 0.5) * 1.0
    trend_prob = 1 / (1 + np.exp(-P_up_input))
    print(f"\n趋势概率中间值: {P_up_input:.6f}")
    print(f"趋势概率:           {trend_prob:.6f}")
    
    # 计算基础分
    trend_component = trend_prob
    momentum_component = result['突破强度']
    money_component = result['资金动量']
    base_score = trend_component * 0.4 + momentum_component * 0.35 + money_component * 0.25
    
    print(f"\n基础分计算:")
    print(f"  trend_component   = {trend_component:.6f}")
    print(f"  momentum_component= {momentum_component:.6f}")
    print(f"  money_component   = {money_component:.6f}")
    print(f"  base_score        = {base_score:.6f}")
    
    # 计算弹性层
    elastic_score = result['压缩度'] * 0.4 + result['量能爆发'] * 0.6
    is_chuangchuang = ts_code.startswith('300')
    is_kechuang = ts_code.startswith('688')
    
    if is_chuangchuang:
        beta_multiplier = 1.25
    elif is_kechuang:
        beta_multiplier = 1.30
    else:
        beta_multiplier = 1.0
    
    elastic_layer = beta_multiplier * (0.7 + 0.3 * elastic_score)
    print(f"\n弹性层计算:")
    print(f"  elastic_score    = {elastic_score:.6f}")
    print(f"  beta_multiplier  = {beta_multiplier}")
    print(f"  elastic_layer    = {elastic_layer:.6f}")
    
    # 计算风险层
    fail_prob = result['失败概率']
    risk_layer = 1 - fail_prob
    print(f"\n风险层计算:")
    print(f"  fail_prob        = {fail_prob:.6f}")
    print(f"  risk_layer       = {risk_layer:.6f}")
    
    # 最终评分
    final_rank_score = base_score * elastic_layer * risk_layer * 100
    print(f"\n最终评分计算:")
    print(f"  final_rank_score = {base_score:.6f} * {elastic_layer:.6f} * {risk_layer:.6f} * 100")
    print(f"  final_rank_score = {final_rank_score:.2f}")
    
    print(f"\n{'='*70}\n")


if __name__ == '__main__':
    debug_score_detail('688585.SH', '上纬新材')
