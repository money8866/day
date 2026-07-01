# -*- coding: utf-8 -*-
"""
计算ETF成份股与合格股池的重叠度
"""
import pandas as pd

# 读取合格股池
bull_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
print('=== 合格股池信息 ===')
print(f'总股票数: {len(bull_df)}')
print(f'列名: {bull_df.columns.tolist()[:10]}')
print()

# 读取ETF成份股
etf_df = pd.read_csv(r'D:\mystock\report_daily\etf_constituents_20260630.csv')
print('=== ETF成份股信息 ===')
print(f'总记录数: {len(etf_df)}')
print(f'去重股票数: {etf_df["con_code"].nunique()}')
print(f'列名: {etf_df.columns.tolist()}')
print()

# 确定bull_df中的股票代码列名
code_col = None
for col in ['ts_code', 'code', '股票代码', 'con_code']:
    if col in bull_df.columns:
        code_col = col
        break

def to_ts_code(code):
    code = str(code).zfill(6)
    if code.startswith('6') or code.startswith('5') or code.startswith('9'):
        return code + '.SH'
    elif code.startswith('0') or code.startswith('3') or code.startswith('1') or code.startswith('2'):
        return code + '.SZ'
    elif code.startswith('4') or code.startswith('8'):
        return code + '.BJ'
    else:
        return code + '.SH'

if code_col:
    bull_codes = set(to_ts_code(c) for c in bull_df[code_col].dropna().unique())
    etf_codes = set(etf_df['con_code'].dropna().unique())
    
    overlap = bull_codes & etf_codes
    overlap_count = len(overlap)
    
    print('=== 重叠度分析 ===')
    print(f'合格股池股票数: {len(bull_codes)}')
    print(f'ETF成份股去重数: {len(etf_codes)}')
    print(f'重叠股票数: {overlap_count}')
    print(f'重叠占合格股池比例: {overlap_count/len(bull_codes)*100:.2f}%')
    print(f'重叠占ETF成份股比例: {overlap_count/len(etf_codes)*100:.2f}%')
    print()
    
    # 按ETF统计各ETF中有多少只在合格股池中
    print('=== 各ETF重叠明细（按重叠比例排序）===')
    etf_overlap = etf_df[etf_df['con_code'].isin(bull_codes)]
    etf_group = etf_overlap.groupby(['etf_name', 'etf_code']).agg(
        overlap_count=('con_code', 'count'),
        overlap_stocks=('con_name', lambda x: ', '.join(x[:5]) + ('...' if len(x)>5 else ''))
    ).reset_index()
    
    # 计算每个ETF的总成份股数
    etf_total = etf_df.groupby('etf_name').size().reset_index(name='total_count')
    etf_group = etf_group.merge(etf_total, on='etf_name')
    etf_group['overlap_ratio'] = (etf_group['overlap_count'] / etf_group['total_count'] * 100).round(2)
    etf_group = etf_group.sort_values('overlap_ratio', ascending=False)
    
    for _, row in etf_group.iterrows():
        print(f"  {row['etf_name']}({row['etf_code']}): {row['overlap_count']}/{row['total_count']} ({row['overlap_ratio']:.1f}%)  -  {row['overlap_stocks']}")
else:
    print('未找到股票代码列，请检查bull_stocks_qualified.csv的列名')
    print(bull_df.head())
