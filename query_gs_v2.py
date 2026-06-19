#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从东方财富API查询高盛前十大流通股东持仓 v2"""
import json, urllib.request

# 东财机构持股明细API - 用HOLDER_TYPE=QFII过滤
url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_QFII_HOLDNEW&columns=SECURITY_CODE,SECURITY_NAME_ABBR,HOLDER_NAME,HOLD_AMOUNT,HOLD_RATIO,HOLD_MARKET_CAP,HOLD_AMOUNT_CHANGE,END_DATE&filter=(HOLDER_TYPE=%22QFII%22)&pageNumber=1&pageSize=100&sortColumns=HOLD_MARKET_CAP&sortTypes=-1&source=WEB&client=WEB"

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://data.eastmoney.com/'
}

req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.loads(resp.read().decode('utf-8'))

if data.get('result') and data['result'].get('data'):
    rows = data['result']['data']
    # 过滤高盛
    gs_rows = [r for r in rows if '高盛' in (r.get('HOLDER_NAME',''))]
    print("QFII持仓TOP100中高盛相关: {} 条".format(len(gs_rows)))
    for r in gs_rows:
        code = r.get('SECURITY_CODE','')
        name = r.get('SECURITY_NAME_ABBR','')
        holder = r.get('HOLDER_NAME','')
        hold_amt = r.get('HOLD_AMOUNT',0) or 0
        hold_cap = r.get('HOLD_MARKET_CAP',0) or 0
        hold_ratio = r.get('HOLD_RATIO',0) or 0
        end_date = r.get('END_DATE','')
        print("  {}({}) | {} | {}万股 {:.1f}亿 {:.2f}% | {}".format(
            name, code, holder, hold_amt, hold_cap/10000, hold_ratio, end_date[:10]))
    
    # 同时看看TOP10都是谁
    print("\nQFII持股市值TOP10:")
    for i, r in enumerate(rows[:10]):
        code = r.get('SECURITY_CODE','')
        name = r.get('SECURITY_NAME_ABBR','')
        holder = r.get('HOLDER_NAME','')
        hold_cap = r.get('HOLD_MARKET_CAP',0) or 0
        print("  {}. {}({}) | {} | {:.1f}亿".format(i+1, name, code, holder, hold_cap/10000))
else:
    print("无数据:", json.dumps(data, ensure_ascii=False)[:300])
