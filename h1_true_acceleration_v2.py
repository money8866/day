#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真加速筛选v2 — 更精确的逻辑
目标：2026H1同比增速 > 2026Q1同比增速
思路：H1同比增速 = (Q1_2026 + Q2_2026) / (Q1_2025 + Q2_2025)
      Q1同比增速 = Q1_2026 / Q1_2025
      如果Q2_2026/Q2_2025 > Q1_2026/Q1_2025，则H1同比 > Q1同比

条件1: H1同比 > Q1同比（真加速）
条件2: H1预测值 > Q4实际值（非萎缩）
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

            q1_26 = pd_map.get('20260331', {})
            q4_25 = pd_map.get('20251231', {})
            h1_25 = pd_map.get('20250630', {})
            q1_25 = pd_map.get('20250331', {})
            h1_24 = pd_map.get('20240630', {})

            q1_26_rev = q1_26.get('rev', 0)
            q1_26_ni = q1_26.get('ni', 0)
            q4_25_rev = q4_25.get('rev', 0)
            q4_25_ni = q4_25.get('ni', 0)
            h1_25_rev = h1_25.get('rev', 0)
            h1_25_ni = h1_25.get('ni', 0)
            q1_25_rev = q1_25.get('rev', 0)
            q1_25_ni = q1_25.get('ni', 0)
            h1_24_rev = h1_24.get('rev', 0)
            h1_24_ni = h1_24.get('ni', 0)

            row['q1_26_rev'] = q1_26_rev
            row['q1_26_ni'] = q1_26_ni
            row['q4_25_rev'] = q4_25_rev
            row['q4_25_ni'] = q4_25_ni
            row['h1_25_rev'] = h1_25_rev
            row['h1_25_ni'] = h1_25_ni
            row['q1_25_rev'] = q1_25_rev
            row['q1_25_ni'] = q1_25_ni

            # ===== 方法: 直接比较同比增速率 =====
            # Q1同比 = Q1_2026 / Q1_2025 - 1
            # Q2同比 = Q2_2026 / Q2_2025 - 1
            # Q2_2025 = H1_2025 - Q1_2025

            # Q1同比增速(营收)
            if q1_25_rev > 0:
                q1_rev_yoy = (q1_26_rev - q1_25_rev) / q1_25_rev * 100
            else:
                q1_rev_yoy = None
            row['q1_rev_yoy'] = round(q1_rev_yoy, 1) if q1_rev_yoy is not None else None

            # Q2_2025基准
            q2_25_rev = h1_25_rev - q1_25_rev
            row['q2_25_rev'] = q2_25_rev
            q2_25_ni = h1_25_ni - q1_25_ni
            row['q2_25_ni'] = q2_25_ni

            # Q2_2026预测 = H1_2026预测 - Q1_2026
            # 用多种方法预测Q2_2026:
            # 方法A: 同比增速延续（Q2同比 = Q1同比）→ 这是baseline
            # 方法B: 环比恢复法（用2025年Q2/Q1环比推算）
            # 方法C: 动量法（如果Q1环比Q4加速，Q2继续）

            # 先用方法A（baseline: Q2同比=Q1同比）
            # 再看能不能比Q1同比更高

            # 核心逻辑：什么条件下H1同比 > Q1同比？
            # H1同比 = (Q1_26 + Q2_26) / (Q1_25 + Q2_25) - 1
            # Q1同比 = Q1_26 / Q1_25 - 1
            #
            # 令 x = Q2_26 / Q2_25 (Q2同比增速+1)
            # 令 a = Q1_26 / Q1_25 (Q1同比增速+1)
            # 令 s = Q1_25 / Q2_25 (Q1占Q2的比例)
            #
            # H1同比 > Q1同比 ⟺ (Q1_26 + Q2_26)/(Q1_25 + Q2_25) > Q1_26/Q1_25
            # ⟺ x > a （即Q2同比增速 > Q1同比增速）
            #
            # 所以核心就是：Q2同比增长率 > Q1同比增长率

            # 用2025年Q2的环比Q1恢复来预测Q2_2026
            # Q2_2025 / Q1_2025 = 恢复系数
            # Q2_2026 = Q1_2026 × 恢复系数
            if q1_25_rev > 0 and q2_25_rev > 0:
                recovery_ratio = q2_25_rev / q1_25_rev  # 2025年Q2/Q1恢复系数
                row['recovery_ratio'] = round(recovery_ratio, 3)
                q2_26_rev_est = q1_26_rev * recovery_ratio
            else:
                recovery_ratio = 1.5  # 默认Q2是Q1的1.5倍
                row['recovery_ratio'] = 1.5
                q2_26_rev_est = q1_26_rev * 1.5

            row['q2_26_rev_est'] = round(q2_26_rev_est, 2)

            # H1_2026预测
            h1_26_rev_est = q1_26_rev + q2_26_rev_est
            row['h1_26_rev_est'] = round(h1_26_rev_est, 2)

            # H1同比
            if h1_25_rev > 0:
                h1_rev_yoy = (h1_26_rev_est - h1_25_rev) / h1_25_rev * 100
            else:
                h1_rev_yoy = None
            row['h1_rev_yoy'] = round(h1_rev_yoy, 1) if h1_rev_yoy is not None else None

            # Q2同比
            if q2_25_rev > 0:
                q2_rev_yoy = (q2_26_rev_est - q2_25_rev) / abs(q2_25_rev) * 100
            else:
                q2_rev_yoy = None
            row['q2_rev_yoy'] = round(q2_rev_yoy, 1) if q2_rev_yoy is not None else None

            # ===== 核心判断 =====
            # 条件1: Q2同比 > Q1同比（等价于H1同比 > Q1同比）
            if q1_rev_yoy is not None and q2_rev_yoy is not None:
                row['rev_accel'] = q2_rev_yoy > q1_rev_yoy
                row['rev_accel_gap'] = round(q2_rev_yoy - q1_rev_yoy, 1)
            else:
                row['rev_accel'] = None
                row['rev_accel_gap'] = None

            # 条件2: H1预测 > Q4实际
            row['h1_gt_q4'] = h1_26_rev_est > q4_25_rev if q4_25_rev > 0 else None

            # 净利同理
            if q1_25_ni and q1_25_ni != 0:
                q1_ni_yoy = (q1_26_ni - q1_25_ni) / abs(q1_25_ni) * 100
            else:
                q1_ni_yoy = None
            row['q1_ni_yoy'] = round(q1_ni_yoy, 1) if q1_ni_yoy is not None else None

            q2_25_ni_val = q2_25_ni or 0
            if q1_25_ni and abs(q1_25_ni) > 0.001 and abs(q2_25_ni_val) > 0.001:
                ni_recovery = q2_25_ni_val / abs(q1_25_ni)
                q2_26_ni_est = q1_26_ni * ni_recovery
                row['ni_recovery_ratio'] = round(ni_recovery, 3)
            elif q1_26_ni > 0:
                q2_26_ni_est = q1_26_ni * 1.3
                row['ni_recovery_ratio'] = 1.3
            else:
                q2_26_ni_est = 0
                row['ni_recovery_ratio'] = None

            row['q2_26_ni_est'] = round(q2_26_ni_est, 2)
            h1_26_ni_est = q1_26_ni + q2_26_ni_est
            row['h1_26_ni_est'] = round(h1_26_ni_est, 2)

            if h1_25_ni and abs(h1_25_ni) > 0.001:
                h1_ni_yoy = (h1_26_ni_est - h1_25_ni) / abs(h1_25_ni) * 100
                row['h1_ni_yoy'] = round(h1_ni_yoy, 1)
            else:
                row['h1_ni_yoy'] = None

            if q2_25_ni_val and abs(q2_25_ni_val) > 0.001:
                q2_ni_yoy = (q2_26_ni_est - q2_25_ni_val) / abs(q2_25_ni_val) * 100
                row['q2_ni_yoy'] = round(q2_ni_yoy, 1)
            else:
                row['q2_ni_yoy'] = None

            if q1_ni_yoy is not None and row.get('q2_ni_yoy') is not None:
                row['ni_accel'] = row['q2_ni_yoy'] > q1_ni_yoy
                row['ni_accel_gap'] = round(row['q2_ni_yoy'] - q1_ni_yoy, 1)
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

    q1y = row.get('q1_rev_yoy')
    q2y = row.get('q2_rev_yoy')
    h1gt = row.get('h1_gt_q4')
    acc = row.get('rev_accel')
    acc_gap = row.get('rev_accel_gap', 0) or 0
    tag = 'ACCEL' if acc else 'no'
    h1_tag = 'H1>Q4' if h1gt else ''
    print('\r  [{}/{}] {}({}) Q1yoy={}% Q2yoy={}% gap={} {}'.format(
        idx+1, len(all_pool), name, code[:6],
        '{:.0f}'.format(q1y) if q1y is not None else '?',
        '{:.0f}'.format(q2y) if q2y is not None else '?',
        '{:+.0f}'.format(acc_gap) if acc_gap else '?',
        tag + ' ' + h1_tag), end='', flush=True)

print('\n完成')

# ===== 筛选 =====
rev_accel = [r for r in results if r.get('rev_accel') and r.get('h1_gt_q4')]
ni_accel = [r for r in results if r.get('ni_accel') and r.get('h1_gt_q4')]
both = [r for r in results if r.get('rev_accel') and r.get('ni_accel') and r.get('h1_gt_q4')]

rev_accel.sort(key=lambda x: x.get('rev_accel_gap', -9999) or -9999, reverse=True)
ni_accel.sort(key=lambda x: x.get('ni_accel_gap', -9999) or -9999, reverse=True)

print('\n' + '=' * 80)
print('营收加速(Q2同比>Q1同比) + H1>Q4: {} 只'.format(len(rev_accel)))
print('=' * 80)
for i, r in enumerate(rev_accel):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('\n  {}. {} {}({}) | {}'.format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print('     Q1同比+{:.0f}% → Q2同比+{:.0f}% (加速{:+.0f}%) | H1同比+{:.0f}%'.format(
        r['q1_rev_yoy'] or 0, r['q2_rev_yoy'] or 0, r['rev_accel_gap'] or 0, r['h1_rev_yoy'] or 0))
    print('     Q4_2025={:.1f}亿 → H1_2026预测={:.1f}亿 | H1净利={:.1f}亿 | 恢复系数={}'.format(
        r['q4_25_rev'] or 0, r['h1_26_rev_est'] or 0, r['h1_26_ni_est'] or 0, r.get('recovery_ratio', '?')))

if both:
    print('\n' + '=' * 80)
    print('双重加速(营收+净利): {} 只'.format(len(both)))
    print('=' * 80)
    for i, r in enumerate(both):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        print('  {}. {} {}({}) | 营收+{:.0f}% 净利+{:.0f}% | H1净利{:.1f}亿'.format(
            i+1, pt, r['name'], r['code'][:6],
            r['rev_accel_gap'] or 0, r['ni_accel_gap'] or 0, r['h1_26_ni_est'] or 0))

print('\n' + '=' * 80)
print('净利加速 TOP15')
print('=' * 80)
for i, r in enumerate(ni_accel[:15]):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('  {}. {} {}({}) | Q1同比+{:.0f}% → Q2同比+{:.0f}% (加速{:+.0f}%)'.format(
        i+1, pt, r['name'], r['code'][:6],
        r['q1_ni_yoy'] or 0, r['q2_ni_yoy'] or 0, r['ni_accel_gap'] or 0))

# ===== 保存 =====
output = {
    'date': '2026-06-19',
    'method': 'v2: Q2_2026_est = Q1_2026 × (Q2_2025/Q1_2025恢复系数); 条件: Q2同比>Q1同比 且 H1_est>Q4_2025',
    'total': len(all_pool),
    'rev_accel_count': len(rev_accel),
    'ni_accel_count': len(ni_accel),
    'both_count': len(both),
    'rev_accel': rev_accel,
    'ni_accel': ni_accel[:30],
    'both_accel': both,
    'all_results': results,
}

out_file = r'D:\mystock\solo\report_daily\h1_true_acceleration_v2_20260619.json'
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print('\n保存: {} ({} KB)'.format(out_file, os.path.getsize(out_file) // 1024))
