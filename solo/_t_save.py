# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r'd:\mystock\solo')
from datetime import datetime
from realtime_theme_monitor import RealtimeThemeMonitor

# 动态实例化绕过 __init__
m = RealtimeThemeMonitor.__new__(RealtimeThemeMonitor)
m.tail_tracker_db = os.path.join(r'd:\mystock', 'cache_daily', 'tail_signal_tracker.db')
m._get_last_trade_date = lambda: '20260807'

# 测试1: 空信号调用(模拟8/7 0信号场景)
try:
    m._save_tail_signals_to_tracker_v3([])
    print('空信号保存: OK')
except Exception as e:
    import traceback; traceback.print_exc()
    print('空信号保存崩溃:', e)

# 测试2: 构造一个假信号保存
try:
    sig = {
        'signal_date': '20260807', 'ts_code': '000001.SZ', 'name': '测试',
        'theme': '测试', 'signal': '关注', 'total_score': 70,
        'theme_score': 20, 'capital_score': 15, 'role_score': 10,
        'technical_score': 10, 'timing_score': 5, 'room_score': 5,
        'risk_penalty': 0, 'role': 'core', 'buy_type': 'CORE_PULLBACK',
        'confidence': 70, 'next_day_expectation': 'test', 'pct_chg': 1.0,
        'price': 10.0, 'detail': {'a': 1},
    }
    m._save_tail_signals_to_tracker_v3([sig])
    print('假信号保存: OK')
except Exception as e:
    import traceback; traceback.print_exc()
    print('假信号保存崩溃:', e)
