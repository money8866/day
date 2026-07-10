# -*- coding: utf-8 -*-
import subprocess, json, os

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr

SKILL_DIR = r"C:\Users\kongx\.qclaw\skills\tongdaxin-mcp"

def mcp_raw(tool, **kwargs):
    args = " ".join(["%s='%s'" % (k,v) for k,v in kwargs.items()])
    ps1 = os.path.join(os.environ.get("TEMP","C:\\temp"), "ex2.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

# 测试巨潮资讯 API
print("=== 巨潮资讯 个股研报 ===")
# 巨潮资讯搜索接口
url1 = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
data1 = json.dumps({
    "stock": ["603906"],
    "tabName": "fulltext",
    "pageSize": 5,
    "pageNum": 1,
    "column": "szse",
    "category": "category_ndbg_szsh;",
    "plate": "",
    "seDate": "2026-04-01~2026-07-10",
    "isHLtitle": True
})
rc, out, err = ps_run(
    "irm -Uri '%s' -Method POST -ContentType 'application/json' -Body '%s' -TimeoutSec 15" % (url1, data1),
    timeout=20
)
print("巨潮 cninfo API: rc=%d" % rc)
if out.strip():
    try:
        d = json.loads(out)
        items = d.get("announcements", [])
        print("  共 %d 条" % len(items))
        for item in items[:5]:
            print("  - [%s] %s" % (str(item.get("announcementTime","")[:10]), item.get("announcementTitle","")[:60]))
    except:
        print("  raw: %s" % out[:400])
else:
    print("  err: %s" % err[:200])

# 测试东财 研报中心 API
print("\n=== 东方财富 研报中心 ===")
# 这是东财的研报搜索页面用的API
url2 = "https://reportapi.eastmoney.com/report/list?cb=datatable&industryCode=*&pageSize=5&pageNum=1&code=603906&endDate=&startDate=&columnCode=*&pageNo=1&qType=0"
rc2, out2, err2 = ps_run("irm '%s' -TimeoutSec 15" % url2, timeout=20)
print("reportapi: rc=%d" % rc2)
if out2.strip():
    print("  raw: %s" % out2[:400])
else:
    print("  err: %s" % err2[:300])

# 东财 研报列表（另一格式）
url3 = "https://datacenter.eastmoney.com/api/data/v1/get?reportName=RPT_STOCK_RESEARCH_TOTAL&columns=REPORT_DATE,INSTITUTION_NAME,TITLE&filter=(SECURITY_CODE%3D%22603906%22)&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=REPORT_DATE&source=DataCenter"
rc3, out3, _ = ps_run("irm '%s' -TimeoutSec 15" % url3, timeout=20)
print("\nRPT_STOCK_RESEARCH_TOTAL: rc=%d" % rc3)
if out3.strip():
    try:
        d3 = json.loads(out3)
        print("  success=%s, msg=%s" % (d3.get("success"), d3.get("message","")))
        print("  raw: %s" % out3[:300])
    except:
        print("  raw: %s" % out3[:300])

# TDX MCP 研报工具深度测试
print("\n=== TDX MCP 研报工具 深度测试 ===")
rc_tok, out_tok, _ = ps_run('& "%s\\get-token.ps1"' % SKILL_DIR, timeout=15)
token = out_tok.strip() if rc_tok == 0 else None
if token:
    rc_c, out_c, _ = ps_run("mcporter config get tdx-finance_qclaw 2>$null")
    if not (rc_c == 0 and token in out_c):
        ps_run("mcporter config remove tdx-finance_qclaw 2>$null")
        ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

    # 问财研报换关键词试试
    tests = [
        ("wenda_report_query", {"query": "603906|20260601|20260710|", "pageSize": "5"}),
        ("wenda_report_query", {"query": "龙蟠科技 研究报告|20260601|20260710|", "pageSize": "5"}),
        ("wenda_report_query", {"query": "长电科技 券商研报|20260601|20260710|", "pageSize": "5"}),
    ]
    for tool, params in tests:
        print("\n  [%s] query=%s" % (tool, params["query"][:30]))
        resp = mcp_raw(tool, **params)
        if resp:
            data = resp.get("data", [])
            print("    返回 %d 条" % len(data))
            for item in data[:3]:
                if isinstance(item, list):
                    title = str(item[0])[:80]
                    print("    - %s" % title)
                elif isinstance(item, dict):
                    print("    - %s" % str(item)[:80])
        else:
            print("    无返回")
else:
    print("Token获取失败")
