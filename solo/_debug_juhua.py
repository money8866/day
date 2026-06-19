import pandas as pd
import os

cache_dir = r'd:\mystock\cache_daily'
code = '600160.SH'

f = os.path.join(cache_dir, code + '.csv')
df = pd.read_csv(f)
df['trade_date'] = df['trade_date'].astype(int)

# 找到 20260610 那天
idx = df[df['trade_date'] == 20260610].index[0]
df_sub = df.iloc[:idx+1].reset_index(drop=True)

# 取最近60天做详细分析
df_60 = df_sub.tail(60).reset_index(drop=True)

close = df_60['close'].values
high = df_60['high'].values
low = df_60['low'].values
vol = df_60['vol'].values

today_close = close[-1]
today_high = high[-1]
today_pct = df_60['pct_chg'].iloc[-1]
today_vol = vol[-1]

ma20 = close[-20:].mean()
ma60 = close[-60:].mean() if len(close) >= 60 else close.mean()
bias20 = (today_close - ma20) / ma20 * 100
bias60 = (today_close - ma60) / ma60 * 100

hhv120 = high.max()
dist_to_high = (today_close - hhv120) / hhv120 * 100

ret10 = (close[-1] / close[-11] - 1) * 100 if len(close) >= 11 else 0
ret5 = (close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0

vol_ma5 = vol[-5:].mean()
vol_ratio = today_vol / vol_ma5 if vol_ma5 > 0 else 1.0
vol_30_max = vol[-30:].max()
vol_peak_ratio = today_vol / vol_30_max if vol_30_max > 0 else 1.0

print('=' * 70)
print(f'巨化股份 600160.SH @ 2026-06-10 失败概率详细分析')
print('=' * 70)

print(f'\n【当日数据】')
print(f'  收盘价: {today_close:.2f} | 涨幅: {today_pct:.2f}% | 最高价: {today_high:.2f}')
print(f'  量比: {vol_ratio:.2f} | 当日量/30日最大量: {vol_peak_ratio:.2f}')

print(f'\n【均线位置】')
print(f'  MA20: {ma20:.2f} | MA20乖离率: {bias20:.2f}%')
print(f'  MA60: {ma60:.2f} | MA60乖离率: {bias60:.2f}%')
print(f'  距离120日高点: {dist_to_high:.2f}%')

print(f'\n【趋势强度】')
print(f'  近5日涨幅: {ret5:.2f}% | 近10日涨幅: {ret10:.2f}%')

print(f'\n【近期K线形态 - 最近15天】')
for i in range(15, 0, -1):
    row = df_sub.iloc[-i]
    td = int(row['trade_date'])
    c = row['close']
    h = row['high']
    pct = row['pct_chg']
    v = row['vol']
    vr = v / vol_ma5 if vol_ma5 > 0 else 1.0
    upper_shadow = (h - c) / c * 100
    mark = ' <<<' if td == 20260610 else ''
    print(f'  {td} 收{c:.2f} 高{h:.2f} 涨跌{pct:+.2f}% 量比{vr:.2f} 上影{upper_shadow:.2f}%{mark}')

# 60日高点追踪
print(f'\n【60日高点追踪】')
for step in [0, 5, 10, 20, 30, 45, 60]:
    if step < len(df_sub):
        row = df_sub.iloc[-(step+1)]
        td = int(row['trade_date'])
        print(f'  -{step}日 ({td}): 收{row["close"]:.2f} 高{row["high"]:.2f} 涨跌{row["pct_chg"]:+.2f}%')

historical_high = high.max()
high_idx = list(high).index(historical_high)
print(f'\n  60日历史高点: {historical_high:.2f} ({len(high) - high_idx - 1}日前)')
print(f'  当前价距历史高点: {(today_close - historical_high) / historical_high * 100:.2f}%')

# ============ 失败概率影响因子拆解 ============
print(f'\n' + '=' * 70)
print(f'失败概率影响因子拆解 (系统报告: 56.7%)')
print('=' * 70)

total_penalty = 0

# 1. 趋势过热
if ret10 > 30:
    total_penalty += 15
    print(f'🚨 近10日涨{ret10:.1f}%>30% → 趋势过热 +15')
elif ret10 > 20:
    total_penalty += 10
    print(f'⚠  近10日涨{ret10:.1f}%>20% → 偏热 +10')
elif ret10 > 15:
    total_penalty += 5
    print(f'•  近10日涨{ret10:.1f}%>15% → 温和 +5')
else:
    total_penalty -= 5
    print(f'✓  近10日涨{ret10:.1f}% → 趋势健康 -5')

# 2. MA20乖离率
if bias20 > 20:
    total_penalty += 15
    print(f'🚨 MA20乖离{bias20:.1f}%>20% → 严重乖离 +15')
elif bias20 > 15:
    total_penalty += 10
    print(f'⚠  MA20乖离{bias20:.1f}%>15% → 偏高 +10')
elif bias20 > 10:
    total_penalty += 5
    print(f'•  MA20乖离{bias20:.1f}%>10% → 略高 +5')
else:
    total_penalty -= 10
    print(f'✓  MA20乖离{bias20:.1f}% → 位置合理 -10')

# 3. 量能萎缩
if vol_peak_ratio < 0.5:
    total_penalty += 15
    print(f'🚨 当日量仅为30日最大量的{vol_peak_ratio:.1%} → 严重萎缩 +15')
elif vol_peak_ratio < 0.7:
    total_penalty += 8
    print(f'⚠  当日量仅为30日最大量的{vol_peak_ratio:.1%} → 量能不足 +8')
elif vol_peak_ratio < 0.9:
    total_penalty += 3
    print(f'•  当日量为30日最大量的{vol_peak_ratio:.1%} → 略低 +3')
else:
    total_penalty -= 10
    print(f'✓  当日量为30日最大量的{vol_peak_ratio:.1%} → 量能健康 -10')

# 4. 长上影
upper_shadow_pct = (today_high - today_close) / today_close * 100
if upper_shadow_pct > 6:
    total_penalty += 15
    print(f'🚨 当日上影{upper_shadow_pct:.1f}% → 冲高回落 +15')
elif upper_shadow_pct > 4:
    total_penalty += 8
    print(f'⚠  当日上影{upper_shadow_pct:.1f}% → 上影偏长 +8')
elif upper_shadow_pct > 2:
    total_penalty += 3
    print(f'•  当日上影{upper_shadow_pct:.1f}% → 轻微上影 +3')
else:
    total_penalty -= 10
    print(f'✓  当日上影{upper_shadow_pct:.1f}% → K线形态健康 -10')

# 5. 接近历史高点
if -5 < dist_to_high < 0:
    total_penalty += 10
    print(f'⚠  距历史高点仅{dist_to_high:.1f}%但未突破 → 套牢盘压力 +10')
elif dist_to_high > 0:
    total_penalty -= 10
    print(f'✓  已突破历史高点{dist_to_high:.1f}% → 空间打开 -10')
else:
    print(f'•  距历史高点{dist_to_high:.1f}% → 压力一般')

calc_prob = 50 + total_penalty
adjusted_prob = max(10, min(90, calc_prob))

print(f'\n' + '=' * 70)
print(f'综合计算')
print('=' * 70)
print(f'  基础概率: 50%')
print(f'  综合调整: {"+" if total_penalty >= 0 else ""}{total_penalty}%')
print(f'  计算失败概率: {calc_prob:.1f}% (钳制后 {adjusted_prob:.1f}%)')
print(f'  系统报告失败概率: 56.7%')
print(f'  差异约 {adjusted_prob - 56.7:.1f}% (系统还综合考虑基本面/资金健康度/热度持续性等因子)')

print(f'\n' + '=' * 70)
print(f'【核心风险总结】')
print('=' * 70)
print(f'  巨化股份6/10虽然涨+{today_pct:.2f}%，评分55.8，但失败概率56.7%的核心原因：')
if bias20 > 10:
    print(f'  🔴 MA20乖离率 {bias20:.2f}% — 短期均线偏离过大，位置偏高')
if ret10 > 15:
    print(f'  🔴 近10日涨幅 {ret10:.2f}% — 短期涨幅累积，有追高风险')
if vol_peak_ratio < 0.9:
    print(f'  🔴 当日量能仅为30日最大量的 {vol_peak_ratio:.1%} — 量能不足，资金参与度不够')
if upper_shadow_pct > 2:
    print(f'  🔴 当日上影 {upper_shadow_pct:.2f}% — 有冲高回落迹象')
if -10 < dist_to_high < 0:
    print(f'  🔴 距历史高点 {dist_to_high:.1f}% — 上方套牢盘压力大')

if total_penalty > 0:
    print(f'\n  结论: {abs(total_penalty)}%的负面因子 > 正面因子，失败概率 > 50%')
else:
    print(f'\n  结论: 各项指标健康，失败概率应较低')
