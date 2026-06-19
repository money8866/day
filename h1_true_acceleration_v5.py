#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真加速筛选v5 — 终极方法
数学本质：H1同比>Q1同比 ⟺ Q2/Q1倍数(2026) > Q2/Q1倍数(2025)
即：2026年Q2相对Q1的恢复幅度 > 2025年的恢复幅度

方法：先看哪些公司2025年Q2/Q1比例已经>1（正常恢复）
然后看2026年Q1同比是否很高，暗示Q2恢复会更强
关键：2026年Q1环比Q4的恢复倍数 > 2025年Q2环比Q1的恢复倍数 → 景气加速

但如果这些都不满足，就用另一个角度：
看H1_2026 / Q1_2026 > H1_2025 / Q1_2025 → 说明2026年H1占比更大 → Q2更强
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
           'q1_rev_yi': q1_rev_yi, 'q1_ni_yi': q1_ni_yi, 'market_cap_yi': market_cap, 'pe': pe}

    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            pm = {}
            for _, r in df.iterrows():
                ed = r['end_date']
                if ed not in pm:
                    pm[ed] = {'rev': (r['total_revenue'] or 0) / 1e8, 'ni': (r['n_income_attr_p'] or 0) / 1e8}

            # 6个关键季度
            q1_26 = pm.get('20260331', {'rev': 0, 'ni': 0})
            q4_25 = pm.get('20251231', {'rev': 0, 'ni': 0})
            h1_25 = pm.get('20250630', {'rev': 0, 'ni': 0})
            q1_25 = pm.get('20250331', {'rev': 0, 'ni': 0})
            h1_24 = pm.get('20240630', {'rev': 0, 'ni': 0})
            q1_24 = pm.get('20240331', {'rev': 0, 'ni': 0})

            q1_26r, q1_26n = q1_26['rev'], q1_26['ni']
            q4_25r = q4_25['rev']
            h1_25r, h1_25n = h1_25['rev'], h1_25['ni']
            q1_25r, q1_25n = q1_25['rev'], q1_25['ni']
            h1_24r = h1_24['rev']
            q1_24r = q1_24['rev']

            q2_25r = h1_25r - q1_25r  # Q2_2025
            q2_25n = h1_25n - q1_25n

            # ===== 同比计算 =====
            # Q1同比
            q1_yoy = (q1_26r / q1_25r - 1) * 100 if q1_25r > 0 else None
            row['q1_yoy'] = round(q1_yoy, 1) if q1_yoy is not None else None

            # H1同比 (假设Q2比例 = 2025年比例)
            # baseline H1同比 = q1_yoy (恒等)
            # 所以需要用不同的Q2比例

            # 2025年: H1/Q1比例
            ratio_25 = h1_25r / q1_25r if q1_25r > 0 else 2.0
            row['ratio_25'] = round(ratio_25, 2)

            # 现在关键: 2026年H1/Q1比例能比2025年更大吗？
            # H1_2026/Q1_2026 = (Q1_26 + Q2_26) / Q1_26 = 1 + Q2_26/Q1_26
            # H1_2025/Q1_2025 = 1 + Q2_25/Q1_25
            # 加速 ⟺ 1+Q2_26/Q1_26 > 1+Q2_25/Q1_25 ⟺ Q2_26/Q1_26 > Q2_25/Q1_25

            # Q2_25/Q1_25 就是 2025年Q2的恢复倍数
            q2q1_25 = q2_25r / q1_25r if q1_25r > 0 and abs(q2_25r) > 0.001 else None
            row['q2q1_25'] = round(q2q1_25, 2) if q2q1_25 is not None else None

            # Q1_26/Q4_25 就是 2026年Q1的恢复倍数
            q1q4_26 = q1_26r / q4_25r if q4_25r > 0.001 else None
            row['q1q4_26'] = round(q1q4_26, 2) if q1q4_26 is not None else None

            # 如果Q1_26/Q4_25 > Q2_25/Q1_25 → 2026恢复斜率更陡 → Q2也倾向加速
            if q2q1_25 is not None and q1q4_26 is not None:
                slope_signal = q1q4_26 > q2q1_25
                slope_gap = q1q4_26 - q2q1_25
                row['slope_signal'] = slope_signal
                row['slope_gap'] = round(slope_gap, 2)
            else:
                slope_signal = False
                slope_gap = None

            # 用斜率信号调整Q2比例
            if q2q1_25 is not None:
                if slope_signal and slope_gap > 0:
                    # 2026恢复更陡 → Q2比例上调
                    adj_factor = 1 + slope_gap * 0.8  # 斜率差越大上调越多
                    q2q1_26 = q2q1_25 * adj_factor
                else:
                    # 恢复更平 → Q2比例下调
                    if slope_gap is not None:
                        q2q1_26 = q2q1_25 * (1 + slope_gap * 0.5)
                    else:
                        q2q1_26 = q2q1_25
                q2q1_26 = max(0.3, q2q1_26)  # 至少Q2是Q1的0.3倍
            else:
                q2q1_26 = 1.5  # 默认
            row['q2q1_26'] = round(q2q1_26, 2)

            # Q2_2026估算
            q2_26r = q1_26r * q2q1_26
            h1_26r = q1_26r + q2_26r
            row['q2_26r'] = round(q2_26r, 2)
            row['h1_26r'] = round(h1_26r, 2)
            row['ratio_26'] = round(h1_26r / q1_26r, 2) if q1_26r > 0 else None

            # H1同比
            h1_yoy = (h1_26r - h1_25r) / h1_25r * 100 if h1_25r > 0 else None
            row['h1_yoy'] = round(h1_yoy, 1) if h1_yoy is not None else None

            # H1 > Q4?
            row['h1_gt_q4'] = h1_26r > q4_25r if q4_25r > 0 else None

            # 加速?
            if q1_yoy is not None and h1_yoy is not None:
                row['accel'] = h1_yoy > q1_yoy
                row['accel_gap'] = round(h1_yoy - q1_yoy, 1)
            else:
                row['accel'] = None
                row['accel_gap'] = None

            # 净利同理
            if q2_25n and abs(q2_25n) > 0.001 and q1_25n and abs(q1_25n) > 0.001:
                ni_q2q1_25 = q2_25n / abs(q1_25n)
                if slope_gap is not None:
                    ni_q2q1_26 = ni_q2q1_25 * (1 + slope_gap * 0.5)
                else:
                    ni_q2q1_26 = ni_q2q1_25
                ni_q2q1_26 = max(0.3, ni_q2q1_26)
            elif q1_26n > 0:
                ni_q2q1_26 = 1.3
            else:
                ni_q2q1_26 = 1.0

            q2_26n = q1_26n * ni_q2q1_26
            h1_26n = q1_26n + q2_26n
            row['h1_26n'] = round(h1_26n, 2)

            q1_ni_yoy = (q1_26n / q1_25n - 1) * 100 if q1_25n and abs(q1_25n) > 0.001 else None
            row['q1_ni_yoy'] = round(q1_ni_yoy, 1) if q1_ni_yoy is not None else None

            if h1_25n and abs(h1_25n) > 0.001:
                h1_ni_yoy = (h1_26n - h1_25n) / abs(h1_25n) * 100
                row['h1_ni_yoy'] = round(h1_ni_yoy, 1)
                ni_accel = h1_ni_yoy > q1_ni_yoy if q1_ni_yoy is not None else None
                row['ni_accel'] = ni_accel
                if ni_accel and q1_ni_yoy is not None:
                    row['ni_accel_gap'] = round(h1_ni_yoy - q1_ni_yoy, 1)
                else:
                    row['ni_accel_gap'] = None
            else:
                row['h1_ni_yoy'] = None
                row['ni_accel'] = None
                row['ni_accel_gap'] = None

            row['data_ok'] = True
        else:
            row['data_ok'] = False
    except Exception as e:
        row['data_ok'] = False
        row['error'] = str(e)[:40]

    results.append(row)
    time.sleep(0.06)

    ag = row.get('accel_gap', 0) or 0
    sl = row.get('slope_signal', False)
    h1q4 = row.get('h1_gt_q4', False)
    parts = []
    if row.get('accel'): parts.append('ACCEL')
    if sl: parts.append('SLOPE')
    if h1q4: parts.append('H1>Q4')
    print('\r  [{}/{}] {}({}) Q1={}% H1={}% gap={:+.0f}% r26={}'.format(
        idx+1, len(all_pool), name, code[:6],
        '{:.0f}'.format(row.get('q1_yoy') or 0),
        '{:.0f}'.format(row.get('h1_yoy') or 0),
        ag, row.get('ratio_26', '?'),
        ' '.join(parts)), end='', flush=True)

# ===== 结果 =====
rev_accel = sorted([r for r in results if r.get('accel') and r.get('h1_gt_q4')],
                   key=lambda x: x.get('accel_gap', -9999) or -9999, reverse=True)
ni_accel = sorted([r for r in results if r.get('ni_accel') and r.get('h1_gt_q4')],
                  key=lambda x: x.get('ni_accel_gap', -9999) or -9999, reverse=True)
both = [r for r in rev_accel if r.get('ni_accel')]
slope_up = sorted([r for r in results if r.get('slope_signal') and r.get('h1_gt_q4')],
                  key=lambda x: x.get('slope_gap', -9999) or -9999, reverse=True)

print('\n\n' + '=' * 80)
print('环比斜率加速(Q1/Q4 > Q2/Q1) + H1>Q4: {} 只'.format(len(slope_up)))
print('=' * 80)
for i, r in enumerate(slope_up):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('\n  {}. {} {}({}) | {}'.format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print('     2025 Q2/Q1={:.2f}x | 2026 Q1/Q4={:.2f}x | 斜率差{:+.2f}'.format(
        r.get('q2q1_25',0) or 0, r.get('q1q4_26',0) or 0, r.get('slope_gap',0) or 0))
    print('     Q2/Q1(调整)={:.2f}x → H1/Q1={:.2f}x(vs 2025 {:.2f}x)'.format(
        r.get('q2q1_26',0) or 0, r.get('ratio_26',0) or 0, r.get('ratio_25',0) or 0))
    print('     Q1同比+{:.0f}% → H1同比+{:.0f}% (加速{:+.0f}%) | H1={:.1f}亿 > Q4={:.1f}亿'.format(
        r.get('q1_yoy',0) or 0, r.get('h1_yoy',0) or 0, r.get('accel_gap',0) or 0,
        r.get('h1_26r',0) or 0, r.get('q4_25r',0) or 0))
    if r.get('h1_26n'):
        print('     H1净利={:.1f}亿(同比+{:.0f}%)'.format(r['h1_26n'], r.get('h1_ni_yoy',0) or 0))

print('\n' + '=' * 80)
print('营收加速(H1同比>Q1同比) + H1>Q4: {} 只'.format(len(rev_accel)))
print('=' * 80)
for i, r in enumerate(rev_accel):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('  {}. {} {}({}) | Q1+{:.0f}% → H1+{:.0f}% (加速{:+.0f}%) | H1={:.1f}亿 Q4={:.1f}亿'.format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_yoy',0) or 0, r.get('h1_yoy',0) or 0, r.get('accel_gap',0) or 0,
        r.get('h1_26r',0) or 0, r.get('q4_25r',0) or 0))

if both:
    print('\n双重加速: {} 只'.format(len(both)))
    for r in both:
        print('  {} {}({}) | rev+{:.0f}% ni+{:.0f}%'.format(
            '[IA]' if r['pool']=='IA' else '[IB]', r['name'], r['code'][:6],
            r.get('accel_gap',0) or 0, r.get('ni_accel_gap',0) or 0))

# near
near = sorted([r for r in results if r.get('h1_gt_q4') and r.get('accel_gap') is not None and -5 < r['accel_gap'] < 0],
              key=lambda x: x.get('accel_gap', -9999), reverse=True)
if near:
    print('\n接近加速(-5%以内): {} 只'.format(len(near)))
    for i, r in enumerate(near[:10]):
        print('  {}. {} {}({}) | Q1+{:.0f}% H1+{:.0f}% {:+.0f}% | slope={}'.format(
            i+1, '[IA]' if r['pool']=='IA' else '[IB]', r['name'], r['code'][:6],
            r.get('q1_yoy',0) or 0, r.get('h1_yoy',0) or 0, r.get('accel_gap',0) or 0,
            r.get('slope_signal', False)))

# 保存
output = {
    'date': '2026-06-19', 'method': 'v5: slope-adjusted Q2 recovery',
    'total': len(all_pool),
    'slope_up_count': len(slope_up),
    'rev_accel_count': len(rev_accel),
    'ni_accel_count': len(ni_accel),
    'both_count': len(both),
    'slope_up': slope_up, 'rev_accel': rev_accel,
    'ni_accel': ni_accel[:20], 'both_accel': both,
    'all_results': results,
}
out = r'D:\mystock\solo\report_daily\h1_true_acceleration_v5_20260619.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)
print('\n保存: {} ({} KB)'.format(out, os.path.getsize(out)//1024))
