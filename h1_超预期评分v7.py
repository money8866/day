#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半年报超预期筛选v7 — 修正Q4计算
关键修正：Q4_2025 = 年报(20251231) - 三季报(20250930)
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
            # 保留所有期，按end_date去重取最新
            pm = {}
            for _, r in df.iterrows():
                ed = r['end_date']
                if ed not in pm:
                    pm[ed] = {'rev': (r['total_revenue'] or 0) / 1e8,
                              'ni': (r['n_income_attr_p'] or 0) / 1e8}

            # 累计值提取
            ann_25 = pm.get('20251231', {'rev': 0, 'ni': 0})  # 年报累计
            q3_25 = pm.get('20250930', {'rev': 0, 'ni': 0})    # 三季报累计
            h1_25 = pm.get('20250630', {'rev': 0, 'ni': 0})    # 半年报累计
            q1_25 = pm.get('20250331', {'rev': 0, 'ni': 0})    # 一季报累计
            q1_26 = pm.get('20260331', {'rev': 0, 'ni': 0})    # 2026一季报累计
            h1_24 = pm.get('20240630', {'rev': 0, 'ni': 0})    # 2024半年报
            q1_24 = pm.get('20240331', {'rev': 0, 'ni': 0})    # 2024一季报
            ann_24 = pm.get('20241231', {'rev': 0, 'ni': 0})   # 2024年报
            q3_24 = pm.get('20240930', {'rev': 0, 'ni': 0})    # 2024三季报

            # ===== 单季度值 =====
            # Q1 = 一季报 (已经是单季)
            q1_26r, q1_26n = q1_26['rev'], q1_26['ni']
            q1_25r, q1_25n = q1_25['rev'], q1_25['ni']
            q1_24r, q1_24n = q1_24['rev'], q1_24['ni']
            # H1 = 半年报 (累计，Q1+Q2)
            h1_25r, h1_25n = h1_25['rev'], h1_25['ni']
            h1_24r, h1_24n = h1_24['rev'], h1_24['ni']
            # Q2 = H1 - Q1
            q2_25r = h1_25r - q1_25r
            q2_25n = h1_25n - q1_25n
            # Q4 = 年报 - 三季报
            q4_25r = ann_25['rev'] - q3_25['rev']
            q4_25n = ann_25['ni'] - q3_25['ni']
            # Q3 = 三季报 - 半年报
            q3_25r = q3_25['rev'] - h1_25r
            # Q4_2024
            q4_24r = ann_24['rev'] - q3_24['rev']
            q4_24n = ann_24['ni'] - q3_24['ni']

            row.update({
                'q1_26r': q1_26r, 'q4_25r': q4_25r, 'h1_25r': h1_25r, 'q1_25r': q1_25r,
                'q2_25r': q2_25r, 'h1_24r': h1_24r, 'q1_24r': q1_24r,
            })

            # ===== 同比 =====
            # Q1_2026同比 vs Q1_2025
            q1_26_yoy = (q1_26r / q1_25r - 1) * 100 if q1_25r > 0 else None
            row['q1_26_yoy'] = round(q1_26_yoy, 1) if q1_26_yoy is not None else None
            # Q1_2025同比 vs Q1_2024
            q1_25_yoy = (q1_25r / q1_24r - 1) * 100 if q1_24r > 0 else None
            row['q1_25_yoy'] = round(q1_25_yoy, 1) if q1_25_yoy is not None else None

            # Q1动量
            q1_mom = (q1_26_yoy - q1_25_yoy) if (q1_26_yoy is not None and q1_25_yoy is not None) else None
            row['q1_mom'] = round(q1_mom, 1) if q1_mom is not None else None

            # H1_2025同比
            h1_25_yoy = (h1_25r / h1_24r - 1) * 100 if h1_24r > 0 else None
            row['h1_25_yoy'] = round(h1_25_yoy, 1) if h1_25_yoy is not None else None

            # ===== H1_2026预测 =====
            if h1_25r > 0 and q1_25r > 0:
                ratio = h1_25r / q1_25r
                h1_26r = q1_26r * ratio
            else:
                ratio = 2.2
                h1_26r = q1_26r * 2.2
            row['ratio'] = round(ratio, 2)
            row['h1_26r'] = round(h1_26r, 2)

            # H1_2026同比
            h1_26_yoy = (h1_26r / h1_25r - 1) * 100 if h1_25r > 0 else None
            row['h1_26_yoy'] = round(h1_26_yoy, 1) if h1_26_yoy is not None else None

            # H1同比加速 (2026H1同比 vs 2025H1同比)
            h1_accel = (h1_26_yoy - h1_25_yoy) if (h1_26_yoy is not None and h1_25_yoy is not None) else None
            row['h1_accel'] = round(h1_accel, 1) if h1_accel is not None else None

            # 条件
            row['c1_q1_gt50'] = (q1_26_yoy or 0) > 50
            row['c2_h1_gt_q4'] = h1_26r > q4_25r if q4_25r > 0 else None
            row['c3_h1_accel'] = h1_accel is not None and h1_accel > 0
            row['c4_q1_mom'] = q1_mom is not None and q1_mom > 0

            # 净利
            q1_26n = q1_26n
            if q1_25n and abs(q1_25n) > 0.001:
                q1_ni_yoy = (q1_26n / q1_25n - 1) * 100
                row['q1_ni_yoy'] = round(q1_ni_yoy, 1)
            else:
                row['q1_ni_yoy'] = None

            if h1_25n and abs(h1_25n) > 0.001 and q1_25n and abs(q1_25n) > 0.001:
                ni_ratio = h1_25n / abs(q1_25n)
                h1_26n = q1_26n * ni_ratio
                row['h1_26n'] = round(h1_26n, 2)
                h1_ni_yoy = (h1_26n - h1_25n) / abs(h1_25n) * 100
                row['h1_ni_yoy'] = round(h1_ni_yoy, 1)
            else:
                row['h1_26n'] = None
                row['h1_ni_yoy'] = None

            # 评分
            score = 0
            if row['c1_q1_gt50']: score += 2
            if row['c2_h1_gt_q4']: score += 2
            if row['c3_h1_accel']: score += 3
            if row['c4_q1_mom']: score += 2
            if (q1_26_yoy or 0) > 100: score += 1
            row['score'] = score

            row['data_ok'] = True
        else:
            row['data_ok'] = False
    except Exception as e:
        row['data_ok'] = False
        row['error'] = str(e)[:40]

    results.append(row)
    time.sleep(0.06)

    s = row.get('score', 0)
    h1gt = row.get('c2_h1_gt_q4', False)
    parts = []
    if row.get('c1_q1_gt50'): parts.append('Q1>50%')
    if h1gt: parts.append('H1>Q4')
    if row.get('c3_h1_accel'): parts.append('H1加速')
    if row.get('c4_q1_mom'): parts.append('Q1动量')
    print('\r  [{}/{}] {}({}) score={} {}'.format(
        idx+1, len(all_pool), name, code[:6], s, ' '.join(parts)), end='', flush=True)

# ===== 排序输出 =====
scored = sorted([r for r in results if r.get('data_ok') and r.get('score', 0) >= 6],
                key=lambda x: (-x.get('score', 0), -(x.get('h1_accel', -9999) or -9999)))

print('\n\n' + '=' * 80)
print('超预期评分 >= 6分: {} 只'.format(len(scored)))
print('Q1同比>50%(+2) + H1>Q4(+2) + H1同比加速(+3) + Q1动量(+2) + Q1>100%(+1)')
print('=' * 80)

for i, r in enumerate(scored):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('\n  {}. {} {}({}) | {} | score={}'.format(i+1, pt, r['name'], r['code'][:6], r['theme'], r['score']))
    print('     Q1同比: 25Q1+{:.0f}% -> 26Q1+{:.0f}% (动量{:+.0f}%)'.format(
        r.get('q1_25_yoy',0) or 0, r.get('q1_26_yoy',0) or 0, r.get('q1_mom',0) or 0))
    print('     H1同比: 25H1+{:.0f}% -> 26H1预测+{:.0f}% (加速{:+.0f}%)'.format(
        r.get('h1_25_yoy',0) or 0, r.get('h1_26_yoy',0) or 0, r.get('h1_accel',0) or 0))
    print('     Q4_2025={:.1f}亿 -> H1_2026预测={:.1f}亿 | H1/Q1={:.2f}x'.format(
        r.get('q4_25r',0) or 0, r.get('h1_26r',0) or 0, r.get('ratio',0) or 0))
    if r.get('h1_26n'):
        print('     H1净利={:.1f}亿(+{:.0f}%) | 市值={:.0f}亿 PE={:.0f}'.format(
            r['h1_26n'], r.get('h1_ni_yoy',0) or 0, r.get('market_cap_yi',0) or 0, r.get('pe',0) or 0))

output = {
    'date': '2026-06-19',
    'method': 'v7: 修正Q4=年报-三季报. 超预期评分系统',
    'conditions': 'Q1同比>50%(+2), H1>Q4(+2), H1同比加速(+3), Q1动量正(+2), Q1>100%(+1)',
    'h1_accel_def': '2026H1预测同比 > 2025H1实际同比',
    'total': len(all_pool),
    'scored_ge6': len(scored),
    'results': scored[:40],
    'all_results': results,
}
out = r'D:\mystock\solo\report_daily\h1_超预期评分v7_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)
print('\n\n保存: {} ({} KB)'.format(out, os.path.getsize(out)//1024))
