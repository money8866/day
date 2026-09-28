# -*- coding: utf-8 -*-
"""传媒主题：候选补词 → 逐只验证可救回的 WEAK 成员（不改任何配置）"""
import os
import sys
from collections import Counter, defaultdict

BASE = r'd:\mystock\solo'
sys.path.insert(0, BASE)
os.chdir(BASE)

import theme_heat_v22 as H

LOG = open(os.path.join(BASE, '_media_words.log'), 'w', encoding='utf-8')
sys.stdout = LOG

THEME = '传媒'
td = H.resolve_trade_date(None)
theme_members, _, _ = H.load_theme_members(td)
config = H.load_theme_config_v3()
mainbiz = H.load_mainbiz()
prev = H.load_prev_member_counts(td)
mv2, _ = H.load_membership_v2()
mapping = H.apply_membership_v2(
    H.build_mapping_quality(theme_members, config, mainbiz, prev), mv2, config)

lay = mapping[THEME]['layers']
recs = theme_members[THEME]
weak = sorted([c for c, v in lay.items() if v['layer'] == 'WEAK'])

CAND = {
    '营销/广告': ['营销', '媒介代理', '品牌管理', '促销品', '流量经营', '公关传播', '整合营销'],
    '广电/IPTV': ['IPTV', '有线电视', '广电网络', '集成播控', '电视网络'],
    '出版/阅读': ['教材', '教辅', '读物', '数字阅读', '版权'],
    '数字文创':  ['数字创意', '数字展示', '数字文化'],
}

print('WEAK 共 %d 只\n' % len(weak))
print('%-11s %-8s %-34s %s' % ('code', 'name', 'why', '首个可命中候选词'))
hit_map = defaultdict(list)      # 词 -> [code]
unrescued = []
for c in weak:
    txt = mainbiz.get(c, '') or ''
    hit = ''
    for grp, words in CAND.items():
        for w in words:
            if w in txt:
                hit = '%s/%s' % (grp, w)
                break
        if hit:
            break
    if hit:
        hit_map[hit.split('/')[1]].append(c)
    else:
        unrescued.append(c)
    print('%-11s %-8s %-34s %s' % (c, recs[c].get('name', ''), lay[c]['why'], hit or '—— 无'))

print('\n--- 逐词救回数（按可救回数降序） ---')
for w, cs in sorted(hit_map.items(), key=lambda x: -len(x[1])):
    print('  %-8s %2d 只  %s' % (w, len(cs), '、'.join(recs[c].get('name', '') for c in cs)))

rescued = sum(len(v) for v in hit_map.values())
print('\n可救回 %d / %d 只' % (rescued, len(weak)))
print('无法救回 %d 只：%s' % (len(unrescued),
      '、'.join('%s(%s)' % (recs[c].get('name', ''), c) for c in unrescued)))

print('\n--- 若把这些词加到 keywords（NORMAL 层）后的预估 ---')
mq = mapping[THEME]
new_valid = mq['valid'] + rescued
print('  valid: %d -> %d / total %d' % (mq['valid'], new_valid, mq['total']))
print('  mapping_quality: %.4f -> %.4f' % (mq['quality'], new_valid / mq['total']))
print('  status: %s -> %s' % (mq['status'],
      'OK' if new_valid / mq['total'] >= H.MAP_OK else ('WARN' if new_valid / mq['total'] >= H.MAP_WARN else 'BAD')))

print('\n--- 当前 keyword 字段内容（供增补定位） ---')
for k in ('keywords', 'core_keywords', 'industry_keywords', 'product_keywords',
          'concept_keywords', 'industry_chains'):
    print('  %-18s %s' % (k, config[THEME].get(k)))

LOG.close()
