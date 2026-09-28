# -*- coding: utf-8 -*-
"""解析候选代码名称/行业"""
import sys
LOG = open(r'd:\mystock\solo\_op_name.log', 'w', encoding='utf-8')
sys.stdout = LOG
from tushare_quant import pro

CODES = ['603003.SH', '000502.SZ', '600225.SH', '002089.SZ', '000673.SZ', '603990.SH',
         '300846.SZ', '688158.SH', '688316.SH', '920493.BJ', '300442.SZ', '002229.SZ',
         '300383.SZ', '603881.SH', '300738.SZ', '300017.SZ', '600589.SH', '000815.SZ',
         '300895.SZ', '600797.SH', '603220.SH', '600602.SH', '002335.SZ', '603985.SH',
         '300857.SZ', '300571.SZ', '002015.SZ', '002261.SZ', '688258.SH', '000034.SZ',
         '301085.SZ', '688227.SH', '920808.BJ', '301202.SZ', '600941.SH', '601728.SH',
         '600050.SH', '002123.SZ', '000971.SZ', '002313.SZ']
try:
    sb = pro.stock_basic(fields='ts_code,name,industry,market,list_status')
    m = {r['ts_code']: r for _, r in sb.iterrows()}
except Exception as e:
    print('err %s' % e)
    m = {}
for c in CODES:
    r = m.get(c)
    if r is None:
        print('%s  <NOT IN stock_basic>' % c)
    else:
        print('%s  %-10s %-12s %-8s %s' % (c, r['name'], r['industry'], r['market'], r.get('list_status')))
LOG.close()
