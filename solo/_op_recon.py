# -*- coding: utf-8 -*-
"""算力运营重构 —— 证据采集（原池 + 全市场主营文本扫描）"""
import io
import json
import os
import re
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
LOG = open(r'd:\mystock\solo\_op_recon.log', 'w', encoding='utf-8')
sys.stdout = LOG

MB = r'd:\mystock\cache_daily\stock_company_mainbiz.json'
CFG = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json'
MAP = r'd:\mystock\solo\report_daily\theme_stock_map_latest_v2.json'
DB = r'd:\mystock\cache_daily\stock_data.db'
OLD81 = r'd:\mystock\solo\_prev_map_ai.json'

mb = json.load(open(MB, encoding='utf-8'))
cfg = json.load(open(CFG, encoding='utf-8'))
new = json.load(open(MAP, encoding='utf-8'))
old = json.load(open(OLD81, encoding='utf-8'))

# ---------- 名称字典 ----------
name = {}
for t, ms in new['themes'].items():
    for m in ms:
        name[m['code']] = m['name']
for m in old['themes']['AI算力']:
    name[m['code']] = m['name']

con = sqlite3.connect(DB)
cur = con.cursor()
tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print('DB tables(%d): %s' % (len(tabs), tabs[:30]))
for t in tabs:
    cols = [r[1] for r in cur.execute('PRAGMA table_info("%s")' % t)]
    if 'ts_code' in cols and any(c in cols for c in ('name', 'stock_name')):
        cn = 'name' if 'name' in cols else 'stock_name'
        n0 = cur.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        print('  -> name source: %s (%s) rows=%d' % (t, cn, n0))
        for code, nm in cur.execute('SELECT ts_code, %s FROM "%s"' % (cn, t)):
            name.setdefault(code, nm)
        break
con.close()
print('名称字典 = %d' % len(name))

# ---------- config 四主题现状 ----------
print('\n=== config 现有四主题成员 ===')
for k in ('CPO', 'LIQUID_COOLING', 'AI_SERVER', 'AI_APPLICATION'):
    v = cfg[k]
    ld = v.get('leader_stocks') or []
    co = v.get('core_stocks') or []
    nm = lambda lst: [name.get(c, c) for c in lst]
    print('%s (%s) leaders=%d %s' % (k, v.get('name_cn'), len(ld), nm(ld)))
    print('    core=%d %s' % (len(co), nm(co)))
print('\nconfig 顶层 key: %s' % [k for k in cfg.keys() if not k.startswith('_')])

# ---------- 全市场主营文本扫描：算力运营 ----------
OP_KW = ['算力租赁', 'GPU租赁', '算力服务', '智算中心', '智能算力中心', '智算', '算力调度',
         '算力网络', 'GPU云', 'AI云', '云算力', '算力平台', 'AI算力', '算力资源', '云计算服务',
         '数据中心服务', 'IDC', '数据中心']
HITS = ['算力租赁', 'GPU租赁', '算力服务', '智算中心', '智能算力中心', '智算', '算力调度',
        '算力网络', 'GPU云', 'AI云', '云算力', '算力平台', 'AI算力', '算力资源', '算力']

print('\n=== 全市场主营文本命中「算力运营」关键词（按命中强度排序）===')
rows = []
for code, txt in mb.items():
    if not txt:
        continue
    hits = [w for w in HITS if w in txt]
    if not hits:
        continue
    rows.append((len(hits), code, name.get(code, ''), '|'.join(hits), txt))
rows.sort(key=lambda x: (-x[0], x[1]))
print('命中总数 = %d' % len(rows))
for n, code, nm, hits, txt in rows:
    print('%2d | %s %-8s | %-28s | %s' % (n, code, nm, hits, txt[:150]))

# ---------- 原 81 只主营文本（含旧 via）----------
print('\n=== 原 AI算力 81 只 主营文本 ===')
old_ai = {}
for m in old['themes']['AI算力']:
    old_ai[m['code']] = (m['name'], (m.get('via') or ''), (m.get('industry') or ''))
for i, (code, (nm, via, ind)) in enumerate(sorted(old_ai.items()), 1):
    print('%2d| %s %-8s | %-18s | %-10s | %s' % (i, code, nm, via, ind, (mb.get(code) or '<无>')[:140]))
