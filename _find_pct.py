import re, sys
with open('D:/mystock/dragon/realtime_monitor.py', 'r', encoding='utf-8') as f:
    content = f.read()
lines = content.split('\n')
for i, line in enumerate(lines, 1):
    if 'pct_change' in line or 'pct_chg' in line:
        print(f'{i}: {line}')
