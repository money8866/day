# -*- coding: utf-8 -*-
import subprocess, json

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

# 巨潮资讯 专门的研究报告接口
print("=== 巨潮资讯 研究报告 API ===")

# 研究报告 (yjb = 研究报告)
for cat, label in [
    ("category_yjdbg_szsh;", "年度研究报告"),
    ("category_yjkb_szsh;", "业绩快报"),
    ("category_yjps_szsh;", "业绩预测"),
    ("category_yjbg_szsh;", "研究报告"),
]:
    payload = json.dumps({
        "stock": ["603906"],
        "tabName": "fulltext",
        "pageSize": 3,
        "pageNum": 1,
        "column": "szse",
        "category": cat,
        "plate": "",
        "seDate": "2026-01-01~2026-07-10",
        "isHLtitle": True
    })
    # escape for PowerShell
    payload_esc = payload.replace("'", "''")
    rc, out = ps_run(
        "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12; "
        "$body = '%s'; "
        "$headers = @{ 'Content-Type' = 'application/json' }; "
        "irm -Uri 'http://www.cninfo.com.cn/new/hisAnnouncement/query' "
        "-Method POST -Headers $headers -Body $body -TimeoutSec 15" % payload_esc,
        timeout=20
    )
    if out.strip():
        try:
            d = json.loads(out.strip())
            items = d.get("announcements", [])
            total = d.get("totalAnnouncement", 0)
            print("  [%s] total=%d, this_page=%d" % (label, total, len(items)))
            for item in items[:3]:
                title = item.get("announcementTitle", "")
                time = str(item.get("announcementTime", ""))[:10]
                print("    [%s] %s" % (time, title[:80]))
        except Exception as e:
            print("  [%s] 解析失败: %s" % (label, e))
            print("    raw: %s" % out[:200])

# 尝试东财研报页面的XHR接口
print("\n=== 东财研报 XHR 接口 ===")
# 这是浏览器里 East Money 研报详情页面加载数据的方式
for code, name in [("603906", "龙蟠科技"), ("600584", "长电科技")]:
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_OTR_STOCK_RESEARCHINFO&columns=REPORT_DATE,INSTITUTION_NAME,TITLE,SECURITY_CODE,UPSIDEDOWN&filter=(SECURITY_CODE%%3D%%22%s%%22)&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=REPORT_DATE&source=DataCenter&client=PC" % code
    rc, out = ps_run("irm '%s' -TimeoutSec 10" % url, timeout=15)
    if out.strip():
        try:
            d = json.loads(out.strip())
            print("  [%s] RPT_OTR_STOCK_RESEARCHINFO: success=%s msg=%s" % (name, d.get("success"), d.get("message","")))
            if d.get("result") and d["result"].get("data"):
                for item in d["result"]["data"][:3]:
                    print("    - [%s] %s | %s" % (str(item.get("REPORT_DATE",""))[:10], item.get("INSTITUTION_NAME",""), item.get("TITLE","")[:50]))
        except:
            print("  [%s] raw: %s" % (name, out[:200]))

# 东财研报中心的接口 (另一个格式)
url2 = "https://reportapi.eastmoney.com/report/list?cb=datatable&industryCode=*&pageSize=5&pageNum=1&code=603906&endDate=&startDate=&columnCode=*&pageNo=1&qType=0"
rc2, out2 = ps_run("irm '%s' -UserAgent 'Mozilla/5.0' -TimeoutSec 15" % url2, timeout=20)
print("\nreportapi with UA: rc=%d" % rc2)
if out2.strip():
    print("  raw: %s" % out2[:300])

print("\n完成!")
