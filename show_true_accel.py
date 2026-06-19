import json

with open(r'D:\mystock\solo\report_daily\h1_true_acceleration_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

print('pool:', d['total_pool'], '| rev_accel:', d['rev_accel_count'], '| ni_accel:', d['ni_accel_count'], '| both:', d['both_accel_count'])

print('\n=== REV ACCEL ===')
for i, r in enumerate(d['rev_accel']):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    q1y = r.get('q1_real_rev_yoy', 0) or 0
    h1y = r.get('h1_predict_rev_yoy', 0) or 0
    gap = r.get('rev_accel_gap', 0) or 0
    q4 = r.get('q4_2025_rev', 0) or 0
    h1est = r.get('h1_2026_rev_est', 0) or 0
    ni = r.get('h1_2026_ni_est', 0) or 0
    print('{0}. {1} {2}({3}) | {4}'.format(i+1, pt, r['name'], r['code'][:6], r['theme'][:8]))
    print('   Q1同比+{0:.0f}% -> H1同比+{1:.0f}% (加速+{2:.0f}%) | Q4={3:.1f}亿 H1预测={4:.1f}亿 净利{5:.1f}亿'.format(q1y, h1y, gap, q4, h1est, ni))

if d.get('both_accel'):
    print('\n=== BOTH ACCEL ===')
    for i, r in enumerate(d['both_accel']):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        rg = r.get('rev_accel_gap', 0) or 0
        ng = r.get('ni_accel_gap', 0) or 0
        print('{0}. {1} {2}({3}) | rev+{4:.0f}% ni+{5:.0f}% | H1净利{6:.1f}亿'.format(
            i+1, pt, r['name'], r['code'][:6], rg, ng, r.get('h1_2026_ni_est',0) or 0))

print('\n=== NI ACCEL TOP15 ===')
for i, r in enumerate(d['ni_accel'][:15]):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    q1y = r.get('q1_real_ni_yoy', 0) or 0
    h1y = r.get('h1_predict_ni_yoy', 0) or 0
    gap = r.get('ni_accel_gap', 0) or 0
    print('{0}. {1} {2}({3}) | Q1同比+{4:.0f}% H1同比+{5:.0f}% 加速+{6:.0f}%'.format(
        i+1, pt, r['name'], r['code'][:6], q1y, h1y, gap))
