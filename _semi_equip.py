import io, sys, json, urllib.request
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

def get_sina_rt(code_with_mkt):
    """code_with_mkt: e.g. 'sh688012' or 'sz002371'"""
    url = f'http://hq.sinajs.cn/list={code_with_mkt}'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'http://finance.sina.com.cn'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('gbk', errors='replace')
        parts = raw.split('"')[1].split(',')
        if len(parts) > 3:
            return {
                'open': float(parts[1]),
                'prev_close': float(parts[2]),
                'current': float(parts[3]),
                'high': float(parts[4]),
                'low': float(parts[5]),
                'vol': float(parts[8]) if parts[8] else 0,
            }
    except: pass
    return None

# 半导体设备核心成份股（按细分领域）
equip_stocks = [
    ('688012', 'SH', '中微公司',    '刻蚀设备龙头'),
    ('002371', 'SZ', '北方华创',    '半导体设备平台龙头'),
    ('688347', 'SH', '华海清科',    'CMP设备龙头'),
    ('688072', 'SH', '拓荆科技',    '薄膜沉积设备'),
    ('688037', 'SH', '芯源微',      '涂胶显影设备'),
    ('688082', 'SH', '盛美上海',    '清洗设备龙头'),
    ('603690', 'SH', '至纯科技',    '湿法清洗设备'),
    ('688361', 'SH', '中科飞测',    '半导体检测'),
    ('300567', 'SZ', '精测电子',    '检测/量测设备'),
    ('300604', 'SZ', '长川科技',    '测试设备龙头'),
    ('688200', 'SH', '华峰测控',    '封装测试设备'),
    ('688275', 'SH', '万润新能',    '正极材料'),
    ('688148', 'SH', '奕瑞科技',    'X线探测器'),
    ('688521', 'SH', '芯原股份',    '芯片设计服务'),
    ('688126', 'SH', '沪硅产业',    '硅片材料'),
    ('688396', 'SH', '华润微',      '功率半导体'),
    ('002049', 'SZ', '紫光国微',    'FPGA/特种IC'),
    ('688111', 'SH', '金山办公',    '软件'),
    ('300223', 'SZ', '北京君正',    '芯片设计'),
    ('688008', 'SH', '澜起科技',    '内存接口芯片'),
    ('300661', 'SZ', '圣邦股份',    '模拟芯片'),
    ('300782', 'SZ', '卓胜微',      '射频芯片'),
    ('688256', 'SH', '寒武纪',      'AI芯片'),
    ('688981', 'SH', '中芯国际',    '晶圆代工'),
    ('688187', 'SH', '时代电气',    '功率半导体'),
]

print("=== 半导体板块超跌扫描 ===", flush=True)
print(f"{'名称':<10} {'代码':<10} {'细分领域':<16} {'今日':>6} {'1日':>6} {'3日':>6} {'5日':>6} {'10日':>7} {'20日':>7} {'RSI14':>6} {'综合分':>6}", flush=True)
print("-"*105, flush=True)

results = []
for code, mkt, name, tag in equip_stocks:
    ts_code = f"{code}.{mkt}"
    sina_code = f"{'sh' if mkt=='SH' else 'sz'}{code}"
    rt = get_sina_rt(sina_code)
    
    try:
        df = pro.daily(ts_code=ts_code, start_date='20260601', end_date='20260730')
        if df is None or len(df) < 25: continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        closes = df['close'].tolist()
        if len(closes) < 25: continue
        lc = closes[-1]
        
        # 今日涨跌（实时）
        today_pct = 0.0
        if rt:
            today_pct = round((rt['current'] - rt['prev_close']) / rt['prev_close'] * 100, 2)
        
        # 历史涨跌幅
        p1 = round((lc - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0
        p3 = round((lc - closes[-4]) / closes[-4] * 100, 2) if len(closes) >= 4 else 0
        p5 = round((lc - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
        p10 = round((lc - closes[-11]) / closes[-11] * 100, 2) if len(closes) >= 11 else 0
        p20 = round((lc - closes[-21]) / closes[-21] * 100, 2) if len(closes) >= 21 else 0
        rsi = rsi14(closes)
        
        # 综合超跌评分（越大越超跌）
        # 权重: 20日跌幅40% + RSI偏离40% + 今日跌幅20%
        rsi_score = max(0, (50 - rsi) / 50 * 40) if rsi else 0
        drop20_score = max(0, -p20 / 50 * 40) if p20 else 0
        today_score = max(0, -today_pct / 10 * 20) if today_pct else 0
        total_score = round(rsi_score + drop20_score + today_score, 1)
        
        results.append({
            'name': name, 'code': code, 'mkt': mkt, 'tag': tag,
            'today_pct': today_pct,
            'p1': p1, 'p3': p3, 'p5': p5, 'p10': p10, 'p20': p20,
            'rsi': rsi, 'score': total_score,
            'rt': rt
        })
    except Exception as e:
        pass

# 按综合超跌评分排序
results.sort(key=lambda x: x['score'], reverse=True)

for r in results:
    def fv(v,w=7): return f'{v:>+{w}.1f}'
    print(f"{r['name']:<10} {r['code']:<10} {r['tag']:<16} {fv(r['today_pct'])} {fv(r['p1'])} {fv(r['p3'])} {fv(r['p5'])} {fv(r['p10'],7)} {fv(r['p20'],7)} {r['rsi']:>6.1f} {r['score']:>6.1f}", flush=True)

print("-"*105, flush=True)
print(f"\n=== 超跌TOP5（综合评分最高）===", flush=True)
for i, r in enumerate(results[:5], 1):
    print(f"\n{i}. {r['name']}({r['code']}) {r['tag']}", flush=True)
    print(f"   今日: {r['today_pct']:+.2f}%  |  20日: {r['p20']:+.2f}%  |  RSI: {r['rsi']}", flush=True)
    print(f"   综合超跌评分: {r['score']}/100", flush=True)
