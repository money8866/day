#!/usr/bin/env python3
import json

with open('D:\\mystock\\solo\\report_daily\\mainboard_v4_scan_20260618.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

stocks = data['data']
total = data['total_count']

print(f'总扫描: {total}只, 实际返回: {len(stocks)}只')

ratings = {}
for s in stocks:
    r = s.get('rating','-')
    ratings[r] = ratings.get(r,0)+1

print('评级分布:', dict(sorted(ratings.items(), reverse=True)))

# 检查各种数据缺失
missing = {'ts_code':0,'score_a_core':0,'score_c_value_health':0,'bias_ma20':0}
for s in stocks:
    for k in missing:
        if s.get(k) is None:
            missing[k] += 1
print('缺失统计:', missing)

# 检查矛盾数据
conflicts = 0
for s in stocks:
    u = s.get('ultimate_score',0)
    if s.get('rating')=='S+' and u<90:
        conflicts += 1
    if s.get('rating')=='S' and u<85:
        conflicts += 1
print(f'评级与分数矛盾: {conflicts}只')

# 极端值检查
extremes = [s for s in stocks if abs(s.get('bias_ma20',0) or 0) > 50 or abs(s.get('ret_120',0) or 0) > 500]
print(f'技术指标极端异常: {len(extremes)}只')

codes = [s.get('ts_code','') for s in stocks]
dup = len(codes) - len(set(codes))
print(f'重复股票: {dup}只')

print()
print('=== TOP5详情 ===')
for s in stocks[:5]:
    print(f"  {s['name']}({s['ts_code']}): A={s.get('score_a_core',0):.1f} B={s.get('score_b_recognition',0):.1f} C={s.get('score_c_value_health',0):.1f} D={s.get('score_d_trend',0):.1f} 总分={s['ultimate_score']:.1f} 评级={s['rating']} MA20={s.get('bias_ma20',0):+.1f}% 60日={s.get('ret_60',0):+.1f}%")

# 分析各分项相关性
print()
print('=== 分项均值对比 ===')
for rating_name in ['S+','S','A']:
    subset = [s for s in stocks if s.get('rating')==rating_name]
    if not subset:
        continue
    avg_a = sum(s.get('score_a_core',0) for s in subset)/len(subset)
    avg_b = sum(s.get('score_b_recognition',0) for s in subset)/len(subset)
    avg_c = sum(s.get('score_c_value_health',0) for s in subset)/len(subset)
    avg_d = sum(s.get('score_d_trend',0) for s in subset)/len(subset)
    avg_u = sum(s.get('ultimate_score',0) for s in subset)/len(subset)
    print(f"  {rating_name}({len(subset)}只): A={avg_a:.1f} B={avg_b:.1f} C={avg_c:.1f} D={avg_d:.1f} 总分={avg_u:.1f}")
