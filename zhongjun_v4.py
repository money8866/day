# -*- coding: utf-8 -*-
"""
三步法中军选股 V4 - 生产版（回档买优化）
核心改进 vs V3:
  1. 技术评分加入回档类型判断（回档MA5/MA10 > 突破买 > 远离均线）
  2. AI建议区分：回档买（立即买）vs 突破买（等回档）
  3. 输出建议买点价位（MA5/MA10具体数值）
  4. 回测验证：突破买改为等回档后买入，提升胜率
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os, json, pickle, time
import tushare as ts
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = r'C:\Users\kongx\mystock'
CACHE_DIR = os.path.join(BASE_DIR, 'cache_daily')
HISTORY_DIR = os.path.join(BASE_DIR, 'screen_history')

# ============================================================
# 初始化
# ============================================================
env = Path(os.path.join(BASE_DIR, '.env')).read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=',1)[1].strip())
pro = ts.pro_api()
os.makedirs(HISTORY_DIR, exist_ok=True)

# ============================================================
# 参数
# ============================================================
PE_MAX = 100
MV_MIN, MV_MAX = 100, 500
MIN_TECH_SCORE = 60
FIN_SCORE_MIN = 20
MIN_REPEAT = 2

# 回档买参数
PULLBACK_TOLERANCE = 0.03   # MA±3%视为回档
FAR_FROM_MA_THRESHOLD = 1.10  # 价格>MA*1.10视为远离

# ============================================================
# 历史记录管理
# ============================================================
def get_history_path():
    return os.path.join(HISTORY_DIR, f"history_{datetime.now().strftime('%Y%m')}.json")

def load_history():
    path = get_history_path()
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'records': []}

def save_history(history):
    path = get_history_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def record_selection(date_str, picks):
    history = load_history()
    existing_dates = [r['date'] for r in history['records']]
    if date_str in existing_dates:
        return count_repeats_from_history(history)
    history['records'].append({
        'date': date_str,
        'ts_codes': [p['ts_code'] for p in picks],
        'names': {p['ts_code']: p['name'] for p in picks},
    })
    save_history(history)
    return count_repeats_from_history(history)

def count_repeats_from_history(history):
    counter = defaultdict(int)
    first_date = defaultdict(str)
    for r in history['records']:
        for tc in r['ts_codes']:
            counter[tc] += 1
            if first_date[tc] == '' or r['date'] < first_date[tc]:
                first_date[tc] = r['date']
    return dict(counter), dict(first_date)

def get_all_time_repeats():
    counter = defaultdict(int)
    first_date = defaultdict(str)
    for fname in os.listdir(HISTORY_DIR):
        if fname.startswith('history_') and fname.endswith('.json'):
            with open(os.path.join(HISTORY_DIR, fname), 'r', encoding='utf-8') as f:
                h = json.load(f)
                for r in h['records']:
                    for tc in r['ts_codes']:
                        counter[tc] += 1
                        if first_date[tc] == '' or r['date'] < first_date[tc]:
                            first_date[tc] = r['date']
    return dict(counter), dict(first_date)

# ============================================================
# 财务数据缓存
# ============================================================
_fin_cache = {}

def get_fin(ts_code):
    if ts_code in _fin_cache:
        return _fin_cache[ts_code]
    try:
        fi = pro.fina_indicator(ts_code=ts_code, period='20260331',
            fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
        d = fi.iloc[0].to_dict() if len(fi) > 0 else {}
        _fin_cache[ts_code] = d
        time.sleep(0.12)
        return d
    except:
        time.sleep(0.3)
        return {}

# ============================================================
# 辅助函数
# ============================================================
def calc_ma(s, n): return s.rolling(n).mean()

def load_maps():
    cs_path = os.path.join(CACHE_DIR, 'concept_stock_map.pkl')
    concept_map = pickle.load(open(cs_path,'rb')) if os.path.exists(cs_path) else {}
    theme_path = os.path.join(BASE_DIR, 'theme_map.json')
    theme_map = json.load(open(theme_path,'r',encoding='utf-8'))
    sc_path = os.path.join(CACHE_DIR, 'stock_concept_map.pkl')
    stock_concept_map = pickle.load(open(sc_path,'rb')) if os.path.exists(sc_path) else {}
    sw_path = os.path.join(CACHE_DIR, 'sw_map.csv')
    sw_df = pd.read_csv(sw_path, dtype=str) if os.path.exists(sw_path) else pd.DataFrame()
    return concept_map, theme_map, stock_concept_map, sw_df

def get_hot_sectors_from_db(top_n=10):
    db_path = os.path.join(CACHE_DIR, 'hot_sector.db')
    import sqlite3
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    latest = pd.read_sql("SELECT MAX(date) as d FROM hot_sector", conn)
    if latest.empty or latest.iloc[0]['d'] is None:
        conn.close()
        return []
    latest_date = latest.iloc[0]['d']
    df = pd.read_sql(f"SELECT * FROM hot_sector WHERE date='{latest_date}' ORDER BY rank ASC LIMIT {top_n}", conn)
    conn.close()
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()
    result = []
    for _, r in df.iterrows():
        name = r['name']
        stocks = set()
        if len(sw_df) > 0 and 'l2_name' in sw_df.columns:
            stocks.update(sw_df[sw_df['l2_name'] == name]['ts_code'].dropna().unique().tolist())
        if len(sw_df) > 0 and 'l1_name' in sw_df.columns:
            stocks.update(sw_df[sw_df['l1_name'] == name]['ts_code'].dropna().unique().tolist())
        for cn, cl in concept_map.items():
            if name in cn or any(kw in cn for kw in name.split('/')):
                stocks.update(cl)
        for tc, concepts in stock_concept_map.items():
            if name in str(concepts):
                stocks.add(tc)
        result.append({'name': name, 'score': r['score'], 'momentum': r.get('momentum', 0),
                       'leader': r.get('leader_name',''), 'stocks': stocks})
    return result

def get_hot_industries_fallback(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8):
    try:
        df = pro.daily(trade_date=trade_date, fields='ts_code,close,pct_chg,amount')
        if df.empty: return []
    except: return []
    if sw_df.empty or 'l2_name' not in sw_df.columns: return []
    sw_merge = sw_df[['ts_code','l2_name']].dropna(subset=['l2_name'])
    df = df.merge(sw_merge, on='ts_code', how='left').dropna(subset=['l2_name'])
    if df.empty: return []
    ip = df.groupby('l2_name').agg(
        avg_pct=('pct_chg','mean'), total_amount=('amount','sum'),
        stock_count=('ts_code','count'), up_ratio=('pct_chg',lambda x:(x>0).mean()),
        limit_up=('pct_chg',lambda x:(x>=9.5).sum())
    ).reset_index()
    ip = ip[ip['stock_count']>=5]
    ip['score'] = ip['avg_pct']*1.5 + ip['limit_up']*3 + ip['up_ratio']*8 + np.log1p(ip['total_amount']/1e8)*2
    top = ip.sort_values('score', ascending=False).head(top_n)
    result = []
    for _, r in top.iterrows():
        ind = r['l2_name']
        stocks = set(sw_df[sw_df['l2_name']==ind]['ts_code'].dropna().unique().tolist())
        for cn, cl in concept_map.items():
            if ind in cn: stocks.update(cl)
        for tc, concepts in stock_concept_map.items():
            if ind in str(concepts): stocks.add(tc)
        result.append({'name':ind, 'score':r['score'], 'momentum':0, 'leader':'', 'stocks':stocks})
    return result

# ============================================================
# 回档类型判断（V4核心新增）
# ============================================================
def detect_entry_type(price, ma5, ma10, ma20, ma60, high_21):
    """
    判断买入时机类型
    返回: (entry_type, entry_score_bonus, suggestion)
    entry_type: 'pullback_ma5' | 'pullback_ma10' | 'breakout' | 'far_away' | 'unknown'
    """
    # 价格在MA5±3%内 → 回档MA5（最优）
    if ma5 * (1 - PULLBACK_TOLERANCE) <= price <= ma5 * (1 + PULLBACK_TOLERANCE):
        return 'pullback_ma5', 10, f'回档MA5买点={price:.2f}'
    
    # 价格在MA10±3%内 → 回档MA10（次优）
    if ma10 * (1 - PULLBACK_TOLERANCE) <= price <= ma10 * (1 + PULLBACK_TOLERANCE):
        return 'pullback_ma10', 5, f'回档MA10买点={price:.2f}'
    
    # 价格突破21日高点98% → 突破买（风险高，等回档）
    if price > high_21 * 0.98:
        # 但如果在MA5附近，仍算回档
        if price <= ma5 * 1.05:
            return 'breakout_near_ma', 0, f'突破后贴近MA5，可买，MA5={ma5:.2f}'
        return 'breakout', -5, f'突破状态，等回档MA5={ma5:.2f}或MA10={ma10:.2f}再买'
    
    # 价格远离均线 → 不买，等回踩
    if price > ma5 * FAR_FROM_MA_THRESHOLD:
        return 'far_away', -10, f'远离MA5({ma5:.2f})，等回踩再买'
    
    # 价格在MA10和MA5之间
    if ma10 < price < ma5:
        return 'between_ma', 3, f'介于MA5({ma5:.2f})和MA10({ma10:.2f})之间，可买'
    
    return 'unknown', 0, '观察'

# ============================================================
# AI基本面评分算法
# ============================================================
def ai_financial_score(fin, pe, sector_count=1, tech_score=0):
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0
    debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or')
    roe = fin.get('roe') or 0
    
    s = 0
    detail = {}
    
    # A. 盈利质量
    gm_s = 5 if gm > 50 else (4 if gm > 40 else (3 if gm > 30 else (2 if gm > 20 else 1)))
    nm_s = 3 if nm > 20 else (2 if nm > 15 else (1 if nm > 10 else 0))
    s += gm_s + nm_s
    detail['盈利质量'] = gm_s + nm_s
    
    # B. 成长确定性
    if op_yoy > 100: growth_s = 5
    elif op_yoy > 50: growth_s = 6
    elif op_yoy > 30: growth_s = 5
    elif op_yoy > 10: growth_s = 4
    elif op_yoy > 0: growth_s = 2
    elif op_yoy > -10: growth_s = 1
    else: growth_s = 0
    
    roe_s = 2 if roe > 10 else (1 if roe > 5 else 0)
    s += growth_s + roe_s
    detail['成长确定性'] = growth_s + roe_s
    
    # C. 财务安全
    if debt < 20: debt_s = 5
    elif debt < 30: debt_s = 4
    elif debt < 40: debt_s = 3
    elif debt < 50: debt_s = 2
    elif debt < 60: debt_s = 1
    else: debt_s = 0
    
    ocf_s = 3 if (ocf is not None and ocf > 0.15) else (
              2 if (ocf is not None and ocf > 0.05) else (
              1 if (ocf is not None and ocf > 0) else (
              -2 if (ocf is not None and ocf <= 0) else 0)))
    s += debt_s + ocf_s
    detail['财务安全'] = debt_s + ocf_s
    
    # D. 估值安全
    peg = pe / op_yoy if op_yoy > 0 else 999
    if peg < 0.5: peg_s = 6
    elif peg < 1: peg_s = 5
    elif peg < 1.5: peg_s = 4
    elif peg < 2: peg_s = 3
    elif peg < 3: peg_s = 2
    else: peg_s = 1
    
    pe_s = -1 if pe > 80 else 0
    s += peg_s + pe_s
    detail['估值安全'] = peg_s + pe_s
    
    total = max(0, min(s, 30))
    return total, detail

def ai_generate_verdict(name, ts_code, fin_score, detail, fin, pe, sector_count,
                         repeat_count, tech_score, buy_price, mv_yi, entry_type='unknown'):
    """AI生成投资建议（V4: 加入回档类型判断）"""
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0
    debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or')
    roe = fin.get('roe') or 0
    
    # V4: 根据回档类型调整评级
    if fin_score >= 24 and sector_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "⭐⭐⭐ 强烈买入(回档买)"
    elif fin_score >= 22 and repeat_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "⭐⭐ 买入(回档买)"
    elif fin_score >= 20 and repeat_count >= 2 and entry_type in ('pullback_ma5', 'pullback_ma10'):
        rating = "✅ 谨慎买入(回档买)"
    elif fin_score >= 20 and repeat_count >= 2 and entry_type == 'breakout':
        rating = "⏳ 突破状态，等回档再买"
    elif fin_score >= 20 and repeat_count >= 2:
        rating = "✅ 谨慎买入"
    elif fin_score >= 20:
        rating = "👀 观望(等二次入选)"
    else:
        rating = "❌ 淘汰"
    
    risks = []
    if gm < 30: risks.append(f"毛利率{gm:.0f}%偏低")
    if op_yoy < 0: risks.append(f"营业利润下滑{op_yoy:.0f}%")
    if debt > 50: risks.append(f"负债率{debt:.0f}%偏高")
    if ocf is not None and ocf < 0: risks.append("经营现金流为负")
    if pe > 80: risks.append(f"PE={pe:.0f}估值偏高")
    if nm < 5: risks.append(f"净利率{nm:.0f}%过低")
    if entry_type == 'far_away': risks.append("价格远离均线，追高风险大")
    
    highlights = []
    if gm > 40: highlights.append(f"毛利率{gm:.0f}%优秀")
    if op_yoy > 30: highlights.append(f"营业利润+{op_yoy:.0f}%高增")
    if debt < 30: highlights.append(f"负债率{debt:.0f}%极低")
    if ocf is not None and ocf > 0.1: highlights.append("现金流健康")
    if sector_count >= 2: highlights.append(f"匹配{sector_count}条主线")
    if repeat_count >= 3: highlights.append(f"第{repeat_count}次入选(趋势确认)")
    if entry_type == 'pullback_ma5': highlights.append("🎯 回档MA5，买点极佳")
    elif entry_type == 'pullback_ma10': highlights.append("🎯 回档MA10，买点良好")
    
    # 仓位建议（回档买可加仓）
    if '强烈买入(回档买)' in rating:
        pos = "30%"
    elif '买入(回档买)' in rating:
        pos = "25%"
    elif '谨慎买入(回档买)' in rating:
        pos = "15%"
    elif '等回档再买' in rating:
        pos = "0%（等回档）"
    elif '谨慎买入' in rating:
        pos = "10%"
    else:
        pos = "0%"
    
    stop_loss = round(buy_price * 0.95, 2)
    
    verdict = {
        'name': name, 'ts_code': ts_code, 'rating': rating,
        'fin_score': fin_score, 'detail': detail,
        'highlights': highlights, 'risks': risks,
        'position': pos, 'stop_loss': stop_loss,
        'repeat': repeat_count, 'entry_type': entry_type,
    }
    return verdict

# ============================================================
# Step2: 中军技术筛选（V4: 加入回档判断）
# ============================================================
def screen_zhongjun(trade_date, hot_sectors):
    try:
        basic = pro.daily_basic(trade_date=trade_date,
            fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    except: return pd.DataFrame()
    if basic.empty: return pd.DataFrame()

    basic['mv_yi'] = basic['total_mv'] / 10000
    cands = basic[(basic['mv_yi'] >= MV_MIN) & (basic['mv_yi'] <= MV_MAX)].copy()
    cands = cands[~cands['ts_code'].str.startswith(('8','4','9'))]
    cands = cands[(cands['pe'] > 0) & (cands['pe'] <= PE_MAX)]

    all_sector_stocks = set()
    for hs in hot_sectors:
        all_sector_stocks.update(hs['stocks'])
    cands = cands[cands['ts_code'].isin(all_sector_stocks)]

    start = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=200)).strftime('%Y%m%d')
    results = []

    for _, row in cands.iterrows():
        tc = row['ts_code']
        pe = row['pe']
        try:
            df = pro.daily(ts_code=tc, start_date=start, end_date=trade_date)
            if len(df) < 60: continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            c, h, l, v = df['close'], df['high'], df['low'], df['vol']
            ma5 = calc_ma(c,5).iloc[-1]
            ma10 = calc_ma(c,10).iloc[-1]
            ma20 = calc_ma(c,20).iloc[-1]
            ma60 = calc_ma(c,60).iloc[-1]
            price = c.iloc[-1]

            score = 0
            if ma5 > ma10 > ma20: score += 25
            if ma20 > ma60: score += 20
            vr = v / v.rolling(20).mean()
            if vr.iloc[-3:].mean() > 1.3: score += 15
            high_21 = h.iloc[-21:-1].max()

            # V4: 先判定回档类型
            entry_type, entry_bonus, entry_suggestion = detect_entry_type(
                price, ma5, ma10, ma20, ma60, high_21)
            score += entry_bonus

            # 过滤：far_away/breakout类型必须已突破21日高点，回档类豁免
            if entry_type in ('far_away', 'breakout') and price <= high_21 * 0.98:
                continue

            pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: score += 10
            pct5 = (price / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: score += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if (rh-rl)/rl*100 < 25: score += 5

            if score < MIN_TECH_SCORE: continue

            sector_count = sum(1 for hs in hot_sectors if tc in hs["stocks"])
            sector_names = [hs['name'] for hs in hot_sectors if tc in hs['stocks']]

            results.append({
                'ts_code': tc, 'close': price, 'mv_yi': row['mv_yi'],
                'pe': pe, 'tech_score': score, 'pct_5d': round(pct5,2),
                'price_pos_120': round(pos120,1), 'turnover_rate': row['turnover_rate'],
                'sector_count': sector_count, 'sector_names': '; '.join(sector_names),
                'entry_type': entry_type, 'entry_suggestion': entry_suggestion,
                'ma5': round(ma5,2), 'ma10': round(ma10,2),
            })
            time.sleep(0.12)
        except: continue

    if not results: return pd.DataFrame()
    rdf = pd.DataFrame(results)
    sb = pro.stock_basic(fields='ts_code,name')
    rdf['name'] = rdf['ts_code'].map(dict(zip(sb['ts_code'], sb['name'])))
    return rdf

# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 80)
    print("三步法中军选股 V4 - 生产版（回档买优化）")
    print(f"PE≤{PE_MAX} | 基本面≥{FIN_SCORE_MIN} | 二次入选确认 | 回档买优先 | -5%止损")
    print("=" * 80)

    cal = pro.trade_cal(exchange='SSE', is_open=1, 
        start_date=(datetime.now()-timedelta(days=10)).strftime('%Y%m%d'),
        end_date=datetime.now().strftime('%Y%m%d'))
    trade_date = cal.sort_values('cal_date').iloc[-1]['cal_date']
    print(f"交易日期: {trade_date}")

    print("加载概念/行业映射...")
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()

    # Step1
    print("\n" + "="*60)
    print("Step1: 主线板块识别")
    print("="*60)
    hot = get_hot_sectors_from_db(top_n=10)
    source = "block.py数据库"
    if not hot:
        hot = get_hot_industries_fallback(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8)
        source = "申万行业动量(备用)"
    print(f"数据源: {source}")
    total_stocks = len(set().union(*[h['stocks'] for h in hot]))
    for i, h in enumerate(hot[:5]):
        print(f"  #{i+1} {h['name']} | 强度={h['score']:.1f} | 龙头={h.get('leader','')} | {len(h['stocks'])}只")
    print(f"  ... 主线去重: {total_stocks}只")

    # Step2
    print("\n" + "="*60)
    print("Step2: 中军技术筛选 + 主线匹配 + 回档判断")
    print("="*60)
    zhongjun = screen_zhongjun(trade_date, hot)
    print(f"技术筛选: {len(zhongjun)}只中军候选")
    if zhongjun.empty:
        print("⚠️ 今日无合格中军")
        return

    # Step3
    print("\n" + "="*60)
    print("Step3: AI基本面评分 + 二次入选确认")
    print("="*60)
    repeat_counter, first_date = get_all_time_repeats()

    all_picks = []
    for _, r in zhongjun.iterrows():
        tc = r['ts_code']
        fin = get_fin(tc)
        fin_score, detail = ai_financial_score(fin, r['pe'], r['sector_count'], r['tech_score'])
        repeat = repeat_counter.get(tc, 0) + 1
        
        verdict = ai_generate_verdict(
            r['name'], tc, fin_score, detail, fin, r['pe'],
            r['sector_count'], repeat, r['tech_score'], r['close'], r['mv_yi'],
            entry_type=r['entry_type']
        )
        
        all_picks.append({
            **r.to_dict(),
            'fin_score': fin_score,
            'detail': detail,
            'repeat': repeat,
            'first_date': first_date.get(tc, trade_date),
            'verdict': verdict,
        })

    picks_df = pd.DataFrame(all_picks)
    today_picks = [{'ts_code': r['ts_code'], 'name': r['name']} for _, r in picks_df.iterrows()]
    repeat_counter_new, first_date_new = record_selection(trade_date, today_picks)

    # 分级
    grade_a = picks_df[(picks_df['repeat'] >= MIN_REPEAT) & (picks_df['fin_score'] >= FIN_SCORE_MIN)]
    grade_a = grade_a.sort_values(['fin_score', 'repeat', 'sector_count'], ascending=False)
    grade_b = picks_df[(picks_df['repeat'] < MIN_REPEAT) & (picks_df['fin_score'] >= FIN_SCORE_MIN)]
    grade_b = grade_b.sort_values('fin_score', ascending=False)
    grade_c = picks_df[picks_df['fin_score'] < FIN_SCORE_MIN]

    # 按回档类型分组统计
    if len(grade_a) > 0:
        pb_ma5 = grade_a[grade_a['entry_type']=='pullback_ma5']
        pb_ma10 = grade_a[grade_a['entry_type']=='pullback_ma10']
        breakout = grade_a[grade_a['entry_type'].isin(['breakout','breakout_near_ma'])]
        far = grade_a[grade_a['entry_type']=='far_away']
        print(f"\n📊 A级回档类型分布:")
        print(f"  回档MA5(最优): {len(pb_ma5)}只 | 回档MA10(良): {len(pb_ma10)}只")
        print(f"  突破状态: {len(breakout)}只 | 远离均线: {len(far)}只")

    print(f"\n📊 今日筛选汇总:")
    print(f"  技术合格: {len(picks_df)}只")
    print(f"  🟢 A级(可买入): {len(grade_a)}只")
    print(f"  🟡 B级(观望): {len(grade_b)}只")
    print(f"  🔴 C级(淘汰): {len(grade_c)}只")

    # 输出A级
    if len(grade_a) > 0:
        print(f"\n{'='*60}")
        print(f"🟢 A级 - 可买入标的 ({len(grade_a)}只)")
        print(f"{'='*60}")
        for _, r in grade_a.iterrows():
            v = r['verdict']
            et = r['entry_type']
            ma5_str = f"MA5={r['ma5']:.2f}" if pd.notna(r['ma5']) else ""
            ma10_str = f"MA10={r['ma10']:.2f}" if pd.notna(r['ma10']) else ""
            print(f"\n  {v['rating']}")
            print(f"  {r['name']}({r['ts_code']}) | 现价{r['close']} PE={r['pe']:.0f} 市值{r['mv_yi']:.0f}亿")
            print(f"  基本面={r['fin_score']}分 | 技术={r['tech_score']} | 匹配{r['sector_count']}条主线 | 第{r['repeat']}次入选")
            print(f"  回档类型: {et} | {r['entry_suggestion']}")
            print(f"  均线: {ma5_str}  {ma10_str}")
            print(f"  评分明细: {r['detail']}")
            if v['highlights']:
                print(f"  ✅ {' | '.join(v['highlights'])}")
            if v['risks']:
                print(f"  ⚠️ {' | '.join(v['risks'])}")
            print(f"  💰 建议仓位:{v['position']} | 止损价:{v['stop_loss']}(-5%)")
            if et == 'breakout':
                print(f"  📌 建议: 等回档到MA5={r['ma5']:.2f}或MA10={r['ma10']:.2f}再买入！")

    if len(grade_b) > 0:
        print(f"\n{'='*60}")
        print(f"🟡 B级 - 观望(等二次入选) ({len(grade_b)}只)")
        print(f"{'='*60}")
        for _, r in grade_b.iterrows():
            v = r['verdict']
            print(f"  {r['name']}({r['ts_code']}) 基本面={r['fin_score']} PE={r['pe']:.0f} "
                  f"技术={r['tech_score']} 匹配{r['sector_count']}线 {v['rating']}")

    if len(grade_c) > 0:
        print(f"\n{'='*60}")
        print(f"🔴 C级 - 淘汰 ({len(grade_c)}只)")
        print(f"{'='*60}")
        c_names = [f"{r['name']}({r['fin_score']}分)" for _, r in grade_c.iterrows()]
        print(f"  {', '.join(c_names[:10])}")

    # 持仓监控
    print(f"\n{'='*60}")
    print("📋 历史入选≥2次标的跟踪")
    print(f"{'='*60}")
    multi = {tc: cnt for tc, cnt in repeat_counter_new.items() if cnt >= 2}
    if multi:
        for tc, cnt in sorted(multi.items(), key=lambda x: -x[1])[:10]:
            try:
                name_df = pro.stock_basic(ts_code=tc, fields='ts_code,name')
                name = name_df.iloc[0]['name'] if len(name_df) > 0 else tc
                last = pro.daily(ts_code=tc, start_date=trade_date, end_date=trade_date)
                price_str = f"现价{last.iloc[0]['close']}" if len(last) > 0 else ""
                print(f"  {name}({tc}) 累计入选{cnt}次 首次{first_date_new.get(tc,'?')} {price_str}")
            except:
                print(f"  {tc} 累计入选{cnt}次")
    else:
        print("  暂无二次入选记录，需连续运行2天以上")

    out = os.path.join(BASE_DIR, f'screen_result_{trade_date}.csv')
    out_df = picks_df.drop(columns=['verdict', 'detail'], errors='ignore')
    out_df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n💾 结果保存: {out}")

    if len(grade_a) > 0:
        print(f"\n{'='*60}")
        print("📌 今日操作摘要")
        print(f"{'='*60}")
        for _, r in grade_a.iterrows():
            v = r['verdict']
            et = r['entry_type']
            if '等回档' in v['rating']:
                print(f"  ⏳ {r['name']}({r['ts_code']}) {v['rating']}")
                print(f"     建议回档买点: MA5={r['ma5']:.2f} MA10={r['ma10']:.2f}")
            else:
                print(f"  {v['rating'].split()[0]} {r['name']}({r['ts_code']}) "
                      f"买入≤{r['close']} 仓位{v['position']} 止损{v['stop_loss']}")

if __name__ == '__main__':
    main()
