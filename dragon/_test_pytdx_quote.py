# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"C:\Users\kongx\mystock\dragon")
os.chdir(r"C:\Users\kongx\mystock\dragon")

from pytdx.hq import TdxHq_API

api = TdxHq_API()
api.connect('218.6.170.47', 7709)

# 测试所有可能的市场参数
print("=== 市场参数测试 ===")
for mkt in range(16):
    r = api.get_security_quotes([(mkt, '000001')])
    if r:
        print(f"mkt={mkt}: price={r[0]['price']} vol={r[0]['vol']} name={r[0].get('name','?')}")

# 测试ETF
print("\n=== ETF测试 ===")
etfs = [('159516',0), ('588000',0), ('512480',0), ('512760',0)]
for code, mkt in etfs:
    r = api.get_security_quotes([(mkt, code)])
    if r:
        q = r[0]
        lc = q.get('last_close', 0)
        pct = (q['price'] - lc) / lc * 100 if lc > 0 else 0
        print(f"{code}: price={q['price']} pct={pct:+.2f}%")
    else:
        print(f"{code}: None")

# 日线测试
print("\n=== 日线K线测试 ===")
bars = api.get_security_bars(4, 0, '159516', 0, 10)
if bars:
    for b in bars[:3]:
        print(f"  {b['datetime']}: close={b['close']} vol={b['vol']}")
else:
    print("159516: 无数据")
    # 试其他market
    for mkt in [1, 47]:
        bars = api.get_security_bars(4, mkt, '159516', 0, 5)
        if bars:
            print(f"  mkt={mkt}: 有数据 {bars[0]['close']}")
            break

# 查688256
bars2 = api.get_security_bars(4, 0, '688256', 0, 5)
if bars2:
    print(f"\n688256 日线: close={bars2[0]['close']}")
else:
    print("\n688256: 无日线数据")

# 查科威尔
bars3 = api.get_security_bars(4, 0, '688218', 0, 5)
if bars3:
    print(f"688218: close={bars3[0]['close']}")
else:
    print("688218: 无数据")

api.disconnect()
print("\ndone")