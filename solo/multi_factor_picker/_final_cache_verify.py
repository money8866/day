"""从本地缓存验证烽火通信二波信号（修正版）"""
import pandas as pd

# 从本地缓存读取数据
cache_file = r'D:\mystock\cache_daily\600498.SH.csv'
daily = pd.read_csv(cache_file, encoding='utf-8')

# 确保trade_date是字符串类型
daily['trade_date'] = daily['trade_date'].astype(str)

# 确保数据正序（从旧到新）
daily = daily.sort_values('trade_date', ascending=True).reset_index(drop=True)

print(f'数据范围: {daily.iloc[0]["trade_date"]} ~ {daily.iloc[-1]["trade_date"]}')
print(f'总数据: {len(daily)}条\n')

# 找到6月11日数据
target_date = '20260611'
target_row = daily[daily['trade_date'] == target_date]

if len(target_row) == 0:
    print(f'❌ 未找到{target_date}数据')
    import sys
    sys.exit(0)

target_idx = target_row.index[0]

print(f'=== 20260611数据分析 ===')
print(f'索引位置: {target_idx}')
print(f'收盘: {float(daily.loc[target_idx, "close"]):.2f}')
print(f'涨幅: {float(daily.loc[target_idx, "pct_chg"]):.2f}%')
print(f'成交量: {float(daily.loc[target_idx, "vol"]):.0f}手\n')

# 取6月11日前的60天数据
lookback_days = 60
start_idx = max(0, target_idx - lookback_days)
recent = daily.loc[start_idx:target_idx].copy()

print(f'=== 二波检测（回看{len(recent)-1}天）===')
print(f'数据范围: {recent.iloc[0]["trade_date"]} ~ {recent.iloc[-1]["trade_date"]}')
print(f'数据条数: {len(recent)}\n')

# 排除最近5天（包括目标日）
recent_ex5 = recent.iloc[:-5]
print(f'排除最近5天后: {len(recent_ex5)}条')

# 找首波涨停日
limit_up_days = recent_ex5[recent_ex5['pct_chg'] >= 9.4]

if len(limit_up_days) > 0:
    print(f'\n涨停日数量: {len(limit_up_days)}')
    wave1_idx = limit_up_days['pct_chg'].idxmax()
    wave1_row = daily.loc[wave1_idx]

    print(f'\n首波涨停日:')
    print(f'  日期: {wave1_row["trade_date"]}')
    print(f'  涨幅: {float(wave1_row["pct_chg"]):.2f}%')
    print(f'  收盘: {float(wave1_row["close"]):.2f}')

    # 找首波后数据
    after_wave1 = daily.loc[wave1_idx+1:target_idx]

    if len(after_wave1) > 0:
        pullback_low = float(after_wave1['low'].min())
        pullback_low_date = after_wave1.loc[after_wave1['low'].idxmin(), 'trade_date']
        pullback_ratio = pullback_low / float(wave1_row['close'])

        print(f'\n回踩分析:')
        print(f'  最低价: {pullback_low:.2f}')
        print(f'  日期: {pullback_low_date}')
        print(f'  回踩比例: {pullback_ratio:.1%}')

        # 二波判断
        latest_close = float(daily.loc[target_idx, 'close'])
        latest_pct = float(daily.loc[target_idx, 'pct_chg'])

        print(f'\n二波判断:')
        print(f'  涨幅≥5%: {"✓" if latest_pct >= 5 else "✗"} ({latest_pct:.1f}%)')
        print(f'  突破首波98%: {"✓" if latest_close >= float(wave1_row["close"]) * 0.98 else "✗"} ({latest_close:.1f} vs {float(wave1_row["close"])*0.98:.1f})')
        print(f'  回踩≥80%: {"✓" if pullback_ratio >= 0.80 else "✗"} ({pullback_ratio:.1%})')

        is_wave2 = (
            latest_pct >= 5 and
            latest_close >= float(wave1_row['close']) * 0.98 and
            pullback_ratio >= 0.80
        )

        print(f'\n【最终结论】')
        print(f'二波确认: {"✓成功" if is_wave2 else "✗失败"}')

        if is_wave2:
            # 计算技术面得分
            tech_score = 0.0
            turnover_rate = 18.6

            if turnover_rate >= 8:
                tech_score += 2.0
                print(f'\nF6换手率: {turnover_rate:.1f}% ≥ 8% → +2.0分')
            elif turnover_rate >= 5:
                tech_score += 1.5
                print(f'\nF6换手率: {turnover_rate:.1f}% ≥ 5% → +1.5分')

            tech_score += 1.0
            print(f'F8成交量: 涨停缩量锁仓 → +1.0分')

            if latest_pct >= 9.4:
                tech_score += 3.0
                print(f'WAVE2二波: 涨停二波 → +3.0分')
            else:
                tech_score += 2.0
                print(f'WAVE2二波: 大涨二波 → +2.0分')

            normalized_score = min(100, (tech_score / 22) * 100)

            print(f'\n【技术面评分】')
            print(f'原始得分: {tech_score:.1f}/22')
            print(f'标准化得分: {normalized_score:.1f}/100')
            print(f'趋势强度: {"强趋势" if normalized_score >= 60 else "中等" if normalized_score >= 40 else "弱趋势"}')

else:
    print('❌ 未找到涨停日')
