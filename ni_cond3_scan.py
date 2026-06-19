#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
净利筛选: H1'26预测同比>100%, H1'25实际同比>100%, H1'26预测同比>Q1'26实际同比
"""
import json, sys, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data['results']
print(f"全池: {len(results)}只\n")

matches = []
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
            q1_25n = pm.get('20250331', 0)

            # H1'25实际净利同比
            h1_25_ni_yoy = (h1_25n / h1_24n - 1) * 100 if h1_24n and abs(h1_24n) > 0.001 else None

            # 已有: h1_ni_yoy = H1'26预测同比, q1_ni_yoy = Q1'26实际同比
            h1_26_ni_yoy = stock.get('h1_ni_yoy')  # H1'26预测同比
            q1_26_ni_yoy = stock.get('q1_ni_yoy')  # Q1'26实际同比

            cond1 = (h1_26_ni_yoy or 0) > 100        # H1'26预测>100%
            cond2 = (h1_25_ni_yoy or -9999) > 100   # H1'25实际>100%
            cond3 = (h1_26_ni_yoy or 0) > (q1_26_ni_yoy or 0)  # H1'26预测 > Q1'26实际

            marker = "  ★" if (cond1 and cond2 and cond3) else ""
            print(f"  {idx+1:>2}. {code} {name:<6} | H1'25={h1_25_ni_yoy:>8.1f}% | H1'26预测={h1_26_ni_yoy:>8.1f}% | Q1'26={q1_26_ni_yoy:>8.1f}%{marker}")

            if cond1 and cond2 and cond3:
                matches.append({
                    'code': code, 'name': name,
                    'theme': stock.get('theme', ''),
                    'pool': stock.get('pool', ''),
                    'h1_25_ni_yoy': round(h1_25_ni_yoy, 1) if h1_25_ni_yoy else None,
                    'h1_26_ni_yoy_pred': h1_26_ni_yoy,
                    'q1_26_ni_yoy': q1_26_ni_yoy,
                    'score': stock.get('score', 0),
                    'market_cap_yi': stock.get('market_cap_yi', 0),
                    'pe': stock.get('pe', 0),
                })
        else:
            print(f"  {idx+1:>2}. {code} {name}: 无数据")
        
        time.sleep(0.06)

    except Exception as e:
        print(f"  {idx+1:>2}. {code} {name}: 错误-{str(e)[:30]}")
        time.sleep(0.06)

print(f"\n{'='*65}")
print(f"★ 最终命中: {len(matches)}只")
print(f"{'='*65}")
for m in sorted(matches, key=lambda x: -(x['h1_26_ni_yoy_pred'] or 0)):
    print(f"\n  {m['code']} {m['name']} ({m['theme']}) [{m['pool']}]")
    print(f"    2025H1实际净利同比: {m['h1_25_ni_yoy']}%")
    print(f"    2026H1预测净利同比: {m['h1_26_ni_yoy_pred']}%")
    print(f"    2026Q1实际净利同比: {m['q1_26_ni_yoy']}%")
    print(f"    ↑ H1'26预测 > Q1'26实际: {m['h1_26_ni_yoy_pred'] > m['q1_26_ni_yoy']}")
    print(f"    市值: {m['market_cap_yi']}亿 | PE: {m['pe']:.0f} | v7评分: {m['score']}分")

# 保存
out = r'D:\mystock\solo\report_daily\ni_cond3_h1gt100_h1_25gt100_h1gtq1_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(matches, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {out}")
