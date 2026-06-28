# -*- coding: utf-8 -*-
"""
从合格股池筛选6月26日二波形态信号
四种形态：强势横盘、深度回调、放量回调、V型急跌
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

# 提取股票代码
codes = qualified_pool['ts_code'].tolist()

# 转换代码格式（去掉后缀.SZ/.SH，转为纯代码）
pure_codes = []
for code in codes:
    if isinstance(code, str):
        # 已经是字符串格式，如"603256"或"603256.SH"
        if '.' in code:
            pure_codes.append(code)  # 保留原格式
        else:
            # 纯数字，需要添加后缀
            pure_codes.append(code)
    else:
        # 数字格式
        pure_codes.append(f"{int(code):06d}")

print(f'\n开始扫描 {len(pure_codes)} 只合格股票...')

# 初始化扫描器
scanner = WavePatternDetector()

# 扫描所有四种形态
all_results = []
target_date = '20260626'

for i, ts_code in enumerate(pure_codes, 1):
    if i % 50 == 0:
        print(f'进度: {i}/{len(pure_codes)} ({i/len(pure_codes)*100:.1f}%)')

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

    except Exception as e:
        continue

print(f'\n扫描完成！')
print(f'共发现 {len(all_results)} 个6月26日信号')

# 转换为DataFrame
if all_results:
    df_results = pd.DataFrame(all_results)
    df_results = df_results.sort_values('score', ascending=False)

    # 保存结果
    output_path = r'D:\mystock\solo\multi_factor_picker\output\qualified_pool_20260626_signals.csv'
    df_results.to_csv(output_path, index=False, encoding='utf-8-sig')

    print(f'\n结果已保存: {output_path}')
    print(f'\n形态分布：')
    print(df_results['pattern'].value_counts())
    print(f'\n评分分布：')
    print(f'  ≥40分：{len(df_results[df_results["score"]>=40])}只')
    print(f'  30-40分：{len(df_results[(df_results["score"]>=30) & (df_results["score"]<40)])}只')
    print(f'  <30分：{len(df_results[df_results["score"]<30])}只')
    print(f'\nTOP10：')
    for i, (idx, row) in enumerate(df_results.head(10).iterrows(), 1):
        print(f'{i}. {row["name"]}({row["ts_code"]}): {row["pattern"]} {row["score"]:.0f}分')
else:
    print('\n未发现符合条件的信号')
