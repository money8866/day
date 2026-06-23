import pandas as pd
import os, sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

# 读取最新的 bullscore CSV
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\bullscore_20260622_142716.csv')
print(f'行数: {len(df)}, 列数: {len(df.columns)}')
print('列名:', df.columns.tolist())

print('\nTOP10 原始评分:')
for i, row in df.head(10).iterrows():
    print(f"  {i+1}. {row.get('name','')} final={row.get('final_score','')} theme_score={row.get('theme_score','')}")

# 统计 theme_score 分布
ts = pd.to_numeric(df['theme_score'], errors='coerce')
print(f'\ntheme_score 统计: 非0={ (ts > 0).sum() }, =0={ (ts == 0).sum() }, NaN={ts.isna().sum() }')
print(f'  max={ts.max()}, mean={ts.mean():.1f}')
