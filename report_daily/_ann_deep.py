# -*- coding: utf-8 -*-
import subprocess, json, os, datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
today = datetime.date.today().strftime('%Y%m%d')

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'ann_f.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        return None
    try:
        return json.loads(out.strip())
    except:
        return None

keywords = ['业绩预增', '业绩预告', '中标', '合作', '回购', '增持', '涨停', '扩产', '量产', '超预期', '扭亏', '战略合作', '订单']

all_good = []
all_bad = []

# 近3天，每天搜不同关键词
print("=== 深度搜索公告（7/11-7/13）===\n")
for day in ['20260713', '20260712', '20260711']:
    print(f"--- {day} ---")
    for kw in keywords:
        resp = mcp('wenda_news_query', bdate=day, edate=day)
        if resp:
            items = resp.get('data', [])
            if isinstance(items, list) and len(items) > 1:
                for item in items[1:]:
                    if not isinstance(item, list) or len(item) < 4: continue
                    title = item[0] if len(item)>0 else ''
                    t_str = item[1] if len(item)>1 else ''
                    src = item[3] if len(item)>3 else ''
                    summary = item[4] if len(item)>4 else ''
                    if not title: continue
                    if kw in title or (summary and kw in summary):
                        is_bad = any(b in title for b in ['*ST', 'ST', '警示函', '立案', '处罚', '亏损', '造假', '冻结'])
                        tag = '✅' if not is_bad else '🔴'
                        print(f"  {tag} {title[:60]}")
                        if summary: print(f"       → {summary[:100]}")
                        print()
    print()

# 搜业绩预告专题
print("=== 业绩预告专题（周末至今日）===\n")
for day in ['20260713', '20260712', '20260711', '20260710']:
    resp2 = mcp('wenda_news_query', bdate=day, edate=day)
    if resp2:
        items = resp2.get('data', [])
        if isinstance(items, list) and len(items) > 1:
            for item in items[1:]:
                if not isinstance(item, list) or len(item) < 4: continue
                title = item[0] if len(item)>0 else ''
                t_str = item[1] if len(item)>1 else ''
                src = item[3] if len(item)>3 else ''
                summary = item[4] if len(item)>4 else ''
                if not title: continue
                if any(k in title for k in ['业绩预告', '半年度', '中报业绩', '净利润', '预计']):
                    is_bad = any(b in title for b in ['*ST', 'ST', '警示函', '立案', '处罚', '亏损', '造假', '冻结', '涉嫌'])
                    tag = '✅' if not is_bad else '🔴'
                    print(f"{tag} [{t_str[:16]}] {title}")
                    if summary: print(f"  → {summary[:150]}")
                    print()

print("完成")
