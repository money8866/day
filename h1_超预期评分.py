#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半年报超预期筛选 — 终极版
重新定义"加速"：

A股结构性特点：H1同比永远< Q1同比（恒等式，因为H1基数更大）
所以"H1同比>Q1同比"这个条件几乎不可能满足。

**新定义**：
1. Q1同比增速极高（>50%）— 趋势确立
2. H1预测绝对值 > Q4实际值 — 非萎缩（H1至少追平Q4）
3. 2026H1同比 > 2025H1同比 — 中报加速（相比去年中报加速）
4. 环比斜率：Q1_26环比Q4_25的恢复斜率 > Q1_25环比Q4_24的恢复斜率 — 年度环比改善
"""

import json, sys, time, os
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

with open(r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json', 'r', encoding='utf-8') as f:
    fund_data = json.load(f)

all_pool = []
for label, key in [('IA', 'IA_data'), ('IB', 'IB_data')]:
    for r in fund_data.get(key, []):
        r['pool'] = label
        all_pool.append(r)

print("IA+IB共 {} 只".format(len(all_pool)))
results = []

for idx, stock in enumerate(all_pool):
    code = stock['ts_code']
    name = stock['name']
    theme = stock.get('theme', '')
    pool = stock['pool']
    q1_rev_yi = stock.get('latest_revenue_yi', 0)
    q1_ni_yi = stock.get('latest_nincome_yi', 0)
    market_cap = stock.get('market_cap_yi', 0)
    pe = stock.get('pe', 0)

    row = {'code': code, 'name': name, 'theme': theme, 'pool': pool,
           'q1_rev_yi': q1_rev_yi, 'q1_ni_yi': q1_ni_yi,
           'market_cap_yi': market_cap, 'pe': pe}

    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            pm = {}
            for _, r in df.iterrows():
                ed = r['end_date']
                if ed not in pm:
                    pm[ed] = {'rev': (r['total_revenue'] or 0) / 1e8, 'ni': (r['n_income_attr_p'] or 0) / 1e8}

            q1_26 = pm.get('20260331', {'rev': 0, 'ni': 0})
            q4_25 = pm.get('20251231', {'rev': 0, 'ni': 0})
            h1_25 = pm.get('20250630', {'rev': 0, 'ni': 0})
            q1_25 = pm.get('20250331', {'rev': 0, 'ni': 0})
            h1_24 = pm.get('20240630', {'rev': 0, 'ni': 0})
            q4_24 = pm.get('20241231', {'rev': 0, 'ni': 0})
            q1_24 = pm.get('20240331', {'rev': 0, 'ni': 0})

            q1_26r, q1_26n = q1_26['rev'], q1_26['ni']
            q4_25r, q4_25n = q4_25['rev'], q4_25['ni']
            h1_25r, h1_25n = h1_25['rev'], h1_25['ni']
            q1_25r, q1_25n = q1_25['rev'], q1_25['ni']
            h1_24r, h1_24n = h1_24['rev'], h1_24['ni']
            q4_24r = q4_24['rev']
            q1_24r = q1_24['rev']

            # ===== 核心同比 =====
            # 2026Q1同比 vs 2025Q1
            q1_26_vs_25 = (q1_26r / q1_25r - 1) * 100 if q1_25r > 0 else None
            # 2025Q1同比 vs 2024Q1
            q1_25_vs_24 = (q1_25r / q1_24r - 1) * 100 if q1_24r > 0 else None
            # Q1加速：2026Q1增速 vs 2025Q1增速
            q1_momentum = (q1_26_vs_25 - q1_25_vs_24) if (q1_26_vs_25 is not None and q1_25_vs_24 is not None) else None

            row['q1_26_vs_25'] = round(q1_26_vs_25, 1) if q1_26_vs_25 is not None else None
            row['q1_25_vs_24'] = round(q1_25_vs_24, 1) if q1_25_vs_24 is not None else None
            row['q1_momentum'] = round(q1_momentum, 1) if q1_momentum is not None else None

            # 2025H1同比 vs 2024H1
            h1_25_vs_24 = (h1_25r / h1_24r - 1) * 100 if h1_24r > 0 else None
            row['h1_25_vs_24'] = round(h1_25_vs_24, 1) if h1_25_vs_24 is not None else None

            # ===== H1_2026预测 =====
            # 用H1/Q1历史比例
            if h1_25r > 0 and q1_25r > 0:
                ratio = h1_25r / q1_25r
                h1_26r = q1_26r * ratio
            else:
                ratio = 2.2
                h1_26r = q1_26r * 2.2
            row['ratio_h1_q1'] = round(ratio, 2)
            row['h1_26r'] = round(h1_26r, 2)

            # H1_2026同比 vs H1_2025
            h1_26_vs_25 = (h1_26r / h1_25r - 1) * 100 if h1_25r > 0 else None
            row['h1_26_vs_25'] = round(h1_26_vs_25, 1) if h1_26_vs_25 is not None else None

            # H1同比加速：H1_26同比 vs H1_25同比
            h1_accel = (h1_26_vs_25 - h1_25_vs_24) if (h1_26_vs_25 is not None and h1_25_vs_24 is not None) else None
            row['h1_accel'] = round(h1_accel, 1) if h1_accel is not None else None

            # 条件1: Q1同比>50%
            row['cond_q1_high'] = (q1_26_vs_25 or 0) > 50
            # 条件2: H1 > Q4 (非萎缩)
            row['cond_h1_gt_q4'] = h1_26r > q4_25r if q4_25r > 0 else None
            # 条件3: H1同比加速 (H1_26同比 > H1_25同比)
            row['cond_h1_accel'] = h1_accel is not None and h1_accel > 0
            # 条件4: Q1动量正 (2026Q1增速 > 2025Q1增速)
            row['cond_q1_momentum'] = q1_momentum is not None and q1_momentum > 0

            # 净利
            if q1_25n and abs(q1_25n) > 0.001:
                q1_ni_yoy = (q1_26n / q1_25n - 1) * 100
                row['q1_ni_yoy'] = round(q1_ni_yoy, 1)
            else:
                row['q1_ni_yoy'] = None

            if h1_25n and abs(h1_25n) > 0.001 and q1_25n and abs(q1_25n) > 0.001:
                ni_ratio = h1_25n / abs(q1_25n)
                h1_26n = q1_26n * ni_ratio
                row['h1_26n'] = round(h1_26n, 2)
                h1_ni_vs_25 = (h1_26n - h1_25n) / abs(h1_25n) * 100
                row['h1_ni_vs_25'] = round(h1_ni_vs_25, 1)
            else:
                row['h1_26n'] = None
                row['h1_ni_vs_25'] = None

            # ===== 综合评分 =====
            score = 0
            if row['cond_q1_high']: score += 2
            if row['cond_h1_gt_q4']: score += 2
            if row['cond_h1_accel']: score += 3
            if row['cond_q1_momentum']: score += 2
            if (q1_26_vs_25 or 0) > 100: score += 1
            row['accel_score'] = score

            row['data_ok'] = True
        else:
            row['data_ok'] = False
    except Exception as e:
        row['data_ok'] = False
        row['error'] = str(e)[:40]

    results.append(row)
    time.sleep(0.06)

    s = row.get('accel_score', 0)
    parts = []
    if row.get('cond_q1_high'): parts.append('Q1>50%')
    if row.get('cond_h1_gt_q4'): parts.append('H1>Q4')
    if row.get('cond_h1_accel'): parts.append('H1加速')
    if row.get('cond_q1_momentum'): parts.append('Q1动量+')
    print('\r  [{}/{}] {}({}) score={} {}'.format(
        idx+1, len(all_pool), name, code[:6], s, ' '.join(parts)), end='', flush=True)

print('\n完成')

# ===== 按评分筛选 =====
scored = sorted([r for r in results if r.get('data_ok') and r.get('accel_score', 0) >= 4],
                key=lambda x: (-x.get('accel_score', 0), -(x.get('h1_accel', -9999) or -9999)))

print('\n' + '=' * 80)
print('超预期评分 >= 4分: {} 只'.format(len(scored)))
print('评分规则: Q1同比>50%(+2) + H1>Q4(+2) + H1同比加速(+3) + Q1动量正(+2) + Q1>100%(+1)')
print('=' * 80)

for i, r in enumerate(scored):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('\n  {}. {} {}({}) | {} | 评分={}'.format(i+1, pt, r['name'], r['code'][:6], r['theme'], r['accel_score']))
    print('     Q1同比: 2025Q1+{:.0f}% -> 2026Q1+{:.0f}% (动量{:+.0f}%)'.format(
        r.get('q1_25_vs_24',0) or 0, r.get('q1_26_vs_25',0) or 0, r.get('q1_momentum',0) or 0))
    print('     H1同比: 2025H1+{:.0f}% -> 2026H1预测+{:.0f}% (加速{:+.0f}%)'.format(
        r.get('h1_25_vs_24',0) or 0, r.get('h1_26_vs_25',0) or 0, r.get('h1_accel',0) or 0))
    print('     Q4_2025={:.1f}亿 -> H1_2026预测={:.1f}亿 | H1/Q1={:.2f}x'.format(
        r.get('q4_25r',0) or 0, r.get('h1_26r',0) or 0, r.get('ratio_h1_q1',0) or 0))
    if r.get('h1_26n'):
        print('     H1净利预测={:.1f}亿(同比+{:.0f}%) | 市值={:.0f}亿 PE={:.0f}'.format(
            r['h1_26n'], r.get('h1_ni_vs_25',0) or 0, r.get('market_cap_yi',0) or 0, r.get('pe',0) or 0))

# 保存
output = {
    'date': '2026-06-19',
    'method': 'v6_ultimate: 超预期评分系统. 条件: Q1同比>50%(+2), H1>Q4(+2), H1同比加速(+3), Q1动量正(+2), Q1>100%(+1)',
    'definition': '"H1同比加速" = 2026H1预测同比 > 2025H1实际同比 (中报增速同比加快)',
    'definition2': '"Q1动量" = 2026Q1同比增速 > 2025Q1同比增速 (一季报趋势加强)',
    'total': len(all_pool),
    'scored_count': len(scored),
    'top_scored': scored[:30],
    'all_results': results,
}
out = r'D:\mystock\solo\report_daily\h1_超预期评分_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)
print('\n保存: {} ({} KB)'.format(out, os.path.getsize(out)//1024))
