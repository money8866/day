#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v4扫描结果 → 基本面深度筛选
找出行业景气度高、个股业绩实质性增长的标的

筛选逻辑：
1. 从v4扫描TOP150中取候选池
2. Tushare拉取最新财报数据：
   - 营收增速（同比/环比）
   - 净利润增速（同比/环比）
   - ROE
   - 毛利率
   - PE/PB
3. 行业景气度：同行业公司业绩集体改善
4. 最终输出：业绩驱动的真正价值股
"""

import json
import sys
import os
import time

# Tushare
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# 加载v4扫描结果
INPUT_FILE = r'D:\mystock\solo\report_daily\mainboard_v4_scan_20260618.json'

with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

stocks = data['data']
print(f"加载 {len(stocks)} 只候选股票")

# =========== Step 1: 提取股票代码 ===========
codes = [s['ts_code'] for s in stocks]

# =========== Step 2: 拉取财报数据 ===========
# 最近4个季度的财务指标
print("\n===== 拉取财务指标 =====")

# 用income接口拉营收和净利润
all_financials = {}

batch_size = 20
for i in range(0, len(codes), batch_size):
    batch = codes[i:i+batch_size]
    for code in batch:
        try:
            # 最近3期财报
            df = pro.income(ts_code=code, fields='ts_code,ann_date,f_ann_date,end_date,total_revenue,revenue,n_income,n_income_attr_p,operate_profit')
            if df is not None and len(df) > 0:
                # 只取最近4期季报
                df = df.sort_values('end_date', ascending=False).head(6)  # 多取几期以确保同比有数据
                all_financials[code] = df
            time.sleep(0.06)
        except Exception as e:
            print(f"  拉取 {code} income 失败: {e}")
            time.sleep(0.1)
    
    print(f"  已处理 {min(i+batch_size, len(codes))}/{len(codes)} 只股票")

print(f"\n成功拉取 {len(all_financials)} 只股票的财报数据")

# =========== Step 3: 拉取财务指标（ROE/毛利率/PE/PB）==========
print("\n===== 拉取财务指标(Fina_indicator) =====")

all_indicators = {}
for i in range(0, len(codes), batch_size):
    batch = codes[i:i+batch_size]
    for code in batch:
        try:
            df = pro.fina_indicator(ts_code=code, fields='ts_code,ann_date,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,eps,dt_eps,bps,cfps')
            if df is not None and len(df) > 0:
                df = df.sort_values('end_date', ascending=False).head(4)
                all_indicators[code] = df
            time.sleep(0.06)
        except Exception as e:
            print(f"  拉取 {code} fina_indicator 失败: {e}")
            time.sleep(0.1)
    
    print(f"  已处理 {min(i+batch_size, len(codes))}/{len(codes)} 只股票")

print(f"\n成功拉取 {len(all_indicators)} 只股票的财务指标")

# =========== Step 4: 拉取估值数据 ===========
print("\n===== 拉取估值数据(daily_basic) =====")

# 用最近一个交易日的daily_basic
all_valuation = {}
for i in range(0, len(codes)):
    code = codes[i]
    for dt in ['20260618','20260617','20260616','20260613','20260612']:
        try:
            df = pro.daily_basic(ts_code=code, trade_date=dt,
                                fields='ts_code,trade_date,pe,pb,ps,dv_ratio,total_mv,circ_mv')
            if df is not None and len(df) > 0:
                all_valuation[code] = df.iloc[0].to_dict()
                break
        except:
            pass
    time.sleep(0.06)
    
    if (i+1) % 20 == 0:
        print(f"  已处理 {i+1}/{len(codes)} 只股票")

print(f"\n成功拉取 {len(all_valuation)} 只股票的估值数据")

# =========== Step 5: 计算业绩增长 ===========
print("\n===== 计算业绩增长 =====")

results = []

for stock in stocks:
    code = stock['ts_code']
    
    # 获取财报数据
    income_data = all_financials.get(code)
    indicator_data = all_indicators.get(code)
    valuation = all_valuation.get(code)
    
    if income_data is None or len(income_data) == 0:
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
    
    # 最近财报数据
    rows = income_data.to_dict('records')
    if len(rows) >= 2:
        latest = rows[0]  # 最新期
        prev = rows[1]    # 上期
        
        # 找去年同期（end_date差1年）
        latest_end = latest.get('end_date', '')
        yoy_end = ''
        if len(latest_end) == 8:
            yoy_end = str(int(latest_end[:4]) - 1) + latest_end[4:]  # 去年同期
        
        yoy = None
        for r in rows:
            if r.get('end_date') == yoy_end:
                yoy = r
                break
        
        # 营收同比增速
        if yoy is not None and yoy.get('total_revenue') and latest.get('total_revenue'):
            try:
                rev_yoy = (latest['total_revenue'] - yoy['total_revenue']) / abs(yoy['total_revenue']) * 100
                info['rev_yoy'] = round(rev_yoy, 2)
            except:
                info['rev_yoy'] = None
        else:
            info['rev_yoy'] = None
        
        # 净利润同比增速
        if yoy is not None and yoy.get('n_income_attr_p') and latest.get('n_income_attr_p'):
            try:
                np_yoy = (latest['n_income_attr_p'] - yoy['n_income_attr_p']) / abs(yoy['n_income_attr_p']) * 100
                info['np_yoy'] = round(np_yoy, 2)
            except:
                info['np_yoy'] = None
        else:
            info['np_yoy'] = None
        
        # 营收环比增速
        if prev.get('total_revenue') and latest.get('total_revenue'):
            try:
                rev_qoq = (latest['total_revenue'] - prev['total_revenue']) / abs(prev['total_revenue']) * 100
                info['rev_qoq'] = round(rev_qoq, 2)
            except:
                info['rev_qoq'] = None
        else:
            info['rev_qoq'] = None
        
        # 净利润环比增速
        if prev.get('n_income_attr_p') and latest.get('n_income_attr_p'):
            try:
                np_qoq = (latest['n_income_attr_p'] - prev['n_income_attr_p']) / abs(prev['n_income_attr_p']) * 100
                info['np_qoq'] = round(np_qoq, 2)
            except:
                info['np_qoq'] = None
        else:
            info['np_qoq'] = None
        
        # 最新财报期
        info['latest_period'] = latest.get('end_date', '')
        info['prev_period'] = prev.get('end_date', '')
        
        # 最新营收和净利润
        info['latest_revenue'] = latest.get('total_revenue')
        info['latest_nincome'] = latest.get('n_income_attr_p')
    
    # 财务指标
    if indicator_data is not None and len(indicator_data) > 0:
        ind = indicator_data.iloc[0]
        info['roe'] = ind.get('roe')
        info['roe_waa'] = ind.get('roe_waa')
        info['gross_margin'] = ind.get('grossprofit_margin')
        info['net_margin'] = ind.get('netprofit_margin')
        info['eps'] = ind.get('eps')
        info['bps'] = ind.get('bps')
    
    # 估值
    if valuation is not None:
        info['pe'] = valuation.get('pe')
        info['pb'] = valuation.get('pb')
        info['ps'] = valuation.get('ps')
        info['dv_ratio'] = valuation.get('dv_ratio')
    
    results.append(info)

print(f"\n成功计算 {len(results)} 只股票的业绩增长数据")

# =========== Step 6: 行业景气度分析 ===========
print("\n===== 行业景气度分析 =====")

# 按主题分组，统计行业内营收/利润增速中位数
theme_stats = {}
for r in results:
    theme = r.get('theme', '其他')
    if theme not in theme_stats:
        theme_stats[theme] = {'rev_yoy': [], 'np_yoy': [], 'count': 0}
    
    if r.get('rev_yoy') is not None and not (r['rev_yoy'] != r['rev_yoy']):  # NaN check
        theme_stats[theme]['rev_yoy'].append(r['rev_yoy'])
    if r.get('np_yoy') is not None and not (r['np_yoy'] != r['np_yoy']):
        theme_stats[theme]['np_yoy'].append(r['np_yoy'])
    theme_stats[theme]['count'] += 1

# 计算行业内中位数
import statistics
for theme, stats in theme_stats.items():
    if len(stats['rev_yoy']) >= 2:
        stats['rev_yoy_median'] = statistics.median(stats['rev_yoy'])
    else:
        stats['rev_yoy_median'] = None
    if len(stats['np_yoy']) >= 2:
        stats['np_yoy_median'] = statistics.median(stats['np_yoy'])
    else:
        stats['np_yoy_median'] = None

# 行业景气度排名
print("\n行业景气度排名（按净利润增速中位数）：")
theme_ranking = []
for theme, stats in theme_stats.items():
    if stats['np_yoy_median'] is not None and stats['count'] >= 3:
        theme_ranking.append({
            'theme': theme,
            'count': stats['count'],
            'np_yoy_median': stats['np_yoy_median'],
            'rev_yoy_median': stats['rev_yoy_median']
        })

theme_ranking.sort(key=lambda x: x['np_yoy_median'] if x['np_yoy_median'] else -9999, reverse=True)

for t in theme_ranking[:20]:
    print(f"  {t['theme']}({t['count']}只): 净利润增速中位数 {t['np_yoy_median']:+.1f}% | 营收增速中位数 {t['rev_yoy_median']:+.1f}%")

# =========== Step 7: 严格筛选 ===========
print("\n===== 严格筛选：业绩实质性增长 =====")

# 筛选条件：
# 1. 净利润同比增速 > 20%
# 2. 营收同比增速 > 10%（排除一次性收益）
# 3. ROE > 8%（排除低效增长）
# 4. PE < 60（排除虚高估值）
# 5. 所属行业景气度中位数 > 10%（行业共振）
# 6. 非周期性下滑行业

qualified = []
for r in results:
    # 条件1: 净利润增速
    np_yoy = r.get('np_yoy')
    if np_yoy is None or np_yoy < 20:
        continue
    
    # 条件2: 营收增速
    rev_yoy = r.get('rev_yoy')
    if rev_yoy is None or rev_yoy < 10:
        continue
    
    # 条件3: ROE
    roe = r.get('roe_waa') or r.get('roe')
    if roe is not None and roe < 8:
        continue
    
    # 条件4: PE
    pe = r.get('pe')
    if pe is not None and pe > 60:
        continue
    
    # 条件5: 行业景气度
    theme = r.get('theme', '其他')
    t_stat = theme_stats.get(theme, {})
    theme_np_median = t_stat.get('np_yoy_median')
    if theme_np_median is not None and theme_np_median < 10:
        continue
    
    # 计算综合业绩评分
    score = 0
    
    # 净利润增速(40分)
    if np_yoy >= 100:
        score += 40
    elif np_yoy >= 50:
        score += 30
    elif np_yoy >= 30:
        score += 20
    else:
        score += 10
    
    # 营收增速(20分)
    if rev_yoy >= 50:
        score += 20
    elif rev_yoy >= 30:
        score += 15
    elif rev_yoy >= 15:
        score += 10
    else:
        score += 5
    
    # ROE(20分)
    if roe is not None:
        if roe >= 20:
            score += 20
        elif roe >= 15:
            score += 15
        elif roe >= 10:
            score += 10
        else:
            score += 5
    
    # 行业共振(10分)
    if theme_np_median is not None:
        if theme_np_median >= 30:
            score += 10
        elif theme_np_median >= 15:
            score += 7
        elif theme_np_median >= 5:
            score += 4
    
    # 估值合理性(10分)
    if pe is not None:
        if pe <= 25:
            score += 10
        elif pe <= 35:
            score += 8
        elif pe <= 50:
            score += 5
        else:
            score += 2
    
    r['fundamental_score'] = score
    r['theme_np_median'] = theme_np_median
    r['theme_rev_median'] = t_stat.get('rev_yoy_median')
    qualified.append(r)

# 按业绩评分排序
qualified.sort(key=lambda x: x.get('fundamental_score', 0), reverse=True)

print(f"\n通过严格筛选的股票: {len(qualified)} 只")
print()
print(f"{'排名':<4} {'名称':<8} {'代码':<12} {'主题':<10} {'净利增速':<10} {'营收增速':<10} {'ROE':<8} {'PE':<8} {'行业净利中位':<12} {'业绩评分':<8}")
print('-' * 90)
for i, q in enumerate(qualified[:30], 1):
    name = q['name']
    code = q['ts_code']
    theme = q.get('theme', '')
    np_yoy = f"{q.get('np_yoy', 'N/A'):+.1f}%" if q.get('np_yoy') is not None else 'N/A'
    rev_yoy = f"{q.get('rev_yoy', 'N/A'):+.1f}%" if q.get('rev_yoy') is not None else 'N/A'
    roe_val = f"{q.get('roe_waa') or q.get('roe'):.1f}%" if (q.get('roe_waa') or q.get('roe')) is not None else 'N/A'
    pe_val = f"{q.get('pe'):.1f}" if q.get('pe') is not None else 'N/A'
    t_med = f"{q.get('theme_np_median'):+.1f}%" if q.get('theme_np_median') is not None else 'N/A'
    fs = q.get('fundamental_score', 0)
    
    print(f"{i:<4} {name:<8} {code:<12} {theme:<10} {np_yoy:<10} {rev_yoy:<10} {roe_val:<8} {pe_val:<8} {t_med:<12} {fs:<8}")

# =========== Step 8: 保存结果 ===========
output_file = r'D:\mystock\solo\report_daily\fundamental_screen_20260618.json'
output = {
    'scan_date': '2026-06-18',
    'method': '基于v4扫描的基本面深度筛选',
    'criteria': '净利润增速>20%, 营收增速>10%, ROE>8%, PE<60, 行业景气度>10%',
    'total_v4_stocks': len(stocks),
    'fundamental_qualified': len(qualified),
    'theme_ranking': theme_ranking[:20],
    'data': qualified
}

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n结果已保存: {output_file}")
print(f"文件大小: {os.path.getsize(output_file)} 字节")
