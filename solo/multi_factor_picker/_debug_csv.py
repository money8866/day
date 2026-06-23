import pandas as pd
df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\bullscore_20260622_142716.csv')
print('列名:', df.columns.tolist())
print('前3行关键列:')
for i, row in df.head(3).iterrows():
    print(f"  ts_code={row.get('ts_code','')}  code={row.get('code','')}  name={row.get('name','')}")
# 查找ts_code列
for col in ['ts_code', 'code', 'ts_code_full']:
    if col in df.columns:
        samples = df[col].head(5).tolist()
        print(f'{col} 样本: {samples}')
print('\n全列名包含ts:')
for col in df.columns:
    if 'ts' in col.lower() or 'code' in col.lower():
        print(f'  {col}: {df[col].head(2).tolist()}')
