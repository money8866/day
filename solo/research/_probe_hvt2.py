# -*- coding: utf-8 -*-
"""探查 HVT json 内 all_states / events 的具体结构（只读）"""
import os, json

RD = r'd:\mystock\solo\report_daily'
f = os.path.join(RD, 'hvt_bull_20260924.json')
d = json.load(open(f, encoding='utf-8'))
for k in ['events', 'all_states', 'te_buy_pool', 'first_echelon_pool', 'te_buy_pool_kept']:
    v = d.get(k)
    print('==', k, '|', type(v).__name__)
    if isinstance(v, dict):
        for kk, vv in v.items():
            t = type(vv).__name__
            if isinstance(vv, list):
                s = vv[0] if vv else None
                print('   %-18s list len=%-5d sample=%s' % (kk, len(vv), str(s)[:160]))
            else:
                print('   %-18s %s = %s' % (kk, t, str(vv)[:120]))
    elif isinstance(v, list):
        print('   len', len(v), '| row0:', str(v[0])[:300] if v else '-')
