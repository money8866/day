"""调试 ThreeGateFilter 的归一化和门控决策"""
import json, sys
sys.path.insert(0, r'd:\mystock\solo')
from entry_timing_engine import (
    _compute_theme_scores_from_report,
    ThreeGateFilter
)

with open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260724.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

stocks_output = data.get('stocks', {})
subtheme_report = data.get('subtheme_report', {})

# 1. 计算原始评分
theme_scores, theme_stages, subtheme_scores = _compute_theme_scores_from_report(subtheme_report)
print("=== 原始评分 ===")
for parent in sorted(theme_scores.keys()):
    print(f"  {parent:<10} raw={theme_scores[parent]:.1f} stage={theme_stages.get(parent,'')}")

# 2. 创建过滤器并检查归一化参数
gate = ThreeGateFilter(theme_scores, theme_stages, subtheme_scores, stocks_output)
print(f"\n=== 归一化参数 ===")
print(f"  raw_min={gate._raw_min:.1f} raw_max={gate._raw_max:.1f}")
print(f"  normalize_threshold={gate.normalize_threshold}")

# 3. 打印每个主题的归一化结果和门禁决策
print(f"\n=== Theme Gate 决策 ===")
for parent in sorted(theme_scores.keys()):
    tg_pass, tg_reason = gate._check_theme_gate(parent)
    norm = gate._normalize(theme_scores[parent])
    stage = theme_stages.get(parent, '')
    print(f"  {parent:<10} raw={theme_scores[parent]:.1f} norm={norm:.1f} stage={parent}={stage} pass={tg_pass} reason={tg_reason}")

# 4. 检查所有子主题里哪些能通过 Sub-theme Gate
print(f"\n=== Sub-theme Gate 决策 (子主题分>=65归一化) ===")
for parent in sorted(subtheme_scores.keys()):
    for sub_name, raw in subtheme_scores[parent].items():
        norm = gate._normalize(raw)
        sg_pass, sg_reason = gate._check_subtheme_gate(parent, sub_name, 'hold')
        if sg_pass:
            print(f"  PASS {parent}/{sub_name} raw={raw:.1f} norm={norm:.1f}")

# 5. 找出有哪些 BREAKOUT BUY 信号被阻塞
print(f"\n=== 原有 BREAKOUT BUY 信号检查 ===")
from collections import Counter
signals_before = Counter()
for code, info in stocks_output.items():
    sig = info.get('entry_signal', '')
    if sig:
        signals_before[sig] += 1
print(f"  各信号分布: {dict(signals_before)}")
