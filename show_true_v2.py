import json

with open(r'D:\mystock\solo\report_daily\h1_true_acceleration_v2_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

print("pool: {} | rev_accel: {} | ni_accel: {} | both: {}".format(
    d['total'], d['rev_accel_count'], d['ni_accel_count'], d['both_count']))

print("\n=== REV ACCEL (Q2yoy > Q1yoy + H1 > Q4) ===")
for i, r in enumerate(d['rev_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    q1y = r.get('q1_rev_yoy', 0) or 0
    q2y = r.get('q2_rev_yoy', 0) or 0
    gap = r.get('rev_accel_gap', 0) or 0
    h1y = r.get('h1_rev_yoy', 0) or 0
    print("{0}. {1} {2}({3}) | {4}".format(i+1, pt, r['name'], r['code'][:6], r['theme'][:8]))
    print("   Q1同比+{0:.0f}% -> Q2同比+{1:.0f}% (加速{2:+.0f}%) | H1同比+{3:.0f}% | H1={4:.1f}亿 Q4={5:.1f}亿".format(
        q1y, q2y, gap, h1y, r.get('h1_26_rev_est',0) or 0, r.get('q4_25_rev',0) or 0))

if d.get('both_accel'):
    print("\n=== BOTH ACCEL ===")
    for i, r in enumerate(d['both_accel']):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        print("{0}. {1} {2}({3}) | rev+{4:.0f}% ni+{5:.0f}% | H1净利{6:.1f}亿".format(
            i+1, pt, r['name'], r['code'][:6],
            r.get('rev_accel_gap',0) or 0, r.get('ni_accel_gap',0) or 0,
            r.get('h1_26_ni_est',0) or 0))

print("\n=== NI ACCEL TOP15 ===")
for i, r in enumerate(d['ni_accel'][:15]):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    q1y = r.get('q1_ni_yoy', 0) or 0
    q2y = r.get('q2_ni_yoy', 0) or 0
    gap = r.get('ni_accel_gap', 0) or 0
    print("{0}. {1} {2}({3}) | Q1同比+{4:.0f}% -> Q2同比+{5:.0f}% (加速{6:+.0f}%)".format(
        i+1, pt, r['name'], r['code'][:6], q1y, q2y, gap))
