import pandas as pd
df = pd.read_csv('D:/mystock/report_daily/etf_midterm_rating.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('列名:', list(df.columns))
print()
print(df.head(5).to_string())
print()
for col in df.columns:
    print(f"  {col}: {df[col].dtype}  非空:{df[col].notna().sum()}")
