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
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'ann_d.ps1')
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

good_items = []
bad_items = []

# 1. 用notice查询今天个股公告
print("=== 个股公告搜索（近4天）===\n")
for day in [today, '20260712', '20260711', '20260710']:
    resp = mcp('wenda_notice_query', bdate=day, edate=day)
    if resp:
        data = resp.get('data', [])
        if isinstance(data, list) and len(data) > 1:
            for item in data[1:]:
                if not isinstance(item, list) or len(item) < 3: continue
                title = item[0] if len(item)>0 else ''
                code = item[1] if len(item)>1 else ''
                t_str = item[2] if len(item)>2 else ''
                summary = item[3] if len(item)>3 else ''
                src = item[4] if len(item)>4 else ''
                
                if not title: continue
                
                is_good = any(k in title for k in [
                    '业绩预增', '预盈', '扭亏', '净利润', '中标', '合作', '签约',
                    '回购', '增持', '分红', '涨停', '获批', '通过', '扩产',
                    '量产', '交付', '订单', '超预期', '大幅增长', '同比',
                    '战略合作', '新签订', '上市'
                ])
                is_bad = any(k in title for k in [
                    '警示函', '立案', '处罚', '监管', '减持', '风险', '亏损',
                    '业绩下滑', '终止', '停产', '起诉', '诉讼', '冻结', '造假',
                    '涉嫌', '查封', 'ST', '*ST'
                ])
                
                if is_good and not is_bad:
                    tag = '✅'
                    good_items.append((code, title, t_str, summary))
                    print(f"{tag} [{code}] [{day}] {title}")
                    if summary: print(f"    → {summary[:100]}")
                    print()
                elif is_bad:
                    tag = '🔴'
                    bad_items.append((code, title, t_str, summary))
                    print(f"{tag} [{code}] [{day}] {title}")
                    if summary: print(f"    → {summary[:100]}")
                    print()

print(f"\n=== 统计 ===")
print(f"  利好: {len(good_items)} 条")
print(f"  利空: {len(bad_items)} 条")

# 2. 用新闻查询搜公告精选
print("\n=== 公告精选（周末至今）===\n")
for day in ['20260713', '20260712', '20260711']:
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
                if any(k in title for k in ['公告精选', '业绩', '预增', '中标', '涨停', '回购']):
                    is_good = any(k in title for k in ['业绩预增', '预盈', '扭亏', '中标', '合作', '涨停', '回购', '增持', '扩产', '超预期'])
                    is_bad = any(k in title for k in ['警示函', '立案', '处罚', '亏损', 'ST'])
                    tag = '✅' if is_good else ('🔴' if is_bad else '⚪')
                    print(f"{tag} [{t_str[:16]}] {title}")
                    if summary: print(f"    → {summary[:150]}")
                    print()

print("完成")
