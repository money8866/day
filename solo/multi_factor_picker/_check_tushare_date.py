# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock')
from tushare_quant import get_last_trade_date
last = get_last_trade_date()
print(f'最近交易日: {last}')
