#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半年报预测系统 — 因子引擎 v1
覆盖IA核心池6只股票

预测目标：2026H1（中报）营收和净利润区间
数据基准：2025H1 + 2026Q1已知 + 高频因子

因子体系：
A1. 季节性外推（Q1占比→H1推算）
A2. Q2环比趋势（Q1环比Q4方向）
A3. 行业景气度因子（板块中位数增速）
A4. 价格传导因子（产品价-原材料价）
A5. 基数效应校正（2025H1基数异常？）
A6. 管理层指引（调研纪要提取）
A7. 现金流先行因子（预收/合同负债）
"""

import json
import sys
import time
import os

sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

TUSHARE_TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# IA核心池6只
STOCKS = {
    '301308.SZ': {'name': '江波龙', 'theme': '存储芯片', 'q1_rev': 99.09, 'q1_ni': 38.62, 'rev_yoy': 133, 'ni_yoy': 2644},
    '688525.SH': {'name': '佰维存储', 'theme': '先进封装', 'q1_rev': 68.14, 'q1_ni': 28.99, 'rev_yoy': 342, 'ni_yoy': 1568},
    '688766.SH': {'name': '普冉股份', 'theme': '存储芯片', 'q1_rev': 14.47, 'q1_ni': 2.51, 'rev_yoy': 256, 'ni_yoy': 1260},
    '603268.SH': {'name': '松发股份', 'theme': '军工', 'q1_rev': 88.88, 'q1_ni': 10.93, 'rev_yoy': 15369, 'ni_yoy': 5340},
    '300302.SZ': {'name': '同有科技', 'theme': 'AI算力基建', 'q1_rev': 1.22, 'q1_ni': 0.87, 'rev_yoy': 156, 'ni_yoy': 431},
    '001389.SZ': {'name': '广合科技', 'theme': 'PCB电子电路', 'q1_rev': 19.14, 'q1_ni': 3.93, 'rev_yoy': 71, 'ni_yoy': 63},
}

print("=" * 70)
print("半年报预测系统 — 因子引擎 v1")
print("=" * 70)

# ============ 拉取历史财报数据 ============
print("\n[Step 1] 拉取历史财报（income 8期 + indicator 4期 + cashflow 4期）...")

stock_data = {}
errors = {}

for code, info in STOCKS.items():
    print(f"\n  拉取 {info['name']}({code})...")
    sd = {'income': [], 'indicator': [], 'cashflow': [], 'balancesheet': []}
    
    # income
    try:
        df = pro.income(ts_code=code, fields='ts_code,end_date,ann_date,total_revenue,revenue,cost_of_goods_sold,total_cogs,n_income_attr_p,oper_cost,admin_exp,fin_exp,sell_exp,rd_exp', period_type='1')
        if df is not None and len(df) > 0:
            sd['income'] = df.sort_values('end_date', ascending=False).head(8).to_dict('records')
        time.sleep(0.06)
    except Exception as e:
        errors[f'{code}_income'] = str(e)
        time.sleep(0.1)
    
    # fina_indicator
    try:
        df = pro.fina_indicator(ts_code=code, fields='ts_code,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,eps,op_yoy,tr_yoy,or_yoy,q_sales_yoy,q_profit_yoy')
        if df is not None and len(df) > 0:
            sd['indicator'] = df.sort_values('end_date', ascending=False).head(4).to_dict('records')
        time.sleep(0.06)
    except Exception as e:
        errors[f'{code}_indicator'] = str(e)
        time.sleep(0.1)
    
    # cashflow
    try:
        df = pro.cashflow(ts_code=code, fields='ts_code,end_date,n_cashflow_act,receive_capital_adv,pay_stk_rhf,notes_receivable')
        if df is not None and len(df) > 0:
            sd['cashflow'] = df.sort_values('end_date', ascending=False).head(4).to_dict('records')
        time.sleep(0.06)
    except Exception as e:
        errors[f'{code}_cashflow'] = str(e)
        time.sleep(0.1)
    
    # balancesheet (预收账款/合同负债)
    try:
        df = pro.balancesheet(ts_code=code, fields='ts_code,end_date,adv_receipts,notes_receivable')
        if df is not None and len(df) > 0:
            sd['balancesheet'] = df.sort_values('end_date', ascending=False).head(4).to_dict('records')
        time.sleep(0.06)
    except Exception as e:
        errors[f'{code}_balancesheet'] = str(e)
        time.sleep(0.1)
    
    stock_data[code] = sd
    print(f"    income:{len(sd['income'])} indicator:{len(sd['indicator'])} cashflow:{len(sd['cashflow'])} balance:{len(sd['balancesheet'])}")

if errors:
    print(f"\n  错误: {errors}")

# ============ 拉取一致预期 ============
print("\n[Step 2] 拉取一致预期（consensus forecast）...")

for code, info in STOCKS.items():
    try:
        df = pro.forecast_good(ts_code=code, period='2026', type='1', fields='ts_code,end_date,net_profit_min,net_profit_max,net_profit_avg,last_cnt')
        if df is not None and len(df) > 0:
            stock_data[code]['forecast'] = df.to_dict('records')
            print(f"  {info['name']}: {len(df)}条预期")
        else:
            # 尝试 fina_forecast
            df2 = pro.fina_forecast(period='2026', fields='ts_code,end_date,net_profit_min,net_profit_max,report_type')
            if df2 is not None and len(df2) > 0:
                match = df2[df2['ts_code'] == code]
                if len(match) > 0:
                    stock_data[code]['forecast'] = match.to_dict('records')
                    print(f"  {info['name']}: fina_forecast {len(match)}条")
                else:
                    stock_data[code]['forecast'] = []
            else:
                stock_data[code]['forecast'] = []
        time.sleep(0.06)
    except Exception as e:
        stock_data[code]['forecast'] = []
        print(f"  {info['name']}: 无一致预期数据")

# ============ 因子计算 ============
print("\n[Step 3] 计算预测因子...")

predictions = {}

for code, info in STOCKS.items():
    sd = stock_data[code]
    inc = sd.get('income', [])
    ind = sd.get('indicator', [])
    cf = sd.get('cashflow', [])
    bs = sd.get('balancesheet', [])
    
    pred = {
        'code': code,
        'name': info['name'],
        'theme': info['theme'],
        'q1_rev_yi': info['q1_rev'],
        'q1_ni_yi': info['q1_ni'],
        'q1_rev_yoy': info['rev_yoy'],
        'q1_ni_yoy': info['ni_yoy'],
        'factors': {},
        'h1_predict': {},
        'confidence': '',
    }
    
    # ----- A1: 季节性外推因子 -----
    # Q1占H1比例的历史均值 → 用Q1推算H1
    q1_h1_ratios_rev = []
    q1_h1_ratios_ni = []
    
    for i in range(len(inc) - 1, max(-1, len(inc) - 5), -1):
        # 找对应的H1数据
        r = inc[i]
        end = r.get('end_date', '')
        if not end:
            continue
        year = end[:4]
        q1_date = year + '0331'
        h1_date = year + '0630'
        
        q1_rev = 0
        h1_rev = 0
        q1_ni = 0
        h1_ni = 0
        
        for row in inc:
            if row.get('end_date') == q1_date:
                q1_rev = row.get('total_revenue', 0) or 0
                q1_ni = row.get('n_income_attr_p', 0) or 0
            elif row.get('end_date') == h1_date:
                h1_rev = row.get('total_revenue', 0) or 0
                h1_ni = row.get('n_income_attr_p', 0) or 0
        
        if q1_rev > 0 and h1_rev > 0:
            q1_h1_ratios_rev.append(q1_rev / h1_rev)
        if q1_ni > 0 and h1_ni > 0:
            q1_h1_ratios_ni.append(q1_ni / h1_ni)
    
    if q1_h1_ratios_rev:
        avg_q1_rev_ratio = sum(q1_h1_ratios_rev) / len(q1_h1_ratios_rev)
        pred['factors']['A1_q1_h1_ratio_rev'] = round(avg_q1_rev_ratio, 3)
        pred['factors']['A1_seasonal_h1_rev'] = round(info['q1_rev'] / avg_q1_rev_ratio, 2)  # H1营收预测
    else:
        # 无历史数据，用默认45%
        avg_q1_rev_ratio = 0.45
        pred['factors']['A1_q1_h1_ratio_rev'] = 0.45
        pred['factors']['A1_seasonal_h1_rev'] = round(info['q1_rev'] / 0.45, 2)
    
    if q1_h1_ratios_ni:
        avg_q1_ni_ratio = sum(q1_h1_ratios_ni) / len(q1_h1_ratios_ni)
        pred['factors']['A1_q1_h1_ratio_ni'] = round(avg_q1_ni_ratio, 3)
        pred['factors']['A1_seasonal_h1_ni'] = round(info['q1_ni'] / avg_q1_ni_ratio, 2)
    else:
        avg_q1_ni_ratio = 0.40
        pred['factors']['A1_q1_h1_ratio_ni'] = 0.40
        pred['factors']['A1_seasonal_h1_ni'] = round(info['q1_ni'] / 0.40, 2)
    
    print(f"\n  [{info['name']}] A1 季节性外推:")
    print(f"    Q1/H1营收比(历史): {q1_h1_ratios_rev} → 均值{avg_q1_rev_ratio:.2f}")
    print(f"    Q1/H1净利比(历史): {q1_h1_ratios_ni} → 均值{avg_q1_ni_ratio:.2f}")
    print(f"    → H1营收预测: {pred['factors']['A1_seasonal_h1_rev']:.2f}亿")
    print(f"    → H1净利预测: {pred['factors']['A1_seasonal_h1_ni']:.2f}亿")
    
    # ----- A2: Q2环比趋势因子 -----
    # Q1环比Q4的方向和幅度
    if len(inc) >= 2:
        q1 = inc[0]  # 20260331
        q4 = None
        # 找Q4 = 20251231
        for r in inc:
            if r.get('end_date') == '20251231':
                q4 = r
                break
        
        if q1 and q4:
            q4_rev = q4.get('total_revenue', 0) or 0
            q4_ni = q4.get('n_income_attr_p', 0) or 0
            if q4_rev > 0:
                qoq_rev = (info['q1_rev'] * 1e8 - q4_rev) / abs(q4_rev) * 100
                pred['factors']['A2_qoq_rev'] = round(qoq_rev, 1)
            else:
                pred['factors']['A2_qoq_rev'] = None
            if q4_ni and q4_ni != 0:
                qoq_ni = (info['q1_ni'] * 1e8 - q4_ni) / abs(q4_ni) * 100
                pred['factors']['A2_qoq_ni'] = round(qoq_ni, 1)
            else:
                pred['factors']['A2_qoq_ni'] = None
            
            print(f"  [{info['name']}] A2 Q2环比趋势:")
            print(f"    Q1环比Q4营收: {pred['factors'].get('A2_qoq_rev', 'N/A')}%")
            print(f"    Q1环比Q4净利: {pred['factors'].get('A2_qoq_ni', 'N/A')}%")
            
            # 如果环比大幅增长，Q2可能继续加速或回落
            qoq = pred['factors'].get('A2_qoq_rev', 0) or 0
            if qoq > 30:
                pred['factors']['A2_momentum'] = '加速'  # Q2可能继续加速
                q2_adj = 1.10  # Q2比Q1季节性再+10%
            elif qoq > 10:
                pred['factors']['A2_momentum'] = '稳定'
                q2_adj = 1.05
            elif qoq > -10:
                pred['factors']['A2_momentum'] = '平稳'
                q2_adj = 1.00
            else:
                pred['factors']['A2_momentum'] = '减速'
                q2_adj = 0.90
            
            pred['factors']['A2_adjusted_h1_rev'] = round(
                pred['factors']['A1_seasonal_h1_rev'] * (0.5 * q2_adj + 0.5), 2
            )
            print(f"    趋势判断: {pred['factors']['A2_momentum']}, Q2调整系数: {q2_adj}")
        else:
            pred['factors']['A2_momentum'] = '未知'
            pred['factors']['A2_adjusted_h1_rev'] = pred['factors']['A1_seasonal_h1_rev']
    
    # ----- A3: 行业景气度因子 -----
    # 同板块其他公司的Q1增速中位数
    theme_peers = {
        '存储芯片': ['301308.SZ', '688766.SH', '688041.SH', '688256.SH', '688521.SH'],
        '先进封装': ['688525.SH', '603501.SH', '002049.SZ', '300782.SZ', '688396.SH'],
        '军工': ['603268.SH', '000768.SZ', '600760.SH', '002179.SZ'],
        'AI算力基建': ['300302.SZ', '300474.SZ', '002415.SZ', '300869.SZ'],
        'PCB电子电路': ['001389.SZ', '001289.SZ', '301269.SZ', '688536.SH', '301281.SZ'],
    }
    
    # 从JSON中读取同行业增速
    theme_stats_file = r'D:\mystock\solo\report_daily\fundamental_screen_full_20260618.json'
    try:
        with open(theme_stats_file, 'r', encoding='utf-8') as f:
            theme_data = json.load(f)
        
        for theme, peers in theme_peers.items():
            if info['theme'] in theme or theme in info['theme']:
                peer_np = []
                for ic in theme_data.get('IA_data', []) + theme_data.get('IB_data', []):
                    if ic.get('ts_code') in peers and ic.get('np_yoy'):
                        peer_np.append(ic['np_yoy'])
                if not peer_np:
                    for ic in theme_data.get('IC_data', []):
                        if ic.get('ts_code') in peers and ic.get('np_yoy'):
                            peer_np.append(ic['np_yoy'])
                
                if peer_np:
                    import statistics
                    median_np = statistics.median(peer_np)
                    pred['factors']['A3_peer_np_median'] = round(median_np, 1)
                    print(f"  [{info['name']}] A3 行业景气: 同板块{len(peer_np)}只 净利增速中位数+{median_np:.0f}%")
                    
                    # 如果本公司增速远超板块中位，可能有溢价但也容易回落
                    if info['ni_yoy'] > median_np * 2:
                        pred['factors']['A3_vs_peers'] = '远超板块（alpha型）'
                    elif info['ni_yoy'] > median_np * 1.2:
                        pred['factors']['A3_vs_peers'] = '略超板块（跟随+alpha）'
                    else:
                        pred['factors']['A3_vs_peers'] = '跟随板块（beta型）'
                break
    except Exception as e:
        print(f"  [{info['name']}] A3 行业景气: 无法获取 ({e})")
    
    # ----- A5: 基数效应因子 -----
    # 2025H1基数是否正常
    if len(inc) >= 4:
        for r in inc:
            if r.get('end_date') == '20250630':
                h1_2025_rev = r.get('total_revenue', 0)
                h1_2025_ni = r.get('n_income_attr_p', 0)
                
                # 对比2024H1
                for r2 in inc:
                    if r2.get('end_date') == '20240630':
                        h1_2024_rev = r2.get('total_revenue', 0)
                        h1_2024_ni = r2.get('n_income_attr_p', 0)
                        
                        if h1_2024_rev and h1_2024_rev > 0:
                            h1_yoy_rev = (h1_2025_rev - h1_2024_rev) / h1_2024_rev * 100
                            pred['factors']['A5_2025h1_rev_yoy'] = round(h1_yoy_rev, 1)
                        if h1_2024_ni and h1_2024_ni != 0:
                            h1_yoy_ni = (h1_2025_ni - h1_2024_ni) / abs(h1_2024_ni) * 100
                            pred['factors']['A5_2025h1_ni_yoy'] = round(h1_yoy_ni, 1)
                        
                        # 如果2025H1大幅下滑，说明2026H1同比会非常好（低基数效应）
                        h1_yoy = pred['factors'].get('A5_2025h1_rev_yoy', 0)
                        if h1_yoy < -20:
                            pred['factors']['A5_base_effect'] = '强低基数效应（2025H1大幅下滑）'
                        elif h1_yoy < -5:
                            pred['factors']['A5_base_effect'] = '中等低基数效应'
                        elif h1_yoy > 20:
                            pred['factors']['A5_base_effect'] = '高基数效应（2025H1已高增，继续高增更难）'
                        else:
                            pred['factors']['A5_base_effect'] = '中性基数'
                        
                        print(f"  [{info['name']}] A5 基数效应:")
                        print(f"    2025H1营收: {h1_2025_rev/1e8:.2f}亿, 2024H1: {h1_2024_rev/1e8:.2f}亿")
                        print(f"    2025H1净利: {h1_2025_ni/1e8:.2f}亿, 2024H1: {h1_2024_ni/1e8:.2f}亿")
                        print(f"    2025H1营收同比: {pred['factors'].get('A5_2025h1_rev_yoy', 'N/A')}%")
                        print(f"    → {pred['factors'].get('A5_base_effect', '未知')}")
                        break
                break
    
    # ----- A7: 现金流先行因子 -----
    if cf:
        latest_cf = cf[0]
        ocf = latest_cf.get('n_cashflow_act', 0)
        pred['factors']['A7_q1_ocf'] = round((ocf or 0) / 1e8, 2)
        
        q1_ni = info['q1_ni']
        if q1_ni > 0:
            ocf_ratio = (ocf or 0) / (q1_ni * 1e8)
            pred['factors']['A7_ocf_ni_ratio'] = round(ocf_ratio, 2)
            if ocf_ratio > 0.8:
                pred['factors']['A7_quality'] = '高现金含量（利润扎实）'
            elif ocf_ratio > 0.5:
                pred['factors']['A7_quality'] = '中等现金含量'
            elif ocf_ratio > 0:
                pred['factors']['A7_quality'] = '低现金含量（有应收/库存压力）'
            else:
                pred['factors']['A7_quality'] = '负现金流（警惕）'
            print(f"  [{info['name']}] A7 现金流: 经营现金流{pred['factors']['A7_q1_ocf']}亿, OCF/NI={ocf_ratio:.2f} → {pred['factors']['A7_quality']}")
    
    # ----- 合成预测 -----
    seasonal_h1_rev = pred['factors'].get('A1_seasonal_h1_rev', 0)
    seasonal_h1_ni = pred['factors'].get('A1_seasonal_h1_ni', 0)
    
    momentum = pred['factors'].get('A2_momentum', '未知')
    q2_adj = 1.00
    if momentum == '加速':
        q2_adj = 1.10
    elif momentum == '稳定':
        q2_adj = 1.05
    elif momentum == '减速':
        q2_adj = 0.90
    
    # H1 = Q1 / (Q1占比) × 动量调整
    h1_rev_mid = seasonal_h1_rev * q2_adj
    h1_ni_mid = seasonal_h1_ni * q2_adj
    
    # 给出区间：±15%
    h1_rev_low = round(h1_rev_mid * 0.85, 2)
    h1_rev_high = round(h1_rev_mid * 1.15, 2)
    h1_rev_mid = round(h1_rev_mid, 2)
    h1_ni_low = round(h1_ni_mid * 0.85, 2)
    h1_ni_high = round(h1_ni_mid * 1.15, 2)
    h1_ni_mid = round(h1_ni_mid, 2)
    
    # 计算同比增速
    if len(inc) >= 4:
        for r in inc:
            if r.get('end_date') == '20250630':
                h1_2025_rev = (r.get('total_revenue', 0) or 0) / 1e8
                h1_2025_ni = (r.get('n_income_attr_p', 0) or 0) / 1e8
                
                if h1_2025_rev > 0:
                    h1_rev_yoy_mid = round((h1_rev_mid - h1_2025_rev) / h1_2025_rev * 100, 1)
                    h1_rev_yoy_low = round((h1_rev_low - h1_2025_rev) / h1_2025_rev * 100, 1)
                    h1_rev_yoy_high = round((h1_rev_high - h1_2025_rev) / h1_2025_rev * 100, 1)
                else:
                    h1_rev_yoy_mid = h1_rev_yoy_low = h1_rev_yoy_high = None
                
                if h1_2025_ni and h1_2025_ni != 0:
                    h1_ni_yoy_mid = round((h1_ni_mid - h1_2025_ni) / abs(h1_2025_ni) * 100, 1)
                    h1_ni_yoy_low = round((h1_ni_low - h1_2025_ni) / abs(h1_2025_ni) * 100, 1)
                    h1_ni_yoy_high = round((h1_ni_high - h1_2025_ni) / abs(h1_2025_ni) * 100, 1)
                else:
                    h1_ni_yoy_mid = h1_ni_yoy_low = h1_ni_yoy_high = None
                
                pred['h1_predict'] = {
                    'h1_2025_rev': round(h1_2025_rev, 2),
                    'h1_2025_ni': round(h1_2025_ni, 2),
                    'h1_rev': {'low': h1_rev_low, 'mid': h1_rev_mid, 'high': h1_rev_high},
                    'h1_ni': {'low': h1_ni_low, 'mid': h1_ni_mid, 'high': h1_ni_high},
                    'h1_rev_yoy': {'low': h1_rev_yoy_low, 'mid': h1_rev_yoy_mid, 'high': h1_rev_yoy_high},
                    'h1_ni_yoy': {'low': h1_ni_yoy_low, 'mid': h1_ni_yoy_mid, 'high': h1_ni_yoy_high},
                }
                break
    
    # 置信度
    factors_filled = sum(1 for k, v in pred['factors'].items() if v is not None and v != '未知')
    total_factors = 7
    confidence_pct = min(factors_filled / total_factors, 1.0)
    if confidence_pct >= 0.7:
        pred['confidence'] = '较高'
    elif confidence_pct >= 0.5:
        pred['confidence'] = '中等'
    else:
        pred['confidence'] = '较低（因子缺失较多）'
    pred['confidence_pct'] = round(confidence_pct, 2)
    
    predictions[code] = pred
    
    # 打印
    hp = pred.get('h1_predict', {})
    print(f"\n  ═══ {info['name']} 半年报预测 ═══")
    print(f"  Q1已知: 营收{info['q1_rev']}亿 净利{info['q1_ni']}亿")
    print(f"  2025H1基准: 营收{hp.get('h1_2025_rev','N/A')}亿 净利{hp.get('h1_2025_ni','N/A')}亿")
    if hp.get('h1_rev'):
        print(f"  H1营收预测: {hp['h1_rev']['low']}~{hp['h1_rev']['high']}亿 (中值{hp['h1_rev']['mid']})")
        if hp.get('h1_rev_yoy', {}).get('mid'):
            print(f"  H1营收同比: +{hp['h1_rev_yoy']['low']}%~+{hp['h1_rev_yoy']['high']}% (中值+{hp['h1_rev_yoy']['mid']}%)")
    if hp.get('h1_ni'):
        print(f"  H1净利预测: {hp['h1_ni']['low']}~{hp['h1_ni']['high']}亿 (中值{hp['h1_ni']['mid']})")
        if hp.get('h1_ni_yoy', {}).get('mid'):
            print(f"  H1净利同比: +{hp['h1_ni_yoy']['low']}%~+{hp['h1_ni_yoy']['high']}% (中值+{hp['h1_ni_yoy']['mid']}%)")
    print(f"  置信度: {pred['confidence']} ({pred['confidence_pct']*100:.0f}%因子填充率)")

# ============ 保存 ============
output = {
    'predict_date': '2026-06-19',
    'target': '2026H1半年报',
    'method': '因子引擎v1: A1季节性外推 + A2环比趋势 + A3行业景气 + A5基数效应 + A7现金流先行',
    'predictions': predictions,
}

output_file = r'D:\mystock\solo\report_daily\h1_predict_v1_20260619.json'
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f"\n\n{'='*70}")
print(f"预测完成，已保存: {output_file}")
print(f"文件大小: {os.path.getsize(output_file)} 字节")
