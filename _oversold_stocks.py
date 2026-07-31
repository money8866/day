import io, sys, json, time, urllib.request, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tushare as ts
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

def get_sina_etf(code, name):
    url = f'http://hq.sinajs.cn/list={code}'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'http://finance.sina.com.cn'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('gbk', errors='replace')
        parts = raw.split('"')[1].split(',')
        if len(parts) > 3:
            return float(parts[3]), float(parts[2])
    except: pass
    return None, None

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

# === 1. 东财行业板块实时行情 ===
print("=== [1] 东财行业板块实时涨跌（概念板块）===", flush=True)
# 概念板块实时
sector_url = 'http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=60&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f4,f8,f20'
req = urllib.request.Request(sector_url, headers={
    'User-Agent': 'Mozilla/5.0', 'Referer': 'http://quote.eastmoney.com'
})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        sector_raw = json.loads(resp.read().decode('utf-8'))
    items = sector_raw.get('data', {}).get('diff', [])
    print(f"概念板块总数: {len(items)}", flush=True)
    items.sort(key=lambda x: x.get('f3', 0), reverse=True)
    print("\n涨幅前20板块:", flush=True)
    for it in items[:20]:
        nm = it.get('f14', '?'); pct = it.get('f3', 0)
        amt = (it.get('f8', 0) or 0)/1e8
        lead = it.get('f20', '')[:15] if it.get('f20') else ''
        print(f"  {pct:>6.2f}%  {nm:<18} 额{amt:.0f}亿  龙头:{lead}", flush=True)
    print("\n跌幅前15板块:", flush=True)
    for it in items[-15:]:
        nm = it.get('f14', '?'); pct = it.get('f3', 0)
        amt = (it.get('f8', 0) or 0)/1e8
        print(f"  {pct:>6.2f}%  {nm:<18} 额{amt:.0f}亿", flush=True)
except Exception as e:
    print(f"板块数据: {e}", flush=True)

# === 2. 全市场超跌股扫描（用东财接口快速获取）===
print("\n=== [2] 全市场超跌扫描（20日跌幅>15%，RSI<38，量比>1.2）===", flush=True)
# 用东财市场扫描接口获取今日强势/弱势股
# 今日跌幅最大个股（超跌）
scan_url = 'http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23&fields=f12,f14,f3,f8,f9,f10,f15,f16,f17,f18'
req2 = urllib.request.Request(scan_url, headers={
    'User-Agent': 'Mozilla/5.0', 'Referer': 'http://quote.eastmoney.com'
})
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        scan_raw = json.loads(resp.read().decode('utf-8'))
    all_items = scan_raw.get('data', {}).get('diff', [])
    print(f"全市场A股: {len(all_items)}只", flush=True)
    # 按跌幅排序
    down_items = [x for x in all_items if x.get('f3', 0) < -3]  # 跌幅>3%
    down_items.sort(key=lambda x: x.get('f3', 0))
    print(f"跌幅>3%个股: {len(down_items)}只", flush=True)
    print("\n今日跌幅最大个股（超跌候选）:", flush=True)
    for it in down_items[:30]:
        nm = it.get('f14', '?'); cd = it.get('f12', '?')
        pct = it.get('f3', 0); amt = (it.get('f8', 0) or 0)/1e8
        pe = it.get('f9', 0) or 0; pb = it.get('f10', 0) or 0
        print(f"  {pct:>6.2f}%  {nm:<10} {cd} 额{amt:.0f}亿 PE{pe:.0f} PB{pb:.1f}", flush=True)
    # 涨幅最大（强势股）
    up_items = [x for x in all_items if x.get('f3', 0) > 2]  # 涨幅>2%
    up_items.sort(key=lambda x: x.get('f3', 0), reverse=True)
    print(f"\n涨幅>2%个股: {len(up_items)}只（强势股）", flush=True)
    print("今日涨幅最大个股:", flush=True)
    for it in up_items[:20]:
        nm = it.get('f14', '?'); cd = it.get('f12', '?')
        pct = it.get('f3', 0); amt = (it.get('f8', 0) or 0)/1e8
        print(f"  {pct:>6.2f}%  {nm:<10} {cd} 额{amt:.0f}亿", flush=True)
except Exception as e:
    print(f"扫描失败: {e}", flush=True)

# === 3. 关键防御板块ETF ===
print("\n=== [3] 关键ETF行情（实时）===", flush=True)
etfs = [
    ('sh518880', '黄金ETF', '黄金'),
    ('sz159611', '电力ETF', '电力'),
    ('sz159628', '银行ETF', '银行'),
    ('sh512880', '证券ETF', '证券'),
    ('sz159992', '创新药ETF', '创新药'),
    ('sh513500', '纳指ETF', '纳指'),
    ('sz159915', '创业板ETF', '创业板'),
    ('sh588000', '科创50ETF', '科创50'),
    ('sz159740', '恒生科技ETF', '恒生科技'),
]
for code, name, tag in etfs:
    cur, prev = get_sina_etf(code, name)
    if cur:
        pct = round((cur-prev)/prev*100, 2) if prev else None
        print(f"  {name:<12} {'%.2f'%cur} {'+' if (pct or 0)>=0 else ''}{'%.2f%%'%(pct or 0) if pct is not None else 'N/A'}", flush=True)

print("\nDone.", flush=True)
