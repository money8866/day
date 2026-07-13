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
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'ann_t.ps1')
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

# 今天日期搜索
print(f"搜索 {today} 市场公告快讯...\n")
resp = mcp('wenda_news_query', bdate=today, edate=today)
good_stocks = []
bad_stocks = []
neutral_stocks = []

if resp:
    items = resp.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item)>0 else ''
            t_str = item[1] if len(item)>1 else ''
            src = item[3] if len(item)>3 else ''
            summary = item[4] if len(item)>4 else ''
            
            if not title:
                continue
            
            # 利好关键词
            is_good = any(k in title or (summary and k in summary) for k in [
                '业绩', '预增', '扭亏', '大幅增长', '净利润', '中标', '合作',
                '突破', '签约', '订单', '回购', '增持', '分红', '涨停',
                '获批', '注册', '批准', '上市', '通过', '首台', '首条',
                '订单大增', '新签订', '战略合作', '扩产', '量产', '交付',
                '业绩超预期', '超预期', '同比', '加速'
            ])
            
            # 利空关键词
            is_bad = any(k in title or (summary and k in summary) for k in [
                '警示函', '立案', '处罚', '监管', '减持', '风险提示',
                '亏损', '业绩下滑', '大幅下降', '亏损', '终止', '停产',
                '起诉', '诉讼', '仲裁', '查封', '冻结', '造假'
            ])
            
            tag = ''
            if is_good and not is_bad:
                tag = '✅ 利好'
                good_stocks.append((title, t_str, src, summary))
            elif is_bad:
                tag = '🔴 利空'
                bad_stocks.append((title, t_str, src, summary))
            else:
                tag = '⚪ 中性'
                neutral_stocks.append((title, t_str, src, summary))
            
            if '业绩' in tag or '利' in tag or '警示' in tag or '中标' in tag or '合作' in tag or '回购' in tag or '增持' in tag or '订单' in tag:
                print(f"  {tag} [{t_str[:16]}] {title}")
                if summary:
                    print(f"    → {summary[:100]}")
                print()

print(f"\n=== 统计 ===")
print(f"  利好: {len(good_stocks)} 条")
print(f"  利空: {len(bad_stocks)} 条")
print(f"  中性: {len(neutral_stocks)} 条")

# 额外搜索近期业绩超预期公告
print(f"\n\n=== 近3日业绩预增公告 ===")
for day in [today, '20260712', '20260711', '20260710']:
    resp2 = mcp('wenda_news_query', bdate=day, edate=day)
    if resp2:
        items2 = resp2.get('data', [])
        if isinstance(items2, list) and len(items2) > 1:
            for item in items2[1:]:
                if not isinstance(item, list) or len(item) < 4: continue
                title = item[0] if len(item)>0 else ''
                t_str = item[1] if len(item)>1 else ''
                src = item[3] if len(item)>3 else ''
                summary = item[4] if len(item)>4 else ''
                if not title: continue
                if any(k in title or (summary and k in summary) for k in ['业绩预告', '业绩预增', '业绩大幅', '净利润同比', '中报业绩', '半年度业绩', '扭亏', '超预期']):
                    print(f"  ✅ [{t_str[:16]}] {title}")
                    if summary: print(f"    → {summary[:120]}")

print("\n完成")
