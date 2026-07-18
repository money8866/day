"""检查dc_df数据结构"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import pandas as pd

dc_df = theme_ts.get_dc_members()
print(f'dc_df shape: {dc_df.shape}')
print(f'dc_df columns: {list(dc_df.columns)}')
print(f'\ndc_df dtypes:\n{dc_df.dtypes}')
print(f'\n前5行:')
print(dc_df.head())
print(f'\nis_industry值分布: {dc_df["is_industry"].value_counts().to_dict()}')

# 检查平安银行是否在dc_df中
pingan = dc_df[dc_df['ts_code'] == '000001.SZ']
print(f'\n平安银行在dc_df中: {len(pingan)}行')
if len(pingan) > 0:
    print(pingan)

# 检查所有东财行业名
ind_df = dc_df[dc_df['is_industry'] == True]
print(f'\n东财行业板块总数: {len(ind_df)}')
print(f'东财行业概念数: {ind_df["concept_name"].nunique()}')

# 抽样查看银行板块成员
bank_members = dc_df[(dc_df['concept_name'] == '银行') | (dc_df['concept_name'] == '银行Ⅱ')]
print(f'\n银行板块成员数: {len(bank_members)}')
print(bank_members.head(10))
