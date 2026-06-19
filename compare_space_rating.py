#!/usr/bin/env python3
import json

with open('D:\\mystock\\solo\\report_daily\\mainboard_v4_scan_20260618.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

stocks = data['data']

# 原版空间分析
def calc_space_old(stock):
    bias_ma20 = stock.get('bias_ma20', 0)
    bias_ma60 = stock.get('bias_ma60', 0)
    ret_60 = stock.get('ret_60', 0)
    ret_120 = stock.get('ret_120', 0)
    score_c = stock.get('score_c_value_health', 0)
    score_d = stock.get('score_d_trend', 0)
    ultimate = stock.get('ultimate_score', 0)
    drawdown_120 = stock.get('drawdown_from_high_120', 0)
    stage = stock.get('stage', '')
    
    base = 100
    if bias_ma20 > 20: base -= 60
    elif bias_ma20 > 15: base -= 40
    elif bias_ma20 > 10: base -= 25
    elif bias_ma20 > 5: base -= 10
    elif bias_ma20 < -5: base += 10
    
    if bias_ma60 > 50: base -= 30
    elif bias_ma60 > 30: base -= 15
    elif bias_ma60 > 20: base -= 5
    
    if ret_60 > 150: base -= 40
    elif ret_60 > 100: base -= 25
    elif ret_60 > 50: base -= 10
    
    if ret_120 > 200: base -= 30
    elif ret_120 > 100: base -= 15
    
    if score_c >= 90: base += 15
    elif score_c >= 80: base += 10
    
    if score_d >= 90: base += 10
    elif score_d >= 80: base += 5
    
    if ultimate >= 90: base += 10
    elif ultimate >= 85: base += 5
    
    if drawdown_120 < -30: base += 10
    elif drawdown_120 < -20: base += 5
    
    if '健康洗盘' in stage: base += 5
    elif '高位' in stage or '透支' in stage: base -= 20
    
    base = max(0, min(100, base))
    if base >= 80: return "25-40%"
    elif base >= 60: return "15-25%"
    elif base >= 40: return "8-15%"
    else: return "0-8%"

# 修复版空间分析（从这里复制逻辑）
def calc_space_new(stock):
    bias_ma20 = stock.get('bias_ma20', 0)
    bias_ma60 = stock.get('bias_ma60', 0)
    ret_60 = stock.get('ret_60', 0)
    ret_120 = stock.get('ret_120', 0)
    score_c = stock.get('score_c_value_health', 0)
    score_d = stock.get('score_d_trend', 0)
    ultimate = stock.get('ultimate_score', 0)
    drawdown_120 = stock.get('drawdown_from_high_120', 0)
    stage = stock.get('stage', '')
    limit_up_120 = stock.get('limit_up_count_120', 0)
    
    base = 100
    
    if bias_ma20 > 25: base -= 70
    elif bias_ma20 > 20: base -= 55
    elif bias_ma20 > 15: base -= 40
    elif bias_ma20 > 10: base -= 25
    elif bias_ma20 > 5: base -= 12
    elif bias_ma20 > 0: base -= 5
    elif bias_ma20 < -8: base += 8
    elif bias_ma20 < -5: base += 5
    
    if bias_ma60 > 60: base -= 40
    elif bias_ma60 > 50: base -= 30
    elif bias_ma60 > 40: base -= 20
    elif bias_ma60 > 30: base -= 12
    elif bias_ma60 > 20: base -= 6
    
    if ret_60 > 120: base -= 50
    elif ret_60 > 80: base -= 35
    elif ret_60 > 50: base -= 18
    elif ret_60 > 30: base -= 8
    
    if ret_120 > 250: base -= 40
    elif ret_120 > 150: base -= 25
    elif ret_120 > 100: base -= 12
    
    if score_c >= 95: base += 12
    elif score_c >= 90: base += 8
    elif score_c >= 80: base += 5
    elif score_c < 70: base -= 10
    
    if score_d >= 95: base += 8
    elif score_d >= 90: base += 5
    elif score_d >= 80: base += 3
    
    if ultimate >= 92: base += 6
    elif ultimate >= 88: base += 3
    
    if drawdown_120 < -35: base += 12
    elif drawdown_120 < -25: base += 6
    elif drawdown_120 < -15: base += 3
    
    if '健康洗盘' in stage: base += 3
    elif '强势整理' in stage: base += 1
    elif '高位' in stage or '透支' in stage: base -= 25
    elif '趋势走弱' in stage: base -= 15
    
    if limit_up_120 >= 8: base += 5
    elif limit_up_120 >= 5: base += 3
    
    base = max(0, min(100, base))
    if base >= 85: return "20-35%"
    elif base >= 65: return "12-22%"
    elif base >= 45: return "5-12%"
    elif base >= 25: return "0-8%"
    else: return "风险提示"

# 统计
old_stats = {"25-40%":0, "15-25%":0, "8-15%":0, "0-8%":0}
new_stats = {"20-35%":0, "12-22%":0, "5-12%":0, "0-8%":0, "风险提示":0}

for s in stocks:
    old_space = calc_space_old(s)
    old_stats[old_space] = old_stats.get(old_space, 0) + 1
    
    new_space = calc_space_new(s)
    new_stats[new_space] = new_stats.get(new_space, 0) + 1

print("=" * 60)
print("修复前后空间分布对比")
print("=" * 60)
print(f"{'空间区间':<12} {'原版（过于乐观）':<20} {'修复版（更准确）':<20} {'变化'}")
print("-" * 60)
print(f"充足/20-35%  {old_stats['25-40%']:>4}只({old_stats['25-40%']/len(stocks)*100:>5.1f}%)  {new_stats['20-35%']:>4}只({new_stats['20-35%']/len(stocks)*100:>5.1f}%)  {new_stats['20-35%']-old_stats['25-40%']:>+d}")
print(f"仍有/12-22%  {old_stats['15-25%']:>4}只({old_stats['15-25%']/len(stocks)*100:>5.1f}%)  {new_stats['12-22%']:>4}只({new_stats['12-22%']/len(stocks)*100:>5.1f}%)  {new_stats['12-22%']-old_stats['15-25%']:>+d}")
print(f"小幅/5-12%   {old_stats['8-15%']:>4}只({old_stats['8-15%']/len(stocks)*100:>5.1f}%)  {new_stats['5-12%']:>4}只({new_stats['5-12%']/len(stocks)*100:>5.1f}%)  {new_stats['5-12%']-old_stats['8-15%']:>+d}")
print(f"有限/0-8%   {old_stats['0-8%']:>4}只({old_stats['0-8%']/len(stocks)*100:>5.1f}%)  {new_stats['0-8%']:>4}只({new_stats['0-8%']/len(stocks)*100:>5.1f}%)  {new_stats['0-8%']-old_stats['0-8%']:>+d}")
print(f"风险提示     {'--':>8}  {new_stats['风险提示']:>4}只({new_stats['风险提示']/len(stocks)*100:>5.1f}%)  +{new_stats['风险提示']}")

print()
print("=" * 60)
print("重点股票空间变化")
print("=" * 60)

key_stocks = ['000960.SZ', '002015.SZ', '600301.SH', '601958.SH', '000060.SZ']
for s in stocks:
    if s['ts_code'] in key_stocks:
        old = calc_space_old(s)
        new = calc_space_new(s)
        change = "🔻下调" if old != new and ("40%" in old or "35%" in old) else ("🔺上调" if "8%" in old and "20%" in new else "➡️不变")
        print(f"{s['name']}({s['ts_code']}): MA20={s.get('bias_ma20',0):+.1f}%  原版={old}  修复版={new}  {change}")
