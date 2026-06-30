"""
调试：测试 _check_entry_precision() 函数是否正常工作
"""
import sys
import os

sys.path.insert(0, r'D:\mystock\solo')

from trend_entry_precision import get_data, _check_entry_precision

# 测试一只已知有信号的股票（600460.SH，20260616有信号）
ts_code = '600460.SH'
print(f'调试 {ts_code}...')
print()

# 读取数据
df = get_data(ts_code)
if df is None or len(df) < 90:
    print(f'❌ 数据不足: {ts_code}')
    exit()

print(f'✅ 数据加载成功: {len(df)}天')
print(f'   日期范围: {df[\"trade_date\"].min()} 至 {df[\"trade_date\"].max()}')
print()

# 测试最后20天
print('测试最后20天:')
signals_found = 0
for idx in range(max(0, len(df) - 20), len(df)):
    row = df.iloc[idx]
    sig = _check_entry_precision(df, idx)
    if sig is not None:
        signals_found += 1
        print(f'  ✅ 发现信号: {sig[\"signal_date\"]} 评分={sig[\"entry_score\"]}')
    
    # 每10天打印进度
    if (len(df) - idx) % 10 == 0:
        print(f'  检查中... 剩余{len(df) - idx}天')

print()
print(f'共发现 {signals_found} 个信号')
print()

if signals_found == 0:
    print('❌ 未发现任何信号！可能原因:')
    print('  1. _check_entry_precision() 逻辑有bug')
    print('  2. 数据不满足条件')
    print('  3. 过滤条件太严格')
    print()
    print('建议: 检查 _check_entry_precision() 函数逻辑')
else:
    print('✅ 函数正常工作')
