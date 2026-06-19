#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真加速筛选v4 — 最直接方法
核心逻辑：H1同比 > Q1同比 ⟺ Q2同比增速 > Q1同比增速
方法：不用预测Q2，直接看历史模式 + 2026景气信号

关键洞察：
如果2025H1同比 > 2025Q1同比 → 公司历史上有"H1加速"模式
如果2026Q1同比已经远高于2025Q1同比 → 2026景气更强，大概率延续加速
如果Q1环比Q4在改善（相比2025年同期改善）→ Q2大概率也改善

最终：
- 看哪些公司在2025年已经是"H1同比>Q1同比"
- 加上2026年Q1同比比2025年更高 → 2026年更可能H1加速
- 再加上环比斜率改善 → 确认信号
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
            q4_24 = pd_map.get('20241231', {})
            h1_24 = pd_map.get('20240630', {})
            q1_24 = pd_map.get('20240331', {})

            q1_26_rev = q1_26.get('rev', 0)
            q4_25_rev = q4_25.get('rev', 0)
            h1_25_rev = h1_25.get('rev', 0)
            q1_25_rev = q1_25.get('rev', 0)
            q4_24_rev = q4_24.get('rev', 0)
            h1_24_rev = h1_24.get('rev', 0)
            q1_24_rev = q1_24.get('rev', 0)

            q1_26_ni = q1_26.get('ni', 0)
            q4_25_ni = q4_25.get('ni', 0)
            h1_25_ni = h1_25.get('ni', 0)
            q1_25_ni = q1_25.get('ni', 0)
            h1_24_ni = h1_24.get('ni', 0)
            q1_24_ni = q1_24.get('ni', 0)

            row.update({
                'q1_26_rev': q1_26_rev, 'q4_25_rev': q4_25_rev,
                'h1_25_rev': h1_25_rev, 'q1_25_rev': q1_25_rev,
                'h1_24_rev': h1_24_rev, 'q1_24_rev': q1_24_rev,
            })

            # ===== 核心计算 =====

            # 2026Q1同比
            if q1_25_rev > 0:
                q1_26_yoy = (q1_26_rev - q1_25_rev) / q1_25_rev * 100
            else:
                q1_26_yoy = None
            row['q1_26_rev_yoy'] = round(q1_26_yoy, 1) if q1_26_yoy is not None else None

            # 2025Q1同比
            if q1_24_rev > 0:
                q1_25_yoy = (q1_25_rev - q1_24_rev) / q1_24_rev * 100
            else:
                q1_25_yoy = None
            row['q1_25_rev_yoy'] = round(q1_25_yoy, 1) if q1_25_yoy is not None else None

            # 2025H1同比
            if h1_24_rev > 0:
                h1_25_yoy = (h1_25_rev - h1_24_rev) / h1_24_rev * 100
            else:
                h1_25_yoy = None
            row['h1_25_rev_yoy'] = round(h1_25_yoy, 1) if h1_25_yoy is not None else None

            # 2025年：H1同比 vs Q1同比
            if q1_25_yoy is not None and h1_25_yoy is not None:
                row['hist_h1_vs_q1'] = h1_25_yoy - q1_25_yoy
                row['hist_h1_accel'] = h1_25_yoy > q1_25_yoy
            else:
                row['hist_h1_vs_q1'] = None
                row['hist_h1_accel'] = None

            # 2026年景气加速：Q1_26_yoy vs Q1_25_yoy
            if q1_26_yoy is not None and q1_25_yoy is not None:
                row['q1_accel_vs_last'] = q1_26_yoy - q1_25_yoy
            else:
                row['q1_accel_vs_last'] = None

            # ===== H1_2026预测 =====
            # 方法：用2025年H1同比模式 + 2026年景气加强
            # 2026H1同比 ≈ max(Q1_26_yoy, 2025H1_yoy + Q1加速幅度)
            # 更好方法：直接用H1 = Q1 × (H1_2025/Q1_2025) 历史比例
            if h1_25_rev > 0 and q1_25_rev > 0:
                h1_over_q1 = h1_25_rev / q1_25_rev  # 2025年H1是Q1的几倍
                h1_26_est_v1 = q1_26_rev * h1_over_q1  # 线性外推
            else:
                h1_over_q1 = 2.0
                h1_26_est_v1 = q1_26_rev * 2.0

            row['h1_over_q1'] = round(h1_over_q1, 2)
            h1_26_rev_est = h1_26_est_v1
            row['h1_26_rev_est'] = round(h1_26_rev_est, 2)

            # H1同比
            if h1_25_rev > 0:
                h1_26_yoy = (h1_26_rev_est - h1_25_rev) / h1_25_rev * 100
                row['h1_26_rev_yoy'] = round(h1_26_yoy, 1)
            else:
                row['h1_26_rev_yoy'] = None

            # 净利同理
            if q1_25_ni and abs(q1_25_ni) > 0.001:
                q1_26_ni_yoy = (q1_26_ni - q1_25_ni) / abs(q1_25_ni) * 100
                row['q1_26_ni_yoy'] = round(q1_26_ni_yoy, 1)
            else:
                row['q1_26_ni_yoy'] = None

            if h1_25_ni and abs(h1_25_ni) > 0.001 and q1_25_ni and abs(q1_25_ni) > 0.001:
                h1_npm = h1_25_ni / h1_25_rev
                h1_26_ni_est = h1_26_rev_est * h1_npm
                row['h1_26_ni_est'] = round(h1_26_ni_est, 2)
                h1_26_ni_yoy = (h1_26_ni_est - h1_25_ni) / abs(h1_25_ni) * 100
                row['h1_26_ni_yoy'] = round(h1_26_ni_yoy, 1)
            else:
                row['h1_26_ni_est'] = None
                row['h1_26_ni_yoy'] = None

            # ===== 核心判断 =====
            # 条件1: H1同比 > Q1同比
            q1y = row.get('q1_26_rev_yoy')
            h1y = row.get('h1_26_rev_yoy')
            if q1y is not None and h1y is not None:
                row['rev_accel'] = h1y > q1y
                row['rev_accel_gap'] = round(h1y - q1y, 1)
            else:
                row['rev_accel'] = None
                row['rev_accel_gap'] = None

            # 条件2: H1 > Q4
            row['h1_gt_q4'] = h1_26_rev_est > q4_25_rev if q4_25_rev > 0 else None

            # 净利
            q1ny = row.get('q1_26_ni_yoy')
            h1ny = row.get('h1_26_ni_yoy')
            if q1ny is not None and h1ny is not None:
                row['ni_accel'] = h1ny > q1ny
                row['ni_accel_gap'] = round(h1ny - q1ny, 1)
            else:
                row['ni_accel'] = None
                row['ni_accel_gap'] = None

            # ===== 附加信号 =====
            # 历史加速模式: 2025年H1同比>Q1同比
            row['hist_pattern'] = 'H1加速' if row.get('hist_h1_accel') else ('H1减速' if row.get('hist_h1_accel') is False else '?')
            # 景气加强: 2026Q1同比 > 2025Q1同比
            q1a = row.get('q1_accel_vs_last')
            if q1a is not None:
                if q1a > 20:
                    row['momentum'] = '景气大幅加强'
                elif q1a > 0:
                    row['momentum'] = '景气稳定'
                elif q1a > -20:
                    row['momentum'] = '景气放缓'
                else:
                    row['momentum'] = '景气恶化'
            else:
                row['momentum'] = '?'

            row['data_ok'] = True
        else:
            row['data_ok'] = False
    except Exception as e:
        row['data_ok'] = False
        row['error'] = str(e)[:60]

    results.append(row)
    time.sleep(0.06)

    q1y = row.get('q1_26_rev_yoy')
    h1y = row.get('h1_26_rev_yoy')
    acc = row.get('rev_accel')
    h1q4 = row.get('h1_gt_q4')
    hist = row.get('hist_pattern', '?')
    mom = row.get('momentum', '?')
    ag = row.get('rev_accel_gap', 0) or 0
    tags = []
    if acc: tags.append('ACCEL')
    if h1q4: tags.append('H1>Q4')
    if hist == 'H1加速': tags.append('历史加速')
    if '加强' in str(mom): tags.append('景气+')
    print('\r  [{}/{}] {}({}) Q1={}% H1={}% gap={:+.0f}% {}'.format(
        idx+1, len(all_pool), name, code[:6],
        '{:.0f}'.format(q1y) if q1y else '?',
        '{:.0f}'.format(h1y) if h1y else '?',
        ag, ' '.join(tags)), end='', flush=True)

print('\n\n完成')

# ===== 筛选 =====
rev_accel = [r for r in results if r.get('rev_accel') and r.get('h1_gt_q4')]
ni_accel = [r for r in results if r.get('ni_accel') and r.get('h1_gt_q4')]
both = [r for r in results if r.get('rev_accel') and r.get('ni_accel') and r.get('h1_gt_q4')]

rev_accel.sort(key=lambda x: x.get('rev_accel_gap', -9999) or -9999, reverse=True)
ni_accel.sort(key=lambda x: x.get('ni_accel_gap', -9999) or -9999, reverse=True)

print('\n' + '=' * 80)
print('营收加速(H1同比>Q1同比) + H1>Q4: {} 只'.format(len(rev_accel)))
print('=' * 80)
for i, r in enumerate(rev_accel):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('\n  {}. {} {}({}) | {}'.format(i+1, pt, r['name'], r['code'][:6], r['theme']))
    print('     Q1同比+{:.0f}% -> H1同比+{:.0f}% (加速{:+.0f}%)'.format(
        r.get('q1_26_rev_yoy',0) or 0, r.get('h1_26_rev_yoy',0) or 0, r.get('rev_accel_gap',0) or 0))
    print('     H1预测={:.1f}亿 Q4={:.1f}亿 | 历史:{} | {}'.format(
        r.get('h1_26_rev_est',0) or 0, r.get('q4_25_rev',0) or 0,
        r.get('hist_pattern','?'), r.get('momentum','?')))
    if r.get('h1_26_ni_est'):
        print('     H1净利预测={:.1f}亿 (同比+{:.0f}%)'.format(
            r['h1_26_ni_est'], r.get('h1_26_ni_yoy',0) or 0))

if both:
    print('\n' + '=' * 80)
    print('双重加速: {} 只'.format(len(both)))
    for i, r in enumerate(both):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        print('  {}. {} {}({}) | rev+{:.0f}% ni+{:.0f}%'.format(
            i+1, pt, r['name'], r['code'][:6],
            r.get('rev_accel_gap',0) or 0, r.get('ni_accel_gap',0) or 0))

print('\n' + '=' * 80)
print('净利加速(H1同比>Q1同比) + H1>Q4: {} 只'.format(len(ni_accel)))
print('=' * 80)
for i, r in enumerate(ni_accel):
    pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
    print('  {}. {} {}({}) | Q1同比+{:.0f}% -> H1同比+{:.0f}% (加速{:+.0f}%) | H1净利{:.1f}亿'.format(
        i+1, pt, r['name'], r['code'][:6],
        r.get('q1_26_ni_yoy',0) or 0, r.get('h1_26_ni_yoy',0) or 0,
        r.get('ni_accel_gap',0) or 0, r.get('h1_26_ni_est',0) or 0))

# 也看看"接近加速"的（gap > -5%）
near_accel = [r for r in results if r.get('h1_gt_q4') and r.get('rev_accel_gap') is not None and -10 < r['rev_accel_gap'] < 0]
near_accel.sort(key=lambda x: x.get('rev_accel_gap', -9999) or -9999, reverse=True)
if near_accel:
    print('\n' + '=' * 80)
    print('接近加速(H1-Q1差距<10%, 有超预期可能): {} 只'.format(len(near_accel)))
    print('=' * 80)
    for i, r in enumerate(near_accel[:10]):
        pt = '[IA]' if r['pool'] == 'IA' else '[IB]'
        print('  {}. {} {}({}) | Q1+{:.0f}% H1+{:.0f}% 差{:+.0f}% | {} {}'.format(
            i+1, pt, r['name'], r['code'][:6],
            r.get('q1_26_rev_yoy',0) or 0, r.get('h1_26_rev_yoy',0) or 0,
            r.get('rev_accel_gap',0) or 0, r.get('hist_pattern','?'), r.get('momentum','?')))

# 保存
output = {
    'date': '2026-06-19',
    'method': 'v4: H1_2026 = Q1_2026 × (H1_2025/Q1_2025历史倍数); H1同比vs Q1同比; H1_est vs Q4_2025',
    'conditions': '1.H1同比>Q1同比(营收加速) 2.H1预测>Q4(非萎缩) 3.净利同理',
    'total': len(all_pool),
    'rev_accel_count': len(rev_accel),
    'ni_accel_count': len(ni_accel),
    'both_count': len(both),
    'near_accel_count': len(near_accel),
    'rev_accel': rev_accel,
    'ni_accel': ni_accel[:30],
    'both_accel': both,
    'near_accel': near_accel[:20],
    'all_results': results,
}

out_file = r'D:\mystock\solo\report_daily\h1_true_acceleration_v4_20260619.json'
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print('\n\n保存: {} ({} KB)'.format(out_file, os.path.getsize(out_file) // 1024))
