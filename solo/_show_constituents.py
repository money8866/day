"""展示 theme3_constituents_v2.py 生成的成分股样例"""
import json

data = json.load(open("cache_backbone_tushare/theme3_constituents_20260612.json", "r", encoding="utf-8"))
print(f"交易日期: {data['trade_date']}  |  版本: {data['version']}  |  主题数: {len(data['themes'])}\n")

# 选 8 个有代表性的主题，每个展示前 8 只股
samples = [
    "AI算力芯片", "光模块与CPO", "数据中心网络",
    "数据中心散热", "高速铜连接",
    "功率半导体", "半导体材料", "存储芯片",
]

for theme in data["themes"]:
    if theme["theme_name"] not in samples:
        continue
    print(f"{'='*70}")
    print(f"[{theme['top_category']}] {theme['theme_name']}  共 {theme['n_stocks']} 只")
    print(f"  概念板块: {theme.get('v2_bridge',{}).get('concept',[])[:5]}")
    print(f"  行业板块: {theme.get('v2_bridge',{}).get('industry',[])[:5]}")
    print(f"  {'代码':<12} {'名称':<10} {'得分':>6} {'纯度':>5} {'链距':>4} {'行业匹配':>8} {'来源':>12}")
    print(f"  {'-'*65}")
    for s in sorted(theme["stocks"], key=lambda x: -x["score"])[:8]:
        print(f"  {s['ts_code']:<12} {s['name']:<10} {s['score']:>6.1f} {s['purity']:>5.2f} {s['chain_distance']:>4d} {str(s['industry_match']):>8} {s['via']:>12}")
    print()
