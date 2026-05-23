# -*- coding: utf-8 -*-
"""
三步法中军选股：
Step1: block.py 获取主线板块
Step2: 中军筛选 + 匹配主线
Step3: DeepSeek 二次过滤
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import json
import tushare as ts
import pandas as pd
import numpy as np
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# ===== 配置 =====
BASE_DIR = r'C:\Users\kongx\mystock'
CACHE_DIR = os.path.join(BASE_DIR, 'cache_daily')

env = Path(os.path.join(BASE_DIR, '.env')).read_text()
DEEPSEEK_KEY = None
for line in env.splitlines():
    if line.startswith('DEEPSEEK_API_KEY='):
        DEEPSEEK_KEY = line.split('=',1)[1].strip()
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=',1)[1].strip())

pro = ts.pro_api()

# ============================================================
# Step 1: 获取主线板块 (from block.py DB)
# ============================================================
def get_hot_sectors(top_n=10):
    """从block.py的SQLite数据库读取主线板块"""
    db_path = os.path.join(CACHE_DIR, 'hot_sector.db')
    import sqlite3
    conn = sqlite3.connect(db_path)
    
    # 获取最新日期
    latest = pd.read_sql("SELECT MAX(date) as d FROM hot_sector", conn)
    latest_date = latest.iloc[0]['d']
    print(f"[Step1] 主线板块数据日期: {latest_date}")
    
    # 读取TOP N
    df = pd.read_sql(f"SELECT * FROM hot_sector WHERE date='{latest_date}' ORDER BY rank ASC LIMIT {top_n}", conn)
    conn.close()
    
    print(f"[Step1] TOP{top_n} 主线板块:")
    for _, r in df.iterrows():
        print(f"  #{r['rank']} {r['name']} | 强度={r['score']:.2f} 动量={r['momentum']:.2f} 龙头={r['leader_name']}")
    
    return df, latest_date

# ============================================================
# Step 1b: 获取主线板块的成分股 (from concept_stock_map + sw_industry)
# ============================================================
def get_sector_stocks(sector_names):
    """获取主线板块对应的成分股代码"""
    # 加载概念-股票映射
    cs_path = os.path.join(CACHE_DIR, 'concept_stock_map.pkl')
    if os.path.exists(cs_path):
        import pickle
        with open(cs_path, 'rb') as f:
            concept_map = pickle.load(f)
    else:
        concept_map = {}
    
    # 加载主题映射
    theme_path = os.path.join(BASE_DIR, 'theme_map.json')
    with open(theme_path, 'r', encoding='utf-8') as f:
        theme_map = json.load(f)
    
    # 加载申万行业
    sw_path = os.path.join(CACHE_DIR, 'sw_map.csv')
    sw_df = pd.read_csv(sw_path, dtype=str) if os.path.exists(sw_path) else pd.DataFrame()
    
    # 加载股票-概念映射
    sc_path = os.path.join(CACHE_DIR, 'stock_concept_map.pkl')
    if os.path.exists(sc_path):
        import pickle
        with open(sc_path, 'rb') as f:
            stock_concept_map = pickle.load(f)
    else:
        stock_concept_map = {}
    
    all_stocks = set()
    sector_stock_map = {}  # sector_name -> set of ts_codes
    
    for name in sector_names:
        stocks = set()
        
        # 1) 从theme_map匹配
        if name in theme_map:
            cfg = theme_map[name]
            # 行业匹配
            if len(sw_df) > 0:
                for level in ['l2_name', 'l3_name']:
                    if level in sw_df.columns:
                        for ind in cfg.get('industry', []):
                            matched = sw_df[sw_df[level] == ind]['ts_code'].dropna().unique()
                            stocks.update(matched)
            # 概念关键词匹配
            for kw in cfg.get('keywords', []):
                for concept_name, code_list in concept_map.items():
                    if kw in concept_name:
                        stocks.update(code_list)
        
        # 2) 从concept_map直接匹配板块名
        if name in concept_map:
            stocks.update(concept_map[name])
        
        # 3) 从sw行业匹配
        if len(sw_df) > 0:
            for level in ['l1_name', 'l2_name', 'l3_name']:
                if level in sw_df.columns:
                    matched = sw_df[sw_df[level] == name]['ts_code'].dropna().unique()
                    stocks.update(matched)
        
        # 4) 从stock_concept_map反查
        for ts_code, concepts in stock_concept_map.items():
            if name in str(concepts):
                stocks.add(ts_code)
        
        sector_stock_map[name] = stocks
        all_stocks.update(stocks)
    
    return sector_stock_map, all_stocks

# ============================================================
# Step 2: 中军筛选 + 匹配主线
# ============================================================
def calc_ma(s, n): return s.rolling(n).mean()
def calc_vol_ratio(v, p=20): return v / v.rolling(p).mean()

def screen_zhongjun(date_str, mv_min=100, mv_max=500):
    """筛选中军候选"""
    daily_basic = pro.daily_basic(trade_date=date_str, 
        fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    if len(daily_basic) == 0:
        cal = pro.trade_cal(exchange='SSE', is_open=1, end_date=date_str, limit=5)
        date_str = cal.sort_values('cal_date').iloc[-2]['cal_date']
        daily_basic = pro.daily_basic(trade_date=date_str,
            fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    
    daily_basic['mv_yi'] = daily_basic['total_mv'] / 10000
    cands = daily_basic[(daily_basic['mv_yi'] >= mv_min) & (daily_basic['mv_yi'] <= mv_max)].copy()
    cands = cands[~cands['ts_code'].str.startswith(('8','4','9'))]
    cands = cands[cands['pe'] > 0]
    
    results = []
    start = (datetime.strptime(date_str, '%Y%m%d') - timedelta(days=200)).strftime('%Y%m%d')
    
    for _, row in cands.iterrows():
        tc = row['ts_code']
        try:
            df = pro.daily(ts_code=tc, start_date=start, end_date=date_str)
            if len(df) < 60: continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            c, h, l, v = df['close'], df['high'], df['low'], df['vol']
            ma5, ma10, ma20, ma60 = calc_ma(c,5), calc_ma(c,10), calc_ma(c,20), calc_ma(c,60)
            
            # 条件评分
            score = 0
            bull = ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]
            if bull: score += 25
            cross = ma20.iloc[-1] > ma60.iloc[-1]
            if cross or abs(ma20.iloc[-1]/ma60.iloc[-1]-1) < 0.05: score += 20
            vr = calc_vol_ratio(v, 20)
            if vr.iloc[-3:].mean() > 1.3: score += 15
            if c.iloc[-1] > h.iloc[-21:-1].max() * 0.98: score += 15
            pos120 = (c.iloc[-1] - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: score += 10
            pct5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: score += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if (rh-rl)/rl*100 < 25: score += 5
            
            if score >= 60:
                results.append({
                    'ts_code': tc, 'close': c.iloc[-1], 'mv_yi': row['mv_yi'],
                    'pe': row['pe'], 'score': score, 'pct_5d': round(pct5,2),
                    'price_pos_120': round(pos120,1), 'turnover_rate': row['turnover_rate']
                })
        except: continue
    
    rdf = pd.DataFrame(results).sort_values('score', ascending=False)
    sb = pro.stock_basic(fields='ts_code,name,industry')
    rdf['name'] = rdf['ts_code'].map(dict(zip(sb['ts_code'], sb['name'])))
    rdf['industry'] = rdf['ts_code'].map(dict(zip(sb['ts_code'], sb['industry'])))
    return rdf

def match_zhongjun_to_sectors(zhongjun_df, sector_stock_map):
    """将中军股票匹配到主线板块"""
    matched = []
    for _, zj in zhongjun_df.iterrows():
        tc = zj['ts_code']
        matched_sectors = []
        for sector, stocks in sector_stock_map.items():
            if tc in stocks:
                matched_sectors.append(sector)
        if matched_sectors:
            matched.append({
                'ts_code': tc, 'name': zj['name'], 'industry': zj['industry'],
                'close': zj['close'], 'mv_yi': zj['mv_yi'], 'pe': zj['pe'],
                'score': zj['score'], 'pct_5d': zj['pct_5d'],
                'price_pos_120': zj['price_pos_120'],
                'matched_sectors': '; '.join(matched_sectors),
                'sector_count': len(matched_sectors)
            })
    
    mdf = pd.DataFrame(matched).sort_values(['sector_count', 'score'], ascending=False)
    return mdf

# ============================================================
# Step 3: DeepSeek 过滤
# ============================================================
def get_financial_data(ts_codes):
    """批量获取财务数据"""
    data = []
    for tc in ts_codes:
        try:
            fi = pro.fina_indicator(ts_code=tc, period='20260331',
                fields='ts_code,roe,yoyprofit,yoy_sales,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
            fi_data = fi.iloc[0].to_dict() if len(fi) > 0 else {}
            
            # 去年Q1对比
            fi_ly = pro.fina_indicator(ts_code=tc, period='20250331',
                fields='ts_code,roe,grossprofit_margin,netprofit_margin')
            fi_ly_data = fi_ly.iloc[0].to_dict() if len(fi_ly) > 0 else {}
            
            data.append({
                'ts_code': tc,
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
            })
            name_map = {}
            sb = pro.stock_basic(fields='ts_code,name')
            name_map = dict(zip(sb['ts_code'], sb['name']))
        except: 
            pass
    return data

def deepseek_filter(matched_df, financial_data):
    """调用DeepSeek进行基本面过滤"""
    # 构建数据摘要
    summaries = []
    name_map_cache = {}
    sb = pro.stock_basic(fields='ts_code,name,industry')
    name_map_cache = dict(zip(sb['ts_code'], sb['name']))
    industry_map_cache = dict(zip(sb['ts_code'], sb['industry']))
    
    for _, r in matched_df.iterrows():
        tc = r['ts_code']
        fin = next((f for f in financial_data if f['ts_code'] == tc), {})
        
        summary = (
            f"{r['name']}({tc}) | 匹配主线:{r['matched_sectors']} | 匹配主线数:{r['sector_count']}\n"
            f"  现价:{r['close']} PE:{r['pe']} 市值:{r['mv_yi']:.0f}亿 技术评分:{r['score']} "
            f"5日涨:{r['pct_5d']}% 120日分位:{r['price_pos_120']}%\n"
            f"  Q1: ROE={fin.get('q1_roe')} 毛利率={fin.get('q1_gross_margin')}% "
            f"净利率={fin.get('q1_net_margin')}% 营业利润YOY={fin.get('q1_op_yoy')}% "
            f"负债率={fin.get('q1_debt_ratio')}% 营收YOY={fin.get('q1_yoy_sales')}% "
            f"经营现金流/营收={fin.get('q1_ocf_ratio')}\n"
            f"  去年Q1: ROE={fin.get('ly_roe')} 毛利率={fin.get('ly_gross_margin')}% 净利率={fin.get('ly_net_margin')}%"
        )
        summaries.append(summary)
    
    all_data = "\n\n".join(summaries)
    
    prompt = f"""你是A股基本面分析专家。以下是已经过技术面筛选并匹配到当前主线板块的"中军"候选股。
这些股票既符合中军启动的技术形态，又属于当前市场主线板块。

请逐只评估，过滤掉业绩风险大、基本面不好的标的，只保留值得买入持有的优质中军。

评估维度（每项1-5分）：
1. **盈利质量**：毛利率>30%为优，净利率趋势向好
2. **成长性**：Q1营业利润YOY为正且可持续，非一次性收益
3. **财务安全**：负债率<60%为安全，经营现金流为正
4. **估值合理性**：PE与成长性匹配（PEG<2为优）
5. **主线契合度**：匹配主线数越多越好，说明是多个热点的交汇点

输出格式：
- **淘汰名单**及原因
- **入选名单**，每只给5项评分和总评(≤25)
- **TOP5推荐**排序，附买入建议

候选股数据：
{all_data}"""
    
    print(f"\n[Step3] 调用DeepSeek分析 ({len(summaries)}只)...")
    
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是专业的A股基本面分析师，擅长从财务数据中识别风险和机会，尤其擅长判断股票与主线板块的契合度。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000
            },
            timeout=120
        )
        
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
        elif resp.status_code == 402:
            return "ERROR:DeepSeek余额不足"
        else:
            return f"ERROR:API返回{resp.status_code}"
    except requests.exceptions.Timeout:
        return "ERROR:超时"
    except Exception as e:
        return f"ERROR:{e}"


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 80)
    print("三步法中军选股系统")
    print("Step1: 主线板块 → Step2: 中军匹配 → Step3: DeepSeek过滤")
    print("=" * 80)
    
    # Step 1: 获取主线板块
    sectors_df, trade_date = get_hot_sectors(top_n=10)
    sector_names = sectors_df['name'].tolist()
    
    # 获取主线成分股
    print(f"\n[Step1b] 获取主线成分股...")
    sector_stock_map, all_sector_stocks = get_sector_stocks(sector_names)
    for name, stocks in sector_stock_map.items():
        print(f"  {name}: {len(stocks)}只成分股")
    print(f"  主线去重合计: {len(all_sector_stocks)}只")
    
    # Step 2: 中军筛选
    print(f"\n[Step2] 中军技术面筛选...")
    zhongjun_df = screen_zhongjun(trade_date)
    print(f"  技术筛选结果: {len(zhongjun_df)}只中军候选")
    
    # 匹配主线
    print(f"\n[Step2b] 匹配主线板块...")
    matched_df = match_zhongjun_to_sectors(zhongjun_df, sector_stock_map)
    print(f"  匹配到主线的: {len(matched_df)}只")
    
    if len(matched_df) == 0:
        print("⚠️ 无匹配主线的中军，请检查概念缓存是否过期")
        return
    
    # 显示匹配结果
    print(f"\n📋 匹配主线的中军候选 (共{len(matched_df)}只):")
    print("-" * 100)
    for _, r in matched_df.head(30).iterrows():
        multi = "🔥" if r['sector_count'] >= 2 else "  "
        print(f"{multi} {r['name']:6s}({r['ts_code']}) 评分:{r['score']:3.0f} "
              f"PE:{r['pe']:7.1f} 市值:{r['mv_yi']:6.0f}亿 "
              f"5日涨:{r['pct_5d']:+6.2f}% 主线:{r['matched_sectors']}")
    
    # Step 3: 获取财务数据 + DeepSeek过滤
    print(f"\n[Step3] 获取财务数据...")
    top_codes = matched_df.head(30)['ts_code'].tolist()
    financial_data = get_financial_data(top_codes)
    print(f"  财务数据获取完成: {len(financial_data)}只")
    
    # DeepSeek过滤
    result = deepseek_filter(matched_df.head(30), financial_data)
    
    if result.startswith("ERROR:"):
        err = result.split(":",1)[1]
        print(f"\n⚠️ DeepSeek调用失败({err})，使用本地过滤...")
        local_filter(matched_df, financial_data)
    else:
        print("\n" + "=" * 80)
        print("DeepSeek 二次筛选结果")
        print("=" * 80)
        print(result)


def local_filter(matched_df, financial_data):
    """本地兜底过滤"""
    print("\n📊 本地基本面过滤:")
    
    passed = []
    for _, r in matched_df.iterrows():
        tc = r['ts_code']
        fin = next((f for f in financial_data if f['ts_code'] == tc), {})
        
        risks = []
        score = 0
        
        gm = fin.get('q1_gross_margin') or 0
        op_yoy = fin.get('q1_op_yoy') or 0
        debt = fin.get('q1_debt_ratio') or 0
        pe = r['pe']
        
        # 盈利质量
        if gm > 40: score += 5
        elif gm > 30: score += 4
        elif gm > 20: score += 3
        else: score += 1; risks.append(f"毛利率{gm:.0f}%低")
        
        # 成长性
        if op_yoy > 50: score += 5
        elif op_yoy > 20: score += 4
        elif op_yoy > 0: score += 3
        else: score += 1; risks.append(f"营业利润YOY={op_yoy:.0f}%")
        
        # 财务安全
        if debt < 30: score += 5
        elif debt < 50: score += 4
        elif debt < 60: score += 3
        else: score += 1; risks.append(f"负债率{debt:.0f}%")
        
        # 估值
        peg = pe / op_yoy if op_yoy > 0 else 999
        if peg < 1: score += 5
        elif peg < 2: score += 4
        elif peg < 3: score += 3
        else: score += 1; risks.append(f"PEG={peg:.1f}")
        
        # 主线契合度
        score += min(r['sector_count'] * 2, 5)
        
        if len(risks) < 3 and score >= 14:
            passed.append({**r.to_dict(), 'fin_score': score, 'risks': risks,
                         'gm': gm, 'op_yoy': op_yoy, 'debt': debt})
    
    passed.sort(key=lambda x: x['fin_score'], reverse=True)
    print(f"\n✅ 通过基本面过滤 ({len(passed)}只):")
    for i, p in enumerate(passed[:10]):
        print(f"  {i+1}. {p['name']}({p['ts_code']}) 综合{p['fin_score']}分 "
              f"PE={p['pe']:.0f} 毛利率={p['gm']:.0f}% 营业利润YOY={p['op_yoy']:.0f}% "
              f"负债率={p['debt']:.0f}% 主线:{p['matched_sectors']}")


if __name__ == '__main__':
    main()
