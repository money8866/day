# -*- coding: utf-8 -*-
"""
从合格股池筛选6月26日二波形态信号 - 简化版
先测试前50只股票
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from wave2_pattern_scanner import WavePatternDetector

# 读取合格股池
qualified_pool = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
print('='*80)
print(f'合格股池：{len(qualified_pool)}只')
print('='*80)

# 只测试前50只
test_size = 50
codes = qualified_pool['ts_code'].head(test_size).tolist()

# 转换代码格式
pure_codes = []
for code in codes:
    if isinstance(code, str):
        pure_codes.append(code)
    else:
        pure_codes.append(f"{int(code):06d}")

print(f'\n测试扫描前 {len(pure_codes)} 只股票...')

# 初始化扫描器
scanner = WavePatternDetector()
target_date = '20260626'

all_results = []

for i, ts_code in enumerate(pure_codes, 1):
    print(f'[{i}/{len(pure_codes)}] 扫描 {ts_code}...', end=' ')

    try:
        # 扫描四种形态
        for pattern_type in ['sideways', 'deep', 'volume_pullback', 'vshape']:
            result = None

            if pattern_type == 'sideways':
                result = scanner.detect_sideways_pattern(ts_code, today_only=False, target_date=target_date)
            elif pattern_type == 'deep':
                result = scanner.detect_deep_pullback_pattern(ts_code, today_only=False, target_date=target_date)
            elif pattern_type == 'volume_pullback':
                result = scanner.detect_volume_pullback_pattern(ts_code, today_only=False, target_date=target_date)
            elif pattern_type == 'vshape':
                result = scanner.detect_vshape_pattern(ts_code, today_only=False, target_date=target_date)

            if result and result.get('entry_date') == int(target_date):
                all_results.append(result)
                print(f'✓ {result["pattern"]}', end='')
    except Exception as e:
        print(f'✗ {str(e)[:20]}', end='')

    print()

print(f'\n扫描完成！共发现 {len(all_results)} 个信号')

# 转换为DataFrame
if all_results:
    df_results = pd.DataFrame(all_results)
    df_results = df_results.sort_values('score', ascending=False)

    # 保存结果
    output_path = r'D:\mystock\solo\multi_factor_picker\output\qualified_pool_20260626_signals_test.csv'
    df_results.to_csv(output_path, index=False, encoding='utf-8-sig')

    print(f'\n结果已保存: {output_path}')
    print(f'\nTOP10：')
    for i, (idx, row) in enumerate(df_results.head(10).iterrows(), 1):
        print(f'{i}. {row["name"]}({row["ts_code"]}): {row["pattern"]} {row["score"]:.0f}分')
else:
    print('\n未发现符合条件的信号')
