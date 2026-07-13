# -*- coding: utf-8 -*-
import subprocess, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'twopm.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== 14:00 REAL-TIME MARKET ===\n")

# Index snapshot
print("--- INDEX SNAPSHOT ---")
大盘 = [
    ('000001', '1', 'ShangZheng', 3996.16),
    ('399001', '0', 'ShenZheng', 15046.67),
    ('399006', '0', 'ChuangYe', 3842.73),
    ('399300', '0', 'HS300', 4780.79),
    ('932000', '1', 'CSI2000', 3272.99),
]
idx_results = []
for code, mkt, name, close_y in 大盘:
    resp = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp and 'HQInfo' in resp:
        hq = resp['HQInfo']
        now = float(hq.get('Now', 0))
        high = float(hq.get('MaxP', 0))
        low = float(hq.get('MinP', 0))
        t = hq.get('HQTime', '')
        pct = (now - close_y) / close_y * 100 if close_y else 0
        amp = (high - low) / close_y * 100
        idx_results.append({'name': name, 'close_y': close_y, 'now': now,
                           'high': high, 'low': low, 'pct': pct, 'amp': amp, 'time': t})
        pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
        print("%-12s now=%.2f  %s%%  H=%.2f L=%.2f  amp=%.2f%%  [%s]" % (
            name, now, pct_str, high, low, amp, t))

# Stocks
print("\n--- STOCKS/ETF ---")
stocks = [
    ('159516', '0', 'SEM_ETF', 0.91),
    ('600036', '1', 'CMBC', 36.88),
    ('600276', '1', 'HengRui', 55.75),
    ('159992', '0', 'InnoPharm_ETF', 0.85),
    ('300083', '0', 'ChuangShiJi', 13.31),
    ('000620', '0', 'YingXin', 3.63),
    ('002965', '0', 'XiangXin', 51.41),
]
for code, mkt, name, y in stocks:
    resp = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp and 'HQInfo' in resp:
        hq = resp['HQInfo']
        now = float(hq.get('Now', 0))
        high = float(hq.get('MaxP', 0))
        low = float(hq.get('MinP', 0))
        pct = (now - y) / y * 100 if y else 0
        pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
        vs_sh = pct - idx_results[0]['pct'] if idx_results else 0
        vs_str = '+%.1f' % vs_sh if vs_sh >= 0 else '%.1f'
        tag = ''
        if pct > 0.5: tag = ' STRONG'
        elif pct < -2.0: tag = ' WEAK'
        print("%-20s now=%.3f  %s%%  vsSH=%s%s" % (name, now, pct_str, vs_str, tag))

# AM vs PM
print("\n--- AM vs PM ---")
# from 11:30 data
am_data = {
    'ShangZheng': -1.54, 'ShenZheng': -2.61, 'ChuangYe': -2.38,
    'HS300': -1.34, 'CSI2000': 0.0
}
for r in idx_results:
    name = r['name']
    if name in am_data:
        am_pct = am_data[name]
        pm_pct = r['pct']
        diff = pm_pct - am_pct
        diff_str = '+%.2f' % diff if diff >= 0 else '%.2f'
        if diff < -0.2:
            trend = 'accelerating down'
        elif diff > 0.2:
            trend = 'recovering'
        else:
            trend = 'stable'
        print("  %-12s  AM=%.2f%%  now=%.2f%%  delta=%s  [%s]" % (
            name, am_pct, pm_pct, diff_str, trend))

# News
print("\n--- LATEST NEWS ---")
resp2 = mcp('wenda_news_query', bdate='20260713', edate='20260713')
bull_n = []
bear_n = []
if resp2:
    items = resp2.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            src = item[3] if len(item) > 3 else ''
            summary = item[4] if len(item) > 4 else ''
            if not title: continue
            s = title + summary
            bk = ['xiaoZhang', 'shangZhang', 'baoFa', 'fanTan', 'tuPo', 'jingLiuRu', 'laSheng']
            rk = ['xiaoDie', 'dieTing', 'BaoXiao', 'KongHuang', 'ShaDie', 'ZhaPan', 'jingLiuChu']
            is_bull = any(k in s for k in bk)
            is_bear = any(k in s for k in rk)
            if is_bull: bull_n.append((t_str, title, summary))
            if is_bear: bear_n.append((t_str, title, summary))

print("  BULL news: %d" % len(bull_n))
for t, title, su in bull_n[:5]:
    print("  + [%s] %s" % (t[11:16], title[:70]))
print("  BEAR news: %d" % len(bear_n))
for t, title, su in bear_n[:5]:
    print("  - [%s] %s" % (t[11:16], title[:70]))

print("\nDone")
