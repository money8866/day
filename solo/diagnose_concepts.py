"""诊断脚本：比对theme.json中所有主题的concept配置与东财实际概念标签。

输出：
1. 每个主题的concept配置项是否能在东财概念中找到匹配
2. 东财概念中最接近的候选（模糊匹配）
3. 统计匹配率
"""
import json
import os
import sys
from difflib import get_close_matches

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

import theme_trend_sentiment_score as ts

# 1. 加载theme.json
theme_path = os.path.join(BASE_DIR, 'theme.json')
with open(theme_path, 'r', encoding='utf-8') as f:
    hot_themes = json.load(f)['HOT_THEMES']

# 2. 获取东财全部概念标签
dc_df = ts.get_dc_members()
all_concepts = set()
all_industries = set()
if dc_df is not None and not dc_df.empty:
    for _, r in dc_df.iterrows():
        bn = r.get('concept_name', '')
        if bn:
            if r.get('is_industry', False):
                all_industries.add(bn)
            else:
                all_concepts.add(bn)

print(f"东财概念标签总数: {len(all_concepts)}")
print(f"东财行业板块总数: {len(all_industries)}")
print()

# 3. 逐主题比对
total_concepts_configured = 0
total_matched = 0
total_unmatched = 0
unmatched_list = []

for theme_name, cfg in sorted(hot_themes.items()):
    concept_list = cfg.get('concept', [])
    if not concept_list:
        continue

    print(f"{'='*60}")
    print(f"主题: {theme_name}")
    print(f"{'='*60}")

    for cc in concept_list:
        total_concepts_configured += 1
        # 精确匹配
        if cc in all_concepts:
            print(f"  [OK]   {cc}")
            total_matched += 1
        elif cc in all_industries:
            print(f"  [OK*]  {cc}  (注: 属于行业板块而非概念)")
            total_matched += 1
        else:
            # 模糊匹配找候选
            candidates = get_close_matches(cc, list(all_concepts | all_industries), n=3, cutoff=0.4)
            print(f"  [MISS] {cc}")
            if candidates:
                print(f"         候选: {candidates}")
            else:
                # 用子串匹配找候选
                substr_candidates = [c for c in (all_concepts | all_industries) if cc in c or c in cc]
                if substr_candidates:
                    print(f"         子串候选: {substr_candidates[:5]}")
                else:
                    print(f"         无候选")
            total_unmatched += 1
            unmatched_list.append((theme_name, cc, candidates))

    print()

# 4. 汇总
print("=" * 60)
print("汇总")
print("=" * 60)
print(f"配置概念总数: {total_concepts_configured}")
print(f"匹配成功: {total_matched}")
print(f"匹配失败: {total_unmatched}")
if total_concepts_configured > 0:
    print(f"匹配率: {total_matched / total_concepts_configured * 100:.1f}%")

print()
print("=" * 60)
print("不匹配清单（需修复）")
print("=" * 60)
for theme_name, cc, candidates in unmatched_list:
    print(f"  [{theme_name}] {cc} -> 候选: {candidates}")

# 保存到文件
output_path = os.path.join(BASE_DIR, 'concept_diagnosis.json')
output = {
    'total_configured': total_concepts_configured,
    'matched': total_matched,
    'unmatched': total_unmatched,
    'match_rate': round(total_matched / max(total_concepts_configured, 1) * 100, 1),
    'unmatched_list': [
        {'theme': t, 'concept': c, 'candidates': cand}
        for t, c, cand in unmatched_list
    ],
}
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n诊断结果已保存: {output_path}")
