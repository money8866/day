import io, sys, json, urllib.request, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 新浪指数行情接口
# sina格式: sh000001=上证, sz399001=深成, sz399006=创业板, sh000300=沪深300, sh000905=中证500, sz399852=中证1000, sz932000=中证2000, sh000688=科创50
codes = [
    ('sh000001', '上证指数'),
    ('sz399001', '深证成指'),
    ('sz399006', '创业板指'),
    ('sh000300', '沪深300'),
    ('sz399852', '中证1000'),
    ('sh000688', '科创50'),
    ('sz932000', '中证2000'),
    ('sz399673', '创业板50'),
]

url = 'http://hq.sinajs.cn/list=' + ','.join([c[0] for c in codes])
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'http://finance.sina.com.cn'
})

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode('gbk', errors='replace')
except Exception as e:
    print(f"Network error: {e}", flush=True)
    raw = ''

# 解析新浪数据: var hq_str_sz399001="深证成指,15493.418,15512.970,14123.310,14302.840,13958.440,14123.310,14123.310,789654321,..."
# 字段: 名称,今开,昨收,当前,最高,最低,...
lines = raw.strip().split('\n')
price_data = {}
for line in lines:
    line = line.strip()
    if not line or '=' not in line: continue
    try:
        # hq_str_sz399001="xxx"
        rest = line.split('=', 1)[1].strip('"; \r\n')
        parts = rest.split(',')
        if len(parts) >= 10:
            code = line.split('_')[0].split('hq_str_')[1] if 'hq_str_' in line else ''
            name = parts[0]
            open_p = float(parts[1])
            prev_close = float(parts[2])
            current = float(parts[3])
            high = float(parts[4])
            low = float(parts[5])
            price_data[code] = {'name': name, 'current': current, 'open': open_p, 'prev_close': prev_close, 'high': high, 'low': low}
    except: pass

# 计算实时涨跌幅
def real_pct(current, prev):
    return round((current - prev) / prev * 100, 2) if prev else None

# 获取历史数据用Tushare
# 先试试能否用tushare获取历史
pro = None
import tushare
TOKEN = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
try:
    pro = tushare.pro_api(TOKEN)
    print(f"Tushare connected OK", flush=True)
except Exception as e:
    print(f"Tushare init error: {e}", flush=True)

# 指数代码映射 (tushare格式)
ts_codes = {
    'sh000001': '000001.SH', 'sz399001': '399001.SZ', 'sz399006': '399006.SZ',
    'sh000300': '000300.SH', 'sz399852': '000852.SH', 'sh000688': '000688.SH',
    'sz932000': '932000.SH', 'sz399673': '399673.SZ',
}

# 如果Tushare可用，获取历史RSI和涨跌幅
results = []
if pro:
    for sina_code, name in codes:
        ts_code = ts_codes.get(sina_code)
        try:
            df = pro.index_daily(ts_code=ts_code, end_date='20260730', limit=60)
            if df is None or len(df) < 20:
                results.append((name, sina_code, ts_code, 'NO_DATA', None, None, None, None, None, None, None, None, None))
                continue
            
            df = df.sort_values('trade_date')
            closes = df['close'].tolist()
            highs = df['high'].tolist()
            lows = df['low'].tolist()
            latest = df.iloc[-1]
            lc = float(latest['close'])
            dt = str(latest['trade_date'])
            
            def pct(n):
                if len(df) < n+1: return None
                return round((lc - closes[-(n+1)]) / closes[-(n+1)] * 100, 2)
            
            if len(closes) >= 15:
                ds = [closes[i]-closes[i-1] for i in range(1,len(closes))]
                ag = sum(max(d,0) for d in ds[:14])/14
                al = sum(max(-d,0) for d in ds[:14])/14
                for i in range(14, len(ds)):
                    ag = (ag*13+max(ds[i],0))/14
                    al = (al*13+max(-ds[i],0))/14
                rsi = round(100-100/(1+ag/al), 1) if al != 0 else 100.0
            else:
                rsi = None
            
            if len(df) >= 20:
                h20 = max(highs[-20:]); l20 = min(lows[-20:])
                fh = round((lc-h20)/h20*100, 2)
            else:
                fh = None
            
            rt = price_data.get(sina_code)
            rt_pct = real_pct(rt['current'], rt['prev_close']) if rt else None
            
            results.append((name, sina_code, ts_code, dt, lc, rt_pct, pct(1), pct(3), pct(5), pct(10), pct(20), rsi, fh))
        except Exception as e:
            results.append((name, sina_code, ts_code, f'ERR:{e}', None, None, None, None, None, None, None, None, None))
else:
    for sina_code, name in codes:
        rt = price_data.get(sina_code)
        if rt:
            pct1 = real_pct(rt['current'], rt['prev_close'])
            results.append((name, sina_code, '', '', rt['current'], pct1, None, None, None, None, None, None, pct1))
        else:
            results.append((name, sina_code, '', 'NO_REALTIME', None, None, None, None, None, None, None, None, None))

# 排序
results.sort(key=lambda x: x[9] if x[9] is not None else 999)

print(f"\n{'INDEX':<10} {'DATE':<8} {'CLOSE':>9} {'实时':>6} {'1D':>6} {'3D':>6} {'5D':>6} {'10D':>7} {'20D':>7} {'RSI14':>6} {'F_High%':>8}", flush=True)
print("-"*100, flush=True)
for r in results:
    nm, sc, tc, dt, cl, rt, g1, g3, g5, g10, g20, rsi, fh = r
    def fv(v,w=7): return f'{v:>{w}.1f}' if v is not None else '    N/A'
    def fv2(v,w=9): return f'{v:>{w}.2f}' if v is not None else '       N/A'
    print(f"{nm:<10} {str(dt):<8} {fv2(cl,9)} {fv(rt,6)} {fv(g1)} {fv(g3)} {fv(g5)} {fv(g10,7)} {fv(g20,7)} {fv(rsi)} {fv2(fh)}", flush=True)
print("-"*100, flush=True)

print("\n=== 超跌信号 ===", flush=True)
for r in results:
    nm, sc, tc, dt, cl, rt, g1, g3, g5, g10, g20, rsi, fh = r
    sigs = []
    if rsi and rsi < 30: sigs.append(f"RSI={rsi}(超卖)")
    if rsi and 30 <= rsi < 35: sigs.append(f"RSI={rsi}(接近超卖)")
    if g20 and g20 < -10: sigs.append(f"20日{g20}%")
    if fh and fh < -10: sigs.append(f"距高点{fh}%")
    if sigs: print(f"  {nm}: {' | '.join(sigs)}", flush=True)
