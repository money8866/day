import json
import pandas as pd

# 读取结果
with open(r'D:\mystock\solo\report_daily\v49_sqlite_scan_20260619.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

# 创建DataFrame
df = pd.DataFrame(results)

# 统计
buy = df[df['signal'] == 'BUY']
watch = df[df['signal'] == 'WATCH']
breakout = df[df['mode'] == 'BREAKOUT']

print(f'📊 统计：')
print(f'  BUY      = {len(buy)}只')
print(f'  WATCH    = {len(watch)}只')
print(f'  BREAKOUT = {len(breakout)}只')

if len(buy) > 0:
    print(f'\n🎯 BUY信号详情：')
    for _, row in buy.iterrows():
        print(f'  {row["name"]} ({row["ts_code"]}) - {row["score"]}分，{row["mode"]}模式，量比{row["vol_ratio"]}，主题加成{row["theme_bonus"]}')

# 保存CSV
csv_file = r'D:\mystock\solo\report_daily\v49_sqlite_scan_20260619.csv'
df.to_csv(csv_file, index=False, encoding='utf-8-sig')
print(f'\n✅ CSV已保存：{csv_file}')
