import json

data = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

print("="*85)
print(f"  V11 + 时间维度版 - 最终评分验证")
print(f"  交易日: {data.get('trade_date')}  主线: {data.get('primary_mainline')}")
print("="*85)

ranking = data.get('all_theme_ranking', [])

print(f"\n📊 全主题综合评分 (时间维度加权50%)\n")
print(f"  {'排名':<4} {'主题':<10} {'总分':>6} {'短期':>5} {'中期':>5} {'加速':>5} "
      f"{'资金':>5} {'容量':>5} {'扩散':>5} {'龙头':>5}")
print(f"  {'-'*4} {'-'*10} {'-'*6} {'-'*5} {'-'*5} {'-'*5} "
      f"{'-'*5} {'-'*5} {'-'*5} {'-'*5}")

for i, r in enumerate(ranking):
    total = r['mainline_score']
    short = r.get('short_term_score', 0)
    mid = r.get('mid_term_score', 0)
    accel = r.get('acceleration_signal', 0)
    cap = r['capital_continuity']
    cap_score = r['capacity_score']
    diff = r['diffusion_score']
    lead = r['leader_structure']
    bar = '█' * int(total / 5)
    print(f"  {i+1:<4} {r['theme']:<10} {total:>6.1f} {short:>5.1f} "
          f"{mid:>5.1f} {accel:>+5.1f} {cap:>5.1f} {cap_score:>5.0f} "
          f"{diff:>5.1f} {lead:>5.1f}  {bar}")

# 详细分析当前主线
primary = data.get('primary_mainline', '')
mm = data.get('mainline_metrics', {})

if primary and mm:
    print(f"\n{'='*85}")
    print(f"  【{primary}】综合评分明细")
    print(f"{'='*85}\n")
    print(f"  短期动量(25%)    = {mm.get('short_term_score', 0):.1f} → 贡献 {mm.get('short_term_score', 0)*0.25:.1f}分")
    print(f"  中期持续性(25%)  = {mm.get('mid_term_score', 0):.1f} → 贡献 {mm.get('mid_term_score', 0)*0.25:.1f}分")
    print(f"  资金连续性(15%)  = {mm.get('capital_continuity', 0):.1f} → 贡献 {mm.get('capital_continuity', 0)*0.15:.1f}分")
    print(f"  成交容量(10%)    = {mm.get('capacity_score', 0):.0f} → 贡献 {mm.get('capacity_score', 0)*0.10:.1f}分")
    print(f"  扩散能力(15%)    = {mm.get('diffusion_score', 0):.1f} → 贡献 {mm.get('diffusion_score', 0)*0.15:.1f}分")
    print(f"  龙头结构(10%)    = {mm.get('leader_structure', 0):.1f} → 贡献 {mm.get('leader_structure', 0)*0.10:.1f}分")
    print(f"  {'-'*40}")
    print(f"  总分: {mm.get('mainline_score', 0):.1f}")

    # 时间维度解读
    short = mm.get('short_term_score', 0)
    mid = mm.get('mid_term_score', 0)
    accel = mm.get('acceleration_signal', 0)

    print(f"\n  🕐 时间维度解读:")
    if short >= 60 and mid >= 40:
        print(f"     ✅ 短期强 + 中期强 = 可持续趋势")
    elif short >= 60 and mid < 40:
        print(f"     ⚠️  短期脉冲强，中期还没确认 = 可能是第一天强，需要观察")
    elif short < 60 and mid >= 40:
        print(f"     ⚠️  中期有基础，但短期在调整 = 等待回踩介入")
    else:
        print(f"     ❌ 短期+中期都弱 = 回避")

    if accel > 3:
        print(f"     🚀 加速度为正 ({accel:+.1f}%) = 短期在加速，正在恢复")
    elif accel < -3:
        print(f"     🛑 加速度为负 ({accel:+.1f}%) = 短期在减速，警惕回落")

    # 子主题数据
    subs = mm.get('sub_themes', [])
    print(f"\n  📂 子主题分层（按子主题得分降序）:")
    print(f"  {'子主题':<14} {'得分':>6} {'权重%':>6} {'5日涨':>7} {'站稳MA5':>8} {'MA10向上':>9} {'龙头强':>7}")
    print(f"  {'-'*14} {'-'*6} {'-'*6} {'-'*7} {'-'*8} {'-'*9} {'-'*7}")
    for s in subs[:5]:
        print(f"  {s['sub_theme']:<14} {s['sub_score']:>6.1f} {s['sub_weight_pct']:>5.1f}% "
              f"{s['avg_change5_pct']:>+6.1f}% {s['above_ma5_ratio']:>7.0f}% "
              f"{s.get('sub_amount_yi', 0):>8.0f}亿 {s['leader_strength']:>6.1f}")

print(f"\n{'='*85}")
print(f"  📌 评分逻辑总结:")
print(f"     主线评分 = 短期动量(25%) + 中期持续性(25%) + 资金连续(15%)")
print(f"                     + 成交容量(10%) + 扩散能力(15%) + 龙头结构(10%)")
print(f"     短期动量 = 5日涨幅强度(50%) + 站稳MA5比例(30%) + 高趋势比例(20%)")
print(f"     中期持续 = 10日涨幅强度(40%) + MA10向上比例(35%) + 均趋势分(25%)")
print(f"{'='*85}")
