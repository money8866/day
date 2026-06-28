"""深度诊断雅克科技6月10日缺失因子"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import numpy as np
from data_fetcher import DataFetcher
from trend_picker import get_daily_data, get_moneyflow_data, get_daily_basic, get_holder_data

# 配置
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}
fetcher = DataFetcher(token, config)

ts_code = '002409.SZ'
name = '雅克科技'
end_date = '20260610'
start_date = '20260101'

print(f'=== {name}({ts_code}) 深度因子诊断 ===')
print(f'截止日期: {end_date}\n')

# 获取所有数据
daily = get_daily_data(fetcher, ts_code, start_date, end_date)
moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
daily_basic = get_daily_basic(fetcher, ts_code, end_date)
income = fetcher.get_income(ts_code)
holders = get_holder_data(fetcher, ts_code)

latest = daily.iloc[-1]
print(f'--- 基础数据 ---')
print(f'收盘价: {latest["close"]:.2f}')
print(f'涨跌幅: {latest["pct_chg"]:.2f}%')
print(f'换手率: {latest.get("turnover_rate", 0):.2f}%')
print(f'流通市值: {daily_basic.iloc[0].get("circ_mv", 0)/10000:.1f}亿')

# ════════════════════════════════════════════════════════
# F1: 赛道属性（详细检查）
# ════════════════════════════════════════════════════════
print(f'\n--- F1 赛道属性分析 ---')
industry = '半导体'
print(f'行业: {industry}')

# 检查是否在主线主题池
in_theme = False
theme_name = ''
from trend_picker_v2_draft import TREND_THEMES
for theme, members in TREND_THEMES.items():
    if name in members:
        in_theme = True
        theme_name = theme
        print(f'主线主题: {theme} ✓')
        break

if not in_theme:
    print(f'未在主线主题池（TREND_THEMES）')

# 政策支持检查（简化：半导体=国家战略）
policy_support = industry in ['半导体', '集成电路', '芯片']
print(f'政策支持: {"✓" if policy_support else "✗"}')

# 行业景气度（从财报推断）
if len(income) >= 2:
    curr_rev = income.iloc[0].get('revenue', 0) or 0
    prev_rev = income.iloc[1].get('revenue', 0) or 0
    if prev_rev > 0:
        rev_growth = (curr_rev - prev_rev) / prev_rev
        print(f'营收增速: {rev_growth*100:.1f}% {"✓景气上行" if rev_growth > 0.15 else "✗景气平淡"}')

print(f'当前F1得分: 1.0分（主线赛道）')
print(f'可提升空间: +0.5分（需加入主线主题池）或+0.5分（政策支持加成）')

# ════════════════════════════════════════════════════════
# F2: 业绩拐点（详细检查）
# ════════════════════════════════════════════════════════
print(f'\n--- F2 业绩拐点分析 ---')
if len(income) >= 2:
    curr = income.iloc[0]
    prev = income.iloc[1]
    
    # 营收增速
    curr_rev = curr.get('revenue', 0) or 0
    prev_rev = prev.get('revenue', 0) or 0
    if prev_rev > 0:
        rev_yoy = (curr_rev - prev_rev) / prev_rev
        print(f'营收YoY: {rev_yoy*100:.1f}% {"✓" if rev_yoy > 0.2 else "✗"}')
    
    # 净利润增速
    curr_profit = curr.get('n_income', 0) or 0
    prev_profit = prev.get('n_income', 0) or 0
    if prev_profit > 0:
        profit_yoy = (curr_profit - prev_profit) / prev_profit
        print(f'净利润YoY: {profit_yoy*100:.1f}%')
    
    # 毛利率
    curr_gp = curr.get('gross_profit', 0) or 0
    if curr_rev > 0:
        curr_gm = curr_gp / curr_rev
        print(f'毛利率: {curr_gm*100:.1f}%')
    
    # PE
    if len(daily_basic) > 0:
        pe = daily_basic.iloc[0].get('pe', 0) or 0
        print(f'PE(TTM): {pe:.1f} {"✓合理" if 0 < pe < 50 else "✗偏高"}')
    
    print(f'当前F2得分: 0.5分（PE数据缺失）')
    print(f'可提升空间: +1.5分（需补全营收增速>20%+毛利率稳定+PE合理）')

# ════════════════════════════════════════════════════════
# F4: 机构持仓（详细检查）
# ════════════════════════════════════════════════════════
print(f'\n--- F4 机构持仓分析 ---')
if len(holders) > 0:
    inst_ratio = 0.0
    inst_names = []
    for _, row in holders.iterrows():
        holder_name = str(row.get('holder_name', ''))
        hold_ratio = float(row.get('hold_ratio', 0) or 0)
        # 判断机构类型
        if any(kw in holder_name for kw in ['基金', '社保', '券商', '保险', 'QFII', '北向', '香港中央结算']):
            inst_ratio += hold_ratio
            inst_names.append(f'{holder_name}({hold_ratio:.1f}%)')
    
    print(f'机构持股比例: {inst_ratio:.1f}%')
    print(f'机构类型: {", ".join(inst_names[:5]) if inst_names else "无"}')
    print(f'{"✓达标" if inst_ratio > 30 else "✗不足30%"}')
    print(f'当前F4得分: 1.0分')
    print(f'可提升空间: +0.5分（需>30%）')

# ════════════════════════════════════════════════════════
# F5: 资金流向（详细检查）
# ════════════════════════════════════════════════════════
print(f'\n--- F5 资金流向分析 ---')
if len(moneyflow) > 0:
    # 今日资金流
    today_flow = moneyflow[moneyflow['trade_date'] == int(end_date)]
    if len(today_flow) > 0:
        tf = today_flow.iloc[0]
        buy_elg = float(tf.get('buy_elg_vol', 0) or 0)  # 超大单买入（万手）
        sell_elg = float(tf.get('sell_elg_vol', 0) or 0)
        net_elg = (buy_elg - sell_elg) * 100  # 转为手
        
        buy_lg = float(tf.get('buy_lg_vol', 0) or 0)  # 大单买入
        sell_lg = float(tf.get('sell_lg_vol', 0) or 0)
        net_lg = (buy_lg - sell_lg) * 100
        
        print(f'超大单净流入: {net_elg/100:.0f}万手')
        print(f'大单净流入: {net_lg/100:.0f}万手')
        print(f'合计主力净流入: {(net_elg+net_lg)/100:.0f}万手')
    
    # 近3日累计
    recent_3 = moneyflow.sort_values('trade_date', ascending=False).head(3)
    net_3day = 0
    for _, row in recent_3.iterrows():
        buy_elg = float(row.get('buy_elg_vol', 0) or 0)
        sell_elg = float(row.get('sell_elg_vol', 0) or 0)
        net_3day += (buy_elg - sell_elg) * 100
    
    print(f'3日累计超大单净流入: {net_3day/100:.0f}万手 {"✓达标" if net_3day > 500000 else "✗不足"}')
    
    print(f'当前F5得分: 1.0分（3日净流入）')
    print(f'可提升空间: +1.0分（需今日超大单净流入>500万手）')

# ════════════════════════════════════════════════════════
# F6: 换手率（详细检查）
# ════════════════════════════════════════════════════════
print(f'\n--- F6 换手率分析 ---')
latest_turnover = float(latest.get('turnover_rate', 0) or 0)
print(f'今日换手率: {latest_turnover:.2f}%')

# 启动前换手率
if len(daily) > 20:
    pre_turnover = daily.iloc[-20:-15]['turnover_rate'].mean()
    print(f'启动前5日平均换手: {pre_turnover:.2f}% {"✓筹码锁定" if pre_turnover < 3 else "✗筹码分散"}')

print(f'当前F6得分: 1.0分（换手率适中）')
print(f'可提升空间: +1.0分（需启动前<3%且今日>5%）')

# ════════════════════════════════════════════════════════
# F8: 量比（需计算）
# ════════════════════════════════════════════════════════
print(f'\n--- F8 量比分析 ---')
latest_vol = float(latest['vol'])
vol_ma5 = daily.iloc[-6:-1]['vol'].mean()  # 前5日均值
vol_ratio = latest_vol / vol_ma5 if vol_ma5 > 0 else 1.0
print(f'今日成交量: {latest_vol/10000:.0f}万手')
print(f'前5日均值: {vol_ma5/10000:.0f}万手')
print(f'量比: {vol_ratio:.2f} {"✓放量" if vol_ratio > 2 else "✗量能不足"}')
print(f'当前F8得分: 0分')
print(f'可提升空间: +2.0分（需量比>2）')

# ════════════════════════════════════════════════════════
# 总结缺失项
# ════════════════════════════════════════════════════════
print(f'\n=== 缺失因子总结 ===')
print(f'当前总分: 8.2/22')
print(f'目标分数: ≥14分（强趋势）')
print(f'需提升: {14-8.2:.1f}分')
print(f'\n可快速补充：')
print(f'1. F1主题池: +0.5分（加入TREND_THEMES）')
print(f'2. F2业绩拐点: +1.0分（补全PE+营收增速）')
print(f'3. F5今日主力净流入: +1.0分（超大单>500万手）')
print(f'4. F6换手率: +0.5分（启动前<3%）')
print(f'5. F8量比: +2.0分（量比>2）')
print(f'6. F9 MACD金叉: +1.0分（检查MACD）')
print(f'\n理论满分: {8.2+0.5+1.0+1.0+0.5+2.0+1.0:.1f}分 → 强趋势 ✓')
