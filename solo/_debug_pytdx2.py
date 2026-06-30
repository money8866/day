# -*- coding: utf-8 -*-
from pytdx.hq import TdxHq_API
from pytdx.config.hosts import hq_hosts

api = TdxHq_API()
# 连接
for name, ip, port in hq_hosts:
    try:
        api.connect(ip, port)
        test = api.get_security_bars(1, 0, '000001', 0, 1)
        if test is not None:
            print(f'已连接：{ip}:{port}')
            break
    except:
        continue

# 测试单只
print('测试获取 600027 实时行情...')
data = api.get_security_quotes([(0, '600027')])
print('返回:', data)
print('类型:', type(data))
if data:
    print('第一条:', data[0])
    print('字段:', list(data[0].keys()) if isinstance(data[0], dict) else 'not dict')

# 测试科创/创业板
print()
print('测试获取 688170 实时行情...')
data2 = api.get_security_quotes([(1, '688170')])
print('返回:', data2)
if data2:
    print('第一条:', data2[0])

print()
print('测试获取 002709 实时行情...')
data3 = api.get_security_quotes([(1, '002709')])
print('返回:', data3)
if data3:
    print('第一条:', data3[0])

api.disconnect()
print('完成。')
