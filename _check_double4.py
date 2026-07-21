import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/double_score_20260721_083744.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('列名:', list(df.columns))
print()
print(df.head(3).to_string())
print()
print('评分范围:', df['DoubleScore'].min(), '-', df['DoubleScore'].max())
print('≥90分:', len(df[df['DoubleScore']>=90]), '  ≥80分:', len(df[df['DoubleScore']>=80]))
