"""
诊断趋势精准入场信号为何今天(20260630)没有数据
"""
import pandas as pd
from datetime import datetime

# 读取CSV文件
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260630_183736_qualified.csv'
df = pd.read_csv(csv_path)

print('=' * 60)
print('CSV文件诊断报告')
print('=' * 60)
print()

print('1. CSV文件概况:')
print(f'  总信号数: {len(df)}')
print(f'  日期范围: {df["signal_date"].min()} 至 {df["signal_date"].max()}')
print()

print('2. 今日信号检查:')
today = 20260630
today_signals = df[df['signal_date'] == today]
print(f'  今日日期: {today}')
print(f'  今日信号数: {len(today_signals)}')
if len(today_signals) == 0:
    print('  ❌ 今日无信号！')
else:
    print('  ✅ 今日有信号')
print()

print('3. 最近5天信号分布:')
for date in sorted(df['signal_date'].unique(), reverse=True)[:5]:
    count = len(df[df['signal_date'] == date])
    print(f'  {date}: {count}个信号')
print()

print('4. 可能原因分析:')
if len(today_signals) == 0:
    print('  ❌ 今日无信号的可能原因:')
    print('    1. 今日可能不是交易日（周末/节假日）')
    print('    2. 数据尚未更新（Tushare数据延迟）')
    print('    3. 今日无股票满足筛选条件（return_1d>0 + RSI6∈[50,70]）')
    print('    4. 脚本运行错误，未生成今日信号')
else:
    print('  ✅ 今日有信号，问题可能在PDF生成环节')
print()

print('5. 建议操作:')
print('  1. 检查今日是否是交易日')
print('  2. 手动运行趋势信号检测脚本')
print('  3. 检查Tushare数据是否已更新到今日')
print('  4. 检查PDF生成脚本是否正确处理今日数据')
print()

print('=' * 60)
