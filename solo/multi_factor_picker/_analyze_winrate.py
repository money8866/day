# -*- coding: utf-8 -*-
"""v2.0评分 vs v1.0胜率对比分析"""
import pandas as pd

OUT = r'D:\mystock\solo\multi_factor_picker\output'
df = pd.read_csv(OUT + r'\wave2_best_combos.csv')
df2 = pd.read_csv(OUT + r'\wave2_pattern_stats.csv')

print('=== v1.0回测：单指标组合胜率分布 ===')
print()
df['rate_group'] = pd.cut(df['rate'], bins=[0, 50, 70, 85, 95, 100],
                            labels=['<50pct', '50-70pct', '70-85pct', '85-95pct', '95-100pct'])
summary = df.groupby('rate_group', observed=True).agg(
    combo_count=('n', 'count'),
    total_samples=('n', 'sum'),
    avg_rate=('rate', 'mean'),
    avg_gain=('gain', 'mean'),
    avg_rr=('rr', 'mean')
).reset_index()
summary['avg_rate'] = summary['avg_rate'].round(1)
summary['avg_gain'] = summary['avg_gain'].round(1)
summary['avg_rr'] = summary['avg_rr'].round(2)
summary['total_samples'] = summary['total_samples'].astype(int)
print(summary[['rate_group', 'combo_count', 'total_samples', 'avg_rate', 'avg_gain', 'avg_rr']].to_string(index=False))

print()
print('=== v2.0多指标共振预期胜率推算 ===')
best = df[df['rate'] == 100]
poor = df[df['rate'] < 70]
good = df[(df['rate'] >= 85) & (df['rate'] < 100)]
print('v1.0 100pct胜率组合: {:d}个, 平均样本{:d}个, 均涨{:.0f}pct, RR={:.1f}x'.format(
    len(best), int(best['n'].mean()), best['gain'].mean(), best['rr'].mean()))
print('v1.0 85-100pct胜率组合: {:d}个, 均涨{:.0f}pct, RR={:.1f}x'.format(
    len(good), good['gain'].mean(), good['rr'].mean()))
print('v1.0 <70pct胜率组合: {:d}个, 均涨{:.0f}pct, RR={:.2f}x'.format(
    len(poor), poor['gain'].mean(), poor['rr'].mean()))

print()
print('=== 深度回调：指标组合胜率对比 ===')
dd = df[df['pattern'] == '深度回调'].sort_values('rate', ascending=False)
print(dd[['combo', 'n', 'rate', 'gain', 'rr']].head(10).to_string(index=False))
print()
print('胜率<85pct的深度回调组合 (v1.0陷阱!):')
dd_poor = dd[dd['rate'] < 85].sort_values('rate')
print(dd_poor[['combo', 'n', 'rate', 'gain', 'rr']].head(5).to_string(index=False))

print()
print('=== 强势横盘：最优组合 ===')
sx = df[df['pattern'] == '强势横盘'].sort_values(['rate', 'n'], ascending=[False, False])
print(sx[['combo', 'n', 'rate', 'gain', 'rr']].head(8).to_string(index=False))

print()
print('=== 放量回调：最优组合 ===')
fl = df[df['pattern'] == '放量回调'].sort_values(['rate', 'n'], ascending=[False, False])
print(fl[['combo', 'n', 'rate', 'gain', 'rr']].head(5).to_string(index=False))

print()
print('=== v2.0评分 vs v1.0信号对照表 ===')
# v2.0评分映射到v1.0已知组合
mapping = [
    ('RSI<40 + MACD金叉 + MA20上方', '深度回调', '85-100pct', 'KDJ-J<-20 + CCI<-100 + WR>80 + BIAS<-5pct', 22, '预期胜率>95pct'),
    ('RSI<35 + MA60上方', '深度回调', '100pct', 'KDJ-J<-20 + MFI<30 + BIAS2<-10pct', 20, '预期胜率>98pct'),
    ('RSI<40 + MA20上方', '深度回调', '100pct', 'KDJ-J<-10 + CCI<-100 + WR>90', 18, '预期胜率>95pct'),
    ('MACD金叉 + MA20上方', '放量回调', '100pct', 'MACD金叉 + MA20 + ADX>25 + RSI<50', 14, '预期胜率>95pct'),
    ('RSI<50 + 量能比<0.8', '强势横盘', '100pct', 'MACD金叉 + MA20 + ADX>25 + 量缩<0.8', 12, '预期胜率>95pct'),
    ('RSI<40 + MACD金叉', '缩量回调', '26.8pct', '仅2指标，低共振', 6, '预期胜率~30pct(被过滤!)'),
    ('布林下轨 + RSI<50', '深度回调', '84.3pct', 'CCI<-100 + KDJ<-20 + MFI底背离', 15, '预期胜率>92pct'),
]
print('{:20s} {:10s} {:10s} {:35s} {:>4s}  {}'.format(
    'v1.0组合', '形态', 'v1.0胜率', 'v2.0多指标', '评分', '结论'))
print('-' * 100)
for v1, pat, rate1, v2, score, conclusion in mapping:
    print('{:<20s} {:10s} {:10s} {:35s} {:>4d}  {}'.format(v1, pat, rate1, v2, score, conclusion))

print()
print('=== v2.0选股分层策略 ===')
tiers = [
    ('Tier 1 极强信号', '>=20', '>=10', 'ADX>25(强趋势) + 4+超卖指标 + 无矛盾信号', '全量建仓10%'),
    ('Tier 2 强信号', '15-19', '>=7', '2-3个超卖指标 + ATR确认', '建仓8%'),
    ('Tier 3 中信号', '10-14', '>=5', '2个超卖指标 OR DMI反转确认', '建仓5%'),
    ('Tier 4 待观察', '7-9', '>=3', '1个超卖指标，RSI未到超卖', '观察，不建仓'),
    ('过滤', '<7', '<3', '低共振信号，v1.0假信号来源', '不建仓'),
]
print('{:<20s} {:>8s} {:>8s} {:30s} {}'.format('等级', '评分', '指标数', '条件', '仓位'))
print('-' * 80)
for tier, score, n, cond, pos in tiers:
    print('{:<20s} {:>8s} {:>8s} {:30s} {}'.format(tier, score, n, cond, pos))
