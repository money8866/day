# -*- coding: utf-8 -*-
"""
V4 回测验证 - 回档买优化
核心改进 vs V3:
  突破状态等回档再买，回档MA5/MA10立即买
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
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

sys.path.insert(0, BASE_DIR)
from zhongjun_v4 import (
    load_maps, screen_zhongjun,
    ai_financial_score, ai_generate_verdict,
    get_fin, detect_entry_type,
    PE_MAX, MV_MIN, MV_MAX, MIN_TECH_SCORE, FIN_SCORE_MIN, MIN_REPEAT,
    PULLBACK_TOLERANCE, FAR_FROM_MA_THRESHOLD
)

def simulate_pullback_entry(ts_code, buy_date, max_wait=5):
    """
    V4核心：模拟等回档买入
    对突破状态标的，检查未来max_wait天是否有回档到MA5/MA10
    返回: (实际买入日期, 实际买入价格) 或 (None, None)
    """
    try:
        end_str = (datetime.strptime(buy_date, '%Y%m%d') + timedelta(days=max_wait+10)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=buy_date, end_date=end_str)
        if df.empty or len(df) < 2:
            return None, None
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 第0天是选股日(不买)，从第1天开始等回档
        for i in range(1, min(len(df), max_wait+1)):
            row = df.iloc[i]
            price = row['close']
            d2 = row['trade_date']

            # 用买入日的数据计算MA
            df_ma = pro.daily(ts_code=ts_code,
                              start_date=(datetime.strptime(d2, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d'),
                              end_date=d2)
            if len(df_ma) < 20:
                continue
            df_ma = df_ma.sort_values('trade_date').reset_index(drop=True)
            c_ma = df_ma['close']
            ma5 = c_ma.rolling(5).mean().iloc[-1]
            ma10 = c_ma.rolling(10).mean().iloc[-1]

            # 判断是否回档
            if (ma5 * (1 - PULLBACK_TOLERANCE) <= price <= ma5 * (1 + PULLBACK_TOLERANCE) or
                ma10 * (1 - PULLBACK_TOLERANCE) <= price <= ma10 * (1 + PULLBACK_TOLERANCE)):
                return d2, price

        # 5天内无回档，跳过
        return None, None
    except Exception as e:
        return None, None

def main():
    print("=" * 80)
    print("V4 回测验证 - 回档买优化")
    print("核心: 突破状态等回档再买，回档MA5/MA10立即买")
    print("=" * 80)

    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=start, end_date=end)
    dates = sorted(cal['cal_date'].tolist())
    print(f"区间: {dates[0]} ~ {dates[-1]} ({len(dates)}天)")

    concept_map, theme_map, stock_concept_map, sw_df = load_maps()
    HOLD = 5
    STOP = -0.05

    repeat_tracker = defaultdict(int)
    first_appear = defaultdict(str)
    all_trades = []
    fin_cache = {}
    skipped_breakout = 0

    for i in range(0, len(dates) - HOLD, 1):
        td = dates[i]
        ed = dates[min(i + HOLD, len(dates)-1)]

        # Step1: 主线
        try:
            df = pro.daily(trade_date=td, fields='ts_code,close,pct_chg,amount')
            if df.empty: continue
        except: continue

        if sw_df.empty or 'l2_name' not in sw_df.columns: continue
        sw_merge = sw_df[['ts_code','l2_name']].dropna(subset=['l2_name'])
        df = df.merge(sw_merge, on='ts_code', how='left').dropna(subset=['l2_name'])
        if df.empty: continue

        ip = df.groupby('l2_name').agg(
            avg_pct=('pct_chg','mean'), total_amount=('amount','sum'),
            stock_count=('ts_code','count'), up_ratio=('pct_chg',lambda x:(x>0).mean()),
            limit_up=('pct_chg',lambda x:(x>=9.5).sum())
        ).reset_index()
        ip = ip[ip['stock_count']>=5]
        ip['score'] = ip['avg_pct']*1.5+ip['limit_up']*3+ip['up_ratio']*8+np.log1p(ip['total_amount']/1e8)*2
        top = ip.sort_values('score',ascending=False).head(8)

        hot = []
        for _, r in top.iterrows():
            ind = r['l2_name']
            stocks = set(sw_df[sw_df['l2_name']==ind]['ts_code'].dropna().unique().tolist())
            for cn, cl in concept_map.items():
                if ind in cn: stocks.update(cl)
            for tc, cs in stock_concept_map.items():
                if ind in str(cs): stocks.add(tc)
            hot.append({'name':ind,'score':r['score'],'momentum':0,'leader':'','stocks':stocks})
        if not hot: continue

        # Step2: 中军筛选（含V4回档判断）
        try:
            basic = pro.daily_basic(trade_date=td, fields='ts_code,close,pe,total_mv')
        except: continue
        if basic.empty: continue
        basic['mv_yi'] = basic['total_mv']/10000
        cands = basic[(basic['mv_yi']>=MV_MIN)&(basic['mv_yi']<=MV_MAX)]
        cands = cands[~cands['ts_code'].str.startswith(('8','4','9'))]
        cands = cands[(cands['pe']>0)&(cands['pe']<=PE_MAX)]
        all_ss = set().union(*[h['stocks'] for h in hot])
        cands = cands[cands['ts_code'].isin(all_ss)]

        start_h = (datetime.strptime(td,'%Y%m%d')-timedelta(days=200)).strftime('%Y%m%d')

        day_picks = []
        for _, row in cands.iterrows():
            tc = row['ts_code']
            pe = row['pe']
            try:
                dh = pro.daily(ts_code=tc, start_date=start_h, end_date=td)
                if len(dh)<60: continue
                dh = dh.sort_values('trade_date').reset_index(drop=True)
                c = dh['close']; h = dh['high']; l = dh['low']; v = dh['vol']
                ma5_v = c.rolling(5).mean().iloc[-1]
                ma10_v = c.rolling(10).mean().iloc[-1]
                ma20_v = c.rolling(20).mean().iloc[-1]
                ma60_v = c.rolling(60).mean().iloc[-1]
                price = c.iloc[-1]

                sc = 0
                if ma5_v > ma10_v > ma20_v: sc += 25
                if ma20_v > ma60_v: sc += 20
                vr = v / v.rolling(20).mean()
                if vr.iloc[-3:].mean() > 1.3: sc += 15
                high_21 = h.iloc[-21:-1].max()
                if price > high_21 * 0.98: sc += 15
                pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
                if pos120 < 70: sc += 10
                pct5 = (price / c.iloc[-6] - 1) * 100
                if 3 < pct5 < 20: sc += 10
                rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
                if rl > 0 and (rh-rl)/rl*100 < 25: sc += 5
                if sc < MIN_TECH_SCORE: continue

                # V4: 回档类型
                entry_type, entry_bonus, entry_suggestion = detect_entry_type(
                    price, ma5_v, ma10_v, ma20_v, ma60_v, high_21)
                sc += entry_bonus

                sec_cnt = sum(1 for hs in hot if tc in hs['stocks'])

                if tc not in fin_cache:
                    fin_cache[tc] = get_fin(tc)
                fin = fin_cache[tc]
                fin_s, _ = ai_financial_score(fin, pe, sec_cnt, sc)

                repeat_tracker[tc] += 1
                if first_appear[tc]=='' or td < first_appear[tc]:
                    first_appear[tc] = td

                day_picks.append({
                    'ts_code':tc, 'name':'', 'close':price,
                    'pe':pe, 'mv_yi':row['mv_yi'], 'tech_score':sc,
                    'fin_score':fin_s, 'sector_count':sec_cnt,
                    'repeat':repeat_tracker[tc], 'pct_5d':round(pct5,2),
                    'entry_type':entry_type,
                })
            except: continue

        if not day_picks: continue

        sb = pro.stock_basic(fields='ts_code,name')
        nm = dict(zip(sb['ts_code'],sb['name']))
        for p in day_picks:
            p['name'] = nm.get(p['ts_code'], p['ts_code'])

        # A级: 二次入选+基本面>=20
        a_picks = [p for p in day_picks if p['repeat']>=MIN_REPEAT and p['fin_score']>=FIN_SCORE_MIN]
        a_picks.sort(key=lambda x: (x['fin_score'], x['repeat'], x['sector_count']), reverse=True)

        # 只对A级模拟交易
        for pick in a_picks[:3]:
            tc = pick['ts_code']
            entry_type = pick['entry_type']
            orig_buy = pick['close']
            actual_buy_date = td
            actual_buy_price = orig_buy
            pullback_wait = 0

            # V4核心: 突破状态等回档
            if entry_type in ('breakout', 'far_away', 'breakout_near_ma'):
                pb_date, pb_price = simulate_pullback_entry(tc, td, max_wait=5)
                if pb_date is None:
                    skipped_breakout += 1
                    continue  # 5天内无回档，跳过
                actual_buy_date = pb_date
                actual_buy_price = pb_price
                pullback_wait = (datetime.strptime(pb_date,'%Y%m%d') - datetime.strptime(td,'%Y%m%d')).days

            # 以实际买入日计算卖出（持有5天）
            end_hold = (datetime.strptime(actual_buy_date,'%Y%m%d') + timedelta(days=HOLD+10)).strftime('%Y%m%d')
            try:
                exit_df = pro.daily(ts_code=tc, start_date=actual_buy_date, end_date=end_hold)
                if exit_df.empty: continue
                exit_df = exit_df.sort_values('trade_date').reset_index(drop=True)

                # 止损(相对实际买入价)
                stopped = False
                sell = actual_buy_price
                actual_hold = min(HOLD, len(exit_df)-1)
                for j in range(1, len(exit_df)):
                    if exit_df.iloc[j]['low']/actual_buy_price-1 <= STOP:
                        sell = actual_buy_price*(1+STOP)
                        actual_hold = j
                        stopped = True
                        break
                if not stopped:
                    sell = exit_df.iloc[min(HOLD, len(exit_df)-1)]['close']

                ret = (sell/actual_buy_price-1)*100
                max_gain = (exit_df['high'].max()/actual_buy_price-1)*100
                max_dd = (exit_df['low'].min()/actual_buy_price-1)*100

                all_trades.append({
                    'date':td, 'actual_buy_date':actual_buy_date,
                    'exit_date':exit_df.iloc[actual_hold]['trade_date'] if not stopped else exit_df.iloc[actual_hold]['trade_date'],
                    'name':pick['name'], 'ts_code':tc,
                    'orig_buy':round(orig_buy,2), 'actual_buy':round(actual_buy_price,2),
                    'sell':round(sell,2), 'pullback_wait':pullback_wait,
                    'return_pct':round(ret,2), 'max_gain':round(max_gain,2),
                    'max_dd':round(max_dd,2), 'stopped':stopped,
                    'fin_score':pick['fin_score'], 'tech_score':pick['tech_score'],
                    'sector_count':pick['sector_count'], 'repeat':pick['repeat'],
                    'pe':pick['pe'], 'entry_type':entry_type,
                })
            except: continue

        if (i+1) % 5 == 0:
            print(f"  进度: {td} | 累计交易{len(all_trades)}笔 | 等回档跳过{skipped_breakout}笔")

        time.sleep(0.3)

    # ============================================================
    # 统计
    # ============================================================
    if not all_trades:
        print("⚠️ 无交易"); return

    rdf = pd.DataFrame(all_trades)
    total = len(rdf)
    wins = (rdf['return_pct']>0).sum()
    losses = (rdf['return_pct']<=0).sum()
    wr = wins/total*100
    avg_ret = rdf['return_pct'].mean()
    cum = (1+rdf['return_pct']/100).prod()-1
    avg_w = rdf[rdf['return_pct']>0]['return_pct'].mean() if wins else 0
    avg_l = abs(rdf[rdf['return_pct']<=0]['return_pct'].mean()) if losses else 1
    plr = avg_w/avg_l if avg_l>0 else 999

    print(f"\n{'='*80}")
    print(f"📊 V4回测结果 (回档买优化)")
    print(f"{'='*80}")
    print(f"交易: {total}笔 (跳过突破{skipped_breakout}笔) | 胜率: {wr:.1f}%")
    print(f"均收: {avg_ret:+.2f}% | 复合累计: {cum*100:+.2f}%")
    print(f"盈亏比: {plr:.2f} (均盈{avg_w:.2f}%/均亏{avg_l:.2f}%)")
    print(f"止损: {rdf['stopped'].sum()}笔({rdf['stopped'].mean()*100:.0f}%)")
    print(f"平均等回档: {rdf['pullback_wait'].mean():.1f}天")

    print(f"\n📈 回档类型 vs 收益:")
    for et in sorted(rdf['entry_type'].unique()):
        sub = rdf[rdf['entry_type']==et]
        pb = f"等{sub['pullback_wait'].mean():.0f}天" if sub['pullback_wait'].mean()>0 else "当天买"
        print(f"  {et}: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}% {pb}")

    print(f"\n📈 基本面分 vs 收益:")
    for lo,hi,lb in [(24,30,'>=24'),(20,23,'20-23')]:
        sub=rdf[(rdf['fin_score']>=lo)&(rdf['fin_score']<=hi)]
        if len(sub)>0:
            print(f"  {lb}: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")

    print(f"\n🔁 入选次数 vs 收益:")
    for rp in sorted(rdf['repeat'].unique()):
        sub=rdf[rdf['repeat']==rp]
        print(f"  {rp}次: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")

    print(f"\n🏆 TOP5盈利:")
    for _, r in rdf.nlargest(5,'return_pct').iterrows():
        pb = f"等{r['pullback_wait']}天" if r['pullback_wait']>0 else "当天买"
        print(f"  {r['name']} {r['date']}→{r['exit_date']} {r['return_pct']:+.2f}% "
              f"买{r['actual_buy']:.2f}(原{r['orig_buy']:.2f}) {pb} 基本面={r['fin_score']} {r['entry_type']}")

    print(f"\n💀 TOP5亏损:")
    for _, r in rdf.nsmallest(5,'return_pct').iterrows():
        st="⚡" if r['stopped'] else ""
        pb = f"等{r['pullback_wait']}天" if r['pullback_wait']>0 else "当天买"
        print(f"  {r['name']} {r['date']}→{r['exit_date']} {r['return_pct']:+.2f}% "
              f"买{r['actual_buy']:.2f} {pb} 基本面={r['fin_score']} PE={r['pe']:.0f} {st}")

    print(f"\n📊 V3 vs V4 对比:")
    print(f"  V3(无回档优化): 胜率58% 盈亏比1.66 累计+133.7%")
    print(f"  V4(回档买优化): 胜率{wr:.0f}% 盈亏比{plr:.2f} 累计{cum*100:+.1f}%")
    print(f"  (V4跳过{skipped_breakout}笔突破买，改为等回档)")

    out = os.path.join(BASE_DIR, 'backtest_v4.csv')
    rdf.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n💾 {out}")

if __name__ == '__main__':
    main()
