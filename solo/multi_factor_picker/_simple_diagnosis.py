"""简化版诊断"""
import tushare as ts

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api(token)

ts_code = '600498.SH'

print(f'=== 烽火通信诊断 ===\n')

# 1. 股票基本信息
stock = pro.stock_basic(ts_code=ts_code).iloc[0]
print(f'股票: {stock["name"]} ({ts_code})')
print(f'行业: {stock["industry"]}')

# 2. 财务数据
print('\n【财务数据】')
income_2025 = pro.income(ts_code=ts_code, period='20251231', fields='ts_code,total_revenue,n_income,ann_date')
if len(income_2025) > 0:
    r = income_2025.iloc[0]
    print(f'2025年报: 营收{r["total_revenue"]/1e8:.1f}亿 净利{r["n_income"]/1e8:.1f}亿 公告日{r["ann_date"]}')

income_2026q1 = pro.income(ts_code=ts_code, period='20260331', fields='ts_code,total_revenue,n_income,ann_date')
if len(income_2026q1) > 0:
    r = income_2026q1.iloc[0]
    print(f'2026Q1:  营收{r["total_revenue"]/1e8:.1f}亿 净利{r["n_income"]/1e8:.1f}亿 公告日{r["ann_date"]}')

# 3. 财务指标
print('\n【财务指标】')
fina = pro.fina_indicator(ts_code=ts_code, period='20251231', fields='ts_code,roe,grossprofit_margin,netprofit_margin')
if len(fina) > 0:
    r = fina.iloc[0]
    print(f'ROE: {r["roe"]:.2f}%')
    print(f'毛利率: {r["grossprofit_margin"]:.1f}%')
    print(f'净利率: {r["netprofit_margin"]:.1f}%')

# 4. 增长率计算
print('\n【增长率对比】')
# 已知数据
revenue_yoy_2025 = 70.66  # 2025年报营收同比
revenue_yoy_2026q1 = 0.86  # 2026Q1营收同比（推测）

print(f'2025年报营收同比: +{revenue_yoy_2025:.2f}%')
print(f'2026Q1营收同比:   +{revenue_yoy_2026q1:.2f}%')
print(f'\nQ1增速 < 年报增速的50%: {"✓ 触发增长衰减降权" if revenue_yoy_2026q1 < revenue_yoy_2025 * 0.5 else "✗ 无降权"}')

# 5. BullScore惩罚项
print('\n【BullScore v3.1惩罚项】')
roe = 2.77
gross_margin = 21.1

penalties = []
if revenue_yoy_2026q1 < revenue_yoy_2025 * 0.5:
    penalties.append(('增长衰减', '预期差因子降权30%'))
if roe < 5:
    penalties.append(('ROE过低', f'ROE={roe:.2f}%<5%，上限60分'))
if gross_margin < 15:
    penalties.append(('毛利率过低', f'毛利率={gross_margin:.1f}%<15%，上限60分'))

if penalties:
    for item, reason in penalties:
        print(f'✓ {item}: {reason}')
else:
    print('✗ 无惩罚项')

# 6. 估算最终得分
print('\n【最终得分估算】')
base_score = 70
adjustments = 0

if revenue_yoy_2026q1 < revenue_yoy_2025 * 0.5:
    adjustments -= 10  # 增长衰减扣分
if roe < 5:
    adjustments -= 10  # ROE惩罚

final_score = base_score + adjustments
print(f'基础分: {base_score}')
print(f'调整项: {adjustments}')
print(f'最终分: {final_score}')

if final_score < 60:
    print(f'\n❌ 结论: {final_score}分 < 60分，不合格')
else:
    print(f'\n⚠️  矛盾: {final_score}分 ≥ 60分，应该合格')

# 7. 检查是否在其他池中
print('\n【检查其他股票池】')

# 检查沪深300
hs300 = pro.index_weight(index_code='000300.SH', start_date='20260601')
if ts_code in hs300['con_code'].values:
    print('✓ 沪深300成分股')
else:
    print('✗ 非沪深300成分股')

# 检查中证500
zz500 = pro.index_weight(index_code='000905.SH', start_date='20260601')
if ts_code in zz500['con_code'].values:
    print('✓ 中证500成分股')
else:
    print('✗ 非中证500成分股')

# 检查中证1000
zz1000 = pro.index_weight(index_code='000852.SH', start_date='20260601')
if ts_code in zz1000['con_code'].values:
    print('✓ 中证1000成分股')
else:
    print('✗ 非中证1000成分股')
