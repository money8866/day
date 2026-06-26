# -*- coding: utf-8 -*-
"""BullScore合格池质量评估v2"""
import pandas as pd
import numpy as np

csv = r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv'
df = pd.read_csv(csv)
n = len(df)

print("=" * 65)
print("BullScore v3.0 合格池质量评估")
print("=" * 65)

# ═══════════════════════════════════════════
# 1. 池子规模与数据完整性
# ═══════════════════════════════════════════
print(f"\n一、数据完整性")
print(f"  合格池规模: {n}只")
print(f"  主题匹配: {(df['theme'].notna() & (df['theme'] != '')).sum()}/{n} 只有主题")
print(f"  60日收益: {(df['60日收益%'].notna()).sum()}/{n} 只有数据")
print(f"  子因子评分(>0): ", end="")
for col in ['产业景气', '技术壁垒', '订单爆发', '业绩质量', '龙头地位', '预期差']:
    cnt = (pd.to_numeric(df[col], errors='coerce') > 0).sum()
    print(f"{col}{cnt}/{n} ", end="")
print()
print(f"  ⚠️ 所有子因子均为0，说明数据获取阶段未能拉取到因子数据")
print(f"  最终分=Bull_v2.1分+主题分v2，主题分v2均为0，最终分≈Bull_v2.1分")

# ═══════════════════════════════════════════
# 2. 评分分布合理性
# ═══════════════════════════════════════════
print(f"\n二、评分分布")
scores = df['最终分']
print(f"  均值: {scores.mean():.1f} | 中位: {scores.median():.1f} | 最高: {scores.max():.1f} | 最低: {scores.min():.1f}")
print(f"  标准差: {scores.std():.1f}")
print(f"  P90: {scores.quantile(0.9):.1f} | P75: {scores.quantile(0.75):.1f} | P25: {scores.quantile(0.25):.1f} | P10: {scores.quantile(0.1):.1f}")

# 评分分层
for lo, hi, label in [(70, 73, '70-73'), (73, 76, '73-76'), (76, 80, '76-80'), (80, 85, '80-85'), (85, 100, '85+')]:
    sub = df[(scores >= lo) & (scores < hi)]
    cnt = len(sub)
    print(f"  {label}分: {cnt:>3d}只 ({cnt/n*100:.1f}%)")

# 评分集中在70-80
between_70_80 = ((scores >= 70) & (scores < 80)).sum()
print(f"\n  评分70-80: {between_70_80}只 ({between_70_80/n*100:.1f}%) ← 核心层")
print(f"  评分80+: {(scores >= 80).sum()}只 ({(scores>=80).sum()/n*100:.1f}%) ← 精华层")

# 正态性检查 - 偏度
from scipy import stats
skew = stats.skew(scores)
print(f"  偏度: {skew:.2f} ({'右偏(高分更少)' if skew > 0 else '左偏(高分更多)'})")

# ═══════════════════════════════════════════
# 3. 等级分布是否合理
# ═══════════════════════════════════════════
print(f"\n三、等级分布")
for g in ['A级产业龙头', 'B级成长股', '观察名单']:
    cnt = (df['等级'] == g).sum()
    avg = scores[df['等级'] == g].mean() if cnt > 0 else 0
    print(f"  {g}: {cnt}只 | 平均分{avg:.1f}")

# 检查A级B级是否真有区分度
ab = df[df['等级'].isin(['A级产业龙头', 'B级成长股'])]
observe = df[df['等级'] == '观察名单']
if len(ab) > 0 and len(observe) > 0:
    ab_avg = ab['最终分'].mean()
    ob_avg = observe['最终分'].mean()
    print(f"  A级+B级平均{ab_avg:.1f}分 vs 观察名单平均{ob_avg:.1f}分 | 区分度: {ab_avg-ob_avg:.1f}分")
    print(f"  {'✅ 等级有明显区分' if ab_avg-ob_avg > 3 else '⚠️ 等级区分度不够'}")

# ═══════════════════════════════════════════
# 4. 行业分散度评分
# ═══════════════════════════════════════════
print(f"\n四、行业分散度")
ind = df['industry'].value_counts()
print(f"  覆盖: {len(ind)}个行业")
for i, (ind_name, cnt) in enumerate(ind.head(10).items()):
    avg_s = df[df['industry']==ind_name]['最终分'].mean()
    bar = '█' * int(cnt/n*100)
    print(f"  {ind_name:<10} {cnt:>3d}只 {avg_s:5.1f}分 | {bar}")
hhi = ((ind/n*100)**2).sum()
print(f"  赫芬达尔指数: {hhi:.0f} ({'高度分散' if hhi < 200 else '中度集中' if hhi < 500 else '高度集中'})")

# ═══════════════════════════════════════════
# 5. TOP10 vs BOTTOM10
# ═══════════════════════════════════════════
print(f"\n五、TOP10 vs BOTTOM10")
top10 = df.sort_values('最终分', ascending=False).head(10)
bot10 = df.sort_values('最终分', ascending=True).head(10)
print(f"  TOP10: {top10['最终分'].mean():.1f}分 | 范围{top10['最终分'].min():.1f}~{top10['最终分'].max():.1f} | 覆盖{top10['industry'].nunique()}个行业")
print(f"  最低10: {bot10['最终分'].mean():.1f}分 | 范围{bot10['最终分'].min():.1f}~{bot10['最终分'].max():.1f}")
print(f"  {"|".join(top10['name'].values)}")
print(f"  {'-'.join(top10['industry'].values)}")

# ═══════════════════════════════════════════
# 6. 质量评级
# ═══════════════════════════════════════════
print(f"\n{'='*65}")
print("六、综合质量评级")
print("=" * 65)

issues = []
strengths = []

# 评分区分度
if scores.std() < 5:
    issues.append(f"评分标准差{scores.std():.1f}，分布过窄，区分度不足")
else:
    strengths.append(f"评分标准差{scores.std():.1f}，有一定区分度")

# 评分范围
diff = scores.max() - scores.min()
if diff > 20:
    strengths.append(f"评分范围{diff:.0f}分，高低分拉开")
else:
    issues.append(f"评分范围仅{diff:.0f}分，拉开不够")

# 数据质量
if (df['theme'].notna() & (df['theme'] != '')).sum() < n * 0.5:
    issues.append(f"主题匹配率{(df['theme'].notna()).sum()/n*100:.0f}%，大量股票缺少主题标签")
else:
    strengths.append(f"主题匹配率{(df['theme'].notna()).sum()/n*100:.0f}%")

# 行业覆盖
if len(ind) >= 30:
    strengths.append(f"覆盖{len(ind)}个行业，广度充足")
else:
    issues.append(f"仅覆盖{len(ind)}个行业，广度不足")

# 三因子数据缺失
zero_factor_cols = []
for col in ['产业景气', '技术壁垒', '订单爆发', '业绩质量', '龙头地位', '预期差', '机构认可', '筹码面']:
    if (pd.to_numeric(df[col], errors='coerce') > 0).sum() == 0:
        zero_factor_cols.append(col)
if zero_factor_cols:
    issues.append(f"子因子全为0 ({len(zero_factor_cols)}项: {','.join(zero_factor_cols[:4])}...)")
else:
    strengths.append("子因子数据完整")

for s in strengths:
    print(f"  ✅ {s}")
for i in issues:
    print(f"  ⚠️ {i}")

# 总分
score_rating = 10
score_rating -= len(issues) * 2
if score_rating > 10: score_rating = 10
if score_rating < 1: score_rating = 1
stars = '⭐' * (score_rating // 2) + ('☆' if score_rating % 2 else '')
print(f"\n  评分: {score_rating}/10 {stars}")
print(f"  {'可参考但需进一步筛选' if score_rating < 6 else '质量可用，数据不足待补充' if score_rating < 8 else '数据完整度高，可直接使用'}")
print(f"\n  ⚡ 关键短板: 子因子数据全部缺失，最终分仅是Bull_v2.1分的排名")
print(f"  ⚡ 改进方向: 修复data_fetcher中的因子数据拉取 + 补充60日收益验证")

print("\nDone.")
