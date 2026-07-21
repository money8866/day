import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/enhanced_timing_20260721_092436.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('列名:', list(df.columns))
print()
print(df.head(5).to_string())
print()
print('评分范围:', df['综合评分'].min() if '综合评分' in df.columns else df.iloc[:, -1].min(), '-', df['综合评分'].max() if '综合评分' in df.columns else df.iloc[:, -1].max())
