"""详细调试二波检测函数"""
import sys
import importlib

for mod_name in list(sys.modules.keys()):
    if 'trend_picker' in mod_name:
        del sys.modules[mod_name]

sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
import pandas as pd

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260301', end_date='20260611')
basic = fetcher.pro.daily_basic(ts_code='600498.SH', start_date='20260301', end_date='20260611')
daily_merged = daily.merge(basic[['trade_date', 'turnover_rate']], on='trade_date', how='left')

lookback_days = 60
detail = {}

# Step 1: 检查数据长度
print(f'数据长度: {len(daily_merged)} (需要≥{lookback_days})')
if len(daily_merged) < lookback_days:
    print('❌ 数据不足')
    sys.exit(0)

# Step 2: 取最近60天（排除最近5天）
recent = daily_merged.head(lookback_days).iloc[5:]
print(f'\n最近60天（排除最近5天）: {len(recent)}条')
print(f'日期范围: {recent.iloc[-1]["trade_date"]} ~ {recent.iloc[0]["trade_date"]}')

if len(recent) == 0:
    print('❌ 没有数据')
    sys.exit(0)

# Step 3: 找涨停日
limit_up_days = recent[recent['pct_chg'] >= 9.5]
print(f'\n涨停日数量: {len(limit_up_days)}')

if len(limit_up_days) > 0:
    wave1_idx = limit_up_days['pct_chg'].idxmax()
    print(f'涨停日详情: {limit_up_days[["trade_date", "pct_chg", "close"]].to_string(index=False)}')
else:
    wave1_idx = recent['pct_chg'].idxmax()

wave1_row = recent.loc[wave1_idx]
wave1_pct = float(wave1_row['pct_chg'])
wave1_close = float(wave1_row['close'])
wave1_date = str(wave1_row['trade_date'])

print(f'\n首波选择:')
print(f'日期: {wave1_date}')
print(f'涨幅: {wave1_pct:.1f}%')
print(f'收盘: {wave1_close:.2f}')
print(f'索引: {wave1_idx}')

if wave1_pct < 8:
    print(f'\n❌ 首波不明显（涨幅{wave1_pct:.1f}% < 8%）')
    sys.exit(0)
else:
    print(f'\n✓ 首波涨幅达标（{wave1_pct:.1f}% ≥ 8%）')

# Step 4: 找首波后数据
after_wave1 = daily_merged.loc[:wave1_idx-1]
print(f'\n首波后数据: {len(after_wave1)}条')

if len(after_wave1) == 0:
    print('❌ 首波后没有数据')
    sys.exit(0)

pullback_low = float(after_wave1['low'].min())
pullback_low_date = str(after_wave1.loc[after_wave1['low'].idxmin(), 'trade_date'])
pullback_ratio = pullback_low / wave1_close

print(f'回踩最低: {pullback_low:.2f} ({pullback_low_date})')
print(f'回踩比例: {pullback_ratio:.1%}')

# Step 5: 今日数据
latest = daily_merged.iloc[0]
latest_pct = float(latest['pct_chg'])
latest_close = float(latest['close'])

print(f'\n今日数据:')
print(f'收盘: {latest_close:.2f}')
print(f'涨幅: {latest_pct:.1f}%')

# Step 6: 判断
print(f'\n二波判断:')
print(f'涨幅≥5%: {"✓" if latest_pct >= 5 else "✗"} ({latest_pct:.1f}%)')
print(f'突破首波: {"✓" if latest_close >= wave1_close else "✗"} ({latest_close:.1f} vs {wave1_close:.1f})')
print(f'回踩有效: {"✓" if pullback_ratio >= 0.80 else "✗"} ({pullback_ratio:.1%})')

is_wave2 = (
    latest_pct >= 5 and
    latest_close >= wave1_close * 0.98 and
    pullback_ratio >= 0.80
)

print(f'\n【最终结论】二波确认: {"✓成功" if is_wave2 else "✗失败"}')
