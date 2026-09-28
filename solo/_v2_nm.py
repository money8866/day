# -*- coding: utf-8 -*-
import json, os, sys
LOG = open('_v2_nm.log', 'w', encoding='utf-8')
sys.stdout = LOG
need = ['000034.SZ','688787.SH','300249.SZ','002229.SZ','603003.SH','600845.SH','600589.SH','000815.SZ','002575.SZ','600487.SH','600522.SH','601869.SH','000063.SZ','002222.SZ','601360.SH','300766.SZ','688615.SH','688207.SH','603859.SH','002929.SZ','301085.SZ','603887.SH','688795.SH','600602.SH','300451.SZ','002195.SZ','688327.SH','688343.SH','001389.SZ','300857.SZ','603629.SH','002335.SZ','300602.SZ','300684.SZ','301489.SZ','301626.SZ','300647.SZ','603124.SH','002937.SZ','688008.SH','688041.SH','688256.SH','688802.SH','000066.SZ','001339.SZ','600100.SH','002261.SZ','600839.SH','000628.SZ','603296.SH','300474.SZ','002272.SZ','688400.SH','688807.SH','688498.SH','688195.SH','300548.SZ']
srcs = [
 ('sw_industry_map', r'd:\mystock\cache_daily\sw_industry_map.json'),
 ('tracker_stats', r'd:\mystock\solo\cache_daily\tracker_stats.json'),
 ('map_old', r'd:\mystock\cache_daily\theme_stock_map_20260622.json'),
]
found = {}
for tag, p in srcs:
    if not os.path.exists(p):
        print('MISS', tag); continue
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print('ERR', tag, e); continue
    print('--- %s type=%s keys=%s' % (tag, type(d).__name__, list(d.keys())[:8] if isinstance(d, dict) else len(d)))
    # try common shapes
    def probe(obj, depth=0):
        if depth > 2: return
        if isinstance(obj, dict):
            for k in list(obj.keys())[:3]:
                v = obj[k]
                print('   %s key=%s type=%s sample=%s' % (' '*depth, k, type(v).__name__, str(v)[:120]))
                probe(v, depth+1)
    probe(d)
LOG.close()
