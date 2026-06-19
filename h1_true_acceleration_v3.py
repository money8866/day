#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真加速筛选v3 — 直接比较Q2同比vsQ1同比
思路：
- Q2_2026_est 不用恢复系数，而是用趋势外推
- 2025年：Q1环比Q4的斜率 vs Q2环比Q1的斜率 → Q2恢复方向
- 如果2025年Q2已经比Q1加速恢复，则2026年Q2也倾向加速
- 额外信号：2026Q1本身同比增速是否已经很高（说明趋势确立）

更直接的方法：
- 用 2026H1预测 = 2026Q1 + 2026Q2_est
- 2026Q2_est 用 "2025年Q2占H1比例" 推算
- 但这又回到了v1的Q1/H1比例问题

**最终方法：Q2同比>Q1同比等价于 Q2_2026/Q2_2025 > Q1_2026/Q1_2025**
即：(H1_2026_est - Q1_2026) / Q2_2025 > Q1_2026 / Q1_2025
这个条件什么时候成立？
当Q2_2025相比Q1_2025的环比恢复 > Q1_2026相比Q1_2025的同比增长时。

换一个角度：直接让用户看到的数字是——
- Q1同比 = Q1_26 / Q1_25
- H1同比 = (Q1_26 + Q2_26) / (Q1_25 + Q2_25)
- 关键在于Q2_26怎么估

**用环比动量法**：看2026Q1环比2025Q4的增速 vs 2025Q2环比2025Q1的增速
如果2026年Q1的环比恢复斜率 > 2025年Q2的环比恢复斜率，说明2026年景气更强
"""

import json
import sys
import time
import os
import statistics

sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TUSHARE_TOKEN)
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
    roe = stock.get('roe_waa', 0)
    inst_score = stock.get('inst_score', 0)
    theme_pros = stock.get('theme_prosperity', '')

    row = {
        'code': code, 'name': name, 'theme': theme, 'pool': pool,
        'q1_rev_yi': q1_rev_yi, 'q1_ni_yi': q1_ni_yi,
        'market_cap_yi': market_cap, 'pe': pe, 'roe_waa': roe,
        'inst_score': inst_score, 'theme_prosperity': theme_pros,
    }

    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            pd_map = {}
            for _, r in df.iterrows():
                ed = r['end_date']
                if ed not in pd_map:
                    pd_map[ed] = {
                        'rev': (r['total_revenue'] or 0) / 1e8,
                        'ni': (r['n_income_attr_p'] or 0) / 1e8,
                    }

            # 2026Q1
            q1_26 = pd_map.get('20260331', {})
            # 2025Q4
            q4_25 = pd_map.get('20251231', {})
            # 2025H1
            h1_25 = pd_map.get('20250630', {})
            # 2025Q1
            q1_25 = pd_map.get('20250331', {})
            # 2024H1
            h1_24 = pd_map.get('20240630', {})
            # 2024Q4
            q4_24 = pd_map.get('20241231', {})
            # 2024Q1
            q1_24 = pd_map.get('20240331', {})

            q1_26_rev = q1_26.get('rev', 0)
            q1_26_ni = q1_26.get('ni', 0)
            q4_25_rev = q4_25.get('rev', 0)
            q4_25_ni = q4_25.get('ni', 0)
            h1_25_rev = h1_25.get('rev', 0)
            h1_25_ni = h1_25.get('ni', 0)
            q1_25_rev = q1_25.get('rev', 0)
            q1_25_ni = q1_25.get('ni', 0)

            q2_25_rev = h1_25_rev - q1_25_rev
            q2_25_ni = h1_25_ni - q1_25_ni

            row.update({
                'q1_26_rev': q1_26_rev, 'q1_26_ni': q1_26_ni,
                'q4_25_rev': q4_25_rev, 'q4_25_ni': q4_25_ni,
                'h1_25_rev': h1_25_rev, 'h1_25_ni': h1_25_ni,
                'q1_25_rev': q1_25_rev, 'q1_25_ni': q1_25_ni,
                'q2_25_rev': q2_25_rev, 'q2_25_ni': q2_25_ni,
            })

            # ===== 核心方法: 用环比斜率比较 =====
            # 2025年的环比恢复: Q2_25/Q1_25 (环比倍数)
            # 2026年的环比恢复: Q1_26/Q4_25 (环比倍数, 已知)
            # 如果 Q1_26/Q4_25 > Q2_25/Q1_25 → 2026年恢复斜率更陡
            # 那么合理推断Q2_26恢复也会更强

            # Q1同比
            if q1_25_rev > 0:
                q1_rev_yoy = q1_26_rev / q1_25_rev * 100  # 倍数×100
            else:
                q1_rev_yoy = None
            row['q1_rev_yoy_pct'] = round(q1_rev_yoy - 100, 1) if q1_rev_yoy is not None else None

            # H1同比
            if h1_25_rev > 0:
                h1_rev_yoy = (q1_26_rev + q2_25_rev) / h1_25_rev * 100  # baseline: Q2保持不变
            else:
                h1_rev_yoy = None

            # 环比斜率信号
            if q1_25_rev > 0 and abs(q2_25_rev) > 0.01:
                recovery_25 = q2_25_rev / q1_25_rev  # 2025年Q2/Q1恢复倍数
                row['recovery_25'] = round(recovery_25, 3)
            else:
                recovery_25 = 1.5
                row['recovery_25'] = None

            if q4_25_rev > 0 and q4_25_rev > 0.01:
                recovery_26 = q1_26_rev / q4_25_rev  # 2026年Q1/Q4恢复倍数
                row['recovery_26'] = round(recovery_26, 3)
            else:
                recovery_26 = 0.5
                row['recovery_26'] = None

            # 斜率加速信号
            if row.get('recovery_25') and row.get('recovery_26'):
                slope_accel = recovery_26 > recovery_25
                slope_gap = recovery_26 - recovery_25
                row['slope_accel'] = slope_accel
                row['slope_gap'] = round(slope_gap, 3)
            else:
                row['slope_accel'] = None
                row['slope_gap'] = None

            # ===== Q2_2026预测 (关键步骤) =====
            # 如果2026恢复斜率 > 2025恢复斜率 → Q2_2026用更强的恢复系数
            # 恢复系数 = 2025年的Q2/Q1比例 × (1 + 斜率加速幅度)

            if row.get('recovery_25') and q1_25_rev > 0:
                base_ratio = q2_25_rev / q1_25_rev  # 基准恢复系数
                # 如果2026Q1/Q4的恢复比2025Q2/Q1的恢复更陡 → Q2也加速
                if row.get('slope_accel') and row.get('slope_gap', 0) > 0:
                    # 加速比例 = 基准 × (1 + slope_gap)
                    accel_ratio = base_ratio * (1 + row['slope_gap'] * 0.5)
                else:
                    # 减速
                    if row.get('slope_gap') is not None:
                        accel_ratio = base_ratio * (1 + row['slope_gap'] * 0.3)
                    else:
                        accel_ratio = base_ratio
                row['q2_recovery_used'] = round(accel_ratio, 3)
                q2_26_rev_est = q1_26_rev * accel_ratio
            else:
                q2_26_rev_est = q1_26_rev * 1.5  # 默认
                row['q2_recovery_used'] = 1.5

            q2_26_rev_est = max(0, q2_26_rev_est)
            row['q2_26_rev_est'] = round(q2_26_rev_est, 2)
            h1_26_rev_est = q1_26_rev + q2_26_rev_est
            row['h1_26_rev_est'] = round(h1_26_rev_est, 2)

            # H1同比
            if h1_25_rev > 0:
                h1_yoy = (h1_26_rev_est - h1_25_rev) / h1_25_rev * 100
                row['h1_rev_yoy_pct'] = round(h1_yoy, 1)
            else:
                row['h1_rev_yoy_pct'] = None

            # Q2同比
            if abs(q2_25_rev) > 0.01:
                q2_yoy = (q2_26_rev_est - q2_25_rev) / abs(q2_25_rev) * 100
                row['q2_rev_yoy_pct'] = round(q2_yoy, 1)
            else:
                row['q2_rev_yoy_pct'] = None

            # ===== 核心：H1同比 > Q1同比? =====
            q1_pct = row.get('q1_rev_yoy_pct')
            h1_pct = row.get('h1_rev_yoy_pct')
            if q1_pct is not None and h1_pct is not None:
                row['rev_accel'] = h1_pct > q1_pct
                row['rev_accel_gap'] = round(h1_pct - q1_pct, 1)
            else:
                row['rev_accel'] = None
                row['rev_accel_gap'] = None

            # 条件2: H1 > Q4
            row['h1_gt_q4'] = h1_26_rev_est > q4_25_rev if q4_25_rev > 0 else None

            # 净利同理
            if q1_25_ni and abs(q1_25_ni) > 0.001:
                q1_ni_yoy_pct = (q1_26_ni - q1_25_ni) / abs(q1_25_ni) * 100
                row['q1_ni_yoy_pct'] = round(q1_ni_yoy_pct, 1)
            else:
                row['q1_ni_yoy_pct'] = None

            # Q2净利预测
            q2_25_ni_val = q2_25_ni or 0
            if q1_25_ni and abs(q1_25_ni) > 0.001 and abs(q2_25_ni_val) > 0.001:
                ni_base = q2_25_ni_val / abs(q1_25_ni)
                if row.get('slope_gap') is not None:
                    ni_ratio = ni_base * (1 + row['slope_gap'] * 0.5)
                else:
                    ni_ratio = ni_base
                q2_26_ni_est = q1_26_ni * ni_ratio
            elif q1_26_ni > 0:
                q2_26_ni_est = q1_26_ni * 1.3
            else:
                q2_26_ni_est = 0
            q2_26_ni_est = max(0, q2_26_ni_est)
            row['q2_26_ni_est'] = round(q2_26_ni_est, 2)

            h1_26_ni = q1_26_ni + q2_26_ni_est
            row['h1_26_ni_est'] = round(h1_26_ni, 2)

            if abs(q2_25_ni_val) > 0.001:
                q2_ni_yoy = (q2_26_ni_est - q2_25_ni_val) / abs(q2_25_ni_val) * 100
                row['q2_ni_yoy_pct'] = round(q2_ni_yoy, 1)
            else:
                row['q2_ni_yoy_pct'] = None

            if h1_25_ni and abs(h1_25_ni) > 0.001:
                h1_ni_yoy = (h1_26_ni - h1_25_ni) / abs(h1_25_ni) * 100
                row['h1_ni_yoy_pct'] = round(h1_ni_yoy, 1)
            else:
                row['h1_ni_yoy_pct'] = None

            if row.get('q1_ni_yoy_pct') is not None and row.get('q2_ni_yoy_pct') is not None:
                row['ni_accel'] = row['q2_ni_yoy_pct'] > row['q1_ni_yoy_pct']
                row['ni_accel_gap'] = round(row['q2_ni_yoy_pct'] - row['q1_ni_yoy_pct'], 1)
            else:
                row['ni_accel'] = None
                row['ni_accel_gap'] = None

            row['data_ok'] = True
        else:
            row['data_ok'] = False
    except Exception as e:
        row['data_ok'] = False
        row['error'] = str(e)[:60]

    results.append(row)
    time.sleep(0.06)

    q1p = row.get('q1_rev_yoy_pct')
    h1p = row.get('h1_rev_yoy_pct')
    acc = row.get('rev_accel')
    h1q4 = row.get('h1_gt_q4')
    slope = row.get('slope_accel')
    gap = row.get('rev_accel_gap', 0) or 0
    parts = []
    if acc: parts.append('ACCEL')
    if slope: parts.append('SLOPE_UP')
    if h1q4: parts.append('H1>Q4')
    print('\r  [{}/{}] {}({}) Q1={}% H1={}% gap={} {}'.format(
        idx+1, len(all_pool), name, code[:6],
        '{:.0f}'.format(q1p) if q1p else '?',
        '{:.0f}'.format(h1p) if h1p else '?',
        '{:+.0f}'.format(gap), ' '.join(parts)), end='', flush=True)

print('\n完成')

# ===== 筛选 =====
rev_accel = [r for r in results if r.get('rev_accel') and r.get('h1_gt_q4')]
ni_accel = [r for r in results if r.get('ni_accel') and r.get('h1_gt_q4')]
both = [r for r in results if r.get('rev_accel') and r.get('ni_accel') and r.get('h1_gt_q4')]
slope_up = [r for r in results if r.get('slope_accel') and r.get('h1_gt_q4')]

rev_accel.sort(key=lambda x: x.get('rev_accel_gap', -9999) or -9999, reverse=True)
ni_accel.sort(key=lambda x: x.get('ni_accel_gap', -9999) or -9999, reverse=True)
slope_up.sort(key=lambda x: x.get('slope_gap', -9999) or -9999, reverse=True)

print('\n' + '=' * 80)
print('【环比斜率加速(Q1/Q4恢复 > Q2/Q1恢复) + H1>Q4】: {} 只'.format(len(slope_up)))
print('=' * 80)
for i, r in enumerate(slope_up):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('\n  {}. {} {}({}) | {}'.format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print('     2025恢复系数(Q2/Q1)={:.2f} | 2026恢复系数(Q1/Q4)={:.2f} | 斜率差={:+.3f}'.format(
        r.get('recovery_25', 0) or 0, r.get('recovery_26', 0) or 0, r.get('slope_gap', 0) or 0))
    print('     Q1同比+{:.0f}% → H1同比+{:.0f}% (加速{:+.0f}%) | H1={:.1f}亿 Q4={:.1f}亿'.format(
        r.get('q1_rev_yoy_pct', 0) or 0, r.get('h1_rev_yoy_pct', 0) or 0,
        r.get('rev_accel_gap', 0) or 0, r.get('h1_26_rev_est', 0) or 0, r.get('q4_25_rev', 0) or 0))

print('\n' + '=' * 80)
print('【营收加速(Q2同比>Q1同比) + H1>Q4】: {} 只'.format(len(rev_accel)))
print('=' * 80)
for i, r in enumerate(rev_accel):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('  {}. {} {}({}) | Q1同比+{:.0f}% → H1同比+{:.0f}% (加速{:+.0f}%)'.format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_rev_yoy_pct', 0) or 0, r.get('h1_rev_yoy_pct', 0) or 0,
        r.get('rev_accel_gap', 0) or 0))

print('\n' + '=' * 80)
print('【净利加速(Q2同比>Q1同比) + H1>Q4】: {} 只'.format(len(ni_accel)))
print('=' * 80)
for i, r in enumerate(ni_accel):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('  {}. {} {}({}) | Q1同比+{:.0f}% → H1同比+{:.0f}% (加速{:+.0f}%) | H1净利{:.1f}亿'.format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_ni_yoy_pct', 0) or 0, r.get('h1_ni_yoy_pct', 0) or 0,
        r.get('ni_accel_gap', 0) or 0, r.get('h1_26_ni_est', 0) or 0))

if both:
    print('\n' + '=' * 80)
    print('【双重加速】: {} 只'.format(len(both)))
    for i, r in enumerate(both):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        print('  {}. {} {}({}) | rev+{:.0f}% ni+{:.0f}%'.format(
            i+1, pt, r['name'], r['code'][:6],
            r.get('rev_accel_gap', 0) or 0, r.get('ni_accel_gap', 0) or 0))

# ===== 保存 =====
output = {
    'date': '2026-06-19',
    'method': 'v3: 环比斜率比较法。Q2_2026 = Q1_2026 × (2025年Q2/Q1恢复系数 × (1+斜率加速幅度×0.5))',
    'conditions': '1.H1同比>Q1同比 2.H1预测>Q4 3.环比斜率Q1/Q4>Q2/Q1',
    'total': len(all_pool),
    'slope_up_count': len(slope_up),
    'rev_accel_count': len(rev_accel),
    'ni_accel_count': len(ni_accel),
    'both_count': len(both),
    'slope_up': slope_up,
    'rev_accel': rev_accel,
    'ni_accel': ni_accel[:30],
    'both_accel': both,
    'all_results': results,
}

out_file = r'D:\mystock\solo\report_daily\h1_true_acceleration_v3_20260619.json'
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print('\n保存: {} ({} KB)'.format(out_file, os.path.getsize(out_file) // 1024))
