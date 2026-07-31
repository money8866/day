import io, sys, json, time, urllib.request, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tushare as ts
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# === 1. 新浪实时指数行情 ===
print("=== [1] 指数实时行情 ===", flush=True)
sina_codes = {
    'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指',
    'sh000300': '沪深300', 'sz399852': '中证1000', 'sh000688': '科创50',
    'sz399673': '创业板50'
}
url = 'http://hq.sinajs.cn/list=' + ','.join(sina_codes.keys())
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://finance.sina.com.cn'
})
with urllib.request.urlopen(req, timeout=10) as resp:
    raw = resp.read().decode('gbk', errors='replace')

rt_data = {}
for line in raw.strip().split('\n'):
    if '=' not in line: continue
    try:
        code_part = line.split('=')[0].strip()
        code = code_part.split('_')[-1] if 'hq_str_' in code_part else ''
        rest = line.split('=', 1)[1].strip('"; \r\n')
        parts = rest.split(',')
        if len(parts) >= 6:
            name = parts[0]
            open_p = float(parts[1])
            prev_close = float(parts[2])
            current = float(parts[3])
            high = float(parts[4])
            low = float(parts[5])
            pct = round((current - prev_close) / prev_close * 100, 2)
            rt_data[code] = {'name': name, 'current': current, 'prev_close': prev_close,
                            'open': open_p, 'high': high, 'low': low, 'pct': pct}
    except: pass

print(f"{'名称':<10} {'昨收':>9} {'今开':>9} {'当前':>9} {'最高':>9} {'最低':>9} {'涨跌%':>7}", flush=True)
print("-"*72, flush=True)
for code, d in rt_data.items():
    print(f"{d['name']:<10} {d['prev_close']:>9.2f} {d['open']:>9.2f} {d['current']:>9.2f} {d['high']:>9.2f} {d['low']:>9.2f} {d['pct']:>7.2f}%", flush=True)

# === 2. Tushare获取指数历史K线计算RSI ===
print("\n=== [2] 指数RSI分析（最近60日） ===", flush=True)
ts_codes = {
    '000001.SH': '上证指数', '399001.SZ': '深证成指', '399006.SZ': '创业板指',
    '000300.SH': '沪深300', '000852.SH': '中证1000', '000688.SH': '科创50',
    '399673.SZ': '创业板50'
}

def calc_rsi14(closes):
    if len(closes) < 15: return None
    ds = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    ag = sum(max(d,0) for d in ds[:14])/14
    al = sum(max(-d,0) for d in ds[:14])/14
    for i in range(14, len(ds)):
        ag = (ag*13+max(ds[i],0))/14
        al = (al*13+max(-ds[i],0))/14
    if al == 0: return 100.0
    return round(100-100/(1+ag/al), 1)

results = []
for ts_code, name in ts_codes.items():
    try:
        df = pro.index_daily(ts_code=ts_code, end_date='20260730', limit=65)
        if df is None or len(df) < 20:
            results.append((name, ts_code, 'NO_DATA', None, None, None, None, None, None, None)); continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].tolist()
        highs = df['high'].tolist()
        lows = df['low'].tolist()
        lc = float(df.iloc[-1]['close'])
        dt = str(df.iloc[-1]['trade_date'])
        def pct(n):
            if len(df) < n+1: return None
            return round((lc - closes[-(n+1)]) / closes[-(n+1)] * 100, 2)
        rsi = calc_rsi14(closes)
        if len(df) >= 20:
            h20 = max(highs[-20:]); l20 = min(lows[-20:])
            fh = round((lc-h20)/h20*100, 2)
        else: fh = None
        results.append((name, ts_code, dt, lc, pct(1), pct(3), pct(5), pct(10), pct(20), rsi, fh))
    except Exception as e:
        results.append((name, ts_code, f'ERR:{e}', None, None, None, None, None, None, None, None))

results.sort(key=lambda x: x[9] if x[9] is not None else 999)

print(f"{'名称':<10} {'日期':<8} {'收盘':>9} {'1日':>6} {'3日':>6} {'5日':>6} {'10日':>7} {'20日':>7} {'RSI14':>6} {'距高点%':>8}", flush=True)
print("-"*88, flush=True)
for r in results:
    nm, tc, dt, cl, g1, g3, g5, g10, g20, rsi, fh = r
    def fv(v,w=7): return f'{v:>{w}.1f}' if v is not None else '    N/A'
    def fv2(v,w=9): return f'{v:>{w}.2f}' if v is not None else '       N/A'
    print(f"{nm:<10} {str(dt):<8} {fv2(cl,9)} {fv(g1)} {fv(g3)} {fv(g5)} {fv(g10,7)} {fv(g20,7)} {fv(rsi)} {fv2(fh)}", flush=True)

# === 3. 涨停板分析 ===
print("\n=== [3] 今日涨停板分析 ===", flush=True)
try:
    lu = pro.limit_list_d(trade_date='2026-07-30', limit_type='U', asset='I')
    if lu is not None and len(lu) > 0:
        print(f"涨停总数（指数）: {len(lu)}", flush=True)
        lu_s = pro.limit_list_d(trade_date='2026-07-30', limit_type='U', asset='E')
        if lu_s is not None and len(lu_s) > 0:
            print(f"涨停总数（个股）: {len(lu_s)}", flush=True)
            # 按行业/概念分组
            ind_cnt = {}
            for _, row in lu_s.iterrows():
                ind = row.get('industry', '未知')
                ind_cnt[ind] = ind_cnt.get(ind, 0) + 1
            top_ind = sorted(ind_cnt.items(), key=lambda x: x[1], reverse=True)[:10]
            print("\n涨停行业分布TOP10:", flush=True)
            for ind, cnt in top_ind:
                print(f"  {ind}: {cnt}只", flush=True)
            # 涨幅分布
            pct_dist = {'10%+': 0, '9-10%': 0, '5-9%': 0, '0-5%': 0}
            for _, row in lu_s.iterrows():
                pct = row.get('pct_chg', 0) or 0
                if pct >= 10: pct_dist['10%+'] += 1
                elif pct >= 9: pct_dist['9-10%'] += 1
                elif pct >= 5: pct_dist['5-9%'] += 1
                else: pct_dist['0-5%'] += 1
            print(f"\n涨幅分布: 10%+={pct_dist['10%+']} 9-10%={pct_dist['9-10%']} 5-9%={pct_dist['5-9%']} 0-5%={pct_dist['0-5%']}", flush=True)
        else:
            print("无个股涨停数据", flush=True)
    else:
        print("今日涨停数据未更新（可能非交易日或数据未入库）", flush=True)
except Exception as e:
    print(f"涨停数据获取失败: {e}", flush=True)

print("\nDone.", flush=True)
