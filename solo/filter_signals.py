import pandas as pd

# Today signals
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260630_204617_qualified.csv'
df = pd.read_csv(csv_path, encoding='utf-8-sig')

print('Today signals:')
print(f'  Total: {len(df)} signals')
print()

# Filter by score >= 70
df_good = df[df['entry_score'] >= 70]

# Filter by score >= 80
df_best = df[df['entry_score'] >= 80]

print(f'Score >= 70: {len(df_good)} signals')
print(f'Score >= 80: {len(df_best)} signals')
print()

if len(df_best) > 0:
    print('BEST signals (Score >= 80):')
    for _, row in df_best.sort_values('entry_score', ascending=False).iterrows():
        print(f"  {row['ts_code']:12} Score={row['entry_score']:3} Ret={row['pct_chg']:5.1f}% Vol={row['vol_ratio']:.2f} MA20={row['above_ma20_pct']:.1f}%")
    print()

if len(df_good) > 0:
    print('GOOD signals (Score 70-79):')
    for _, row in df_good.sort_values('entry_score', ascending=False).iterrows():
        if row['entry_score'] < 80:
            print(f"  {row['ts_code']:12} Score={row['entry_score']:3} Ret={row['pct_chg']:5.1f}% Vol={row['vol_ratio']:.2f} MA20={row['above_ma20_pct']:.1f}%")

# Save
df_good.to_csv(r'D:\mystock\solo\trend_feature_output\entry_filtered_score70.csv', index=False, encoding='utf-8-sig')
print()
print('Saved to: entry_filtered_score70.csv')
