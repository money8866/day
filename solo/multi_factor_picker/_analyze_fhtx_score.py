"""分析烽火通信为何不在BullScore合格池"""
import pandas as pd
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

# 初始化数据获取器
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

ts_code = '600498.SH'

print(f'=== 烽火通信（{ts_code}）基本面诊断 ===\n')

# 1. 获取财务数据
print('【1】财务数据对比（2025年报 vs 2026Q1）')
print('-' * 60)

# 2025年报
income_2025 = fetcher.pro.income(ts_code=ts_code, period='20251231', fields='ts_code,total_revenue,n_income')
if len(income_2025) > 0:
    row = income_2025.iloc[0]
    print(f'2025年报:')
    print(f'  营收: {row["total_revenue"]/1e8:.2f}亿')
    print(f'  净利: {row["n_income"]/1e8:.2f}亿')

# 2026Q1
income_2026q1 = fetcher.pro.income(ts_code=ts_code, period='20260331', fields='ts_code,total_revenue,n_income')
if len(income_2026q1) > 0:
    row = income_2026q1.iloc[0]
    print(f'\n2026Q1:')
    print(f'  营收: {row["total_revenue"]/1e8:.2f}亿')
    print(f'  净利: {row["n_income"]/1e8:.2f}亿')

# 2. 毛利率趋势
print('\n\n【2】毛利率趋势（近4年）')
print('-' * 60)

fina_indicator = fetcher.pro.fina_indicator(ts_code=ts_code, start_date='20230101', fields='ts_code,end_date,profit_dedt,grossprofit_margin,netprofit_margin')
if len(fina_indicator) > 0:
    for i, row in fina_indicator.iterrows():
        if '1231' in str(row['end_date']):
            print(f'{row["end_date"]}: 毛利率 {row["grossprofit_margin"]:.1f}% | 净利率 {row["netprofit_margin"]:.1f}%')

# 3. ROE趋势
print('\n\n【3】ROE趋势（近4年）')
print('-' * 60)

for i, row in fina_indicator.iterrows():
    if '1231' in str(row['end_date']):
        roe_data = fetcher.pro.fina_indicator(ts_code=ts_code, period=row['end_date'], fields='ts_code,roe')
        if len(roe_data) > 0:
            print(f'{row["end_date"]}: ROE {roe_data.iloc[0]["roe"]:.2f}%')

# 4. 增长质量评分模拟
print('\n\n【4】BullScore评分模拟')
print('-' * 60)

# 预期差因子（模拟数据）
revenue_yoy_2025 = 70.66  # 已知值
revenue_yoy_2026q1 = 0.86  # 已知值

growth_trend = 'falling' if revenue_yoy_2026q1 < revenue_yoy_2025 * 0.5 else 'stable'

print(f'\n增长趋势: {growth_trend}')
print(f'  2025年报营收增速: +{revenue_yoy_2025:.2f}%')
print(f'  2026Q1营收增速: +{revenue_yoy_2026q1:.2f}%')

if growth_trend == 'falling':
    print(f'\n⚠️  触发增长衰减降权：')
    print(f'  Q1增速({revenue_yoy_2026q1:.1f}%) < 年报增速({revenue_yoy_2025:.1f}%)的50%')
    print(f'  预期差因子降权30%')

# 5. 估算BullScore
print('\n\n【5】预估BullScore总分')
print('-' * 60)

# 假设其他因子正常（50-80分），预期差异常
expectation_score = 60  # 正常情况
expectation_score_adjusted = expectation_score * 0.7  # 降权后

other_factors = {
    '产业景气': 75,
    '技术壁垒': 70,
    '订单爆发': 80,
    '业绩质量': 65,
    '龙头地位': 60,
    '机构认可': 70,
    '市值弹性': 80,
    '估值安全': 75,
    '筹码面': 70,
}

# 权重
weights = {
    '产业景气': 0.14,
    '技术壁垒': 0.10,
    '订单爆发': 0.14,
    '预期差': 0.14,
    '业绩质量': 0.12,
    '龙头地位': 0.08,
    '机构认可': 0.08,
    '市值弹性': 0.05,
    '估值安全': 0.07,
    '筹码面': 0.08,
}

total_score = 0
for factor, score in other_factors.items():
    weight = weights[factor]
    total_score += score * weight
    print(f'{factor}({weight*100:.0f}%): {score}分 × {weight} = {score * weight:.2f}')

total_score += expectation_score_adjusted * weights['预期差']
print(f'预期差({weights["预期差"]*100:.0f}%): {expectation_score}×0.7 = {expectation_score_adjusted:.0f}分 → {expectation_score_adjusted * weights["预期差"]:.2f}')

print(f'\n预估总分: {total_score:.2f}')
print(f'\n结论: {"❌ 不合格（<60分）" if total_score < 60 else "✓ 合格（≥60分）"}')

# 6. 对比合格股
print('\n\n【6】对比合格池门槛')
print('-' * 60)

qualified = pd.read_csv(r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv')
print(f'合格池最低分: {qualified["最终分"].min():.2f}')
print(f'合格池平均分: {qualified["最终分"].mean():.2f}')
print(f'合格池中位数: {qualified["最终分"].median():.2f}')
print(f'\n烽火通信预估分: {total_score:.2f}')
print(f'差距: {60 - total_score:.2f}分')
