"""
测试：检查为什么提取不到个股
"""
import os
import json
from datetime import datetime, timedelta

NEWS_CACHE_DIR = r'D:\mystock\news_cache'

# 获取所有日志文件
files = [f for f in os.listdir(NEWS_CACHE_DIR) if f.startswith('ai_analysis_') and f.endswith('.json')]
print(f'Total files: {len(files)}')

# 检查日期
today = datetime.now()
print(f'Today: {today.strftime("%Y-%m-%d")}')
print(f'Today (YYYYMMDD): {today.strftime("%Y%m%d")}')

cutoff = today - timedelta(days=7)
print(f'Cutoff date (7 days ago): {cutoff.strftime("%Y-%m-%d")}')
print(f'Cutoff (YYYYMMDD): {cutoff.strftime("%Y%m%d")}')

# 测试日期比较
test_dates = ['20260609', '20260622', '20260630', '20260701']
for d in test_dates:
    is_recent = d >= cutoff.strftime('%Y%m%d')
    print(f'  {d} >= {cutoff.strftime("%Y%m%d")}: {is_recent}')

print()
print('Testing extraction...')

stocks = []
for filename in files[:5]:  # 只测试前5个
    try:
        date_str = filename.split('_')[-1].replace('.json', '')
        print(f'{filename} -> date: {date_str}')
        
        if date_str >= cutoff.strftime('%Y%m%d'):
            print(f'  -> Recent!')
            filepath = os.path.join(NEWS_CACHE_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            code = data.get('code', '')
            name = data.get('name', '')
            print(f'  -> {code} ({name})')
            stocks.append({'ts_code': code, 'name': name})
    except Exception as e:
        print(f'  -> Error: {e}')

print()
print(f'Extracted {len(stocks)} stocks')
