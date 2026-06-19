#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
H1'26预告净利同比 vs Q1'26实际净利同比 差距分析
找出 H1预告 >> Q1实际的股（加速股）
"""
import json, sys, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

# 加载真实预告
with open(r'D:\mystock\solo\report_daily\h1_forecast_real_20260619.json', 'r', encoding='utf-8') as f:
    forecast_list = json.load(f)

# 加载v7结果
with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    v7_data = json.load(f)
v7_map = {r['code']: r for r in v7_data['results']}

print(f"真实预告 {len(forecast_list)} 只，逐只计算 H1预告 vs Q1实际 差距...\n")

results = []
for idx, fc in enumerate(forecast_list):
    code = fc['code']
    name = fc['name']
    p_min = fc.get('p_change_min', 0) or 0
    p_max = fc.get('p_change_max', 0) or 0
    
    v7 = v7_map.get(code, {})
    q1_ni_yoy = v7.get('q1_ni_yoy', None)
    
    # 获取H1'25实际净利同比
    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            pm = {}
            for _, r in df.iterrows():
                ed = r['end_date']
                if ed not in pm:
                    pm[ed] = (r['n_income_attr_p'] or 0) / 1e8
            h1_25n = pm.get('20250630', 0)
            h1_24n = pm.get('20240630', 0)
            h1_25_ni_yoy = (h1_25n / h1_24n - 1) * 100 if h1_24n and abs(h1_24n) > 0.001 else None
        else:
            h1_25_ni_yoy = None
        time.sleep(0.06)
    except:
        h1_25_ni_yoy = None
        time.sleep(0.06)
    
    # 差距 = H1预告下限 - Q1实际
    gap = (p_min - q1_ni_yoy) if (q1_ni_yoy is not None) else None
    h1_gt_q1 = (p_min > q1_ni_yoy) if (q1_ni_yoy is not None) else None
    
    results.append({
        'code': code, 'name': name, 'theme': fc.get('theme',''), 'pool': fc.get('pool',''),
        'p_min': p_min, 'p_max': p_max,
        'q1_ni_yoy': q1_ni_yoy,
        'h1_25_ni_yoy': h1_25_ni_yoy,
        'gap': gap, 'h1_gt_q1': h1_gt_q1,
        'ann_date': fc.get('ann_date',''), 'type': fc.get('type',''),
        'market_cap_yi': fc.get('market_cap_yi', 0),
        'pe': fc.get('pe', None),
        'score_v7': fc.get('score_v7', 0),
    })
    
    q1_str = f"{q1_ni_yoy:.1f}%" if q1_ni_yoy is not None else "N/A"
    gap_str = f"{gap:+.1f}%" if gap is not None else "N/A"
    marker = "  ★加速★" if (h1_gt_q1) else ""
    print(f"  {idx+1:>2}. {code} {name:<6} | H1预告={p_min:>8.1f}%~{p_max:>8.1f}% | Q1实际={q1_str:>10} | 差距={gap_str:>10} {marker}")

# 按差距排序
results.sort(key=lambda x: -(x['gap'] or -9999))

print(f"\n{'='*70}")
print("★ H1预告 >> Q1实际（差距从大到小排序）")
print(f"{'='*70}\n")

accel = [r for r in results if r['h1_gt_q1']]
decel = [r for r in results if r['h1_gt_q1'] is False]
unknown = [r for r in results if r['h1_gt_q1'] is None]

print(f"加速股(H1预告>Q1实际): {len(accel)}只")
print(f"减速股(H1预告<Q1实际): {len(decel)}只")
print(f"Q1数据缺失: {len(unknown)}只\n")

for m in accel:
    print(f"  {m['code']} {m['name']} ({m['theme']}) [{m['pool']}]")
    print(f"    H1'26预告: {m['p_min']:.1f}% ~ {m['p_max']:.1f}%")
    print(f"    Q1'26实际: {m['q1_ni_yoy']:.1f}%")
    print(f"    差距: +{m['gap']:.1f}个百分点 (H1比Q1快)")
    print(f"    H1'25实际: {m['h1_25_ni_yoy']:.1f}%" if m['h1_25_ni_yoy'] else "    H1'25实际: N/A")
    print(f"    预告日期: {m['ann_date']} | 类型: {m['type']}")
    pe_str = f"{m['pe']:.0f}" if m.get('pe') else 'N/A'
    print(f"    市值: {m['market_cap_yi']}亿 | PE: {pe_str} | v7: {m['score_v7']}分")
    print()

print(f"\n{'='*70}")
print("△ 减速股(H1预告 < Q1实际，H1增速放缓)")
print(f"{'='*70}\n")
for m in decel:
    print(f"  {m['code']} {m['name']} ({m['theme']}) [{m['pool']}]")
    print(f"    H1'26预告: {m['p_min']:.1f}% ~ {m['p_max']:.1f}%")
    print(f"    Q1'26实际: {m['q1_ni_yoy']:.1f}%")
    print(f"    差距: {m['gap']:.1f}个百分点 (H1比Q1慢)")
    print()

# 保存
out = r'D:\mystock\solo\report_daily\h1_vs_q1_gap_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump({'accel': accel, 'decel': decel, 'unknown': unknown}, f, ensure_ascii=False, indent=2)
print(f"结果已保存: {out}")
