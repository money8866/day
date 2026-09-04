# -*- coding: utf-8 -*-
import urllib.request, json, re

hdrs = {'Referer':'http://finance.sina.com.cn','User-Agent':'Mozilla/5.0'}

def sina(sym):
    url = f'http://hq.sinajs.cn/list={sym}'
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode('gbk')

def parse(sym):
    d = sina(sym)
    if '"' not in d: return None
    parts = d.split('"')[1].split(',')
    if len(parts) < 4: return None
    name = parts[0]
    open_p = float(parts[1])
    prev = float(parts[2])
    price = float(parts[3])
    high = float(parts[4])
    low = float(parts[5])
    chg = (price - prev) / prev * 100
    return {'name':name,'price':price,'prev':prev,'open':open_p,'high':high,'low':low,'chg':chg}

# 指数
indices = {
    'sh000001':'上证','sz399001':'深成','sz399006':'创业板',
    'sh000300':'沪深300','sh000016':'上证50','sh000688':'科创50','sh000905':'中证500'
}
print('=== 指数 ===')
for sym,label in indices.items():
    r = parse(sym)
    if r:
        print(f"{label}: {r['price']:.2f}  {r['chg']:+.2f}%  开{r['open']:.2f} 高{r['high']:.2f} 低{r['low']:.2f}")

# ETF
etfs = {
    'sh512480':'半导体ETF','sz159813':'芯片ETF','sh588000':'科创50ETF',
    'sz159915':'创业板ETF','sz159611':'电力ETF','sh518880':'黄金ETF',
    'sz159667':'机器人ETF','sh513500':'纳指ETF','sh513100':'标普ETF',
    'sz159865':'医疗ETF','sh512760':'芯片ETF国联安','sz159870':'化工ETF',
    'sh516650':'有色ETF','sz159322':'黄金股ETF',
}
print('\n=== ETF ===')
for sym,label in etfs.items():
    r = parse(sym)
    if r:
        print(f"{label}: {r['price']:.3f}  {r['chg']:+.2f}%")
