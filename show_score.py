import json

with open(r'D:\mystock\solo\report_daily\h1_超预期评分_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

print("total:{} scored:{}".format(d['total'], d['scored_count']))

for i, r in enumerate(d['top_scored']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("\n{}. {} {}({}) | {} | score={}".format(i+1, pt, r['name'], r['code'][:6], r['theme'], r['accel_score']))
    print("   Q1: 25Q1+{:.0f}% -> 26Q1+{:.0f}% (mom {:+.0f}%)".format(
        r.get('q1_25_vs_24',0) or 0, r.get('q1_26_vs_25',0) or 0, r.get('q1_momentum',0) or 0))
    print("   H1: 25H1+{:.0f}% -> 26H1+{:.0f}% (accel {:+.0f}%)".format(
        r.get('h1_25_vs_24',0) or 0, r.get('h1_26_vs_25',0) or 0, r.get('h1_accel',0) or 0))
    print("   H1={:.1f}亿 > Q4={:.1f}亿 | H1/Q1={:.2f}x".format(
        r.get('h1_26r',0) or 0, r.get('q4_25r',0) or 0, r.get('ratio_h1_q1',0) or 0))
    if r.get('h1_26n'):
        print("   ni={:.1f}亿(+{:.0f}%) | cap={:.0f}亿 PE={:.0f}".format(
            r['h1_26n'], r.get('h1_ni_vs_25',0) or 0, r.get('market_cap_yi',0) or 0, r.get('pe',0) or 0))
