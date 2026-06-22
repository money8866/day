#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""详细调试300607.SZ的突破信号计算"""

import sys
sys.path.append('d:/mystock/solo')

from short_term_analyzer import pro, TRADE_DATE, CACHE_DIR
import os
import pandas as pd

ts_code = '300607.SZ'
_cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")

print(f'股票代码: {ts_code}')
print(f'当前交易日: {TRADE_DATE}')
print(f'缓存文件: {_cache_file}')
print()

if os.path.exists(_cache_file):
    df = pd.read_csv(_cache_file)
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date')
    latest = df.iloc[-1]
    
    # 提取所有因子
    close = float(latest.get('close', 0) or 0)
    boll_upper = float(latest.get('boll_upper_bfq', 0) or 0)
    ma5 = float(latest.get('ma_bfq_5', 0) or 0)
    ma10 = float(latest.get('ma_bfq_10', 0) or 0)
    ma20 = float(latest.get('ma_bfq_20', 0) or 0)
    ma60 = float(latest.get('ma_bfq_60', 0) or 0)
    macd = float(latest.get('macd_bfq', 0) or 0)
    dif = float(latest.get('macd_dif_bfq', 0) or 0)
    dea = float(latest.get('macd_dea_bfq', 0) or 0)
    kdj_j = float(latest.get('kdj_bfq', 50) or 50)
    rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
    atr = float(latest.get('atr_bfq', 0) or 0)
    
    print("=== 因子数据 ===")
    print(f"close: {close:.2f}")
    print(f"boll_upper: {boll_upper:.2f}")
    print(f"ma5: {ma5:.2f}")
    print(f"ma10: {ma10:.2f}")
    print(f"ma20: {ma20:.2f}")
    print(f"ma60: {ma60:.2f}")
    print(f"macd: {macd:.4f}")
    print(f"dif: {dif:.4f}")
    print(f"dea: {dea:.4f}")
    print(f"kdj_j: {kdj_j:.2f}")
    print(f"rsi_6: {rsi_6:.2f}")
    print(f"atr: {atr:.4f}")
    print()
    
    print("=== 突破评分计算 ===")
    total_score = 0
    
    # 1. 价格突破 (30分): close > boll_upper
    if close > boll_upper and boll_upper > 0:
        print(f"✓ 价格突破: close({close:.2f}) > boll_upper({boll_upper:.2f}) → +30分")
        total_score += 30
    else:
        print(f"✗ 价格突破: close({close:.2f}) <= boll_upper({boll_upper:.2f}) → +0分")
    
    # 2. 趋势均线 (25分): ma5 > ma10 且 ma10 > ma20 且 close > ma5
    if ma5 > ma10 and ma10 > ma20 and close > ma5 and ma5 > 0:
        print(f"✓ 趋势均线: ma5({ma5:.2f})>ma10({ma10:.2f})>ma20({ma20:.2f}), close>ma5 → +25分")
        total_score += 25
    else:
        print(f"✗ 趋势均线: ma5({ma5:.2f})>ma10({ma10:.2f})>ma20({ma20:.2f})? {ma5>ma10 and ma10>ma20} | close>ma5? {close>ma5} → +0分")
    
    # 3. 动能共振 (20分): macd > 0 且 dif > dea 且 kdj_j > 80
    if macd > 0 and dif > dea and kdj_j > 80:
        print(f"✓ 动能共振: macd({macd:.4f})>0, dif({dif:.4f})>dea({dea:.4f}), kdj_j({kdj_j:.2f})>80 → +20分")
        total_score += 20
    else:
        print(f"✗ 动能共振: macd>0? {macd>0} | dif>dea? {dif>dea} | kdj_j>80? {kdj_j>80} → +0分")
    
    # 4. 空间与安全 (15分): rsi_6 > 65 且 rsi_6 < 85
    if 65 < rsi_6 < 85:
        print(f"✓ 空间与安全: 65 < rsi_6({rsi_6:.2f}) < 85 → +15分")
        total_score += 15
    else:
        print(f"✗ 空间与安全: rsi_6({rsi_6:.2f}) 不在65-85之间 → +0分")
    
    # 5. 波动率辅助 (10分): atr > 0 且 close > ma60
    if atr > 0 and close > ma60 and ma60 > 0:
        print(f"✓ 波动率辅助: atr({atr:.4f})>0, close({close:.2f})>ma60({ma60:.2f}) → +10分")
        total_score += 10
    else:
        print(f"✓ 波动率辅助: atr({atr:.4f})>0, close({close:.2f})>ma60({ma60:.2f}) → +10分")
    
    print()
    print(f"=== 总突破分数: {total_score}分 ===")
else:
    print("缓存文件不存在")
