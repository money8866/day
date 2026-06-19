#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富API查询高盛前十大股东 - 用正确的报表名"""
import json, urllib.request

# 试多个可能的reportName
report_names = [
    "RPT_INSTITUTIONAL_HOLDNEW",
    "RPT_QFII_HOLDDETAIL",
    "RPT_FUND_HOLDDETAIL",
    "RPT_HOLDERS_NUMCHANGE",
    "RPT_TOP10_FLOATHOLDER",
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://data.eastmoney.com/'
}

for rn in report_names:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={}&columns=ALL&pageNumber=1&pageSize=3&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB".format(rn)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if data.get('success') and data.get('result'):
            print("{} => OK, {} rows".format(rn, len(data['result'].get('data',[]))))
            if data['result'].get('data'):
                print("  columns:", list(data['result']['data'][0].keys())[:10])
        else:
            print("{} => fail: {}".format(rn, data.get('message','')[:60]))
    except Exception as e:
        print("{} => error: {}".format(rn, str(e)[:60]))

# 另一种方式：直接查个股的十大流通股东
print("\n--- 个股十大流通股东 ---")
# 海目星 688559
url2 = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUALHOLD_DETAIL&columns=ALL&filter=(SECURITY_CODE%3D%22688559%22)&pageNumber=1&pageSize=20&sortColumns=HOLD_RATIO&sortTypes=-1&source=WEB&client=WEB"
req2 = urllib.request.Request(url2, headers=headers)
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        d2 = json.loads(resp.read().decode('utf-8'))
    if d2.get('success') and d2.get('result'):
        print("RPT_MUTUALHOLD_DETAIL => OK")
        for r in d2['result'].get('data', [])[:10]:
            print("  {} | {} | {}%".format(
                r.get('HOLDER_NAME',''), r.get('HOLD_AMOUNT',''), r.get('HOLD_RATIO','')))
    else:
        print("RPT_MUTUALHOLD_DETAIL => fail:", d2.get('message','')[:60])
except Exception as e:
    print("error:", str(e)[:60])
