# -*- coding: utf-8 -*-
"""
生成TOP50股票跟踪清单
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from datetime import datetime

print('='*80)
print('生成TOP50股票跟踪清单')
print('='*80)

# 读取合格股池
print('\n[1/3] 读取合格股池...')
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
print(f'合格股池：{len(df)}只')

# 按BullScore排序，取TOP50
print('\n[2/3] 按BullScore排序，提取TOP50...')
top50 = df.head(50)[['ts_code', 'name', 'final_score']].copy()
top50['rank'] = range(1, len(top50) + 1)

print(f'TOP50已提取')

# 保存TOP50清单（CSV格式）
print('\n[3/3] 保存跟踪模板...')

# 1. 保存股票清单
output_csv = r'D:\mystock\solo\multi_factor_picker\output\top50_tracking_list.csv'
top50.to_csv(output_csv, index=False, encoding='utf-8-sig')
print(f'清单已保存: {output_csv}')

# 2. 创建跟踪模板（含示例数据）
template_data = []
for i, (idx, row) in enumerate(top50.iterrows(), 1):
    template_data.append({
        'rank': row['rank'],
        'ts_code': row['ts_code'],
        'name': row['name'],
        'final_score': row['final_score'],
        'info_type': '',  # 待填入：新订单/新产品/新项目/技术突破
        'title': '',      # 待填入：公告标题
        'ann_date': '',   # 待填入：公告日期
        'amount': '',     # 待填入：金额（如果适用）
        'score': '',      # 待填入：评分（或留空让脚本自动计算）
        'notes': '',      # 待填入：备注
    })

template_df = pd.DataFrame(template_data)
template_path = r'D:\mystock\solo\multi_factor_picker\output\fundamental_info_manual.csv'
template_df.to_csv(template_path, index=False, encoding='utf-8-sig')
print(f'跟踪模板已保存: {template_path}')

# 打印TOP10
print('\n' + '='*80)
print('TOP10 股票（重点跟踪）')
print('='*80)
print(f"{'排名':<6} {'代码':<12} {'名称':<20} {'BullScore':<12}")
print('-'*80)

for i, (idx, row) in enumerate(top50.head(10).iterrows(), 1):
    print(f"{i:<6} {row['ts_code']:<12} {row['name']:<20} {row['final_score']:<12.1f}")

print('='*80)

# 按行业分类统计
print('\n行业分布统计：')
print('-'*80)

# 从原始数据中提取行业信息
df_with_industry = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\_qualified_for_report.csv')
top50_codes = set(top50['ts_code'].tolist())

# 如果有行业字段，统计行业分布
if 'industry' in df_with_industry.columns:
    top50_with_industry = df_with_industry[df_with_industry['ts_code'].isin(top50_codes)]
    industry_counts = top50_with_industry['industry'].value_counts()
    
    for industry, count in industry_counts.head(10).items():
        print(f'  {industry}: {count}只')
else:
    print('  （原始数据无行业字段，无法统计）')

print('\n' + '='*80)
print('使用说明：')
print('='*80)
print('1. 每天打开巨潮网（cninfo.com.cn）')
print('2. 按顺序检查TOP50股票的公告')
print('3. 发现重要信息（新订单/新产品/新项目）时，填入CSV：')
print(f'   {template_path}')
print('4. 每天晚上运行评分脚本：')
print('   cd D:\\mystock\\solo\\multi_factor_picker')
print('   python fundamental_info_scorer.py')
print('5. 查看生成的PDF报告')
print('='*80)

# 生成快速查看HTML
html_path = r'D:\mystock\solo\multi_factor_picker\output\top50_tracking_list.html'
html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>TOP50股票跟踪清单</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <h1>TOP50股票跟踪清单</h1>
    <p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>总数量：{len(top50)}只</p>
    <table>
        <tr>
            <th>排名</th>
            <th>代码</th>
            <th>名称</th>
            <th>BullScore</th>
            <th>巨潮网链接</th>
        </tr>
"""

for i, (idx, row) in enumerate(top50.iterrows(), 1):
    ts_code = str(row['ts_code'])  # 转换为字符串
    # 生成巨潮网链接
    stock_code = ts_code.replace('.SZ', '').replace('.SH', '')
    juchao_url = f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={stock_code}"
    
    html_content += f"""
        <tr>
            <td>{i}</td>
            <td>{ts_code}</td>
            <td>{row['name']}</td>
            <td>{row['final_score']:.1f}</td>
            <td><a href="{juchao_url}" target="_blank">查看公告</a></td>
        </tr>
    """

html_content += """
    </table>
</body>
</html>
"""

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f'\nHTML快速查看已生成: {html_path}')
print('（用浏览器打开，点击"查看公告"快速访问巨潮网）')
