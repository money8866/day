#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全池检查: 先看哪些股 H1 2025 实际净利同比>100%
"""
import json, sys, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data['results']
print(f"全池 {len(results)} 只, 检查 H1 2025 实际净利同比\n")

h1_25_high = []
for idx, stock in enumerate(results):
    code = stock['code']
    name = stock['name']
    
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
            
            if h1_24n and abs(h1_24n) > 0.001:
                h1_25_ni_yoy = (h1_25n / h1_24n - 1) * 100
                if h1_25_ni_yoy > 50:
                    h1_25_high.append({
                        'code': code, 'name': name, 'theme': stock.get('theme',''),
                        'h1_25_ni_yoy': round(h1_25_ni_yoy, 1),
                        'h1_26_ni_pred': stock.get('h1_ni_yoy', 0),
                        'q1_26_ni_yoy': stock.get('q1_ni_yoy', 0),
                        'q1_26_yoy': stock.get('q1_26_yoy', 0),
                    })
        
        time.sleep(0.06)
        if (idx+1) % 10 == 0:
            print(f"  已查 {idx+1}/{len(results)} ...")
        
    except Exception as e:
        time.sleep(0.06)

print(f"\n{'='*60}")
print(f"H1 2025 净利同比 > 50% 的: {len(h1_25_high)}只")
print(f"{'='*60}")
h1_25_high.sort(key=lambda x: -x['h1_25_ni_yoy'])
for m in h1_25_high:
    print(f"  {m['code']} {m['name']} ({m['theme']})")
    print(f"    H1'25实际净利同比: {m['h1_25_ni_yoy']:.1f}%")
    print(f"    H1'26预测净利同比: {m['h1_26_ni_pred']:.1f}%")
    print(f"    Q1'26实际净利同比: {m['q1_26_ni_yoy']:.1f}%")
    print(f"    Q1'26营收同比: {m['q1_26_yoy']:.1f}%")
    print()
