# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# 定位主循环中的评分代码段
old_block = '''            high_21 = h.iloc[-21:-1].max()
            if price > high_21 * 0.98: sc += 15
            pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: sc += 10
            pct5 = (price / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: sc += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if rl > 0 and (rh - rl) / rl * 100 < 25: sc += 5
            if sc < MIN_TECH_SCORE: continue
            entry_type, entry_bonus = detect_entry_type(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21)
            sc += entry_bonus'''

new_block = '''            high_21 = h.iloc[-21:-1].max()
            pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: sc += 10
            pct5 = (price / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: sc += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if rl > 0 and (rh - rl) / rl * 100 < 25: sc += 5

            # V4: 先判定回档类型（根据price与high_21的关系）
            entry_type, entry_bonus = detect_entry_type(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21)
            sc += entry_bonus

            # 过滤：far_away/breakout 类型必须已突破21日高点（回档类豁免）
            if entry_type in ('far_away', 'breakout') and price <= high_21 * 0.98:
                continue

            if sc < MIN_TECH_SCORE: continue'''

if old_block in content:
    content = content.replace(old_block, new_block)
    changes += 1
    print('[FIX] 回测脚本主循环修复完成')
else:
    print('[ERROR] 未找到目标代码段，请检查')