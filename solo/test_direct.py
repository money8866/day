#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直接测试detect_breakout函数"""

import os
import sys
sys.path.append('d:/mystock/solo')

# 直接从short_term_analyzer导入
from short_term_analyzer import detect_breakout, pro, TRADE_DATE, CACHE_DIR

ts_code = '300607.SZ'
print(f'股票代码: {ts_code}')
print(f'当前交易日: {TRADE_DATE}')

# 先检查缓存文件
_cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")
print(f'缓存文件存在: {os.path.exists(_cache_file)}')

# 调用detect_breakout
result = detect_breakout(ts_code, pro)
print(f'\n突破分数: {result["breakout_score"]}')
print(f'突破信号: {result["signal"]}')
print(f'各维度得分: {result["breakdown"]}')
