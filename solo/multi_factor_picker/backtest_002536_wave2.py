"""
飞龙股份（002536.SZ）二波策略历史回测
======================================
遍历历史每个交易日，检测何时发出信号。
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wave2_pattern_scanner import WavePatternDetector, get_effective_date
import pandas as pd
from datetime import datetime, timedelta

ts_code = '002536.SZ'

# 获取股票数据
print(f'=== {ts_code} 二波策略历史回测 ===')
print()

# 先获取数据范围
detector = WavePatternDetector(force_date='')
df = detector.load_data(ts_code, lookback=300)
if df is None:
    print(f'无法获取{ts_code}数据')
    sys.exit(1)

print(f'数据范围: {df.iloc[0]["trade_date"]} 至 {df.iloc[-1]["trade_date"]}')
print(f'数据长度: {len(df)}天')
print()

# 遍历历史每个交易日（从100天前开始，避免数据不足）
start_idx = max(0, len(df) - 200)  # 最近200天
end_idx = len(df) - 1

print(f'回测范围: {df.iloc[start_idx]["trade_date"]} 至 {df.iloc[end_idx]["trade_date"]}')
print(f'共 {end_idx - start_idx + 1} 个交易日')
print()

signals = []
total_days = end_idx - start_idx + 1

for i, idx in enumerate(range(start_idx, end_idx + 1)):
    trade_date = str(df.iloc[idx]['trade_date'])
    
    if (i + 1) % 20 == 0:
        print(f'  进度: {i+1}/{total_days} ({trade_date})')
    
    # 创建指定日期的检测器
    detector = WavePatternDetector(force_date=trade_date)
    
    # 测试四种形态
    patterns = [
        ('强势横盘', detector.detect_sideways_pattern),
        ('深度回调', detector.detect_deep_pullback_pattern),
        ('放量回调', detector.detect_volume_pullback_pattern),
        ('V型急跌', detector.detect_vshape_pattern),
    ]
    
    for pattern_name, detect_func in patterns:
        result = detect_func(ts_code, today_only=False)
        if result:
            signals.append({
                'date': trade_date,
                'pattern': pattern_name,
                'score': result.get('score', 0),
                'wave1_gain': result.get('wave1_gain', 0),
                'pullback': result.get('pullback', 0),
                'adjust_days': result.get('adjust_days', 0),
            })
            print(f'  ✅ {trade_date}: {pattern_name}形态 (评分{result.get("score", 0)}分)')

print()
print('=' * 60)
print(f'回测完成！共发现 {len(signals)} 个信号')
print('=' * 60)
print()

if signals:
    # 按日期排序
    signals_sorted = sorted(signals, key=lambda x: x['date'])
    
    print('信号明细:')
    for sig in signals_sorted:
        print(f'  {sig["date"]}: {sig["pattern"]}形态 (评分{sig["score"]}分)')
        print(f'    一波涨幅: {sig["wave1_gain"]:.1f}%  回调幅度: {sig["pullback"]:.1f}%  调整天数: {sig["adjust_days"]}天')
        print()
    
    # 保存CSV
    df_out = pd.DataFrame(signals_sorted)
    output_path = r'D:\mystock\solo\trend_feature_output\002536_wave2_backtest.csv'
    df_out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f'回测结果已保存: {output_path}')
else:
    print('未检测到任何信号')
