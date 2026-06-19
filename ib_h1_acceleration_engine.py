#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IB观察池63只 — 半年报加速选股引擎
目标：找Q2环比>Q1环比（加速）+ 半年报超预期
"""

import json
import sys
import time
import os

sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ===== 加载IB池 =====
with open(r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json', 'r', encoding='utf-8') as f:
    fund_data = json.load(f)

ib_pool = fund_data.get('IB_data', [])
print(f"IB观察池共 {len(ib_pool)} 只，开始拉取Q1/Q4财务数据...")

# ===== 拉取income数据（每只股票2个period: 20260331 Q1 + 20251231 Q4）=====
# Tushare income接口按period拉取，一次拉2期（Q1+H1/半年报）
# 为节省API，先批量拉取近4期，再用代码解析Q1/Q4
results = []
total = len(ib_pool)

for idx, stock in enumerate(ib_pool):
    code = stock['ts_code']
    name = stock['name']
    theme = stock.get('theme', '')
    q1_np_yoy = stock.get('np_yoy', 0)
    q1_rev_yoy = stock.get('rev_yoy', 0)
    q1_rev_yi = stock.get('latest_revenue_yi', 0)
    q1_ni_yi = stock.get('latest_nincome_yi', 0)
    market_cap = stock.get('market_cap_yi', 0)
    roe = stock.get('roe_waa', 0)
    pe = stock.get('pe', 0)
    inst_score = stock.get('inst_score', 0)
    theme_pros = stock.get('theme_prosperity', '')
    
    row_data = {
        'code': code, 'name': name, 'theme': theme,
        'q1_rev_yi': q1_rev_yi, 'q1_ni_yi': q1_ni_yi,
        'q1_rev_yoy': q1_rev_yoy, 'q1_np_yoy': q1_np_yoy,
        'market_cap_yi': market_cap, 'roe_waa': roe, 'pe': pe,
        'inst_score': inst_score, 'theme_prosperity': theme_pros,
    }
    
    try:
        # 拉近4期财报
        df = pro.income(ts_code=code,
                        fields='ts_code,end_date,total_revenue,n_income_attr_p',
                        period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False)
            
            # 提取Q1(20260331)和Q4(20251231)
            q1_row = df[df['end_date'] == '20260331']
            q4_row = df[df['end_date'] == '20251231']
            h1_2025_row = df[df['end_date'] == '20250630']
            
            q1_rev = 0
            q1_ni = 0
            q4_rev = 0
            q4_ni = 0
            h1_2025_rev = 0
            h1_2025_ni = 0
            
            if len(q1_row) > 0:
                q1_rev = (q1_row.iloc[0]['total_revenue'] or 0) / 1e8
                q1_ni = (q1_row.iloc[0]['n_income_attr_p'] or 0) / 1e8
            
            if len(q4_row) > 0:
                q4_rev = (q4_row.iloc[0]['total_revenue'] or 0) / 1e8
                q4_ni = (q4_row.iloc[0]['n_income_attr_p'] or 0) / 1e8
            
            if len(h1_2025_row) > 0:
                h1_2025_rev = (h1_2025_row.iloc[0]['total_revenue'] or 0) / 1e8
                h1_2025_ni = (h1_2025_row.iloc[0]['n_income_attr_p'] or 0) / 1e8
            
            row_data['q1_rev'] = q1_rev
            row_data['q1_ni'] = q1_ni
            row_data['q4_rev'] = q4_rev
            row_data['q4_ni'] = q4_ni
            row_data['h1_2025_rev'] = h1_2025_rev
            row_data['h1_2025_ni'] = h1_2025_ni
            
            # 计算Q1环比增速
            if q4_rev > 0:
                row_data['qoq_rev_q1'] = round((q1_rev - q4_rev) / q4_rev * 100, 1)
            else:
                row_data['qoq_rev_q1'] = None
            
            if q4_ni and q4_ni != 0:
                row_data['qoq_ni_q1'] = round((q1_ni - q4_ni) / abs(q4_ni) * 100, 1)
            else:
                row_data['qoq_ni_q1'] = None
            
            # 估算Q2营收（用季节性：H1 = Q1×2.1的历史均值）
            # 更准确：用H1_2025 - Q1_2025
            if h1_2025_rev > 0 and q1_rev > 0:
                q2_rev_est = h1_2025_rev - q1_rev
                row_data['q2_rev_est'] = q2_rev_est
                # Q2环比Q1增速
                if q1_rev > 0:
                    row_data['qoq_rev_q2'] = round((q2_rev_est - q1_rev) / q1_rev * 100, 1)
                else:
                    row_data['qoq_rev_q2'] = None
                
                # H1_2026预测（季节性外推）
                # Q1_2026 / Q1_H1_ratio
                h1_2025_ratio = q1_rev / h1_2025_rev if h1_2025_rev > 0 else 0.45
                h1_2026_rev_est = q1_rev / h1_2025_ratio if h1_2025_ratio > 0 else q1_rev * 2.2
                row_data['h1_2026_rev_est'] = round(h1_2026_rev_est, 2)
                
                # 2026H1同比
                if h1_2025_rev > 0:
                    row_data['h1_2026_rev_yoy'] = round((h1_2026_rev_est - h1_2025_rev) / h1_2025_rev * 100, 1)
                else:
                    row_data['h1_2026_rev_yoy'] = None
                
                # H1净利预测（用Q1净利率推算）
                q1_npm = q1_ni / q1_rev if q1_rev > 0 else 0
                row_data['h1_2026_ni_est'] = round(h1_2026_rev_est * q1_npm, 2)
                if h1_2025_ni and h1_2025_ni != 0:
                    row_data['h1_2026_ni_yoy'] = round((row_data['h1_2026_ni_est'] - h1_2025_ni) / abs(h1_2025_ni) * 100, 1)
                else:
                    row_data['h1_2026_ni_yoy'] = None
                
                # 超预期信号：2026H1增速 > 2025H1增速
                if row_data.get('h1_2026_rev_yoy') is not None and h1_2025_rev > 0:
                    rev_growth_2025 = (h1_2025_rev - q4_rev) / q4_rev * 100 if q4_rev > 0 else 0
                    row_data['h1_beat_signal'] = row_data['h1_2026_rev_yoy'] > rev_growth_2025
                else:
                    row_data['h1_beat_signal'] = None
            else:
                row_data['q2_rev_est'] = None
                row_data['qoq_rev_q2'] = None
                row_data['h1_2026_rev_est'] = None
                row_data['h1_2026_ni_est'] = None
                row_data['h1_beat_signal'] = None
            
            # 加速信号：Q2环比>Q1环比
            qoq_q1 = row_data.get('qoq_rev_q1')
            qoq_q2 = row_data.get('qoq_rev_q2')
            if qoq_q1 is not None and qoq_q2 is not None:
                row_data['acceleration_rev'] = qoq_q2 > qoq_q1
                row_data['acceleration_gap'] = round(qoq_q2 - qoq_q1, 1)
            else:
                row_data['acceleration_rev'] = None
                row_data['acceleration_gap'] = None
            
            # 加速信号（净利）
            qoq_ni_q1 = row_data.get('qoq_ni_q1')
            if h1_2025_ni and h1_2025_ni > 0 and q1_ni > 0 and q4_ni and q4_ni != 0:
                q2_ni_est = h1_2025_ni - q1_ni
                qoq_ni_q2 = (q2_ni_est - q1_ni) / abs(q1_ni) * 100
                row_data['qoq_ni_q2'] = round(qoq_ni_q2, 1)
                row_data['acceleration_ni'] = qoq_ni_q2 > qoq_ni_q1 if qoq_ni_q1 is not None else None
            else:
                row_data['qoq_ni_q2'] = None
                row_data['acceleration_ni'] = None
            
            row_data['data_ok'] = True
            
        else:
            row_data['data_ok'] = False
            
    except Exception as e:
        row_data['data_ok'] = False
        row_data['error'] = str(e)[:50]
    
    results.append(row_data)
    time.sleep(0.06)
    
    # 进度显示
    pct = (idx + 1) * 100 // total
    bar = '#' * (pct // 5) + '-' * (20 - pct // 5)
    print(f"\r  [{bar}] {idx+1}/{total} {name}({code[:6]}) | Q1环比{row_data.get('qoq_rev_q1','?')}% | Q2环比{row_data.get('qoq_rev_q2','?')}% | 加速={row_data.get('acceleration_rev','?')}", end='', flush=True)

print(f"\n\n拉取完成，共 {sum(1 for r in results if r.get('data_ok'))} 只有效数据")

# ===== 筛选加速股 =====
print("\n" + "=" * 80)
print("【核心筛选】Q2环比 > Q1环比（营收加速）+ H1超预期")
print("=" * 80)

# 先看整体Q1环比分布
qoq_vals = [r.get('qoq_rev_q1') for r in results if r.get('qoq_rev_q1') is not None]
import statistics
if qoq_vals:
    print(f"Q1环比分布: 中位数={statistics.median(qoq_vals):.1f}% | 均值={statistics.mean(qoq_vals):.1f}% | 范围: {min(qoq_vals):.1f}%~{max(qoq_vals):.1f}%")

# 核心筛选条件
ACCEL_FILTER = True   # Q2环比>Q1环比
BEAT_FILTER = True    # H1增速>去年H1增速
MIN_QOQ_Q1 = -30      # Q1本身不能太差（至少-30%以上才说明有收入）
MIN_H1_YOY = 10       # H1同比至少+10%

accelerated = []
for r in results:
    if not r.get('data_ok'):
        continue
    if r.get('acceleration_rev') is None:
        continue
    
    acc_ok = r['acceleration_rev'] if ACCEL_FILTER else True
    beat_ok = r.get('h1_beat_signal') if BEAT_FILTER else True
    qoq_q1_ok = (r.get('qoq_rev_q1') or -999) >= MIN_QOQ_Q1
    h1_yoy_ok = (r.get('h1_2026_rev_yoy') or -999) >= MIN_H1_YOY
    
    if acc_ok and beat_ok and qoq_q1_ok and h1_yoy_ok:
        r['filter_pass'] = True
        accelerated.append(r)
    else:
        r['filter_pass'] = False

print(f"\n通过加速+超预期筛选: {len(accelerated)} 只")

# 按加速幅度排序
accelerated.sort(key=lambda x: x.get('acceleration_gap', -9999), reverse=True)

for i, r in enumerate(accelerated):
    print(f"\n  {i+1}. {r['name']}({r['code'][:6]}) | {r['theme'][:8]}")
    print(f"     Q1营收: {r['q1_rev_yi']:.1f}亿(Q1环比{r['qoq_rev_q1']}%) | Q2环比: {r['qoq_rev_q2']}%")
    print(f"     加速差值: +{r['acceleration_gap']}% | H1同比: {r['h1_2026_rev_yoy']}%")
    print(f"     H1营收预测: {r['h1_2026_rev_est']}亿 | 净利预测: {r['h1_2026_ni_est']}亿")
    print(f"     2025H1基准: {r['h1_2025_rev']}亿 | Q4基数: {r['q4_rev']}亿")

# ===== 也看净利加速 =====
print("\n\n" + "=" * 80)
print("【净利加速】Q2净利环比 > Q1净利环比")
print("=" * 80)

ni_accelerated = [r for r in results if r.get('acceleration_ni') and r.get('qoq_ni_q1') is not None and r['qoq_ni_q1'] != 0]
ni_accelerated.sort(key=lambda x: x.get('qoq_ni_q2', 0) - x.get('qoq_ni_q1', 0), reverse=True)

for i, r in enumerate(ni_accelerated[:10]):
    gap = (r.get('qoq_ni_q2') or 0) - (r.get('qoq_ni_q1') or 0)
    print(f"  {i+1}. {r['name']}({r['code'][:6]}) | Q1环比{r['qoq_ni_q1']}% → Q2环比{r['qoq_ni_q2']}% (差+{gap:.0f}%)")

# ===== 综合评分 =====
print("\n\n" + "=" * 80)
print("【综合评分】加速×基本面×机构热度")
print("=" * 80)

SCORED = []
for r in results:
    if not r.get('data_ok'):
        continue
    score = 0
    
    # 加速因子（最高40分）
    gap = r.get('acceleration_gap', 0) or 0
    if gap > 20:
        score += 40
    elif gap > 10:
        score += 30
    elif gap > 0:
        score += 20
    elif gap is not None:
        score += 0  # 减速
    
    # Q1本身质量（最高20分）
    qoq = r.get('qoq_rev_q1', -999) or -999
    if qoq > 20:
        score += 20
    elif qoq > 0:
        score += 15
    elif qoq >= -10:
        score += 10
    else:
        score += 5
    
    # H1增速（最高20分）
    h1_yoy = r.get('h1_2026_rev_yoy', -999) or -999
    if h1_yoy > 50:
        score += 20
    elif h1_yoy > 30:
        score += 15
    elif h1_yoy > 10:
        score += 10
    elif h1_yoy > 0:
        score += 5
    
    # 机构热度（最高10分）
    inst = r.get('inst_score', 0)
    if inst >= 80:
        score += 10
    elif inst >= 60:
        score += 7
    elif inst >= 50:
        score += 4
    
    # 行业景气（最高10分）
    pros = r.get('theme_prosperity', '')
    if '🔥' in pros:
        score += 10
    elif '📈' in pros:
        score += 7
    elif '📊' in pros:
        score += 4
    
    r['total_score'] = score
    
    # 标签
    tags = []
    if r.get('acceleration_rev'):
        tags.append('🚀营收加速')
    if r.get('acceleration_ni'):
        tags.append('💰净利加速')
    if r.get('h1_beat_signal'):
        tags.append('⭐H1超预期')
    if r.get('theme_prosperity', '').startswith('🔥'):
        tags.append('🔥高景气')
    r['tags'] = tags
    
    SCORED.append(r)

SCORED.sort(key=lambda x: x['total_score'], reverse=True)

print(f"\n{'排名':<4} {'名称':<8} {'代码':<10} {'行业':<10} {'综合分':<6} {'标签'}")
print("-" * 70)
for i, r in enumerate(SCORED[:30]):
    tags_str = ' '.join(r.get('tags', []))
    print(f"{i+1:<4} {r['name']:<8} {r['code']:<10} {r['theme'][:8]:<10} {r['total_score']:<6} {tags_str}")

# ===== 保存结果 =====
output = {
    'date': '2026-06-19',
    'ib_pool_count': len(ib_pool),
    'valid_data_count': sum(1 for r in results if r.get('data_ok')),
    'accelerated_count': len(accelerated),
    'scored': SCORED,
    'accelerated': accelerated,
    'ni_accelerated': ni_accelerated[:20],
    'all_results': results,
}

out_file = r'D:\mystock\solo\report_daily\ib_h1_acceleration_20260619.json'
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n\n结果已保存: {out_file}")
print(f"大小: {os.path.getsize(out_file)} 字节")
