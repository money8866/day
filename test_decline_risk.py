# -*- coding: utf-8 -*-
"""
test_decline_risk.py - Test decline risk module with real data
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import block_decline_risk as drc

# =========================================================
# Test 1: Declining sector (simulated)
# =========================================================
print("=" * 50)
print("Test 1: Declining sector")
state = {
    'history': [600, 700, 800, 900, 1000, 1200, 1300, 1250, 1150, 1050],
    'momentum': 0,
    'acc': 0,
}
risk = drc.calc_decline_risk("chip_concept", 1050, state)
print(f"  Level: {risk['level']}")
print(f"  Discount: {risk['discount']}")
print(f"  Signals: {risk['signal_labels']}")
print(f"  Detail: {risk['detail']}")

# =========================================================
# Test 2: Safe rising sector
# =========================================================
print()
print("=" * 50)
print("Test 2: Safe rising sector")
safe_state = {
    'history': [400, 450, 500, 550, 600, 650, 700],
    'momentum': 0,
    'acc': 0,
}
safe_risk = drc.calc_decline_risk("ai_concept", 700, safe_state)
print(f"  Level: {safe_risk['level']}")
print(f"  Discount: {safe_risk['discount']}")
print(f"  Detail: {safe_risk['detail']}")

# =========================================================
# Test 3: Surge-then-crash scenario
# =========================================================
print()
print("=" * 50)
print("Test 3: Surge then crash")
crash_state = {
    'history': [500, 600, 800, 1000, 1200, 1400, 1500, 1580, 1600, 1300],
    'momentum': 0,
    'acc': 0,
}
crash_risk = drc.calc_decline_risk("semiconductor", 1300, crash_state)
print(f"  Level: {crash_risk['level']}")
print(f"  Discount: {crash_risk['discount']}")
print(f"  Signals: {crash_risk['signal_labels']}")
print(f"  Detail: {crash_risk['detail']}")

# =========================================================
# Test 4: Format report
# =========================================================
print()
print("=" * 50)
print("Test 4: Format decline report")
warnings = [
    {'sector': 'chip', 'level': 2, 'level_label': 'danger', 'signals': 'surge_crash,decay_3d'},
    {'sector': 'ai', 'level': 1, 'level_label': 'alert', 'signals': 'decay_2d'},
]
report = drc.format_decline_report(warnings)
print(report)

# =========================================================
# Test 5: Score v2 wrapper
# =========================================================
print()
print("=" * 50)
print("Test 5: Score v2 wrapper")

def mock_calc_score(df):
    return 1200

import pandas as pd
fake_df = pd.DataFrame({
    'pct_chg': [-2.1, 1.5, -3.8, 0.2, -1.5],
    'amount': [10.0, 20.0, 15.0, 8.0, 12.0],
})

score, risk = drc.calc_sector_score_v2(mock_calc_score, fake_df, "test", crash_state, fake_df)
print(f"  Base: 1200, Final: {score}, Level: {risk['level']}")
print(f"  Discount applied: {risk['discount']}")

print()
print("All tests passed!")
