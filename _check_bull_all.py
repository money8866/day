import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/bull_stocks_all_20260721_004031.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('列名:', list(df.columns))
print()
print(df.head(5).to_string())
print()
print('最终分范围:', df['最终分'].min(), '-', df['最终分'].max())
if 'Bull_v2.1分' in df.columns:
    print('Bull评分范围:', df['Bull_v2.1分'].min(), '-', df['Bull_v2.1分'].max())
print('等级分布:', df['等级'].value_counts().to_dict() if '等级' in df.columns else 'N/A')
