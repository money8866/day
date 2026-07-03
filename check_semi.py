"""检查半导体设备相关数据"""
import csv
import os

# 检查半导体板块数据
csv_file = r'D:\mystock\cache_sector\半导体.csv'
if os.path.exists(csv_file):
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f'半导体板块数据: {len(rows)} 条')
    if rows:
        print(f'最新日期: {rows[0].get("trade_date", "")}')
        print(f'字段: {list(rows[0].keys())}')
        print()
        print('最新5条:')
        for row in rows[:5]:
            print(f'  {row.get("trade_date", "")}: {row.get("name", "")} 涨跌幅: {row.get("pct_chg", "")}%')
else:
    print('文件不存在')

# 检查半导体设备ETF数据
print()
print('=' * 50)
print('半导体设备ETF (159516.SZ) 最新持仓')
print('=' * 50)

etf_file = r'D:\mystock\report_daily\etf_pdfs\半导体设备_159516.SZ_top20.pdf'
if os.path.exists(etf_file):
    print(f'ETF文件存在: {etf_file}')
else:
    print(f'ETF文件不存在: {etf_file}')
