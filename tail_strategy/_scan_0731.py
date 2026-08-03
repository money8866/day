#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按 daily_cache 统一缓存指南扫描指定日期尾盘信号 (默认20260731)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from tail_strategy.daily_scan import scan_date, print_signals

trade_date = sys.argv[1] if len(sys.argv) > 1 else '20260731'
signals = scan_date(trade_date, min_score=50)

print(f'\n新引擎 {trade_date} 信号: {len(signals)}只')
print_signals(signals, 40)

from collections import Counter
lv = Counter(s.signal for s in signals)
print(f'\n分级: {dict(lv)}')
