import json

with open('d:/mystock/solo/cache_backbone_tushare/trend_lifecycle_v7_1_20260612.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

rankings = d.get('rankings', [])

print(f"{'主题':<12} {'结构分':>7} {'短期动量':>8} {'中期趋势':>8} {'长期周期':>8} {'生命周期':<12} {'是否主线':<8}")
print("-" * 80)

for r in rankings:
    print(f"{r.get('theme',''):<12} {r.get('trend_structure_score',0):>7.1f} {r.get('short_momentum_score',0):>8.1f} {r.get('mid_trend_score',0):>8.1f} {r.get('long_cycle_score',0):>8.1f} {r.get('lifecycle_stage',''):<12} {'是' if r.get('is_core_mainline') else '否':<8}")

print("\n【V7.1 核心主线】")
for r in rankings:
    if r.get('is_core_mainline'):
        print(f"  {r.get('theme')}: 结构分={r.get('trend_structure_score')} 阶段={r.get('lifecycle_stage')}")
        print(f"    短期: {r.get('short_momentum_score')} 中期: {r.get('mid_trend_score')} 长期: {r.get('long_cycle_score')}")
        print(f"    判定: {r.get('mainline_strength')}")
        print(f"    解读: {r.get('interpretation')}")
