import json

raw = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

print("="*80)
print(f"  V11 子主题分层加权版 V3 - 最终验证")
print(f"  交易日: {raw.get('trade_date')}  主线: {raw.get('primary_mainline')}")
print("="*80)

ranking = raw.get('all_theme_ranking', [])

print(f"\n📊 全主题主线评分排名:\n")
print(f"  {'排名':<4} {'主题':<10} {'总分':>6} {'资金连续':>7} {'容量':>5} {'扩散':>6} {'龙头':>6}")
print(f"  {'-'*4} {'-'*10} {'-'*6} {'-'*7} {'-'*5} {'-'*6} {'-'*6}")
for i, r in enumerate(ranking):
    bar = '█' * int(r['mainline_score'] / 5)
    print(f"  {i+1:<4} {r['theme']:<10} {r['mainline_score']:>6.1f} {r['capital_continuity']:>7.1f} "
          f"{r['capacity_score']:>5.0f} {r['diffusion_score']:>6.1f} "
          f"{r['leader_structure']:>6.1f}  {bar}")

primary = raw.get('primary_mainline', '')
mm = raw.get('mainline_metrics', {})

if primary and mm:
    subs = mm.get('sub_themes', [])
    print(f"\n{'='*80}")
    print(f"  【{primary}】子主题分层详情")
    print(f"{'='*80}\n")
    print(f"  {'子主题':<16} {'得分':>6} {'权重%':>6} {'龙头强度':>8} {'5日涨幅':>8} {'站稳MA5':>7} {'高趋势':>6} {'集中度':>7} {'成交额':>9}")
    print(f"  {'-'*16} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*7} {'-'*6} {'-'*7} {'-'*9}")
    for s in subs:
        bar = '█' * int(s['sub_score'] / 5)
        print(f"  {s['sub_theme']:<16} {s['sub_score']:>6.1f} {s['sub_weight_pct']:>5.1f}% "
              f"{s['leader_strength']:>7.1f} {s['avg_change5_pct']:>+7.1f}% "
              f"{s['above_ma5_ratio']:>6.0f}% {s['high_trend_count']:>5} "
              f"{s['concentration']:>6.0f}% {s['sub_amount_yi']:>8.1f}亿  {bar}")

    print(f"\n  评分公式:")
    print(f"    资金连续性: {mm['capital_continuity']:.1f} × 0.35 = {mm['capital_continuity']*0.35:.1f}")
    print(f"    成交容量:   {mm['capacity_score']:.0f} × 0.30 = {mm['capacity_score']*0.30:.1f}")
    print(f"    扩散能力:   {mm['diffusion_score']:.1f} × 0.20 = {mm['diffusion_score']*0.20:.1f}")
    print(f"    龙头结构:   {mm['leader_structure']:.1f} × 0.15 = {mm['leader_structure']*0.15:.1f}")
    print(f"    总分:       {mm['mainline_score']:.1f}")

print(f"\n{'='*80}")
print(f"  ✅ 改进总结:")
print(f"     1. 扩散能力: 修正为加权平均 + 均匀度 + 活跃比例 + 扩散广度 (满分100)")
print(f"     2. 权重: 子主题成交额/一级主题总成交额 (允许多重归属，权重总和>100%)")
print(f"     3. 资金连续性: 子主题资金连续性 × 归一化权重，真实反映资金流向")
print(f"     4. 龙头结构: 新增龙头跨子主题分散度，龙头集中=风险高=分低")
print(f"     5. 子主题得分: 龙头强度+均趋势+动能+站稳MA5+活跃度+扩散度 六维评估")
print(f"{'='*80}")
