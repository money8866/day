# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - 补充侦察：主营构成/行业/Token/其它模块财报缓存"""
import os
import json
import glob
import sqlite3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


p('=' * 70)
p('1) cache_daily/parquet/mainbz_*.json 样例')
p('=' * 70)
g = glob.glob(r'D:\mystock\cache_daily\parquet\mainbz_*.json')
p('文件数:', len(g))
if g:
    p('样例路径:', g[0], '大小KB:', round(os.path.getsize(g[0]) / 1e3, 1))
    with open(g[0], 'r', encoding='utf-8') as f:
        obj = json.load(f)
    p('顶层类型:', type(obj).__name__)
    if isinstance(obj, dict):
        p('顶层 keys:', list(obj.keys())[:20])
        for k in list(obj.keys())[:3]:
            p('  %s -> %s' % (k, str(obj[k])[:400]))
    else:
        p(str(obj)[:800])

p('')
p('=' * 70)
p('2) 全盘查找行业分类 / SW 缓存 / 其它财报快照')
p('=' * 70)
PAT = []
for root in (r'D:\mystock\cache_daily', r'D:\mystock\solo'):
    for pat in ('**/classify*', '**/*classify*', '**/sw_*.csv', '**/sw_*.parquet',
                '**/*express*', '**/*forecast*', '**/*disclosure*',
                '**/stock_basic*', '**/*fina*', '**/*earnings*'):
        PAT.extend(glob.glob(os.path.join(root, pat), recursive=True))
seen = set()
for fp in sorted(set(PAT)):
    if fp in seen or '__pycache__' in fp or not os.path.isfile(fp):
        continue
    seen.add(fp)
    p('  %9.1f KB  %s' % (os.path.getsize(fp) / 1e3, fp))
p('  共 %d 个' % len(seen))

p('')
p('=' * 70)
p('3) Token 探测')
p('=' * 70)
tok = None
for cand in (r'd:\mystock\config\.env', r'd:\mystock\solo\.env',
             r'd:\mystock\solo\sli\.env', r'd:\mystock\cache_daily\.env'):
    if not os.path.exists(cand):
        p('  %-40s 不存在' % cand)
        continue
    p('  %-40s 存在' % cand)
    with open(cand, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            p('     %s = %s' % (k, (v[:6] + '***' + v[-4:]) if len(v) > 12 else '***'))
            if 'TOKEN' in k.upper() and tok is None:
                tok = v

p('')
p('=' * 70)
p('4) Tushare API 可用性 + 历史财报可获取性（只探测，不落库）')
p('=' * 70)
p('token 已找到:', bool(tok))
if tok:
    import tushare as ts
    pro = ts.pro_api(tok)
    tests = [
        ('trade_cal', dict(exchange='SSE', start_date='20180101', end_date='20180110')),
        ('fina_indicator_vip', dict(period='20191231', fields='ts_code,end_date,ann_date,or_yoy,netprofit_yoy')),
        ('daily_basic', dict(trade_date='20191231', fields='ts_code,pe_ttm,pb,ps,total_mv')),
        ('income_vip', dict(period='20191231', fields='ts_code,end_date,ann_date,revenue,n_income_attr_p')),
        ('balancesheet_vip', dict(period='20191231',
                                  fields='ts_code,end_date,ann_date,total_assets,accounts_receiv,inventories')),
        ('cashflow_vip', dict(period='20191231', fields='ts_code,end_date,ann_date,n_cashflow_act')),
        ('express_vip', dict(period='20191231', fields='ts_code,end_date,ann_date,revenue,n_income')),
        ('forecast_vip', dict(period='20191231', fields='ts_code,end_date,ann_date,type,p_change_min')),
    ]
    for m, kw in tests:
        try:
            df = getattr(pro, m)(**kw)
            n = 0 if df is None else len(df)
            cols = [] if df is None else list(df.columns)
            p('  %-20s OK  rows=%-6d cols=%s' % (m, n, cols))
        except Exception as e:
            p('  %-20s FAIL %s' % (m, str(e)[:160]))

p('')
p('=' * 70)
p('5) hvt_bull / ige / eld 的财报相关缓存路径')
p('=' * 70)
for root in (r'd:\mystock\solo\hvt_bull', r'd:\mystock\solo\ige', r'd:\mystock\solo\eld'):
    p('--- %s' % root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__', '.git')]
        for fn in filenames:
            if fn.endswith(('.parquet', '.db', '.csv', '.npz')) and 'cache' in dirpath.lower():
                fp = os.path.join(dirpath, fn)
                p('   %9.1f KB %s' % (os.path.getsize(fp) / 1e3, fp))

with open(os.path.join(HERE, '_fs_probe3.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
