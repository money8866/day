# -*- coding: utf-8 -*-
"""Simple test of engine's _scan_for_impulse."""
import traceback
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich
from rib.engine import RIBEngine

df = enrich(create_rib_pattern())
engine = RIBEngine()
end_idx = len(df) - 1

print(f"Data: {len(df)} rows, end_idx={end_idx}")

try:
    result = engine._scan_for_impulse(df, end_idx)
    print(f"Result: {'Found' if result else 'None'}")
    if result:
        print(f"  ret={result.impulse_return*100:.1f}%, days={result.impulse_days}")
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
