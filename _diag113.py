# -*- coding: utf-8 -*-
data = open(r'C:\Users\kongx\mystock\zhongjun_backtest_v3_local_halfyear.py','rb').read()
lines = data.split(b'\n')
# Show full line 113 in hex
l113 = lines[112]
print('L113 hex:', l113.hex())
print('L113 len:', len(l113))
# Count single quotes
sc = l113.count(b"'")
print('Single quote count:', sc)
# Find position of each single quote
for i, b in enumerate(l113):
    if b == 39:  # 0x27 = single quote
        print(f'  quote at byte {i}: ...{l113[max(0,i-5):i+5].hex()}...')
