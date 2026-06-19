import pandas as pd
import os

cache_dir = r'd:\mystock\cache_daily'
code = '688403.SH'

f = os.path.join(cache_dir, code + '.csv')
df = pd.read_csv(f)
df['trade_date'] = df['trade_date'].astype(int)
df = df[df['trade_date'] <= 20260610].tail(260).reset_index(drop=True)

close = df['close'].values
high = df['high'].values
low = df['low'].values
vol = df['vol'].values
pct = df['pct_chg'].values

today_close = close[-1]
today_high = high[-1]
today_pct = pct[-1]
today_vol = vol[-1]

# 均线
ma20 = close[-20:].mean() if len(close) >= 20 else close.mean()
ma60 = close[-60:].mean() if len(close) >= 60 else close.mean()
bias20 = (today_close - ma20) / ma20 * 100
bias60 = (today_close - ma60) / ma60 * 100

# 趋势涨幅
ret5 = (close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0
ret10 = (close[-1] / close[-11] - 1) * 100 if len(close) >= 11 else 0
ret20 = (close[-1] / close[-21] - 1) * 100 if len(close) >= 21 else 0

# 20日/60日/120日高点
hhv20 = high[-20:].max() if len(high) >= 20 else high.max()
hhv60 = high[-60:].max() if len(high) >= 60 else high.max()
hhv120 = high[-120:].max() if len(high) >= 120 else high.max()

dist_to_hhv20 = (today_close - hhv20) / hhv20 * 100
dist_to_hhv60 = (today_close - hhv60) / hhv60 * 100
dist_to_hhv120 = (today_close - hhv120) / hhv120 * 100

# 量比
vol_ma5 = vol[-5:].mean()
vol_ratio = today_vol / vol_ma5 if vol_ma5 > 0 else 1.0
vol_30_max = vol[-30:].max() if len(vol) >= 30 else vol.max()
vol_peak_ratio = today_vol / vol_30_max if vol_30_max > 0 else 1.0

# 上影线
upper_shadow = (today_high - today_close) / today_close * 100

print('=' * 70)
print(f'汇成股份 688403.SH @ 2026-06-10 评分拆解 (系统评分: 9.2)')
print('=' * 70)

print(f'\n【当日数据】')
print(f'  收盘价: {today_close:.2f} | 涨幅: {today_pct:.2f}% | 最高价: {today_high:.2f}')
print(f'  上影线: {upper_shadow:.2f}% | 量比(vs 5日均): {vol_ratio:.2f} | 量比(vs 30日最大): {vol_peak_ratio:.2f}')
print(f'  换手率: 11.23% | 成交额: 19.33亿')

print(f'\n【均线位置】')
print(f'  MA20: {ma20:.2f} | MA20乖离率: {bias20:.2f}%')
print(f'  MA60: {ma60:.2f} | MA60乖离率: {bias60:.2f}%')
print(f'  距20日高: {dist_to_hhv20:.2f}% | 距60日高: {dist_to_hhv60:.2f}% | 距120日高: {dist_to_hhv120:.2f}%')

print(f'\n【趋势强度】')
print(f'  近5日涨幅: {ret5:.2f}% | 近10日涨幅: {ret10:.2f}% | 近20日涨幅: {ret20:.2f}%')

print(f'\n【近期K线形态 - 最近20天】')
for i in range(20, 0, -1):
    if i <= len(df):
        row = df.iloc[-i]
        td = int(row['trade_date'])
        c = row['close']
        h = row['high']
        pct_val = row['pct_chg']
        v = row['vol']
        vr = v / vol_ma5 if vol_ma5 > 0 else 1.0
        up = (h - c) / c * 100
        mark = ' <<<' if td == 20260610 else ''
        print(f'  {td} 收{c:.2f} 高{h:.2f} 涨跌{pct_val:+.2f}% 量比{vr:.2f} 上影{up:.2f}%{mark}')

# 60日高点追踪
print(f'\n【60日高点追踪】')
for step in [0, 5, 10, 20, 30, 45, 60]:
    if step < len(df):
        row = df.iloc[-(step+1)]
        td = int(row['trade_date'])
        print(f'  -{step}日 ({td}): 收{row["close"]:.2f} 高{row["high"]:.2f} 涨跌{row["pct_chg"]:+.2f}%')

# 计算评分系统中各项因子
print(f'\n' + '=' * 70)
print(f'评分因子拆解')
print('=' * 70)

# 1. 趋势强度
trend_score = 40  # 基础值
if ret10 > 20:
    trend_score += 30
elif ret10 > 10:
    trend_score += 20
elif ret10 > 5:
    trend_score += 10
else:
    trend_score -= 10

# 2. 位置安全
position_score = 50
if bias20 > 15:
    position_score -= 20
    print(f'  🚨 MA20乖离{bias20:.1f}% > 15% → 位置过高 -20')
elif bias20 > 10:
    position_score -= 10
    print(f'  ⚠ MA20乖离{bias20:.1f}% > 10% → 位置偏高 -10')
elif bias20 < -10:
    position_score -= 15
    print(f'  ⚠ MA20乖离{bias20:.1f}% < -10% → 位置过低 -15')
else:
    position_score += 10
    print(f'  ✓ MA20乖离{bias20:.1f}% → 位置合理 +10')

# 量能
if vol_ratio < 0.8:
    position_score -= 10
    print(f'  ⚠ 量比{vol_ratio:.2f} < 0.8 → 资金参与不足 -10')
elif vol_ratio > 1.5:
    position_score += 10
    print(f'  ✓ 量比{vol_ratio:.2f} > 1.5 → 资金活跃 +10')

# 3. 资金健康度
capital_score = 50
if vol_peak_ratio > 1.0:
    capital_score += 20
elif vol_peak_ratio > 0.8:
    capital_score += 10
elif vol_peak_ratio < 0.5:
    capital_score -= 20
    print(f'  🚨 量仅为30日最大量的{vol_peak_ratio:.1%} → 资金健康度 -20')
elif vol_peak_ratio < 0.7:
    capital_score -= 10
    print(f'  ⚠ 量仅为30日最大量的{vol_peak_ratio:.1%} → 资金健康度 -10')

# 4. 热度持续性
hot_score = 50

# 5. 基本面
basic_score = 58.5  # 系统报告值

# 6. 假突破惩罚
fake_breakout = 0

# 检查60日内失败突破
print(f'\n【假突破检测 - 60日内】')
hhv_60 = high[-60:].max() if len(high) >= 60 else high.max()
found_breakout = False
for i in range(5, 60):  # 从5天前开始往前找
    if i >= len(high):
        break
    day_high = high[-(i+1)]
    day_close = close[-(i+1)]
    if day_high >= hhv_60 * 0.95:  # 接近高点
        # 看3天后是否回落>8%
        after_idx = -(i+1) + 3
        if -after_idx <= len(close):
            after_price = close[after_idx]
            drop = (day_high - after_price) / day_high * 100
            if drop > 8:
                print(f'  🚨 -{i}日 高{day_high:.2f}→3天后{after_price:.2f}, 回落{drop:.1f}% → 假突破! -20')
                fake_breakout += 20
                found_breakout = True
                break

if not found_breakout:
    print(f'  ✓ 未发现显著假突破')

# 长上影
if upper_shadow > 6:
    fake_breakout += 20
    print(f'  🚨 当日上影{upper_shadow:.1f}% → 冲高回落 -20')
elif upper_shadow > 4:
    fake_breakout += 15
    print(f'  ⚠ 当日上影{upper_shadow:.1f}% → 上影偏长 -15')
elif upper_shadow > 3:
    fake_breakout += 8
    print(f'  • 当日上影{upper_shadow:.1f}% → 轻微上影 -8')

# 主题质量扣分（之前改了权重）
theme_quality = 67  # 存储芯片主题分，非核心主线
theme_penalty = 0
if theme_quality < 70:
    theme_penalty = int((70 - theme_quality) * 0.5)
    print(f'  • 主题"存储芯片"质量{theme_quality}分 < 70 → 主题扣分 -{theme_penalty}')

# 综合
total_score = (trend_score * 0.25 + position_score * 0.25 + capital_score * 0.25
                + hot_score * 0.20 + basic_score * 0.15) - fake_breakout - theme_penalty
total_score = max(0, min(100, total_score))

print(f'\n' + '=' * 70)
print(f'【因子分解】')
print(f'  趋势强度: {trend_score:.1f} (基础40 + 近10日{ret10:.1f}%调整)')
print(f'  位置安全: {position_score:.1f} (MA20乖离{bias20:.1f}% + 量比{vol_ratio:.2f})')
print(f'  资金健康度: {capital_score:.1f} (量比峰值{vol_peak_ratio:.2f})')
print(f'  热度持续性: {hot_score:.1f}')
print(f'  基本面: {basic_score:.1f}')
print(f'  假突破惩罚: -{fake_breakout}')
print(f'  主题质量扣分: -{theme_penalty}')
print(f'  → 加权综合: {total_score:.1f}')
print(f'  → 系统报告评分: 9.2')

# 失败概率
print(f'\n【失败概率分析】')
print(f'  距20日高点: {dist_to_hhv20:.2f}% | 距60日高: {dist_to_hhv60:.2f}%')
print(f'  近10日涨幅: {ret10:.2f}%')
print(f'  量比(vs 30日最大量): {vol_peak_ratio:.2f}')
print(f'  → 系统报告失败概率: 90.0% (高位 + 缩量 + 长上影组合)')

print(f'\n' + '=' * 70)
print(f'【核心问题总结】')
print('=' * 70)
print(f'  汇成股份评分仅9.2分的主要原因：')
if fake_breakout > 0:
    print(f'  🔴 假突破惩罚: -{fake_breakout} 分')
if theme_penalty > 0:
    print(f'  🔴 主题质量扣分: -{theme_penalty} 分（非核心主线）')
print(f'  🔴 位置安全偏低: 短期涨幅过大 + 量能萎缩')
print(f'  🔴 当日涨幅仅+2.46%，上影线却有{upper_shadow:.2f}% → 冲高回落')
print(f'  🔴 当日量仅为30日最大量的{vol_peak_ratio:.1%} → 资金撤退迹象')
