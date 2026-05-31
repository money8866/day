#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直接修改 main.py，集成历史跟踪（无emoji输出）"""
import os
import sys

# 读取原文件
file_path = r'C:\Users\kongx\mystock\dragon\main.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 修改1: 导入语句 (第15行)
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # 修改导入
    if 'from sector_strength import analyze_sector_strength, get_main_theme_stocks' in line:
        new_lines.append('from sector_strength import (\n')
        new_lines.append('    analyze_sector_strength, get_main_theme_stocks,\n')
        new_lines.append('    track_history_themes, find_recurring_themes\n')
        new_lines.append(')\n')
        i += 1
        continue
    
    # 修改2: 在板块强度部分之后添加历史跟踪
    if 'print("  (无数据)")' in line and i < 60:
        new_lines.append(line)
        # 添加历史跟踪代码
        extra = [
            '\n',
            '    # 历史主线跟踪\n',
            '    if themes:\n',
            '        top5_names = [t["name"] for t in themes]\n',
            '        history = track_history_themes(trade_date, top5_names)\n',
            '        recurring = find_recurring_themes(history, min_count=3)\n',
            '    else:\n',
            '        history = []\n',
            '        recurring = []\n',
            '\n',
            '    # 显示反复活跃板块\n',
            '    if recurring:\n',
            '        print("\\n  [反复活跃] 反复活跃板块 (>=3日):")\n',
            '        for item in recurring[:3]:\n',
            '            print("    %s (%d日)" % (item["name"], item["count"]))\n',
        ]
        new_lines.extend(extra)
        i += 1
        continue
    
    # 修改3: 龙头评分部分，传递recurring_themes参数
    if 'scored = score_dragon_candidates(candidates)' in line:
        new_lines.append('        # 传递反复活跃板块信息\n')
        new_lines.append('        recurring_set = set(item["name"] for item in recurring) if recurring else set()\n')
        new_lines.append('        scored = score_dragon_candidates(candidates, recurring_themes=recurring_set)\n')
        i += 1
        continue
    
    new_lines.append(line)
    i += 1

# 写回文件 (使用 write_file.py 确保编码正确)
output_path = r'C:\Users\kongx\mystock\dragon\main_new.py'
with open(output_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('SUCCESS: main_new.py 已生成')
print('行数: %d' % len(new_lines))
print(' ')
print('请手动检查 main_new.py，然后替换原文件:')
print('  copy /Y "C:\\Users\\kongx\\mystock\\dragon\\main_new.py" "C:\\Users\\kongx\\mystock\\dragon\\main.py"')
