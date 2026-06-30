"""
飞龙股份（002536.SZ）二波策略测试
==============================
测试四种二波形态：强势横盘、深度回调、放量回调、V型急跌。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wave2_pattern_scanner import WavePatternDetector, get_effective_date
import pandas as pd

# 初始化检测器
detector = WavePatternDetector(force_date='')

# 测试股票
ts_code = '002536.SZ'
print(f'=== {ts_code} 飞龙股份 二波策略测试 ===')
print()

# 测试四种形态
patterns = [
    ('强势横盘', 'sideways'),
    ('深度回调', 'deep'),
    ('放量回调', 'volume'),
    ('V型急跌', 'vshape'),
]

results = []
for name, pattern_key in patterns:
    print(f'--- 测试{name}形态 ---')
    
    if pattern_key == 'sideways':
        result = detector.detect_sideways_pattern(ts_code, today_only=False)
    elif pattern_key == 'deep':
        result = detector.detect_deep_pullback_pattern(ts_code, today_only=False)
    elif pattern_key == 'volume':
        result = detector.detect_volume_pullback_pattern(ts_code, today_only=False)
    elif pattern_key == 'vshape':
        result = detector.detect_vshape_pattern(ts_code, today_only=False)
    
    if result:
        print(f'  ✅ 检测到{name}形态！')
        print(f'     评分: {result.get("score", 0)}分')
        print(f'     信号日: {result.get("signal_date", "")}')
        print(f'     一波涨幅: {result.get("wave1_gain", 0):.1f}%')
        print(f'     调整天数: {result.get("adjust_days", 0)}天')
        print(f'     回调幅度: {result.get("pullback", 0):.1f}%')
        results.append(result)
    else:
        print(f'  ❌ 未检测到{name}形态')
    print()

# 总结
print('=' * 60)
print(f'检测结果: {len(results)}/{len(patterns)} 种形态')
if results:
    print()
    print('形态列表:')
    for r in results:
        print(f'  - {r.get("pattern", "")}: 评分{r.get("score", 0)}分')
print('=' * 60)
