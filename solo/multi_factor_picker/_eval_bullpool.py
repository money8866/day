# -*- coding: utf-8 -*-
"""BullScore合格池质量评估"""
import pandas as pd
import numpy as np

csv = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
df = pd.read_csv(csv)
n = len(df)
scores = df['最终分']

# ═══════════════════════════════════════════════════
# 1. 历史验证：60日收益分布
# ═══════════════════════════════════════════════════
print("=" * 65)
print("一、近期表现验证（60日收益%）")
print("=" * 65)

ret = pd.to_numeric(df.get('60日收益%', pd.Series(dtype=float)), errors='coerce')
ret_valid = ret.dropna()
print(f"有效样本: {len(ret_valid)}/{n}")

if len(ret_valid) > 100:
    # 整体
    print(f"\n整体: 均值{ret_valid.mean():.1f}% | 中位{ret_valid.median():.1f}% | 最高{ret_valid.max():.0f}% | 最低{ret_valid.min():.0f}%")
    print(f"正收益比例: {(ret_valid>0).mean()*100:.1f}%")
    print(f"跑赢10%: {(ret_valid>10).mean()*100:.1f}% | 跑赢20%: {(ret_valid>20).mean()*100:.1f}%")
    print(f"亏损>10%: {(ret_valid<-10).mean()*100:.1f}%")

    # 按评分分层
    print(f"\n按评分分层60日收益：")
    for lo, hi, label in [(0, 70, '<70分'), (70, 75, '70-75分'), (75, 80, '75-80分'), (80, 85, '80-85分'), (85, 100, '85分+')]:
        s = ret_valid[(scores >= lo) & (scores < hi)]
        if len(s) > 10:
            wr = (s > 0).mean() * 100
            wr10 = (s > 10).mean() * 100
            print(f"  {label}: {len(s):>3d}只 | 均值{s.mean():+5.1f}% | 中位{s.median():+5.1f}% | 正收益{wr:5.1f}% | >10%: {wr10:5.1f}%")

# ═══════════════════════════════════════════════════
# 2. 财务质量
# ═══════════════════════════════════════════════════
print(f"\n{'='*65}")
print("二、财务质量概览")
print("=" * 65)

for field, label in [('营收同比', '营收同比%'), ('利润同比', '利润同比%'), ('ROE', 'ROE%'), ('毛利率', '毛利率%'), ('研发投入%', '研发投入%')]:
    col = df.get(field)
    if col is None:
        col = df.get(label)
    if col is None:
        continue
    valid = pd.to_numeric(col, errors='coerce').dropna()
    if len(valid) < 50:
        continue
    print(f"  {label}: 均值{valid.mean():.1f} | 中位{valid.median():.1f} | >0: {(valid>0).mean()*100:.0f}% | 可用{len(valid)}只")

# 三因子分布
print(f"\n得分因子分布：")
for field in ['产业景气', '技术壁垒', '订单爆发', '业绩质量', '龙头地位', '预期差', '机构认可', '市值弹性', '估值安全', '筹码面']:
    col = df.get(field)
    if col is None:
        continue
    valid = pd.to_numeric(col, errors='coerce').dropna()
    if len(valid) < 50:
        continue
    print(f"  {field}: 均值{valid.mean():.1f} | 中位{valid.median():.1f} | 范围{valid.min():.1f}~{valid.max():.1f}")

# ═══════════════════════════════════════════════════
# 3. 评分可信度分析
# ═══════════════════════════════════════════════════
print(f"\n{'='*65}")
print("三、评分可信度分析")
print("=" * 65)

# 排名分散度 - 高分应该更多集中在高质量标的
top10 = df.sort_values('最终分', ascending=False).head(10)
print(f"TOP10评分: {top10['最终分'].mean():.1f} | 评分差距: {top10['最终分'].max()-top10['最终分'].min():.1f}分")

# 评分层次合理性 - 看高分组的财务指标是否更好
for metric, mlabel in [('营收同比', '营收同比%'), ('利润同比', '利润同比%'), ('ROE', 'ROE%')]:
    col = df.get(metric) or df.get(mlabel)
    if col is None:
        continue
    print(f"\n  {mlabel}按评分分层：")
    for lo, hi, label in [(0, 70, '<70分'), (70, 75, '70-75'), (75, 80, '75-80'), (80, 100, '80分+')]:
        sub = df[(scores >= lo) & (scores < hi)]
        vals = pd.to_numeric(sub.get(metric, pd.Series(dtype=float)) if metric in df.columns else sub.get(mlabel, pd.Series(dtype=float)), errors='coerce').dropna()
        if len(vals) > 10:
            print(f"    {label}: 均值{vals.mean():+.1f} (n={len(vals)})", end='')
            # 与基准层('<70')比较
            base = pd.to_numeric(df[df['最终分'] < 70].get(metric, pd.Series(dtype=float)), errors='coerce').dropna()
            if len(base) > 10:
                print(f" | 超基准: {vals.mean()-base.mean():+.1f}")
            else:
                print()

# ═══════════════════════════════════════════════════
# 4. 行业集中度风险
# ═══════════════════════════════════════════════════
print(f"\n{'='*65}")
print("四、行业集中度")
print("=" * 65)
ind = df['industry'].value_counts()
print(f"覆盖行业: {len(ind)}个")
print(f"TOP3行业占比: {ind.head(3).sum()/n*100:.1f}%")
print(f"TOP5行业占比: {ind.head(5).sum()/n*100:.1f}%")
print(f"TOP10行业占比: {ind.head(10).sum()/n*100:.1f}%")
hhi_val = ((ind/n*100)**2).sum()
print(f"赫芬达尔指数(HHI): {hhi_val:.1f} (越低越分散)")

# 头部行业平均评分
print(f"\nTOP10行业平均评分：")
for ind_name in ind.head(10).index.tolist():
    avg_s = df[df['industry']==ind_name]['最终分'].mean()
    cnt = ind[ind_name]
    print(f"  {ind_name}: {avg_s:.1f}分 ({cnt}只)")

# ═══════════════════════════════════════════════════
# 5. 综合质量打分
# ═══════════════════════════════════════════════════
print(f"\n{'='*65}")
print("五、综合质量评估")
print("=" * 65)

# 正面信号
pos = 0
neg = 0
notes = []

if len(ret_valid) > 100:
    wr = (ret_valid > 0).mean() * 100
    if wr > 55:
        pos += 1
        notes.append(f"✅ 60日正收益比例{wr:.0f}%，趋势方向健康")
    else:
        neg += 1
        notes.append(f"⚠️ 60日正收益比例仅{wr:.0f}%，整体表现偏弱")

if len(df) > 500:
    notes.append(f"✅ 池子规模775只，覆盖面广")
elif len(df) < 200:
    notes.append(f"⚠️ 池子偏窄({len(df)}只)，可能遗漏机会")

hhi = (ind.head(5).sum()/n*100)
if hhi < 40:
    pos += 1
    notes.append(f"✅ TOP5行业占{hhi:.0f}%，分散度合理")
else:
    neg += 1
    notes.append(f"⚠️ TOP5行业占{hhi:.0f}%，集中度偏高")

# 评分分层合理性
for lo, hi, label in [(70, 75, '70-75分'), (80, 100, '80分+')]:
    sub = df[(scores >= lo) & (scores < hi)]
    revs = pd.to_numeric(sub.get('营收同比', pd.Series(dtype=float)), errors='coerce').dropna()
    if len(revs) > 50:
        break

# TOP10回测收益可见性 - 如果有60日收益
if len(ret_valid) > 100:
    top10_ret = pd.to_numeric(df.sort_values('最终分', ascending=False).head(10).get('60日收益%', pd.Series(dtype=float)), errors='coerce').dropna()
    if len(top10_ret) > 5:
        top_wr = (top10_ret > 0).mean() * 100
        print(f"TOP10近60日正收益比例: {top_wr:.0f}%")
        notes.append(f"✅ TOP10评分股正收益{top_wr:.0f}%，评分与走势正相关" if top_wr > 50 else f"⚠️ TOP10评分股正收益仅{top_wr:.0f}%，评分-收益相关性不足")

print()
for n in notes:
    print(n)

print(f"\n{'='*65}")
print(f"综合评级: {'A' if pos > 1 and neg == 0 else 'B' if pos >= neg else 'C'}  (正面{pos} | 负面{neg})")
print("=" * 65)
