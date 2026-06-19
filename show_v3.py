import json

with open(r'D:\mystock\solo\report_daily\h1_true_acceleration_v3_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

print("total:{} | slope_up:{} | rev_accel:{} | ni_accel:{} | both:{}".format(
    d['total'], d['slope_up_count'], d['rev_accel_count'], d['ni_accel_count'], d['both_count']))

print("\n=== SLOPE UP (Q1/Q4 recovery > Q2/Q1 recovery) + H1>Q4 ===")
for i, r in enumerate(d['slope_up']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    r25 = r.get('recovery_25', 0) or 0
    r26 = r.get('recovery_26', 0) or 0
    sg = r.get('slope_gap', 0) or 0
    q1p = r.get('q1_rev_yoy_pct', 0) or 0
    h1p = r.get('h1_rev_yoy_pct', 0) or 0
    ag = r.get('rev_accel_gap', 0) or 0
    h1 = r.get('h1_26_rev_est', 0) or 0
    q4 = r.get('q4_25_rev', 0) or 0
    ni = r.get('h1_26_ni_est', 0) or 0
    print("\n{}. {} {}({}) | {}".format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print("   2025恢复(Q2/Q1)={:.2f}x | 2026恢复(Q1/Q4)={:.2f}x | 斜率差{:+.3f}".format(r25, r26, sg))
    print("   Q1同比+{:.0f}% -> H1同比+{:.0f}% (加速{:+.0f}%) | H1={:.1f}亿 > Q4={:.1f}亿 | H1净利{:.1f}亿".format(
        q1p, h1p, ag, h1, q4, ni))

print("\n=== REV ACCEL (H1同比>Q1同比) + H1>Q4 ===")
for i, r in enumerate(d['rev_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("{}. {} {}({}) | Q1+{:.0f}% -> H1+{:.0f}% (加速{:+.0f}%)".format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_rev_yoy_pct',0) or 0, r.get('h1_rev_yoy_pct',0) or 0,
        r.get('rev_accel_gap',0) or 0))

print("\n=== NI ACCEL ===")
for i, r in enumerate(d['ni_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print("{}. {} {}({}) | Q1+{:.0f}% -> H1+{:.0f}% (加速{:+.0f}%) | H1净利{:.1f}亿".format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_ni_yoy_pct',0) or 0, r.get('h1_ni_yoy_pct',0) or 0,
        r.get('ni_accel_gap',0) or 0, r.get('h1_26_ni_est',0) or 0))
