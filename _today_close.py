# -*- coding: utf-8 -*-
"""今日收盘复盘"""
import requests, re, tushare as ts

def sina_quote(codes):
    url = f"http://hq.sinajs.cn/list={','.join(codes)}"
    headers = {'Referer': 'http://finance.sina.com.cn'}
    r = requests.get(url, headers=headers, timeout=10)
    r.encoding = 'gbk'
    data = {}
    for line in r.text.strip().split('\n'):
        if not line or 'hq_str_' not in line: continue
        m = re.match(r'var hq_str_(\w+)="(.*)"', line)
        if m:
            code, vals = m.groups()
            if vals:
                arr = vals.split(',')
                if len(arr) >= 32:
                    data[code] = {
                        'name': arr[0],
                        'open': float(arr[1]),
                        'pre_close': float(arr[2]),
                        'price': float(arr[3]),
                        'high': float(arr[4]),
                        'low': float(arr[5]),
                        'vol': float(arr[8]) / 1e8,
                        'amount': float(arr[9]) / 1e8,
                    }
    return data

idx_codes = ['sh000001','sz399001','sz399006','sh000016','sh000300','sh000688','sh000905']
names = {'sh000001':'上证','sz399001':'深成','sz399006':'创业板','sh000016':'上证50',
         'sh000300':'沪深300','sh000688':'科创50','sh000905':'中证500'}

print('=== 指数 ===')
d = sina_quote(idx_codes)
for c in idx_codes:
    if c in d:
        x = d[c]
        chg = (x['price']-x['pre_close'])/x['pre_close']*100
        print(f"{names[c]} {x['price']:.2f} {chg:+.2f}% 量{x['vol']:.0f}亿")

etfs = ['sh588000','sz159915','sh512480','sz159819','sh518880','sh513500','sz159941','sh512760']
enames = {'sh588000':'科创50ETF','sz159915':'创业板ETF','sh512480':'半导体ETF',
          'sz159819':'医疗ETF','sh518880':'黄金ETF','sh513500':'标普ETF',
          'sz159941':'纳指ETF','sh512760':'芯片ETF'}
print('\n=== ETF ===')
efd = sina_quote(etfs)
lst = []
for c in etfs:
    if c in efd:
        x = efd[c]
        chg = (x['price']-x['pre_close'])/x['pre_close']*100
        lst.append((enames[c], chg, x['price']))
lst.sort(key=lambda x:x[1], reverse=True)
for n,c,p in lst:
    print(f"{n} {c:+.2f}%")

pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
mkt = pro.daily(trade_date='20260825')
if len(mkt):
    up = len(mkt[mkt['pct_chg']>0])
    dn = len(mkt[mkt['pct_chg']<0])
    print(f'\n=== 涨跌分布 ===\n涨{up} 跌{dn}')
    top = mkt.nlargest(10,'pct_chg')[['ts_code','pct_chg','close']]
    bot = mkt.nsmallest(10,'pct_chg')[['ts_code','pct_chg','close']]
    print('\n涨幅TOP10')
    for _,r in top.iterrows():
        print(f"{r['ts_code']} {r['pct_chg']:+.2f}%")
    print('\n跌幅TOP10')
    for _,r in bot.iterrows():
        print(f"{r['ts_code']} {r['pct_chg']:+.2f}%")
else:
    print('Tushare暂无数据')
