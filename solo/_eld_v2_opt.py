# -*- coding: utf-8 -*-
"""ELD V2 优化分析：0804/0806 全样本 5 日最高涨幅，找大牛股因子特征"""
import csv, sys
import statistics as st
sys.path.insert(0, r'D:\mystock\solo')
from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file

def max_gain_5d(ts_code, sig_date):
    f = ts_code_to_tdx_file(ts_code)
    if not f:
        return None
    df = parse_tdx_day_file(f)
    if df is None or df.empty:
        return None
    match = df.index[df['trade_date'] >= sig_date]
    if len(match) == 0:
        return None
    idx = match[0]
    if idx >= len(df) - 1:
        return None
    base = df.iloc[idx]['close']
    fut = df.iloc[idx + 1: idx + 6]
    if fut.empty:
        return None
    hi = fut['high'].max()
    return (hi - base) / base * 100

def load_eld(date):
    p = rf'D:\mystock\report_daily\eld_report_{date}.csv'
    with open(p, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return rows

def to_f(x, d=0.0):
    try:
        return float(x or d)
    except:
        return d

# 两天全样本
for DATE in ['20260804', '20260806']:
    rows = load_eld(DATE)
    print(f'\n{"="*90}')
    print(f'  {DATE}  ELD 全样本 5 日最高涨幅分析（n={len(rows)}）')
    print(f'{"="*90}')
    
    data = []
    for r in rows:
        g = max_gain_5d(r['ts_code'], DATE)
        if g is None:
            continue
        data.append((r, g))
    
    data.sort(key=lambda x: -x[1])
    
    # 全样本统计
    gains = [g for _, g in data]
    print(f'有效样本: {len(gains)} | 均值 {st.mean(gains):+.2f}% | 中位 {st.median(gains):+.2f}% | 最高 {max(gains):+.1f}% | 最低 {min(gains):+.1f}%')
    print(f'正收益占比: {sum(1 for g in gains if g>0)/len(gains)*100:.1f}%')
    print()
    
    # Top 10 大牛股
    print('🔥 5日最高涨幅 TOP10（大牛股）:')
    print(f'{"排名":<4}{"股票":<10}{"V2":>6}{"事件":>6}{"预期差V2":>8}{"趋势":>6}{"机构":>6}{"行业热度":>8}{"乖离":>7}{"Buy":>6}{"5日最高":>8}')
    for i, (r, g) in enumerate(data[:10], 1):
        v2 = to_f(r.get('final_score_v2'))
        ev = to_f(r.get('event_quality_score'))
        ex = to_f(r.get('expectation_gap_v2_score'))
        tr = to_f(r.get('trend_score'))
        inst = to_f(r.get('institution_accumulation_score'))
        ind = to_f(r.get('industry_heat_score'))
        bias = to_f(r.get('bias_pct'))
        buy = to_f(r.get('buy_score'))
        print(f'{i:<4}{r["name"]:<10}{v2:>6.1f}{ev:>6.1f}{ex:>8.1f}{tr:>6.1f}{inst:>6.1f}{ind:>8.1f}{bias:>6.1f}%{buy:>6.1f}{g:>7.1f}%')
    
    print()
    # Bottom 5
    print('💀 5日最高涨幅 BOTTOM5（大熊股）:')
    for i, (r, g) in enumerate(data[-5:], 1):
        v2 = to_f(r.get('final_score_v2'))
        print(f'  {r["name"]:<10} V2:{v2:.1f}  5日最高:{g:+.1f}%')
    
    print()
    # V2 十分位分组收益
    print('📊 V2 十分位分组（V2高→低，每组平均5日最高）:')
    n = len(data)
    bins = 5
    per = n // bins
    for b in range(bins):
        group = data[b*per:(b+1)*per if b < bins-1 else n]
        gg = [g for _, g in group]
        vv = [to_f(r.get('final_score_v2')) for r, _ in group]
        print(f'  第{b+1}组 (V2 {min(vv):.0f}~{max(vv):.0f}) n={len(gg)} 均值{st.mean(gg):+.2f}% 中位{st.median(gg):+.2f}% 正收益{sum(1 for x in gg if x>0)/len(gg)*100:.0f}%')
    
    print()
    # 按因子分组：看哪些因子能区分牛熊
    print('🧪 因子分组（中位数分割，比较上半组 vs 下半组 5日最高均值）:')
    factors = [
        ('event_quality_score', '事件质量'),
        ('expectation_gap_v2_score', '预期差V2'),
        ('trend_score', '趋势Alpha'),
        ('institution_accumulation_score', '机构吸筹'),
        ('industry_heat_score', '行业热度'),
        ('bias_pct', '乖离率'),
        ('buy_score', 'Buy Score'),
        ('buy_quality_score', '买点质量'),
    ]
    for key, name in factors:
        vals = [(to_f(r.get(key)), g) for r, g in data]
        vals_sorted = sorted(vals, key=lambda x: x[0])
        mid = len(vals_sorted) // 2
        low = [g for _, g in vals_sorted[:mid]]
        high = [g for _, g in vals_sorted[mid:]]
        print(f'  {name:<12} 下半组(低){st.mean(low):+.2f}%  vs  上半组(高){st.mean(high):+.2f}%  差值{st.mean(high)-st.mean(low):+.2f}%')
