"""回测验证：用新评分重新计算87信号，检查评分倒挂是否修复"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

import pandas as pd
import numpy as np
import importlib.util

# 加载新的detect函数
spec = importlib.util.spec_from_file_location("vms", r"d:\mystock\solo\vol_ma_sync_surge_scan.py")
vms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vms)

# 读取月分析数据
sig_df = pd.read_excel(r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx")
for col in ['T+5_win', 'T+5_maxup_win', 'T+10_win', 'T+10_maxup_win']:
    if col in sig_df.columns:
        sig_df[col] = sig_df[col].astype(bool)

valid = sig_df[sig_df['T+5_chg'].notna()].copy()
print(f"样本数: {len(valid)}")

# 用新评分系统重新评分
new_scores = []
for idx, row in valid.iterrows():
    # 构造虚拟的输入数据（用现有字段还原）
    # 注意：detect_vol_ma_sync_surge需要完整df，这里我们直接用特征重算评分
    vol_surge_ratio = row['vol_surge']
    ma20_slope_5d = row['ma20_slope_5d']
    ma20_slope_pre = row['ma20_slope_pre']
    macd_status = row['macd_status']
    dist_ma20 = row['dist_ma20']
    vol_price_coord = row['vol_price_coord']
    last_chg = row['last_chg']
    
    # 假设DIF在零轴上方（多数情况）
    last_dif = 1
    
    score = 0
    # 1. 量能放大倍数
    if vol_surge_ratio >= 2.5: score += 20
    elif vol_surge_ratio >= 2.0: score += 15
    elif vol_surge_ratio >= 1.8: score += 10
    elif vol_surge_ratio >= 1.5: score += 8
    
    # 2. MA20前段横盘 + 后段突破斜率
    consolidation_quality = max(0, 3 - abs(ma20_slope_pre))
    if ma20_slope_5d < 3:
        breakout_score = 12
    elif ma20_slope_5d < 5:
        breakout_score = 6
    else:
        breakout_score = 3
    score += int(consolidation_quality * 3 + breakout_score)
    
    # 3. MACD状态
    if macd_status == "刚刚红柱": score += 15
    elif macd_status == "红柱放大": score += 12
    if last_dif > 0: score += 5
    
    # 4. 量价配合度
    if 1.5 <= vol_price_coord < 2.0: score += 15
    elif vol_price_coord >= 2.0: score += 12
    elif vol_price_coord >= 1.2: score += 8
    else: score += 10
    
    # 5. 距MA20
    if 12 <= dist_ma20 <= 14: score += 15
    elif 10 <= dist_ma20 < 12: score += 12
    elif 14 < dist_ma20 <= 15: score += 8
    elif 5 <= dist_ma20 < 8: score += 7
    else: score += 5
    
    # 6. 当日涨幅
    if 5 <= last_chg <= 7: score += 12
    elif 7 < last_chg <= 10: score += 10
    elif 3 <= last_chg < 5: score += 7
    elif 1 <= last_chg < 3: score += 8
    else: score += 6
    
    new_scores.append(score)

valid['new_score'] = new_scores

# 对比新旧评分的倒挂情况
print("\n" + "=" * 60)
print("【新旧评分 vs 止盈胜率对比】")
print("=" * 60)

print(f"\n--- 旧评分（score）分箱 ---")
print(f"{'评分区间':<15}{'信号数':<8}{'止盈胜率':<10}{'均收益':<10}")
for sr in [(75, 80), (80, 85), (85, 90), (90, 100)]:
    mask = (valid['score'] >= sr[0]) & (valid['score'] < sr[1])
    sub = valid[mask]
    if len(sub) > 0:
        wr = sub['T+5_maxup_win'].mean() * 100
        avg = sub['T+5_chg'].mean()
        print(f"[{sr[0]}-{sr[1]})    {len(sub):<8}{wr:.1f}%      {avg:+.2f}%")

print(f"\n--- 新评分（new_score）分箱 ---")
print(f"{'评分区间':<15}{'信号数':<8}{'止盈胜率':<10}{'均收益':<10}")
# 动态确定区间
ns_min, ns_max = valid['new_score'].min(), valid['new_score'].max()
print(f"新评分范围: {ns_min} - {ns_max}")
bins = [(60, 70), (70, 75), (75, 80), (80, 85), (85, 100)]
for sr in bins:
    mask = (valid['new_score'] >= sr[0]) & (valid['new_score'] < sr[1])
    sub = valid[mask]
    if len(sub) > 0:
        wr = sub['T+5_maxup_win'].mean() * 100
        avg = sub['T+5_chg'].mean()
        print(f"[{sr[0]}-{sr[1]})    {len(sub):<8}{wr:.1f}%      {avg:+.2f}%")

# 相关性对比
corr_old = valid['score'].corr(valid['T+5_maxup_win'], method='spearman')
corr_new = valid['new_score'].corr(valid['T+5_maxup_win'], method='spearman')
print(f"\n旧评分 vs 止盈胜率 相关性: {corr_old:+.3f}")
print(f"新评分 vs 止盈胜率 相关性: {corr_new:+.3f}")

# 新评分最优组合验证
print("\n" + "=" * 60)
print("【新评分最优组合验证】")
print("=" * 60)

# 排除7月
mask_jul = ~valid['date'].astype(str).str.startswith('202607')
sub_jul = valid[mask_jul]
if len(sub_jul) > 0:
    # 新评分>=75
    sub_hi = sub_jul[sub_jul['new_score'] >= 75]
    if len(sub_hi) > 0:
        wr = sub_hi['T+5_maxup_win'].mean() * 100
        avg = sub_hi['T+5_chg'].mean()
        print(f"排除7月+新评分>=75: {len(sub_hi)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")

# 新评分>=80
sub_80 = valid[valid['new_score'] >= 80]
if len(sub_80) > 0:
    wr = sub_80['T+5_maxup_win'].mean() * 100
    avg = sub_80['T+5_chg'].mean()
    print(f"新评分>=80: {len(sub_80)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")

# 新评分>=85
sub_85 = valid[valid['new_score'] >= 85]
if len(sub_85) > 0:
    wr = sub_85['T+5_maxup_win'].mean() * 100
    avg = sub_85['T+5_chg'].mean()
    print(f"新评分>=85: {len(sub_85)}只, 止盈胜率={wr:.1f}%, 均收益={avg:+.2f}%")

# 信立泰验证（20260715）
print("\n--- 信立泰(002294)验证 ---")
xinliatai = valid[valid['code'].str.startswith('002294')]
if len(xinliatai) > 0:
    for _, row in xinliatai.iterrows():
        print(f"  日期:{row['date']}, 旧评分:{row['score']}, 新评分:{row['new_score']}, T+5收益:{row['T+5_chg']:+.2f}%")
else:
    print("  信立泰不在已验证样本中（可能未到T+5期）")
    # 查全量
    xl = sig_df[sig_df['code'].str.startswith('002294')]
    if len(xl) > 0:
        for _, row in xl.iterrows():
            print(f"  日期:{row['date']}, 旧评分:{row['score']}, T+5收益:{row.get('T+5_chg', '未到期')}")
