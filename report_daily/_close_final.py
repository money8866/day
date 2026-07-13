# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'cl.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== CLOSING DATA 16:10 ===\n")

# Index
大盘 = [
    ('000001', '1', 'ShangZheng', 3996.16),
    ('399001', '0', 'ShenZheng', 15046.67),
    ('399006', '0', 'ChuangYe', 3842.73),
    ('399300', '0', 'HS300', 4780.79),
    ('932000', '1', 'CSI2000', 3272.99),
    ('000852', '1', 'CSI1000', 8278.13),
]
idx_res = []
for code, mkt, name, y_close in 大盘:
    resp = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp and 'HQInfo' in resp:
        hq = resp['HQInfo']
        now = float(hq.get('Now', 0))
        high = float(hq.get('MaxP', 0))
        low = float(hq.get('MinP', 0))
        t = hq.get('HQTime', '')
        pct = (now - y_close) / y_close * 100 if y_close else 0
        amp = (high - low) / y_close * 100
        idx_res.append({'name': name, 'y': y_close, 'close': now, 'high': high, 'low': low, 'pct': pct, 'amp': amp, 'time': t})

# Stocks
stocks = [
    ('159516', '0', 'SEM_ETF', 0.91),
    ('600036', '1', 'CMBC', 36.88),
    ('600276', '1', 'HengRui', 55.75),
    ('159992', '0', 'InnoPharm_ETF', 0.85),
    ('300083', '0', 'ChuangShiJi', 13.31),
    ('000620', '0', 'YingXin', 3.63),
    ('002965', '0', 'XiangXin', 51.41),
    ('300762', '0', 'ShanghaiHanXun', 45.43),
    ('002025', '0', 'HangTianDianQi', 79.29),
    ('688432', '1', 'YouYanGui', 56.60),
    ('688120', '1', 'HuaHaiQingKe', 324.60),
    ('159352', '0', 'Bank_ETF', 1.00),
]
stk_res = []
for code, mkt, name, y_close in stocks:
    resp = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp and 'HQInfo' in resp:
        hq = resp['HQInfo']
        now = float(hq.get('Now', 0))
        high = float(hq.get('MaxP', 0))
        low = float(hq.get('MinP', 0))
        pct = (now - y_close) / y_close * 100 if y_close else 0
        amp = (high - low) / y_close * 100
        stk_res.append({'name': name, 'code': code, 'y': y_close, 'close': now, 'high': high, 'low': low, 'pct': pct, 'amp': amp})

# News
resp2 = mcp('wenda_news_query', bdate='20260713', edate='20260713')
news_list = []
if resp2:
    items = resp2.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            src = item[3] if len(item) > 3 else ''
            summary = item[4] if len(item) > 4 else ''
            if title and len(title) > 5:
                news_list.append((t_str, title, summary, src))

news_list.sort(key=lambda x: x[0], reverse=True)

# Output
print("=== INDEX CLOSING ===")
for r in idx_res:
    pct_str = '+%.2f' % r['pct'] if r['pct'] >= 0 else '%.2f'
    print("%-12s close=%.2f  %s%%  amp=%.2f%%  H=%.2f L=%.2f" % (
        r['name'], r['close'], pct_str, r['amp'], r['high'], r['low']))

print("\n=== STOCKS CLOSING ===")
for r in stk_res:
    pct_str = '+%.2f' % r['pct'] if r['pct'] >= 0 else '%.2f'
    print("%-20s close=%.3f  %s%%  amp=%.2f%%" % (r['name'], r['close'], pct_str, r['amp']))

print("\n=== TODAY NEWS ===")
for t, title, summary, src in news_list[:15]:
    print("[%s] %s" % (t[11:16], title[:70]))

print("\nDone")
