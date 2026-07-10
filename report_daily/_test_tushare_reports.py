# -*- coding: utf-8 -*-
import subprocess, json, os

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

def mcp_raw(tool, **kwargs):
    args = " ".join(["%s='%s'" % (k,v) for k,v in kwargs.items()])
    ps1 = os.path.join(os.environ.get("TEMP","C:\\temp"), "ex3.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=90)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

SKILL_DIR = r"C:\Users\kongx\.qclaw\skills\tongdaxin-mcp"

# 1. 巨潮资讯 研究报告接口
print("=== 巨潮资讯 研究报告 ===")
url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
payload = json.dumps({
    "stock": ["603906"],
    "tabName": "fulltext",
    "pageSize": 5,
    "pageNum": 1,
    "column": "szse",
    "category": "category_yjdbg_szsh;",
    "plate": "",
    "seDate": "2026-04-01~2026-07-10",
    "isHLtitle": True
})
rc, out = ps_run(
    "irm -Uri '%s' -Method POST -ContentType 'application/json' -Body '%s' -TimeoutSec 15" % (url, payload)
)
if out.strip():
    try:
        d = json.loads(out)
        items = d.get("announcements", [])
        print("  category_yjdbg_szsh: %d 条" % len(items))
        for item in items[:5]:
            print("  - [%s] %s" % (str(item.get("announcementTime","")[:10]), item.get("announcementTitle","")[:80]))
    except Exception as e:
        print("  失败: %s | raw: %s" % (e, out[:200]))

# 研报 其他类别
for cat in ["category_yjkb_szsh;", "category_yjps_szsh;"]:
    payload2 = json.dumps({
        "stock": ["603906"],
        "tabName": "fulltext",
        "pageSize": 3,
        "pageNum": 1,
        "column": "szse",
        "category": cat,
        "plate": "",
        "seDate": "2026-04-01~2026-07-10",
        "isHLtitle": True
    })
    rc2, out2 = ps_run(
        "irm -Uri '%s' -Method POST -ContentType 'application/json' -Body '%s' -TimeoutSec 15" % (url, payload2)
    )
    if out2.strip():
        try:
            d2 = json.loads(out2)
            items2 = d2.get("announcements", [])
            print("  %s: %d 条" % (cat, len(items2)))
            for item in items2[:3]:
                print("  - [%s] %s" % (str(item.get("announcementTime","")[:10]), item.get("announcementTitle","")[:80]))
        except:
            pass

# 2. 东财 数据中心 个股研报（不同格式）
print("\n=== 东财 datacenter 研报 ===")
for rname in ["RPT_STOCK_RESEARCH_TOTAL", "RPT_RESEARCH_REPORT", "RPT_RESEARCH_LIST", "RPT_ANALYST_REPORT"]:
    url2 = (
        "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        "?reportName=%s"
        "&columns=REPORT_DATE,INSTITUTION_NAME,TITLE,SECURITY_CODE"
        "&filter=(SECURITY_CODE%%3D%%22603906%%22)"
        "&pageNumber=1&pageSize=3&sortTypes=-1&sortColumns=REPORT_DATE"
        "&source=DataCenter&client=PC"
    ) % rname
    rc2, out2 = ps_run("irm '%s' -TimeoutSec 10" % url2, timeout=15)
    if out2.strip():
        try:
            d2 = json.loads(out2.strip())
            success = d2.get("success", False)
            msg = d2.get("message", "")
            print("  %s: success=%s msg=%s" % (rname, success, msg[:30]))
        except:
            print("  %s: parse failed" % rname)

# 3. TDX MCP - 找所有包含"研报"或"report"的工具
print("\n=== TDX MCP 工具列表（筛选研报相关） ===")
rc_tok, out_tok = ps_run('& "%s\\get-token.ps1"' % SKILL_DIR)
token = out_tok.strip() if rc_tok == 0 else None
if token:
    rc_c, out_c = ps_run("mcporter config get tdx-finance_qclaw 2>$null")
    if not (rc_c == 0 and token in out_c):
        ps_run("mcporter config remove tdx-finance_qclaw 2>$null")
        ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

    # 用gg_search搜研报试试
    print("\n  gg_search 测试:")
    for kw in ["龙蟠科技 研报", "603906 研究报告", "长电科技 券商研报", "个股研报 2026"]:
        resp = mcp_raw("gg_search", query=kw, count="5")
        if resp:
            print("    [%s] -> %s" % (kw[:20], str(resp)[:100]))
        else:
            print("    [%s] -> 无返回" % kw[:20])

print("\n完成!")
