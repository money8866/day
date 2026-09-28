# -*- coding: utf-8 -*-
import json, os, sys, io
LOG = open('_v2_nm2.log', 'w', encoding='utf-8')
sys.stdout = LOG
need = """000034.SZ 688787.SH 300249.SZ 002229.SZ 603003.SH 600845.SH 600589.SH 000815.SZ 002575.SZ 600487.SH 600522.SH 601869.SH 000063.SZ 002222.SZ 601360.SH 300766.SZ 688615.SH 688207.SH 603859.SH 002929.SZ 301085.SZ 603887.SH 688795.SH 600602.SH 300451.SZ 002195.SZ 688327.SH 688343.SH 001389.SZ 300857.SZ 603629.SH 002335.SZ 300602.SZ 300684.SZ 301489.SZ 301626.SZ 300647.SZ 603124.SH 002937.SZ 688008.SH 688041.SH 688256.SH 688802.SH 000066.SZ 001339.SZ 600100.SH 002261.SZ 600839.SH 000628.SZ 603296.SH 300474.SZ 002272.SZ 688400.SH 688807.SH 688498.SH 688195.SH 300548.SZ 688048.SH 688449.SH 300709.SZ 002881.SZ 001389.SZ 603656.SH 688787.SH""".split()
maps = []
for tag, p in [('new', r'd:\mystock\solo\report_daily\theme_stock_map_latest_v2.json'),
               ('old622', r'd:\mystock\cache_daily\theme_stock_map_20260622.json'),
               ('old730', r'd:\mystock\cache_daily\theme_stock_map_20260730.json')]:
    if os.path.exists(p):
        maps.append((tag, json.load(open(p, encoding='utf-8'))))
names = {}
for tag, mp in maps:
    for c, v in mp.get('stocks', {}).items():
        if c not in names and v.get('name'):
            names[c] = v['name']
print('names total', len(names))
miss = []
for c in need:
    if c in names:
        print('%s %s' % (c, names[c]))
    else:
        miss.append(c)
print('\nMISS:', ' '.join(miss))
# 旧 AI算力 主题成员（用于核对81）
for tag, mp in maps:
    for tn, lst in mp.get('themes', {}).items():
        if '算力' in tn and ('AI' in tn or '人工智能' in tn):
            print('\n[%s] 主题=%s n=%d' % (tag, tn, len(lst)))
            print(' '.join('%s:%s' % (r['code'], r.get('name', '')) for r in lst))
LOG.close()
