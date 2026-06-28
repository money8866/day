"""完整验证二波检测"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260301', end_date='20260611')
basic = fetcher.pro.daily_basic(ts_code='600498.SH', start_date='20260301', end_date='20260611')
daily_merged = daily.merge(basic[['trade_date', 'turnover_rate']], on='trade_date', how='left')

# Step 1: 找首波涨停日
lookback_days = 60
recent = daily_merged.head(lookback_days).iloc[5:]

if len(recent) > 0:
    limit_up_days = recent[recent['pct_chg'] >= 9.4]

    if len(limit_up_days) > 0:
        wave1_idx = limit_up_days['pct_chg'].idxmax()
    else:
        wave1_idx = recent['pct_chg'].idxmax()

    wave1_row = daily_merged.loc[wave1_idx]
    wave1_close = float(wave1_row['close'])
    wave1_pct = float(wave1_row['pct_chg'])
    wave1_date = str(wave1_row['trade_date'])

    print(f'【首波确认】')
    print(f'日期: {wave1_date}')
    print(f'涨幅: {wave1_pct:.1f}%')
    print(f'收盘: {wave1_close:.2f}')

    # Step 2: 找首波后回踩最低点
    after_wave1 = daily_merged.loc[:wave1_idx-1]  # 从开头到首波索引之前

    if len(after_wave1) > 0:
        pullback_low = float(after_wave1['low'].min())
        pullback_low_date = str(after_wave1.loc[after_wave1['low'].idxmin(), 'trade_date'])
        pullback_ratio = pullback_low / wave1_close

        print(f'\n【回踩分析】')
        print(f'最低价: {pullback_low:.2f}')
        print(f'日期: {pullback_low_date}')
        print(f'回踩比例: {pullback_ratio:.1%}')

        # Step 3: 今日数据
        latest = daily_merged.iloc[0]
        latest_close = float(latest['close'])
        latest_pct = float(latest['pct_chg'])

        print(f'\n【今日数据】')
        print(f'收盘: {latest_close:.2f}')
        print(f'涨幅: {latest_pct:.1f}%')

        # Step 4: 判断二波
        print(f'\n【二波判断】')
        print(f'涨幅≥5%: {"✓" if latest_pct >= 5 else "✗"}')
        print(f'突破首波: {"✓" if latest_close >= wave1_close * 0.98 else "✗"} ({latest_close:.1f} vs {wave1_close*0.98:.1f})')
        print(f'回踩有效: {"✓" if pullback_ratio >= 0.82 else "✗"}')

        is_wave2 = (
            latest_pct >= 5 and
            latest_close >= wave1_close * 0.98 and
            pullback_ratio >= 0.82
        )

        print(f'\n【最终结论】')
        print(f'二波确认: {"✓成功" if is_wave2 else "✗失败"}')

        if not is_wave2:
            if latest_pct < 5:
                print(f'原因: 涨幅不足5%（实际{latest_pct:.1f}%）')
            elif latest_close < wave1_close * 0.98:
                print(f'原因: 未突破首波（{latest_close:.1f} < {wave1_close*0.98:.1f}）')
            elif pullback_ratio < 0.82:
                print(f'原因: 回踩过深（{pullback_ratio:.1%} < 82%）')
    else:
        print('❌ 首波后没有数据')
else:
    print('❌ 最近60天没有数据')
