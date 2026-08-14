# -*- coding: utf-8 -*-
"""ELD V2 优化分析 v2：只看有真实评分的预增样本（event_quality>0），剔除零分票"""
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

def to_f(x, d=0.0):
    try:
        return float(x or d)
    except:
        return d

def load_eld(date):
    p = rf'D:\mystock\report_daily\eld_report_{date}.csv'
    with open(p, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return rows

for DATE in ['20260804', '20260806']:
    rows = load_eld(DATE)
    print(f'\n{"="*90}')
    print(f'  {DATE}  预增样本内分析（event>0，n≈?）')
    print(f'{"="*90}')
    
    data = []
    for r in rows:
        if to_f(r.get('event_quality_score')) <= 0:
            continue  # 剔除非预增零分票
        g = max_gain_5d(r['ts_code'], DATE)
        if g is None:
            continue
        data.append((r, g))
    
    data.sort(key=lambda x: -x[1])
    gains = [g for _, g in data]
    
    print(f'预增样本: {len(gains)} | 均值 {st.mean(gains):+.2f}% | 中位 {st.median(gains):+.2f}% | 最高 {max(gains):+.1f}% | 最低 {min(gains):+.1f}%')
    print(f'正收益占比: {sum(1 for g in gains if g>0)/len(gains)*100:.1f}%')
    print()
    
    # Top 10 大牛股（预增样本内）
    print('🔥 5日最高涨幅 TOP10（预增样本内）:')
    print(f'{"排名":<4}{"股票":<10}{"V2":>6}{"事件":>6}{"预期差V2":>8}{"趋势":>6}{"机构":>6}{"行业":>6}{"乖离":>7}{"Buy":>6}{"质量":>6}{"连板":>4}{"5日最高":>8}')
    for i, (r, g) in enumerate(data[:10], 1):
        v2 = to_f(r.get('final_score_v2'))
        ev = to_f(r.get('event_quality_score'))
        ex = to_f(r.get('expectation_gap_v2_score'))
        tr = to_f(r.get('trend_score'))
        inst = to_f(r.get('institution_accumulation_score'))
        ind = to_f(r.get('industry_heat_score'))
        bias = to_f(r.get('bias_pct'))
        buy = to_f(r.get('buy_score'))
        qual = to_f(r.get('buy_quality_score'))
        cons = int(to_f(r.get('buy_cons_count')))
        print(f'{i:<4}{r["name"]:<10}{v2:>6.1f}{ev:>6.1f}{ex:>8.1f}{tr:>6.1f}{inst:>6.1f}{ind:>6.1f}{bias:>6.1f}%{buy:>6.1f}{qual:>6.1f}{cons:>4}{g:>7.1f}%')
    
    print()
    # Bottom 5
    print('💀 BOTTOM5:')
    for i, (r, g) in enumerate(data[-5:], 1):
        v2 = to_f(r.get('final_score_v2'))
        print(f'  {r["name"]:<10} V2:{v2:.1f} 5日最高:{g:+.1f}%')
    
    print()
    # V2 五分位（预增样本内）
    print('📊 V2 五分位排序（高→低）:')
    n = len(data)
    bins = 5
    per = n // bins
    for b in range(bins):
        group = data[b*per:(b+1)*per if b < bins-1 else n]
        gg = [g for _, g in group]
        vv = [to_f(r.get('final_score_v2')) for r, _ in group]
        print(f'  Q{b+1} (V2 {min(vv):.0f}~{max(vv):.0f}) n={len(gg)} 均值{st.mean(gg):+.2f}% 中位{st.median(gg):+.2f}% 正收益{sum(1 for x in gg if x>0)/len(gg)*100:.0f}%')
    
    print()
    # 因子分组
    print('🧪 因子分组（中位数分割，上半组-下半组）:')
    factors = [
        ('final_score_v2', 'V2总分'),
        ('event_quality_score', '事件质量'),
        ('expectation_gap_v2_score', '预期差V2'),
        ('trend_score', '趋势Alpha'),
        ('institution_accumulation_score', '机构吸筹'),
        ('industry_heat_score', '行业热度'),
        ('bias_pct', '乖离率'),
        ('buy_score', 'Buy Score'),
        ('buy_quality_score', '买点质量'),
        ('volume_ratio', '量比'),
        ('forecast_pct', '预增幅度'),
    ]
    for key, name in factors:
        vals = [(to_f(r.get(key)), g) for r, g in data]
        vals_sorted = sorted(vals, key=lambda x: x[0])
        mid = len(vals_sorted) // 2
        low = [g for _, g in vals_sorted[:mid]]
        high = [g for _, g in vals_sorted[mid:]]
        diff = st.mean(high) - st.mean(low)
        sign = '📈正向' if diff > 0.5 else ('📉反向' if diff < -0.5 else '➖不显著')
        print(f'  {name:<14} 低{st.mean(low):+.2f}%  vs  高{st.mean(high):+.2f}%  差{diff:+.2f}% {sign}')
    
    # 公告后天数分组
    print()
    print('📅 公告后天数分布（5-12日窗口内 vs 其他）:')
    in_win = [(r, g) for r, g in data if 5 <= int(to_f(r.get('days_since_ann'))) <= 12]
    pre_win = [(r, g) for r, g in data if 0 < int(to_f(r.get('days_since_ann'))) < 5]
    post_win = [(r, g) for r, g in data if int(to_f(r.get('days_since_ann'))) > 12]
    for label, grp in [('窗口前1-4日', pre_win), ('窗口内5-12日', in_win), ('窗口后13+日', post_win)]:
        if not grp:
            print(f'  {label}: 0样本')
            continue
        gg = [g for _, g in grp]
        print(f'  {label}: n={len(gg)} 均值{st.mean(gg):+.2f}% 中位{st.median(gg):+.2f}% 正收益{sum(1 for x in gg if x>0)/len(gg)*100:.0f}%')
    
    # 机构状态分组
    print()
    print('🏢 机构状态分组:')
    from collections import defaultdict
    grp_by = defaultdict(list)
    for r, g in data:
        grp_by[r.get('institution_state', '未知')].append(g)
    for k, v in sorted(grp_by.items(), key=lambda kv: -st.mean(kv[1])):
        print(f'  {k}: n={len(v)} 均值{st.mean(v):+.2f}% 中位{st.median(v):+.2f}%')
    
    # 买点类型分组
    print()
    print('🎯 买点类型分组:')
    grp_by = defaultdict(list)
    for r, g in data:
        t = r.get('buy_point_type') or 'NONE'
        grp_by[t].append(g)
    for k, v in sorted(grp_by.items(), key=lambda kv: -st.mean(kv[1])):
        if len(v) < 5:
            continue
        print(f'  {k:<16}: n={len(v):>3} 均值{st.mean(v):+.2f}% 中位{st.median(v):+.2f}%')
