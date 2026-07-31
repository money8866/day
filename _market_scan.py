import io, sys, json, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tushare as ts
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# 东方财富全市场实时行情（page=1只取100条，换多个页）
def eastmoney_fullscan(page=1, sort='f3', desc=True, limit=100):
    sort_field = 'f3' if sort == 'f3' else 'f9'
    order = '0' if desc else '1'
    url = (f'http://push2.eastmoney.com/api/qt/clist/get?pn={page}&pz={limit}&po={order}'
           f'&np=1&fltt=2&invt=2&fid={sort_field}'
           f'&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23'
           f'&fields=f12,f14,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21')
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'http://quote.eastmoney.com'
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data.get('data', {}).get('diff', []), data.get('data', {}).get('total', 0)

# === 1. 今日涨跌幅分布（按页扫描前500只）===
print("=== [1] 今日市场涨跌分布（采样500只）===", flush=True)
all_stocks = []
for pg in range(1, 6):  # 5页=500只
    items, total = eastmoney_fullscan(page=pg, sort='f3', desc=True, limit=100)
    all_stocks.extend(items)
    time.sleep(0.3)

# 按涨跌幅分布
pct_buckets = {'涨停(+10%)': 0, '涨幅>5%': 0, '涨幅2-5%': 0, '涨幅0-2%': 0,
               '跌幅0-2%': 0, '跌幅2-5%': 0, '跌幅5-10%': 0, '跌停(-10%)': 0}
for s in all_stocks:
    pct = s.get('f3', 0) or 0
    if pct >= 9.5: pct_buckets['涨停(+10%)'] += 1
    elif pct >= 5: pct_buckets['涨幅>5%'] += 1
    elif pct >= 2: pct_buckets['涨幅2-5%'] += 1
    elif pct >= 0: pct_buckets['涨幅0-2%'] += 1
    elif pct >= -2: pct_buckets['跌幅0-2%'] += 1
    elif pct >= -5: pct_buckets['跌幅2-5%'] += 1
    elif pct >= -9.5: pct_buckets['跌幅5-10%'] += 1
    else: pct_buckets['跌停(-10%)'] += 1

print("涨跌分布（采样500只）:", flush=True)
for k, v in pct_buckets.items():
    bar = '█' * (v // 2)
    print(f"  {k:<12}: {v:>4}只  {bar}", flush=True)

# === 2. 今日强势股（涨幅TOP20）===
print("\n=== [2] 今日强势股TOP20 ===", flush=True)
all_stocks.sort(key=lambda x: x.get('f3', 0), reverse=True)
for i, s in enumerate(all_stocks[:20], 1):
    nm = s.get('f14', '?'); cd = s.get('f12', '?')
    pct = s.get('f3', 0) or 0; amt = (s.get('f8', 0) or 0)/1e8
    pe = s.get('f9', 0) or 0; pb = s.get('f10', 0) or 0
    cur = s.get('f43', 0) or 0; high = s.get('f15', 0) or 0; low = s.get('f16', 0) or 0
    print(f"  {i:>2}. {nm:<10} {cd} {pct:>+6.2f}% 额{amt:.0f}亿 PE{pe:.0f} 最高{high:.2f} 最低{low:.2f}", flush=True)

# === 3. 今日超跌股（跌幅TOP20）===
print("\n=== [3] 今日超跌股（跌幅TOP20）===", flush=True)
all_stocks.sort(key=lambda x: x.get('f3', 0))
for i, s in enumerate(all_stocks[:20], 1):
    nm = s.get('f14', '?'); cd = s.get('f12', '?')
    pct = s.get('f3', 0) or 0; amt = (s.get('f8', 0) or 0)/1e8
    pe = s.get('f9', 0) or 0; pb = s.get('f10', 0) or 0
    cur = s.get('f43', 0) or 0; high = s.get('f15', 0) or 0; low = s.get('f16', 0) or 0
    print(f"  {i:>2}. {nm:<10} {cd} {pct:>+6.2f}% 额{amt:.0f}亿 PE{pe:.0f} 最高{high:.2f} 最低{low:.2f}", flush=True)

# === 4. 超跌且今日相对抗跌（筛选：昨日超跌但今日跌幅<市场平均）===
print("\n=== [4] RSI超跌扫描（用Tushare历史数据）===", flush=True)
# 选一批代表性超跌股
scan_codes = [
    ('600519', 'SH', '贵州茅台'), ('300750', 'SZ', '宁德时代'),
    ('688981', 'SH', '中芯国际'), ('300059', 'SZ', '东方财富'),
    ('002475', 'SZ', '立讯精密'), ('300760', 'SZ', '迈瑞医疗'),
    ('688012', 'SH', '中微公司'), ('600745', 'SH', '闻泰科技'),
    ('300223', 'SZ', '北京君正'), ('002049', 'SZ', '紫光国微'),
    ('688111', 'SH', '金山办公'), ('300496', 'SZ', '中科创达'),
    ('300124', 'SZ', '汇川技术'), ('002371', 'SZ', '北方华创'),
    ('300408', 'SZ', '三环集团'), ('688256', 'SH', '寒武纪'),
    ('688008', 'SH', '澜起科技'), ('300782', 'SZ', '卓胜微'),
    ('688385', 'SH', '复旦微电'), ('300661', 'SZ', '圣邦股份'),
]
def rsi14(closes):
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
for code, mkt, name in scan_codes:
    ts_code = f"{code}.{mkt}"
    try:
        df = pro.daily(ts_code=ts_code, start_date='20260601', end_date='20260730')
        if df is None or len(df) < 25: continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].tolist()
        if len(closes) < 25: continue
        lc = closes[-1]
        p1 = round((lc - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0
        p3 = round((lc - closes[-4]) / closes[-4] * 100, 2) if len(closes) >= 4 else 0
        p5 = round((lc - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
        p10 = round((lc - closes[-11]) / closes[-11] * 100, 2) if len(closes) >= 11 else 0
        p20 = round((lc - closes[-21]) / closes[-21] * 100, 2) if len(closes) >= 21 else 0
        rsi = rsi14(closes)
        results.append((name, code, mkt, lc, p1, p3, p5, p10, p20, rsi))
    except Exception as e:
        pass

results.sort(key=lambda x: x[8])  # 按20日跌幅排序（最超跌排前面）
print(f"{'名称':<10} {'收盘':>8} {'1日':>7} {'3日':>7} {'5日':>7} {'10日':>7} {'20日':>7} {'RSI14':>6}", flush=True)
print("-"*72, flush=True)
for r in results:
    nm, cd, mkt, cl, p1, p3, p5, p10, p20, rsi = r
    def fv(v,w=7): return f'{v:>+{w}.1f}'
    print(f"{nm:<10} {cl:>8.2f} {fv(p1)} {fv(p3)} {fv(p5)} {fv(p10)} {fv(p20)} {rsi:>6.1f}", flush=True)

# === 5. 历史反弹规律总结 ===
print("\n=== [5] 大跌后反弹规律总结 & 今日建议 ===", flush=True)
# 按RSI和超跌程度分类
rsi_oversold = [r for r in results if r[9] and r[9] < 35]
deep_drop = [r for r in results if r[8] and r[8] < -15]
both = [r for r in results if r[9] and r[9] < 35 and r[8] and r[8] < -15]
print(f"RSI<35（超卖）: {len(rsi_oversold)}只", flush=True)
print(f"20日跌>15%（深跌）: {len(deep_drop)}只", flush=True)
print(f"双重信号（RSI<35且20日跌>15%）: {len(both)}只", flush=True)
for r in both:
    nm, cd, mkt, cl, p1, p3, p5, p10, p20, rsi = r
    print(f"  ★ {nm}: 20日{round(r[8],1)}% RSI={rsi}", flush=True)

print("\nDone.", flush=True)
