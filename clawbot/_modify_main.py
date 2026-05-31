#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修改 main.py，加入历史主线跟踪"""
import os
import sys

# 读取原文件
with open(r'C:\Users\kongx\mystock\dragon\main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 修改行
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # 1. 修改导入语句 (第15行)
    if 'from sector_strength import analyze_sector_strength, get_main_theme_stocks' in line:
        new_lines.append('from sector_strength import (\n')
        new_lines.append('    analyze_sector_strength, get_main_theme_stocks,\n')
        new_lines.append('    track_history_themes, find_recurring_themes\n')
        new_lines.append(')\n')
        i += 1
        continue
    
    # 2. 在板块强度部分之后添加历史跟踪 (在 print("(无数据)") 之后)
    if 'print("\\n📍 【3. 龙头评分 TOP10】")' in line:
        # 回溯插入历史跟踪代码（在【2. 主线板块】部分末尾）
        # 实际上我需要找到更好的插入点
        pass
    
    new_lines.append(line)
    i += 1

# 这个脚本太复杂，我改用完整重写方案
print("此方案太复杂，改用完整重写...")
