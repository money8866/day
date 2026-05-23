# -*- coding: utf-8 -*-
# 将 zhongjun_v4.py 的 "未突破21日高点过滤" 逻辑同步到回测脚本
# 过滤规则：
#   - far_away / breakout 类型：必须 price > high_21 * 0.98 才纳入
#   - pullback_ma5 / pullback_ma10 / breakout_near_ma：无论是否突破21日高点都纳入

import re

with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# 找到当前主循环中所有 entry_type 判断的位置
# 在回测脚本中，每个标的买入时记录 entry_type
# 需要添加：breakout/far_away 且 price <= high_21 * 0.98 → 不买入（wait）

# 查看主循环中的过滤逻辑
# 关键是：在 "判断买入信号" 的地方加入新过滤
print('=== 回测脚本关键检查 ===')
for keyword in ['price <= high_21', 'high_21', 'entry_type', 'breakout', 'pullback_ma5']:
    count = content.count(keyword)
    print(f'{keyword}: {count} occurrences')

# 找到主循环中 "未突破21日高点的过滤" 代码段并更新
# 回测脚本已有部分过滤，需要把 "far_away" 和 "breakout" 未突破的纳入 skip
old_section = '''        # V4: 判断是否回档买入
        entry_type, entry_bonus, entry_suggestion = detect_entry_type(
            price, ma5, ma10, ma20, ma60, high_21)
        score += entry_bonus

        # 原始加分条件的重复：price > high_21*0.98 也加了15分
        # V4：移除这个重复加分（已在 detect_entry_type 里处理）
        # --- 以下为旧逻辑（已注释）---
        # if price > high_21 * 0.98: score += 15

        if score < MIN_TECH_SCORE: continue'''

new_section = '''        # V4: 判定回档类型
        entry_type, entry_bonus, entry_suggestion = detect_entry_type(
            price, ma5, ma10, ma20, ma60, high_21)
        score += entry_bonus

        # 过滤：far_away/breakout 类型必须已突破21日高点，回档类豁免
        if entry_type in ('far_away', 'breakout') and price <= high_21 * 0.98:
            continue

        if score < MIN_TECH_SCORE: continue'''

if old_section in content:
    content = content.replace(old_section, new_section)
    changes += 1
    print('[FIX] 更新回测脚本的回档类型过滤逻辑')
else:
    print('[SKIP] 主循环代码段未变化')

with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py', 'w', encoding='utf-8') as f:
    f.write(content)

import py_compile
py_compile.compile(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py', doraise=True)
print(f'回测脚本修复完成 ({changes} 处)，语法 OK')