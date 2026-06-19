import json

with open(r'D:\mystock\solo\report_daily\h1_true_acceleration_v4_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

print("total:{} | rev_accel:{} | ni_accel:{} | both:{} | near:{}".format(
    d['total'], d['rev_accel_count'], d['ni_accel_count'], d['both_count'], d.get('near_accel_count',0)))

print("\n=== REV ACCEL ===")
for i, r in enumerate(d['rev_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("\n{}. {} {}({}) | {}".format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print("   Q1同比+{:.0f}% -> H1同比+{:.0f}% (加速{:+.0f}%)".format(
        r.get('q1_26_rev_yoy',0) or 0, r.get('h1_26_rev_yoy',0) or 0, r.get('rev_accel_gap',0) or 0))
    print("   H1={:.1f}亿 Q4={:.1f}亿 H1/Q1={:.2f}x | {} | {}".format(
        r.get('h1_26_rev_est',0) or 0, r.get('q4_25_rev',0) or 0,
        r.get('h1_over_q1',0) or 0, r.get('hist_pattern','?'), r.get('momentum','?')))
    if r.get('h1_26_ni_est'):
        print("   H1净利={:.1f}亿(同比+{:.0f}%)".format(r['h1_26_ni_est'], r.get('h1_26_ni_yoy',0) or 0))

if d.get('both_accel'):
    print("\n=== BOTH ===")
    for i, r in enumerate(d['both_accel']):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        print("{}. {} {}({}) | rev+{:.0f}% ni+{:.0f}%".format(
            i+1, pt, r['name'], r['code'][:6],
            r.get('rev_accel_gap',0) or 0, r.get('ni_accel_gap',0) or 0))

print("\n=== NI ACCEL ===")
for i, r in enumerate(d['ni_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("{}. {} {}({}) | Q1+{:.0f}% -> H1+{:.0f}% (加速{:+.0f}%) | ni={:.1f}亿".format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_26_ni_yoy',0) or 0, r.get('h1_26_ni_yoy',0) or 0,
        r.get('ni_accel_gap',0) or 0, r.get('h1_26_ni_est',0) or 0))

print("\n=== NEAR ACCEL (gap -10%~0%) ===")
for i, r in enumerate(d.get('near_accel', [])):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("{}. {} {}({}) | Q1+{:.0f}% H1+{:.0f}% {:+.0f}% | {} {}".format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_26_rev_yoy',0) or 0, r.get('h1_26_rev_yoy',0) or 0,
        r.get('rev_accel_gap',0) or 0, r.get('hist_pattern','?'), r.get('momentum','?')))
