import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/double_score_20260721_074911.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('列名:', list(df.columns))
print()
print(df.head(5).to_string())
print()
print('评分范围:', df['最终分'].min() if '最终分' in df.columns else 'N/A', '-', df['最终分'].max())
print('等级:', df['等级'].value_counts().to_dict() if '等级' in df.columns else 'N/A')
