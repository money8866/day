import json
data = json.load(open("cache_backbone_tushare/theme3_constituents_20260612.json", "r", encoding="utf-8"))

checks = [
    ("宗申动力", "001696.SZ"),
    ("万丰奥威", "002085.SZ"),
]

print("=" * 70)
print("验证1：宗申动力 / 万丰奥威 是否在低空经济成分股中")
print("=" * 70)
for theme in data["themes"]:
    if theme["top_category"] != "低空经济":
        continue
    stocks = {s["ts_code"]: s for s in theme["stocks"]}
    print(f"\n[{theme['theme_name']}] 共 {theme['n_stocks']} 只")
    print(f"  概念板块: {theme.get('v2_bridge',{}).get('concept',[])}")
    print(f"  行业板块: {theme.get('v2_bridge',{}).get('industry',[])}")
    for name, code in checks:
        if code in stocks:
            s = stocks[code]
            print(f"  ✅ {name}({code}) | 纯度:{s['purity']:.2f} | 链距:{s['chain_distance']} | 行业匹配:{s['industry_match']} | via:{s['via']}")
        else:
            print(f"  ❌ {name}({code}) — 未命中")

print()
print("=" * 70)
print("验证2：消费电子 新增光学光电子后的命中情况")
print("=" * 70)
for theme in data["themes"]:
    if theme["theme_name"] != "消费电子":
        continue
    stocks = {s["ts_code"]: s for s in theme["stocks"]}
    print(f"\n[消费电子] 共 {theme['n_stocks']} 只")
    print(f"  概念板块: {theme.get('v2_bridge',{}).get('concept',[])}")
    print(f"  行业板块: {theme.get('v2_bridge',{}).get('industry',[])}")
    # 找光学光电子相关的股票（含"光学"字样或属于光学光电子行业的）
    optical_hits = []
    for s in theme["stocks"]:
        if "光学" in s["name"] or "光电" in s["name"] or "光" in s["name"]:
            optical_hits.append(s)
    print(f"  光学/光电 命中 {len(optical_hits)} 只:")
    for s in sorted(optical_hits, key=lambda x: -x["score"])[:12]:
        print(f"    {s['name']}({s['ts_code']}) 得分{s['score']:.0f} 纯度{s['purity']:.2f}")
