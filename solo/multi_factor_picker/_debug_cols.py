import pandas as pd
df = pd.read_csv('cache/income_000001.SZ_2023_None.csv', nrows=3)
print("列类型:")
print(df.dtypes)
print("\n前2行:")
print(df.head(2))
print("\n尝试 .str.endswith('1231'):")
try:
    print(df['end_date'].str.endswith('1231'))
except Exception as e:
    print(f"失败: {e}")

# 检查ann_date
print(f"\nann_date值: {df['ann_date'].tolist()}")
print(f"end_date值: {df['end_date'].tolist()}")
print(f"end_date类型: {type(df['end_date'].iloc[0])}")
