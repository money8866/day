#!/usr/bin/env python3
import json

with open('D:\\mystock\\solo\\report_daily\\mainboard_v4_scan_20260618.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

stocks = data['data']
print(f'声明569只, 实际返回{len(stocks)}只 (筛选率{len(stocks)/569*100:.1f}%)')

# 评分分布
scores = [s.get('ultimate_score',0) for s in stocks]
print(f'终极评分: 最高{max(scores):.1f} 最低{min(scores):.1f} 中位数{sorted(scores)[len(scores)//2]:.1f}')

# 各分项相关性
print()
print('=== 分项相关系数 ===')
import math
def corr(data1, data2):
    n = len(data1)
    m1 = sum(data1)/n
    m2 = sum(data2)/n
    num = sum((a-m1)*(b-m2) for a,b in zip(data1,data2))
    d1 = math.sqrt(sum((a-m1)**2 for a in data1))
    d2 = math.sqrt(sum((b-m2)**2 for b in data2))
    return num/(d1*d2)

a_s = [s.get('score_a_core',0) for s in stocks]
b_s = [s.get('score_b_recognition',0) for s in stocks]
c_s = [s.get('score_c_value_health',0) for s in stocks]
d_s = [s.get('score_d_trend',0) for s in stocks]
u_s = [s.get('ultimate_score',0) for s in stocks]

print(f'  A(中军) vs 总分: {corr(a_s,u_s):.3f}')
print(f'  B(辨识度) vs 总分: {corr(b_s,u_s):.3f}')
print(f'  C(价值健康) vs 总分: {corr(c_s,u_s):.3f}')
print(f'  D(趋势结构) vs 总分: {corr(d_s,u_s):.3f}')
print(f'  A vs B: {corr(a_s,b_s):.3f} (独立度检验)')
print(f'  C vs D: {corr(c_s,d_s):.3f} (独立度检验)')

# 各阶段分布
stages = {}
for s in stocks:
    st = s.get('stage','未知')
    stages[st] = stages.get(st,0)+1
print()
print('=== 阶段分布 ===')
for st,cnt in sorted(stages.items(), key=lambda x:-x[1]):
    print(f'  {st}: {cnt}只')

# 各主题分布  
themes = {}
for s in stocks:
    t = s.get('theme','其他')
    themes[t] = themes.get(t,0)+1
print()
print('=== TOP15主题 ===')
for t,cnt in sorted(themes.items(), key=lambda x:-x[1])[:15]:
    print(f'  {t}: {cnt}只')

# 检查A项极端高但总分低的股票（可能有问题）
print()
print('=== A项高分(>95)但总分低(<80) ===')
for s in stocks:
    if s.get('score_a_core',0) > 95 and s.get('ultimate_score',0) < 80:
        print(f"  {s['name']}({s['ts_code']}): A={s['score_a_core']:.1f} 总分={s['ultimate_score']:.1f} B={s.get('score_b_recognition',0):.1f} C={s.get('score_c_value_health',0):.1f} D={s.get('score_d_trend',0):.1f}")

# 检查bull_score与ultimate_score的一致性
print()
print('=== bull_score(底部信号) vs ultimate_score ===')
bull_high = [s for s in stocks if s.get('bull_score',0) >= 80]
bull_low = [s for s in stocks if s.get('bull_score',0) < 80]
if bull_high:
    avg_u_high = sum(s.get('ultimate_score',0) for s in bull_high)/len(bull_high)
    print(f'  bull>=80的股票({len(bull_high)}只): 平均总分{avg_u_high:.1f}')
if bull_low:
    avg_u_low = sum(s.get('ultimate_score',0) for s in bull_low)/len(bull_low)
    print(f'  bull<80的股票({len(bull_low)}只): 平均总分{avg_u_low:.1f}')

# 空间分析结果
def calc_space_rating(stock):
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

spaces = {}
for s in stocks:
    sp = calc_space_rating(s)
    spaces[sp] = spaces.get(sp,0)+1
print()
print('=== 空间分类 ===')
for sp,cnt in sorted(spaces.items(), key=lambda x:-int(x[0].split('-')[0])):
    print(f'  {sp}: {cnt}只')
