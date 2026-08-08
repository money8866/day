# -*- coding: utf-8 -*-
"""黄金牛股(未来5倍潜力)筛选模型"""
import pandas as pd
import numpy as np

pd.set_option('display.width', 260)
pd.set_option('display.max_columns', 30)
pd.set_option('display.unicode.east_asian_width', True)

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

# ── 硬性过滤（5倍牛股必要条件）──
hard = df[
    (df['市值(亿)'] > 80) &                                 # 用户硬性: >80亿
    (~df['增强提示'].str.contains('景气下行')) &             # 行业景气不能下滑
    (df['RiskScore'] < 40) &                                # 无重大风险红旗
    (df['PEG'] < 1.5) &                                     # 成长与估值匹配
    (~df['行业景气阶段'].isin(['下行', '衰退'])) &           # 行业阶段向上
    (df['ProfitQualityFactor'] >= 0.85)                     # 扣非利润占比高(利润真实)
].copy()
print(f'硬性过滤后: {len(hard)} 只 (从861只)')

# ── 综合评分（总分100+双创加成）──
hard['成长质量分'] = (np.minimum(hard['AdjustedProfitGrowth'] / 100, 2) * 50 + hard['GrowthScore']) / 2
hard['盈利质量分'] = hard['QualityScore'] * 0.5 + hard['ProfitQualityFactor'] * 100 * 0.25 + np.minimum(hard['毛利率%'] / 30, 1) * 100 * 0.25
hard['护城河分'] = hard['MoatScore']
hard['估值分'] = np.where(hard['PEG'] < 1, 100, np.where(hard['PEG'] < 1.5, 70, 30)) * 0.7 + np.minimum(hard['估值空间%'] / 50, 1) * 100 * 0.3
hard['风险分'] = np.clip(100 - hard['RiskScore'], 0, 100)
hard['景气分'] = hard['行业景气阶段'].map({'主升': 100, '景气上行': 90, '复苏': 65, '震荡': 40}).fillna(30)
hard['加速度分'] = hard['加速度分']

hard['黄金牛股分'] = (
    hard['成长质量分'] * 0.25 +
    hard['盈利质量分'] * 0.25 +
    hard['护城河分'] * 0.20 +
    hard['估值分'] * 0.15 +
    hard['风险分'] * 0.10 +
    hard['景气分'] * 0.05 +
    np.where(hard['市场'] == '双创', 2, 0)                  # 双创优先
)

hard = hard.sort_values('黄金牛股分', ascending=False)
out_cols = ['代码6', '名称', '市场', '主题', '市值(亿)', '利润YoY%', 'Q1利润YoY%', 'PEG',
            '估值空间%', 'MoatScore', 'RiskScore', '毛利率%', 'ROE%', 'AdjustedProfitGrowth',
            '加速度分', '行业景气阶段', '龙头类型', '黄金牛股分']
print('\n=== 黄金牛股 Top20 (未来5倍潜力) ===')
print(hard.head(20)[out_cols].to_string(index=False, formatters={
    '市值(亿)': '{:.0f}'.format, '利润YoY%': '{:.0f}'.format, 'Q1利润YoY%': '{:.0f}'.format,
    'PEG': '{:.2f}'.format, '估值空间%': '{:.0f}'.format, 'MoatScore': '{:.0f}'.format,
    'RiskScore': '{:.0f}'.format, '毛利率%': '{:.0f}'.format, 'ROE%': '{:.0f}'.format,
    'AdjustedProfitGrowth': '{:.0f}'.format, '加速度分': '{:.0f}'.format, '黄金牛股分': '{:.1f}'.format}))
