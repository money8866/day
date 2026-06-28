# -*- coding: utf-8 -*-
"""
从合格股池筛选6月26日二波形态信号 - 优化版
使用批量扫描提高效率
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from wave2_pattern_scanner import WavePatternDetector
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 读取合格股池
qualified_pool = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
print('='*80)
print(f'合格股池：{len(qualified_pool)}只')
print('='*80)

# 提取股票代码
codes = qualified_pool['ts_code'].tolist()

# 转换代码格式
pure_codes = []
for code in codes:
    if isinstance(code, str):
        pure_codes.append(code)
    else:
        pure_codes.append(f"{int(code):06d}")

print(f'\n准备扫描 {len(pure_codes)} 只股票...')

# 初始化扫描器
scanner = WavePatternDetector()
target_date = '20260626'

# 线程安全的计数器
counter = {'count': 0, 'lock': threading.Lock()}

def scan_stock(ts_code):
    """扫描单只股票的四种形态"""
    results = []
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
                results.append(result)

    except Exception as e:
        pass

    # 更新计数器
    with counter['lock']:
        counter['count'] += 1
        if counter['count'] % 50 == 0:
            print(f'进度: {counter["count"]}/{len(pure_codes)} ({counter["count"]/len(pure_codes)*100:.1f}%)')

    return results

# 使用线程池并行扫描
all_results = []
print(f'\n开始并行扫描...')

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {executor.submit(scan_stock, code): code for code in pure_codes}

    for future in as_completed(futures):
        results = future.result()
        if results:
            all_results.extend(results)

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
