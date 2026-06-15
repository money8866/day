import json

raw = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

print("="*80)
print(f"  V11 子主题分层加权版 - 评分结果验证")
print(f"  交易日: {raw.get('trade_date')}  主线: {raw.get('primary_mainline')}")
print("="*80)

ranking = raw.get('all_theme_ranking', [])

# === 1. 全主题排名 ===
print(f"\n📊 全主题主线评分排名（共{len(ranking)}个）:\n")
print(f"  {'排名':<4} {'主题':<10} {'总分':>6} {'资金连续':>7} {'容量':>5} {'扩散':>6} {'龙头':>6} {'活跃子主题':>9}")
print(f"  {'-'*4} {'-'*10} {'-'*6} {'-'*7} {'-'*5} {'-'*6} {'-'*6} {'-'*9}")

for i, r in enumerate(ranking):
    print(f"  {i+1:<4} {r['theme']:<10} {r['mainline_score']:>6.1f} {r['capital_continuity']:>7.1f} "
          f"{r['capacity_score']:>5.0f} {r['diffusion_score']:>6.1f} "
          f"{r['leader_structure']:>6.1f}")

# === 2. 主线子主题分层详情 ===
primary = raw.get('primary_mainline', '')
mm = raw.get('mainline_metrics', {})

if primary and mm:
    subs = mm.get('sub_themes', [])
    print(f"\n{'='*80}")
    print(f"  【{primary}】子主题分层详情（按子主题得分降序）")
    print(f"{'='*80}\n")
    print(f"  {'子主题':<16} {'得分':>6} {'权重%':>6} {'龙头强度':>8} {'5日涨幅':>8} {'站稳MA5':>7} {'高趋势股':>8} {'集中度':>7} {'成交额':>9}")
    print(f"  {'-'*16} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*7} {'-'*9}")

    for s in subs:
        print(f"  {s['sub_theme']:<16} {s['sub_score']:>6.1f} {s['sub_weight_pct']:>5.1f}% "
              f"{s['leader_strength']:>7.1f} {s['avg_change5_pct']:>+7.1f}% "
              f"{s['above_ma5_ratio']:>6.0f}% {s['high_trend_count']:>7} "
              f"{s['concentration']:>6.0f}% {s['sub_amount_yi']:>8.1f}亿")
        for ls in s.get('top_leaders', []):
            print(f"    → {ls['name']}({ls['ts_code']}) trend={ls['trend_score']} amt={ls['amount_yi']}亿")

    print(f"\n  综合主线评分公式:")
    print(f"    资金连续性: {mm['capital_continuity']:.1f} × 0.35 = {mm['capital_continuity']*0.35:.1f}")
    print(f"    成交容量:   {mm['capacity_score']:.0f} × 0.30 = {mm['capacity_score']*0.30:.1f}")
    print(f"    扩散能力:   {mm['diffusion_score']:.1f} × 0.20 = {mm['diffusion_score']*0.20:.1f}")
    print(f"    龙头结构:   {mm['leader_structure']:.1f} × 0.15 = {mm['leader_structure']*0.15:.1f}")
    print(f"    {'─'*50}")
    print(f"    总分:       {mm['mainline_score']:.1f}")

# === 3. Top3 一级主题子主题对比 ===
print(f"\n{'='*80}")
print(f"  Top3 主题子主题得分对比:")
print(f"{'='*80}\n")

for r in ranking[:3]:
    cat = r['theme']
    # 需要从原始JSON读取完整cat_metrics
    pass

# 从文件末尾截取的mainline_metrics获取各主题sub_themes
# 通过重新分析得到
import glob, os, sys
sys.path.insert(0, 'd:/mystock/solo')

# 简单打印ranking中的子主题数量信息
for r in ranking[:3]:
    print(f"  【{r['theme']}】活跃子主题 {r['n_active_subs']}/{r['n_total_subs']} 扩散分={r['diffusion_score']:.1f} 龙头分={r['leader_structure']:.1f}")

print(f"\n{'='*80}")
print(f"  子主题分层加权 vs 旧版算法 对比（以金融为例）:")
print(f"{'='*80}\n")

print(f"  旧版问题:")
print(f"    ❌ 资金连续性 = 全量股票MA10向上% + 全量站稳MA5% + 均趋势分")
print(f"       → 银行强+券商强+保险弱 → 弱弱加权拉低，强者也被平均")
print(f"    ❌ 扩散能力 = 活跃子主题比例 + 高趋势股比例 + 子主题数量")
print(f"       → 1强4弱时，active_ratio=20%，但真实扩散被掩盖")
print(f"    ❌ 龙头结构 = Top5成交额股票趋势分均值")
print(f"       → 龙头集中在同一子主题时，评分仍然很高，不反映风险")
print()
print(f"  新版改进:")
print(f"    ✅ 资金连续性 = Σ(子主题资金连续性 × 子主题成交额权重)")
print(f"       → 大钱在哪个子主题，对总分影响更大")
print(f"    ✅ 扩散能力 = 加权子主题得分(35%) + 均匀度(30%) + 活跃比例(20%) + 子主题数量(15%)")
print(f"       → 子主题之间方差越大，均匀度分越低，拉开差距")
print(f"    ✅ 龙头结构 = Top5趋势分(40%) + 综合分(30%) + 龙头跨子主题分散度(20%)")
print(f"       → 龙头集中在1个子主题时，分散度=低，分数降低")
print(f"{'='*80}")
