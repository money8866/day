# -*- coding: utf-8 -*-
"""probe11: 名称变更表结构 + 三表列并集（ST 剔除与字段可用性）"""
import os, glob, random
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


p('1) treasure_namechg 结构')
g = glob.glob(os.path.join(CD, 'treasure_namechg_*.parquet'))
p('   文件数:', len(g))
if g:
    d = pd.read_parquet(g[0])
    p('   shape=%s cols=%s' % (d.shape, list(d.columns)))
    p(d.head(6).to_string())
    if len(g) > 1:
        d2 = pd.read_parquet(g[1])
        p('   第二文件 shape=%s' % (d2.shape,))
        p(d2.head(6).to_string())

p('')
p('2) 三表列并集（抽样 400 文件）')
for key in ('income', 'balance', 'cashflow'):
    fs = [f for f in os.listdir(PD) if f.startswith(key) and f.endswith('.parquet')]
    fs += [f for f in os.listdir(CD) if f.startswith(key) and f.endswith('.parquet')]
    p('   --- %s: %d 文件' % (key, len(fs)))
    # 文件名模式
    pats = {}
    for f in fs:
        base = f.split('.parquet')[0]
        tag = 'code_' if '_code_' in base else ('v14_' if '_v14_' in base else 'plain')
        pats[tag] = pats.get(tag, 0) + 1
    p('       命名模式:', pats)
    random.seed(7)
    sub = random.sample(fs, min(400, len(fs)))
    cols = set()
    for f in sub:
        d = os.path.join(PD, f) if os.path.exists(os.path.join(PD, f)) else os.path.join(CD, f)
        try:
            cols |= set(pd.read_parquet(d).columns)
        except Exception:
            pass
    p('       列并集(%d): %s' % (len(cols), sorted(cols)))

p('')
p('3) 关键字段是否可用')
for k in ('money_cap', 'total_liab', 'contract_liab', 'notes_receiv', 'st_borr',
          'lt_borr', 'total_cur_liab', 'total_hldr_eqy_exc_min_int', 'accounts_receiv',
          'inventories', 'goodwill', 'fix_assets', 'intan_assets'):
    p('   %-28s' % k)

with open(os.path.join(HERE, '_fs_probe11.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
