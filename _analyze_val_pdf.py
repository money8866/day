# -*- coding: utf-8 -*-
import pandas as pd

df = pd.read_csv('D:/mystock/solo/report_daily/valuation_ge100_v2.csv', encoding='utf-8-sig')

print('=== features 分布 ===')
print(df['features'].value_counts().to_string())
print()

print('=== 评分分段 ===')
bins = [0, 20, 40, 60, 70, 75, 80, 100]
labels = ['0-20', '20-40', '40-60', '60-70', '70-75', '75-80', '80+']
df['score_bin'] = pd.cut(df['composite_score'], bins=bins, labels=labels, right=False)
print(df['score_bin'].value_counts().sort_index().to_string())
print()

print('=== TOP 20 ===')
top20 = df.nlargest(20, 'composite_score')
for _, r in top20.iterrows():
    ups = r['realistic_upside_%']
    print(f"  {r['name']:>6s} | PE={r['pe_ttm']:>5.1f} | PEG={r['peg']:>5.2f} | PE调={r['pe_adjusted']:>5.1f} | 空间={ups:>6.1f}% | 分={r['composite_score']:.0f} | {r['features']}")
print()

print('=== 市值空间分布 ===')
print(f"  空间>=100%: {len(df[df['realistic_upside_%']>=100])} 只")
print(f"  空间>=50%:  {len(df[df['realistic_upside_%']>=50])} 只")
print(f"  空间<0%:    {len(df[df['realistic_upside_%']<0])} 只")
print()

print('=== 主题分布 TOP 15 ===')
thm_cnt = df['theme'].value_counts().head(15)
for t, c in thm_cnt.items():
    print(f"  {t}: {c}只")
print()

print('=== 问题诊断 ===')
# 检查有无行业字段
if 'industry' not in df.columns:
    print("  ❌ 缺少 industry 行业字段")
if 'filter_reason' not in df.columns:
    print("  ❌ 缺少 filter_reason 字段")
else:
    print(f"  filter_reason: {df['filter_reason'].value_counts().to_dict()}")

# features 单一化问题
feat_cnt = df['features'].value_counts()
if len(feat_cnt) == 1:
    print(f"  ❌ features 仅有一个标签 '{feat_cnt.index[0]}'，357只股票全是同一个特征描述，失去区分度")
elif len(feat_cnt) <= 3:
    print(f"  ⚠️  features 仅有 {len(feat_cnt)} 个标签，区分度不足")

# PE调整 vs PE_TTM
same_pe = (df['pe_adjusted'] == df['pe_ttm']).sum()
print(f"  PE调整≡PE_TTM: {same_pe}/{len(df)} ({same_pe/len(df)*100:.1f}%)  -> 可能调整公式未生效")
print()

# 重复code检查
dup = df[df['code'].duplicated()]['code'].tolist()
if dup:
    print(f"  ❌ 有重复代码: {dup}")
else:
    print("  ✅ 无重复代码")
