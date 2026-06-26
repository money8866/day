# -*- coding: utf-8 -*-
"""临时脚本：更新 bull_scorer_v2.py 头部为v3.0"""
with open('D:\\mystock\\solo\\multi_factor_picker\\bull_scorer_v2.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the v2.2 header (in docstring)
old_header = '"""\nBullScore v2.2 — 中长线牛股选股系统\n'
new_header = '"""\nBullScore v3.0 — 中长线牛股选股系统（超预期持续成长版）\n'

content = content.replace(old_header, new_header, 1)

# Replace other v2.2 references
content = content.replace('BullScore v2.2 完整评分计算', 'BullScore v3.0 完整评分计算（超预期持续成长版）')
content = content.replace('BullScore v2.2', 'BullScore v3.0')
content = content.replace('BullScore_v2.2', 'BullScore_v3.0')
content = content.replace('（增强版v2.1）', '（增强版v3.0）')
content = content.replace('Alpha因子评分器:', 'Alpha因子评分器（已弃用v3.0）:')

with open('D:\\mystock\\solo\\multi_factor_picker\\bull_scorer_v2.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('✅ 头部已更新为v3.0')
