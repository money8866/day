#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从东方财富API查询高盛前十大流通股东持仓"""
import json, time
try:
    import urllib.request
    import urllib.parse
except:
    pass

# 东财QFII持仓查询API
# 高盛国际-自有资金的机构ID
gs_name = "高盛"

# 方法1: 东财机构持股查询
# URL: https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_QFII_HOLDNEW&columns=ALL&filter=(HOLDER_NAME%20like%20%22%25%E9%AB%98%E7%9B%9B%25%22)&pageNumber=1&pageSize=50&sortColumns=HOLD_MARKET_CAP&sortTypes=-1

url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_QFII_HOLDNEW&columns=ALL&filter=(HOLDER_NAME%20like%20%22%25%E9%AB%98%E7%9B%9B%25%22)&pageNumber=1&pageSize=50&sortColumns=HOLD_MARKET_CAP&sortTypes=-1"

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/'
}

req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    
    if data.get('result') and data['result'].get('data'):
        rows = data['result']['data']
        print("高盛前十大流通股东持仓 (按持股市值排序)")
        print("共 {} 条记录".format(len(rows)))
        print()
        
        for i, r in enumerate(rows[:50]):
            code = r.get('SECURITY_CODE', '')
            name = r.get('SECURITY_NAME_ABBR', '')
            holder = r.get('HOLDER_NAME', '')
            hold_amt = r.get('HOLD_AMOUNT', 0) or 0  # 万股
            hold_ratio = r.get('HOLD_RATIO', 0) or 0  # %
            hold_cap = r.get('HOLD_MARKET_CAP', 0) or 0  # 万元
            change = r.get('HOLD_AMOUNT_CHANGE', 0) or 0
            end_date = r.get('END_DATE', '')
            
            cap_yi = hold_cap / 10000  # 万元 -> 亿
            print("{:>2}. {}({}) | {} | 持{}万股市值{:.1f}亿占比{:.2f}% | 变动{}万股 | {}".format(
                i+1, name, code, holder,
                hold_amt/10000 if hold_amt > 10000 else hold_amt,
                cap_yi, hold_ratio,
                change/10000 if abs(change) > 10000 else change,
                end_date[:10] if end_date else ''))
    else:
        print("API返回无数据")
        print(json.dumps(data, ensure_ascii=False)[:500])
except Exception as e:
    print("错误: {}".format(e))
    
    # 备用: 试试另一个接口
    url2 = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_QFII_HOLDNEW&columns=ALL&filter=(HOLDER_NAME%20like%20%22%25%E9%AB%98%E7%9B%9B%25%22)&pageNumber=1&pageSize=5&sortColumns=REPORT_DATE&sortTypes=-1"
    req2 = urllib.request.Request(url2, headers=headers)
    try:
        with urllib.request.urlopen(req2, timeout=15) as resp2:
            d2 = json.loads(resp2.read().decode('utf-8'))
        print("\n备用结果:", json.dumps(d2, ensure_ascii=False)[:500])
    except Exception as e2:
        print("备用也失败: {}".format(e2))
