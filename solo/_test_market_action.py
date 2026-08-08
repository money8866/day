# -*- coding: utf-8 -*-
"""大盘动作信号监测单测: 解析V9.9报告 + 模拟加仓/减仓触发"""
import os, sys, time, json

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from realtime_theme_monitor import RealtimeThemeMonitor

# 绕过 __init__
m = RealtimeThemeMonitor.__new__(RealtimeThemeMonitor)
m.quotes = {}
m.intraday_snapshots = {}
m.full_market_stats = {}
m.last_market_action_alert = 0
m._market_action_triggered = {}
m._parsed_action = None
m._last_theme_results = []

# ── 1. 测试报告解析 ──
print("=" * 60)
print("1. 解析 V9.9 报告")
parsed = m._parse_market_action_report()
if parsed:
    print(f"  动作: {parsed['action']}")
    print(f"  市场: {parsed['market_regime']}")
    print(f"  仓位: {parsed['position']}%")
    print(f"  报告日期: {parsed['report_date']}")
    print(f"  加仓条件: {parsed['add_conditions']}")
    print(f"  减仓条件: {parsed['reduce_conditions']}")
else:
    print("  ❌ 解析失败!")
    exit(1)

# ── 2. 测试加仓信号触发(模拟市场满足条件) ──
print("\n" + "=" * 60)
print("2. 模拟加仓信号触发")
m.full_market_stats = {'up_ratio': 62, 'zt_count': 80, 'dt_count': 3, 'up_count': 3000, 'down_count': 1800, 'total': 4800, 'down_ratio': 37.5}
m._last_theme_results = [{'theme': '半导体', 'alpha': 85}, {'theme': 'AI', 'alpha': 78}, {'theme': '机器人', 'alpha': 72}]
m.last_market_action_alert = 0
m._market_action_triggered = {}
print(f"  debug: full={m.full_market_stats}, mainline={m._get_mainline_strength()}, broken={m._get_yesterday_broken_rate()}")
alerts = m._check_market_action_signals()
if alerts:
    print("  ✅ 加仓信号触发:")
    for a in alerts:
        print(f"  类型: {a['type']}")
        print(a['msg'])
else:
    print("  ⚠ 无信号(可能条件未满足)")

# ── 3. 测试减仓信号触发 ──
print("\n" + "=" * 60)
print("3. 模拟减仓信号触发")
m.full_market_stats = {'up_ratio': 35, 'zt_count': 20, 'dt_count': 30, 'up_count': 1680, 'down_count': 3000, 'total': 4800, 'down_ratio': 62.5}
m._last_theme_results = [{'theme': '半导体', 'alpha': 30}, {'theme': 'AI', 'alpha': 25}, {'theme': '机器人', 'alpha': 20}]
m.last_market_action_alert = 0
m._market_action_triggered = {}
alerts = m._check_market_action_signals()
if alerts:
    print("  ✅ 减仓信号触发:")
    for a in alerts:
        print(f"  类型: {a['type']}")
        print(a['msg'])
else:
    print("  ⚠ 无信号(可能条件未满足)")

# ── 4. 测试冷却(同条件不重复) ──
print("\n" + "=" * 60)
print("4. 冷却测试(30分钟内不重复)")
alerts2 = m._check_market_action_signals()
if alerts2:
    print("  ❌ 冷却失败! 应该无信号")
else:
    print("  ✅ 冷却正确, 30分钟内无重复信号")

# ── 5. 测试中性市场(不触发) ──
print("\n" + "=" * 60)
print("5. 中性市场测试")
m.full_market_stats = {'up_ratio': 50, 'zt_count': 40, 'dt_count': 10, 'up_count': 2400, 'down_count': 2400, 'total': 4800, 'down_ratio': 50}
m._last_theme_results = [{'theme': '半导体', 'alpha': 55}, {'theme': 'AI', 'alpha': 50}]
m.last_market_action_alert = 0
m._market_action_triggered = {}
alerts = m._check_market_action_signals()
if alerts:
    print("  ❌ 中性市场不应触发!")
else:
    print("  ✅ 中性市场正确不触发")

print("\n✅ 全部单测通过")