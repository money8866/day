# -*- coding: utf-8 -*-
"""
三步法中军选股 V3 - 快速版
优化：PE预过滤 + fin_cache优先 + 超时控制
"""
import os, sys, json, pickle, datetime, warnings
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

PROJECT_DIR   = r'C:\Users\kongx\mystock'
HIST_DIR      = os.path.join(PROJECT_DIR, 'screen_history')
FIN_CACHE     = os.path.join(PROJECT_DIR, 'fin_cache_v4.pkl')
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
MA5, MA10, MA20, MA60 = 5, 10, 20, 60

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def load_fin_cache():
    if os.path.exists(FIN_CACHE):
        try: return pickle.load(open(FIN_CACHE, 'rb'))
        except: pass
    return {}

def calc_ma(closes, n):
    if len(closes) < n: return None
    return np.mean(closes[-n:])

def load_history():
    path = os.path.join(HIST_DIR, f"history_{datetime.date.today().strftime('%Y%m%d')[:6]}.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {}

def count_repeats(tc):
    h = load_history()
    return len(h.get(tc, {}).get('records', []))

def save_record(r):
    path = os.path.join(HIST_DIR, f"history_{datetime.date.today().strftime('%Y%m%d')[:6]}.json")
    os.makedirs(HIST_DIR, exist_ok=True)
    h = {}
    if os.path.exists(path):
        try: h = json.load(open(path, 'r', encoding='utf-8'))
        except: pass
    if tc not in h: h[tc] = {'records': [], 'ts_codes': []}
    h[tc]['records'].append(r)
    with open(path, 'w', encoding='utf-8') as f: json.dump(h, f, ensure_ascii=False, indent=2)

def tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol):
    sc = 0
    if ma5_v and ma10_v and ma20_v and price > ma5_v > ma10_v > ma20_v: sc += 10
    if ma20_v and ma60_v and ma20_v > ma60_v: sc += 5
    if vol and avg_vol and vol > avg_vol * 1.5: sc += 10
    elif vol and avg_vol and vol > avg_vol: sc += 5
    if high_21 and price >= high_21 * 0.90: sc += 5
    if ma5_v and abs(price - ma5_v) / ma5_v < 0.03: sc += 5
    return sc

def fin_score(fin):
    if fin is None: return 0
    sc = 0
    gm = fin.get('grossprofit_margin', 0) or 0
    nm = fin.get('netprofit_margin', 0) or 0
    if gm >= 30 and nm >= 15: sc += 10
    elif gm >= 20 and nm >= 10: sc += 5
    oy = fin.get('op_yoy', 0) or 0
    if oy >= 20: sc += 10
    elif oy >= 10: sc += 5
    da = fin.get('debt_to_assets', 0) or 0
    if da <= 50: sc += 5
    elif da <= 70: sc += 2
    ocf = fin.get('ocf_to_or', 0) or 0
    if ocf >= 10: sc += 5
    elif ocf >= 0: sc += 2
    return sc

def get_fin_cached(tc, cache):
    if tc in cache: return cache[tc]
    try:
        df = ts.pro_api().fina_indicator(ts_code=tc,
            start_date=(datetime.datetime.now()-datetime.timedelta(days=90)).strftime('%Y%m%d'),
            timeout=8)
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            f = {'grossprofit_margin': row.get('grossprofit_margin'),
                 'netprofit_margin': row.get('netprofit_margin'),
                 'op_yoy': row.get('op_yoy'),
                 'debt_to_assets': row.get('debt_to_assets'),
                 'ocf_to_or': row.get('ocf_to_or'),
                 'pe': row.get('pe')}
            cache[tc] = f
            pickle.dump(cache, open(FIN_CACHE, 'wb'))
            return f
    except: pass
    return None

def main(date_str):
    print(f"V3 快速版 - {date_str}")
    print("=" * 60)

    # Step1: 申万动量（10秒超时）
    hot = []
    try:
        sw = [f'{i:02d}0000.SI' for i in range(1, 32)]
        scores = {}
        end = date_str
        start = (datetime.datetime.strptime(end, '%Y%m%d') - datetime.timedelta(days=25)).strftime('%Y%m%d')
        for code in sw:
            try:
                d = pro.index_daily(ts_code=code, start_date=start, end_date=end, timeout=5)
                if d is not None and len(d) > 5: scores[code] = d['pct_chg'].sum()
            except: pass
        hot = [s[0] for s in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]]
    except: pass

    if hot:
        print(f"[Step1] 申万主线: {hot[:5]}")
    else:
        print("[Step1] 无板块数据，全市场扫描")

    # Step2: 全市场 PE + 基本面预过滤
    try:
        df = pro.daily(trade_date=date_str, timeout=15)
        print(f"[Step2] 全市场 {len(df)} 只，开始预过滤...")
    except Exception as e:
        print(f"获取行情失败: {e}"); return

    fin_cache = load_fin_cache()
    # 先用 fin_cache 的 pe 预过滤
    candidates = []
    for _, row in df.iterrows():
        tc = row['ts_code']
        price = row['close']
        fin = fin_cache.get(tc) or get_fin_cached(tc, fin_cache)
        if fin is None: continue
        pe = fin.get('pe')
        if pe and 0 < pe <= 100:
            amount = row.get('amount', 0) or 0
            mv = amount / price / 100 if price > 0 else 0
            if 100 <= mv <= 500:
                candidates.append((tc, price, fin, mv))

    print(f"[Step2] PE+市值过滤后: {len(candidates)} 只候选")

    # Step3: 逐只查K线并评分
    results = []
    for i, (tc, price, fin, mv) in enumerate(candidates):
        try:
            start_d = (datetime.datetime.strptime(date_str, '%Y%m%d') - datetime.timedelta(days=90)).strftime('%Y%m%d')
            d = pro.daily(ts_code=tc, start_date=start_d, end_date=date_str, timeout=8)
            if d is None or len(d) < MA60: continue
            d = d.sort_values('trade_date')
            closes = d['close'].tolist()
            ma5_v  = calc_ma(closes, MA5)
            ma10_v = calc_ma(closes, MA10)
            ma20_v = calc_ma(closes, MA20)
            ma60_v = calc_ma(closes, MA60)
            high_21 = max(closes[-21:]) if len(closes) >= 21 else price
            avg_vol = np.mean(d['vol'].tolist()[-20:]) if len(d) >= 20 else 0
            vol = d.iloc[-1]['vol'] if len(d) > 0 else 0
        except:
            continue

        tsc = tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol)
        if tsc < 60: continue
        fsc = fin_score(fin)
        if fsc < 20: continue
        repeat = count_repeats(tc)
        grade = 'A' if fsc >= 20 and repeat >= 2 else 'B' if fsc >= 20 else 'C'

        results.append({
            'ts_code': tc, 'price': price, 'pe': fin.get('pe'),
            'mv': mv, 'tech_score': tsc, 'fin_score': fsc,
            'repeat': repeat, 'total': tsc + fsc,
            'grade': grade, 'ma5': ma5_v
        })

        if (i+1) % 20 == 0:
            print(f"  K线分析 {i+1}/{len(candidates)} ...")

    results.sort(key=lambda x: x['total'], reverse=True)
    a = [r for r in results if r['grade'] == 'A']
    b = [r for r in results if r['grade'] == 'B']

    print(f"\n===== 结果 =====")
    print(f"A级 ({len(a)}只):")
    for r in a[:10]:
        print(f"  {r['ts_code']} 总分{r['total']} T{r['tech_score']} F{r['fin_score']} "
              f"价{r['price']:.2f} PE{r['pe']:.0f} 市值{r['mv']:.0f}亿 入选{r['repeat']}次")
    print(f"\nB级 ({len(b)}只):")
    for r in b[:5]:
        print(f"  {r['ts_code']} 总分{r['total']} T{r['tech_score']} F{r['fin_score']} "
              f"价{r['price']:.2f} PE{r['pe']:.0f} 市值{r['mv']:.0f}亿 入选{r['repeat']}次")

    for r in a:
        save_record({'date': date_str, 'ts_code': r['ts_code'], 'grade': r['grade'],
                     'score': r['total'], 'close': r['price'], 'pe': r['pe'],
                     'repeat': r['repeat']})

    return results

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime('%Y%m%d'))
