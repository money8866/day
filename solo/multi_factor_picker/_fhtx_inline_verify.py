"""烽火通信二波检测（内联实现）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

ts_code = '600498.SH'
daily = fetcher.pro.daily(ts_code=ts_code, start_date='20260301', end_date='20260611')
basic = fetcher.pro.daily_basic(ts_code=ts_code, start_date='20260301', end_date='20260611')
daily_merged = daily.merge(basic[['trade_date', 'turnover_rate']], on='trade_date', how='left')

print(f'烽火通信数据: {len(daily_merged)}条\n')

# === 内联二波检测逻辑 ===
lookback_days = 60
detail = {}

# Step 1: 检查数据长度
if len(daily_merged) < lookback_days:
    print(f'❌ 数据不足（{len(daily_merged)} < {lookback_days}）')
    sys.exit(0)

# Step 2: 取最近60天（排除最近5天）
recent = daily_merged.head(lookback_days).iloc[5:]
print(f'最近{lookback_days}天（排除最近5天）: {len(recent)}条')

if len(recent) == 0:
    print('❌ 没有数据')
    sys.exit(0)

# Step 3: 找首波涨停日（优先）
limit_up_days = recent[recent['pct_chg'] >= 9.4]
print(f'涨停日数量: {len(limit_up_days)}')

if len(limit_up_days) > 0:
    wave1_idx = limit_up_days['pct_chg'].idxmax()
    print(f'涨停日详情:')
    print(limit_up_days[['trade_date', 'pct_chg', 'close']].to_string(index=False))
else:
    wave1_idx = recent['pct_chg'].idxmax()

wave1_row = recent.loc[wave1_idx]
wave1_date = str(wave1_row['trade_date'])
wave1_pct = float(wave1_row['pct_chg'])
wave1_close = float(wave1_row['close'])

print(f'\n首波选择:')
print(f'日期: {wave1_date}')
print(f'涨幅: {wave1_pct:.1f}%')
print(f'收盘: {wave1_close:.2f}')

if wave1_pct < 8:
    print(f'❌ 首波不明显（涨幅{wave1_pct:.1f}% < 8%）')
    sys.exit(0)

detail['wave1_date'] = wave1_date
detail['wave1_pct'] = round(wave1_pct, 1)
detail['wave1_close'] = round(wave1_close, 2)

# Step 4: 找首波后数据（数据倒序，首波后在前面）
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

detail['pullback_low'] = round(pullback_low, 2)
detail['pullback_low_date'] = pullback_low_date
detail['pullback_ratio'] = round(pullback_ratio, 3)

# Step 5: 今日数据
latest = daily_merged.iloc[0]
latest_pct = float(latest['pct_chg'])
latest_close = float(latest['close'])
latest_turnover = float(latest.get('turnover_rate', 0) or 0)

print(f'\n今日数据:')
print(f'收盘: {latest_close:.2f}')
print(f'涨幅: {latest_pct:.1f}%')
print(f'换手率: {latest_turnover:.2f}%')

detail['latest_pct'] = round(latest_pct, 1)
detail['latest_close'] = round(latest_close, 2)

# Step 6: 二波判断（修复阈值）
print(f'\n【二波判断】')
print(f'涨幅≥5%: {"✓" if latest_pct >= 5 else "✗"} ({latest_pct:.1f}%)')
print(f'突破首波98%: {"✓" if latest_close >= wave1_close * 0.98 else "✗"} ({latest_close:.1f} vs {wave1_close*0.98:.1f})')
print(f'回踩≥80%: {"✓" if pullback_ratio >= 0.80 else "✗"} ({pullback_ratio:.1%})')

is_wave2 = (
    latest_pct >= 5 and
    latest_close >= wave1_close * 0.98 and
    pullback_ratio >= 0.80
)

print(f'\n【最终结论】')
print(f'二波确认: {"✓成功" if is_wave2 else "✗失败"}')
print(f'详情: {detail}')

if is_wave2:
    # 计算技术面得分
    tech_score = 0.0
    
    # F6: 换手率评分（启动日）
    if latest_turnover >= 8:
        tech_score += 2.0
        print(f'\nF6换手率: {latest_turnover:.1f}% ≥ 8% → +2.0分')
    elif latest_turnover >= 5:
        tech_score += 1.5
        print(f'\nF6换手率: {latest_turnover:.1f}% ≥ 5% → +1.5分')
    elif latest_turnover > 0:
        tech_score += 1.0
        print(f'\nF6换手率: {latest_turnover:.1f}% （涨停缩量）→ +1.0分')
    
    # F8: 成交量评分
    tech_score += 1.0
    print(f'F8成交量: 涨停缩量 → +1.0分')
    
    # WAVE2: 二波加分
    if latest_pct >= 9.4:
        tech_score += 3.0
        print(f'WAVE2二波: 涨停二波 → +3.0分')
    else:
        tech_score += 2.0
        print(f'WAVE2二波: 大涨二波 → +2.0分')
    
    # F9: 过热惩罚
    if latest_turnover > 25:
        tech_score -= 1.0
        print(f'F9过热: 换手率{latest_turnover:.1f}% > 25% → -1.0分')
    
    # 标准化得分
    normalized_score = min(100, (tech_score / 22) * 100)
    
    print(f'\n【最终评分】')
    print(f'原始得分: {tech_score:.1f}')
    print(f'标准化得分: {normalized_score:.1f}/100')
    print(f'趋势强度: {"强趋势" if normalized_score >= 60 else "中等" if normalized_score >= 40 else "弱趋势"}')
