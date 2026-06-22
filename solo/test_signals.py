#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试突破信号和二波信号"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro, TRADE_DATE, detect_breakout, detect_wave2_reversal

test_stocks = ['002747.SZ', '000970.SZ', '002167.SZ', '300308.SZ']

print(f'当前交易日: {TRADE_DATE}')
print('测试股票的突破/二波信号:')
for code in test_stocks:
    breakout = detect_breakout(code, pro)
    wave2 = detect_wave2_reversal(code, pro)
    print(f'{code}: 突破={breakout["breakout_score"]}分, 二波={wave2["wave2_score"]}分')
    print(f'  突破信号: {breakout["signal"]}')
    print(f'  二波信号: {wave2["signal"]}')
