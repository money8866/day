"""用趋势模型v2分析烽火通信6月走势"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import numpy as np
from data_fetcher import DataFetcher
from trend_picker import get_daily_data, get_moneyflow_data, get_daily_basic, get_holder_data
from trend_picker_v2_draft import detect_wave2_pattern, TREND_THEMES, STRATEGIC_INDUSTRIES

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}
fetcher = DataFetcher(token, config)

ts_code = '600498.SH'
name = '烽火通信'
industry = '通信'
end_date = '20260618'  # 分析6月18日（趋势启动日）
start_date = '20260101'

print(f'=== {name}({ts_code}) 趋势模型v2分析 ===')
print(f'分析日期: {end_date}\n')

# 获取数据
daily = get_daily_data(fetcher, ts_code, start_date, end_date)
moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
daily_basic = get_daily_basic(fetcher, ts_code, end_date)
income = fetcher.get_income(ts_code)
holders = get_holder_data(fetcher, ts_code)

if len(daily) < 30:
    print('数据不足')
    sys.exit(1)

latest = daily.iloc[-1]
latest_close = float(latest['close'])
latest_pct = float(latest['pct_chg'])
latest_turnover = float(latest.get('turnover_rate', 0))
latest_vol = float(latest['vol'])

print(f'--- 基础数据 ---')
print(f'收盘价: {latest_close:.2f}')
print(f'涨跌幅: {latest_pct:+.2f}%')
print(f'换手率: {latest_turnover:.2f}%')

circ_mv = daily_basic.iloc[0].get('circ_mv', 0)/10000 if len(daily_basic) > 0 else 0
print(f'流通市值: {circ_mv:.0f}亿\n')

# ════════════════════════════════════════════════════════
# F1: 赛道属性
# ════════════════════════════════════════════════════════
f1_score = 0.0
f1_detail = {}

# 检查主线行业
is_strategic = any(si in industry for si in STRATEGIC_INDUSTRIES)
if is_strategic:
    f1_score += 1.0
    f1_detail['strategic_industry'] = True

# 检查主线主题池
in_theme = False
theme_name = ''
for theme, members in TREND_THEMES.items():
    if name in members:
        in_theme = True
        theme_name = theme
        f1_score += 0.5
        f1_detail['theme'] = theme
        break

# 商业航天属主线
if '商业航天' in industry or '通信' in industry:
    f1_score += 0.5
    f1_detail['policy_support'] = True

f1_score = min(f1_score, 2.0)
print(f'F1 赛道属性: {f1_score:.1f}分')
for k, v in f1_detail.items():
    print(f'  {k}: {v}')

# ════════════════════════════════════════════════════════
# F2: 业绩拐点
# ════════════════════════════════════════════════════════
f2_score = 0.0
if len(income) >= 2:
    curr = income.iloc[0]
    prev = income.iloc[1]
    
    curr_rev = curr.get('revenue', 0) or 0
    prev_rev = prev.get('revenue', 0) or 0
    if prev_rev > 0:
        rev_yoy = (curr_rev - prev_rev) / prev_rev
        if rev_yoy > 0.2:
            f2_score += 1.0
            print(f'F2 业绩拐点: {f2_score:.1f}分（营收YoY {rev_yoy*100:.1f}%）')
        elif rev_yoy > 0.1:
            f2_score += 0.5
            print(f'F2 业绩拐点: {f2_score:.1f}分（营收YoY {rev_yoy*100:.1f}%）')
    
    curr_gp = curr.get('gross_profit', 0) or 0
    if curr_rev > 0:
        curr_gm = curr_gp / curr_rev
        prev_gp = prev.get('gross_profit', 0) or 0
        prev_rev2 = prev.get('revenue', 0) or 0
        if prev_rev2 > 0:
            prev_gm = prev_gp / prev_rev2
            if curr_gm >= prev_gm:
                f2_score += 0.5
                print(f'  毛利率 {curr_gm*100:.1f}% 稳定')
    
    if len(daily_basic) > 0:
        pe = daily_basic.iloc[0].get('pe', 0) or 0
        if 0 < pe < 50:
            f2_score += 0.5
            print(f'  PE(TTM) {pe:.1f} 合理')

f2_score = min(f2_score, 2.0)

# ════════════════════════════════════════════════════════
# F3: 市值区间
# ════════════════════════════════════════════════════════
f3_score = 0.0
if circ_mv > 0:
    if 50 <= circ_mv <= 300:
        f3_score = 2.0
    elif 300 < circ_mv <= 800:
        f3_score = 1.5
    elif 800 < circ_mv <= 2000:
        f3_score = 1.0
    print(f'F3 市值区间: {f3_score:.1f}分（流通市值{circ_mv:.0f}亿）')

fund_total = f1_score * 0.75 + f2_score * 0.75 + f3_score * 0.5
print(f'→ 基本面合计: {fund_total:.2f}分\n')

# ════════════════════════════════════════════════════════
# F4: 机构持仓
# ════════════════════════════════════════════════════════
f4_score = 1.0
if len(holders) > 0:
    inst_ratio = 0.0
    for _, row in holders.iterrows():
        holder_name = str(row.get('holder_name', ''))
        hold_ratio = float(row.get('hold_ratio', 0) or 0)
        if any(kw in holder_name for kw in ['基金', '社保', '券商', '保险', 'QFII', '北向']):
            inst_ratio += hold_ratio
    
    if inst_ratio > 30:
        f4_score = 1.5
    print(f'F4 机构持仓: {f4_score:.1f}分（机构持股{inst_ratio:.1f}%）')

# ════════════════════════════════════════════════════════
# F5: 资金流向
# ════════════════════════════════════════════════════════
f5_score = 0.0
if len(moneyflow) > 0:
    # 今日资金流
    today_flow = moneyflow[moneyflow['trade_date'] == int(end_date)]
    if len(today_flow) > 0:
        tf = today_flow.iloc[0]
        buy_elg = float(tf.get('buy_elg_vol', 0) or 0)
        sell_elg = float(tf.get('sell_elg_vol', 0) or 0)
        net_elg = (buy_elg - sell_elg) * 100
        
        buy_lg = float(tf.get('buy_lg_vol', 0) or 0)
        sell_lg = float(tf.get('sell_lg_vol', 0) or 0)
        net_lg = (buy_lg - sell_lg) * 100
        
        net_main = net_elg + net_lg
        if net_elg > 50000:
            f5_score = 2.0
            print(f'F5 资金流向: {f5_score:.1f}分（超大单净流入{net_elg/100:.0f}万手）')
        elif net_main > 100000:
            f5_score = 2.0
            print(f'F5 资金流向: {f5_score:.1f}分（主力合计净流入{net_main/100:.0f}万手）')
        elif net_elg > 20000:
            f5_score = 1.5
            print(f'F5 资金流向: {f5_score:.1f}分（超大单净流入{net_elg/100:.0f}万手）')
        else:
            f5_score = 1.0
            print(f'F5 资金流向: {f5_score:.1f}分')
    
    # 3日累计
    recent_3 = moneyflow.sort_values('trade_date', ascending=False).head(3)
    net_3day = 0
    for _, row in recent_3.iterrows():
        buy_elg = float(row.get('buy_elg_vol', 0) or 0)
        sell_elg = float(row.get('sell_elg_vol', 0) or 0)
        net_3day += (buy_elg - sell_elg) * 100
    print(f'  3日累计超大单净流入: {net_3day/100:.0f}万手')

# ════════════════════════════════════════════════════════
# F6: 换手率
# ════════════════════════════════════════════════════════
f6_score = 1.0
if 5 <= latest_turnover <= 10:
    f6_score = 1.0
elif latest_turnover > 10:
    f6_score = 0.5  # 过热
    print(f'F6 换手率: {f6_score:.1f}分（过热警告）')

cap_total = f4_score * 0.75 + f5_score * 1.0 + f6_score * 0.5
print(f'→ 资金面合计: {cap_total:.2f}分\n')

# ════════════════════════════════════════════════════════
# 技术面
# ════════════════════════════════════════════════════════
close = daily['close']
ma5 = close.rolling(5).mean()
ma10 = close.rolling(10).mean()
ma20 = close.rolling(20).mean()
ma60 = close.rolling(60).mean()

latest_ma5 = float(ma5.iloc[-1])
latest_ma10 = float(ma10.iloc[-1])
latest_ma20 = float(ma20.iloc[-1])
latest_ma60 = float(ma60.iloc[-1]) if len(ma60) > 0 and not pd.isna(ma60.iloc[-1]) else 0

# F7: 均线系统
f7_score = 0.0
if latest_ma5 > latest_ma10 > latest_ma20:
    f7_score += 2.0
    print(f'F7 均线系统: {f7_score:.1f}分（多头排列）')
elif latest_close > latest_ma20:
    f7_score += 1.0
    print(f'F7 均线系统: {f7_score:.1f}分（MA20上方）')

if latest_close > latest_ma20 * 0.98:
    f7_score += 0.5
if latest_ma60 > 0 and latest_close > latest_ma60:
    f7_score += 0.5

f7_score = min(f7_score, 3.0)

# F8: 量比（涨停日特殊处理）
is_limit_up = latest_pct >= 9.5
f8_score = 0.0

if is_limit_up:
    # 涨停日用换手率判断
    if latest_turnover >= 8:
        f8_score = 2.0
        print(f'F8 成交量: {f8_score:.1f}分（涨停换手率{latest_turnover:.1f}%）')
    elif latest_turnover >= 5:
        f8_score = 1.0
        print(f'F8 成交量: {f8_score:.1f}分（涨停换手率{latest_turnover:.1f}%）')
else:
    # 非涨停日用量比
    vol_ma5 = daily['vol'].iloc[-6:-1].mean()
    vol_ratio = latest_vol / vol_ma5 if vol_ma5 > 0 else 1.0
    if vol_ratio >= 2.0:
        f8_score = 2.0
    elif vol_ratio >= 1.5:
        f8_score = 1.0
    print(f'F8 成交量: {f8_score:.1f}分（量比{vol_ratio:.2f}）')

# F9: 技术指标（涨停豁免）
f9_score = 0.0
if is_limit_up:
    f9_score = 1.0
    print(f'F9 技术指标: {f9_score:.1f}分（涨停豁免）')
else:
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(6).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
    rs = gain / loss.replace(0, 0.0001)
    rsi6 = 100 - (100 / (1 + rs))
    latest_rsi6 = float(rsi6.iloc[-1])
    
    if latest_rsi6 > 80:
        print(f'F9 技术指标: 0分（RSI过热{latest_rsi6:.1f}）')
    elif 50 <= latest_rsi6 <= 70:
        f9_score += 1.0
        print(f'F9 技术指标: {f9_score:.1f}分（RSI={latest_rsi6:.1f}）')

# WAVE2: 二波确认
is_wave2, wave2_detail = detect_wave2_pattern(daily)
wave2_score = 0.0
if is_wave2:
    wave2_score = 2.0
    if is_limit_up:
        wave2_score += 1.0
    print(f'WAVE2 二波确认: {wave2_score:.1f}分')
    for k, v in wave2_detail.items():
        print(f'  {k}: {v}')

tech_total = f7_score * 0.4 + f8_score * 0.25 + f9_score * 0.25 + wave2_score
print(f'→ 技术面合计: {tech_total:.2f}分\n')

# ════════════════════════════════════════════════════════
# 总分计算
# ════════════════════════════════════════════════════════
total_score = fund_total + cap_total + tech_total
normalized = round(total_score / 22.0 * 100, 1)

print(f'='*60)
print(f'【总分】{total_score:.1f}/22 （标准化: {normalized}/100）')
print(f'='*60)

if total_score >= 14:
    print(f'✓ 强趋势（A点二波确认买点）')
elif total_score >= 10:
    print(f'⚠ 中等趋势（需结合买点判断）')
elif total_score >= 7:
    print(f'⚠ 弱趋势')
else:
    print(f'✗ 趋势终结或未启动')

# ════════════════════════════════════════════════════════
# 历史回溯（6月关键日期）
# ════════════════════════════════════════════════════════
print(f'\n=== 6月关键日期回溯 ===')
key_dates = ['20260602', '20260603', '20260604', '20260605', '20260606', '20260609', '20260610', '20260611', '20260612', '20260613', '20260616', '20260617', '20260618', '20260619', '20260620', '20260623', '20260624', '20260625', '20260626']
for kd in key_dates:
    kd_row = daily[daily['trade_date'] == int(kd)]
    if len(kd_row) > 0:
        kr = kd_row.iloc[0]
        kp = float(kr['pct_chg'])
        kc = float(kr['close'])
        kt = float(kr.get('turnover_rate', 0))
        kv = float(kr['vol']) / 10000
        mark = '***' if abs(kp) > 7 else ''
        print(f'{kd} | {kc:.2f} | {kp:+5.1f}% | 换手{kt:.1f}% | {kv:.0f}万手 {mark}')
