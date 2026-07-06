"""临时调试脚本：检查ETF数据连通性"""
import os, sys
import pandas as pd
from mainline_engine.data.source import create_from_config

ds = create_from_config()
data = ds.get_etf_data(['510050.SH', '512880.SH', '512660.SH'], '20250706', '20260706')
for code, df in data.items():
    t = type(df).__name__
    s = str(df.shape) if hasattr(df, 'shape') else 'N/A'
    cols = str(list(df.columns)) if hasattr(df, 'columns') else 'N/A'
    print(f'{code}: type={t}, shape={s}, cols={cols}')
    if hasattr(df, 'shape') and df.shape[0] > 0:
        print(f'  date: {df.trade_date.iloc[0]} -> {df.trade_date.iloc[-1]}, {df.shape[0]} rows')
