# -*- coding: utf-8 -*-
"""临时探查：cache_daily/parquet 下 daily_basic 缓存的历史覆盖范围（用完即删）"""
import glob, os

base = r'D:\mystock\cache_daily'
# 1) parquet 目录
pf = sorted(glob.glob(os.path.join(base, 'parquet', 'daily_basic_*.parquet')))
print('parquet/daily_basic_*.parquet:', len(pf))
if pf:
    dates = [os.path.basename(x).replace('daily_basic_', '').replace('.parquet', '') for x in pf]
    print('   min:', min(dates), 'max:', max(dates), 'first5:', dates[:5], 'last5:', dates[-5:])
pf2 = sorted(glob.glob(os.path.join(base, 'parquet', 'daily_basic_code_*.parquet')))
print('parquet/daily_basic_code_*:', len(pf2), (os.path.basename(pf2[0]) if pf2 else ''))

# 2) cache_daily 根目录 treasure 前缀
pf3 = sorted(glob.glob(os.path.join(base, 'treasure_daily_basic_*.parquet')))
dates3 = [os.path.basename(x).replace('treasure_daily_basic_', '').replace('.parquet', '') for x in pf3]
print('treasure_daily_basic_*.parquet:', len(pf3), 'min:', min(dates3) if dates3 else '-', 'max:', max(dates3) if dates3 else '-')

# 3) 根目录 daily_basic_{date}.parquet（integrated_hunter/mid_report_hunter 写的）
pf4 = sorted(glob.glob(os.path.join(base, 'daily_basic_20*.parquet')))
dates4 = [os.path.basename(x).replace('daily_basic_', '').replace('.parquet', '') for x in pf4]
print('root daily_basic_20*.parquet:', len(pf4), 'min:', min(dates4) if dates4 else '-', 'max:', max(dates4) if dates4 else '-')

# 4) root turnover_rate csv
pf5 = sorted(glob.glob(os.path.join(base, 'turnover_rate_*.csv')))
dates5 = [os.path.basename(x).replace('turnover_rate_', '').replace('.csv', '') for x in pf5]
print('root turnover_rate_*.csv:', len(pf5), 'min:', min(dates5) if dates5 else '-', 'max:', max(dates5) if dates5 else '-')
