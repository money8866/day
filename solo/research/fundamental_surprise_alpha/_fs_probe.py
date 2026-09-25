# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - 环境/数据源侦察（只读，不写入任何生产模块）"""
import os
import sqlite3
import glob
import json

OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


p('=' * 70)
p('1) 主行情库 D:\\mystock\\cache_daily\\stock_data.db')
p('=' * 70)
DB = r'D:\mystock\cache_daily\stock_data.db'
p('exists:', os.path.exists(DB), 'size_MB:', round(os.path.getsize(DB) / 1e6, 1))
c = sqlite3.connect(DB)
for (name, typ) in c.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"):
    try:
        n = c.execute("SELECT COUNT(*) FROM [%s]" % name).fetchone()[0]
    except Exception as e:
        n = -1
    p('  %-28s %-6s rows=%s' % (name, typ, n))
c.close()

p('')
p('=' * 70)
p('2) cache_daily 目录下的 csv / db / sqlite 文件')
p('=' * 70)
CD = r'D:\mystock\cache_daily'
for f in sorted(os.listdir(CD)):
    fp = os.path.join(CD, f)
    if os.path.isfile(fp) and os.path.getsize(fp) > 0:
        p('  %-46s %10.1f KB' % (f, os.path.getsize(fp) / 1e3))

p('')
p('=' * 70)
p('3) 全盘搜索含基本面语义的文件（db/sqlite/parquet/csv）')
p('=' * 70)
ROOTS = [r'D:\mystock\cache_daily', r'D:\mystock\solo']
KEYS = ('fina', 'income', 'balance', 'cashflow', 'cash_flow', 'express',
        'forecast', 'disclosure', 'daily_basic', 'stock_basic', 'dividend',
        'mainbz', 'sw_', 'index_classify', 'industry')
hits = []
for root in ROOTS:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ('.git', 'node_modules', '__pycache__', 'chip_cache')]
        depth = dirpath[len(root):].count(os.sep)
        if depth > 3:
            dirnames[:] = []
            continue
        for fn in filenames:
            low = fn.lower()
            if not low.endswith(('.db', '.sqlite', '.parquet', '.csv', '.json', '.npz')):
                continue
            if any(k in low for k in KEYS):
                fp = os.path.join(dirpath, fn)
                try:
                    sz = os.path.getsize(fp) / 1e3
                except OSError:
                    continue
                hits.append((sz, fp))
hits.sort(reverse=True)
for sz, fp in hits[:80]:
    p('  %10.1f KB  %s' % (sz, fp))
p('  共 %d 个命中文件' % len(hits))

p('')
p('=' * 70)
p('4) 各 DB 内的表清单')
p('=' * 70)
seen = set()
for sz, fp in hits:
    if not fp.lower().endswith(('.db', '.sqlite')):
        continue
    if fp in seen:
        continue
    seen.add(fp)
    try:
        cc = sqlite3.connect(fp)
        tabs = [r[0] for r in cc.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        p('  %s  ->  %s' % (fp, tabs))
        cc.close()
    except Exception as e:
        p('  %s  ->  读取失败 %s' % (fp, e))

p('')
p('=' * 70)
p('5) token 配置位置')
p('=' * 70)
for cand in (r'd:\mystock\config\.env', r'd:\mystock\solo\.env',
             r'd:\mystock\solo\sli\.env', r'd:\mystock\solo\bts\.env'):
    p('  %-40s exists=%s' % (cand, os.path.exists(cand)))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_fs_probe.txt'),
          'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
