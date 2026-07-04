#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量运行所有主题的辨识度评分"""
import sys, os, json, time
sys.path.insert(0, r'd:\mystock\solo')
from theme_recognition_scorer import ThemeRecognitionScorer, CACHE_DIR
from datetime import datetime

# 加载主题映射
map_path = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
with open(map_path, 'r', encoding='utf-8') as f:
    theme_map = json.load(f)

themes = theme_map.get('themes', {})
print(f"[Start] 共 {len(themes)} 个主题待评分")
print(f"[Start] 时间: {datetime.now().strftime('%H:%M:%S')}")
print("=" * 80)

output_dir = r'd:\mystock\solo\multi_factor_picker\output'

scorer = ThemeRecognitionScorer()

# 统计汇总
all_results = []
grade_stats = {'S': 0, 'A': 0, 'B': 0, 'C': 0}
theme_summaries = []

start_time = time.time()

for i, (theme_name, stocks) in enumerate(themes.items(), 1):
    theme_start = time.time()
    print(f"\n[{i}/{len(themes)}] {theme_name} ({len(stocks)}只)...")

    try:
        results = scorer.score_theme_stocks(theme_name, stocks, output_dir)
        all_results.extend(results)

        # 统计
        for r in results:
            grade_stats[r['grade']] = grade_stats.get(r['grade'], 0) + 1

        # 主题摘要
        s_count = sum(1 for r in results if r['grade'] == 'S')
        a_count = sum(1 for r in results if r['grade'] == 'A')
        top1 = results[0] if results else None
        elapsed = time.time() - theme_start
        theme_summaries.append({
            'theme': theme_name,
            'total': len(results),
            'S': s_count,
            'A': a_count,
            'B': sum(1 for r in results if r['grade'] == 'B'),
            'C': sum(1 for r in results if r['grade'] == 'C'),
            'top1_name': top1.get('name', '') if top1 else '',
            'top1_score': top1['total_score'] if top1 else 0,
            'top1_grade': top1['grade'] if top1 else '',
            'elapsed_s': round(elapsed, 1),
        })
        print(f"  完成: S={s_count} A={a_count} 用时{elapsed:.1f}s TOP1={top1.get('name','') if top1 else ''}({top1['grade'] if top1 else ''}/{top1['total_score'] if top1 else 0})")
    except Exception as e:
        print(f"  [Error] {e}")
        theme_summaries.append({
            'theme': theme_name, 'total': 0, 'S': 0, 'A': 0, 'B': 0, 'C': 0,
            'top1_name': '', 'top1_score': 0, 'top1_grade': '', 'elapsed_s': 0,
        })

total_elapsed = time.time() - start_time

# 输出汇总
print("\n" + "=" * 80)
print(f"\n[完成] 总用时: {total_elapsed/60:.1f}分钟")
print(f"\n=== 全局分级统计 ===")
print(f"S级: {grade_stats['S']}只")
print(f"A级: {grade_stats['A']}只")
print(f"B级: {grade_stats['B']}只")
print(f"C级: {grade_stats['C']}只")
print(f"总计: {sum(grade_stats.values())}只")

# 保存主题摘要
import pandas as pd
summary_df = pd.DataFrame(theme_summaries)
summary_df = summary_df.sort_values('top1_score', ascending=False)
summary_path = os.path.join(output_dir, f"recognition_summary_{datetime.now().strftime('%Y%m%d')}.csv")
summary_df.to_csv(summary_path, index=False, encoding='utf-8-sig')
print(f"\n[Output] 主题摘要: {summary_path}")

# 全局TOP30
all_results.sort(key=lambda x: -x['total_score'])
print(f"\n=== 全局TOP30 高辨识度个股 ===")
print(f"{'排名':<4}{'等级':<4}{'股票名称':<10}{'代码':<12}{'主题':<16}{'总分':>6}{'机构':>6}{'游资':>6}{'主题':>6}{'连板':>6}{'北向%':>7}")
print("-" * 90)
for i, r in enumerate(all_results[:30], 1):
    d = r['dimensions']
    nb = d['institution'].get('north_hold_ratio', 0)
    theme_short = r.get('theme', '')[:14]
    print(f"{i:<4}{r['grade']:<4}{r.get('name',''):<10}{r['ts_code']:<12}"
          f"{theme_short:<16}{r['total_score']:>6.1f}"
          f"{d['institution']['score']:>6.0f}"
          f"{d['hot_money']['score']:>6.0f}"
          f"{d['theme_position']['score']:>6.0f}"
          f"{d['limit_up_gene']['score']:>6.0f}"
          f"{nb:>7.2f}")

# S级股票列表
s_stocks = [r for r in all_results if r['grade'] == 'S']
if s_stocks:
    print(f"\n=== S级股票完整列表 ({len(s_stocks)}只) ===")
    for r in s_stocks:
        d = r['dimensions']
        print(f"  {r.get('name','')}({r['ts_code']}) 总分={r['total_score']} 主题={r.get('theme','')}")

# 各主题TOP1
print(f"\n=== 各主题TOP1 ===")
print(f"{'主题':<20}{'TOP1股票':<12}{'等级':<4}{'总分':>6}{'S':>3}{'A':>3}{'B':>3}{'C':>3}")
print("-" * 60)
for s in sorted(theme_summaries, key=lambda x: -x['top1_score']):
    print(f"{s['theme'][:18]:<20}{s['top1_name']:<12}{s['top1_grade']:<4}{s['top1_score']:>6.1f}"
          f"{s['S']:>3}{s['A']:>3}{s['B']:>3}{s['C']:>3}")
