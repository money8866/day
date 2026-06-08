
# -*- coding: utf-8 -*-
"""调试三只股票失败概率详细分析（优化版）
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import calc_dual_layer_score_v6, get_hist_data


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def analyze_fail_prob_detail(df, ts_code, name):
    """详细分析失败概率计算过程"""
    print("\n" + "="*60)
    print("股票: {} ({})".format(ts_code, name))
    print("="*60)
    
    C = df['close']
    H = df['high']
    L = df['low']
    VOL = df['vol']
    
    # === 优化后的失败概率计算 ===
    MA60 = C.rolling(60).mean().iloc[-1]
    # 1. 高价风险因子
    price_ma60_ratio = C.iloc[-1] / MA60
    high_risk_zone = np.clip((price_ma60_ratio - 1.1) / 0.4, 0.0, 1.0)
    print("1. 高价风险因子: {:.4f} (价格/MA60: {:.4f})".format(high_risk_zone, price_ma60_ratio))
    
    # 2. 阻力压力因子
    HHV20 = H.tail(20).max()
    LLV20 = L.tail(20).min()
    amp20 = (HHV20 - LLV20) / (LLV20 + 1e-6)
    resistance_pressure = np.clip((amp20 - 0.15) / 0.25, 0.0, 1.0)
    print("2. 阻力压力因子: {:.4f} (20日振幅: {:.4f})".format(resistance_pressure, amp20))
    
    # 3. 派发风险因子
    vol_ratio = VOL.iloc[-1] / (VOL.tail(10).mean() + 1e-6)
    price_change = (C.iloc[-1] - C.iloc[-2]) / C.iloc[-2]
    distribution_risk = 0.0
    if price_change < 0:
        distribution_risk = np.clip((vol_ratio - 1.0) * abs(price_change) * 10, 0.0, 1.0)
    print("3. 派发风险因子: {:.4f} (量比: {:.4f}, 涨跌幅: {:.4f}%)".format(distribution_risk, vol_ratio, price_change*100))
    
    # 4. 趋势稳定性下降风险
    ma20 = C.rolling(20).mean().iloc[-1]
    ma5 = C.rolling(5).mean().iloc[-1]
    trend_decline_risk = 0.0
    if ma5 < ma20:
        trend_decline_risk = np.clip((ma20 - ma5) / ma20 * 20, 0.0, 1.0)
    print("4. 趋势下降风险: {:.4f} (MA5: {:.2f}, MA20: {:.2f})".format(trend_decline_risk, ma5, ma20))
    
    # 计算fail_prob
    fail_prob = sigmoid(
        (resistance_pressure - 0.5) * 1.5 +
        (high_risk_zone - 0.5) * 1.2 +
        (distribution_risk - 0.5) * 1.5 +
        (trend_decline_risk - 0.5) * 0.8
    )
    print("\n5. 计算过程:")
    print("   (resistance_pressure-0.5)*1.5 = {:.4f}".format((resistance_pressure-0.5)*1.5))
    print("   (high_risk_zone-0.5)*1.2   = {:.4f}".format((high_risk_zone-0.5)*1.2))
    print("   (distribution_risk-0.5)*1.5   = {:.4f}".format((distribution_risk-0.5)*1.5))
    print("   (trend_decline_risk-0.5)*0.8  = {:.4f}".format((trend_decline_risk-0.5)*0.8))
    print("6. fail_prob = {:.4f}".format(fail_prob))
    
    # V6完整结果
    v6_result = calc_dual_layer_score_v6(df, ts_code)
    print("\nV6返回失败概率: {}".format(v6_result.get('失败概率', 0)))
    return v6_result


def main():
    stock_list = [
        ('002747.SZ', '埃斯顿'),
        ('300814.SZ', '中富电路'),
        ('301205.SZ', '联特科技')
    ]
    
    for ts_code, name in stock_list:
        try:
            df = get_hist_data(ts_code)
            if df is not None and len(df) >= 60:
                analyze_fail_prob_detail(df, ts_code, name)
            else:
                print("\n股票 {} 数据不足".format(ts_code))
        except Exception as e:
            print("\n分析 {} 出错: {}".format(ts_code, e))
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

