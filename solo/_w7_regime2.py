# -*- coding: utf-8 -*-
"""市场形态分期刻画 + 行业分期收益"""
import sqlite3, csv, os
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
mkt = {}
for d, avg, up in cc.execute("""select trade_date, avg(pct_chg),
        sum(case when pct_chg>0 then 1 else 0 end)*100.0/count(*) from daily_cache
        where trade_date between '20260820' and '20261001' group by trade_date order by trade_date"""):
    mkt[d] = (avg, up)
idx = {}
for code in ('000300.SH', '000852.SH', '399006.SZ', '000688.SH'):
    idx[code] = dict(cc.execute("select trade_date, pct_chg from index_daily_cache where ts_code=? "
                                "and trade_date between '20260820' and '20261001'", (code,)))
# 行业映射
ind = {}
p = r'D:\mystock\cache_daily\stock_basic.csv'
with open(p, encoding='utf-8-sig') as f:
    rdr = csv.DictReader(f)
    cols = rdr.fieldnames
    for r in rdr:
        code = r.get('ts_code') or r.get('code')
        if code:
            ind[code] = r.get('industry') or ''
print('[stock_basic columns]', cols)
print()

print('【逐日市场（全市场等权平均 pct_chg / 上涨占比）】')
print('%-10s %8s %8s %9s %9s %9s %9s' % ('日期', '全市场', '上涨%', '沪深300', '中证1000', '创业板指', '科创50'))
for d in sorted(mkt):
    a, u = mkt[d]
    print('%-10s %+7.2f%% %7.1f%% %+8.2f%% %+8.2f%% %+8.2f%% %+8.2f%%'
          % (d, a, u, idx['000300.SH'].get(d, 0), idx['000852.SH'].get(d, 0),
             idx['399006.SZ'].get(d, 0), idx['000688.SH'].get(d, 0)))
print()

per = [('前期0825-0916', '20260825', '20260916'),
       ('转差0917-0924', '20260917', '20260924'),
       ('最新0928-0930', '20260928', '20260930')]
print('【区间市场环境】')
print('%-16s %4s %11s %9s %10s %10s   %s' % ('区间', '日数', '等权累计', '均上涨比', '科创50累', '创业板累', '普跌日(<25%)'))
for lbl, a, b in per:
    ds = [d for d in sorted(mkt) if a <= d <= b]
    if not ds:
        continue
    cum = sum(mkt[d][0] for d in ds)
    up = sum(mkt[d][1] for d in ds) / len(ds)
    bad = sum(1 for d in ds if mkt[d][1] < 25)
    print('%-16s %4d %10.2f%% %8.1f%% %9.2f%% %9.2f%%   %d/%d'
          % (lbl, len(ds), cum, up, sum(idx['000688.SH'].get(d, 0) for d in ds),
             sum(idx['399006.SZ'].get(d, 0) for d in ds), bad, len(ds)))
print()

print('【行业等权累计收益（行业来自 cache_daily/stock_basic.csv）】')
targets = ['半导体', '元器件', '通信设备', '电气设备', '软件服务',
           '建筑工程', '机械基件', '小金属', '化工原料']
print('%-10s %20s %20s %20s' % ('行业', *[l for l, _, _ in per]))
for t in targets:
    codes = [c for c, v in ind.items() if v == t]
    if not codes:
        continue
    cells = []
    for lbl, a, b in per:
        ph = ','.join('?' * len(codes))
        r = cc.execute("select sum(a.pct_chg) from daily_cache a where a.trade_date between ? and ? "
                       "and a.ts_code in (%s) group by a.ts_code" % ph, (a, b, *codes)).fetchall()
        vals = [x[0] for x in r if x[0] is not None]
        cells.append('%+.2f%%(n=%d)' % (sum(vals) / len(vals), len(vals)) if vals else '-')
    print('%-10s %20s %20s %20s' % (t, *cells))
cc.close()
