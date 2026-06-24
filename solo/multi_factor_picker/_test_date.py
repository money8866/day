# -*- coding: utf-8 -*-
"""验证数据日期逻辑"""
import datetime

# 模拟all_results
all_results = [
    {'entry_date': '20260623', 'score': 32},
    {'entry_date': '20260622', 'score': 26},
    {'entry_date': '20260623', 'score': 25},
]

scan_date = datetime.date.today().strftime('%Y%m%d')
if all_results:
    data_dates = [r.get('entry_date', '') for r in all_results if r.get('entry_date')]
    data_date = max(data_dates) if data_dates else scan_date
else:
    data_date = scan_date

print(f'scan_date={scan_date}')
print(f'data_date={data_date}')
print(f'today_str(报告显示)={data_date}')
print(f'PDF文件名用={scan_date}')
