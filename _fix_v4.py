# -*- coding: utf-8 -*-
import sys, re

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Remove duplicate "if score < MIN_TECH_SCORE: continue" followed by empty line
# Replace the section where it appears twice in a row
content = re.sub(
    r'(if score < MIN_TECH_SCORE: continue)\n\n\1',
    r'\1',
    content
)

# Fix 2: Replace the escaped unicode comment with proper Chinese
content = content.replace(
    r'            # \\u8fc7\\u6ee4\uff1a\\u672a\\u7a81\\u7834\\u8fc7\\u53bb21\u65e5\u9ad8\u70b9\u7684\u6807\u7684\uff0c\u4e0d\u5f25\u5165',
    r'            # 过滤：未突破过去21日高点的标的，不入选（排除弱势股）'
)
# Also check without double backslashes
content = re.sub(
    r'# \\\u8fc7\\\u6ee4.*?\u4e0d\u5f25\u5165',
    r'# 过滤：未突破过去21日高点的标的，不入选（排除弱势股）',
    content
)

# Fix 3: Add sector_count before sector_names if not present
# Find the sector_names line and insert sector_count before it
sector_names_pattern = r'(\n            sector_names = \[hs\[.name.\] for hs in hot_sectors if tc in hs\[.stocks.\]\])'
if 'sector_count = sum(1 for hs in hot_sectors' not in content:
    content = re.sub(
        sector_names_pattern,
        r'\n            sector_count = sum(1 for hs in hot_sectors if tc in hs["stocks"])\1',
        content
    )
    print('Inserted sector_count')
else:
    print('sector_count already present')

# Fix 4: The filter should be right after MIN_TECH_SCORE check
# Ensure the "if price <= high_21 * 0.98: continue" is right after the MIN_TECH_SCORE check
# Find and verify the filter placement
if 'if price <= high_21 * 0.98: continue' not in content:
    content = content.replace(
        '            if score < MIN_TECH_SCORE: continue\n\n            # 过滤',
        '            if score < MIN_TECH_SCORE: continue\n            # 过滤'
    )
    print('Fixed filter placement')

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('File updated')

# Verify syntax
import py_compile
py_compile.compile(r'C:\Users\kongx\mystock\zhongjun_v4.py', doraise=True)
print('Syntax OK')