# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 修复 verdict 评级逻辑：far_away 类型给出明确警示（不推荐买入）
old = '''    # V4: 根据回档类型调整评级
    if fin_score >= 24 and sector_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "⭐⭐⭐ 强烈买入(回档买)"
    elif fin_score >= 22 and repeat_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "⭐⭐ 买入(回档买)"
    elif fin_score >= 20 and repeat_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "✅ 谨慎买入(回档买)"
    elif fin_score >= 20 and repeat_count >= 2 and entry_type == 'breakout':
        rating = "⏳ 突破状态，等回档再买"
    elif fin_score >= 20 and repeat_count >= 2:
        rating = "✅ 谨慎买入"
    elif fin_score >= 20:
        rating = "👀 观望(等二次入选)"
    else:
        rating = "❌ 淘汰"'''

new = '''    # V4: 根据回档类型调整评级（far_away 最差，breakout 次之，回档最好）
    if entry_type == 'far_away':
        rating = "⚠️ 远离均线，不推荐买入"
    elif fin_score >= 24 and sector_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "⭐⭐⭐ 强烈买入(回档买)"
    elif fin_score >= 22 and repeat_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "⭐⭐ 买入(回档买)"
    elif fin_score >= 20 and repeat_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "✅ 谨慎买入(回档买)"
    elif fin_score >= 20 and repeat_count >= 2 and entry_type == 'breakout':
        rating = "⏳ 突破状态，等回档再买"
    elif fin_score >= 20 and repeat_count >= 2:
        rating = "✅ 谨慎买入"
    elif fin_score >= 20:
        rating = "👀 观望(等二次入选)"
    else:
        rating = "❌ 淘汰"'''

if old in content:
    content = content.replace(old, new)
    print('[FIX] verdict 评级逻辑已更新')
else:
    print('[SKIP] verdict 代码未变化')

# 修复 verdict 仓位建议：far_away 不建仓
old2 = '''    if '强烈买入(回档买)' in rating:
        pos = "30%"
    elif '买入(回档买)' in rating:
        pos = "25%"
    elif '谨慎买入(回档买)' in rating:
        pos = "15%"
    elif '等回档再买' in rating:
        pos = "0%（等回档）"
    elif '谨慎买入' in rating:
        pos = "10%"
    else:
        pos = "0%"'''

new2 = '''    if '远离均线' in rating:
        pos = "0%（不推荐）"
    elif '强烈买入(回档买)' in rating:
        pos = "30%"
    elif '买入(回档买)' in rating:
        pos = "25%"
    elif '谨慎买入(回档买)' in rating:
        pos = "15%"
    elif '等回档再买' in rating:
        pos = "0%（等回档）"
    elif '谨慎买入' in rating:
        pos = "10%"
    else:
        pos = "0%"'''

if old2 in content:
    content = content.replace(old2, new2)
    print('[FIX] verdict 仓位建议已更新')
else:
    print('[SKIP] 仓位建议代码未变化')

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'w', encoding='utf-8') as f:
    f.write(content)

import py_compile
py_compile.compile(r'C:\Users\kongx\mystock\zhongjun_v4.py', doraise=True)
print('语法 OK')