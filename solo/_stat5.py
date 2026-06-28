import pandas as pd
import os

# 从CSV中找到600188的信号
output_dir = r'D:\mystock\solo\trend_feature_output'
files = sorted([f for f in os.listdir(output_dir) if 'qualified' in f and f.endswith('.csv')], reverse=True)
csv_path = os.path.join(output_dir, files[0])
df = pd.read_csv(csv_path)

df['entry_date'] = df['entry_date'].astype(str)
mask = (df['entry_date'] >= '20260501') & (df['entry_date'] <= '20260625') & (df['ts_code'] == '600188.SH')
sig = df[mask].sort_values('signal_date')
print("600188.SH 所有信号:")
print(sig[['signal_date','entry_date','signal_score','prior_rally_gain','pullback_depth','return_1d','return_5d','return_10d','return_20d']].to_string(index=False))
