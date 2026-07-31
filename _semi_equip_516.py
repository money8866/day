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

# === 从东财API获取中证半导体材料设备指数(931743)成分 ===
print("=== 获取159516（半导体材料设备ETF）成分股 ===", flush=True)

# 东财指数成分接口
em_url = 'http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f62&fs=b:BK0716&fields=f12,f14,f62,f64,f65,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124'
req = urllib.request.Request(em_url, headers={
    'User-Agent': 'Mozilla/5.0', 'Referer': 'http://quote.eastmoney.com'
})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = json.loads(resp.read().decode('utf-8'))
    items = raw.get('data', {}).get('diff', [])
    print(f"东财半导体材料设备板块成分: {len(items)}只", flush=True)
    # 按持仓市值/权重排
    items.sort(key=lambda x: x.get('f62', 0) or 0, reverse=True)
    print("TOP10:", flush=True)
    for it in items[:10]:
        print(f"  {it.get('f14')} {it.get('f12')} 持仓{it.get('f62')}万股 市值{it.get('f64',0)/1e8:.1f}亿", flush=True)
except Exception as e:
    print(f"东财板块数据: {e}", flush=True)

# === 用东财获取159516 ETF持仓 ===
etf_hold_url = 'http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f20&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23&fields=f12,f14,f3,f4,f8,f9,f10,f15,f16,f17,f18'
# 先用指数成分接口
idx_url = 'http://push2.eastmoney.com/api/qt/slist/get?fltt=2&invt=2&fid=f62&fs=id:BK0716&fields=f12,f14,f62,f64&pn=1&pz=100'
req2 = urllib.request.Request(idx_url, headers={
    'User-Agent': 'Mozilla/5.0', 'Referer': 'http://data.eastmoney.com'
})
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        idx_raw = json.loads(resp.read().decode('utf-8'))
    total = idx_raw.get('data', {}).get('total', 0)
    idx_items = idx_raw.get('data', {}).get('diff', [])
    print(f"\n指数BK0716成分: {total}只", flush=True)
    idx_items.sort(key=lambda x: x.get('f62', 0) or 0, reverse=True)
    for it in idx_items[:10]:
        print(f"  {it.get('f14')} {it.get('f12')} 持仓量{it.get('f62')}万股", flush=True)
except Exception as e:
    print(f"指数成分: {e}", flush=True)

# === 用东财搜索931743指数 ===
search_url = 'http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f4,f8&fields=f12,f14'
# 直接搜索半导体设备材料相关板块
bk_search = 'http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f8'
req3 = urllib.request.Request(bk_search, headers={
    'User-Agent': 'Mozilla/5.0', 'Referer': 'http://quote.eastmoney.com'
})
try:
    with urllib.request.urlopen(req3, timeout=10) as resp:
        bk_raw = json.loads(resp.read().decode('utf-8'))
    all_bk = bk_raw.get('data', {}).get('diff', [])
    # 找半导体/芯片/设备相关板块
    semi_bk = [x for x in all_bk if any(k in x.get('f14','') for k in ['半导体','芯片','集成电路','设备','材料'])]
    print(f"\n半导体相关板块共{len(semi_bk)}个:", flush=True)
    semi_bk.sort(key=lambda x: x.get('f3', 0))
    for bk in semi_bk[:20]:
        print(f"  {bk.get('f3',0):>+6.2f}%  {bk.get('f14'):<20} {bk.get('f12')}", flush=True)
except Exception as e:
    print(f"板块搜索: {e}", flush=True)

print("\nDone.", flush=True)
