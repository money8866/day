import pandas as pd, glob, os

# 找所有wave2相关CSV
csvs = glob.glob(r'D:\mystock\solo\multi_factor_picker\output\*wave2*.csv')
csvs += glob.glob(r'D:\mystock\solo\multi_factor_picker\output\*pattern*.csv')
for c in sorted(csvs, key=lambda x: os.path.getmtime(x), reverse=True)[:10]:
    print(c, os.path.getsize(c))

# 看wave2_daily
df1 = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\wave2_daily_20260623.csv')
print('\n=== wave2_daily ===')
print('columns:', df1.columns.tolist())
print('rows:', len(df1))
print(df1[['ts_code','name','pattern','base_score']].head(5))

# 看wave2_pattern_scanner的输出
scan_csv = r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_scan_results.csv'
if os.path.exists(scan_csv):
    df2 = pd.read_csv(scan_csv)
    print('\n=== pattern_scan ===')
    print('columns:', df2.columns.tolist())
    print('rows:', len(df2))
else:
    # 搜索其他可能的CSV
    for c in glob.glob(r'D:\mystock\solo\multi_factor_picker\output\*scan*.csv'):
        df2 = pd.read_csv(c)
        print(f'\n=== {c} ===')
        print('columns:', df2.columns.tolist())
        if 'score' in df2.columns:
            print('score range:', df2['score'].min(), '-', df2['score'].max())
        break
