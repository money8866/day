#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富API - 抓取高盛持股明细 - 用浏览器开发者工具发现的正确API"""
import json, urllib.request, urllib.parse

# 东财股东持股页面实际调用的API
# 通过抓包分析，真实URL格式如下:
base = "https://datacenter-web.eastmoney.com/api/data/v1/get"

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Referer': 'https://data.eastmoney.com/gdfx/shareholder/10429689.html',
    'Accept': '*/*',
}

# 尝试不同的filter和reportName组合
configs = [
    # 高盛公司有限责任公司
    {"reportName": "RPT_GDFX_SHAREHOLDERDETAIL", "filter": '(HD_CODE="10429689")'},
    {"reportName": "RPT_GDFX_SHAREHOLDERDETAIL", "filter": '(HOLDER_ID="10429689")'},
    {"reportName": "RPT_GDFX_FREEHOLDERDETAIL", "filter": '(HD_CODE="10429689")'},
    {"reportName": "RPT_SHAREHOLDER_FREEHOLDER", "filter": '(HD_CODE="10429689")'},
    # 也试高盛国际
    {"reportName": "RPT_GDFX_SHAREHOLDERDETAIL", "filter": '(HOLDER_NAME="高盛国际-自有资金")'},
    {"reportName": "RPT_GDFX_SHAREHOLDERDETAIL", "filter": '(HOLDER_NAME="高盛公司有限责任公司")'},
]

for cfg in configs:
    params = urllib.parse.urlencode({
        'reportName': cfg['reportName'],
        'columns': 'ALL',
        'filter': cfg['filter'],
        'pageNumber': 1,
        'pageSize': 5,
        'sortColumns': 'END_DATE',
        'sortTypes': -1,
        'source': 'WEB',
        'client': 'WEB',
    })
    url = base + '?' + params
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode('utf-8'))
        if d.get('success') and d.get('result'):
            print("✓ {} filter={} => {} rows".format(cfg['reportName'], cfg['filter'][:40], len(d['result'].get('data',[]))))
            if d['result'].get('data'):
                print("  keys:", list(d['result']['data'][0].keys())[:8])
        else:
            msg = d.get('message','')[:50]
            print("✗ {} filter={} => {}".format(cfg['reportName'], cfg['filter'][:40], msg))
    except Exception as e:
        print("✗ error:", str(e)[:60])

# 最后试一下通过页面JS加载的数据
# 东财数据中心用emdw模块
url2 = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=SH688559&type=0"
req2 = urllib.request.Request(url2, headers=headers)
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        d2 = resp.read().decode('utf-8')
    print("\nemweb个股股东:", d2[:300])
except Exception as e:
    print("\nemweb:", str(e)[:60])
