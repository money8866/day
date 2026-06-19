#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
净利润筛选: 2026H1预测同比>100%, 2025H1实际同比>100%, 2026Q1实际同比<100%
读取v7评分JSON, 补充H1 2024净利数据计算H1 2025同比
"""
import json, sys, time, os
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data['results']
print(f"总股票数: {len(results)}")

# 预筛选: h1_ni_yoy>100 and q1_ni_yoy<100
pre_filter = [r for r in results if (r.get('h1_ni_yoy') or 0) > 100 and (r.get('q1_ni_yoy') or 9999) < 100]
print(f"预筛选(h1_ni_yoy>100%, q1_ni_yoy<100%): {len(pre_filter)}只")

matches = []
for idx, stock in enumerate(pre_filter):
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
            
            h1_25n = pm.get('20250630', 0)  # 2025H1累计净利
            h1_24n = pm.get('20240630', 0)  # 2024H1累计净利
            
            if h1_24n and abs(h1_24n) > 0.001:
                h1_25_ni_yoy = (h1_25n / h1_24n - 1) * 100
                print(f"  {code} {name}: H1_25同比={h1_25_ni_yoy:.1f}% | H1_26预测同比={stock.get('h1_ni_yoy',0):.1f}% | Q1_26同比={stock.get('q1_ni_yoy',0):.1f}%")
                
                if h1_25_ni_yoy > 100:
                    matches.append({
                        'code': code, 'name': name, 'theme': stock.get('theme',''),
                        'pool': stock.get('pool',''),
                        'h1_26_ni_yoy_pred': stock.get('h1_ni_yoy', 0),
                        'h1_25_ni_yoy_actual': round(h1_25_ni_yoy, 1),
                        'q1_26_ni_yoy_actual': stock.get('q1_ni_yoy', 0),
                        'score': stock.get('score', 0),
                        'market_cap_yi': stock.get('market_cap_yi', 0),
                    })
        else:
            print(f"  {code} {name}: 无数据")
        
        time.sleep(0.06)
        
    except Exception as e:
        print(f"  {code} {name}: 错误-{str(e)[:40]}")
        time.sleep(0.06)

print(f"\n{'='*60}")
print(f"最终命中: {len(matches)}只")
print(f"{'='*60}")
for m in sorted(matches, key=lambda x: -x['h1_26_ni_yoy_pred']):
    print(f"  {m['code']} {m['name']} ({m['theme']}) [{m['pool']}]")
    print(f"    2026H1预测净利同比: {m['h1_26_ni_yoy_pred']:.1f}%")
    print(f"    2025H1实际净利同比: {m['h1_25_ni_yoy_actual']:.1f}%")
    print(f"    2026Q1实际净利同比: {m['q1_26_ni_yoy_actual']:.1f}%")
    print(f"    市值: {m['market_cap_yi']}亿 | v7评分: {m['score']}分")
    print()

# 保存结果
out_path = r'D:\mystock\solo\report_daily\ni_screen_h1_gt100_q1_lt100_20260619.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(matches, f, ensure_ascii=False, indent=2)
print(f"结果保存: {out_path}")
