#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IB+IA池 — 半年报真加速筛选
条件：
1. 2026H1预测同比 > 2026Q1实际同比（H1增速>Q1增速 = 真加速）
2. 2026H1预测值 > 2025Q4实际值（H1不是萎缩）
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

# 加载全部池
with open(r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json', 'r', encoding='utf-8') as f:
    fund_data = json.load(f)

all_pool = []
for label, key in [('IA', 'IA_data'), ('IB', 'IB_data')]:
    for r in fund_data.get(key, []):
        r['pool'] = label
        all_pool.append(r)

print(f"IA+IB池共 {len(all_pool)} 只")

# 拉取2025Q4, 2025Q1, 2025H1的income数据
print("\n拉取财报数据...")
results = []

for idx, stock in enumerate(all_pool):
    code = stock['ts_code']
    name = stock['name']
    theme = stock.get('theme', '')
    pool = stock['pool']
    q1_rev_yi = stock.get('latest_revenue_yi', 0)  # 2026Q1营收(亿)
    q1_ni_yi = stock.get('latest_nincome_yi', 0)   # 2026Q1净利(亿)
    q1_rev_yoy = stock.get('rev_yoy', 0)            # 2026Q1营收同比%
    q1_np_yoy = stock.get('np_yoy', 0)             # 2026Q1净利同比%
    market_cap = stock.get('market_cap_yi', 0)
    pe = stock.get('pe', 0)
    roe = stock.get('roe_waa', 0)
    inst_score = stock.get('inst_score', 0)
    theme_pros = stock.get('theme_prosperity', '')

    row = {
        'code': code, 'name': name, 'theme': theme, 'pool': pool,
        'q1_rev_yi': q1_rev_yi, 'q1_ni_yi': q1_ni_yi,
        'q1_rev_yoy': q1_rev_yoy, 'q1_np_yoy': q1_np_yoy,
        'market_cap_yi': market_cap, 'pe': pe, 'roe_waa': roe,
        'inst_score': inst_score, 'theme_prosperity': theme_pros,
    }

    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)

            # 提取各期数据
            periods_needed = ['20260331', '20251231', '20250630', '20250331', '20240630']
            period_data = {}
            for _, r in df.iterrows():
                ed = r['end_date']
                if ed in periods_needed:
                    period_data[ed] = {
                        'rev': (r['total_revenue'] or 0) / 1e8,  # 亿元
                        'ni': (r['n_income_attr_p'] or 0) / 1e8,
                    }

            row['period_data'] = period_data

            # 2026Q1实际（已知）
            q1_2026 = period_data.get('20260331', {})
            row['q1_2026_rev'] = q1_2026.get('rev', 0)
            row['q1_2026_ni'] = q1_2026.get('ni', 0)

            # 2025Q4实际
            q4_2025 = period_data.get('20251231', {})
            row['q4_2025_rev'] = q4_2025.get('rev', 0)
            row['q4_2025_ni'] = q4_2025.get('ni', 0)

            # 2025H1实际
            h1_2025 = period_data.get('20250630', {})
            row['h1_2025_rev'] = h1_2025.get('rev', 0)
            row['h1_2025_ni'] = h1_2025.get('ni', 0)

            # 2025Q1实际
            q1_2025 = period_data.get('20250331', {})
            row['q1_2025_rev'] = q1_2025.get('rev', 0)
            row['q1_2025_ni'] = q1_2025.get('ni', 0)

            # 2024H1（用于算2025H1同比）
            h1_2024 = period_data.get('20240630', {})
            row['h1_2024_rev'] = h1_2024.get('rev', 0)
            row['h1_2024_ni'] = h1_2024.get('ni', 0)

            # ===== 核心计算 =====

            # 2026Q1实际同比 = (Q1_2026 - Q1_2025) / Q1_2025
            if row['q1_2025_rev'] > 0:
                row['q1_real_rev_yoy'] = round((row['q1_2026_rev'] - row['q1_2025_rev']) / row['q1_2025_rev'] * 100, 1)
            else:
                row['q1_real_rev_yoy'] = None

            if row['q1_2025_ni'] and row['q1_2025_ni'] != 0:
                row['q1_real_ni_yoy'] = round((row['q1_2026_ni'] - row['q1_2025_ni']) / abs(row['q1_2025_ni']) * 100, 1)
            else:
                row['q1_real_ni_yoy'] = None

            # 2026H1预测（季节性外推）
            # 方法: 用2025年Q1/H1比例推算2026H1
            if row['h1_2025_rev'] > 0 and row['q1_2025_rev'] > 0:
                q1_h1_ratio = row['q1_2025_rev'] / row['h1_2025_rev']
                if 0.2 < q1_h1_ratio < 0.8:  # 合理范围
                    h1_2026_rev_est = row['q1_2026_rev'] / q1_h1_ratio
                else:
                    h1_2026_rev_est = row['q1_2026_rev'] * 2.2  # 默认
            elif row['q1_2026_rev'] > 0:
                h1_2026_rev_est = row['q1_2026_rev'] * 2.2
            else:
                h1_2026_rev_est = 0

            row['q1_h1_ratio'] = round(q1_h1_ratio, 3)
            row['h1_2026_rev_est'] = round(h1_2026_rev_est, 2)

            # H1净利预测（用Q1净利率）
            if h1_2026_rev_est > 0 and row['q1_2026_rev'] > 0:
                q1_npm = row['q1_2026_ni'] / row['q1_2026_rev']
                h1_2026_ni_est = h1_2026_rev_est * q1_npm
            else:
                q1_npm = 0
                h1_2026_ni_est = 0

            row['q1_npm'] = round(q1_npm * 100, 1)  # 净利率%
            row['h1_2026_ni_est'] = round(h1_2026_ni_est, 2)

            # 2026H1预测同比 = (H1_2026_est - H1_2025) / H1_2025
            if row['h1_2025_rev'] > 0:
                row['h1_predict_rev_yoy'] = round((h1_2026_rev_est - row['h1_2025_rev']) / row['h1_2025_rev'] * 100, 1)
            else:
                row['h1_predict_rev_yoy'] = None

            if row['h1_2025_ni'] and row['h1_2025_ni'] != 0:
                row['h1_predict_ni_yoy'] = round((h1_2026_ni_est - row['h1_2025_ni']) / abs(row['h1_2025_ni']) * 100, 1)
            else:
                row['h1_predict_ni_yoy'] = None

            # ===== 核心筛选 =====

            # 条件1: H1同比增速 > Q1同比增速（真加速）
            # 营收
            q1_yoy = row.get('q1_real_rev_yoy')
            h1_yoy = row.get('h1_predict_rev_yoy')
            if q1_yoy is not None and h1_yoy is not None:
                row['rev_accel'] = h1_yoy > q1_yoy
                row['rev_accel_gap'] = round(h1_yoy - q1_yoy, 1)
            else:
                row['rev_accel'] = None
                row['rev_accel_gap'] = None

            # 净利
            q1_ni_yoy = row.get('q1_real_ni_yoy')
            h1_ni_yoy = row.get('h1_predict_ni_yoy')
            if q1_ni_yoy is not None and h1_ni_yoy is not None:
                row['ni_accel'] = h1_ni_yoy > q1_ni_yoy
                row['ni_accel_gap'] = round(h1_ni_yoy - q1_ni_yoy, 1)
            else:
                row['ni_accel'] = None
                row['ni_accel_gap'] = None

            # 条件2: 2026H1预测 > 2025Q4实际（不是萎缩）
            row['h1_gt_q4'] = h1_2026_rev_est > row['q4_2025_rev'] if row['q4_2025_rev'] > 0 else None

            row['data_ok'] = True
        else:
            row['data_ok'] = False
    except Exception as e:
        row['data_ok'] = False
        row['error'] = str(e)[:80]

    results.append(row)
    time.sleep(0.06)

    pct = (idx + 1) * 100 // len(all_pool)
    bar = '#' * (pct // 5) + '-' * (20 - pct // 5)
    acc_str = '✅' if row.get('rev_accel') else ('❌' if row.get('rev_accel') is False else '?')
    h1q4_str = '✅' if row.get('h1_gt_q4') else ('❌' if row.get('h1_gt_q4') is False else '?')
    print("\r  [{bar}] {pct}% {name}({code}) Q1同比{q1yoy}% H1同比{h1yoy}% 加速={acc} H1>Q4={h1q4}".format(
        bar=bar, pct=pct, name=name, code=code[:6],
        q1yoy=row.get('q1_real_rev_yoy', '?'), h1yoy=row.get('h1_predict_rev_yoy', '?'),
        acc=acc_str, h1q4=h1q4_str), end='', flush=True)

print(f"\n\n完成，有效数据 {sum(1 for r in results if r.get('data_ok'))} 只")

# ===== 筛选 =====
print("\n" + "=" * 80)
print("【核心筛选】H1同比 > Q1同比 且 H1预测 > Q4实际")
print("=" * 80)

# 营收加速 + H1>Q4
rev_pass = [r for r in results if r.get('rev_accel') and r.get('h1_gt_q4')]
# 净利加速 + H1>Q4
ni_pass = [r for r in results if r.get('ni_accel') and r.get('h1_gt_q4')]
# 双重加速
both_pass = [r for r in results if r.get('rev_accel') and r.get('ni_accel') and r.get('h1_gt_q4')]

print(f"\n营收加速(H1同比>Q1同比) + H1>Q4: {len(rev_pass)} 只")
print(f"净利加速(H1同比>Q1同比) + H1>Q4: {len(ni_pass)} 只")
print(f"双重加速(营收+净利) + H1>Q4: {len(both_pass)} 只")

# 排序
rev_pass.sort(key=lambda x: x.get('rev_accel_gap', -9999) or -9999, reverse=True)
ni_pass.sort(key=lambda x: x.get('ni_accel_gap', -9999) or -9999, reverse=True)

print("\n>>> 营收加速（H1同比>Q1同比）+ H1>Q4 <<<")
for i, r in enumerate(rev_pass):
    gap = r.get('rev_accel_gap', 0)
    q1yoy = r.get('q1_real_rev_yoy', 0)
    h1yoy = r.get('h1_predict_rev_yoy', 0)
    h1est = r.get('h1_2026_rev_est', 0)
    q4val = r.get('q4_2025_rev', 0)
    pool_tag = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print(f"  {i+1}. {pool_tag} {r['name']}({r['code'][:6]}) | {r['theme'][:8]}")
    print(f"     Q1同比+{q1yoy}% → H1同比+{h1yoy}% (加速+{gap}%) | Q4={q4val:.1f}亿 → H1预测={h1est:.1f}亿")

print("\n>>> 净利加速（H1同比>Q1同比）+ H1>Q4 <<<")
for i, r in enumerate(ni_pass[:20]):
    gap = r.get('ni_accel_gap', 0)
    q1yoy = r.get('q1_real_ni_yoy', 0)
    h1yoy = r.get('h1_predict_ni_yoy', 0)
    pool_tag = '[IA]' if r['pool'] == 'IA' else '[IB]'
    ni_est = r.get('h1_2026_ni_est', 0)
    print(f"  {i+1}. {pool_tag} {r['name']}({r['code'][:6]}) | Q1同比+{q1yoy}% → H1同比+{h1yoy}% (加速+{gap}%) | H1净利{ni_est:.1f}亿")

if both_pass:
    print("\n>>> 🔥 双重加速（营收+净利同时加速）<<<")
    both_pass.sort(key=lambda x: (x.get('rev_accel_gap', 0) or 0) + (x.get('ni_accel_gap', 0) or 0), reverse=True)
    for i, r in enumerate(both_pass):
        pool_tag = '[IA]' if r['pool'] == 'IA' else '[IB]'
        rgap = r.get('rev_accel_gap', 0)
        ngap = r.get('ni_accel_gap', 0)
        print(f"  {i+1}. {pool_tag} {r['name']}({r['code'][:6]}) | {r['theme'][:8]} | 营收加速+{rgap}% 净利加速+{ngap}%")

# ===== 保存 =====
output = {
    'date': '2026-06-19',
    'logic': 'H1同比>Q1同比(真加速) + H1预测>Q4实际(非萎缩)',
    'total_pool': len(all_pool),
    'rev_accel_count': len(rev_pass),
    'ni_accel_count': len(ni_pass),
    'both_accel_count': len(both_pass),
    'rev_accel': rev_pass,
    'ni_accel': ni_pass[:30],
    'both_accel': both_pass,
    'all_results': results,
}

out_file = r'D:\mystock\solo\report_daily\h1_true_acceleration_20260619.json'
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n\n保存: {out_file} ({os.path.getsize(out_file)//1024} KB)")
