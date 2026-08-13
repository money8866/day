# -*- coding: utf-8 -*-
"""A股收盘复盘 - 新浪实时行情"""
import requests, re

def sina_quote(codes):
    """新浪实时行情"""
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

# 指数
idx_codes = ['sh000001', 'sz399001', 'sz399006', 'sh000016', 'sh000300', 'sh000688', 'sh000905']
idx_names = {'sh000001':'上证','sz399001':'深成','sz399006':'创业板','sh000016':'上证50',
             'sh000300':'沪深300','sh000688':'科创50','sh000905':'中证500'}

print('=== 大盘指数 8/13 ===')
idx_data = sina_quote(idx_codes)
for c in idx_codes:
    if c in idx_data:
        d = idx_data[c]
        chg = (d['price'] - d['pre_close']) / d['pre_close'] * 100
        print(f"{idx_names[c]} {d['price']:.2f} {chg:+.2f}% Vol{d['vol']:.0f}亿")

# ETF
etf_codes = ['sh588000', 'sz159915', 'sh512480', 'sz159819', 'sh518880', 'sh513500', 'sz159941', 'sh512760']
etf_names = {'sh588000':'科创50ETF','sz159915':'创业板ETF','sh512480':'半导体ETF',
             'sz159819':'医疗ETF','sh518880':'黄金ETF','sh513500':'标普ETF',
             'sz159941':'纳指ETF','sh512760':'芯片ETF'}

print('\n=== ETF涨跌 ===')
etf_data = sina_quote(etf_codes)
etf_list = []
for c in etf_codes:
    if c in etf_data:
        d = etf_data[c]
        chg = (d['price'] - d['pre_close']) / d['pre_close'] * 100
        etf_list.append((etf_names[c], chg, d['price']))
etf_list.sort(key=lambda x: x[1], reverse=True)
for n, c, p in etf_list:
    print(f"{n} {c:+.2f}% {p:.3f}")

# Tushare补涨跌分布
import tushare as ts
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
mkt = pro.daily(trade_date='20260813')
up_cnt = len(mkt[mkt['pct_chg'] > 0])
down_cnt = len(mkt[mkt['pct_chg'] < 0])
print(f'\n=== 涨跌分布 ===\n涨{up_cnt} 跌{down_cnt}')

# 涨幅TOP10
print('\n=== 涨幅TOP10 ===')
top = mkt.nlargest(10, 'pct_chg')[['ts_code','pct_chg','close']]
for _, r in top.iterrows():
    print(f"{r['ts_code']} {r['pct_chg']:+.2f}% {r['close']:.2f}")

# 跌幅TOP10
print('\n=== 跌幅TOP10 ===')
bot = mkt.nsmallest(10, 'pct_chg')[['ts_code','pct_chg','close']]
for _, r in bot.iterrows():
    print(f"{r['ts_code']} {r['pct_chg']:+.2f}% {r['close']:.2f}")
