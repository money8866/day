"""查询7月所有信号详情"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

import pandas as pd
import numpy as np

sig_df = pd.read_excel(r"d:\mystock\cache_daily\VolMaSync_MonthAnalysis.xlsx")
for col in ['T+5_win', 'T+5_maxup_win', 'T+10_win', 'T+10_maxup_win']:
    if col in sig_df.columns:
        sig_df[col] = sig_df[col].astype(bool)

# 筛选7月
sig_df['date_str'] = sig_df['date'].astype(str)
july = sig_df[sig_df['date_str'].str.startswith('202607')].copy()
print(f"7月信号总数: {len(july)}")
print(f"7月日期分布:")
print(july['date_str'].value_counts().sort_index())

# 按日期+评分排序
july = july.sort_values(['date', 'score'], ascending=[True, False])

print("\n" + "=" * 120)
print("【7月信号明细】")
print("=" * 120)
print(f"{'日期':<10}{'代码':<12}{'名称':<10}{'评分':<6}{'量能放大':<8}{'MA20斜率5d':<12}{'MACD':<10}{'距MA20':<8}{'量价配合':<8}{'当日涨幅':<8}{'T+5收益':<10}{'T+5最大涨':<10}{'T+5最大回撤':<10}{'止盈':<5}")
print("-" * 150)

for _, row in july.iterrows():
    t5_chg = f"{row['T+5_chg']:+.2f}%" if pd.notna(row['T+5_chg']) else "未到期"
    t5_maxup = f"{row['T+5_maxup']:+.2f}%" if pd.notna(row.get('T+5_maxup', None)) else "-"
    t5_maxdd = f"{row['T+5_maxdd']:+.2f}%" if pd.notna(row.get('T+5_maxdd', None)) else "-"
    win = "✓" if row.get('T+5_maxup_win', False) else ("✗" if pd.notna(row['T+5_chg']) else "-")
    print(f"{row['date_str']:<10}{row['code']:<12}{str(row['name'])[:8]:<10}{row['score']:<6}{row['vol_surge']:<8.2f}"
          f"{row['ma20_slope_5d']:<+6.2f}/{row['ma20_slope_pre']:<+5.2f}  "
          f"{str(row['macd_status']):<10}{row['dist_ma20']:<+6.2f}%  {row['vol_price_coord']:<6.2f}    "
          f"{row['last_chg']:<+6.2f}%  {t5_chg:<10}{t5_maxup:<10}{t5_maxdd:<10}{win:<5}")

# 汇总统计
print("\n" + "=" * 60)
print("【7月信号汇总统计】")
print("=" * 60)
valid_july = july[july['T+5_chg'].notna()]
if len(valid_july) > 0:
    print(f"已验证T+5: {len(valid_july)}只")
    print(f"  最终涨幅>=3%胜率: {valid_july['T+5_win'].sum()}/{len(valid_july)} = {valid_july['T+5_win'].sum()/len(valid_july)*100:.1f}%")
    print(f"  动态止盈胜率: {valid_july['T+5_maxup_win'].sum()}/{len(valid_july)} = {valid_july['T+5_maxup_win'].sum()/len(valid_july)*100:.1f}%")
    print(f"  平均T+5收益: {valid_july['T+5_chg'].mean():+.2f}%")
    print(f"  平均最大涨幅: {valid_july['T+5_maxup'].mean():+.2f}%")
    print(f"  平均最大回撤: {valid_july['T+5_maxdd'].mean():+.2f}%")
    
    # 盈亏统计
    wins = valid_july[valid_july['T+5_chg'] > 0]['T+5_chg']
    losses = valid_july[valid_july['T+5_chg'] < 0]['T+5_chg']
    print(f"\n  盈利: {len(wins)}只, 平均{wins.mean() if len(wins)>0 else 0:+.2f}%")
    print(f"  亏损: {len(losses)}只, 平均{losses.mean() if len(losses)>0 else 0:+.2f}%")

# 按日期分组
print("\n" + "=" * 60)
print("【7月按日期分组】")
print("=" * 60)
print(f"{'日期':<12}{'信号数':<8}{'已验证':<8}{'止盈胜率':<10}{'平均收益':<10}")
print("-" * 50)
for date in sorted(july['date_str'].unique()):
    sub = july[july['date_str'] == date]
    sub_valid = sub[sub['T+5_chg'].notna()]
    if len(sub_valid) > 0:
        wr = sub_valid['T+5_maxup_win'].sum() / len(sub_valid) * 100
        avg = sub_valid['T+5_chg'].mean()
        print(f"{date:<12}{len(sub):<8}{len(sub_valid):<8}{wr:.0f}%      {avg:+.2f}%")
    else:
        print(f"{date:<12}{len(sub):<8}未到期")

# 表现最好和最差
print("\n" + "=" * 60)
print("【7月表现最好Top5】")
print("=" * 60)
top5 = valid_july.nlargest(5, 'T+5_chg')[['date','code','name','score','T+5_chg','T+5_maxup']]
for _, r in top5.iterrows():
    print(f"  {r['date']} {r['code']} {r['name'][:8]} 评分{r['score']} T+5收益{r['T+5_chg']:+.2f}% 最大涨{r['T+5_maxup']:+.2f}%")

print("\n【7月表现最差Bottom5】")
bot5 = valid_july.nsmallest(5, 'T+5_chg')[['date','code','name','score','T+5_chg','T+5_maxdd']]
for _, r in bot5.iterrows():
    print(f"  {r['date']} {r['code']} {r['name'][:8]} 评分{r['score']} T+5收益{r['T+5_chg']:+.2f}% 最大回撤{r['T+5_maxdd']:+.2f}%")
