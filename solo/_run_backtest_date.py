# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.argv = ['etf_mainline_strategy_tushare.py', '--date', '20260610']
os.chdir(r'd:\mystock\solo')
exec(open(r'd:\mystock\solo\etf_mainline_strategy_tushare.py', encoding='utf-8').read())
