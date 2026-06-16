"""快速诊断: 检查Tushare接口返回的真实字段名和数据结构"""
import tushare as ts
import pandas as pd
import sys, time
from pathlib import Path

# 读取token
with open(Path(r'D:\mystock\config\.env'), 'r', encoding='utf-8') as f:
    token = None
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('TUSHARE_TOKEN='):
            token = line.split('=', 1)[1].strip().strip('"\'')
            break

pro = ts.pro_api(token)
print('Token OK')

for test in ['600519.SH', '000001.SZ']:
    print('\n' + '=' * 70)
    print(f'测试股票: {test}')
    print('=' * 70)

    # income
    print('\n[income 最新2期]')
    try:
        df = pro.income(ts_code=test, start_date='20230101', end_date='20251231')
        df = df.sort_values('end_date', ascending=False).head(3)
        for col in df.columns:
            print(f'  {col}: {df.iloc[0][col] if len(df) > 0 else "N/A"}')
    except Exception as e:
        print(f'  ERROR: {e}')
    time.sleep(0.2)

    # balancesheet
    print('\n[balancesheet 最新2期]')
    try:
        bs = pro.balancesheet(ts_code=test, start_date='20240101', end_date='20251231')
        bs = bs.sort_values('end_date', ascending=False).head(3)
        print(f'  行数: {len(bs)}')
        # 只打印关键字段
        key_cols = [c for c in bs.columns if c in ['end_date', 'total_assets', 'total_owner_equity', 'inventory', 'fixed_assets', 'total_current_assets', 'total_liability']]
        for col in key_cols:
            print(f'  {col}: {bs.iloc[0][col] if len(bs) > 0 else "N/A"}')
        print(f'  所有字段: {list(bs.columns)}')
    except Exception as e:
        print(f'  ERROR: {e}')
    time.sleep(0.2)

    # forecast
    print('\n[forecast 最新]')
    try:
        fc = pro.forecast(ts_code=test, start_date='20240101', end_date='20251231')
        print(f'  行数: {len(fc)}, 字段: {list(fc.columns) if len(fc) > 0 else "无"}')
        if len(fc) > 0:
            latest = fc.sort_values('ann_date', ascending=False).iloc[0]
            print(f'  最新: {latest.to_dict()}')
    except Exception as e:
        print(f'  ERROR: {e}')
    time.sleep(0.2)

    # moneyflow
    print('\n[moneyflow 最新5天]')
    try:
        mf = pro.moneyflow(ts_code=test, start_date='20250601', end_date='20250616')
        print(f'  行数: {len(mf)}, 字段: {list(mf.columns) if len(mf) > 0 else "无"}')
        if len(mf) > 0:
            mf = mf.sort_values('trade_date', ascending=False).head(5)
            print(f'  最新5天:')
            for _, row in mf.iterrows():
                vals = {c: row[c] for c in mf.columns if c not in ['ts_code']}
                print(f'    {vals}')
    except Exception as e:
        print(f'  ERROR: {e}')
    time.sleep(0.2)

print('\n=== 诊断完成 ===')
