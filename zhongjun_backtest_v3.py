# -*- coding: utf-8 -*-
"""
V3回测验证 - 模拟连续运行，验证二次入选+AI评分
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

# 导入V3核心函数
sys.path.insert(0, BASE_DIR)
from zhongjun_v3 import (
    load_maps, get_hot_sectors_from_db, screen_zhongjun, 
    ai_financial_score, ai_generate_verdict,
    get_fin, PE_MAX, MV_MIN, MV_MAX, MIN_TECH_SCORE, FIN_SCORE_MIN, MIN_REPEAT
)

def main():
    print("="*80)
    print("V3 回测验证 - 二次入选+AI评分")
    print("="*80)
    
    # 回测区间
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=start, end_date=end)
    dates = sorted(cal['cal_date'].tolist())
    print(f"区间: {dates[0]} ~ {dates[-1]} ({len(dates)}天)")
    
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()
    
    HOLD = 5
    STOP = -0.05
    
    # 模拟连续运行
    repeat_tracker = defaultdict(int)   # 累计入选次数
    first_appear = defaultdict(str)
    all_trades = []
    fin_cache = {}
    
    # 每隔1天采样（模拟每日运行）
    for i in range(0, len(dates) - HOLD, 1):
        td = dates[i]
        ed = dates[min(i + HOLD, len(dates)-1)]
        
        # Step1: 主线（用回测版行业动量）
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
        for _,r in top.iterrows():
            ind = r['l2_name']
            stocks = set(sw_df[sw_df['l2_name']==ind]['ts_code'].dropna().unique().tolist())
            for cn,cl in concept_map.items():
                if ind in cn: stocks.update(cl)
            for tc,cs in stock_concept_map.items():
                if ind in str(cs): stocks.add(tc)
            hot.append({'name':ind,'score':r['score'],'momentum':0,'leader':'','stocks':stocks})
        
        if not hot: continue
        
        # Step2: 中军筛选
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
        for _,row in cands.iterrows():
            tc = row['ts_code']
            pe = row['pe']
            try:
                dh = pro.daily(ts_code=tc, start_date=start_h, end_date=td)
                if len(dh)<60: continue
                dh = dh.sort_values('trade_date').reset_index(drop=True)
                c,h,l,v = dh['close'],dh['high'],dh['low'],dh['vol']
                ma5,ma10,ma20,ma60 = c.rolling(5).mean(),c.rolling(10).mean(),c.rolling(20).mean(),c.rolling(60).mean()
                
                sc=0
                if ma5.iloc[-1]>ma10.iloc[-1]>ma20.iloc[-1]: sc+=25
                if ma20.iloc[-1]>ma60.iloc[-1]: sc+=20
                vr = v/v.rolling(20).mean()
                if vr.iloc[-3:].mean()>1.3: sc+=15
                if c.iloc[-1]>h.iloc[-21:-1].max()*0.98: sc+=15
                pos120=(c.iloc[-1]-l.rolling(120).min().iloc[-1])/(h.rolling(120).max().iloc[-1]-l.rolling(120).min().iloc[-1])*100
                if pos120<70: sc+=10
                pct5=(c.iloc[-1]/c.iloc[-6]-1)*100
                if 3<pct5<20: sc+=10
                rh=h.iloc[-45:-5].max(); rl=l.iloc[-45:-5].min()
                if (rh-rl)/rl*100<25: sc+=5
                if sc<MIN_TECH_SCORE: continue
                
                sec_cnt = sum(1 for hs in hot if tc in hs['stocks'])
                
                # 财务
                if tc not in fin_cache:
                    fin_cache[tc] = get_fin(tc)
                fin = fin_cache[tc]
                fin_s, _ = ai_financial_score(fin, pe, sec_cnt, sc)
                
                # 更新入选次数
                repeat_tracker[tc] += 1
                if first_appear[tc]=='' or td < first_appear[tc]:
                    first_appear[tc] = td
                
                day_picks.append({
                    'ts_code':tc, 'name':'', 'close':row['close'],
                    'pe':pe, 'mv_yi':row['mv_yi'], 'tech_score':sc,
                    'fin_score':fin_s, 'sector_count':sec_cnt,
                    'repeat':repeat_tracker[tc], 'pct_5d':round(pct5,2)
                })
            except: continue
        
        if not day_picks: continue
        
        # 获取名字
        sb = pro.stock_basic(fields='ts_code,name')
        nm = dict(zip(sb['ts_code'],sb['name']))
        for p in day_picks:
            p['name'] = nm.get(p['ts_code'], p['ts_code'])
        
        # 按V3逻辑分级
        # A级: repeat>=2 + fin>=20
        a_picks = [p for p in day_picks if p['repeat']>=MIN_REPEAT and p['fin_score']>=FIN_SCORE_MIN]
        a_picks.sort(key=lambda x: (x['fin_score'], x['repeat'], x['sector_count']), reverse=True)
        
        # 只对A级模拟交易
        for pick in a_picks[:3]:  # TOP3
            tc = pick['ts_code']
            buy = pick['close']
            try:
                exit_df = pro.daily(ts_code=tc, start_date=td, end_date=ed)
                if exit_df.empty: continue
                exit_df = exit_df.sort_values('trade_date').reset_index(drop=True)
                
                # 止损模拟
                stopped = False
                sell = buy
                actual_hold = len(exit_df)-1
                for j in range(1, len(exit_df)):
                    if exit_df.loc[j,'low']/buy-1 <= STOP:
                        sell = buy*(1+STOP)
                        actual_hold = j
                        stopped = True
                        break
                if not stopped:
                    sell = exit_df.iloc[-1]['close']
                
                ret = (sell/buy-1)*100
                max_gain = (exit_df['high'].max()/buy-1)*100
                max_dd = (exit_df['low'].min()/buy-1)*100
                
                all_trades.append({
                    'date':td, 'exit_date':exit_df.iloc[-1]['trade_date'] if not stopped else exit_df.loc[actual_hold,'trade_date'],
                    'name':pick['name'], 'ts_code':tc,
                    'buy':buy, 'sell':round(sell,2),
                    'return_pct':round(ret,2), 'max_gain':round(max_gain,2),
                    'max_dd':round(max_dd,2), 'stopped':stopped,
                    'fin_score':pick['fin_score'], 'tech_score':pick['tech_score'],
                    'sector_count':pick['sector_count'], 'repeat':pick['repeat'],
                    'pe':pick['pe']
                })
            except: continue
        
        if (i+1) % 5 == 0:
            print(f"  进度: {td} | 累计交易{len(all_trades)}笔 | 候选池{len(repeat_tracker)}只")
        
        time.sleep(0.5)
    
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
    print(f"📊 V3回测结果 (二次入选+AI评分+止损)")
    print(f"{'='*80}")
    print(f"交易: {total}笔 | 胜率: {wr:.1f}%")
    print(f"均收: {avg_ret:+.2f}% | 复合累计: {cum*100:+.2f}%")
    print(f"盈亏比: {plr:.2f} (均盈{avg_w:.2f}%/均亏{avg_l:.2f}%)")
    print(f"止损: {rdf['stopped'].sum()}笔({rdf['stopped'].mean()*100:.0f}%)")
    
    print(f"\n📈 基本面分 vs 收益:")
    for lo,hi,lb in [(24,30,'≥24'),(20,23,'20-23')]:
        sub=rdf[(rdf['fin_score']>=lo)&(rdf['fin_score']<=hi)]
        if len(sub)>0:
            print(f"  {lb}: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")
    
    print(f"\n🔁 入选次数 vs 收益:")
    for rp in sorted(rdf['repeat'].unique()):
        sub=rdf[rdf['repeat']==rp]
        print(f"  {rp}次: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}%")
    
    print(f"\n🏆 TOP5盈利:")
    for _,r in rdf.nlargest(5,'return_pct').iterrows():
        print(f"  {r['name']} {r['date']}→{r['exit_date']} {r['return_pct']:+.2f}% "
              f"基本面={r['fin_score']} 入选{r['repeat']}次 PE={r['pe']:.0f}")
    
    print(f"\n💀 TOP5亏损:")
    for _,r in rdf.nsmallest(5,'return_pct').iterrows():
        st="⚡" if r['stopped'] else ""
        print(f"  {r['name']} {r['date']}→{r['exit_date']} {r['return_pct']:+.2f}% "
              f"基本面={r['fin_score']} PE={r['pe']:.0f} {st}")
    
    # 对比表
    print(f"\n📊 V2 vs V3 对比:")
    print(f"  V2(无二次入选): 胜率46% 盈亏比1.53 累计+25.8%")
    print(f"  V3(二次入选):   胜率{wr:.0f}% 盈亏比{plr:.2f} 累计{cum*100:+.1f}%")
    
    out = os.path.join(BASE_DIR, 'backtest_v3.csv')
    rdf.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n💾 {out}")

if __name__ == '__main__':
    main()
