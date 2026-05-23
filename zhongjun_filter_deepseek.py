# -*- coding: utf-8 -*-
"""
中军二次筛选 - 调用DeepSeek过滤业绩风险大、基本面不好的标的
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts
import pandas as pd
import json
import requests
from pathlib import Path
from datetime import datetime

# ===== 配置 =====
env = Path(r'C:\Users\kongx\mystock\.env').read_text()
DEEPSEEK_KEY = None
TUSHARE_KEY = None
for line in env.splitlines():
    if line.startswith('DEEPSEEK_API_KEY='):
        DEEPSEEK_KEY = line.split('=',1)[1].strip()
    if line.startswith('TUSHARE_TOKEN='):
        TUSHARE_KEY = line.split('=',1)[1].strip()

ts.set_token(TUSHARE_KEY)
pro = ts.pro_api()

# ===== Step 1: 从zhongjun_screener结果中取TOP30 =====
print("[1/4] 重新运行中军筛选获取TOP30...")

def calc_ma(series, n):
    return series.rolling(n).mean()

def calc_vol_ratio(volume_series, period=20):
    avg = volume_series.rolling(period).mean()
    return volume_series / avg

def calc_price_position(close, high, low, period=120):
    h = high.rolling(period).max()
    l = low.rolling(period).min()
    return (close - l) / (h - l) * 100

def calc_volatility(close, period=20):
    ret = close.pct_change()
    return ret.rolling(period).std() * __import__('numpy').sqrt(250) * 100

# Run screener (reuse logic)
from datetime import timedelta
date_str = datetime.now().strftime('%Y%m%d')

daily_basic = pro.daily_basic(trade_date=date_str, fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
if len(daily_basic) == 0:
    df_cal = pro.trade_cal(exchange='SSE', is_open=1, end_date=date_str, limit=5)
    last_date = df_cal.sort_values('cal_date').iloc[-2]['cal_date']
    daily_basic = pro.daily_basic(trade_date=last_date, fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    date_str = last_date

daily_basic['mv_yi'] = daily_basic['total_mv'] / 10000
candidates = daily_basic[(daily_basic['mv_yi'] >= 100) & (daily_basic['mv_yi'] <= 500)].copy()
candidates = candidates[~candidates['ts_code'].str.startswith(('8','4','9'))]
candidates = candidates[candidates['pe'] > 0]

results = []
for idx, row in candidates.iterrows():
    ts_code = row['ts_code']
    try:
        start_date = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=200)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=date_str)
        if len(df) < 60:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        close = df['close']; high = df['high']; low = df['low']; vol = df['vol']
        ma5 = calc_ma(close, 5); ma10 = calc_ma(close, 10); ma20 = calc_ma(close, 20); ma60 = calc_ma(close, 60)
        cur_close = close.iloc[-1]; cur_ma5 = ma5.iloc[-1]; cur_ma10 = ma10.iloc[-1]; cur_ma20 = ma20.iloc[-1]; cur_ma60 = ma60.iloc[-1]
        ma_bullish = cur_ma5 > cur_ma10 > cur_ma20
        ma20_cross_ma60 = cur_ma20 > cur_ma60
        ma_golden = abs(cur_ma20 / cur_ma60 - 1) < 0.05 or ma20_cross_ma60
        vol_ratio = calc_vol_ratio(vol, 20)
        recent_vol_ratio = vol_ratio.iloc[-3:].mean()
        vol_breakout = recent_vol_ratio > 1.3
        platform_high = high.iloc[-21:-1].max()
        price_breakout = cur_close > platform_high * 0.98
        price_pos_120 = calc_price_position(close, high, low, 120).iloc[-1]
        from_bottom = price_pos_120 < 70
        vol20 = calc_volatility(close, 20).iloc[-1]
        vol_ok = vol20 < 60
        pct_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100
        launch_ok = 3 < pct_5d < 20
        recent40_high = high.iloc[-45:-5].max()
        recent40_low = low.iloc[-45:-5].min()
        platform_amp = (recent40_high - recent40_low) / recent40_low * 100
        platform_ok = platform_amp < 25
        score = 0
        if ma_bullish: score += 25
        if ma_golden: score += 20
        if vol_breakout: score += 15
        if price_breakout: score += 15
        if from_bottom: score += 10
        if launch_ok: score += 10
        if platform_ok: score += 5
        if score >= 60:
            results.append({'ts_code': ts_code, 'close': cur_close, 'mv_yi': row['mv_yi'],
                          'pe': row['pe'], 'pb': row['pb'], 'turnover_rate': row['turnover_rate'],
                          'score': score, 'pct_5d': round(pct_5d, 2), 'vol_ratio': round(recent_vol_ratio, 2),
                          'price_pos_120': round(price_pos_120, 1), 'volatility': round(vol20, 1)})
    except:
        continue

result_df = pd.DataFrame(results).sort_values('score', ascending=False)
stock_basic = pro.stock_basic(fields='ts_code,name,industry')
name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
industry_map = dict(zip(stock_basic['ts_code'], stock_basic['industry']))
result_df['name'] = result_df['ts_code'].map(name_map)
result_df['industry'] = result_df['ts_code'].map(industry_map)
industry_counts = result_df['industry'].value_counts()
hot_industries = industry_counts[industry_counts >= 2].index.tolist()
result_df['is_sector_play'] = result_df['industry'].isin(hot_industries)
result_df.loc[result_df['is_sector_play'], 'score'] += 10
result_df = result_df.sort_values('score', ascending=False)

top30 = result_df.head(30)
print(f"  TOP30获取完成, 最高分: {top30.iloc[0]['score']}")

# ===== Step 2: 获取详细财务数据 =====
print("[2/4] 获取TOP30详细财务数据...")

financial_data = []
for _, r in top30.iterrows():
    ts_code = r['ts_code']
    name = r['name']
    try:
        # 财务指标
        fi = pro.fina_indicator(ts_code=ts_code, period='20260331', 
            fields='ts_code,roe,yoyprofit,yoy_sales,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
        fi_data = fi.iloc[0].to_dict() if len(fi) > 0 else {}
        
        # 去年同期对比
        fi_ly = pro.fina_indicator(ts_code=ts_code, period='20250331',
            fields='ts_code,roe,grossprofit_margin,netprofit_margin')
        fi_ly_data = fi_ly.iloc[0].to_dict() if len(fi_ly) > 0 else {}
        
        # 现金流
        cf = pro.cashflow(ts_code=ts_code, period='20260331',
            fields='ts_code,c_pay_acq_asset,ncf_oper,c_fr_sale_sg')
        cf_data = cf.iloc[0].to_dict() if len(cf) > 0 else {}
        
        # 业绩快报/预告
        expr = None
        try:
            fore = pro.forecast(ts_code=ts_code, period='20260331', fields='ts_code,type,p_change_min,p_change_max,summary')
            if len(fore) > 0:
                expr = fore.iloc[0].to_dict()
        except:
            pass
        
        financial_data.append({
            'ts_code': ts_code,
            'name': name,
            'industry': r['industry'],
            'close': r['close'],
            'mv_yi': r['mv_yi'],
            'pe': r['pe'],
            'score': r['score'],
            'pct_5d': r['pct_5d'],
            'price_pos_120': r['price_pos_120'],
            'is_sector_play': r['is_sector_play'],
            'q1_roe': fi_data.get('roe'),
            'q1_gross_margin': fi_data.get('grossprofit_margin'),
            'q1_net_margin': fi_data.get('netprofit_margin'),
            'q1_op_yoy': fi_data.get('op_yoy'),
            'q1_debt_ratio': fi_data.get('debt_to_assets'),
            'q1_yoy_sales': fi_data.get('yoy_sales'),
            'q1_yoy_profit': fi_data.get('yoyprofit'),
            'q1_ocf_ratio': fi_data.get('ocf_to_or'),
            'ly_roe': fi_ly_data.get('roe'),
            'ly_gross_margin': fi_ly_data.get('grossprofit_margin'),
            'ly_net_margin': fi_ly_data.get('netprofit_margin'),
            'ncf_oper': cf_data.get('ncf_oper'),
            'forecast': expr,
        })
        print(f"  {name} OK")
    except Exception as e:
        print(f"  {name} err: {e}")

# ===== Step 3: 构建DeepSeek分析prompt =====
print("[3/4] 构建分析请求...")

# 精简数据给DeepSeek
stock_summaries = []
for d in financial_data:
    fc = ""
    if d['forecast']:
        fc = f" 业绩预告:{d['forecast'].get('type','')} 预计变动:{d['forecast'].get('p_change_min','')}~{d['forecast'].get('p_change_max','')}% {d['forecast'].get('summary','')}"
    
    summary = (
        f"{d['name']}({d['ts_code']}) | 行业:{d['industry']} | 现价:{d['close']} | "
        f"PE:{d['pe']} | 市值:{d['mv_yi']:.0f}亿 | 技术评分:{d['score']} | 5日涨:{d['pct_5d']}% | "
        f"120日分位:{d['price_pos_120']}% | 板块效应:{'是' if d['is_sector_play'] else '否'}\n"
        f"  Q1: ROE={d['q1_roe']} 毛利率={d['q1_gross_margin']}% 净利率={d['q1_net_margin']}% "
        f"营业利润YOY={d['q1_op_yoy']}% 负债率={d['q1_debt_ratio']}% "
        f"营收YOY={d['q1_yoy_sales']}% 净利YOY={d['q1_yoy_profit']}% "
        f"经营现金流/营收={d['q1_ocf_ratio']}\n"
        f"  去年同期Q1: ROE={d['ly_roe']} 毛利率={d['ly_gross_margin']}% 净利率={d['ly_net_margin']}%"
        f"{fc}"
    )
    stock_summaries.append(summary)

all_data = "\n\n".join(stock_summaries)

prompt = f"""你是A股基本面分析专家。以下是从量化筛选出的30只"中军"候选股，已附带最新财务数据。
请逐只评估，过滤掉业绩风险大、基本面不好的标的，只保留值得买入持有的优质中军。

评估维度（每项1-5分）：
1. **盈利质量**：毛利率>30%为优，净利率趋势向好
2. **成长性**：Q1营业利润YOY为正且可持续，不是一次性收益
3. **财务安全**：负债率<60%为安全，经营现金流为正
4. **估值合理性**：PE与成长性匹配（PEG<2为优）
5. **行业地位**：细分领域前3，有护城河

输出格式：
- 先列出**淘汰名单**及原因（1-2句）
- 再列出**入选名单**，每只给出5项评分(1-5)和总评(≤25)，以及买入建议
- 最后给出**TOP5推荐**排序

候选股数据：
{all_data}"""

# ===== Step 4: 调用DeepSeek API =====
print("[4/4] 调用DeepSeek分析...")

try:
    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是专业的A股基本面分析师，擅长从财务数据中识别风险和机会。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 4000
        },
        timeout=120
    )
    
    if resp.status_code == 200:
        result = resp.json()
        analysis = result['choices'][0]['message']['content']
        print("\n" + "="*80)
        print("DeepSeek 二次筛选结果")
        print("="*80)
        print(analysis)
    elif resp.status_code == 402:
        print("DeepSeek API余额不足(402)，改用本地模型分析...")
        # Fallback: local analysis
        print("\n" + "="*80)
        print("本地模型二次筛选结果")
        print("="*80)
        do_local_analysis(financial_data)
    else:
        print(f"DeepSeek API错误: {resp.status_code} {resp.text}")
except requests.exceptions.Timeout:
    print("DeepSeek超时，改用本地分析...")
    do_local_analysis(financial_data)
except Exception as e:
    print(f"调用失败: {e}，改用本地分析...")
    do_local_analysis(financial_data)


def do_local_analysis(data):
    """本地兜底分析逻辑"""
    print("\n📊 逐只基本面评估:\n")
    
    eliminated = []
    passed = []
    
    for d in data:
        risks = []
        scores = {}
        
        # 1. 盈利质量
        gm = d['q1_gross_margin'] or 0
        nm = d['q1_net_margin'] or 0
        if gm > 40: scores['盈利质量'] = 5
        elif gm > 30: scores['盈利质量'] = 4
        elif gm > 20: scores['盈利质量'] = 3
        elif gm > 10: scores['盈利质量'] = 2
        else: scores['盈利质量'] = 1; risks.append(f"毛利率仅{gm:.1f}%")
        
        # 净利率趋势
        ly_nm = d['ly_net_margin'] or 0
        if nm < ly_nm - 5:
            risks.append(f"净利率下滑({ly_nm:.1f}%→{nm:.1f}%)")
            scores['盈利质量'] = max(1, scores['盈利质量'] - 1)
        
        # 2. 成长性
        op_yoy = d['q1_op_yoy'] or 0
        if op_yoy > 50: scores['成长性'] = 5
        elif op_yoy > 20: scores['成长性'] = 4
        elif op_yoy > 0: scores['成长性'] = 3
        elif op_yoy > -20: scores['成长性'] = 2; risks.append(f"营业利润YOY={op_yoy:.1f}%")
        else: scores['成长性'] = 1; risks.append(f"营业利润大幅下滑YOY={op_yoy:.1f}%")
        
        # 3. 财务安全
        debt = d['q1_debt_ratio'] or 0
        if debt < 30: scores['财务安全'] = 5
        elif debt < 50: scores['财务安全'] = 4
        elif debt < 60: scores['财务安全'] = 3
        elif debt < 70: scores['财务安全'] = 2; risks.append(f"负债率{debt:.1f}%偏高")
        else: scores['财务安全'] = 1; risks.append(f"负债率{debt:.1f}%危险")
        
        # 经营现金流
        ocf = d['q1_ocf_ratio']
        if ocf is not None and ocf < 0:
            risks.append(f"经营现金流/营收={ocf:.2f}为负")
            scores['财务安全'] = max(1, scores['财务安全'] - 1)
        
        # 4. 估值合理性 (PE vs 成长)
        pe = d['pe'] or 0
        if op_yoy > 0:
            peg = pe / op_yoy if op_yoy > 0 else 999
        else:
            peg = 999
        if peg < 1: scores['估值'] = 5
        elif peg < 2: scores['估值'] = 4
        elif peg < 3: scores['估值'] = 3
        elif peg < 5: scores['估值'] = 2; risks.append(f"PEG={peg:.1f}偏高")
        else: scores['估值'] = 1; risks.append(f"PE={pe:.0f}相对成长偏高(PEG={peg:.1f})")
        
        # 5. 行业地位 (板块效应+市值)
        if d['is_sector_play'] and d['mv_yi'] > 150:
            scores['行业地位'] = 4
        elif d['is_sector_play']:
            scores['行业地位'] = 3
        else:
            scores['行业地位'] = 2
        
        total = sum(scores.values())
        
        if len(risks) >= 3 or total < 12:
            eliminated.append({'name': d['name'], 'code': d['ts_code'], 'risks': risks, 'total': total})
        else:
            passed.append({
                'name': d['name'], 'code': d['ts_code'], 'industry': d['industry'],
                'close': d['close'], 'pe': pe, 'mv': d['mv_yi'],
                'scores': scores, 'total': total, 'risks': risks,
                'op_yoy': op_yoy, 'debt': debt, 'gm': gm,
                'score_tech': d['score'], 'pct_5d': d['pct_5d'],
                'price_pos_120': d['price_pos_120']
            })
    
    # 输出淘汰名单
    print("❌ 淘汰名单:")
    for e in eliminated:
        print(f"  {e['name']}({e['code']}) 总分{e['total']}/25 - {'; '.join(e['risks'])}")
    
    # 输出入选名单
    passed.sort(key=lambda x: x['total'], reverse=True)
    print(f"\n✅ 入选名单 ({len(passed)}只):")
    for p in passed:
        risk_str = f" ⚠️{'/'.join(p['risks'])}" if p['risks'] else ""
        print(f"  {p['name']}({p['code']}) 总分{p['total']}/25 "
              f"盈利{p['scores']['盈利质量']} 成长{p['scores']['成长性']} "
              f"安全{p['scores']['财务安全']} 估值{p['scores']['估值']} 地位{p['scores']['行业地位']}"
              f" | PE={p['pe']:.0f} 市值={p['mv']:.0f}亿 毛利率={p['gm']:.1f}%"
              f" 营业利润YOY={p['op_yoy']:.1f}% 负债率={p['debt']:.1f}%{risk_str}")
    
    # TOP5
    print(f"\n🏆 TOP5推荐:")
    for i, p in enumerate(passed[:5]):
        print(f"  {i+1}. {p['name']}({p['code']}) {p['total']}分 | "
              f"{p['industry']} | 现价{p['close']} PE{p['pe']:.0f} | "
              f"毛利率{p['gm']:.1f}% 营业利润+{p['op_yoy']:.0f}% | "
              f"5日涨{p['pct_5d']}% 120日分位{p['price_pos_120']}%")
