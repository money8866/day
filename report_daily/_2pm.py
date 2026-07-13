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

print("=== 14:00 实时盘面分析 ===\n")

# 指数快照
print("--- 指数实时 ---")
大盘 = [
    ('000001', '1', '上证指数', 3996.16),
    ('399001', '0', '深证成指', 15046.67),
    ('399006', '0', '创业板指', 3842.73),
    ('399300', '0', '沪深300', 4780.79),
    ('932000', '1', '中证2000', 3272.99),
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
        idx_results.append({
            'name': name, 'close_y': close_y, 'now': now,
            'high': high, 'low': low, 'pct': pct, 'amp': amp, 'time': t
        })
        pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
        amp_str = '%.2f' % amp
        print("%s: %.2f  %s%%  H=%.2f L=%.2f 震幅%s%% [%s]" % (
            name, now, pct_str, high, low, amp_str, t))

# 个股/ETF实时
print("\n--- 持仓与关注股 ---")
stocks = [
    ('159516', '0', '半导体设备ETF(持仓)'),
    ('600036', '1', '招商银行'),
    ('600276', '1', '恒瑞医药'),
    ('159992', '0', '创新药ETF'),
    ('300083', '0', '创世纪(推荐)'),
    ('000620', '0', '盈新发展(推荐)'),
    ('002965', '0', '祥鑫科技(推荐)'),
]
基准 = {'159516': 0.91, '600036': 36.88, '600276': 55.75, '159992': 0.85,
        '300083': 13.31, '000620': 3.63, '002965': 51.41}
for code, mkt, name in stocks:
    resp = mcp('tdx_quotes', code=code, setcode=mkt)
    if resp and 'HQInfo' in resp:
        hq = resp['HQInfo']
        now = float(hq.get('Now', 0))
        high = float(hq.get('MaxP', 0))
        low = float(hq.get('MinP', 0))
        t = hq.get('HQTime', '')
        y = 基准.get(code, 0)
        pct = (now - y) / y * 100 if y else 0
        pct_str = '+%.2f' % pct if pct >= 0 else '%.2f'
        vs_sh = pct - idx_results[0]['pct'] if idx_results else 0
        vs_str = '+%.1f' % vs_sh if vs_sh >= 0 else '%.1f'
        mark = ''
        if pct > 0.5: mark = '<<<
        elif pct < -2: mark = '>>>
        print("%-22s %.3f  %s%% %s" % (name, now, pct_str, mark))

# 上午 vs 下午对比
print("\n--- 上午 vs 下午 涨跌对比 ---")
# 上午收盘估算(用11:30数据)
am = {
    '上证指数': -1.54, '深证成指': -2.61, '创业板指': -2.38,
    '沪深300': -1.34, '招商银行': 1.06, '恒瑞医药': -1.0,
    '半导体设备ETF(持仓)': -3.3, '创世纪(推荐)': -11.7
}
for r in idx_results:
    pct = r['pct']
    name = r['name']
    am_pct = am.get(name, 0)
    diff = pct - am_pct
    diff_str = '+%.2f' % diff if diff >= 0 else '%.2f'
    trend = '扩大跌幅' if diff < -0.2 else ('收窄跌幅' if diff > 0.2 else '基本持平')
    if name in am:
        print("  %s: 上午%.2f%% -> 现在%.2f%% 变化%s (%s)" % (
            name, am_pct, pct, diff_str, trend))

# 快讯
print("\n--- 14:00 最新快讯 ---")
resp2 = mcp('wenda_news_query', bdate='20260713', edate='20260713')
bull_news = []
bear_news = []
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
            bull_kw = ['涨停', '上涨', '反弹', '爆发', '突破', '净流入', '资金流入', '拉升', '走强']
            bear_kw = ['跌停', '下跌', '暴跌', '恐慌', '杀跌', '砸盘', '净流出', '资金流出', '大跌', '跳水', '领跌']
            is_bull = any(k in s for k in bull_kw)
            is_bear = any(k in s for k in bear_kw)
            if is_bull: bull_news.append((t_str, title, summary))
            if is_bear: bear_news.append((t_str, title, summary))

print("  强势快讯: %d条" % len(bull_news))
for t, title, su in bull_news[:3]:
    print("  + [%s] %s" % (t[11:16], title[:70]))
print("  弱势快讯: %d条" % len(bear_news))
for t, title, su in bear_news[:3]:
    print("  - [%s] %s" % (t[11:16], title[:70]))

print("\nDone")
