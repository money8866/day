#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富API - 高盛公司有限责任公司 十大流通股东持股明细"""
import json, urllib.request

# 东财股东持股明细API - 高盛公司有限责任公司 hdCode=10429689
# 持股明细页: ShareHolderDetail
url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_GDFX_HOLDER_DETAIL&columns=ALL&filter=(HD_CODE%3D%2210429689%22)&pageNumber=1&pageSize=50&sortColumns=END_DATE,HOLD_MARKET_CAP&sortTypes=-1,-1&source=WEB&client=WEB"

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/gdfx/shareholder/10429689.html'
}

try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    
    if data.get('success') and data.get('result'):
        rows = data['result'].get('data', [])
        print("高盛公司有限责任公司 十大流通股东持仓明细")
        print("共 {} 条".format(len(rows)))
        
        # 按报告期分组
        by_period = {}
        for r in rows:
            period = r.get('END_DATE', '')[:10]
            if period not in by_period:
                by_period[period] = []
            by_period[period].append(r)
        
        # 只看最新的报告期
        periods = sorted(by_period.keys(), reverse=True)
        for period in periods[:2]:
            print("\n=== 报告期: {} ===".format(period))
            pr = sorted(by_period[period], key=lambda x: -(x.get('HOLD_MARKET_CAP',0) or 0))
            for i, r in enumerate(pr[:30]):
                code = r.get('SECURITY_CODE','')
                name = r.get('SECURITY_NAME_ABBR','')
                hold_amt = r.get('HOLD_AMOUNT', 0) or 0
                hold_ratio = r.get('HOLD_RATIO', 0) or 0
                hold_cap = r.get('HOLD_MARKET_CAP', 0) or 0
                change = r.get('HOLD_CHANGE', '')
                rank = r.get('HOLDER_RANK', 0)
                
                cap_yi = hold_cap / 100000000 if hold_cap else 0
                amt_wan = hold_amt / 10000 if hold_amt else 0
                
                print("  {:>2}. {}({}) | 第{}大 | 持{:.0f}万股 {:.2f}亿 {:.2f}% | {}".format(
                    i+1, name, code, rank, amt_wan, cap_yi, hold_ratio, change))
    else:
        print("失败:", json.dumps(data, ensure_ascii=False)[:300])
        
        # 试另一个reportName
        for rn in ['RPT_GDFX_HOLDERDETAIL', 'RPT_GDFX_FREEHOLDDETAIL', 'RPT_HOLDER_FREEHOLDDET']:
            url2 = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={}&columns=ALL&filter=(HD_CODE%3D%2210429689%22)&pageNumber=1&pageSize=5&sortColumns=END_DATE&sortTypes=-1&source=WEB&client=WEB".format(rn)
            req2 = urllib.request.Request(url2, headers=headers)
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                d2 = json.loads(resp2.read().decode('utf-8'))
            ok = 'OK' if d2.get('success') else d2.get('message','')[:50]
            print("{} => {}".format(rn, ok))

except Exception as e:
    print("错误:", e)
