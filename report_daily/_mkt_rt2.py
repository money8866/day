# -*- coding: utf-8 -*-
import sys, datetime
from pytdx.hq import TdxHq_API

sys.stdout.reconfigure(encoding='utf-8')
api = TdxHq_API(heartbeat=False, auto_retry=True)

connected = False
for ip, port in [('123.125.108.14', 7709), ('218.108.47.77', 7709)]:
    try:
        if api.connect(ip, int(port)):
            connected = True
            print("Connected: %s:%s" % (ip, port))
            break
    except:
        continue

if not connected:
    print("Connect failed")
    sys.exit(1)

now = datetime.datetime.now()
print("Time: %s\n" % now.strftime('%Y-%m-%d %H:%M:%S'))

# 用 get_security_quotes 查实时快照
# 格式: 市场,代码  (0=深圳,1=上海)
# 上证=1,000001  深证=0,399001  创业板=0,399006  沪深300=0,399300
# 中证2000=1,932000
quotes_fmt = [
    (1, '000001', '上证指数'),
    (0, '399001', '深证成指'),
    (0, '399006', '创业板指'),
    (0, '399300', '沪深300'),
    (1, '932000', '中证2000'),
    (1, '931025', '中证1000'),
]

# pytdx get_security_quotes 格式: [(market, code), ...]
ql = [(m, c) for m, c, _ in quotes_fmt]
print("Query: %d instruments" % len(ql))
data = api.get_security_quotes(ql)
api.disconnect()

# 解析
print("\n=== REAL-TIME QUOTES ===")
results = []
if data:
    for item in data:
        try:
            name = item.get('name', '')
            close = float(item.get('close', 0))
            pct_chg = float(item.get('pct_chg', 0))
            high = float(item.get('high', 0))
            low = float(item.get('low', 0))
            open_p = float(item.get('open', 0))
            vol = float(item.get('vol', 0))
            amount = float(item.get('amount', 0))
            
            if close > 0 and close < 100000 and pct_chg != 0 and not (close < 100 and pct_chg > 100):
                results.append({
                    'name': name, 'close': close, 'pct': pct_chg,
                    'high': high, 'low': low, 'open': open_p,
                    'vol': vol, 'amount': amount
                })
                pct_str = '+%.2f' % pct_chg if pct_chg >= 0 else '%.2f' % pct_chg
                print("%s: %.2f  %s%%  O=%.2f  H=%.2f  L=%.2f  vol=%.0f  amount=%.1f亿" % (
                    name, close, pct_str, open_p, high, low, vol, amount/1e8))
        except Exception as e:
            continue

if not results:
    print("No valid data. Raw sample:")
    for item in data[:3]:
        print("  %s" % item)

print("\nDone")
