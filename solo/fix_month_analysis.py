"""快速修正胜率统计Bug并重新分析"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

import pandas as pd
import numpy as np

# 读取已保存的数据
sig_df = pd.read_excel(r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx")
print(f"信号总数: {len(sig_df)}")

# 修正数据类型
for col in ['T+5_win', 'T+5_maxup_win', 'T+10_win', 'T+10_maxup_win']:
    if col in sig_df.columns:
        sig_df[col] = sig_df[col].astype(bool)

valid_t5 = sig_df[sig_df['T+5_chg'].notna()].copy()
valid_t10 = sig_df[sig_df['T+10_chg'].notna()].copy()

print(f"T+5已验证: {len(valid_t5)}只, T+10已验证: {len(valid_t10)}只")

# ============ 1. 总体胜率（修正后）============
print("\n" + "=" * 60)
print("【1. 总体胜率（修正后）】")
print("=" * 60)

if len(valid_t5) > 0:
    t5_win = int(valid_t5['T+5_win'].sum())
    t5_maxup_win = int(valid_t5['T+5_maxup_win'].sum())
    t5_flat = int((valid_t5['T+5_chg'] > 0).sum())
    
    print(f"\n--- T+5统计（{len(valid_t5)}只已验证）---")
    print(f"  最终涨幅>=3%: {t5_win}/{len(valid_t5)} = {t5_win/len(valid_t5)*100:.1f}%")
    print(f"  动态止盈(最大涨幅>=3%): {t5_maxup_win}/{len(valid_t5)} = {t5_maxup_win/len(valid_t5)*100:.1f}%")
    print(f"  平盘以上(涨幅>0): {t5_flat}/{len(valid_t5)} = {t5_flat/len(valid_t5)*100:.1f}%")
    print(f"  平均涨幅: {valid_t5['T+5_chg'].mean():+.2f}%")
    print(f"  中位数: {valid_t5['T+5_chg'].median():+.2f}%")
    print(f"  最大涨幅均值: {valid_t5['T+5_maxup'].mean():+.2f}%")
    print(f"  最大回撤均值: {valid_t5['T+5_maxdd'].mean():+.2f}%")

if len(valid_t10) > 0:
    t10_win = int(valid_t10['T+10_win'].sum())
    t10_maxup_win = int(valid_t10['T+10_maxup_win'].sum())
    print(f"\n--- T+10统计（{len(valid_t10)}只已验证）---")
    print(f"  最终涨幅>=3%: {t10_win}/{len(valid_t10)} = {t10_win/len(valid_t10)*100:.1f}%")
    print(f"  动态止盈(最大涨幅>=3%): {t10_maxup_win}/{len(valid_t10)} = {t10_maxup_win/len(valid_t10)*100:.1f}%")
    print(f"  平均涨幅: {valid_t10['T+10_chg'].mean():+.2f}%")

# ============ 2. 按日期分组（修正后）============
print("\n" + "=" * 60)
print("【2. 按日期分组（修正后）】")
print("=" * 60)
print(f"\n{'日期':<12}{'信号数':<8}{'验证':<6}{'最终胜率':<14}{'止盈胜率':<14}{'均收益':<10}")
print("-" * 70)

for date in sorted(sig_df['date'].unique(), reverse=True):
    sub = sig_df[sig_df['date'] == date]
    sub_valid = sub[sub['T+5_chg'].notna()]
    if len(sub_valid) > 0:
        win_w = int(sub_valid['T+5_win'].sum())
        maxup_w = int(sub_valid['T+5_maxup_win'].sum())
        avg = sub_valid['T+5_chg'].mean()
        print(f"{date:<12}{len(sub):<8}{len(sub_valid):<6}"
              f"{win_w}/{len(sub_valid)}={win_w/len(sub_valid)*100:>4.0f}%      "
              f"{maxup_w}/{len(sub_valid)}={maxup_w/len(sub_valid)*100:>4.0f}%      "
              f"{avg:+.2f}%")
    else:
        print(f"{date:<12}{len(sub):<8}未到期")

# ============ 3. 按评分分组（修正后）============
print("\n" + "=" * 60)
print("【3. 按评分分组（修正后）】")
print("=" * 60)
if len(valid_t5) > 0:
    print(f"\n{'评分区间':<15}{'信号数':<8}{'最终胜率':<14}{'止盈胜率':<14}{'均收益':<10}")
    print("-" * 70)
    for sr in [(75, 80), (80, 85), (85, 90), (90, 100)]:
        mask = (valid_t5['score'] >= sr[0]) & (valid_t5['score'] < sr[1])
        sub = valid_t5[mask]
        if len(sub) > 0:
            win_w = int(sub['T+5_win'].sum())
            maxup_w = int(sub['T+5_maxup_win'].sum())
            avg = sub['T+5_chg'].mean()
            print(f"[{sr[0]}-{sr[1]})    {len(sub):<8}"
                  f"{win_w}/{len(sub)}={win_w/len(sub)*100:>4.0f}%      "
                  f"{maxup_w}/{len(sub)}={maxup_w/len(sub)*100:>4.0f}%      "
                  f"{avg:+.2f}%")

# ============ 4. 按月对比 ============
print("\n" + "=" * 60)
print("【4. 6月 vs 7月对比】")
print("=" * 60)
sig_df['month'] = sig_df['date'].astype(str).str[:6]
for month in ['202606', '202607']:
    sub = valid_t5[valid_t5['date'].astype(str).str.startswith(month)]
    if len(sub) > 0:
        win_w = int(sub['T+5_win'].sum())
        maxup_w = int(sub['T+5_maxup_win'].sum())
        print(f"\n{month}月:")
        print(f"  信号数: {len(sub)}")
        print(f"  最终胜率: {win_w}/{len(sub)} = {win_w/len(sub)*100:.1f}%")
        print(f"  止盈胜率: {maxup_w}/{len(sub)} = {maxup_w/len(sub)*100:.1f}%")
        print(f"  平均收益: {sub['T+5_chg'].mean():+.2f}%")
        print(f"  最大涨幅均值: {sub['T+5_maxup'].mean():+.2f}%")
        print(f"  最大回撤均值: {sub['T+5_maxdd'].mean():+.2f}%")

# ============ 5. 盈亏比 ============
print("\n" + "=" * 60)
print("【5. 盈亏统计（修正后）】")
print("=" * 60)
if len(valid_t5) > 0:
    wins = valid_t5[valid_t5['T+5_chg'] > 0]['T+5_chg']
    losses = valid_t5[valid_t5['T+5_chg'] < 0]['T+5_chg']
    print(f"\n盈利次数: {len(wins)}, 平均盈利: {wins.mean() if len(wins)>0 else 0:+.2f}%")
    print(f"亏损次数: {len(losses)}, 平均亏损: {losses.mean() if len(losses)>0 else 0:+.2f}%")
    if len(wins) > 0 and len(losses) > 0:
        win_loss_ratio = wins.mean() / abs(losses.mean())
        print(f"盈亏比: {win_loss_ratio:.2f}")
        # 期望值
        win_rate = len(wins) / len(valid_t5)
        ev = win_rate * wins.mean() + (1 - win_rate) * losses.mean()
        print(f"胜率: {win_rate*100:.1f}%")
        print(f"期望值(每次): {ev:+.2f}%")

# ============ 6. 最优组合 ============
print("\n" + "=" * 60)
print("【6. 最优组合探索】")
print("=" * 60)

# 排除7月
mask_jul = ~valid_t5['date'].astype(str).str.startswith('202607')
sub_jul = valid_t5[mask_jul]
if len(sub_jul) > 0:
    win_w = int(sub_jul['T+5_win'].sum())
    maxup_w = int(sub_jul['T+5_maxup_win'].sum())
    print(f"\n排除7月: {len(sub_jul)}只, 最终胜率={win_w/len(sub_jul)*100:.1f}%, 止盈胜率={maxup_w/len(sub_jul)*100:.1f}%, 均={sub_jul['T+5_chg'].mean():+.2f}%")

# 距MA20[10-14]
mask_dist = (valid_t5['dist_ma20'] >= 10) & (valid_t5['dist_ma20'] <= 14)
sub_dist = valid_t5[mask_dist]
if len(sub_dist) > 0:
    win_w = int(sub_dist['T+5_win'].sum())
    maxup_w = int(sub_dist['T+5_maxup_win'].sum())
    print(f"距MA20[10-14]: {len(sub_dist)}只, 最终胜率={win_w/len(sub_dist)*100:.1f}%, 止盈胜率={maxup_w/len(sub_dist)*100:.1f}%, 均={sub_dist['T+5_chg'].mean():+.2f}%")

# 排除7月 + 距MA20[10-14]
mask_both = mask_jul & mask_dist
sub_both = valid_t5[mask_both]
if len(sub_both) > 0:
    win_w = int(sub_both['T+5_win'].sum())
    maxup_w = int(sub_both['T+5_maxup_win'].sum())
    print(f"排除7月+距MA20[10-14]: {len(sub_both)}只, 最终胜率={win_w/len(sub_both)*100:.1f}%, 止盈胜率={maxup_w/len(sub_both)*100:.1f}%, 均={sub_both['T+5_chg'].mean():+.2f}%")

# 当日涨幅3-7%
mask_chg = (valid_t5['last_chg'] >= 3) & (valid_t5['last_chg'] <= 7)
sub_chg = valid_t5[mask_chg]
if len(sub_chg) > 0:
    win_w = int(sub_chg['T+5_win'].sum())
    maxup_w = int(sub_chg['T+5_maxup_win'].sum())
    print(f"当日涨幅[3-7%]: {len(sub_chg)}只, 最终胜率={win_w/len(sub_chg)*100:.1f}%, 止盈胜率={maxup_w/len(sub_chg)*100:.1f}%, 均={sub_chg['T+5_chg'].mean():+.2f}%")

# MACD刚刚红柱
mask_macd = valid_t5['macd_status'] == '刚刚红柱'
sub_macd = valid_t5[mask_macd]
if len(sub_macd) > 0:
    win_w = int(sub_macd['T+5_win'].sum())
    maxup_w = int(sub_macd['T+5_maxup_win'].sum())
    print(f"MACD刚刚红柱: {len(sub_macd)}只, 最终胜率={win_w/len(sub_macd)*100:.1f}%, 止盈胜率={maxup_w/len(sub_macd)*100:.1f}%, 均={sub_macd['T+5_chg'].mean():+.2f}%")
