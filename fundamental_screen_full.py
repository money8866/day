#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场基本面筛选 — 机构增量视角
覆盖v4扫描全部569只主板股票

机构增量资金的核心逻辑：
1. 业绩确定性 > 弹性（机构买的是可验证的增长，不是故事）
2. 行业景气共振 > 个股独立行情（机构按行业配置，不是按个股）
3. 估值合理性 > 绝对低估值（PE<60但增长>30%，PEG<2优先）
4. 流动性门槛 > 小盘股（日均成交额>3亿，市值>100亿）
5. 北向资金偏好 > ROE稳定 + 分红
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

# 全部569只股票
stocks = data['data']
codes = [s['ts_code'] for s in stocks]
print(f"加载全部 {len(stocks)} 只候选股票")

# =========== Step 1: 拉取财报数据 ===========
print("\n===== 拉取财报数据（3类接口 × 569只，预计20-30分钟）=====")

all_financials = {}
all_indicators = {}
all_valuation = {}
all_amt = {}  # 日均成交额
errors = {'income': 0, 'indicator': 0, 'valuation': 0}

for i, code in enumerate(codes):
    # income
    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,total_revenue,n_income_attr_p', period_type='1')
        if df is not None and len(df) > 0:
            df = df.sort_values('end_date', ascending=False).head(8)
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
    
    # daily_basic
    for dt in ['20260618','20260617','20260616','20260613','20260612']:
        try:
            df = pro.daily_basic(ts_code=code, trade_date=dt, fields='ts_code,trade_date,pe,pb,ps,dv_ratio,turnover_rate')
            if df is not None and len(df) > 0:
                all_valuation[code] = df.iloc[0].to_dict()
                break
        except:
            pass
    time.sleep(0.055)
    
    # 成交额 - 从v4数据中直接取
    stock_info = stocks[i]
    all_amt[code] = stock_info.get('avg_amount_20d_yi', 0)
    
    if (i+1) % 50 == 0:
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
        'avg_amount_20d_yi': stock.get('avg_amount_20d_yi', 0),
        'amount_ratio': stock.get('amount_ratio', 1),
        'stage': stock.get('stage', ''),
        'score_a': stock.get('score_a_core', 0),
        'score_b': stock.get('score_b_recognition', 0),
        'score_c': stock.get('score_c_value_health', 0),
        'score_d': stock.get('score_d_trend', 0),
        'bull_score': stock.get('bull_score', 0),
        'turnover_rate': valuation.get('turnover_rate') if valuation else None,
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
                info['rev_yoy'] = round((latest['total_revenue'] - yoy['total_revenue']) / abs(yoy['total_revenue']) * 100, 1)
            except:
                pass
        
        # 净利润同比
        info['np_yoy'] = None
        if yoy is not None and yoy.get('n_income_attr_p') and latest.get('n_income_attr_p'):
            try:
                info['np_yoy'] = round((latest['n_income_attr_p'] - yoy['n_income_attr_p']) / abs(yoy['n_income_attr_p']) * 100, 1)
            except:
                pass
        
        # 净利润环比
        info['np_qoq'] = None
        if prev.get('n_income_attr_p') and latest.get('n_income_attr_p'):
            try:
                info['np_qoq'] = round((latest['n_income_attr_p'] - prev['n_income_attr_p']) / abs(prev['n_income_attr_p']) * 100, 1)
            except:
                pass
        
        # 营收环比
        info['rev_qoq'] = None
        if prev.get('total_revenue') and latest.get('total_revenue'):
            try:
                info['rev_qoq'] = round((latest['total_revenue'] - prev['total_revenue']) / abs(prev['total_revenue']) * 100, 1)
            except:
                pass
        
        info['latest_period'] = latest.get('end_date', '')
        info['latest_revenue_yi'] = round(latest.get('total_revenue', 0) / 1e8, 2) if latest.get('total_revenue') else None
        info['latest_nincome_yi'] = round(latest.get('n_income_attr_p', 0) / 1e8, 2) if latest.get('n_income_attr_p') else None
        
        # 一次性收益检测
        info['one_time_suspect'] = False
        if info.get('np_qoq') is not None and info['np_qoq'] < -80:
            info['one_time_suspect'] = True
        if info.get('np_yoy') is not None and info.get('rev_yoy') is not None:
            if info['np_yoy'] > 500 and info['rev_yoy'] < 50:
                info['one_time_suspect'] = True
        
        # 业绩连续性：最近2期是否都正增长
        info['consecutive_growth'] = False
        if len(rows) >= 3:
            r0_rev = rows[0].get('total_revenue', 0) or 0
            r1_rev = rows[1].get('total_revenue', 0) or 0
            r2_rev = rows[2].get('total_revenue', 0) or 0
            if r0_rev > r1_rev > r2_rev and r0_rev > 0:
                info['consecutive_growth'] = True
    
    # 财务指标
    if indicator_data is not None and len(indicator_data) > 0:
        ind = indicator_data.iloc[0]
        info['roe_latest'] = ind.get('roe')
        info['roe_waa'] = ind.get('roe_waa')
        info['gross_margin'] = ind.get('grossprofit_margin')
        info['net_margin'] = ind.get('netprofit_margin')
        info['eps'] = ind.get('eps')
        
        # ROE稳定性：检查最近4期ROE的方差
        if len(indicator_data) >= 3:
            roe_list = [r.get('roe') for r in indicator_data.to_dict('records') if r.get('roe') is not None]
            if len(roe_list) >= 3:
                info['roe_stable'] = statistics.stdev(roe_list) < 15  # 标准差<15算稳定
            else:
                info['roe_stable'] = None
        else:
            info['roe_stable'] = None
    
    # 估值
    if valuation is not None:
        info['pe'] = valuation.get('pe')
        info['pb'] = valuation.get('pb')
        info['ps'] = valuation.get('ps')
        info['dv_ratio'] = valuation.get('dv_ratio')
    
    # PEG计算
    if info.get('pe') and info.get('np_yoy') and info['np_yoy'] > 0:
        info['peg'] = round(info['pe'] / info['np_yoy'], 2)
    else:
        info['peg'] = None
    
    results.append(info)

print(f"成功计算 {len(results)} 只股票的业绩数据")

# =========== Step 3: 行业景气度 ===========
print("\n===== 行业景气度（机构视角）=====")

theme_stats = {}
for r in results:
    theme = r.get('theme', '其他')
    if theme not in theme_stats:
        theme_stats[theme] = {'np_yoy': [], 'rev_yoy': [], 'count': 0, 'caps': [], 'amounts': []}
    
    if r.get('np_yoy') is not None and abs(r['np_yoy']) < 3000:
        theme_stats[theme]['np_yoy'].append(r['np_yoy'])
    if r.get('rev_yoy') is not None and abs(r['rev_yoy']) < 3000:
        theme_stats[theme]['rev_yoy'].append(r['rev_yoy'])
    if r.get('market_cap_yi'):
        theme_stats[theme]['caps'].append(r['market_cap_yi'])
    if r.get('avg_amount_20d_yi'):
        theme_stats[theme]['amounts'].append(r['avg_amount_20d_yi'])
    theme_stats[theme]['count'] += 1

for theme, stats in theme_stats.items():
    stats['np_yoy_median'] = statistics.median(stats['np_yoy']) if len(stats['np_yoy']) >= 2 else None
    stats['rev_yoy_median'] = statistics.median(stats['rev_yoy']) if len(stats['rev_yoy']) >= 2 else None
    stats['avg_cap'] = statistics.mean(stats['caps']) if stats['caps'] else 0
    stats['avg_amount'] = statistics.mean(stats['amounts']) if stats['amounts'] else 0
    
    # 机构景气度评级
    np_m = stats['np_yoy_median'] or 0
    rev_m = stats['rev_yoy_median'] or 0
    cnt = stats['count']
    
    if cnt >= 5 and np_m >= 30 and rev_m >= 15:
        stats['prosperity'] = '🔥高景气'
        stats['inst_score'] = 90
    elif cnt >= 3 and np_m >= 15 and rev_m >= 8:
        stats['prosperity'] = '📈景气上行'
        stats['inst_score'] = 70
    elif cnt >= 3 and np_m >= 5:
        stats['prosperity'] = '➡️平稳'
        stats['inst_score'] = 50
    elif np_m >= -5:
        stats['prosperity'] = '📉景气下行'
        stats['inst_score'] = 30
    else:
        stats['prosperity'] = '❌困境'
        stats['inst_score'] = 10
    
    # 板块容量（机构是否配得进来）
    if stats['avg_cap'] >= 300 and stats['avg_amount'] >= 10:
        stats['capacity'] = '✅大容量'
    elif stats['avg_cap'] >= 100 and stats['avg_amount'] >= 3:
        stats['capacity'] = '⚠️中容量'
    else:
        stats['capacity'] = '❌小容量'

# 排名
theme_ranking = []
for theme, stats in theme_stats.items():
    if stats['count'] >= 3 and stats['np_yoy_median'] is not None:
        theme_ranking.append({
            'ranking': 0,
            'theme': theme,
            'count': stats['count'],
            'np_yoy_median': round(stats['np_yoy_median'], 1),
            'rev_yoy_median': round(stats['rev_yoy_median'], 1) if stats['rev_yoy_median'] else None,
            'prosperity': stats['prosperity'],
            'inst_score': stats['inst_score'],
            'avg_cap': round(stats['avg_cap'], 0),
            'avg_amount': round(stats['avg_amount'], 1),
            'capacity': stats['capacity'],
        })

theme_ranking.sort(key=lambda x: (x['inst_score'], x['np_yoy_median']), reverse=True)
for i, t in enumerate(theme_ranking, 1):
    t['ranking'] = i

print(f"\n行业景气度排名（{len(theme_ranking)}个行业，口径≥3只）：")
for t in theme_ranking[:20]:
    rev_str = f"营收+{t['rev_yoy_median']:.0f}%" if t.get('rev_yoy_median') else ""
    print(f"  {t['prosperity']} {t['theme']}({t['count']}只): 净利+{t['np_yoy_median']:.0f}% | {rev_str} | {t['capacity']} 均市值{t['avg_cap']:.0f}亿 均成交{t['avg_amount']:.1f}亿")

# =========== Step 4: 机构增量视角分层筛选 ===========
print("\n===== 机构增量视角筛选 =====")

tier_inst_a = []  # 机构核心池：高景气+业绩确定+流动性好+估值合理
tier_inst_b = []  # 机构观察池：景气上行+业绩增长
tier_inst_c = []  # 机构跟踪池：业绩回暖

for r in results:
    np_yoy = r.get('np_yoy')
    rev_yoy = r.get('rev_yoy')
    roe = r.get('roe_waa') or r.get('roe_latest')
    pe = r.get('pe')
    peg = r.get('peg')
    dv = r.get('dv_ratio')
    market_cap = r.get('market_cap_yi', 0)
    amt = r.get('avg_amount_20d_yi', 0)
    theme = r.get('theme', '')
    one_time = r.get('one_time_suspect', False)
    consecutive = r.get('consecutive_growth', False)
    roe_stable = r.get('roe_stable')
    
    if np_yoy is None or rev_yoy is None:
        continue
    
    # 行业信息
    t_stats = theme_stats.get(theme, {})
    prosperity = t_stats.get('prosperity', '❓')
    inst_score = t_stats.get('inst_score', 0)
    capacity = t_stats.get('capacity', '❌小容量')
    
    r['theme_prosperity'] = prosperity
    r['theme_inst_score'] = inst_score
    r['theme_capacity'] = capacity
    r['theme_np_median'] = t_stats.get('np_yoy_median')
    r['theme_rev_median'] = t_stats.get('rev_yoy_median')
    
    # ===== 机构核心池 =====
    # 条件：高景气行业 + 净利>30% + 营收>15% + ROE>8% + 流动性够 + 非一次性 + PEG<2优先
    if (inst_score >= 70 and np_yoy > 30 and rev_yoy > 15 and 
        (roe is None or roe > 8) and market_cap >= 100 and amt >= 3 and 
        not one_time):
        r['tier'] = 'IA'
        r['tier_desc'] = '机构核心池'
        # 机构评分
        inst_personal_score = 60  # 基础分
        inst_personal_score += min(np_yoy / 5, 15)  # 净利增速加分（上限15）
        inst_personal_score += min(rev_yoy / 5, 10)  # 营收增速加分
        if roe and roe > 15: inst_personal_score += 5  # 高ROE加分
        if consecutive: inst_personal_score += 5  # 连续增长加分
        if roe_stable: inst_personal_score += 3  # ROE稳定加分
        if peg and peg < 1: inst_personal_score += 5  # PEG<1加分
        elif peg and peg < 2: inst_personal_score += 2
        if dv and dv > 2: inst_personal_score += 2  # 有分红加分
        if pe and pe < 30: inst_personal_score += 3  # 低PE加分
        r['inst_personal_score'] = round(min(inst_personal_score, 100), 1)
        tier_inst_a.append(r)
    
    # ===== 机构观察池 =====
    # 中景气 + 净利>15% + 营收>5% + 非一次性
    elif (inst_score >= 50 and np_yoy > 15 and rev_yoy > 5 and not one_time):
        r['tier'] = 'IB'
        r['tier_desc'] = '机构观察池'
        r['inst_personal_score'] = round(40 + min(np_yoy / 10, 15) + min(rev_yoy / 5, 10), 1)
        tier_inst_b.append(r)
    
    # ===== 机构跟踪池 =====
    elif np_yoy > 0 and rev_yoy > 0:
        r['tier'] = 'IC'
        r['tier_desc'] = '机构跟踪池'
        r['inst_personal_score'] = round(20 + min(np_yoy / 15, 10) + min(rev_yoy / 10, 5), 1)
        tier_inst_c.append(r)

# 排序
tier_inst_a.sort(key=lambda x: x.get('inst_personal_score', 0), reverse=True)
tier_inst_b.sort(key=lambda x: x.get('inst_personal_score', 0), reverse=True)
tier_inst_c.sort(key=lambda x: x.get('np_yoy', 0), reverse=True)

print(f"\n筛选结果:")
print(f"  IA 机构核心池: {len(tier_inst_a)} 只")
print(f"  IB 机构观察池: {len(tier_inst_b)} 只")
print(f"  IC 机构跟踪池: {len(tier_inst_c)} 只")

print(f"\n--- IA 机构核心池（确定性+流动性+景气度）---")
for r in tier_inst_a[:20]:
    pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else "PE=N/A"
    peg_str = f"PEG={r['peg']:.1f}" if r.get('peg') else ""
    roe_str = f"ROE={r['roe_waa']:.1f}%" if r.get('roe_waa') else "ROE=N/A"
    dv_str = f"股息={r['dv_ratio']:.1f}%" if r.get('dv_ratio') else ""
    cg = "🔄连续" if r.get('consecutive_growth') else ""
    print(f"  [{r['inst_personal_score']:.0f}分] {r['name']}({r['ts_code'][:6]}): 净利+{r['np_yoy']:.0f}% | 营收+{r['rev_yoy']:.0f}% | {roe_str} | {pe_str} {peg_str} | 市值{r['market_cap_yi']:.0f}亿 | 成交{r['avg_amount_20d_yi']:.1f}亿 | {r['theme']} {r['theme_prosperity']} {r['theme_capacity']} {dv_str} {cg}")

print(f"\n--- IB 机构观察池 ---")
for r in tier_inst_b[:15]:
    pe_str = f"PE={r['pe']:.0f}" if r.get('pe') else "PE=N/A"
    print(f"  [{r['inst_personal_score']:.0f}分] {r['name']}({r['ts_code'][:6]}): 净利+{r['np_yoy']:.0f}% | 营收+{r['rev_yoy']:.0f}% | {pe_str} | {r['theme']} {r['theme_prosperity']}")

# =========== Step 5: 保存 ===========
output_file = r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json'
output = {
    'scan_date': '2026-06-18',
    'method': '机构增量视角基本面筛选（覆盖全市场569只）',
    'criteria': {
        'IA_机构核心池': '高景气行业(inst_score≥70) + 净利>30% + 营收>15% + ROE>8% + 市值≥100亿 + 日均成交≥3亿 + 非一次性收益',
        'IB_机构观察池': '景气行业(inst_score≥50) + 净利>15% + 营收>5% + 非一次性收益',
        'IC_机构跟踪池': '净利>0% + 营收>0%'
    },
    'total_scanned': len(stocks),
    'IA_count': len(tier_inst_a),
    'IB_count': len(tier_inst_b),
    'IC_count': len(tier_inst_c),
    'theme_ranking': theme_ranking,
    'IA_data': tier_inst_a,
    'IB_data': tier_inst_b,
    'IC_data': tier_inst_c
}

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n结果已保存: {output_file}")
print(f"文件大小: {os.path.getsize(output_file)} 字节")
