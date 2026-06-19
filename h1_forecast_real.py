#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用Tushare forecast接口 + 巨潮资讯公告 + 网络搜索
获取2026H1真实业绩预告，替代ratio预测法
"""
import json, sys, time, re
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

with open(r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data['results']
print(f"全池 {len(results)} 只，查询2026H1业绩预告...\n")

forecast_data = []

for idx, stock in enumerate(results):
    code = stock['code']
    name = stock['name']
    
    try:
        # 查forecast接口 - 2026中报(end_date=20260630)
        df = pro.forecast(ts_code=code, start_date='20260101', end_date='20260630')
        
        if df is not None and len(df) > 0:
            df = df.sort_values('ann_date', ascending=False)
            latest = df.iloc[0]
            
            ann_date = latest.get('ann_date', '')
            ftype = latest.get('type', '')        # 预增/预减/扭亏/略增等
            p_min = latest.get('p_change_min', 0) or 0   # 净利同比下限%
            p_max = latest.get('p_change_max', 0) or 0   # 净利同比上限%
            np_min = latest.get('net_profit_min', 0) or 0 # 净利下限(万元)
            np_max = latest.get('net_profit_max', 0) or 0 # 净利上限(万元)
            summary = latest.get('summary', '') or ''
            
            print(f"  ✓ {code} {name}: 预告日期={ann_date}, 类型={ftype}, 净利同比={p_min:.1f}%~{p_max:.1f}%, 净利={np_min:.0f}~{np_max:.0f}万")
            
            forecast_data.append({
                'code': code, 'name': name, 'theme': stock.get('theme',''), 'pool': stock.get('pool',''),
                'ann_date': ann_date, 'type': ftype,
                'p_change_min': p_min, 'p_change_max': p_max,
                'net_profit_min_wan': np_min, 'net_profit_max_wan': np_max,
                'summary': summary[:200],
                'market_cap_yi': stock.get('market_cap_yi', 0),
                'pe': stock.get('pe', None),
                'score_v7': stock.get('score', 0),
            })
        else:
            print(f"  - {code} {name}: 暂无2026H1预告")
        
        time.sleep(0.06)
        if (idx+1) % 10 == 0:
            print(f"  ...已查 {idx+1}/{len(results)}")
            
    except Exception as e:
        print(f"  ! {code} {name}: 错误-{str(e)[:40]}")
        time.sleep(0.06)

print(f"\n{'='*60}")
print(f"共 {len(forecast_data)} 只有2026H1业绩预告")
print(f"{'='*60}")

# 按预告增速排序
forecast_data.sort(key=lambda x: -(x.get('p_change_max',0) or 0))

for m in forecast_data:
    print(f"\n  {m['code']} {m['name']} ({m['theme']}) [{m['pool']}]")
    print(f"    预告日期: {m['ann_date']} | 类型: {m['type']}")
    print(f"    净利同比区间: {m['p_change_min']:.1f}% ~ {m['p_change_max']:.1f}%")
    print(f"    预告净利: {m['net_profit_min_wan']:.0f}万 ~ {m['net_profit_max_wan']:.0f}万")
    pe_str = f"{m['pe']:.0f}" if m.get('pe') else 'N/A'
    print(f"    市值: {m['market_cap_yi']}亿 | PE: {pe_str} | v7评分: {m['score_v7']}分")
    if m['summary']:
        print(f"    摘要: {m['summary'][:100]}")

# 保存
out = r'D:\mystock\solo\report_daily\h1_forecast_real_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(forecast_data, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {out}")
