# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'r', encoding='utf-8') as f:
    content = f.read()

# FIX A: 把两处 MIN_TECH_SCORE 检查合并成一处的合理版本
# 问题：L419是第一次检查（在pos120/pct5加分前），L427是第二次
# 解决：删除L419的检查，只在所有加分后检查一次（L427位置）
old_a = '''            # 回档买点特殊豁免：即使未突破21日高点，回档MA5/MA10仍是有效买点
            # 但纯突破类(far_away/breakout)必须已突破21日高点
            if entry_type in ('far_away', 'breakout') and price <= high_21 * 0.98:
                continue

            if score < MIN_TECH_SCORE: continue
            pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: score += 10
            pct5 = (price / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: score += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if (rh-rl)/rl*100 < 25: score += 5

            if score < MIN_TECH_SCORE: continue'''

new_a = '''            # 过滤：far_away/breakout类型必须已突破21日高点，回档类豁免
            if entry_type in ('far_away', 'breakout') and price <= high_21 * 0.98:
                continue

            pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: score += 10
            pct5 = (price / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: score += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if (rh-rl)/rl*100 < 25: score += 5

            if score < MIN_TECH_SCORE: continue'''

if old_a in content:
    content = content.replace(old_a, new_a)
    print('[FIX A] 两处检查合并为一处，过滤位置修正')
else:
    print('[FIX A] 未找到目标代码段，尝试备选方案')
    # 备选：找到所有 score < MIN 检查，删除第一个
    import re
    matches = list(re.finditer(r'if score < MIN_TECH_SCORE: continue', content))
    print(f'  找到 {len(matches)} 处 score < MIN_TECH_SCORE 检查')
    if len(matches) >= 2:
        # 删除第一个（最早的）
        content = content[:matches[0].start()] + content[matches[0].end():]
        print('  已删除第一处 MIN_TECH_SCORE 检查')

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'w', encoding='utf-8') as f:
    f.write(content)

import py_compile
py_compile.compile(r'C:\Users\kongx\mystock\zhongjun_v4.py', doraise=True)
print('语法 OK')