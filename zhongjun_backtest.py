# -*- coding: utf-8 -*-
"""
三步法中军选股 - 一个月回测（本地过滤，不调DeepSeek）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os, json, pickle
import tushare as ts
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = r'C:\Users\kongx\mystock'
CACHE_DIR = os.path.join(BASE_DIR, 'cache_daily')

env = Path(os.path.join(BASE_DIR, '.env')).read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=',1)[1].strip())

pro = ts.pro_api()

# ============================================================
# 辅助函数
# ============================================================
def get_trade_dates(n=30):
    end = datetime.now().strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', is_open=1, end_date=end, limit=n)
    return sorted(cal['cal_date'].tolist())

def calc_ma(s, n): return s.rolling(n).mean()

def load_maps():
    """加载概念/行业映射"""
    cs_path = os.path.join(CACHE_DIR, 'concept_stock_map.pkl')
    concept_map = {}
    if os.path.exists(cs_path):
        with open(cs_path, 'rb') as f:
            concept_map = pickle.load(f)
    
    theme_path = os.path.join(BASE_DIR, 'theme_map.json')
    with open(theme_path, 'r', encoding='utf-8') as f:
        theme_map = json.load(f)
    
    sc_path = os.path.join(CACHE_DIR, 'stock_concept_map.pkl')
    stock_concept_map = {}
    if os.path.exists(sc_path):
        with open(sc_path, 'rb') as f:
            stock_concept_map = pickle.load(f)
    
    sw_path = os.path.join(CACHE_DIR, 'sw_map.csv')
    sw_df = pd.read_csv(sw_path, dtype=str) if os.path.exists(sw_path) else pd.DataFrame()
    
    return concept_map, theme_map, stock_concept_map, sw_df

def get_hot_industries(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8):
    """用申万二级行业涨跌幅替代block.py主线评分（轻量回测）"""
    try:
        df = pro.daily(trade_date=trade_date, fields='ts_code,close,pct_chg,amount')
        if df.empty: return []
    except: return []
    
    # 用sw_df合并行业信息
    if sw_df.empty or 'l2_name' not in sw_df.columns:
        return []
    
    sw_merge = sw_df[['ts_code', 'l2_name']].dropna(subset=['l2_name'])
    df = df.merge(sw_merge, on='ts_code', how='left')
    df = df.dropna(subset=['l2_name'])
    
    if df.empty:
        return []
    
    # 行业平均涨跌
    industry_perf = df.groupby('l2_name').agg(
        avg_pct=('pct_chg', 'mean'),
        total_amount=('amount', 'sum'),
        stock_count=('ts_code', 'count'),
        up_ratio=('pct_chg', lambda x: (x > 0).mean()),
        limit_up=('pct_chg', lambda x: (x >= 9.5).sum())
    ).reset_index()
    industry_perf = industry_perf[industry_perf['stock_count'] >= 5]
    industry_perf['score'] = (
        industry_perf['avg_pct'] * 1.5
        + industry_perf['limit_up'] * 3
        + industry_perf['up_ratio'] * 8
        + np.log1p(industry_perf['total_amount'] / 1e8) * 2
    )
    
    top = industry_perf.sort_values('score', ascending=False).head(top_n)
    result = []
    for _, r in top.iterrows():
        industry = r['l2_name']
        stocks = set()
        stocks.update(sw_df[sw_df['l2_name'] == industry]['ts_code'].dropna().unique().tolist())
        for kw in [industry]:
            for cn, cl in concept_map.items():
                if kw in cn: stocks.update(cl)
        for tc, concepts in stock_concept_map.items():
            if industry in str(concepts): stocks.add(tc)
        result.append({'name': industry, 'score': r['score'], 'stocks': stocks})
    return result

def local_filter_basic(fin_data):
    """本地基本面过滤（替代DeepSeek），返回1-25分"""
    gm = fin_data.get('q1_gross_margin') or 0
    nm = fin_data.get('q1_net_margin') or 0
    op_yoy = fin_data.get('q1_op_yoy') or 0
    debt = fin_data.get('q1_debt_ratio') or 0
    pe = fin_data.get('pe') or 999
    ocf = fin_data.get('q1_ocf_ratio')
    
    s = 0
    # 盈利质量
    if gm > 40: s += 5
    elif gm > 30: s += 4
    elif gm > 20: s += 3
    elif gm > 10: s += 2
    else: s += 1
    
    # 净利率补充
    if nm > 15: s += 1  # bonus
    
    # 成长性
    if op_yoy > 50: s += 5
    elif op_yoy > 20: s += 4
    elif op_yoy > 0: s += 3
    elif op_yoy > -20: s += 2
    else: s += 1
    
    # 财务安全
    if debt < 30: s += 5
    elif debt < 50: s += 4
    elif debt < 60: s += 3
    else: s += 1
    
    if ocf is not None and ocf < 0:
        s -= 1
    
    # 估值(PEG)
    peg = pe / op_yoy if op_yoy > 0 else 999
    if peg < 1: s += 5
    elif peg < 2: s += 4
    elif peg < 3: s += 3
    elif peg < 5: s += 2
    else: s += 1
    
    # 最低-5分(因为ocf扣分), 最高26分
    return max(0, s)

def screen_and_match(trade_date, hot_sectors, min_score=60):
    """Step2: 中军筛选+主线匹配"""
    try:
        basic = pro.daily_basic(trade_date=trade_date,
            fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    except: return pd.DataFrame()
    if basic.empty: return pd.DataFrame()
    
    basic['mv_yi'] = basic['total_mv'] / 10000
    cands = basic[(basic['mv_yi'] >= 100) & (basic['mv_yi'] <= 500)].copy()
    cands = cands[~cands['ts_code'].str.startswith(('8','4','9'))]
    cands = cands[cands['pe'] > 0]
    
    start = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=200)).strftime('%Y%m%d')
    all_sector_stocks = set()
    for hs in hot_sectors:
        all_sector_stocks.update(hs['stocks'])
    
    results = []
    for _, row in cands.iterrows():
        tc = row['ts_code']
        # 快速过滤：不在主线板块内则跳过
        if tc not in all_sector_stocks:
            continue
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
            
            if score >= min_score:
                # 匹配主线数
                matched = sum(1 for hs in hot_sectors if tc in hs['stocks'])
                if matched >= 1:
                    results.append({
                        'ts_code': tc, 'close': c.iloc[-1], 'mv_yi': row['mv_yi'],
                        'pe': row['pe'], 'tech_score': score, 'pct_5d': round(pct5,2),
                        'price_pos_120': round(pos120,1), 'sector_count': matched
                    })
        except: continue
    
    if not results:
        return pd.DataFrame()
    
    rdf = pd.DataFrame(results).sort_values(['sector_count', 'tech_score'], ascending=False)
    sb = pro.stock_basic(fields='ts_code,name')
    rdf['name'] = rdf['ts_code'].map(dict(zip(sb['ts_code'], sb['name'])))
    return rdf

def get_financial_batch(ts_codes):
    """批量获取财务"""
    data = {}
    for tc in ts_codes:
        try:
            fi = pro.fina_indicator(ts_code=tc, period='20260331',
                fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
            if len(fi) > 0:
                d = fi.iloc[0].to_dict()
                d['pe'] = 0  # will fill later
                data[tc] = d
        except: pass
    return data

# ============================================================
# 主回测
# ============================================================
def main():
    print("=" * 80)
    print("三步法中军选股 - 一个月回测")
    print("=" * 80)
    
    # 交易日
    dates = get_trade_dates(35)
    print(f"交易日范围: {dates[0]} ~ {dates[-1]} ({len(dates)}天)")
    
    # 加载映射（只加载一次）
    print("\n加载概念/行业映射...")
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()
    
    # 财务数据（只获取一次，用Q1最新）
    print("预加载股票基本信息...")
    sb = pro.stock_basic(fields='ts_code,name')
    name_map = dict(zip(sb['ts_code'], sb['name']))
    
    # 财务预加载
    print("预加载Q1财务数据...")
    all_basic = pro.daily_basic(trade_date=dates[-1],
        fields='ts_code,close,pe,total_mv') if len(dates) > 0 else pd.DataFrame()
    all_codes = all_basic['ts_code'].tolist()[:500]  # 取前500只避免太慢
    fin_data = get_financial_batch(all_codes)
    print(f"  财务数据: {len(fin_data)}只")
    
    # 回测参数
    HOLD_DAYS = 5  # 持有天数
    SAMPLE_INTERVAL = 2  # 每隔N天采样一次
    TOP_N = 3  # 每次选TOP N
    
    # 回测结果
    all_picks = []
    trade_dates = dates
    
    print(f"\n{'='*80}")
    print(f"回测参数: 持有{HOLD_DAYS}天, 每隔{SAMPLE_INTERVAL}天选股, 每次选TOP{TOP_N}")
    print(f"{'='*80}")
    
    for i in range(0, len(trade_dates) - HOLD_DAYS, SAMPLE_INTERVAL):
        trade_date = trade_dates[i]
        exit_date = trade_dates[min(i + HOLD_DAYS, len(trade_dates) - 1)]
        
        print(f"\n📅 {trade_date} 选股 → {exit_date} 卖出")
        
        # Step1: 热门行业
        hot = get_hot_industries(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8)
        if not hot:
            print("  ⚠️ 无法获取热门行业,跳过")
            continue
        
        top3_names = [h['name'] for h in hot[:3]]
        print(f"  热门行业: {', '.join(top3_names)}")
        
        # Step2: 中军筛选+匹配
        matched = screen_and_match(trade_date, hot, min_score=55)
        if matched.empty:
            print("  ⚠️ 无匹配中军,跳过")
            continue
        
        print(f"  中军候选: {len(matched)}只")
        
        # Step3: 本地基本面过滤
        for idx, r in matched.iterrows():
            tc = r['ts_code']
            fin = fin_data.get(tc, {})
            fin['pe'] = r['pe']
            bs = local_filter_basic(fin)
            matched.loc[idx, 'fin_score'] = bs
        
        matched = matched.sort_values(['fin_score', 'sector_count', 'tech_score'], ascending=False)
        
        # TOP N
        picks = matched.head(TOP_N)
        
        # 跟踪收益
        for _, pick in picks.iterrows():
            tc = pick['ts_code']
            name = pick['name']
            buy_price = pick['close']
            
            # 获取卖出价
            try:
                exit_daily = pro.daily(ts_code=tc, start_date=trade_date,
                    end_date=exit_date)
                if len(exit_daily) > 0:
                    exit_daily = exit_daily.sort_values('trade_date')
                    sell_price = exit_daily.iloc[-1]['close']
                    ret = (sell_price / buy_price - 1) * 100
                    
                    # 最高价(最大回撤)
                    max_price = exit_daily['high'].max()
                    min_price = exit_daily['low'].min()
                    max_ret = (max_price / buy_price - 1) * 100
                    max_dd = (min_price / buy_price - 1) * 100
                    
                    all_picks.append({
                        'trade_date': trade_date,
                        'exit_date': exit_date,
                        'ts_code': tc,
                        'name': name,
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'return_pct': round(ret, 2),
                        'max_gain': round(max_ret, 2),
                        'max_drawdown': round(max_dd, 2),
                        'tech_score': pick['tech_score'],
                        'fin_score': pick['fin_score'],
                        'sector_count': pick['sector_count'],
                        'pe': pick['pe'],
                        'matched_sectors': pick.get('sector_count', 0)
                    })
                    
                    emoji = "🟢" if ret > 0 else "🔴"
                    print(f"  {emoji} {name}({tc}) 买{buy_price}→卖{sell_price} 收益{ret:+.2f}% "
                          f"最高{max_ret:+.2f}% 最低{max_dd:+.2f}% "
                          f"技术{pick['tech_score']} 基本面{pick['fin_score']} 匹配{pick['sector_count']}线")
            except Exception as e:
                print(f"  ❌ {name} 跟踪失败: {e}")
    
    # ============================================================
    # 回测统计
    # ============================================================
    if not all_picks:
        print("\n⚠️ 无有效回测数据")
        return
    
    rdf = pd.DataFrame(all_picks)
    
    print(f"\n{'='*80}")
    print("📊 回测统计")
    print(f"{'='*80}")
    
    total = len(rdf)
    wins = len(rdf[rdf['return_pct'] > 0])
    losses = len(rdf[rdf['return_pct'] <= 0])
    win_rate = wins / total * 100
    avg_ret = rdf['return_pct'].mean()
    median_ret = rdf['return_pct'].median()
    avg_max_gain = rdf['max_gain'].mean()
    avg_max_dd = rdf['max_drawdown'].mean()
    total_ret = rdf['return_pct'].sum()
    
    print(f"总交易次数: {total}")
    print(f"胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
    print(f"平均收益率: {avg_ret:+.2f}%")
    print(f"中位数收益率: {median_ret:+.2f}%")
    print(f"累计收益率: {total_ret:+.2f}%")
    print(f"平均最大浮盈: {avg_max_gain:+.2f}%")
    print(f"平均最大浮亏: {avg_max_dd:+.2f}%")
    
    # 盈亏比
    avg_win = rdf[rdf['return_pct'] > 0]['return_pct'].mean() if wins > 0 else 0
    avg_loss = abs(rdf[rdf['return_pct'] <= 0]['return_pct'].mean()) if losses > 0 else 1
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 999
    print(f"盈亏比: {pl_ratio:.2f} (平均盈利{avg_win:.2f}% / 平均亏损{avg_loss:.2f}%)")
    
    # TOP盈利案例
    print(f"\n🏆 TOP5 盈利案例:")
    for _, r in rdf.nlargest(5, 'return_pct').iterrows():
        print(f"  {r['name']}({r['ts_code']}) {r['trade_date']}→{r['exit_date']} "
              f"收益{r['return_pct']:+.2f}% (最高{r['max_gain']:+.2f}%) PE={r['pe']:.0f}")
    
    # TOP亏损案例
    print(f"\n💀 TOP5 亏损案例:")
    for _, r in rdf.nsmallest(5, 'return_pct').iterrows():
        print(f"  {r['name']}({r['ts_code']}) {r['trade_date']}→{r['exit_date']} "
              f"收益{r['return_pct']:+.2f}% (最低{r['max_drawdown']:+.2f}%) PE={r['pe']:.0f}")
    
    # 基本面分数分析
    print(f"\n📈 基本面分数与收益关系:")
    for score_range in [(15, 26, '≥15分(优秀)'), (12, 14, '12-14分(一般)'), (0, 11, '<12分(差)')]:
        sub = rdf[(rdf['fin_score'] >= score_range[0]) & (rdf['fin_score'] <= score_range[1])]
        if len(sub) > 0:
            print(f"  {score_range[2]}: {len(sub)}只, 平均收益{sub['return_pct'].mean():+.2f}%, 胜率{(sub['return_pct']>0).mean()*100:.0f}%")
    
    # 主线匹配数分析
    print(f"\n🔗 主线匹配数与收益关系:")
    for sc in sorted(rdf['sector_count'].unique()):
        sub = rdf[rdf['sector_count'] == sc]
        print(f"  匹配{sc}条主线: {len(sub)}只, 平均收益{sub['return_pct'].mean():+.2f}%, 胜率{(sub['return_pct']>0).mean()*100:.0f}%")
    
    # 保存结果
    out_path = os.path.join(BASE_DIR, 'backtest_result.csv')
    rdf.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n💾 回测结果已保存: {out_path}")


if __name__ == '__main__':
    main()
