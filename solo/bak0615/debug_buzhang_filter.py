import sys
sys.path.insert(0, '.')
from advanced_buzhang_analysis import AdvancedBuzhangDetector
import pandas as pd

# 读取太辰光的K线数据
df = pd.read_csv('cache_daily/300570.SZ.csv')
df = df.sort_values('trade_date')

# 创建补涨检测器
detector = AdvancedBuzhangDetector()

# 分析太辰光
result = detector.analyze_stock(df, None, 200, 5)  # 假设市值200亿，换手率5%
print('太辰光补涨分析结果:')
print(f'有效: {result.get("valid")}')
print(f'综合评分: {result.get("overall_score", 0):.2f}')
print(f'各项指标评分: {result.get("metrics", {})}')
print(f'检测到的模式: {result.get("detected_patterns", [])}')
if 'reason' in result:
    print(f'失败原因: {result.get("reason")}')