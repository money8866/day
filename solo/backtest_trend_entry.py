"""
趋势精准入场策略回测分析
对比3种方案：
  A. 原版：return_1d>0 + RSI[50,70]
  B. 放宽版：return_1d>0 + RSI[40,75]
  C. 多因子版：return_1d>0 + RSI[40,75] + MACD金叉
"""

import pandas as pd
import numpy as np
from datetime import datetime

# 读取全量扫描结果
INPUT_DIR = r'D:\mystock\solo\trend_feature_output'
OUTPUT_DIR = r'D:\mystock\solo\backtest_output'

def find_latest_csv():
    """找到最新的全量扫描CSV"""
    import os
    files = [f for f in os.listdir(INPUT_DIR) if f.startswith('entry_precision_') and f.endswith('.csv')]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(INPUT_DIR, files[0])

def backtest_strategy(df, name, filters):
    """
    回测单个策略
    filters: dict, e.g. {'return_1d': '>0', 'rsi6': [50,70]}
    """
    df_filtered = df.copy()
    
    # 应用过滤条件
    for key, condition in filters.items():
        if key == 'return_1d':
            df_filtered = df_filtered[df_filtered['return_1d'] > 0]
        elif key == 'rsi6':
            lo, hi = condition
            df_filtered = df_filtered[(df_filtered['rsi6'] >= lo) & (df_filtered['rsi6'] <= hi)]
        elif key == 'macd_golden':
            if condition:
                df_filtered = df_filtered[df_filtered['macd_golden'] == 1]
    
    if len(df_filtered) == 0:
        print(f"\n{name}: 无信号")
        return
    
    # 计算指标
    results = {}
    for w in [1, 5, 10, 20]:
        col = f'return_{w}d'
        if col in df_filtered.columns:
            r = df_filtered[col].dropna()
            wins = r[r > 0]
            results[w] = {
                'count': len(r),
                'mean': r.mean(),
                'win_rate': len(wins) / len(r) * 100 if len(r) > 0 else 0,
                'max_loss': r.min(),
                'max_profit': r.max(),
                'loss_10pct': (r < -10).sum(),
                'loss_15pct': (r < -15).sum(),
            }
    
    # 打印结果
    print(f"\n{'='*60}")
    print(f"{name}")
    print(f"{'='*60}")
    print(f"信号数: {len(df_filtered)}")
    
    for w in [1, 5, 10, 20]:
        if w in results:
            r = results[w]
            print(f"\n  +{w}d:")
            print(f"    信号数: {r['count']}")
            print(f"    平均收益: {r['mean']:.2f}%")
            print(f"    胜率: {r['win_rate']:.1f}%")
            print(f"    最大亏损: {r['max_loss']:.2f}%")
            print(f"    最大盈利: {r['max_profit']:.2f}%")
            print(f"    亏损>10%: {r['loss_10pct']}个")
            print(f"    亏损>15%: {r['loss_15pct']}个")
    
    # 按评分分组
    print(f"\n  按入场评分分组:")
    for bucket, label in [(80, '≥80'), (70, '70~79'), (60, '60~69'), (50, '50~59')]:
        if bucket >= 80:
            s3 = df_filtered[df_filtered['entry_score'] >= 80]
        else:
            s3 = df_filtered[(df_filtered['entry_score'] >= bucket) & (df_filtered['entry_score'] < bucket + 10)]
        if len(s3) == 0:
            continue
        r = s3['return_10d'].dropna()
        wins = r[r > 0]
        print(f"    {label}分: {len(s3)}个  均={r.mean():.2f}%  胜率={len(wins)/len(r)*100:.1f}%")
    
    return df_filtered

def main():
    print('=' * 60)
    print('趋势精准入场策略回测分析')
    print('=' * 60)
    print()
    
    # 1. 读取数据
    csv_path = find_latest_csv()
    if csv_path is None:
        print("错误: 未找到全量扫描CSV文件")
        print("请先运行: python trend_entry_precision.py --pool qualified --recent 90")
        return
    
    print(f"读取文件: {csv_path}")
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    print(f"总信号数: {len(df)}")
    print()
    
    # 2. 定义3种策略
    strategies = [
        {
            'name': 'A. 原版 (return_1d>0 + RSI[50,70])',
            'filters': {
                'return_1d': '>0',
                'rsi6': [50, 70],
            }
        },
        {
            'name': 'B. 放宽版 (return_1d>0 + RSI[40,75])',
            'filters': {
                'return_1d': '>0',
                'rsi6': [40, 75],
            }
        },
        {
            'name': 'C. 多因子版 (return_1d>0 + RSI[40,75] + MACD金叉)',
            'filters': {
                'return_1d': '>0',
                'rsi6': [40, 75],
                'macd_golden': True,
            }
        },
    ]
    
    # 3. 回测每个策略
    results = {}
    for strategy in strategies:
        df_result = backtest_strategy(
            df, 
            strategy['name'], 
            strategy['filters']
        )
        results[strategy['name']] = df_result
    
    # 4. 保存回测结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = os.path.join(OUTPUT_DIR, f'backtest_trend_entry_{timestamp}.xlsx')
    
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        for name, df_result in results.items():
            if df_result is not None and len(df_result) > 0:
                sheet_name = name.split('.')[0][:31]  # Excel限制31字符
                df_result.to_excel(writer, sheet_name=sheet_name, index=False)
    
    print(f"\n回测结果已保存: {output_path}")
    print()
    print('=' * 60)

if __name__ == '__main__':
    main()
