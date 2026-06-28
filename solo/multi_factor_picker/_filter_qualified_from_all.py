# -*- coding: utf-8 -*-
"""
从之前全市场扫描结果中筛选合格股池的26号信号
"""
import pandas as pd

print('='*80)
print('从全市场扫描结果筛选合格股池信号')
print('='*80)

# 读取全市场扫描结果
all_signals = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_20260628_111801.csv')
print(f'\n全市场扫描结果：{len(all_signals)}只信号')

# 筛选26号信号
signals_26 = all_signals[all_signals['entry_date'] == 20260626]
print(f'26号信号：{len(signals_26)}只')

# 读取合格股池
qualified_pool = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
print(f'\n合格股池：{len(qualified_pool)}只')

# 获取合格股池的代码列表
qualified_codes = set()
for code in qualified_pool['ts_code']:
    if isinstance(code, str):
        # 移除后缀（如果有）
        if '.' in code:
            qualified_codes.add(code)
        else:
            # 添加.SZ或.SH后缀
            if code.startswith('6'):
                qualified_codes.add(f"{code}.SH")
            else:
                qualified_codes.add(f"{code}.SZ")
    else:
        # 数字格式
        code_str = f"{int(code):06d}"
        if code_str.startswith('6'):
            qualified_codes.add(f"{code_str}.SH")
        else:
            qualified_codes.add(f"{code_str}.SZ")

print(f'合格股池代码数：{len(qualified_codes)}')

# 筛选合格股池中的信号
qualified_signals = signals_26[signals_26['ts_code'].isin(qualified_codes)]
print(f'\n合格股池中的26号信号：{len(qualified_signals)}只')

if len(qualified_signals) > 0:
    # 按评分排序
    qualified_signals = qualified_signals.sort_values('score', ascending=False)

    # 保存结果
    output_path = r'D:\mystock\solo\multi_factor_picker\output\qualified_pool_20260626_signals.csv'
    qualified_signals.to_csv(output_path, index=False, encoding='utf-8-sig')

    print(f'\n结果已保存: {output_path}')
    print(f'\n形态分布：')
    print(qualified_signals['pattern'].value_counts())
    print(f'\n评分分布：')
    print(f'  ≥40分：{len(qualified_signals[qualified_signals["score"]>=40])}只')
    print(f'  30-40分：{len(qualified_signals[(qualified_signals["score"]>=30) & (qualified_signals["score"]<40)])}只')
    print(f'  <30分：{len(qualified_signals[qualified_signals["score"]<30])}只')
    print(f'\nTOP10：')
    for i, (idx, row) in enumerate(qualified_signals.head(10).iterrows(), 1):
        print(f'{i}. {row["name"]}({row["ts_code"]}): {row["pattern"]} {row["score"]:.0f}分')
else:
    print('\n合格股池中没有26号的二波形态信号')
