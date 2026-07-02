"""
检查CSV中是否有股票有多行记录（多个信号日期）
"""
import csv
from collections import Counter

def check_duplicate_stocks(csv_file):
    """检查是否有股票出现多次"""
    print('=' * 70)
    print('检查CSV中是否有股票有多行记录')
    print('=' * 70)
    print()
    
    # 读取CSV
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f'总记录数: {len(rows)}')
    
    # 统计每个股票代码的出现次数
    ts_code_counts = Counter([row['ts_code'] for row in rows])
    
    # 找出出现多次的股票
    duplicates = {code: count for code, count in ts_code_counts.items() if count > 1}
    
    if duplicates:
        print(f'⚠ 发现 {len(duplicates)} 只股票有多个信号记录：')
        print()
        
        for code, count in duplicates.items():
            print(f'{code}: {count} 个信号')
            # 显示该股票的所有记录
            matching_rows = [row for row in rows if row['ts_code'] == code]
            for i, row in enumerate(matching_rows, 1):
                print(f'  {i}. 信号日期: {row.get("launch_date", "")}, B浪评分: {row.get("bwave_score", ""}')
            print()
    else:
        print('✓ 所有股票都只有1个信号记录')
        print()
        
        # 显示所有股票列表
        print('股票列表（按代码排序）:')
        for code in sorted(ts_code_counts.keys()):
            print(f'  {code}')
    
    print()
    print('=' * 70)
    
    return duplicates

if __name__ == '__main__':
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_115514_qualified.csv'
    check_duplicate_stocks(csv_file)
