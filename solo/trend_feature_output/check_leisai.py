"""
检查雷赛智能在CSV中是否有多个信号记录
"""
import csv

def check_stock_records(csv_file, target_code='002979.SZ'):
    """检查指定股票在CSV中的记录数"""
    print('=' * 70)
    print(f'查找股票: {target_code} (雷赛智能)')
    print('=' * 70)
    print()
    
    # 读取CSV
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # 查找匹配记录
    matching_rows = [row for row in rows if row['ts_code'] == target_code]
    
    print(f'找到 {len(matching_rows)} 行记录')
    print()
    
    if matching_rows:
        for i, row in enumerate(matching_rows, 1):
            print(f'--- 第 {i} 行 ---')
            print(f'信号日期: {row.get("launch_date", "")}')
            print(f'B浪评分: {row.get("bwave_score", "")}')
            print(f'信号类型: {row.get("signal_type", "")}')
            print(f'A浪涨幅: {row.get("a_gain", "")}%')
            print(f'B浪回调: {row.get("b_drop", "")}%')
            print(f'未来1日收益: {row.get("return_1d", "")}%')
            print(f'信号标签: {row.get("signal_tags", "")}')
            print()
        
        if len(matching_rows) > 1:
            print(f'⚠ 该股票有 {len(matching_rows)} 个信号记录（不同信号日期）')
        else:
            print('ℹ 该股票只有 1 个信号记录')
    else:
        print('未找到该股票记录')
    
    print()
    print('=' * 70)
    
    return matching_rows

if __name__ == '__main__':
    csv_file = r'D:\mystock\solo\trend_feature_output\bwave_20260702_115514_qualified.csv'
    check_stock_records(csv_file)
