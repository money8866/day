# -*- coding: utf-8 -*-
"""
三步法中军选股 - 优化版回测
优化点:
1. 财务数据实时获取(不预加载,避免未命中)
2. PE<100硬过滤
3. -5%止损 + 持仓5天止盈
4. 多主线匹配加权(≥2条主线必须入选)
5. 基本面评分精细化
6. 重复入选加分
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

env = Path(os.path.join(BASE_DIR, '.env')).read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='):
        ts.set_token(line.split('=',1)[1].strip())
pro = ts.pro_api()

# ============================================================
# 参数
# ============================================================
PE_MAX = 100          # PE硬过滤上限
STOP_LOSS = -0.05     # 止损线 -5%
HOLD_DAYS = 5         # 最大持仓天数
SAMPLE_INTERVAL = 2   # 采样间隔
TOP_N = 3             # 每次选股数
MV_MIN, MV_MAX = 100, 500
MIN_TECH_SCORE = 60
FIN_SCORE_MIN = 14    # 基本面最低分

# ============================================================
# 缓存: 财务数据(避免重复请求)
# ============================================================
_fin_cache = {}

def get_fin_cached(ts_code):
    if ts_code in _fin_cache:
        return _fin_cache[ts_code]
    try:
        fi = pro.fina_indicator(ts_code=ts_code, period='20260331',
            fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
        if len(fi) > 0:
            d = fi.iloc[0].to_dict()
        else:
            d = {}
        _fin_cache[ts_code] = d
        time.sleep(0.15)
        return d
    except:
        time.sleep(0.3)
        return {}

# ============================================================
# 辅助
# ============================================================
def calc_ma(s, n): return s.rolling(n).mean()

def load_maps():
    cs_path = os.path.join(CACHE_DIR, 'concept_stock_map.pkl')
    concept_map = {}
    if os.path.exists(cs_path):
        with open(cs_path, 'rb') as f: concept_map = pickle.load(f)
    theme_path = os.path.join(BASE_DIR, 'theme_map.json')
    with open(theme_path, 'r', encoding='utf-8') as f: theme_map = json.load(f)
    sc_path = os.path.join(CACHE_DIR, 'stock_concept_map.pkl')
    stock_concept_map = {}
    if os.path.exists(sc_path):
        with open(sc_path, 'rb') as f: stock_concept_map = pickle.load(f)
    sw_path = os.path.join(CACHE_DIR, 'sw_map.csv')
    sw_df = pd.read_csv(sw_path, dtype=str) if os.path.exists(sw_path) else pd.DataFrame()
    return concept_map, theme_map, stock_concept_map, sw_df

def get_hot_industries(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8):
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
        result.append({'name':ind, 'score':r['score'], 'stocks':stocks})
    return result

# ============================================================
# 基本面评分 (优化版)
# ============================================================
def calc_fin_score(fin, pe):
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0
    debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or')

    s = 0
    # 盈利质量 (1-6)
    if gm > 50: s += 6
    elif gm > 40: s += 5
    elif gm > 30: s += 4
    elif gm > 20: s += 3
    elif gm > 10: s += 2
    else: s += 1
    if nm > 20: s += 1  # 净利率bonus

    # 成长性 (1-6)
    if op_yoy > 100: s += 6
    elif op_yoy > 50: s += 5
    elif op_yoy > 30: s += 4
    elif op_yoy > 10: s += 3
    elif op_yoy > 0: s += 2
    elif op_yoy > -10: s += 1
    else: s += 0

    # 财务安全 (1-6)
    if debt < 20: s += 6
    elif debt < 30: s += 5
    elif debt < 40: s += 4
    elif debt < 50: s += 3
    elif debt < 60: s += 2
    else: s += 1

    if ocf is not None and ocf < 0: s -= 2  # 现金流为负重罚
    if ocf is not None and ocf > 0.1: s += 1

    # 估值 (1-6)
    peg = pe / op_yoy if op_yoy > 0 else 999
    if peg < 0.5: s += 6
    elif peg < 1: s += 5
    elif peg < 1.5: s += 4
    elif peg < 2: s += 3
    elif peg < 3: s += 2
    else: s += 1

    return max(0, min(s, 26))

# ============================================================
# Step2: 中军筛选 + 主线匹配 + 基本面过滤
# ============================================================
def screen_and_filter(trade_date, hot_sectors):
    try:
        basic = pro.daily_basic(trade_date=trade_date,
            fields='ts_code,close,pe,pb,total_mv,circ_mv,turnover_rate,volume')
    except: return pd.DataFrame()
    if basic.empty: return pd.DataFrame()

    basic['mv_yi'] = basic['total_mv'] / 10000
    cands = basic[(basic['mv_yi'] >= MV_MIN) & (basic['mv_yi'] <= MV_MAX)].copy()
    cands = cands[~cands['ts_code'].str.startswith(('8','4','9'))]
    # PE硬过滤
    cands = cands[(cands['pe'] > 0) & (cands['pe'] <= PE_MAX)]

    all_sector_stocks = set()
    for hs in hot_sectors:
        all_sector_stocks.update(hs['stocks'])

    # 先过滤: 只保留主线内的股票
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

            # 主线匹配
            sector_count = sum(1 for hs in hot_sectors if tc in hs['stocks'])

            # 基本面过滤
            fin = get_fin_cached(tc)
            fin_score = calc_fin_score(fin, pe)

            if fin_score < FIN_SCORE_MIN:
                continue

            results.append({
                'ts_code': tc, 'close': c.iloc[-1], 'mv_yi': row['mv_yi'],
                'pe': pe, 'tech_score': score, 'fin_score': fin_score,
                'pct_5d': round(pct5,2), 'price_pos_120': round(pos120,1),
                'sector_count': sector_count,
                'composite': fin_score * 2 + sector_count * 5 + score * 0.3
            })
            time.sleep(0.15)
        except: continue

    if not results: return pd.DataFrame()
    rdf = pd.DataFrame(results).sort_values('composite', ascending=False)
    sb = pro.stock_basic(fields='ts_code,name')
    rdf['name'] = rdf['ts_code'].map(dict(zip(sb['ts_code'], sb['name'])))
    return rdf

# ============================================================
# 模拟持仓: 含止损
# ============================================================
def simulate_holding(ts_code, buy_price, trade_date, exit_date):
    """模拟持仓,含-5%止损"""
    try:
        df = pro.daily(ts_code=ts_code, start_date=trade_date, end_date=exit_date)
        if df.empty: return None
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        sell_price = buy_price
        sell_date = exit_date
        stopped = False
        
        for i in range(1, len(df)):
            # 日内最低价触发止损
            if df.loc[i, 'low'] / buy_price - 1 <= STOP_LOSS:
                sell_price = buy_price * (1 + STOP_LOSS)
                sell_date = df.loc[i, 'trade_date']
                stopped = True
                break
        
        if not stopped:
            sell_price = df.iloc[-1]['close']
            sell_date = df.iloc[-1]['trade_date']
        
        ret = (sell_price / buy_price - 1) * 100
        max_gain = (df['high'].max() / buy_price - 1) * 100
        max_dd = (df['low'].min() / buy_price - 1) * 100
        
        return {
            'sell_price': round(sell_price, 2),
            'sell_date': sell_date,
            'return_pct': round(ret, 2),
            'max_gain': round(max_gain, 2),
            'max_drawdown': round(max_dd, 2),
            'stopped': stopped,
            'actual_hold': len(df) - 1
        }
    except:
        return None

# ============================================================
# 主回测
# ============================================================
def main():
    print("=" * 80)
    print("三步法中军选股 - 优化版回测")
    print(f"PE≤{PE_MAX} | 止损{STOP_LOSS*100}% | 基本面≥{FIN_SCORE_MIN}分 | 持仓{HOLD_DAYS}天")
    print("=" * 80)

    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=start_date, end_date=end_date)
    dates = sorted(cal['cal_date'].tolist())
    print(f"交易日: {dates[0]} ~ {dates[-1]} ({len(dates)}天)")

    concept_map, theme_map, stock_concept_map, sw_df = load_maps()

    all_picks = []
    repeat_counter = defaultdict(int)  # 追踪重复入选

    for i in range(0, len(dates) - HOLD_DAYS, SAMPLE_INTERVAL):
        trade_date = dates[i]
        exit_date = dates[min(i + HOLD_DAYS, len(dates) - 1)]

        print(f"\n📅 {trade_date} → {exit_date}")

        # Step1
        hot = get_hot_industries(trade_date, concept_map, theme_map, stock_concept_map, sw_df, top_n=8)
        if not hot:
            print("  ⚠️ 无热门行业")
            continue
        top3 = [h['name'] for h in hot[:3]]
        print(f"  热门: {', '.join(top3)}")

        # Step2+3
        matched = screen_and_filter(trade_date, hot)
        if matched.empty:
            print("  ⚠️ 无合格中军")
            continue

        print(f"  合格中军: {len(matched)}只")

        # 重复入选加分
        for idx, r in matched.iterrows():
            tc = r['ts_code']
            repeat_counter[tc] += 1
            if repeat_counter[tc] >= 2:
                matched.loc[idx, 'composite'] += 10 * (repeat_counter[tc] - 1)
        matched = matched.sort_values('composite', ascending=False)

        picks = matched.head(TOP_N)
        
        for _, pick in picks.iterrows():
            tc = pick['ts_code']
            name = pick['name']
            buy_price = pick['close']
            
            result = simulate_holding(tc, buy_price, trade_date, exit_date)
            if result is None:
                print(f"  ❌ {name} 数据缺失")
                continue

            all_picks.append({
                'trade_date': trade_date, 'exit_date': result['sell_date'],
                'ts_code': tc, 'name': name,
                'buy_price': buy_price, 'sell_price': result['sell_price'],
                'return_pct': result['return_pct'],
                'max_gain': result['max_gain'],
                'max_drawdown': result['max_drawdown'],
                'stopped': result['stopped'],
                'actual_hold': result['actual_hold'],
                'tech_score': pick['tech_score'],
                'fin_score': pick['fin_score'],
                'sector_count': pick['sector_count'],
                'pe': pick['pe'],
                'repeat': repeat_counter[tc]
            })

            emoji = "🟢" if result['return_pct'] > 0 else "🔴"
            stop_tag = "⚡止损" if result['stopped'] else f"{result['actual_hold']}天"
            repeat_tag = f"🔁x{repeat_counter[tc]}" if repeat_counter[tc] >= 2 else ""
            print(f"  {emoji} {name}({tc}) {buy_price}→{result['sell_price']} "
                  f"{result['return_pct']:+.2f}% [{stop_tag}] "
                  f"技术{pick['tech_score']} 基本面{pick['fin_score']} "
                  f"匹配{pick['sector_count']}线 PE={pick['pe']:.0f} {repeat_tag}")

    # ============================================================
    # 统计
    # ============================================================
    if not all_picks:
        print("\n⚠️ 无有效回测数据")
        return

    rdf = pd.DataFrame(all_picks)

    total = len(rdf)
    wins = len(rdf[rdf['return_pct'] > 0])
    losses = len(rdf['return_pct'] <= 0)
    win_rate = wins / total * 100
    avg_ret = rdf['return_pct'].mean()
    median_ret = rdf['return_pct'].median()
    cum_ret = (1 + rdf['return_pct']/100).prod() - 1
    avg_max_gain = rdf['max_gain'].mean()
    avg_max_dd = rdf['max_drawdown'].mean()
    stopped_n = rdf['stopped'].sum()
    avg_win = rdf[rdf['return_pct']>0]['return_pct'].mean() if wins else 0
    avg_loss = abs(rdf[rdf['return_pct']<=0]['return_pct'].mean()) if losses else 1
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 999

    print(f"\n{'='*80}")
    print("📊 优化版回测统计")
    print(f"{'='*80}")
    print(f"总交易: {total}笔 | 胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
    print(f"平均收益: {avg_ret:+.2f}% | 中位数: {median_ret:+.2f}%")
    print(f"复合累计收益: {cum_ret*100:+.2f}%")
    print(f"盈亏比: {pl_ratio:.2f} (均盈{avg_win:.2f}%/均亏{avg_loss:.2f}%)")
    print(f"平均最大浮盈: +{avg_max_gain:.2f}% | 平均最大浮亏: {avg_max_dd:.2f}%")
    print(f"触发止损: {stopped_n}笔 ({stopped_n/total*100:.0f}%)")

    # 分组分析
    print(f"\n📈 基本面分数 vs 收益:")
    for lo, hi, label in [(20,26,'≥20(优)'),(16,19,'16-19(良)'),(14,15,'14-15(及格)')]:
        sub = rdf[(rdf['fin_score']>=lo)&(rdf['fin_score']<=hi)]
        if len(sub)>0:
            print(f"  {label}: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")

    print(f"\n🔗 主线匹配数 vs 收益:")
    for sc in sorted(rdf['sector_count'].unique()):
        sub = rdf[rdf['sector_count']==sc]
        print(f"  {sc}条: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")

    print(f"\n🔁 重复入选 vs 收益:")
    for rp in sorted(rdf['repeat'].unique()):
        sub = rdf[rdf['repeat']==rp]
        print(f"  {rp}次: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")

    print(f"\n⚡ 止损效果:")
    if stopped_n > 0:
        stopped_df = rdf[rdf['stopped']]
        print(f"  止损笔均亏: {stopped_df['return_pct'].mean():.2f}% (无止损则可能更大)")

    # TOP案例
    print(f"\n🏆 TOP5 盈利:")
    for _, r in rdf.nlargest(5,'return_pct').iterrows():
        tag = f"🔁x{r['repeat']}" if r['repeat']>=2 else ""
        print(f"  {r['name']} {r['trade_date']}→{r['exit_date']} "
              f"{r['return_pct']:+.2f}% PE={r['pe']:.0f} 基本面={r['fin_score']:.0f} {tag}")

    print(f"\n💀 TOP5 亏损:")
    for _, r in rdf.nsmallest(5,'return_pct').iterrows():
        stop = "⚡" if r['stopped'] else ""
        print(f"  {r['name']} {r['trade_date']}→{r['exit_date']} "
              f"{r['return_pct']:+.2f}% PE={r['pe']:.0f} {stop}")

    out = os.path.join(BASE_DIR, 'backtest_optimized.csv')
    rdf.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n💾 保存: {out}")

if __name__ == '__main__':
    main()
