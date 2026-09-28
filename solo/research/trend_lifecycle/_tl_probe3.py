# -*- coding: utf-8 -*-
"""TL-01 探针 3：复权因子质量 / 年度覆盖 / 指数可得性"""
import os
import numpy as np
import pandas as pd

FS = r'd:\mystock\solo\research\fundamental_surprise_alpha\data'
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

print('=' * 70, flush=True)
print('[1] price_panel 复权因子 qf 的时变性检验', flush=True)
px = pd.read_parquet(os.path.join(FS, 'price_panel.parquet'),
                     columns=['ts_code', 'trade_date', 'close', 'qf', 'qfq_close',
                              'high', 'low', 'qfq_high', 'qfq_low'])
px['trade_date'] = px['trade_date'].astype(str)
print('  行=%d 只=%d' % (len(px), px['ts_code'].nunique()), flush=True)
g = px.groupby('ts_code')['qf']
nq = g.nunique()
print('  每只股票 qf 不同取值数分布: %s' % nq.value_counts().head(8).to_dict(), flush=True)
print('  qf 全期恒定的股票数 = %d / %d' % (int((nq == 1).sum()), len(nq)), flush=True)
for c in ('000001.SZ', '600519.SH', '300750.SZ'):
    s = px[px['ts_code'] == c].sort_values('trade_date')
    if len(s) == 0:
        continue
    print('  %s  n=%d  qf uniq=%d  qf first=%.6f last=%.6f  close[0]=%.3f qfq[0]=%.3f' % (
        c, len(s), s['qf'].nunique(), s['qf'].iloc[0], s['qf'].iloc[-1],
        s['close'].iloc[0], s['qfq_close'].iloc[0]), flush=True)
print('  qfq_close 缺失率 %.6f ; qfq_high 缺失率 %.6f' % (
    px['qfq_close'].isna().mean(), px['qfq_high'].isna().mean()), flush=True)

print('=' * 70, flush=True)
print('[2] price_panel 年度覆盖', flush=True)
px['yr'] = px['trade_date'].str[:4]
t = px.groupby('yr').agg(rows=('ts_code', 'size'), n=('ts_code', 'nunique'))
print(t.to_string(), flush=True)
del px

print('=' * 70, flush=True)
print('[3] 指数文件全清单', flush=True)
cand = []
for d in (CD, PD):
    for f in os.listdir(d):
        if f.startswith('index_daily') and f.endswith('.parquet'):
            cand.append(os.path.join(d, f))
print('  文件数=%d' % len(cand), flush=True)
rows = []
for fp in cand:
    b = os.path.basename(fp)
    p = b.replace('.parquet', '').split('_')
    code = p[2] + '.' + p[3] if len(p) > 4 else b
    try:
        d = pd.read_parquet(fp, columns=['ts_code', 'trade_date', 'close'])
    except Exception:
        continue
    rows.append(dict(code=code, n=len(d),
                     d0=str(d['trade_date'].min()), d1=str(d['trade_date'].max())))
r = pd.DataFrame(rows)
agg = r.groupby('code').agg(files=('n', 'size'), rows=('n', 'sum'),
                            d0=('d0', 'min'), d1=('d1', 'max'))
print(agg.to_string(), flush=True)

print('=' * 70, flush=True)
print('[4] stk_factor 文件命名分布（前 15）', flush=True)
sf = [f for f in os.listdir(PD) if f.startswith('stk_factor') and f.endswith('.parquet')]
codes = {}
for f in sf:
    p = f.replace('.parquet', '').split('_')
    # stk_factor_pro_range_000001_SZ_20240101_20260620
    if len(p) >= 8:
        key = (p[4] + '.' + p[5], p[6], p[7])
        codes[key] = codes.get(key, 0) + 1
ks = sorted(codes.items(), key=lambda x: -x[1])
print('  组合数=%d' % len(codes), flush=True)
for k, v in ks[:15]:
    print('   %s x%d' % (str(k), v), flush=True)
s0 = sorted(set(k[1] for k in codes))
print('  start 日期种类 %d  例:%s' % (len(s0), s0[:6]), flush=True)
s1 = sorted(set(k[2] for k in codes))
print('  end   日期种类 %d  例:%s' % (len(s1), s1[-6:]), flush=True)
