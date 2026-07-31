import io, sys, json, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def get_sina_rt(codes_with_mkt):
    """批量获取新浪实时行情"""
    url = f'http://hq.sinajs.cn/list={",".join(codes_with_mkt)}'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'http://finance.sina.com.cn'})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode('gbk', errors='replace')
        result = {}
        for line in raw.strip().split('\n'):
            if '=' not in line: continue
            try:
                code_part = line.split('=')[0].strip()
                code = code_part.split('_')[-1]
                rest = line.split('=', 1)[1].strip('"; \r\n')
                parts = rest.split(',')
                if len(parts) >= 6:
                    name = parts[0]
                    open_p = float(parts[1]) if parts[1] else 0
                    prev_close = float(parts[2]) if parts[2] else 0
                    current = float(parts[3]) if parts[3] else 0
                    high = float(parts[4]) if parts[4] else 0
                    low = float(parts[5]) if parts[5] else 0
                    vol = float(parts[8]) if len(parts) > 8 and parts[8] else 0
                    amt = float(parts[9]) if len(parts) > 9 and parts[9] else 0
                    pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0
                    result[code] = {'name': name, 'current': current, 'prev_close': prev_close,
                                   'open': open_p, 'high': high, 'low': low, 'pct': pct, 'vol': vol, 'amt': amt}
            except: pass
        return result
    except Exception as e:
        return {}

# === 1. 主要指数 ===
print("=== [1] 主要指数实时行情 ===", flush=True)
idx_codes = ['sh000001', 'sz399001', 'sz399006', 'sh000300', 'sz399852', 'sh000688', 'sz399673']
idx_names = {'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指',
             'sh000300': '沪深300', 'sz399852': '中证1000', 'sh000688': '科创50', 'sz399673': '创业板50'}
idx_rt = get_sina_rt(idx_codes)

print(f"{'名称':<10} {'昨收':>9} {'今开':>9} {'当前':>9} {'最高':>9} {'最低':>9} {'涨跌%':>7} {'成交额(亿)':>10}", flush=True)
print("-"*85, flush=True)
for code in idx_codes:
    if code in idx_rt:
        d = idx_rt[code]
        nm = idx_names.get(code, d['name'])
        amt = d['amt'] / 1e8
        print(f"{nm:<10} {d['prev_close']:>9.2f} {d['open']:>9.2f} {d['current']:>9.2f} {d['high']:>9.2f} {d['low']:>9.2f} {d['pct']:>+7.2f}% {amt:>10.1f}", flush=True)

# === 2. 半导体ETF及成分股 ===
print("\n=== [2] 半导体板块实时行情 ===", flush=True)
semi_codes = [
    'sh512480',  # 半导体ETF
    'sz159516',  # 半导体材料设备ETF
    'sh688012',  # 中微公司
    'sz002371',  # 北方华创
    'sh688981',  # 中芯国际
    'sh688347',  # 华海清科
    'sh688072',  # 拓荆科技
    'sh688037',  # 芯源微
    'sh688082',  # 盛美上海
    'sh603690',  # 至纯科技
    'sh688234',  # 天岳先进
    'sh688432',  # 有研硅
    'sh688535',  # 华岭股份
    'sh605358',  # 立昂微
]
semi_rt = get_sina_rt(semi_codes)

semi_names = {
    'sh512480': '半导体ETF', 'sz159516': '半导体材料设备ETF',
    'sh688012': '中微公司', 'sz002371': '北方华创', 'sh688981': '中芯国际',
    'sh688347': '华海清科', 'sh688072': '拓荆科技', 'sh688037': '芯源微',
    'sh688082': '盛美上海', 'sh603690': '至纯科技', 'sh688234': '天岳先进',
    'sh688432': '有研硅', 'sh688535': '华岭股份', 'sh605358': '立昂微',
}

print(f"{'名称':<16} {'当前价':>8} {'涨跌%':>7} {'最高':>8} {'最低':>8} {'成交额(万)':>10}", flush=True)
print("-"*70, flush=True)
for code in semi_codes:
    if code in semi_rt:
        d = semi_rt[code]
        nm = semi_names.get(code, d['name'])
        amt = d['amt'] / 1e4
        print(f"{nm:<16} {d['current']:>8.2f} {d['pct']:>+7.2f}% {d['high']:>8.2f} {d['low']:>8.2f} {amt:>10.0f}", flush=True)

# === 3. 防御板块 ===
print("\n=== [3] 防御/避险板块 ===", flush=True)
def_codes = [
    'sh518880',  # 黄金ETF
    'sz159611',  # 电力ETF
    'sz159928',  # 消费ETF
    'sh512880',  # 证券ETF
    'sz159992',  # 创新药ETF
]
def_rt = get_sina_rt(def_codes)

def_names = {'sh518880': '黄金ETF', 'sz159611': '电力ETF', 'sz159928': '消费ETF',
             'sh512880': '证券ETF', 'sz159992': '创新药ETF'}

print(f"{'名称':<12} {'当前价':>8} {'涨跌%':>7} {'最高':>8} {'最低':>8}", flush=True)
print("-"*50, flush=True)
for code in def_codes:
    if code in def_rt:
        d = def_rt[code]
        nm = def_names.get(code, d['name'])
        print(f"{nm:<12} {d['current']:>8.2f} {d['pct']:>+7.2f}% {d['high']:>8.2f} {d['low']:>8.2f}", flush=True)

# === 4. 市场情绪判断 ===
print("\n=== [4] 市场情绪判断 ===", flush=True)
up_count = sum(1 for d in idx_rt.values() if d['pct'] > 0)
down_count = sum(1 for d in idx_rt.values() if d['pct'] < 0)
semi_up = sum(1 for code, d in semi_rt.items() if code.startswith(('sh688','sz','sh603')) and d['pct'] > 0)
semi_down = sum(1 for code, d in semi_rt.items() if code.startswith(('sh688','sz','sh603')) and d['pct'] < 0)

gem_pct = idx_rt.get('sz399006', {}).get('pct', 0)
star_pct = idx_rt.get('sh000688', {}).get('pct', 0)

print(f"主要指数: 上涨{up_count}只 / 下跌{down_count}只", flush=True)
print(f"半导体样本: 上涨{semi_up}只 / 下跌{semi_down}只", flush=True)
print(f"创业板指: {gem_pct:+.2f}%  |  科创50: {star_pct:+.2f}%", flush=True)

if star_pct > 5:
    print("\n✅ 今日市场情绪：**强反弹**（科创50涨幅>5%）", flush=True)
elif star_pct > 2:
    print("\n✅ 今日市场情绪：**明显反弹**（科创50涨幅>2%）", flush=True)
elif star_pct > 0:
    print("\n🟡 今日市场情绪：**弱反弹**（科创50涨幅0-2%）", flush=True)
else:
    print("\n🔴 今日市场情绪：**继续下跌**", flush=True)

print("\n数据时间: 实时（新浪）", flush=True)
