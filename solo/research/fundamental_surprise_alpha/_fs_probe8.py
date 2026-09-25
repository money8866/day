# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - 价格边界与结构确认 (probe8)"""
import os, glob, sqlite3, json
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


DB = r'D:\mystock\cache_daily\stock_data.db'
CD = r'D:\mystock\cache_daily'

c = sqlite3.connect(DB, timeout=180)

p('=' * 60)
p('1) daily_cache 结构')
cols = [r[1] for r in c.execute("PRAGMA table_info(daily_cache)")]
p('   列:', cols)
p('   样本(000001.SZ 最新3行):')
for r in c.execute("SELECT * FROM daily_cache WHERE ts_code='000001.SZ' ORDER BY trade_date DESC LIMIT 3"):
    p('   ', r)

p('')
p('2) index_daily_cache 内容(按行数)')
try:
    q = pd.read_sql_query(
        "SELECT ts_code, COUNT(*) n, MIN(trade_date) mn, MAX(trade_date) mx "
        "FROM index_daily_cache GROUP BY ts_code ORDER BY n DESC LIMIT 15", c)
    p(q.to_string())
except Exception as e:
    p('   ERR', e)
c.close()

p('')
p('3) cache_daily 下 .db 文件')
for d in ('', 'parquet', 'industry'):
    for fp in glob.glob(os.path.join(CD, d, '*.db')):
        p('   %.2fMB  %s' % (os.path.getsize(fp) / 1048576.0, fp))

p('')
p('4) 是否存在日线 parquet（检查更长历史）')
for pat in ('daily*.parquet', '*daily*.parquet', 'stk_daily*.parquet', 'kline*.parquet'):
    for d in ('', 'parquet'):
        found = glob.glob(os.path.join(CD, d, pat))
        if found:
            p('   %s -> %d 个, 例: %s' % (pat, len(found),
                                          [os.path.basename(x) for x in found[:4]]))

p('')
p('5) treasure_fin_ind 结构')
g = glob.glob(os.path.join(CD, 'treasure_fin_ind_*.parquet'))
p('   文件数:', len(g))
if g:
    d0 = pd.read_parquet(g[0])
    p('   样本文件:', os.path.basename(g[0]), 'shape=', d0.shape)
    p('   列(%d):' % len(d0.columns), list(d0.columns))
    if 'ann_date' in d0.columns:
        p('   ann_date 空值率: %.4f' % d0['ann_date'].isna().mean())
    if 'end_date' in d0.columns:
        p('   end_date 分布(top6):')
        p(d0['end_date'].astype(str).value_counts().head(6).to_string())

p('')
p('6) 行业分类')
fp1 = os.path.join(CD, 'sw_industry_map.json')
if os.path.exists(fp1):
    j = json.load(open(fp1, 'r', encoding='utf-8'))
    p('   sw_industry_map.json type=%s len=%s' % (type(j).__name__, len(j)))
    if isinstance(j, dict):
        p('   sample:', list(j.items())[:4])
    else:
        p('   sample:', j[:4])
fp2 = os.path.join(CD, 'industry', 'sw_industry_map.csv')
if os.path.exists(fp2):
    d2 = pd.read_csv(fp2, dtype=str)
    p('   industry/sw_industry_map.csv shape=', d2.shape, 'cols=', list(d2.columns))
    p(d2.head(3).to_string())

p('')
p('7) stock_basic')
for cand in (os.path.join(CD, 'stock_basic.csv'), os.path.join(CD, 'parquet', 'stock_basic.csv')):
    if os.path.exists(cand):
        d3 = pd.read_csv(cand, dtype=str)
        p('   %s shape=%s cols=%s' % (cand, d3.shape, list(d3.columns)))
        p(d3.head(3).to_string())
        break

p('')
p('8) forecast 预告覆盖')
g4 = sorted(glob.glob(os.path.join(CD, 'forecast_*.parquet')))
p('   forecast 文件数:', len(g4))
if g4:
    d4 = pd.read_parquet(g4[0])
    p('   cols=', list(d4.columns))
    anns, n = set(), 0
    for fp in g4:
        try:
            dd = pd.read_parquet(fp, columns=['ann_date'])
            anns |= set(dd['ann_date'].dropna().astype(str))
            n += len(dd)
        except Exception:
            pass
    p('   总行数=%d  ann_date 范围 %s ~ %s' % (n, min(anns) if anns else '-', max(anns) if anns else '-'))

with open(os.path.join(HERE, '_fs_probe8.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
