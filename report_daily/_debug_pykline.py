# -*- coding: utf-8 -*-
import os, sys, datetime
from pytdx.hq import TdxHq_API

sys.stdout.reconfigure(encoding='utf-8')
api = TdxHq_API(heartbeat=False)

# 测试几个指数
tests = [
    (0, '399300'),  # 沪深300
    (1, '1A0001'),  # 上证
    (0, '399303'),  # 国证2000
]

connected = api.connect('123.125.108.14', 7709)
print(f"连接: {connected}")

for market, code in tests:
    data = api.get_security_bars(4, market, code, 0, 10)
    if data:
        print(f"\nmarket={market} code={code}:")
        print(f"  字段: {list(data[0].keys())}")
        print(f"  第一条: {data[0]}")
        print(f"  最后一条: {data[-1]}")

api.disconnect()
