#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试300607.SZ在两个文件中的计算结果"""

import sys
sys.path.append('d:/mystock/solo')

# 从short_term_analyzer.py测试
print("=== 从short_term_analyzer.py测试 ===")
from short_term_analyzer import detect_breakout, detect_wave2_reversal, pro, TRADE_DATE
breakout1 = detect_breakout('300607.SZ', pro)
wave2_1 = detect_wave2_reversal('300607.SZ', pro)
print(f"300607.SZ 突破分数: {breakout1['breakout_score']}")
print(f"300607.SZ 二波分数: {wave2_1['wave2_score']}")
print(f"突破信号: {breakout1['signal']}")
print(f"二波信号: {wave2_1['signal']}")

# 从tushare_quant.py测试
print("\n=== 从tushare_quant.py测试 ===")
from tushare_quant import detect_breakout as detect_breakout2, detect_wave2_reversal as detect_wave2_reversal2, pro as pro2
breakout2 = detect_breakout2('300607.SZ', pro2)
wave2_2 = detect_wave2_reversal2('300607.SZ', pro2)
print(f"300607.SZ 突破分数: {breakout2['breakout_score']}")
print(f"300607.SZ 二波分数: {wave2_2['wave2_score']}")
print(f"突破信号: {breakout2['signal']}")
print(f"二波信号: {wave2_2['signal']}")

# 比较结果
print("\n=== 比较结果 ===")
print(f"突破分数一致: {breakout1['breakout_score'] == breakout2['breakout_score']}")
print(f"二波分数一致: {wave2_1['wave2_score'] == wave2_2['wave2_score']}")
