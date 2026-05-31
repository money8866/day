import sys
sys.stdout.reconfigure(encoding='utf-8')
from pytdx.hq import TdxHq_API
api = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=False)

ok = api.connect('218.6.170.47', 7709)
print('Connect:', ok)

stocks = [
    (0, '300179'), (0, '002297'), (1, '688368'), (1, '688381'), (0, '301312'),
    (0, '300975'), (1, '688512'), (0, '301021'), (1, '688720'), (0, '605580'),
]
for mkt, code in stocks:
    try:
        data = api.get_security_quotes([(mkt, code)])
        if data and data[0]:
            q = data[0]
            print(f'OK {code} price={q["price"]} pct={(q["price"]-q["last_close"])/q["last_close"]*100:.2f}%')
        else:
            print(f'EMPTY {code}')
    except Exception as e:
        print(f'ERR {code}: {e}')

# Also test bars for first stock
print('\nBars test:')
try:
    bars = api.get_security_bars(1, 0, '300179', 0, 5)
    if bars:
        print('Bars OK:', len(bars), bars[0] if bars else None)
    else:
        print('Bars empty')
except Exception as e:
    print('Bars err:', e)

api.disconnect()
