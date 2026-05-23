# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip_next = False
for i, line in enumerate(lines):
    # Skip the duplicate MIN_TECH_SCORE check
    if 'if score < MIN_TECH_SCORE: continue' in line:
        # Check if next non-empty line is also this check
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        if j < len(lines) and 'if score < MIN_TECH_SCORE: continue' in lines[j]:
            skip_next = True
            new_lines.append(line)  # keep first
            continue
    
    # Replace unicode escape comment
    if '\\u8fc7\\u6ee4' in line or '\\u672a\\u7a81\\u7834' in line:
        line = '            # 过滤：未突破过去21日高点的标的，不入选（排除弱势股）\n'
    
    if skip_next:
        skip_next = False
        continue
    
    new_lines.append(line)

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

import py_compile
py_compile.compile(r'C:\Users\kongx\mystock\zhongjun_v4.py', doraise=True)
print('OK')