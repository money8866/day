# -*- coding: utf-8 -*-
import subprocess, json, os, sys, datetime
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()
SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'n2.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("=== 午盘主题深度分析  11:30 ===\n")

# 拉所有今日快讯
resp = mcp_call('wenda_news_query', bdate='20260713', edate='20260713')
all_news = []
if resp:
    items = resp.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            src = item[3] if len(item) > 3 else ''
            summary = item[4] if len(item) > 4 else ''
            if title and len(title) > 5:
                all_news.append((t_str, title, summary, src))

all_news.sort(key=lambda x: x[0], reverse=True)
print("今日快讯总数: %d条\n" % len(all_news))

# 全部快讯按时间输出
print("=== 全部快讯 ===")
for t, title, summary, src in all_news:
    s = title + summary
    bull_kw = ['涨停', '上涨', '爆发', '反弹', '突破', '超跌', '净流入', '资金流入', '大涨', '拉升', '拉升', '翻红', '走高']
    bear_kw = ['跌停', '下跌', '暴跌', '恐慌', '杀跌', '砸盘', '净流出', '资金流出', '大跌', '跳水', '领跌', '翻绿', '走低', '走弱']
    is_bull = any(k in s for k in bull_kw)
    is_bear = any(k in s for k in bear_kw)
    if is_bull: tag = "BULL"
    elif is_bear: tag = "BEAR"
    else: tag = "INFO"
    print("[%s][%s] %s" % (tag, t[11:16], title[:70]))
    if summary: print("    %s" % summary[:120])
