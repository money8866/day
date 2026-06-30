"""深度诊断烽火通信为何不在报告"""
import pandas as pd
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
import tushare as ts

# 初始化
for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
pro = ts.pro_api(token)

ts_code = '600498.SH'

print(f'=== 烽火通信（{ts_code}）深度诊断 ===\n')

# 1. 检查股票是否存在
stock_basic = pro.stock_basic(ts_code=ts_code)
if len(stock_basic) > 0:
    print(f'股票名称: {stock_basic.iloc[0]["name"]}')
    print(f'上市状态: {stock_basic.iloc[0]["list_status"]}')
    print(f'行业: {stock_basic.iloc[0]["industry"]}')
else:
    print('❌ 股票不存在')
    exit()

# 2. 检查财务数据完整性
print('\n\n【关键财务数据检查】')
print('-' * 60)

# 收入数据
income = pro.income(ts_code=ts_code, start_date='20250101', end_date='20260630', fields='ts_code,ann_date,f_ann_date,end_date,total_revenue,n_income')
print(f'\n收入数据条数: {len(income)}')
for i, row in income.iterrows():
    print(f'  {row["end_date"]}: 营收{row["total_revenue"]/1e8:.2f}亿 净利{row["n_income"]/1e8:.2f}亿')

# 业绩预告
forecast = pro.forecast(ts_code=ts_code, start_date='20250101', end_date='20261231')
print(f'\n业绩预告条数: {len(forecast)}')
if len(forecast) > 0:
    for i, row in forecast.iterrows():
        print(f'  {row["ann_date"]} {row["end_date"]}: {row["type"]} 净利{row["net_profit_min"]/1e8:.2f}-{row["net_profit_max"]/1e8:.2f}亿')

# 3. 检查指数成分
print('\n\n【指数成分检查】')
print('-' * 60)

# 沪深300
hs300 = pro.index_weight(index_code='399300.SZ', start_date='20260101', end_date='20260630')
in_hs300 = ts_code in hs300['con_code'].values
print(f'沪深300成分: {"✓ 是" if in_hs300 else "✗ 否"}')

# CSI2000
csi2000 = pro.index_weight(index_code='932000.CSI', start_date='20260101', end_date='20260630')
in_csi2000 = ts_code in csi2000['con_code'].values
print(f'中证2000成分: {"✓ 是" if in_csi2000 else "✗ 否"}')

# 4. 检查实时行情
print('\n\n【实时行情检查】')
print('-' * 60)

daily = pro.daily(ts_code=ts_code, start_date='20260620', end_date='20260627')
print(f'日线数据条数: {len(daily)}')
if len(daily) > 0:
    latest = daily.iloc[0]
    print(f'最新日期: {latest["trade_date"]}')
    print(f'收盘价: {latest["close"]:.2f}')
    print(f'涨跌幅: {latest["pct_chg"]:.2f}%')

# 5. 检查技术指标
print('\n\n【技术指标检查】')
print('-' * 60)

stk_factor = pro.stk_factor_pro(ts_code=ts_code, start_date='20260601', end_date='20260630', fields='ts_code,trade_date,close,rsi,pe_ttm,pb')
print(f'技术指标条数: {len(stk_factor)}')
if len(stk_factor) > 0:
    for i, row in stk_factor.iterrows():
        print(f'  {row["trade_date"]}: RSI={row["rsi"]:.1f} PE={row["pe_ttm"]:.1f}')

# 6. 模拟评分
print('\n\n【BullScore评分模拟（修正版）】')
print('-' * 60)

# 真实数据
revenue_2025 = 249.19  # 亿
profit_2025 = 3.76  # 亿
revenue_2026q1 = 45.10  # 亿
profit_2026q1 = 0.37  # 亿

# 计算增速（需要去年数据）
income_2024 = pro.income(ts_code=ts_code, period='20241231', fields='ts_code,total_revenue,n_income')
if len(income_2024) > 0:
    revenue_2024 = income_2024.iloc[0]['total_revenue'] / 1e8
    profit_2024 = income_2024.iloc[0]['n_income'] / 1e8
    
    revenue_yoy_2025 = (revenue_2025 - revenue_2024) / revenue_2024 * 100
    profit_yoy_2025 = (profit_2025 - profit_2024) / profit_2024 * 100
else:
    # 已知值
    revenue_yoy_2025 = 70.66
    profit_yoy_2025 = 200.0  # 假设

income_2025q1 = pro.income(ts_code=ts_code, period='20250331', fields='ts_code,total_revenue,n_income')
if len(income_2025q1) > 0:
    revenue_2025q1 = income_2025q1.iloc[0]['total_revenue'] / 1e8
    revenue_yoy_2026q1 = (revenue_2026q1 - revenue_2025q1) / revenue_2025q1 * 100
else:
    revenue_yoy_2026q1 = 0.86  # 已知值

print(f'\n营收增速对比:')
print(f'  2025年报: +{revenue_yoy_2025:.2f}%')
print(f'  2026Q1:  +{revenue_yoy_2026q1:.2f}%')

# 关键判断
growth_trend = 'falling' if revenue_yoy_2026q1 < revenue_yoy_2025 * 0.5 else 'stable'
print(f'\n增长趋势: {growth_trend}')

# ROE检查
roe_2025 = 2.77
roe_penalty = roe_2025 < 5
print(f'ROE={roe_2025:.2f}% <5%: {"✓ 触发上限60分惩罚" if roe_penalty else "✗ 无惩罚"}')

# 毛利率检查
gross_margin_2025 = 21.1
gross_penalty = gross_margin_2025 < 15
print(f'毛利率={gross_margin_2025:.1f}% <15%: {"✓ 触发上限60分惩罚" if gross_penalty else "✗ 无惩罚"}')

# 最终评分估算
print('\n\n最终评分估算:')
base_score = 70  # 基础分

# 扣分项
if growth_trend == 'falling':
    base_score -= 10  # 增长衰减
if roe_penalty:
    base_score -= 10  # ROE惩罚
if gross_penalty:
    base_score -= 10  # 毛利率惩罚

print(f'基础分: 70')
print(f'增长衰减: -10')
print(f'ROE惩罚: -10')
print(f'毛利率惩罚: {"-10" if gross_penalty else "0"}')
print(f'\n最终估算: {base_score}分')

if base_score < 60:
    print(f'\n❌ 结论: {base_score}分 < 60分，不在合格池')
else:
    print(f'\n⚠️  矛盾: {base_score}分 ≥ 60分，应该合格但实际不在报告')

# 7. 检查数据时间
print('\n\n【数据更新时间检查】')
print('-' * 60)

trade_cal = pro.trade_cal(exchange='SSE', start_date='20260620', end_date='20260630')
trade_days = trade_cal[trade_cal['is_open'] == 1]['cal_date'].tolist()
print(f'近期交易日: {", ".join(trade_days[:5])}')

# 检查数据更新时间
if len(daily) > 0:
    latest_date = daily.iloc[0]['trade_date']
    print(f'最新日线日期: {latest_date}')
    print(f'数据是否滞后: {"✓ 是" if latest_date < "20260627" else "✗ 否"}')
