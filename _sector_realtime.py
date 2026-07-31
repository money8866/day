import io, sys, json, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def get_sina_rt(codes):
    url = f'http://hq.sinajs.cn/list={",".join(codes)}'
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
                    pc = float(parts[2]) if parts[2] else 0
                    cur = float(parts[3]) if parts[3] else 0
                    pct = round((cur - pc) / pc * 100, 2) if pc else 0
                    result[code] = {'name': parts[0], 'current': cur, 'prev_close': pc,
                                   'high': float(parts[4]) if parts[4] else 0,
                                   'low': float(parts[5]) if parts[5] else 0,
                                   'pct': pct, 'amt': float(parts[9]) if len(parts)>9 and parts[9] else 0}
            except: pass
        return result
    except: return {}

# === 扩展板块ETF ===
print("=== [A] 主题板块ETF实时行情 ===", flush=True)
etf_list = [
    ('sh512480', '半导体ETF'),
    ('sz159516', '半导体材料设备ETF'),
    ('sz159628', '银行ETF'),
    ('sz159611', '电力ETF(持仓)'),
    ('sh512660', '通信ETF'),
    ('sh512760', '芯片ETF'),
    ('sz159995', '芯片ETF(纳思达)'),
    ('sh513500', '纳指ETF'),
    ('sz159992', '创新药ETF'),
    ('sz159741', '恒生科技ETF'),
    ('sh515980', '人工智能ETF'),
    ('sz159819', '机器人ETF'),
    ('sh512690', '中药ETF'),
    ('sz159509', '消费电子ETF'),
    ('sh588000', '科创50ETF'),
    ('sz159915', '创业板ETF'),
]
etf_rt = get_sina_rt([c for c,n in etf_list])
etf_map = dict(etf_list)

print(f"{'ETF':<22} {'当前价':>7} {'涨跌%':>8} {'最高':>7} {'最低':>7}", flush=True)
print("-"*60, flush=True)
for code, name in etf_list:
    if code in etf_rt:
        d = etf_rt[code]
        print(f"{name:<22} {d['current']:>7.3f} {d['pct']:>+8.2f}% {d['high']:>7.3f} {d['low']:>7.3f}", flush=True)

# === 关键个股实时 ===
print("\n=== [B] 关键个股实时行情 ===", flush=True)
stocks_list = [
    ('sh688012', '中微公司'),
    ('sz002371', '北方华创'),
    ('sh688981', '中芯国际'),
    ('sh688234', '天岳先进'),
    ('sh605358', '立昂微'),
    ('sh688072', '拓荆科技'),
    ('sh688037', '芯源微'),
    ('sh688082', '盛美上海'),
    ('sh688432', '有研硅'),
    ('sh688535', '华岭股份'),
    ('sh603690', '至纯科技'),
    ('sh600519', '贵州茅台'),
    ('sz300750', '宁德时代'),
    ('sh688111', '金山办公'),
    ('sz300760', '迈瑞医疗'),
]
st_rt = get_sina_rt([c for c,n in stocks_list])

# 分类
chips = [c for c,n in stocks_list if any(k in n for k in ['中微','北方','中芯','天岳','立昂','拓荆','芯源','盛美','有研','华岭','至纯'])]
consumer = [c for c,n in stocks_list if any(k in n for k in ['茅台','宁德','金山','迈瑞'])]

print(f"{'名称':<10} {'当前价':>8} {'涨跌%':>7} {'最高':>8} {'最低':>8} {'状态':<8}", flush=True)
print("-"*60, flush=True)
for code, name in stocks_list:
    if code in st_rt:
        d = st_rt[code]
        tag = ''
        if d['pct'] >= 8: tag = '🔥'
        elif d['pct'] >= 5: tag = '⚡'
        elif d['pct'] >= 2: tag = '↑'
        elif d['pct'] >= 0: tag = '↗'
        elif d['pct'] >= -2: tag = '↓'
        else: tag = '🔴'
        print(f"{name:<10} {d['current']:>8.2f} {d['pct']:>+7.2f}% {d['high']:>8.2f} {d['low']:>8.2f} {tag}", flush=True)

# === 情绪综合判断 ===
print("\n=== [C] 情绪综合判断 ===", flush=True)
idx_pcts = {'科创50': etf_rt.get('sh588000',{}).get('pct',0),
            '创业板': etf_rt.get('sz159915',{}).get('pct',0),
            '半导体': etf_rt.get('sh512480',{}).get('pct',0),
            '芯片': etf_rt.get('sh512760',{}).get('pct',0),
            '人工智能': etf_rt.get('sh515980',{}).get('pct',0),
            '机器人': etf_rt.get('sz159819',{}).get('pct',0),
            '创新药': etf_rt.get('sz159992',{}).get('pct',0),
            '消费电子': etf_rt.get('sz159509',{}).get('pct',0),
            '纳指': etf_rt.get('sh513500',{}).get('pct',0),
            '黄金': etf_rt.get('sh518880',{}).get('pct',0),
            '电力': etf_rt.get('sz159611',{}).get('pct',0),
            '银行': etf_rt.get('sz159628',{}).get('pct',0)}
sorted_idx = sorted(idx_pcts.items(), key=lambda x: x[1], reverse=True)
print("主题强弱排名（按涨幅）:", flush=True)
for nm, pct in sorted_idx:
    bar = '█' * int(abs(pct) / 2)
    sign = '+' if pct > 0 else ''
    print(f"  {nm:<10} {sign}{pct:>6.2f}%  {bar}", flush=True)
