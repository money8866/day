# -*- coding: utf-8 -*-
"""补充候选取证：CPO 池外 / AI服务器 池外。"""
import os, json
CACHE = r'd:\mystock\cache_daily'
with open(os.path.join(CACHE, 'stock_company_mainbiz.json'), 'r', encoding='utf-8') as f:
    agg = json.load(f)

def g(code):
    r = agg.get(code)
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        return (r.get('mainbiz') or r.get('main_business') or r.get('business') or '<无>')
    return '<未收录>'

CPO_X = [('603083.SH','剑桥科技'),('000988.SZ','华工科技'),('300620.SZ','光库科技'),
         ('688195.SH','腾景科技'),('688498.SH','源杰科技'),('300504.SZ','天邑股份'),
         ('688048.SH','长光华芯'),('002897.SZ','意华股份')]
SRV_X = [('601138.SH','工业富联'),('000034.SZ','神州数码'),('002261.SZ','拓维信息'),
         ('001339.SZ','智微智能'),('603296.SH','华勤技术'),('688041.SH','海光信息'),
         ('300474.SZ','景嘉微'),('688521.SH','芯原股份')]
print('=== CPO 池外候选 ===')
for c,n in CPO_X: print('%s %s | %s' % (c,n,g(c)))
print('\n=== AI服务器 池外候选 ===')
for c,n in SRV_X: print('%s %s | %s' % (c,n,g(c)))
