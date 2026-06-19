import json

with open(r'D:\mystock\solo\report_daily\ib_h1_acceleration_20260619.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

print("=" * 80)
print("IB池加速选股结果 | 2026-06-19")
print("IB池总数: {} | 有效数据: {} | 加速股: {}".format(
    d['ib_pool_count'], d['valid_data_count'], d['accelerated_count']))
print("=" * 80)

print("\n>>> 综合评分TOP30 <<<")
scored = d['scored']
for i, r in enumerate(scored[:30]):
    tags = ' '.join(r.get('tags', []))
    qoq1 = r.get('qoq_rev_q1', '?')
    qoq2 = r.get('qoq_rev_q2', '?')
    gap = r.get('acceleration_gap', 0) or 0
    h1_rev = r.get('h1_2026_rev_est', '?')
    h1_yoy = r.get('h1_2026_rev_yoy', '?')
    ni_est = r.get('h1_2026_ni_est', '?')
    print("{2}. {0:<8} {1:<10} {3:<10} 分={4:<3} Q1环比={5:>7}% Q2环比={6:>7}% 差={7:>+6}% H1增速={8:>6}% H1营收={9}亿 {10}".format(
        r['name'], r['code'], i+1, r['theme'][:8], r['total_score'],
        str(qoq1), str(qoq2), gap, str(h1_yoy), str(h1_rev), tags))

print("\n>>> 营收加速+超预期详情 <<<")
acc = d.get('accelerated', [])
print("共 {} 只通过加速+超预期筛选".format(len(acc)))
for i, r in enumerate(acc):
    print("")
    print("  {}. {}({}) | {}".format(i+1, r['name'], r['code'][:6], r['theme']))
    print("     Q1营收: {}亿 | Q4基数: {}亿".format(
        round(r.get('q1_rev', 0), 1) or '?', round(r.get('q4_rev', 0), 1) or '?'))
    print("     Q1环比: {}% -> Q2环比: {}% | 加速差: +{}%".format(
        r.get('qoq_rev_q1', '?'), r.get('qoq_rev_q2', '?'), r.get('acceleration_gap', 0)))
    h1_2025 = round(r.get('h1_2025_rev', 0), 1)
    h1_2026 = round(r.get('h1_2026_rev_est', 0), 1)
    h1_yoy = r.get('h1_2026_rev_yoy', 0) or 0
    print("     2025H1: {}亿 -> 2026H1预测: {}亿 (同比+{}%)".format(h1_2025, h1_2026, h1_yoy))
    ni_est = round(r.get('h1_2026_ni_est', 0), 1)
    beat = r.get('h1_beat_signal', False)
    print("     净利预测: {}亿 | 超预期={}".format(ni_est if ni_est else '?', beat))

print("")
print(">>> 净利加速TOP10 <<<")
ni_acc = d.get('ni_accelerated', [])
for i, r in enumerate(ni_acc[:10]):
    gap = (r.get('qoq_ni_q2') or 0) - (r.get('qoq_ni_q1') or 0)
    print("  {}. {}({}) | Q1净利环比{}%->Q2环比{}% | 差+{}%".format(
        i+1, r['name'], r['code'][:6],
        r.get('qoq_ni_q1', '?'), r.get('qoq_ni_q2', '?'), round(gap, 0)))
