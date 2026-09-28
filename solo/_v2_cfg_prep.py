# -*- coding: utf-8 -*-
"""V2.0 映射打通前置勘查：快照基线 + 导出 AI5 成员 + 现有 core_stocks"""
import json
import os
import sys

BASE = r'd:\mystock\solo'
BD = os.path.join(BASE, 'report_daily')
CFG = os.path.join(BASE, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json')
AI5 = ('CPO', '液冷', 'AI服务器', '算力运营', 'AI应用')

LOG = open(os.path.join(BASE, '_v2_cfg_prep.log'), 'w', encoding='utf-8')
sys.stdout = LOG

mp = json.load(open(os.path.join(BD, 'theme_stock_map_latest_v2.json'), encoding='utf-8'))
baseline = {t: [s['code'] for s in lst] for t, lst in mp['themes'].items()}
json.dump(baseline, open(os.path.join(BASE, '_v2_map_baseline.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('=== 基线快照 ===')
print('主题数 %d | 映射数 %d | 个股数 %d' % (len(baseline), sum(len(v) for v in baseline.values()),
                                        mp.get('n_stocks', 0)))

cfg = json.load(open(CFG, encoding='utf-8'))
cn2key = {}
for k, v in cfg.items():
    if k.startswith('_'):
        continue
    cn2key[v.get('name_cn') or k] = k

print('\n=== AI5 主题现状（映射 / 配置）===')
for t in AI5:
    k = cn2key.get(t)
    c = cfg.get(k, {})
    print('%-8s key=%-22s map_n=%-4d core_stocks=%-3d leaders=%-3d segment_gate=%s'
          % (t, k, len(baseline.get(t, [])), len(c.get('core_stocks') or []),
             len(c.get('leaders') or []), bool(c.get('segment_gate'))))
    print('         core_stocks=%s' % (c.get('core_stocks') or []))
    print('         leaders=%s' % (c.get('leaders') or []))

mv = json.load(open(os.path.join(BASE, 'theme_membership_v2.json'), encoding='utf-8'))
print('\n=== membership_v2 五主题四级成员 ===')
for t in AI5:
    v = mv['themes'].get(t) or {}
    for tier in ('cores', 'extensions', 'relateds'):
        print('%s %s: %s' % (t, tier, v.get(tier)))
    print('  -> sflag=%s SR=%s MQ=%s' % (v.get('sample_flag'), v.get('sample_reliability'),
                                         v.get('membership_quality')))

print('\n=== 全主题映射规模（基线，降序）===')
for t, v in sorted(baseline.items(), key=lambda kv: -len(kv[1])):
    flag = ' <== AI5' if t in AI5 else ''
    print('%-14s %d%s' % (t, len(v), flag))

print('\nDONE')
LOG.close()
