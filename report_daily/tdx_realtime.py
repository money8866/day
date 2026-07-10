# -*- coding: utf-8 -*-
from pytdx.hq import TdxHq_API
import json

api = TdxHq_API()
api.connect('218.6.170.47', 7709)

# 002125 湘潭电化 = market=0 (深圳)
data = api.get_security_quotes([(0, '002125')])
if data:
    d = data[0]
    pct = (d['price'] / d['last_close'] - 1) * 100
    output = {
        'name': '湘潭电化',
        'code': '002125',
        'price': d['price'],
        'last_close': d['last_close'],
        'open': d['open'],
        'high': d['high'],
        'low': d['low'],
        'pct_chg': round(pct, 2),
        'vol': d['vol'],
        'amount_yi': round(d['amount'] / 1e8, 2),
        'bid1': d['bid1'],
        'ask1': d['ask1'],
        'bid_vol1': d['bid_vol1'],
        'ask_vol1': d['ask_vol1'],
        'time': d['servertime']
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

api.disconnect()
