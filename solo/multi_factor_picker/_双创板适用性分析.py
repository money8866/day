"""双创板二波算法适用性分析"""
import pandas as pd
import glob
import os

print('=== 双创板二波算法适用性分析 ===\n')

cache_dir = r'D:\mystock\cache_daily'

# 统计双创板股票
gem_files = glob.glob(f'{cache_dir}/300*.csv')  # 创业板
star_files = glob.glob(f'{cache_dir}/688*.csv')  # 科创板

print(f'【股票数量统计】')
print(f'创业板（300xxx）: {len(gem_files)}只')
print(f'科创板（688xxx）: {len(star_files)}只')
print(f'双创板总计: {len(gem_files) + len(star_files)}只\n')

# 分析双创板二波信号（全市场扫描结果）
print('【从全市场扫描结果提取双创板信号】\n')

# 读取之前的扫描结果
try:
    all_results = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\全市场扫描_20260611.csv')
    
    # 筛选双创板
    gem_results = all_results[all_results['code'].str.startswith('300')]
    star_results = all_results[all_results['code'].str.startswith('688')]
    
    print(f'全市场扫描中双创板信号：')
    print(f'创业板信号: {len(gem_results)}只')
    print(f'科创板信号: {len(star_results)}只\n')
    
    # 分析二波确认比例
    gem_wave2 = len(gem_results[gem_results['wave2'] == True])
    star_wave2 = len(star_results[star_results['wave2'] == True])
    
    print(f'二波确认比例：')
    print(f'创业板: {gem_wave2}/{len(gem_results)}只 = {gem_wave2/len(gem_results)*100:.1f}%' if len(gem_results) > 0 else '创业板: 0只')
    print(f'科创板: {star_wave2}/{len(star_results)}只 = {star_wave2/len(star_results)*100:.1f}%' if len(star_results) > 0 else '科创板: 0只')
    
    # 显示TOP10双创板信号
    if len(gem_results) > 0 or len(star_results) > 0:
        combined = pd.concat([gem_results, star_results]).sort_values('tech', ascending=False)
        
        print(f'\n\n【TOP20双创板二波信号】\n')
        print(f'{"排名":<4} {"代码":<10} {"名称":<10} {"涨幅":<8} {"技术分":<8} {"板块":<8} {"首波日期":<10} {"回踩比例":<10} {"二波"}')
        print('-'*90)
        
        for i, (_, row) in enumerate(combined.head(20).iterrows(), 1):
            board = '创业板' if row['code'].startswith('300') else '科创板'
            wave2_mark = '✓' if row['wave2'] else '✗'
            print(f'{i:<4} {row["code"]:<10} {row["name"]:<10} {row["pct"]:+6.1f}%  {row["tech"]:<8.1f} {board:<8} {row["wave1_date"]:<10} {row["pullback_ratio"]:<10.1%} {wave2_mark}')
    
except Exception as e:
    print(f'未找到扫描结果: {e}\n')

print('\n\n【双创板特殊规则分析】')

# 根据回测结果（MEMORY.md中的记录）
print('\n基于回测结论（2026-06-24）：')
print('━' * 70)
print('\n【双创板四形态胜率】')
print(f'{"形态":<12} {"成功率":<10} {"均涨%":<10} {"盈亏比":<10} {"加分值":<10}')
print('-' * 50)
print(f'{"V型急跌":<12} {"97.2%":<10} {"13.2%":<10} {"16.1x":<10} {"+8":<10}')
print(f'{"强势横盘":<12} {"93.3%":<10} {"13.1%":<10} {"16.6x":<10} {"过滤":<10}')
print(f'{"放量回调":<12} {"91.2%":<10} {"12.5%":<10} {"14.5x":<10} {"0":<10}')
print(f'{"深度回调":<12} {"88.2%":<10} {"12.1%":<10} {"12.2x":<10} {"-2":<10}')

print('\n\n【关键修正规则】')

rules = [
    ('回踩阈值', '双创板V型急跌放宽至80%（主板82%）', '双创板波动大，允许更深回踩'),
    ('创新低排除', '深度回调/放量回调/V型急跌若创新低一律过滤', '创新低胜率从97.2%跌至16.7%'),
    ('三均线支撑', 'MA60+MA120+MA250上方成功率接近100%', '强制过滤条件'),
    ('板块适配', '双创强势横盘过滤，V型急跌+8分', '避免形态重叠'),
    ('换手率判断', '涨停日用换手率≥8%替代量比', '封板后量比失真'),
]

for rule, detail, reason in rules:
    print(f'\n✓ {rule}')
    print(f'  规则: {detail}')
    print(f'  原因: {reason}')

print('\n\n【适用性结论】')

conclusions = [
    ('算法适用', '✓', '双创板完全适用，需加入板块适配规则'),
    ('胜率更高', '✓', 'V型急跌97.2%胜率远超主板'),
    ('风险更大', '⚠️', '波动大，需严格止损（-5% vs 主板-7%）'),
    ('形态差异', '✓', '强势横盘过滤，聚焦V型急跌'),
    ('数据需求', '⚠️', '需前复权数据避免除权失真'),
]

for item, status, note in conclusions:
    print(f'{status} {item}: {note}')

print('\n\n【实战建议】')

print('''
【双创板二波操作要点】

1. 形态选择
   ✓ 优先选择V型急跌（+10分加成）
   ✓ 放量回调次选（+5分加成）
   ✗ 强势横盘过滤（与V型急跌重叠）
   ✗ 深度回调减分（-2分）

2. 入场条件
   ✓ 三均线支撑（MA60+MA120+MA250）
   ✓ 不创新低（回调最低点≥首波低点）
   ✓ RSI回归40-50区间
   ✓ 缩量回踩（量比<0.7）

3. 止损止盈
   止损: -5%（主板-7%）
   止盈: +15%（主板+20%）
   持仓: 3-10天

4. 仓位管理
   单只: ≤15%（主板≤20%）
   总仓: ≤60%（波动风险）
''')
