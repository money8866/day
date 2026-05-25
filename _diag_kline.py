# -*- coding: utf-8 -*-
"""诊断：为什么K线分析后候选为0"""
import sys, warnings, pickle, numpy as np, tushare as ts, datetime
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

FIN_CACHE = r'C:\Users\kongx\mystock\fin_cache_v4.pkl'
fin_cache = pickle.load(open(FINICK_CACHE, 'rb')) if False else {}

# 获取候选列表（前20只）
df_basic = pro.daily_basic(trade_date='20260522', timeout=15)
df_basic = df_basic.dropna(subset=['pe', 'total_mv'])
df_basic = df_basic[(df_basic['pe'] > 0) & (df_basic['pe'] <= 100)]
df_basic['mv_yi'] = df_basic['total_mv'] / 10000
df_basic = df_basic[(df_basic['mv_yi'] >= 100) & (df_basic['mv_yi'] <= 500)]
candidates = df_basic.head(10)[['ts_code', 'close', 'pe', 'mv_yi']].values.tolist()

MA5, MA10, MA20, MA60 = 5, 10, 20, 60

def calc_ma(closes, n):
    if len(closes) < n: return None
    return np.mean(closes[-n:])

def tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol):
    sc = 0
    if ma5_v and ma10_v and ma20_v and price > ma5_v > ma10_v > ma20_v: sc += 10
    if ma20_v and ma60_v and ma20_v > ma60_v: sc += 5
    if vol and avg_vol and vol > avg_vol * 1.5: sc += 10
    elif vol and avg_vol and vol > avg_vol: sc += 5
    if high_21 and price >= high_21 * 0.90: sc += 5
    if ma5_v and abs(price - ma5_v) / ma5_v < 0.03: sc += 5
    return sc

print("测试前10只候选K线分析:")
for tc, price, pe, mv in candidates:
    fin = fin_cache.get(tc)
    try:
        start_d = (datetime.datetime.strptime('20260522', '%Y%m%d') - datetime.timedelta(days=90)).strftime('%Y%m%d')
        d = pro.daily(ts_code=tc, start_date=start_d, end_date='20260522', timeout=8)
        print(f"\n{tc} daily返回: {len(d) if d is not None else 'None'} 行")
        if d is None or len(d) < MA60:
            print(f"  → 数据不足({len(d) if d is not None else 0}<{MA60})")
            continue
        d = d.sort_values('trade_date')
        closes = d['close'].tolist()
        ma5_v  = calc_ma(closes, MA5)
        ma10_v = calc_ma(closes, MA10)
        ma20_v = calc_ma(closes, MA20)
        ma60_v = calc_ma(closes, MA60)
        high_21 = max(closes[-21:]) if len(closes) >= 21 else price
        avg_vol = np.mean(d['vol'].tolist()[-20:]) if len(d) >= 20 else 0
        vol = d.iloc[-1]['vol']
        tsc = tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol)
        print(f"  价={price:.2f} ma5={ma5_v:.2f} ma10={ma10_v:.2f} ma20={ma20_v:.2f} ma60={ma60_v:.2f}")
        print(f"  vol={vol:.0f} avg_vol={avg_vol:.0f} high21={high_21:.2f}")
        print(f"  → tech_score={tsc}")
    except Exception as e:
        print(f"  异常: {e}")
