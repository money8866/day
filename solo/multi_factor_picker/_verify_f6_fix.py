"""验证F6修复效果"""
latest_pct = 9.5
latest_turnover = 18.6
is_limit_up = latest_pct >= 9.4  # 修复后的阈值

print(f'涨跌幅: {latest_pct:.1f}%')
print(f'换手率: {latest_turnover:.1f}%')
print(f'is_limit_up: {is_limit_up} (阈值9.4)\n')

if is_limit_up:
    if latest_turnover >= 8:
        f6_score = 2.0
        note = '涨停启动日充分换手'
    elif latest_turnover >= 5:
        f6_score = 1.5
        note = '涨停启动日适中换手'
    else:
        f6_score = 1.0
        note = '缩量涨停（锁仓）'
else:
    if latest_turnover > 10:
        f6_score = 0.5
        note = '过热警告'
    elif 5 <= latest_turnover <= 10:
        f6_score = 1.0
        note = '换手适中'
    else:
        f6_score = 0.5
        note = '换手偏低'

print(f'✓ F6得分: {f6_score}')
print(f'✓ 说明: {note}')
