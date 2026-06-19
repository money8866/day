#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v4扫描结果 → 基本面深度筛选 v2
放宽标准，同时标注业绩质量等级

筛选逻辑：
- 宽口径：净利润增速>15%, 营收增速>5%, ROE>5%
- 严口径：净利润增速>30%, 营收增速>15%, ROE>10%, PE<50
- 排除一次性收益（环比暴跌的排除）
- 行业景气度标注
"""

import json
import sys
import os
import time
import statistics

sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

INPUT_FILE = r'D:\mystock\solo\report_daily\mainboard_v4_scan_20260618.json'

with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

stocks = data['data']
codes = [s['ts_code'] for s in stocks]
print(f"加载 {len(stocks)} 只候选股票")

# =========== Step 1: 拉取财报数据 ===========
print("\n===== 拉取财报数据 =====")

all_financials = {}
all_indicators = {}
all_valuation = {}
errors = {'income': 0, 'indicator': 0, 'valuation': 0}

for i, code in enumerate(codes):
    # income
    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False).head(8)  # 多取一些给同比用
            all_financials[code] = df
        time.sleep(0.055)
    except:
        errors['income'] += 1
        time.sleep(0.1)
    
    # fina_indicator
    try:
        df = pro.fina_indicator(ts_code=code, fields='ts_code,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,eps')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False).head(4)
            all_indicators[code] = df
        time.sleep(0.055)
    except:
        errors['indicator'] += 1
        time.sleep(0.1)
    
    # daily_basic - 逐个日期尝试
    for dt in ['20260618','20260617','20260616','20260613','20260612']:
        try:
            df = pro.daily_basic(ts_code=code, trade_date=dt, fields='ts_code,trade_date,pe,pb,ps,dv_ratio')
            if df is not None and len(df) > 0:
                all_valuation[code] = df.iloc[0].to_dict()
                break
        except:
            pass
    time.sleep(0.055)
    
    if (i+1) % 30 == 0:
        print(f"  已处理 {i+1}/{len(codes)} | income:{len(all_financials)} indicator:{len(all_indicators)} valuation:{len(all_valuation)} | err:{errors}")

print(f"\n数据加载完成: income:{len(all_financials)} indicator:{len(all_indicators)} valuation:{len(all_valuation)}")

# =========== Step 2: 计算业绩指标 ===========
print("\n===== 计算业绩增长 =====")

results = []

for stock in stocks:
    code = stock['ts_code']
    income_data = all_financials.get(code)
    indicator_data = all_indicators.get(code)
    valuation = all_valuation.get(code)
    
    if income_data is None or len(income_data) < 2:
        continue
    
    info = {
        'ts_code': code,
        'name': stock['name'],
        'theme': stock.get('theme', ''),
        'ultimate_score': stock.get('ultimate_score', 0),
        'rating': stock.get('rating', ''),
        'bias_ma20': stock.get('bias_ma20', 0),
        'ret_60': stock.get('ret_60', 0),
        'ret_120': stock.get('ret_120', 0),
        'market_cap_yi': stock.get('market_cap_yi', 0),
        'stage': stock.get('stage', ''),
        'score_a': stock.get('score_a_core', 0),
        'score_b': stock.get('score_b_recognition', 0),
        'score_c': stock.get('score_c_value_health', 0),
        'score_d': stock.get('score_d_trend', 0),
    }
    
    rows = income_data.to_dict('records')
    if len(rows) >= 2:
        latest = rows[0]
        prev = rows[1]
        
        # 找去年同期
        latest_end = latest.get('end_date', '')
        yoy_end = ''
        if len(latest_end) == 8:
            yoy_end = str(int(latest_end[:4]) - 1) + latest_end[4:]
        
        yoy = None
        for r in rows:
            if r.get('end_date') == yoy_end:
                yoy = r
                break
        
        # 营收同比
        info['rev_yoy'] = None
        if yoy is not None and yoy.get('total_revenue') and latest.get('total_revenue'):
            try:
                rev_yoy = (latest['total_revenue'] - yoy['total_revenue']) / abs(yoy['total_revenue']) * 100
                info['rev_yoy'] = round(rev_yoy, 1)
            except:
                pass
        
        # 净利润同比
        info['np_yoy'] = None
        if yoy is not None and yoy.get('n_income_attr_p') and latest.get('n_income_attr_p'):
            try:
                np_yoy = (latest['n_income_attr_p'] - yoy['n_income_attr_p']) / abs(yoy['n_income_attr_p']) * 100
                info['np_yoy'] = round(np_yoy, 1)
            except:
                pass
        
        # 净利润环比（排除一次性收益）
        info['np_qoq'] = None
        if prev.get('n_income_attr_p') and latest.get('n_income_attr_p'):
            try:
                np_qoq = (latest['n_income_attr_p'] - prev['n_income_attr_p']) / abs(prev['n_income_attr_p']) * 100
                info['np_qoq'] = round(np_qoq, 1)
            except:
                pass
        
        info['latest_period'] = latest.get('end_date', '')
        info['latest_revenue_yi'] = round(latest.get('total_revenue', 0) / 1e8, 2) if latest.get('total_revenue') else None
        info['latest_nincome_yi'] = round(latest.get('n_income_attr_p', 0) / 1e8, 2) if latest.get('n_income_attr_p') else None
        
        # 一次性收益检测：净利润环比暴跌>80%说明上期可能有处置收益
        info['one_time_suspect'] = False
        if info.get('np_qoq') is not None and info['np_qoq'] < -80:
            info['one_time_suspect'] = True
        # 净利润同比增速>500%但营收增速<50%，可能一次性收益
        if info.get('np_yoy') is not None and info.get('rev_yoy') is not None:
            if info['np_yoy'] > 500 and info['rev_yoy'] < 50:
                info['one_time_suspect'] = True
    
    # 财务指标
    if indicator_data is not None and len(indicator_data) > 0:
        ind = indicator_data.iloc[0]
        info['roe'] = ind.get('roe')
        info['roe_waa'] = ind.get('roe_waa')
        info['gross_margin'] = ind.get('grossprofit_margin')
        info['net_margin'] = ind.get('netprofit_margin')
    
    # 估值
    if valuation is not None:
        info['pe'] = valuation.get('pe')
        info['pb'] = valuation.get('pb')
        info['ps'] = valuation.get('ps')
        info['dv_ratio'] = valuation.get('dv_ratio')
    
    results.append(info)

print(f"成功计算 {len(results)} 只股票的业绩数据")

# =========== Step 3: 行业景气度 ===========
print("\n===== 行业景气度 =====")

theme_stats = {}
for r in results:
    theme = r.get('theme', '其他')
    if theme not in theme_stats:
        theme_stats[theme] = {'np_yoy': [], 'rev_yoy': [], 'count': 0}
    
    if r.get('np_yoy') is not None and abs(r['np_yoy']) < 2000:  # 排除极端值
        theme_stats[theme]['np_yoy'].append(r['np_yoy'])
    if r.get('rev_yoy') is not None and abs(r['rev_yoy']) < 2000:
        theme_stats[theme]['rev_yoy'].append(r['rev_yoy'])
    theme_stats[theme]['count'] += 1

for theme, stats in theme_stats.items():
    stats['np_yoy_median'] = statistics.median(stats['np_yoy']) if len(stats['np_yoy']) >= 2 else None
    stats['rev_yoy_median'] = statistics.median(stats['rev_yoy']) if len(stats['rev_yoy']) >= 2 else None
    
    # 行业景气度评级
    if stats['np_yoy_median'] is not None:
        np_m = stats['np_yoy_median']
        rev_m = stats['rev_yoy_median'] or 0
        if np_m >= 50 and rev_m >= 20:
            stats['prosperity'] = '🔥高景气'
        elif np_m >= 20 and rev_m >= 10:
            stats['prosperity'] = '📈景气上行'
        elif np_m >= 5:
            stats['prosperity'] = '➡️平稳'
        elif np_m >= -10:
            stats['prosperity'] = '📉景气下行'
        else:
            stats['prosperity'] = '❌困境'
    else:
        stats['prosperity'] = '❓数据不足'

# 排名
theme_ranking = []
for theme, stats in theme_stats.items():
    if stats['count'] >= 3 and stats['np_yoy_median'] is not None:
        theme_ranking.append({
            'theme': theme,
            'count': stats['count'],
            'np_yoy_median': round(stats['np_yoy_median'], 1),
            'rev_yoy_median': round(stats['rev_yoy_median'], 1) if stats['rev_yoy_median'] else None,
            'prosperity': stats['prosperity']
        })

theme_ranking.sort(key=lambda x: x['np_yoy_median'], reverse=True)

print("\n行业景气度排名：")
for t in theme_ranking:
    print(f"  {t['prosperity']} {t['theme']}({t['count']}只): 净利中位数 {t['np_yoy_median']:+.1f}% | 营收中位数 {t['rev_yoy_median']:+.1f}%" if t['rev_yoy_median'] else f"  {t['prosperity']} {t['theme']}({t['count']}只): 净利中位数 {t['np_yoy_median']:+.1f}%")

# =========== Step 4: 分层筛选 ===========
print("\n===== 分层筛选 =====")

tier1 = []  # 优质成长：净利>30%, 营收>15%, ROE>10%, PE<50, 非一次性
tier2 = []  # 稳健成长：净利>15%, 营收>5%, ROE>5%, 非一次性
tier3 = []  # 业绩回暖：净利>0%, 营收>0%, 行业景气

for r in results:
    np_yoy = r.get('np_yoy')
    rev_yoy = r.get('rev_yoy')
    roe = r.get('roe_waa') or r.get('roe')
    pe = r.get('pe')
    theme = r.get('theme', '')
    one_time = r.get('one_time_suspect', False)
    
    if np_yoy is None or rev_yoy is None:
        continue
    
    r['theme_prosperity'] = theme_stats.get(theme, {}).get('prosperity', '❓')
    r['theme_np_median'] = theme_stats.get(theme, {}).get('np_yoy_median')
    r['theme_rev_median'] = theme_stats.get(theme, {}).get('rev_yoy_median')
    
    # Tier 1: 优质成长
    tiers = None
    if np_yoy > 30 and rev_yoy > 15 and (roe is None or roe > 10) and (pe is None or pe < 50) and not one_time:
        tiers = 'T1'
        tier1.append(r)
    elif np_yoy > 15 and rev_yoy > 5 and (roe is None or roe > 5) and not one_time:
        tiers = 'T2'
        tier2.append(r)
    elif np_yoy > 0 and rev_yoy > 0:
        tiers = 'T3'
        tier3.append(r)
    
    r['tier'] = tiers

# 排序
tier1.sort(key=lambda x: x.get('np_yoy', 0), reverse=True)
tier2.sort(key=lambda x: x.get('np_yoy', 0), reverse=True)
tier3.sort(key=lambda x: x.get('np_yoy', 0), reverse=True)

print(f"\n筛选结果:")
print(f"  T1 优质成长: {len(tier1)} 只")
print(f"  T2 稳健成长: {len(tier2)} 只")
print(f"  T3 业绩回暖: {len(tier3)} 只")

print(f"\n--- T1 优质成长 ---")
for r in tier1[:15]:
    pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else "PE=N/A"
    roe_str = f"ROE={r['roe_waa']:.1f}%" if r.get('roe_waa') else "ROE=N/A"
    print(f"  {r['name']}({r['ts_code']}): 净利 {r['np_yoy']:+.1f}% | 营收 {r['rev_yoy']:+.1f}% | {roe_str} | {pe_str} | {r['theme']} {r.get('theme_prosperity','')}")

print(f"\n--- T2 稳健成长 ---")
for r in tier2[:15]:
    pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else "PE=N/A"
    roe_str = f"ROE={r['roe_waa']:.1f}%" if r.get('roe_waa') else "ROE=N/A"
    print(f"  {r['name']}({r['ts_code']}): 净利 {r['np_yoy']:+.1f}% | 营收 {r['rev_yoy']:+.1f}% | {roe_str} | {pe_str} | {r['theme']} {r.get('theme_prosperity','')}")

# =========== Step 5: 保存 ===========
output_file = r'D:\mystock\solo\report_daily\fundamental_screen_20260618.json'
output = {
    'scan_date': '2026-06-18',
    'method': '基于v4扫描的基本面深度筛选v2',
    'criteria': {
        'T1_优质成长': '净利>30%, 营收>15%, ROE>10%, PE<50, 非一次性收益',
        'T2_稳健成长': '净利>15%, 营收>5%, ROE>5%, 非一次性收益',
        'T3_业绩回暖': '净利>0%, 营收>0%'
    },
    'total_v4_stocks': len(stocks),
    'T1_count': len(tier1),
    'T2_count': len(tier2),
    'T3_count': len(tier3),
    'theme_ranking': theme_ranking,
    'T1_data': tier1,
    'T2_data': tier2,
    'T3_data': tier3
}

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n结果已保存: {output_file}")
print(f"文件大小: {os.path.getsize(output_file)} 字节")
