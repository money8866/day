import re, sys
with open('D:/mystock/dragon/realtime_monitor.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    if 'def fetch_realtime_snapshot' in line or 'def init_price_cache' in line or 'def fetch_latest_bars' in line:
        print(f'{i}: {line}', end='')
