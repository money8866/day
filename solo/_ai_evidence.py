# -*- coding: utf-8 -*-
"""AI算力 拆旧建新：81 只成员的主营业务证据盘点"""
import json, os, io, sys, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = r'd:\mystock\solo'
CACHE = r'd:\mystock\cache_daily'
MAP = os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json')

with open(MAP, 'r', encoding='utf-8') as f:
    raw = json.load(f)

agg = None
for cand in ('stock_company_mainbiz.json', 'stock_mainbiz.json', 'company_mainbiz.json'):
    p = os.path.join(CACHE, cand)
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            agg = json.load(f)
        print('mainbiz 聚合源: %s  记录=%d' % (p, len(agg)))
        break
if agg is None:
    print('未找到 mainbiz 聚合文件，改用 per-stock mainbz_*.json')

def get_txt(code):
    if agg:
        r = agg.get(code)
        if isinstance(r, str):
            return r, ''
        if isinstance(r, dict):
            return (r.get('mainbiz') or r.get('main_business') or r.get('business') or ''), \
                   (r.get('fore_biz') or r.get('forebusiness') or '')
    p = os.path.join(CACHE, 'parquet', 'mainbz_%s.json' % code.replace('.', '_'))
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            return (d.get('mainbiz') or d.get('main_business') or d.get('business') or json.dumps(d, ensure_ascii=False)[:200]), ''
        return str(d)[:200], ''
    return '', ''

ai = raw['themes']['AI算力']
print('AI算力成员 %d 只' % len(ai))
print('=' * 110)
for i, s in enumerate(ai, 1):
    code, name = s.get('code'), s.get('name')
    txt, fb = get_txt(code)
    print('%2d| %s %s | via=%s | ind=%s | layer=%s' % (i, code, name, s.get('via'), s.get('industry'), s.get('irs_layer')))
    print('    主营: %s' % (txt[:200] if txt else '<无>'))
