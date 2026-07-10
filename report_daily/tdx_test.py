# -*- coding: utf-8 -*-
from pytdx.hq import TdxHq_API
import json

servers = [
    ('218.6.170.47', 7709),
    ('123.125.108.14', 7709),
    ('180.153.18.170', 7709),
    ('180.153.18.172', 80),
]

result = None
for host, port in servers:
    try:
        api = TdxHq_API()
        api.connect(host, port)
        # 002125: 深圳market=1
        data = api.get_security_quotes([(1, '002125')])
        api.disconnect()
        if data:
            result = data
            print(f'成功连接 {host}:{port}')
            break
        else:
            print(f'{host}:{port} 无数据')
    except Exception as e:
        print(f'{host}:{port} 失败: {e}')
        try:
            api.disconnect()
        except:
            pass

if result:
    d = result[0]
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
        'amount': round(d['amount'] / 1e8, 2),
        'bid1': d['bid1'],
        'ask1': d['ask1'],
        'time': d['servertime']
    }
    print(json.dumps(output, ensure_ascii=False))
else:
    print('所有服务器均未获取到数据')
    # 尝试获取上证指数确认连接
    try:
        api = TdxHq_API()
        api.connect('218.6.170.47', 7709)
        idx = api.get_security_quotes([(1, '000001')])
        print('上证指数测试:', idx)
        api.disconnect()
    except Exception as e2:
        print('上证测试失败:', e2)
