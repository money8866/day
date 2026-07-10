# -*- coding: utf-8 -*-
import subprocess, json, os

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

SKILL_DIR = r"C:\Users\kongx\.qclaw\skills\tongdaxin-mcp"

def mcp_raw(tool, **kwargs):
    args = " ".join(["%s='%s'" % (k,v) for k,v in kwargs.items()])
    ps1 = os.path.join(os.environ.get("TEMP","C:\\temp"), "ex4.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=90)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

# Init MCP
rc_tok, out_tok = ps_run('& "%s\\get-token.ps1"' % SKILL_DIR)
token = out_tok.strip() if rc_tok == 0 else None
if token:
    rc_c, out_c = ps_run("mcporter config get tdx-finance_qclaw 2>$null")
    if not (rc_c == 0 and token in out_c):
        ps_run("mcporter config remove tdx-finance_qclaw 2>$null")
        ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

# 测试 gg_search 完整功能
print("=== gg_search 完整测试 ===")
tests = [
    ("gg_search", {"query": "603906 研究 评级", "count": "5"}),
    ("gg_search", {"query": "龙蟠科技 券商研报 2026", "count": "5"}),
    ("gg_search", {"query": "长电科技 研究报告 评级", "count": "5"}),
]
for tool, params in tests:
    resp = mcp_raw(tool, **params)
    print("\n[%s] %s" % (tool, params["query"]))
    if resp:
        print("  data: %s" % str(resp)[:300])
    else:
        print("  无返回")

# 测试 tdx_api_data
print("\n=== tdx_api_data 测试 ===")
resp = mcp_raw("tdx_api_data", mode="raw", entry="report", code="603906", count="5")
print("tdx_api_data(raw,entry=report): %s" % (str(resp)[:200] if resp else "无返回"))

# 测试东方财富浏览器研报页面 (HTML)
print("\n=== 东财研报中心 页面抓取 ===")
url = "https://data.eastmoney.com/stockdata/research.html"
rc, out = ps_run("(irm '%s' -TimeoutSec 15).Content | Select-String -Pattern 'title|report|研报' | Select-Object -First 10" % url, timeout=20)
if out.strip():
    print("HTML内容片段: %s" % out[:400])
else:
    print("无输出")

# 测试东财研报API（换格式试试）
print("\n=== 东财研报 API v2 ===")
# 研报中心列表页的XHR接口
url2 = "https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_LICO_FN_CPD&columns=SECURITY_CODE,REPORT_DATE,ORG_NAME,TITLE&filter=(SECURITY_CODE%3D%22603906%22)&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=REPORT_DATE&source=DataCenter&client=PC"
rc2, out2 = ps_run("irm '%s' -TimeoutSec 15" % url2, timeout=20)
if out2.strip():
    try:
        d = json.loads(out2.strip())
        print("RPT_LICO_FN_CPD: success=%s msg=%s" % (d.get("success"), d.get("message","")))
    except:
        print("raw: %s" % out2[:200])

# 测试研报下载接口
print("\n=== 研报下载 ===")
# 东方财富研报详情页
url3 = "https://data.eastmoney.com/stockdata/research/603906.html"
rc3, out3 = ps_run("(irm '%s' -TimeoutSec 15).Links | Where-Object { $_.href -match 'report|research' } | Select-Object -First 5 -ExpandProperty href" % url3, timeout=20)
print("研报链接: %s" % out3[:300] if out3.strip() else "无")

print("\n完成!")
