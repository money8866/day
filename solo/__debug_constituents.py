"""调试：检查ETF成份股"""
import os, sys
from mainline_engine.data.source import create_from_config

ds = create_from_config()

# 检查几只ETF的成份股
for code in ['588000.SH', '512760.SH', '159922.SZ', '159915.SZ']:
    cons = ds.get_etf_constituents(code)
    print(f'{code}: type={type(cons).__name__}, len={len(cons) if cons else 0}')
    if cons and len(cons) > 0:
        print(f'  first 5: {cons[:5]}')
    print()
