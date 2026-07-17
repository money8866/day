"""分析各因子与胜率的相关性，用于重新校准评分权重"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

import pandas as pd
import numpy as np

sig_df = pd.read_excel(r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx")
for col in ['T+5_win', 'T+5_maxup_win', 'T+10_win', 'T+10_maxup_win']:
    if col in sig_df.columns:
        sig_df[col] = sig_df[col].astype(bool)

valid = sig_df[sig_df['T+5_chg'].notna()].copy()
print(f"样本数: {len(valid)}")

# 列名确认
print(f"\n列名: {list(valid.columns)}")

# 分析连续因子与T+5胜率(动态止盈)的相关性
print("\n" + "=" * 60)
print("【连续因子与T+5动态止盈胜率的相关性（Spearman）】")
print("=" * 60)
continuous_factors = ['vol_surge', 'ma20_slope_5d', 'ma20_slope_pre',
                     'dist_ma20', 'vol_price_coord', 'last_vol_ratio',
                     'last_chg', 'score']
for f in continuous_factors:
    if f in valid.columns:
        corr_maxup = valid[f].corr(valid['T+5_maxup_win'], method='spearman')
        corr_chg = valid[f].corr(valid['T+5_chg'], method='spearman')
        print(f"  {f:<20} vs 止盈胜率: {corr_maxup:+.3f}   vs T+5收益: {corr_chg:+.3f}")

# 分析分类因子
print("\n" + "=" * 60)
print("【分类因子与T+5动态止盈胜率】")
print("=" * 60)

# MACD状态
print("\n--- macd_status ---")
for v in valid['macd_status'].dropna().unique():
    sub = valid[valid['macd_status'] == v]
    if len(sub) > 0:
        wr = sub['T+5_maxup_win'].mean() * 100
        avg = sub['T+5_chg'].mean()
        print(f"  {v:<12}: {len(sub)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")

# DIF是否在零轴上方
print("\n--- dif_above_zero ---")
if 'dif_above_zero' in valid.columns:
    for v in valid['dif_above_zero'].dropna().unique():
        sub = valid[valid['dif_above_zero'] == v]
        if len(sub) > 0:
            wr = sub['T+5_maxup_win'].mean() * 100
            avg = sub['T+5_chg'].mean()
            print(f"  DIF在零轴{'上方' if v else '下方'}: {len(sub)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")
else:
    print("  (月分析数据无 dif_above_zero 列)")

# 分箱分析各连续因子
def bin_analysis(factor, bins, labels=None):
    print(f"\n--- {factor} 分箱 ---")
    if labels is None:
        labels = [f"[{b[0]},{b[1]})" for b in bins]
    valid['bin'] = pd.cut(valid[factor], bins=[b[0] for b in bins] + [bins[-1][1]],
                         labels=labels, include_lowest=True)
    for lab in labels:
        sub = valid[valid['bin'] == lab]
        if len(sub) > 0:
            wr = sub['T+5_maxup_win'].mean() * 100
            avg = sub['T+5_chg'].mean()
            print(f"  {lab:<15}: {len(sub)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")

bin_analysis('vol_surge', [(1.5, 1.8), (1.8, 2.0), (2.0, 2.5), (2.5, 5.0)])
bin_analysis('dist_ma20', [(5, 8), (8, 10), (10, 12), (12, 14), (14, 15.1)])
bin_analysis('vol_price_coord', [(0.95, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 10.0)])
bin_analysis('last_chg', [(0, 1), (1, 3), (3, 5), (5, 7), (7, 10), (10, 20)])
bin_analysis('ma20_slope_5d', [(2, 3), (3, 5), (5, 8), (8, 20)])
if 'last_vol_ratio' in valid.columns:
    bin_analysis('last_vol_ratio', [(1.0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 10)])

# 综合评分校准建议
print("\n" + "=" * 60)
print("【评分校准建议】")
print("=" * 60)
print("基于上述分箱胜率，建议权重调整方向：")
print("  - 正向因子（胜率随值增大而升高）：应加分")
print("  - 反向因子（胜率随值增大而降低）：应减分或反向加权")
