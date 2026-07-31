import io, sys, json, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tushare as ts
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

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

def get_sina_rt(mkt, code):
    sina = f"{'sh' if mkt=='SH' else 'sz'}{code}"
    url = f'http://hq.sinajs.cn/list={sina}'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'http://finance.sina.com.cn'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('gbk', errors='replace')
        parts = raw.split('"')[1].split(',')
        if len(parts) > 5:
            return float(parts[2]), float(parts[3])
    except: pass
    return None, None

# 159516 持仓（2026-06-30，stk_mkv_ratio>1%）
etf_holds = [
    ('688012', 'SH', '中微公司',    15.28, '刻蚀设备龙头'),
    ('002371', 'SZ', '北方华创',    13.49, '半导体设备平台'),
    ('300604', 'SZ', '长川科技',     6.54, '测试设备龙头'),
    ('688072', 'SH', '拓荆科技',     6.51, '薄膜沉积设备'),
    ('688120', 'SH', '华大九天',     5.56, 'EDA软件龙头'),
    ('688361', 'SH', '中科飞测',     4.57, '半导体检测'),
    ('688200', 'SH', '华峰测控',     3.18, '封装测试设备'),
    ('688037', 'SH', '芯源微',       3.09, '涂胶显影设备'),
    ('688126', 'SH', '沪硅产业',     2.86, '硅片材料'),
    ('300666', 'SZ', '苏试试验',     2.51, '环境可靠性测试'),
    ('002409', 'SZ', '雅克科技',     2.76, '半导体材料'),
    ('688082', 'SH', '盛美上海',     2.20, '清洗设备龙头'),
    ('605358', 'SH', '立昂微',       2.41, '硅片+功率'),
    ('688234', 'SH', '有研硅',       2.46, '硅片材料'),
    ('300655', 'SZ', '晶瑞电材',     2.23, '光刻胶/湿电子'),
    ('603690', 'SH', '至纯科技',     1.25, '湿法清洗设备'),
    ('688409', 'SH', '富乐德',       1.34, '半导体洗净服务'),
    ('600206', 'SH', '有研新材',     1.57, '靶材材料'),
    ('300236', 'SZ', '上海新阳',     1.80, '电镀液/光刻胶'),
    ('688652', 'SH', '屹唐装备',     1.26, '去胶/刻蚀设备'),
    ('301611', 'SZ', '天工股份',     1.91, '石墨模具'),
    ('688432', 'SH', '有研硅',       0.60, '硅片（已跌停）'),
    ('688535', 'SH', '华岭股份',     0.66, 'IC测试服务'),
    ('688082', 'SH', '盛美上海',     2.20, '清洗设备'),
]

# 去重（保留权重最高的）
seen = set()
etf_holds_dedup = []
for h in etf_holds:
    if h[0] not in seen:
        seen.add(h[0])
        etf_holds_dedup.append(h)

print("=== 159516 成分股超跌扫描 ===", flush=True)
print(f"{'名称':<8} {'代码':<10} {'持仓%':>6} {'细分领域':<16} {'今日':>6} {'1日':>6} {'3日':>6} {'5日':>6} {'10日':>7} {'20日':>7} {'RSI14':>6} {'超跌分':>6}", flush=True)
print("-"*115, flush=True)

results = []
for code, mkt, name, weight, tag in etf_holds_dedup:
    ts_code = f"{code}.{mkt}"
    prev, cur = get_sina_rt(mkt, code)
    today_pct = round((cur - prev) / prev * 100, 2) if prev and cur else 0.0

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

        rsi_score = max(0, (50 - rsi) / 50 * 40) if rsi else 0
        drop20_score = max(0, -p20 / 50 * 40) if p20 else 0
        today_score = max(0, -today_pct / 10 * 20) if today_pct else 0
        total_score = round(rsi_score + drop20_score + today_score, 1)

        results.append({
            'name': name, 'code': code, 'mkt': mkt, 'weight': weight, 'tag': tag,
            'prev_close': prev, 'current': cur, 'today_pct': today_pct,
            'p1': p1, 'p3': p3, 'p5': p5, 'p10': p10, 'p20': p20,
            'rsi': rsi, 'score': total_score
        })
    except Exception as e:
        pass

results.sort(key=lambda x: x['score'], reverse=True)

for r in results:
    def fv(v,w=7): return f'{v:>+{w}.1f}'
    print(f"{r['name']:<8} {r['code']:<10} {r['weight']:>5.2f}% {r['tag']:<16} {fv(r['today_pct'])} {fv(r['p1'])} {fv(r['p3'])} {fv(r['p5'])} {fv(r['p10'],7)} {fv(r['p20'],7)} {r['rsi']:>6.1f} {r['score']:>6.1f}", flush=True)

print("-"*115, flush=True)
print(f"\n=== 超跌TOP10（按综合超跌评分）===", flush=True)
for i, r in enumerate(results[:10], 1):
    star = '⭐' if r['score'] >= 50 else ''
    print(f"\n{i}. {r['name']}({r['code']}) 持仓{r['weight']:.1f}% {star}", flush=True)
    print(f"   细分: {r['tag']}", flush=True)
    print(f"   今日: {r['today_pct']:+.2f}%  |  20日: {r['p20']:+.2f}%  |  RSI: {r['rsi']}", flush=True)
    print(f"   超跌综合评分: {r['score']:.1f}/100", flush=True)
