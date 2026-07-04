# -*- coding: utf-8 -*-
"""AI分析主题-个股映射的合理性和精准度"""
import json
import os
import sys
import requests
from collections import defaultdict

sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import deepseek

# 加载最新映射
with open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

themes = data['themes']
stocks = data['stocks']

# 1. 抽样分析：每个主题取前5、中间5、后5只股票
sample_data = {}
for theme_name, stock_list in themes.items():
    if len(stock_list) <= 15:
        sample = stock_list
    else:
        mid = len(stock_list) // 2
        sample = stock_list[:5] + stock_list[mid-2:mid+3] + stock_list[-5:]
    sample_data[theme_name] = [{
        'code': s['code'],
        'name': s['name'],
        'industry': s.get('industry', ''),
        'concepts': s.get('concepts', [])[:5],
        'via': s['via'],
        'score': s['score']
    } for s in sample]

# 2. 跨主题最多的股票
cross_stocks = []
for code, info in stocks.items():
    if len(info['themes']) >= 4:
        cross_stocks.append({
            'code': code,
            'name': info['name'],
            'industry': info.get('industry', ''),
            'concepts': info.get('concepts', [])[:5],
            'themes': info['themes']
        })
cross_stocks.sort(key=lambda x: -len(x['themes']))
cross_stocks = cross_stocks[:20]

# 3. 构建分析prompt
prompt = f"""你是A股市场主题投资专家。请分析以下主题-个股映射数据的合理性和精准度。

## 数据概览
- 主题数: {data['n_themes']}
- 股票数: {data['n_stocks']}
- 映射关系数: {data['n_stock_refs']}
- 每只股票最多归属5个主题
- 优化规则：单主题最多300只，单股票最多5个主题，按via优先级(leader>core>industry>concept)排序

## 需要分析的维度

### 维度1: 主题内成份股精准度
对每个主题，抽样了前5（最高分）、中5、后5（最低分）只股票。
请判断：
- 高分股是否确实是该主题的核心标的
- 低分股是否合理（边缘概念 vs 误纳入）
- 是否有明显不应归入该主题的股票

### 维度2: 跨主题股票合理性
列出了归属4-5个主题的股票，请判断多主题归属是否合理。

## 抽样数据

"""

for theme_name, samples in sample_data.items():
    prompt += f"\n### {theme_name} (共{len(themes[theme_name])}只)\n"
    for s in samples:
        prompt += f"  {s['name']}({s['code']}) 行业:{s['industry']} 概念:{s['concepts']} via:{s['via']} 分:{s['score']}\n"

prompt += "\n## 跨主题最多的TOP20股票\n"
for s in cross_stocks:
    prompt += f"  {s['name']}({s['code']}) 行业:{s['industry']} 主题:{s['themes']}\n"

prompt += """
## 输出要求

请按以下格式输出分析结果：

### 1. 主题精准度评分（每主题0-10分）
列出所有主题的评分，格式：主题名: 评分 - 简要说明

### 2. 精准度问题清单
列出发现的具体问题：
- [主题名] 股票名(代码): 问题描述

### 3. 跨主题合理性分析
对TOP20跨主题股票逐一判断合理/不合理

### 4. 优化建议
给出3-5条具体优化建议

### 5. 总体评价
整体精准度评分（0-100分）和总结
"""

print(f"Prompt长度: {len(prompt)} 字符")
print(f"抽样主题数: {len(sample_data)}")
print(f"跨主题股票数: {len(cross_stocks)}")
print("正在调用AI分析...")

result = deepseek(prompt, use_flash=False)
print("\n" + "="*80)
print("AI分析结果：")
print("="*80)
print(result)

# 保存结果
with open(r'd:\mystock\solo\_ai_analysis_result.txt', 'w', encoding='utf-8') as f:
    f.write(result)
print("\n结果已保存到 _ai_analysis_result.txt")
