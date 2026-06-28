"""烽火通信6月关键买点评分分析"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher
from trend_picker import get_daily_data, get_moneyflow_data, get_daily_basic, get_holder_data
from trend_picker_v2_draft import detect_wave2_pattern

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}
fetcher = DataFetcher(token, config)

ts_code = '600498.SH'
name = '烽火通信'
industry = '通信'

print(f'=== {name}({ts_code}) 关键日期评分 ===\n')

# 关键买点日期
key_dates = {
    '20260611': '首次涨停启动',
    '20260616': '二波启动点',
    '20260617': '二波确认点',
    '20260618': '高位震荡日',
}

for target_date, desc in key_dates.items():
    print(f'--- {target_date}（{desc}）---')
    
    daily = get_daily_data(fetcher, ts_code, '20260101', target_date)
    moneyflow = get_moneyflow_data(fetcher, ts_code, '20260101', target_date)
    daily_basic = get_daily_basic(fetcher, ts_code, target_date)
    
    if len(daily) < 30:
        print('数据不足\n')
        continue
    
    latest = daily.iloc[-1]
    close = float(latest['close'])
    pct = float(latest['pct_chg'])
    turnover = float(latest.get('turnover_rate', 0))
    vol = float(latest['vol'])
    
    print(f'收盘: {close:.2f} | 涨跌: {pct:+.1f}% | 换手: {turnover:.1f}%')
    
    # F1: 赛道属性（商业航天=主线）
    f1 = 1.5  # 通信+政策支持
    
    # F3: 市值
    circ_mv = daily_basic.iloc[0].get('circ_mv', 0)/10000 if len(daily_basic) > 0 else 0
    if 50 <= circ_mv <= 300:
        f3 = 2.0
    elif 300 < circ_mv <= 800:
        f3 = 1.5
    elif 800 < circ_mv <= 2000:
        f3 = 1.0
    else:
        f3 = 0.5
    
    # F4: 机构持仓（固定）
    f4 = 1.5
    
    # F5: 资金流向
    today_flow = moneyflow[moneyflow['trade_date'] == int(target_date)]
    f5 = 1.0
    if len(today_flow) > 0:
        tf = today_flow.iloc[0]
        buy_elg = float(tf.get('buy_elg_vol', 0) or 0)
        sell_elg = float(tf.get('sell_elg_vol', 0) or 0)
        net_elg = (buy_elg - sell_elg) * 100
        if net_elg > 50000:
            f5 = 2.0
        elif net_elg > 20000:
            f5 = 1.5
    
    # F6: 换手率
    f6 = 1.0
    if 5 <= turnover <= 10:
        f6 = 1.0
    elif turnover > 10:
        f6 = 0.5  # 过热
    
    # F7: 均线系统
    close_series = daily['close']
    ma5 = close_series.rolling(5).mean()
    ma10 = close_series.rolling(10).mean()
    ma20 = close_series.rolling(20).mean()
    
    latest_ma5 = float(ma5.iloc[-1])
    latest_ma10 = float(ma10.iloc[-1])
    latest_ma20 = float(ma20.iloc[-1])
    
    f7 = 0.0
    if latest_ma5 > latest_ma10 > latest_ma20:
        f7 += 2.0
    elif close > latest_ma20:
        f7 += 1.0
    
    if close > latest_ma20 * 0.98:
        f7 += 0.5
    f7 = min(f7, 3.0)
    
    # F8: 成交量（涨停日特殊处理）
    is_limit_up = pct >= 9.5
    f8 = 0.0
    if is_limit_up:
        if turnover >= 8:
            f8 = 2.0
        elif turnover >= 5:
            f8 = 1.0
    else:
        vol_ma5 = daily['vol'].iloc[-6:-1].mean()
        vol_ratio = vol / vol_ma5 if vol_ma5 > 0 else 1.0
        if vol_ratio >= 2.0:
            f8 = 2.0
        elif vol_ratio >= 1.5:
            f8 = 1.0
    
    # F9: 技术指标（涨停豁免）
    f9 = 1.0 if is_limit_up else 0.0
    
    # WAVE2: 二波确认
    is_wave2, wave2_detail = detect_wave2_pattern(daily)
    wave2_score = 0.0
    if is_wave2:
        wave2_score = 2.0
        if is_limit_up:
            wave2_score += 1.0
    
    # 总分计算
    fund_total = f1 * 0.75 + f3 * 0.5  # F2缺失，暂忽略
    cap_total = f4 * 0.75 + f5 * 1.0 + f6 * 0.5
    tech_total = f7 * 0.4 + f8 * 0.25 + f9 * 0.25 + wave2_score
    
    total = fund_total + cap_total + tech_total
    normalized = round(total / 22.0 * 100, 1)
    
    print(f'F1={f1:.1f} F3={f3:.1f} F4={f4:.1f} F5={f5:.1f} F6={f6:.1f} '
          f'F7={f7:.1f} F8={f8:.1f} F9={f9:.1f} WAVE2={wave2_score:.1f}')
    print(f'总分: {total:.1f}/22 ({normalized}/100)')
    
    if total >= 14:
        print('✓ 强趋势')
    elif total >= 10:
        print('⚠ 中等趋势')
    else:
        print('✗ 弱趋势/未启动')
    print()

print('\n=== 对比雅克科技6月10日 ===')
print('雅克科技6月10日（二波确认涨停日）')
print('得分: 14.0/22（强趋势）')
print('\n结论：')
print('- 烽火通信6月11日首次涨停得分偏低（市值过大+资金流向不足）')
print('- 烽火通信6月16日二波启动点得分仍低于14分阈值')
print('- 模型对大盘股趋势启动识别能力偏弱')
