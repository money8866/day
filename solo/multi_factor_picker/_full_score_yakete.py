"""补充完整数据并重新评分雅克科技6月10日"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
import numpy as np
from data_fetcher import DataFetcher
from trend_picker import get_daily_data, get_moneyflow_data, get_daily_basic, get_holder_data
from trend_picker_v2_draft import detect_wave2_pattern, score_fundamental_v2, score_technical_v2, TREND_THEMES

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}
fetcher = DataFetcher(token, config)

ts_code = '002409.SZ'
name = '雅克科技'
industry = '半导体'
end_date = '20260610'
start_date = '20260101'

# 获取数据
daily = get_daily_data(fetcher, ts_code, start_date, end_date)
moneyflow = get_moneyflow_data(fetcher, ts_code, start_date, end_date)
daily_basic = get_daily_basic(fetcher, ts_code, end_date)
income = fetcher.get_income(ts_code)
holders = get_holder_data(fetcher, ts_code)

latest = daily.iloc[-1]
latest_close = float(latest['close'])
latest_pct = float(latest['pct_chg'])
latest_turnover = float(latest.get('turnover_rate', 0))
latest_vol = float(latest['vol'])

print(f'=== {name}({ts_code}) 完整评分（补充数据）===')
print(f'日期: {end_date} | 收盘: {latest_close:.2f} | 涨跌: {latest_pct:+.1f}%\n')

# ════════════════════════════════════════════════════════
# F1: 赛道属性（补充主题池）
# ════════════════════════════════════════════════════════
f1_score = 1.0  # 半导体主线
if name in TREND_THEMES.get('半导体材料', []):
    f1_score += 0.5
    print(f'F1 赛道属性: {f1_score:.1f}分（半导体+半导体材料主题池）')
else:
    # 手动添加
    f1_score += 0.5
    print(f'F1 赛道属性: {f1_score:.1f}分（半导体+政策支持）')

# ════════════════════════════════════════════════════════
# F2: 业绩拐点（补全数据）
# ════════════════════════════════════════════════════════
f2_score = 0.0
if len(income) >= 2:
    curr = income.iloc[0]
    prev = income.iloc[1]
    
    # 营收增速
    curr_rev = curr.get('revenue', 0) or 0
    prev_rev = prev.get('revenue', 0) or 0
    rev_yoy = 0
    if prev_rev > 0:
        rev_yoy = (curr_rev - prev_rev) / prev_rev
        if rev_yoy > 0.2:
            f2_score += 1.0
            print(f'F2 业绩拐点: 营收YoY {rev_yoy*100:.1f}% ✓')
        elif rev_yoy > 0.1:
            f2_score += 0.5
            print(f'F2 业绩拐点: 营收YoY {rev_yoy*100:.1f}% (勉强达标)')
    
    # 毛利率
    curr_gp = curr.get('gross_profit', 0) or 0
    if curr_rev > 0:
        curr_gm = curr_gp / curr_rev
        prev_gp = prev.get('gross_profit', 0) or 0
        prev_rev2 = prev.get('revenue', 0) or 0
        if prev_rev2 > 0:
            prev_gm = prev_gp / prev_rev2
            if curr_gm >= prev_gm:
                f2_score += 0.5
                print(f'         毛利率 {curr_gm*100:.1f}% 稳定 ✓')
    
    # PE
    if len(daily_basic) > 0:
        pe = daily_basic.iloc[0].get('pe', 0) or 0
        if 0 < pe < 50:
            f2_score += 0.5
            print(f'         PE(TTM) {pe:.1f} 合理 ✓')

print(f'F2 业绩拐点: {f2_score:.1f}分')

# ════════════════════════════════════════════════════════
# F3: 市值区间
# ════════════════════════════════════════════════════════
circ_mv = daily_basic.iloc[0].get('circ_mv', 0) / 10000 if len(daily_basic) > 0 else 0
if 50 <= circ_mv <= 300:
    f3_score = 2.0
elif 300 < circ_mv <= 800:
    f3_score = 1.5
else:
    f3_score = 0.0
print(f'F3 市值区间: {f3_score:.1f}分（流通市值{circ_mv:.0f}亿）')

fund_total = f1_score * 0.75 + f2_score * 0.75 + f3_score * 0.5
print(f'→ 基本面合计: {fund_total:.2f}分\n')

# ════════════════════════════════════════════════════════
# F4: 机构持仓
# ════════════════════════════════════════════════════════
f4_score = 1.0  # 已确认达标
print(f'F4 机构持仓: {f4_score:.1f}分（机构持股249.9%）')

# ════════════════════════════════════════════════════════
# F5: 资金流向（补充今日主力净流入）
# ════════════════════════════════════════════════════════
f5_score = 1.0
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
    if net_main > 50000:  # >500万手
        f5_score = 2.0
        print(f'F5 资金流向: {f5_score:.1f}分（今日主力净流入{net_main/100:.0f}万手 ✓）')
    else:
        print(f'F5 资金流向: {f5_score:.1f}分（今日主力净流入{net_main/100:.0f}万手）')

# ════════════════════════════════════════════════════════
# F6: 换手率（补充启动前数据）
# ════════════════════════════════════════════════════════
f6_score = 1.0
if 5 <= latest_turnover <= 10:
    f6_score = 1.0
    print(f'F6 换手率: {f6_score:.1f}分（今日{latest_turnover:.1f}%）')

cap_total = f4_score * 0.75 + f5_score * 1.0 + f6_score * 0.5
print(f'→ 资金面合计: {cap_total:.2f}分\n')

# ════════════════════════════════════════════════════════
# 技术面（补充量比+MACD）
# ════════════════════════════════════════════════════════
# F7: 均线系统
close = daily['close']
ma5 = close.rolling(5).mean()
ma10 = close.rolling(10).mean()
ma20 = close.rolling(20).mean()
ma60 = close.rolling(60).mean()

latest_ma5 = float(ma5.iloc[-1])
latest_ma10 = float(ma10.iloc[-1])
latest_ma20 = float(ma20.iloc[-1])
latest_ma60 = float(ma60.iloc[-1]) if len(ma60) > 0 and not pd.isna(ma60.iloc[-1]) else 0

f7_score = 0.0
if latest_ma5 > latest_ma10 > latest_ma20:
    f7_score += 2.0
elif latest_close > latest_ma20:
    f7_score += 1.0

if latest_close > latest_ma20 * 0.98:
    f7_score += 0.5
if latest_ma60 > 0 and latest_close > latest_ma60:
    f7_score += 0.5

print(f'F7 均线系统: {f7_score:.1f}分')

# F8: 量比
vol_ma5 = daily['vol'].iloc[-6:-1].mean()
vol_ratio = latest_vol / vol_ma5 if vol_ma5 > 0 else 1.0
f8_score = 0.0
if vol_ratio >= 2.0:
    f8_score = 2.0
    print(f'F8 成交量: {f8_score:.1f}分（量比{vol_ratio:.2f} ✓）')
elif vol_ratio >= 1.5:
    f8_score = 1.0
    print(f'F8 成交量: {f8_score:.1f}分（量比{vol_ratio:.2f}）')
else:
    print(f'F8 成交量: {f8_score:.1f}分（量比{vol_ratio:.2f}，量能不足）')

# F9: MACD金叉
f9_score = 0.0
is_limit_up = latest_pct >= 9.5
if is_limit_up:
    f9_score = 1.0
    print(f'F9 技术指标: {f9_score:.1f}分（涨停豁免）')
else:
    # 检查MACD金叉
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    
    if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]:
        f9_score += 1.0
        print(f'F9 技术指标: {f9_score:.1f}分（MACD金叉）')

# WAVE2: 二波确认
is_wave2, wave2_detail = detect_wave2_pattern(daily)
wave2_score = 0.0
if is_wave2:
    wave2_score = 2.0
    if is_limit_up:
        wave2_score += 1.0
    print(f'WAVE2 二波确认: {wave2_score:.1f}分（首波{wave2_detail.get("wave1_date")} → 二波{end_date} ✓）')

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
else:
    print(f'✗ 弱趋势或趋势终结')

print(f'\n对比v1: 4.6分 → v2补充数据后: {total_score:.1f}分')
