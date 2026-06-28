import pandas as pd
import os

output_dir = r'D:\mystock\solo\trend_feature_output'
files = sorted([f for f in os.listdir(output_dir) if 'qualified' in f and f.endswith('.csv')], reverse=True)
csv_path = os.path.join(output_dir, files[0])
df = pd.read_csv(csv_path)

df['entry_date'] = df['entry_date'].astype(str)
mask = (df['entry_date'] >= '20260501') & (df['entry_date'] <= '20260625')
df = df[mask].reset_index(drop=True)

print(f"总信号: {len(df)} 个\n")

for w in [1, 5, 10, 20]:
    col = f'return_{w}d'
    if col not in df.columns:
        continue
    rets = df[col].dropna()
    neg = rets[rets < 0]
    zero = rets[rets == 0]
    pos = rets[rets > 0]
    # 亏损程度分布
    neg_5_plus = (rets < -5).sum()
    neg_10_plus = (rets < -10).sum()
    neg_15_plus = (rets < -15).sum()
    print(f"+{w:>2}d: 盈利{len(pos)}({(len(pos)/len(rets))*100:.1f}%)  持平{len(zero)}  亏损{len(neg)}({(len(neg)/len(rets))*100:.1f}%)  |  亏>-5%: {(rets< -5).sum()} 亏>-10%: {(rets< -10).sum()} 亏>-15%: {(rets< -15).sum()}")
