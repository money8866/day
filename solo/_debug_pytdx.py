# -*- coding: utf-8 -*-
from pytdx.hq import TdxHq_API
api = TdxHq_API()
if api.connect('218.6.170.47', 7709, time_out=5):
    quotes = api.get_security_quotes([(0, '600027'), (1, '002709')])
    print('返回类型:', type(quotes))
    print('返回条数:', len(quotes) if quotes else 0)
    if quotes:
        print('第一条:', quotes[0])
        print('第一条类型:', type(quotes[0]))
        if isinstance(quotes[0], dict):
            print('字段名:', list(quotes[0].keys()))
    api.disconnect()
else:
    print('连接失败')
