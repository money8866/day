# -*- coding: utf-8 -*-
from pytdx.hq import TdxHq_API
import json, time

api = TdxHq_API()

# 先测试能通的上证
print('测试连接...')
api.connect('218.6.170.47', 7709)
idx = api.get_security_quotes([(1, '000001')])
print('上证连接状态:', len(idx) > 0)

# 尝试不同market组合查002125
print()
print('=== 002125 查询测试 ===')
for market in [0, 1]:
    for code in ['002125', '002 125', '2125']:
        try:
            data = api.get_security_quotes([(market, code)])
            if data:
                print(f'market={market}, code={code}: {data[0]["price"]}')
        except:
            pass

# 同时查几只确认能用的深圳股票
print()
print('=== 对比查询 ===')
test_stocks = [
    (0, '600519'),  # 茅台
    (1, '000001'),  # 平安
    (0, '000001'),  # 深证?
    (1, '000002'),  # 万科
    (1, '002125'),  # 湘潭电化
    (0, '002125'),  # 湘潭电化(上海?)
]
for m, c in test_stocks:
    try:
        data = api.get_security_quotes([(m, c)])
        if data:
            d = data[0]
            print(f'market={m}, {c}: {d["price"]}')
        else:
            print(f'market={m}, {c}: 无数据')
    except Exception as e:
        print(f'market={m}, {c}: 错误 {e}')

api.disconnect()
