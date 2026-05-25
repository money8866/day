# -*- coding: utf-8 -*-
"""V3 TDX版 - K线改用通达信本地数据"""
import os, sys, json, pickle, datetime, warnings
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

TDX_PATH  = r"C:\new_tdx\vipdoc"
FIN_CACHE = r'C:\Users\kongx\mystock\fin_cache_v4.pkl'
HIST_DIR  = r'C:\Users\kongx\mystock\screen_history'
MA5, MA10, MA20, MA60 = 5, 10, 20, 60

# ===== 通达信日线读取 =====
def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath): return None
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32: break
            date_int = int.from_bytes(chunk[0:4], "little")
            open_p   = int.from_bytes(chunk[4:8], "little") / 100
            high_p   = int.from_bytes(chunk[8:12], "little") / 100
            low_p    = int.from_bytes(chunk[12:16], "little") / 100
            close_p  = int.from_bytes(chunk[16:20], "little") / 100
            volume   = int.from_bytes(chunk[20:24], "little")
            amount   = int.from_bytes(chunk[24:28], "little") / 100.0
            dt = datetime.datetime.strptime(str(date_int), "%Y%m%d")
            data.append({"date": dt, "date_int": date_int,
                         "open": open_p, "high": high_p, "low": low_p,
                         "close": close_p, "volume": volume, "amount": amount})
    return data  # 列表，无DataFrame依赖

def get_tdx_kline(ts_code, end_date_str, n_days=100):
    """读取TDX日线数据，返回最近n_days条（含end_date）"""
    # 转换代码
    code = ts_code.split('.')[0]       # 000001
    market = ts_code.split('.')[1].lower()  # sz / sh
    subdir = "lday"
    filename = f"{market}{code}.day"
    filepath = os.path.join(TDX_PATH, market, subdir, filename)
    raw = parse_tdx_day_file(filepath)
    if not raw: return None

    # 过滤到 end_date（含）
    end_dt = datetime.datetime.strptime(end_date_str, '%Y%m%d')
    filtered = [r for r in raw if r['date'] <= end_dt]
    if len(filtered) < MA60: return None
    # 取最近 n_days 条
    recent = filtered[-n_days:]
    return recent

# ===== 工具函数 =====
def load_fin_cache():
    if os.path.exists(FIN_CACHE):
        try: return pickle.load(open(FIN_CACHE, 'rb'))
        except: pass
    return {}

def save_fin_cache(cache):
    pickle.dump(cache, open(FIN_CACHE, 'wb'))

def calc_ma(closes, n):
    if len(closes) < n: return None
    return np.mean(closes[-n:])

def load_histories():
    h = {}
    if os.path.exists(HIST_DIR):
        for f in os.listdir(HIST_DIR):
            if f.startswith('history_') and f.endswith('.json'):
                try:
                    with open(os.path.join(HIST_DIR, f), 'r', encoding='utf-8') as f0:
                        h.update(json.load(f0))
                except: pass
    return h

def count_repeats(tc, before):
    hist = load_histories()
    if tc not in hist: return 0
    return sum(1 for r in hist[tc].get('records', []) if r.get('date', '') < before)

def save_record(r):
    ym = r.get('date', datetime.date.today().strftime('%Y%m%d'))[:6]
    path = os.path.join(HIST_DIR, f'history_{ym}.json')
    os.makedirs(HIST_DIR, exist_ok=True)
    h = {}
    if os.path.exists(path):
        try: h = json.load(open(path, 'r', encoding='utf-8'))
        except: pass
    tc = r['ts_code']
    if tc not in h: h[tc] = {'records': [], 'ts_codes': []}
    h[tc]['records'].append(r)
    with open(path, 'w', encoding='utf-8') as f0:
        json.dump(h, f0, ensure_ascii=False, indent=2)

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
        df = pro.fina_indicator(ts_code=tc,
            start_date=(datetime.datetime.now()-datetime.timedelta(days=120)).strftime('%Y%m%d'),
            timeout=10)
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            f = {'grossprofit_margin': row.get('grossprofit_margin'),
                 'netprofit_margin': row.get('netprofit_margin'),
                 'op_yoy': row.get('op_yoy'),
                 'debt_to_assets': row.get('debt_to_assets'),
                 'ocf_to_or': row.get('ocf_to_or')}
            cache[tc] = f
            save_fin_cache(cache)
            return f
    except: pass
    return None

def ts_code_to_tdx_path(tc):
    """检查TDX文件是否存在"""
    code = tc.split('.')[0]
    market = tc.split('.')[1].lower()
    path = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
    return path

# ===== 主流程 =====
def main(date_str):
    print(f"V3 TDX版 - {date_str}")
    print("=" * 60)

    # Step1: 申万动量（可选）
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
    print(f"[Step1] 申万主线: {hot[:5] if hot else '无数据'}")

    # Step2: PE+市值（Tushare daily_basic，1次调用）
    print(f"[Step2] 获取全市场PE+市值...")
    try:
        df_basic = pro.daily_basic(trade_date=date_str, timeout=15)
        if df_basic is None or len(df_basic) == 0:
            print("获取daily_basic失败"); return
        df_basic = df_basic.dropna(subset=['pe', 'total_mv'])
        df_basic = df_basic[(df_basic['pe'] > 0) & (df_basic['pe'] <= 100)]
        df_basic['mv_yi'] = df_basic['total_mv'] / 10000
        df_basic = df_basic[(df_basic['mv_yi'] >= 100) & (df_basic['mv_yi'] <= 500)]
        print(f"    PE+市值过滤: {len(df_basic)} 只候选")
    except Exception as e:
        print(f"获取daily_basic失败: {e}"); return

    # Step3: 基本面评分
    print(f"[Step3] 基本面评分...")
    fin_cache = load_fin_cache()
    scored = []
    for _, row in df_basic.iterrows():
        tc = row['ts_code']
        fin = fin_cache.get(tc)
        if fin is None:
            fin = get_fin_cached(tc, fin_cache)
        if fin is None: continue
        fsc = fin_score(fin)
        if fsc >= 15:
            scored.append({'ts_code': tc, 'price': row['close'],
                          'pe': row['pe'], 'mv_yi': row['mv_yi'], 'fin_score': fsc})

    print(f"    基本面≥15分: {len(scored)} 只")
    if not scored: print("无候选"); return

    # Step4: TDX K线技术评分
    print(f"[Step4] TDX K线技术分析...")
    results = []
    for i, s in enumerate(scored):
        tc, price = s['ts_code'], s['price']

        # 检查TDX文件是否存在
        tdx_path = ts_code_to_tdx_path(tc)
        if not os.path.exists(tdx_path):
            continue

        kdata = get_tdx_kline(tc, date_str, n_days=100)
        if not kdata or len(kdata) < MA60:
            continue

        closes = [r['close'] for r in kdata]
        vols   = [r['volume'] for r in kdata]
        ma5_v  = calc_ma(closes, MA5)
        ma10_v = calc_ma(closes, MA10)
        ma20_v = calc_ma(closes, MA20)
        ma60_v = calc_ma(closes, MA60)
        high_21 = max(closes[-21:]) if len(closes) >= 21 else price
        avg_vol  = np.mean(vols[-20:]) if len(vols) >= 20 else 0
        vol = vols[-1] if vols else 0

        tsc = tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol)
        if tsc < 30: continue

        repeat = count_repeats(tc, date_str)
        fin_sc = s['fin_score']
        grade = 'A' if fin_sc >= 15 and repeat >= 2 else 'B' if fin_sc >= 15 else 'C'

        results.append({
            'ts_code': tc, 'price': price, 'pe': s['pe'], 'mv_yi': s['mv_yi'],
            'tech_score': tsc, 'fin_score': fin_sc,
            'repeat': repeat, 'total': tsc + fin_sc,
            'grade': grade, 'ma5': ma5_v
        })

        if (i+1) % 50 == 0:
            print(f"    TDX分析 {i+1}/{len(scored)} (当前候选{len(results)}) ...")

    results.sort(key=lambda x: x['total'], reverse=True)
    a = [r for r in results if r['grade'] == 'A']
    b = [r for r in results if r['grade'] == 'B']

    print(f"\n===== V3 TDX选股结果 ({date_str}) =====")
    print(f"🏆 A级 ({len(a)}只) - 二次入选+基本面≥15 → 可买入:")
    for r in a[:10]:
        print(f"  {r['ts_code']} 总分{r['total']} T{r['tech_score']} F{r['fin_score']} "
              f"价{r['price']:.2f} PE{r['pe']:.0f} 市值{r['mv_yi']:.0f}亿 入选{r['repeat']}次")
    print(f"\n📋 B级 ({len(b)}只) - 首次入选+基本面≥15 → 观察:")
    for r in b[:20]:
        print(f"  {r['ts_code']} 总分{r['total']} T{r['tech_score']} F{r['fin_score']} "
              f"价{r['price']:.2f} PE{r['pe']:.0f} 市值{r['mv_yi']:.0f}亿 入选{r['repeat']}次")

    for r in a:
        save_record({'date': date_str, 'ts_code': r['ts_code'], 'grade': r['grade'],
                     'score': r['total'], 'close': r['price'], 'pe': r['pe'], 'repeat': r['repeat']})

    return results

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime('%Y%m%d'))
