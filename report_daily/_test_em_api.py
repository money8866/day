# -*- coding: utf-8 -*-
import subprocess, json

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr

# 东财reportapi - 个股研报
codes = [("603906", "龙蟠科技"), ("600584", "长电科技")]

for code, name in codes:
    print("\n=== %s (%s) ===" % (name, code))

    # 方式1: 研报列表 API
    url = (
        "https://reportapi.eastmoney.com/report/list?"
        "cb=datatable&industryCode=*&pageSize=5&pageNum=1"
        "&code=%s&endDate=&startDate=&columnCode=*&pageNo=1&qType=0"
    ) % code

    rc, out, err = ps_run("irm '%s' -TimeoutSec 15" % url)
    if out.strip():
        text = out.strip()
        if text.startswith("datatable("):
            text = text[len("datatable("):-1]
        try:
            data = json.loads(text)
            items = data.get("data", []) or []
            print("  reportapi: %d 条" % len(items))
            for item in items[:5]:
                print("    [%s] %s | %s | %s" % (
                    str(item.get("publishDate",""))[:10],
                    item.get("orgName",""),
                    item.get("researchType",""),
                    item.get("title","")[:60]
                ))
        except Exception as e:
            print("  解析失败: %s | raw: %s" % (e, text[:300]))
    else:
        print("  无输出 | err: %s" % err[:200])

    # 方式2: 搜索 API
    url2 = "https://search-api.eastmoney.com/search/jsonp?cb=datatable&param={%22uid%22:%22%22,%22keyword%22:%22" + code + "%E8%82%A1%E7%A5%A8%20%E7%A0%94%E6%8A%A5%22,%22type%22:[5],%22pageIndex%22:1,%22pageSize%22:5,%22dateRange%22:180}".replace('"', '%22').replace('{', '%7B').replace('}', '%7D')
    # 简化版
    url2 = (
        "https://search-api-web.eastmoney.com/search/jsonp"
        "?uid=&keyword=" + code + "%E8%82%A1%E7%A5%A8+%E7%A0%94%E6%8A%A5"
        "&type=[5]&pageIndex=1&pageSize=5"
    )

    rc2, out2, _ = ps_run("irm '%s' -TimeoutSec 15" % url2)
    if out2.strip():
        print("  search-api: %s" % out2.strip()[:300])
    else:
        print("  search-api: 无返回")

    # 方式3: 东方财富研报 专门接口
    url3 = "https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_RESEARCH_TOTAL&columns=REPORT_DATE,INSTITUTION_NAME,TITLE,SECURITY_CODE&filter=(SECURITY_CODE%3D%22" + code + "%22)&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=REPORT_DATE&source=DataCenter&client=PC"
    rc3, out3, _ = ps_run("irm '%s' -TimeoutSec 15" % url3)
    if out3.strip():
        try:
            d = json.loads(out3.strip())
            print("  RPT_RESEARCH_TOTAL: success=%s" % d.get("success"))
            if d.get("result"):
                print("    data: %s" % str(d["result"].get("data",""))[:200])
        except:
            print("  RPT_RESEARCH_TOTAL raw: %s" % out3.strip()[:300])

    # 方式4: 问财 个股研报详情接口
    url4 = "https://www.iwencai.com/stockpick/search?typed=1&preParams=&ts=1&f=1&qs=result_rewrite&selfspts1=&selfspts2=&w=" + code + "%E8%82%A1%E7%A5%A8%E7%A0%94%E6%8A%A5&peerstockcodes=&in crom_timestamp=1"
    print("  iwencai: %s" % url4[:100])
