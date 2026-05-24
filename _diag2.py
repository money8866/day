# -*- coding: utf-8 -*-
data = open(r'C:\Users\kongx\mystock\zhongjun_backtest_v3_local_halfyear.py', 'rb').read()
lines = data.split(b'\n')
# Print line 112 and 113 exactly as bytes
print('LINE 112:')
print(lines[111])
print('HEX 112:', lines[111].hex())
print()
print('LINE 113:')
print(lines[112])
print('HEX 113:', lines[112].hex())
print()
print('LINE 114:')
print(lines[113])
print('HEX 114:', lines[113].hex())
print()
# Count quotes in each line
for i in [111, 112, 113]:
    q = lines[i].count(b"'")
    print(f'Line {i+1} quote count: {q}')
