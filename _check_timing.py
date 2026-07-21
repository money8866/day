import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/timing_analysis_20260721_090211.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('列名:', list(df.columns))
print()
print(df.head(5).to_string())
print()
print('评分范围:', df['timing_score'].min() if 'timing_score' in df.columns else df.iloc[:, -1].min(), '-', df['timing_score'].max() if 'timing_score' in df.columns else df.iloc[:, -1].max())
