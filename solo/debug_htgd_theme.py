#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查亨通光电的行业和概念标签，以及为什么匹配不到主题"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro
import json, os

ts_code = '600487.SH'
stock_name = '亨通光电'

# 1. 获取stock_basic信息
try:
    df = pro.stock_basic(ts_code=ts_code, fields='ts_code, name, industry, area')
    if df is not None and not df.empty:
        row = df.iloc[0]
        print(f"stock_basic 行业: {row.get('industry', 'N/A')}")
    else:
        print("stock_basic 无数据")
except Exception as e:
    print(f"stock_basic 错误: {e}")

# 2. 获取概念标签（从东财）
try:
    from theme_trend_sentiment_score import get_dc_members
    dc_df = get_dc_members()
    if dc_df is not None:
        # 查找亨通光电
        htgd = dc_df[dc_df['ts_code'] == ts_code]
        if not htgd.empty:
            row = htgd.iloc[0]
            print(f"东财行业: {row.get('industry', 'N/A')}")
            print(f"东财概念: {row.get('concept', 'N/A')}")
        else:
            print("东财数据中未找到亨通光电")
except Exception as e:
    print(f"东财数据错误: {e}")

# 3. 加载theme.json，检查光通信主题的配置
theme_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'theme.json')
with open(theme_path, 'r', encoding='utf-8') as f:
    themes = json.load(f)['HOT_THEMES']

# 检查"光通信"主题
gc = themes.get('光通信', {})
print(f"\n光通信主题 industry: {gc.get('industry', [])}")
print(f"光通信主题 concept: {gc.get('concept', [])}")
print(f"光通信主题 keywords: {gc.get('keywords', [])}")
print(f"光通信主题 core_companies: {gc.get('core_companies', [])}")
print(f"光通信主题 leader_companies: {gc.get('leader_companies', [])}")

# 检查其他可能相关的主题
print("\n=== 检查所有包含'通信'关键词的主题 ===")
for tname, tcfg in themes.items():
    inds = tcfg.get('industry', [])
    for ind in inds:
        if '通信' in ind:
            print(f"{tname}: industry包含 {ind}")
    kws = tcfg.get('keywords', [])
    for kw in kws:
        if '通信' in kw or '光纤' in kw or '光缆' in kw:
            print(f"{tname}: keywords包含 {kw}")
            break
