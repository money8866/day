# -*- coding: utf-8 -*-
"""提取 Bull 硬性过滤后 103 只股票（代码6, 名称, 市场, 主题, 黄金牛股分）。"""
import pandas as pd
import numpy as np

pd.set_option('display.width', 260)

df = pd.read_csv(r'd:\mystock\solo\report_daily\double_score_20260808_162927.csv', dtype={'代码': str})

num_cols = ['市值(亿)', '营收YoY%', '利润YoY%', 'Q1利润YoY%', 'ROE%', '毛利率%', 'PEG',
            '估值空间%', 'MoatScore', 'RiskScore', 'AdjustedProfitGrowth', 'GrowthScore',
            'QualityScore', 'ProfitQualityFactor', '加速度分', 'PEG_V14', 'ValueBonus',
            'IndustryCycleScore', 'DoubleScore', 'SustainableScore', 'FinalScore']
for c in num_cols:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors='coerce')

df['增强提示'] = df['增强提示'].fillna('')
df['代码6'] = df['代码'].astype(str).str.zfill(6)
df['市场'] = np.where(df['代码6'].str.startswith(('30', '68')), '双创', '主板')

hard = df[
    (df['市值(亿)'] > 80) &
    (~df['增强提示'].str.contains('景气下行')) &
    (df['RiskScore'] < 40) &
    (df['PEG'] < 1.5) &
    (~df['行业景气阶段'].isin(['下行', '衰退'])) &
    (df['ProfitQualityFactor'] >= 0.85)
].copy()

print(f"硬性过滤后: {len(hard)} 只")
hard.to_csv(r'd:\mystock\solo\bull103.csv', index=False,
            columns=['代码6', '名称', '市场', '主题', '市值(亿)', '黄金牛股分' if '黄金牛股分' in hard.columns else 'DoubleScore'])
print("已保存 bull103.csv")
for _, row in hard.iterrows():
    print(row['代码6'], row['名称'], row['市场'])
