# -*- coding: utf-8 -*-
"""测试pytdx中证2000代码"""
from pytdx.hq import TdxHq_API

servers = [
    ('218.6.170.47', 7709),
    ('123.125.108.14', 7709),
    ('180.153.18.170', 7709),
    ('180.153.18.172', 80),
    ('202.108.253.139', 80),
]

api = TdxHq_API(heartbeat=False, auto_retry=True)
connected = False
for host, port in servers:
    try:
        if api.connect(host, port, time_out=5):
            connected = True
            print(f"连接成功: {host}:{port}")
            break
    except:
        continue

if not connected:
    print("全部连接失败")
    sys.exit(1)

# 测试代码组合
tests = [
    (1, '932000'),  # 上海 中证2000
    (0, '932000'),  # 深圳 中证2000
    (1, '000001'),  # 上证指数(market=1)
    (1, '000985'),  # 中证全指
    (1, '000852'),  # 中证1000
]

for market, code in tests:
    try:
        q = api.get_security_quotes([(market, code)])
        if q:
            r = q[0]
            print(f"✅ market={market} code={code}: name={r.get('name')} price={r.get('price')} pre_close={r.get('pre_close')}")
        else:
            print(f"❌ market={market} code={code}: 无数据")
    except Exception as e:
        print(f"❌ market={market} code={code}: {e}")

# 再试 get_index_bars 获取历史K线确认代码
print("\n=== get_index_bars 测试 ===")
for market, code in [(1, '932000'), (1, '000001')]:
    try:
        bars = api.get_index_bars(4, market, code, 0, 5)
        if bars:
            print(f"✅ market={market} code={code}: 最近K线 {len(bars)}条, 最新收盘{bars[-1]['close']}")
        else:
            print(f"❌ market={market} code={code}: get_index_bars无数据")
    except Exception as e:
        print(f"❌ market={market} code={code}: {e}")

api.disconnect()
