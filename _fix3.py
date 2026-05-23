# -*- coding: utf-8 -*-
import re

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# ============================================================
# FIX 1: 撤销错误的硬过滤（L424行）
# 原来的：if price <= high_21 * 0.98: continue
# 改为：注释说明（这个过滤和L408加分逻辑重复，且会误杀回档买点）
# ============================================================
old1 = '''            if score < MIN_TECH_SCORE: continue

            # 过滤：未突破过去21日高点的标的，不入选（排除弱势股）
            if price <= high_21 * 0.98: continue

            sector_count'''
new1 = '''            if score < MIN_TECH_SCORE: continue

            sector_count'''

if old1 in content:
    content = content.replace(old1, new1)
    changes += 1
    print('[FIX 1] 撤销错误的 price <= high_21*0.98 硬过滤')
else:
    print('[FIX 1] 未找到对应代码段，跳过')

# ============================================================
# FIX 2: 把 L408 的 "+15分突破新高" 改为真正的过滤条件
# 原来是加分逻辑（有漏洞：回档买点可能在21日高点以下但仍是好买点）
# 改为：如果未突破21日高点则 skip（不是减分，是直接过滤）
# ============================================================
# 先找 L407-408 的位置
old2 = '''            high_21 = h.iloc[-21:-1].max()
            if price > high_21 * 0.98: score += 15'''
new2 = '''            high_21 = h.iloc[-21:-1].max()
            # 过滤：价格必须已突破21日高点（排除弱势股/下降趋势中的假突破）
            if price <= high_21 * 0.98: continue'''

if old2 in content:
    content = content.replace(old2, new2)
    changes += 1
    print('[FIX 2] 将"突破21日高点+15分"改为"未突破则直接跳过"')
else:
    print('[FIX 2] 未找到 L407-408 对应代码段，跳过')

# ============================================================
# FIX 3: 回档类信号特殊豁免（回档买点可能在21日高点以下）
# 在过滤后加回档MA5/MA10的特殊处理
# ============================================================
old3 = '''            high_21 = h.iloc[-21:-1].max()
            # 过滤：价格必须已突破21日高点（排除弱势股/下降趋势中的假突破）
            if price <= high_21 * 0.98: continue'''
new3 = '''            high_21 = h.iloc[-21:-1].max()

            # V4: 先判定回档类型
            entry_type, entry_bonus, entry_suggestion = detect_entry_type(
                price, ma5, ma10, ma20, ma60, high_21)
            score += entry_bonus

            # 回档买点特殊豁免：即使未突破21日高点，回档MA5/MA10仍是有效买点
            # 但纯突破类(far_away/breakout)必须已突破21日高点
            if entry_type in ('far_away', 'breakout') and price <= high_21 * 0.98:
                continue

            if score < MIN_TECH_SCORE: continue'''

if old3 in content:
    content = content.replace(old3, new3)
    changes += 1
    print('[FIX 3] 回档类信号豁免逻辑：far_away/breakout未突破21日高点才过滤，回档MA5/MA10豁免')
else:
    print('[FIX 3] 未找到对应代码段（可能已被FIX 2修改），跳过')

# ============================================================
# FIX 4: 移除原来L416-419的回档判断（因为已在FIX3中提前判断）
# ============================================================
old4 = '''            # V4: 回档类型判断并加分
            entry_type, entry_bonus, entry_suggestion = detect_entry_type(
                price, ma5, ma10, ma20, ma60, high_21)
            score += entry_bonus

            if score < MIN_TECH_SCORE: continue

            sector_count'''
new4 = '''            if score < MIN_TECH_SCORE: continue

            sector_count'''

if old4 in content:
    content = content.replace(old4, new4)
    changes += 1
    print('[FIX 4] 移除重复的回档判断代码')
else:
    print('[FIX 4] 未找到重复代码段，跳过')

with open(r'C:\Users\kongx\mystock\zhongjun_v4.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'\n共应用 {changes} 个修复')

import py_compile
py_compile.compile(r'C:\Users\kongx\mystock\zhongjun_v4.py', doraise=True)
print('语法检查 OK')