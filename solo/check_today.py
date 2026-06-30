import pandas as pd

csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260630_204617_qualified.csv'
df = pd.read_csv(csv_path, encoding='utf-8-sig')
print(f'Today signals: {len(df)}')
print()

print('MA20 Offset Distribution:')
print(f'  Min: {df["above_ma20_pct"].min():.1f}%')
print(f'  Max: {df["above_ma20_pct"].max():.1f}%')
print(f'  Mean: {df["above_ma20_pct"].mean():.1f}%')
print()

print('MA20 groups:')
for name, low, high in [('5-10%', 5, 10), ('10-12%', 10, 12), ('12-15%', 12, 15), ('15-18%', 15, 18), ('18%+', 18, 999)]:
    subset = df[(df['above_ma20_pct'] >= low) & (df['above_ma20_pct'] < high)]
    if len(subset) > 0:
        print(f'{name}: {len(subset)} signals')

print()
print('Today signals:')
for _, row in df.iterrows():
    print(f"  {row['ts_code']:12} MA20={row['above_ma20_pct']:5.1f}% Ret={row['pct_chg']:5.1f}% Vol={row['vol_ratio']:.2f} Score={row['entry_score']}")
