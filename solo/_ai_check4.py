# -*- coding: utf-8 -*-
"""核验：四新主题成员互斥性 + 原 AI算力 81 只当前落位"""
import json

P = r'd:\mystock\solo\_prev_map_ai.json'          # 改造前快照（含 AI算力 81 只）
NEW = r'd:\mystock\solo\report_daily\theme_stock_map_latest_v2.json'

old = json.load(open(P, encoding='utf-8'))
new = json.load(open(NEW, encoding='utf-8'))
FOUR = ['CPO', '液冷', 'AI服务器', 'AI应用']

code2name = {}
for t, ms in old['themes'].items():
    for m in ms:
        code2name[m['code']] = m['name']
old_ai = {m['code']: m['name'] for m in old['themes']['AI算力']}
print('OLD_AI_COMPUTING_TOTAL = %d' % len(old_ai))

# 四新主题互斥性
sets = {t: {m['code'] for m in new['themes'].get(t, [])} for t in FOUR}
for i in range(len(FOUR)):
    for j in range(i + 1, len(FOUR)):
        ov = sets[FOUR[i]] & sets[FOUR[j]]
        if ov:
            print('  [重叠] %s ∩ %s = %s' % (FOUR[i], FOUR[j], [old_ai.get(c, code2name.get(c, c)) for c in ov]))
print('四新主题 pairwise 重叠检查完成')

# 每只 81 成员当前所属一级主题（仅列出四新主题与旧成员相关项）
print('\n--- 81 成员当前落位（仅四新主题命中）---')
hit = {}
for t in FOUR:
    for c in sets[t]:
        hit.setdefault(c, []).append(t)
for code, name in old_ai.items():
    print('%s %-8s -> %s' % (code, name, hit.get(code, [])))

# 四新主题中不属于原 81 只的（池外新增）
print('\n--- 池外新增（不属于原 81）---')
for t in FOUR:
    extra = [c for c in sets[t] if c not in old_ai]
    print('%s: %s' % (t, [(c, old_ai.get(c) or code2name.get(c, c)) for c in extra]))
