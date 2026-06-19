import json

with open(r'D:\mystock\solo\report_daily\h1_true_acceleration_v5_20260619.json','r',encoding='utf-8') as f:
    d = json.load(f)

# 检查前10只有效数据的斜率
valid = [r for r in d['all_results'] if r.get('data_ok') and r.get('q2q1_25') is not None and r.get('q1q4_26') is not None]
print("有完整数据: {} 只".format(len(valid)))

# 看q2q1_25 vs q1q4_26的分布
for r in valid[:20]:
    print("{0}({1}): Q2/Q1(25)={2:.2f}x  Q1/Q4(26)={3:.2f}x  gap={4:+.2f}  Q1yoy={5:.0f}%  H1/Q1(25)={6:.2f}x".format(
        r['name'], r['code'][:6], r['q2q1_25'], r['q1q4_26'],
        (r['q1q4_26'] or 0) - (r['q2q1_25'] or 0),
        r.get('q1_yoy',0) or 0, r.get('ratio_25',0) or 0))
