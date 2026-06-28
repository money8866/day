"""验证烽火通信二波检测"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
from trend_picker_v2_draft import detect_wave2_pattern

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 获取烽火通信日线数据
daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260301', end_date='20260611')
print(f'数据条数: {len(daily)}')
print(f'日期范围: {daily.iloc[-1]["trade_date"]} ~ {daily.iloc[0]["trade_date"]}')

# 获取每日基本数据（包含换手率）
basic = fetcher.pro.daily_basic(ts_code='600498.SH', start_date='20260301', end_date='20260611')
print(f'\n基本数据条数: {len(basic)}')

if len(basic) > 0:
    today_basic = basic.iloc[0]
    print(f'今日换手率: {float(today_basic.get("turnover_rate", 0) or 0):.1f}%')

# 合并数据
daily_merged = daily.merge(basic[['trade_date', 'turnover_rate']], on='trade_date', how='left')
print(f'\n合并后数据: {len(daily_merged)}条')
print(f'换手率字段: {"turnover_rate" in daily_merged.columns}')

# 二波检测
is_wave2, detail = detect_wave2_pattern(daily_merged, lookback_days=90)

print(f'\n【二波检测结果】')
print(f'首波日期: {detail.get("wave1_date", "N/A")}')
print(f'首波涨幅: {detail.get("wave1_pct", "N/A")}%')
print(f'首波收盘: {detail.get("wave1_close", "N/A")}')
print(f'回踩最低: {detail.get("pullback_low", "N/A")}')
print(f'回踩比例: {detail.get("pullback_ratio", "N/A")}')
print(f'今日收盘: {detail.get("latest_close", "N/A")}')
print(f'今日涨幅: {detail.get("latest_pct", "N/A")}%')
print(f'二波确认: {"✓" if is_wave2 else "✗"}')

if not is_wave2:
    print(f'原因: {detail.get("note", detail.get("is_wave2", "未知"))}')
