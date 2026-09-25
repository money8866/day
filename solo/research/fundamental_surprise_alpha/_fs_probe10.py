# -*- coding: utf-8 -*-
"""probe10: 三表 parquet 字段与重述结构（as-of 可行性）"""
import os, re, glob, collections
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


# 命名模式
for key in ('income', 'balancesheet', 'balance', 'cashflow'):
    for d, tag in ((CD, 'top'), (PD, 'parquet')):
        fs = [f for f in os.listdir(d) if f.startswith(key + '_') and f.endswith('.parquet')]
        if fs:
            ex = fs[:3]
            p('%s.%s: %d 个 例=%s' % (key, tag, len(fs), ex))
    p('')

# 读样本并 dump 列
def show(path, key):
    d = pd.read_parquet(path)
    p('--- %s  shape=%s' % (os.path.basename(path), d.shape))
    p('    cols(%d): %s' % (len(d.columns), list(d.columns)))
    if 'end_date' in d.columns:
        vc = d['end_date'].astype(str).value_counts()
        p('    end_date 期数=%d  重复期数=%d  最大重复=%d' % (len(vc), (vc > 1).sum(), vc.max()))
    for c in ('ann_date', 'f_ann_date', 'update_flag', 'report_type', 'comp_type'):
        if c in d.columns:
            s = d[c].astype(str)
            p('    %s: n_null=%d  range=%s~%s' % (c, d[c].isna().sum(), s.min(), s.max()))
    return d


for key in ('income', 'balance', 'balancesheet', 'cashflow'):
    for d in (PD, CD):
        fs = sorted(f for f in os.listdir(d) if f.startswith(key + '_') and f.endswith('.parquet'))
        if fs:
            show(os.path.join(d, fs[0]), key)
            p('')

# 重述实例：000001.SZ
p('=' * 50)
p('重述检查 000001.SZ')
for name in ('income', 'balance', 'balancesheet', 'cashflow'):
    for d in (PD, CD):
        fs = glob.glob(os.path.join(d, '%s_000001*' % name))
        if fs:
            dd = pd.read_parquet(fs[0])
            p('  %s -> shape=%s' % (os.path.basename(fs[0]), dd.shape))
            if 'end_date' in dd.columns and 'ann_date' in dd.columns:
                vc = dd['end_date'].astype(str).value_counts()
                multi = vc[vc > 1].index[:3]
                for e in multi:
                    sub = dd[dd['end_date'].astype(str) == e][['end_date', 'ann_date'] +
                                                              [c for c in ('f_ann_date', 'update_flag') if c in dd.columns]]
                    p('     end=%s 多版本:' % e)
                    p(sub.to_string())
            break

with open(os.path.join(HERE, '_fs_probe10.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
