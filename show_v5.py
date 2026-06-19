import json

with open(r'D:\mystock\solo\report_daily\h1_true_acceleration_v5_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

print("total:{} | slope:{} | rev_accel:{} | ni_accel:{} | both:{}".format(
    d['total'], d['slope_up_count'], d['rev_accel_count'], d['ni_accel_count'], d['both_count']))

print("\n=== SLOPE UP ===")
for i, r in enumerate(d['slope_up']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("\n{}. {} {}({}) | {}".format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print("   Q2/Q1(2025)={:.2f}x Q1/Q4(2026)={:.2f}x slope_gap={:+.2f}".format(
        r.get('q2q1_25',0) or 0, r.get('q1q4_26',0) or 0, r.get('slope_gap',0) or 0))
    print("   Q2/Q1(adj)={:.2f}x H1/Q1={:.2f}x(vs25 {:.2f}x)".format(
        r.get('q2q1_26',0) or 0, r.get('ratio_26',0) or 0, r.get('ratio_25',0) or 0))
    print("   Q1同比+{:.0f}% -> H1同比+{:.0f}% (加速{:+.0f}%) H1={:.1f}亿>Q4={:.1f}亿".format(
        r.get('q1_yoy',0) or 0, r.get('h1_yoy',0) or 0, r.get('accel_gap',0) or 0,
        r.get('h1_26r',0) or 0, r.get('q4_25r',0) or 0))
    if r.get('h1_26n'): print("   H1净利={:.1f}亿(+{:.0f}%)".format(r['h1_26n'], r.get('h1_ni_yoy',0) or 0))

print("\n=== REV ACCEL ===")
for i, r in enumerate(d['rev_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("{}. {} {}({}) | Q1+{:.0f}% -> H1+{:.0f}% ({:+.0f}%) | H1={:.1f}亿 Q4={:.1f}亿".format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_yoy',0) or 0, r.get('h1_yoy',0) or 0, r.get('accel_gap',0) or 0,
        r.get('h1_26r',0) or 0, r.get('q4_25r',0) or 0))

if d.get('both_accel'):
    print("\n=== BOTH ===")
    for r in d['both_accel']:
        print("{} {}({}) | rev+{:.0f}% ni+{:.0f}%".format(
            '[IA]' if r['pool']=='IA' else '[IB]', r['name'], r['code'][:6],
            r.get('accel_gap',0) or 0, r.get('ni_accel_gap',0) or 0))

print("\n=== NI ACCEL ===")
for i, r in enumerate(d['ni_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("{}. {} {}({}) | Q1+{:.0f}% -> H1+{:.0f}% ({:+.0f}%) ni={:.1f}亿".format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_ni_yoy',0) or 0, r.get('h1_ni_yoy',0) or 0,
        r.get('ni_accel_gap',0) or 0, r.get('h1_26n',0) or 0))
