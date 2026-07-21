import pandas as pd
df = pd.read_csv('../report_daily/bull_stocks_all.csv', encoding='utf-8-sig')
ds = pd.read_csv('../report_daily/double_score_20260721_083744.csv', encoding='utf-8-sig')
codes = set(str(c).strip() for c in ds['代码'])
df2 = df[df['code'].astype(str).str.strip().isin(codes)]
print('行业分布:')
for ind, cnt in df2['industry'].value_counts().items():
    print(f'  {ind}: {cnt}')
print()
print('样本股票行业:')
for _, r in df2.head(5).iterrows():
    print(f'  {r["code"]} {r["name"]:8s} 行业={r["industry"]}')