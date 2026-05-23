# -*- coding: utf-8 -*-
"""
三步法中军选股 V3 - 生产版
核心逻辑：
  Step1: 主线板块识别（block.py数据库 + 申万行业动量）
  Step2: 中军技术筛选 + 主线匹配
  Step3: 基本面评分（AI算法）+ 二次入选确认
  输出：仅推荐二次入选 + 基本面≥20 + PE≤100的标的
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os, json, pickle, time, hashlib
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

# 确保历史目录存在
os.makedirs(HISTORY_DIR, exist_ok=True)

# ============================================================
# 参数
# ============================================================
PE_MAX = 100
MV_MIN, MV_MAX = 100, 500
MIN_TECH_SCORE = 60
FIN_SCORE_MIN = 20      # 基本面最低20分（回测验证：≥20分胜率57%）
MIN_REPEAT = 2          # 必须二次入选

# ============================================================
# 历史记录管理（二次入选核心）
# ============================================================
def get_history_path():
    """按月存储历史"""
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
    """记录当日选股结果"""
    history = load_history()
    # 去重：同一天不重复记录
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
    """统计每只股票的历史入选次数"""
    counter = defaultdict(int)
    first_date = defaultdict(str)
    for r in history['records']:
        for tc in r['ts_codes']:
            counter[tc] += 1
            if first_date[tc] == '' or r['date'] < first_date[tc]:
                first_date[tc] = r['date']
    return dict(counter), dict(first_date)

def get_all_time_repeats():
    """跨月统计入选次数"""
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
    """从block.py的SQLite获取主线"""
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
    
    # 获取成分股
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()
    
    result = []
    for _, r in df.iterrows():
        name = r['name']
        stocks = set()
        # 申万行业匹配
        if len(sw_df) > 0 and 'l2_name' in sw_df.columns:
            stocks.update(sw_df[sw_df['l2_name'] == name]['ts_code'].dropna().unique().tolist())
        if len(sw_df) > 0 and 'l1_name' in sw_df.columns:
            stocks.update(sw_df[sw_df['l1_name'] == name]['ts_code'].dropna().unique().tolist())
        # 概念匹配
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
    """备用：申万行业动量评分"""
    try:
        df = pro.daily(trade_date=trade_date, fields='ts_code,close,pct_chg,amount')
        if df.empty: return []
    except: return []
    if sw_df.empty or 'l2_name' not in sw_df.columns: return []
    sw_merge = sw_df[['ts_code', 'l2_name']].dropna(subset=['l2_name'])
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
# AI基本面评分算法
# ============================================================
def ai_financial_score(fin, pe, sector_count=1, tech_score=0):
    """
    AI分析引擎 - 基于回测验证的评分体系
    
    回测验证结论:
    - 基本面≥20分: 胜率57%, 均收+1.79%
    - 16-19分: 胜率40%, 均收+0.30%
    - 14-15分: 胜率20%, 均收-2.53%
    - 二次入选: 胜率62%, 均收+3.67%
    - 盈亏比1.53关键在于-5%止损
    
    评分逻辑(满分30):
    A. 盈利质量(0-8): 毛利率+净利率+趋势
    B. 成长确定性(0-8): 营业利润YOY+营收质量
    C. 财务安全(0-8): 负债率+现金流
    D. 估值安全(0-6): PEG+PE绝对值
    """
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0
    debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or')
    roe = fin.get('roe') or 0
    
    s = 0
    detail = {}
    
    # ---- A. 盈利质量 (0-8) ----
    gm_s = 0
    if gm > 50: gm_s = 5
    elif gm > 40: gm_s = 4
    elif gm > 30: gm_s = 3
    elif gm > 20: gm_s = 2
    else: gm_s = 1
    
    # 净利率加分
    nm_s = 0
    if nm > 20: nm_s = 3
    elif nm > 15: nm_s = 2
    elif nm > 10: nm_s = 1
    
    # 毛利率趋势(同比): 暂无数据，跳过
    s += gm_s + nm_s
    detail['盈利质量'] = gm_s + nm_s
    
    # ---- B. 成长确定性 (0-8) ----
    # 核心判断：营业利润YOY是否可持续
    growth_s = 0
    if op_yoy > 100:
        growth_s = 5  # 爆发但需验证可持续性
    elif op_yoy > 50:
        growth_s = 6  # 高增且更可靠
    elif op_yoy > 30:
        growth_s = 5  # 稳健高增
    elif op_yoy > 10:
        growth_s = 4  # 温和增长
    elif op_yoy > 0:
        growth_s = 2  # 微增
    elif op_yoy > -10:
        growth_s = 1  # 微降
    else:
        growth_s = 0  # 大降
    
    # ROE辅助验证成长质量
    roe_s = 0
    if roe > 10: roe_s = 2
    elif roe > 5: roe_s = 1
    
    s += growth_s + roe_s
    detail['成长确定性'] = growth_s + roe_s
    
    # ---- C. 财务安全 (0-8) ----
    debt_s = 0
    if debt < 20: debt_s = 5
    elif debt < 30: debt_s = 4
    elif debt < 40: debt_s = 3
    elif debt < 50: debt_s = 2
    elif debt < 60: debt_s = 1
    else: debt_s = 0
    
    ocf_s = 0
    if ocf is not None:
        if ocf > 0.15: ocf_s = 3
        elif ocf > 0.05: ocf_s = 2
        elif ocf > 0: ocf_s = 1
        else: ocf_s = -2  # 现金流为负严重扣分
    
    s += debt_s + ocf_s
    detail['财务安全'] = debt_s + ocf_s
    
    # ---- D. 估值安全 (0-6) ----
    peg = pe / op_yoy if op_yoy > 0 else 999
    peg_s = 0
    if peg < 0.5: peg_s = 6
    elif peg < 1: peg_s = 5
    elif peg < 1.5: peg_s = 4
    elif peg < 2: peg_s = 3
    elif peg < 3: peg_s = 2
    else: peg_s = 1
    
    # PE绝对值安全线
    pe_s = 0
    if pe < 30: pe_s = 0  # 低PE可能是周期股陷阱，不额外加分
    elif pe < 50: pe_s = 0
    elif pe < 80: pe_s = 0
    else: pe_s = -1  # PE>80扣分
    
    s += peg_s + pe_s
    detail['估值安全'] = peg_s + pe_s
    
    total = max(0, min(s, 30))
    return total, detail

def ai_generate_verdict(name, ts_code, fin_score, detail, fin, pe, sector_count, 
                         repeat_count, tech_score, buy_price, mv_yi):
    """AI生成投资建议"""
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0
    debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or')
    roe = fin.get('roe') or 0
    
    # 综合评级
    if fin_score >= 24 and sector_count >= 2:
        rating = "⭐⭐⭐ 强烈买入"
    elif fin_score >= 22 and repeat_count >= 2:
        rating = "⭐⭐ 买入"
    elif fin_score >= 20 and repeat_count >= 2:
        rating = "✅ 谨慎买入"
    elif fin_score >= 20:
        rating = "👀 观望(等二次入选)"
    else:
        rating = "❌ 淘汰"
    
    # 风险点
    risks = []
    if gm < 30: risks.append(f"毛利率{gm:.0f}%偏低")
    if op_yoy < 0: risks.append(f"营业利润下滑{op_yoy:.0f}%")
    if debt > 50: risks.append(f"负债率{debt:.0f}%偏高")
    if ocf is not None and ocf < 0: risks.append("经营现金流为负")
    if pe > 80: risks.append(f"PE={pe:.0f}估值偏高")
    if nm < 5: risks.append(f"净利率{nm:.0f}%过低")
    
    # 亮点
    highlights = []
    if gm > 40: highlights.append(f"毛利率{gm:.0f}%优秀")
    if op_yoy > 30: highlights.append(f"营业利润+{op_yoy:.0f}%高增")
    if debt < 30: highlights.append(f"负债率{debt:.0f}%极低")
    if ocf is not None and ocf > 0.1: highlights.append("现金流健康")
    if sector_count >= 2: highlights.append(f"匹配{sector_count}条主线")
    if repeat_count >= 3: highlights.append(f"第{repeat_count}次入选(趋势确认)")
    
    # 目标仓位
    if rating.startswith("⭐⭐⭐"):
        pos = "30%"
    elif rating.startswith("⭐⭐"):
        pos = "20%"
    elif rating.startswith("✅"):
        pos = "10%"
    else:
        pos = "0%"
    
    # 止损建议
    stop_loss = round(buy_price * 0.95, 2)
    
    verdict = {
        'name': name, 'ts_code': ts_code, 'rating': rating,
        'fin_score': fin_score, 'detail': detail,
        'highlights': highlights, 'risks': risks,
        'position': pos, 'stop_loss': stop_loss,
        'repeat': repeat_count
    }
    return verdict

# ============================================================
# Step2: 中军技术筛选
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
            ma5,ma10,ma20,ma60 = calc_ma(c,5),calc_ma(c,10),calc_ma(c,20),calc_ma(c,60)

            score = 0
            if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]: score += 25
            if ma20.iloc[-1] > ma60.iloc[-1]: score += 20
            vr = v / v.rolling(20).mean()
            if vr.iloc[-3:].mean() > 1.3: score += 15
            if c.iloc[-1] > h.iloc[-21:-1].max() * 0.98: score += 15
            pos120 = (c.iloc[-1] - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: score += 10
            pct5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: score += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if (rh-rl)/rl*100 < 25: score += 5

            if score < MIN_TECH_SCORE: continue

            sector_count = sum(1 for hs in hot_sectors if tc in hs['stocks'])
            sector_names = [hs['name'] for hs in hot_sectors if tc in hs['stocks']]

            results.append({
                'ts_code': tc, 'close': c.iloc[-1], 'mv_yi': row['mv_yi'],
                'pe': pe, 'tech_score': score, 'pct_5d': round(pct5,2),
                'price_pos_120': round(pos120,1), 'turnover_rate': row['turnover_rate'],
                'sector_count': sector_count, 'sector_names': '; '.join(sector_names),
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
    print("三步法中军选股 V3 - 生产版")
    print(f"PE≤{PE_MAX} | 基本面≥{FIN_SCORE_MIN} | 二次入选确认 | -5%止损")
    print("=" * 80)

    # 获取最新交易日
    cal = pro.trade_cal(exchange='SSE', is_open=1, 
        start_date=(datetime.now()-timedelta(days=10)).strftime('%Y%m%d'),
        end_date=datetime.now().strftime('%Y%m%d'))
    trade_date = cal.sort_values('cal_date').iloc[-1]['cal_date']
    print(f"交易日期: {trade_date}")

    # 加载映射
    print("加载概念/行业映射...")
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()

    # Step1: 主线板块
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

    # Step2: 中军筛选
    print("\n" + "="*60)
    print("Step2: 中军技术筛选 + 主线匹配")
    print("="*60)
    
    zhongjun = screen_zhongjun(trade_date, hot)
    print(f"技术筛选: {len(zhongjun)}只中军候选")

    if zhongjun.empty:
        print("⚠️ 今日无合格中军")
        return

    # Step3: AI基本面评分 + 二次入选确认
    print("\n" + "="*60)
    print("Step3: AI基本面评分 + 二次入选确认")
    print("="*60)

    # 获取历史入选次数
    repeat_counter, first_date = get_all_time_repeats()

    # 为每只候选评分
    all_picks = []
    for _, r in zhongjun.iterrows():
        tc = r['ts_code']
        fin = get_fin(tc)
        fin_score, detail = ai_financial_score(fin, r['pe'], r['sector_count'], r['tech_score'])
        repeat = repeat_counter.get(tc, 0) + 1  # +1包含本次
        
        verdict = ai_generate_verdict(
            r['name'], tc, fin_score, detail, fin, r['pe'],
            r['sector_count'], repeat, r['tech_score'], r['close'], r['mv_yi']
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
    
    # 记录本次选股到历史
    today_picks = [{'ts_code': r['ts_code'], 'name': r['name']} for _, r in picks_df.iterrows()]
    repeat_counter_new, first_date_new = record_selection(trade_date, today_picks)

    # ---- 输出分级结果 ----
    
    # A级: 二次入选 + 基本面≥20 → 可买入
    grade_a = picks_df[(picks_df['repeat'] >= MIN_REPEAT) & (picks_df['fin_score'] >= FIN_SCORE_MIN)]
    grade_a = grade_a.sort_values(['fin_score', 'repeat', 'sector_count'], ascending=False)
    
    # B级: 基本面≥20 但首次入选 → 观望等二次
    grade_b = picks_df[(picks_df['repeat'] < MIN_REPEAT) & (picks_df['fin_score'] >= FIN_SCORE_MIN)]
    grade_b = grade_b.sort_values('fin_score', ascending=False)
    
    # C级: 基本面<20 → 淘汰
    grade_c = picks_df[picks_df['fin_score'] < FIN_SCORE_MIN]

    print(f"\n📊 今日筛选汇总:")
    print(f"  技术合格: {len(picks_df)}只")
    print(f"  🟢 A级(可买入, 二次入选+基本面≥20): {len(grade_a)}只")
    print(f"  🟡 B级(观望, 基本面≥20但首次入选): {len(grade_b)}只")
    print(f"  🔴 C级(淘汰, 基本面<20): {len(grade_c)}只")

    # ---- A级详细输出 ----
    if len(grade_a) > 0:
        print(f"\n{'='*60}")
        print(f"🟢 A级 - 可买入标的 ({len(grade_a)}只)")
        print(f"{'='*60}")
        for _, r in grade_a.iterrows():
            v = r['verdict']
            print(f"\n  {v['rating']}")
            print(f"  {r['name']}({r['ts_code']}) | 现价{r['close']} PE={r['pe']:.0f} 市值{r['mv_yi']:.0f}亿")
            print(f"  基本面={r['fin_score']}分 | 技术={r['tech_score']} | 匹配{r['sector_count']}条主线 | 第{r['repeat']}次入选")
            print(f"  评分明细: {r['detail']}")
            if v['highlights']:
                print(f"  ✅ {' | '.join(v['highlights'])}")
            if v['risks']:
                print(f"  ⚠️ {' | '.join(v['risks'])}")
            print(f"  💰 建议仓位:{v['position']} | 止损价:{v['stop_loss']}(-5%)")

    # ---- B级简要输出 ----
    if len(grade_b) > 0:
        print(f"\n{'='*60}")
        print(f"🟡 B级 - 观望(等二次入选) ({len(grade_b)}只)")
        print(f"{'='*60}")
        for _, r in grade_b.iterrows():
            v = r['verdict']
            print(f"  {r['name']}({r['ts_code']}) 基本面={r['fin_score']} PE={r['pe']:.0f} "
                  f"技术={r['tech_score']} 匹配{r['sector_count']}线 {v['rating']}")
            print(f"    → 等明日再次入选即可升级为A级买入")

    # ---- C级简要 ----
    if len(grade_c) > 0:
        print(f"\n{'='*60}")
        print(f"🔴 C级 - 淘汰 ({len(grade_c)}只)")
        print(f"{'='*60}")
        c_names = [f"{r['name']}({r['fin_score']}分)" for _, r in grade_c.iterrows()]
        print(f"  {', '.join(c_names[:10])}")

    # ---- 持仓监控 ----
    print(f"\n{'='*60}")
    print("📋 历史入选≥2次标的跟踪")
    print(f"{'='*60}")
    multi = {tc: cnt for tc, cnt in repeat_counter_new.items() if cnt >= 2}
    if multi:
        # 获取实时价格
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

    # ---- 保存 ----
    out = os.path.join(BASE_DIR, f'screen_result_{trade_date}.csv')
    out_df = picks_df.drop(columns=['verdict', 'detail'], errors='ignore')
    out_df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n💾 结果保存: {out}")
    
    # 摘要输出
    if len(grade_a) > 0:
        print(f"\n{'='*60}")
        print("📌 今日操作摘要")
        print(f"{'='*60}")
        for _, r in grade_a.iterrows():
            v = r['verdict']
            print(f"  {v['rating'].split()[0]} {r['name']}({r['ts_code']}) "
                  f"买入≤{r['close']} 仓位{v['position']} 止损{v['stop_loss']}")

if __name__ == '__main__':
    main()
