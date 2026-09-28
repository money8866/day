# -*- coding: utf-8 -*-
"""临时复验器：固定 TRADE_DATE=20260924 跑 build_theme_stock_map_v2.py --dry-run。

用途：与上一轮同口径 dry-run 做前后对比（环节清单门槛精修前 vs 后）。
不改任何生产文件、不写映射产物（--dry-run）。
"""
import os
import runpy
import sys

sys.argv = ['build_theme_stock_map_v2.py', '--dry-run']

import tushare_quant as tq  # noqa: E402

tq.TRADE_DATE = '20260924'

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
runpy.run_path(os.path.join(_HERE, 'build_theme_stock_map_v2.py'), run_name='__main__')
