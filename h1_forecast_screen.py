#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用真实业绩预告数据重新筛选
条件: H1'26预告净利同比>100% AND H1'25实际净利同比>100% AND H1'26预告>Q1'26实际
"""
import json, sys, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

# 加载真实预告数据
with open(r'D:\mystock\solo\report_daily\h1_forecast_real_20260619.json', 'r', encoding='utf-8') as f:
    forecast_list = json.load(f)

# 加载v7结果(含Q1实际数据)
with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    v7_data = json.load(f)
v7_map = {r['code']: r for r in v7_data['results']}

print(f"真实预告数据: {len(forecast_list)}只\n")
print(f"{'='*65}")
print("逐只核查 H1'25实际净利同比 + Q1'26实际净利同比")
print(f"{'='*65}\n")

results = []
for idx, fc in enumerate(forecast_list):
    code = fc['code']
    name = fc['name']
    p_min = fc.get('p_change_min', 0) or 0   # H1'26预告净利同比下限
    p_max = fc.get('p_change_max', 0) or 0   # H1'26预告净利同比上限
    
    # 从v7获取Q1'26实际净利同比
    v7 = v7_map.get(code, {})
    q1_ni_yoy = v7.get('q1_ni_yoy', None)  # Q1'26实际净利同比
    
    # 从Tushare获取H1'25实际净利同比
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
    
    # 三条件
    cond1 = p_min > 100          # H1'26预告>100%
    cond2 = (h1_25_ni_yoy or -9999) > 100   # H1'25实际>100%
    cond3 = p_min > (q1_ni_yoy or -9999)      # H1'26预告 > Q1'26实际
    
    hit = "★命中" if (cond1 and cond2 and cond3) else ""
    print(f"  {idx+1:>2}. {code} {name:<6} | H1'25实际={h1_25_ni_yoy:>8.1f}% | H1'26预告={p_min:>8.1f}%~{p_max:>8.1f}% | Q1'26实际={q1_ni_yoy:>8.1f}% {hit}")
    
    if cond1 and cond2 and cond3:
        results.append({
            'code': code, 'name': name,
            'theme': fc.get('theme',''), 'pool': fc.get('pool',''),
            'h1_25_ni_yoy_actual': round(h1_25_ni_yoy, 1) if h1_25_ni_yoy else None,
            'h1_26_ni_yoy_forecast_min': p_min,
            'h1_26_ni_yoy_forecast_max': p_max,
            'q1_26_ni_yoy_actual': q1_ni_yoy,
            'ann_date': fc.get('ann_date',''),
            'type': fc.get('type',''),
            'market_cap_yi': fc.get('market_cap_yi', 0),
            'pe': fc.get('pe', None),
            'score_v7': fc.get('score_v7', 0),
        })

print(f"\n{'='*65}")
print(f"★ 命中(三条件同时满足): {len(results)}只")
print(f"{'='*65}")
for m in sorted(results, key=lambda x: -(x.get('h1_26_ni_yoy_forecast_min',0) or 0)):
    print(f"\n  {m['code']} {m['name']} ({m['theme']}) [{m['pool']}]")
    print(f"    2025H1实际净利同比: {m['h1_25_ni_yoy_actual']}%")
    print(f"    2026H1预告净利同比: {m['h1_26_ni_yoy_forecast_min']:.1f}% ~ {m['h1_26_ni_yoy_forecast_max']:.1f}%")
    print(f"    2026Q1实际净利同比: {m['q1_26_ni_yoy_actual']:.1f}%")
    print(f"    ↑ H1'26预告 > Q1'26实际: {m['h1_26_ni_yoy_forecast_min']:.1f}% > {m['q1_26_ni_yoy_actual']:.1f}% ✅")
    print(f"    预告日期: {m['ann_date']} | 类型: {m['type']}")
    pe_str = f"{m['pe']:.0f}" if m.get('pe') else 'N/A'
    print(f"    市值: {m['market_cap_yi']}亿 | PE: {pe_str} | v7评分: {m['score_v7']}分")

# 保存
out = r'D:\mystock\solo\report_daily\h1_forecast_screened_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {out}")
