import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/mystock/dragon')
os.chdir('D:/mystock/dragon')

# Patch RealtimeMonitor to add debug output
from realtime_monitor import RealtimeMonitor, TdxServerManager

# Test server manager
sm = TdxServerManager()
api = sm.get_api()
print('API:', api)
if api:
    # Test fetch for first stock
    data = api.get_security_quotes([(0, '300179')])
    print('Direct test 300179:', data[0]['price'] if data and data[0] else 'EMPTY')

# Now test full monitor
mon = RealtimeMonitor()
print('Watch stocks:', len(mon.watch_stocks))
if not mon.watch_stocks:
    mon.load_watch_pool()
print('After load:', len(mon.watch_stocks))
for s in mon.watch_stocks:
    print(f'  {s["code"]} {s["name"]} mkt={s["market"]}')

print('\nInit cache...')
mon.init_price_cache()
print(f'Cache: {len(mon.price_cache)} stocks')

print('\nTick fetch...')
quotes = mon.fetch_realtime_snapshot()
print(f'Quotes: {len(quotes)} returned')
for code, q in quotes.items():
    print(f'  {code}: price={q["price"]}')
