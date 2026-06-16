# -*- coding: utf-8 -*-
"""测试东方财富概念板块HTTP接口"""
import sys
import requests
import json
import time
import random

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://quote.eastmoney.com/',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 1. 获取概念板块列表
print("=" * 60)
print("测试1: 东方财富概念板块列表")
print("=" * 60)
url1 = "https://17.push2.eastmoney.com/api/qing/clist/get"
params1 = {
    "pn": 1,
    "pz": 500,  # 一次拿500个
    "po": 1,
    "np": 1,
    "fltt": 2,
    "invt": 2,
    "fs": "m:90+t:2+f:!50",  # m:90+t:2=概念板块
    "fields": "f1,f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18",
    "fid": "f3",
}
try:
    r = requests.get(url1, params=params1, headers=headers, timeout=10)
    data = r.json()
    if data.get('data') and data['data'].get('diff'):
        concepts = data['data']['diff']
        print(f"✓ 获取 {len(concepts)} 个概念板块")
        print(f"  示例: {concepts[0]}")
        # 找几个关键概念
        for kw in ['AI', 'PCB', '算力', '机器人', '锂电', '光伏', '半导体设备']:
            matches = [c for c in concepts if kw in str(c.get('f14', ''))]
            if matches:
                print(f"  含'{kw}': {len(matches)}个, 例: {matches[0].get('f14')}({matches[0].get('f12')})")
        # 保存前几个的code
        sample_codes = [c['f12'] for c in concepts[:5]]
        print(f"  前5个代码: {sample_codes}")
    else:
        print(f"✗ 返回为空: {data}")
except Exception as e:
    print(f"✗ 失败: {e}")

# 2. 测试获取概念成分股
print("\n" + "=" * 60)
print("测试2: 概念板块成分股 (以'低空经济'为例)")
print("=" * 60)
# 先找到低空经济概念的code
concept_code = None
if data.get('data') and data['data'].get('diff'):
    for c in data['data']['diff']:
        if '低空经济' in str(c.get('f14', '')):
            concept_code = c['f12']
            print(f"找到低空经济: code={concept_code}, name={c['f14']}")
            break

if concept_code:
    url2 = "https://push2.eastmoney.com/api/qing/get"
    params2 = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": f"b:{concept_code}+f:!50",
        "fields": "f1,f2,f3,f4,f5,f6,f12,f14",
    }
    try:
        r2 = requests.get(url2, params=params2, headers=headers, timeout=10)
        data2 = r2.json()
        if data2.get('data') and data2['data'].get('diff'):
            stocks = data2['data']['diff']
            print(f"✓ 低空经济概念成分股 {len(stocks)} 只")
            print(f"  示例: {stocks[0]}")
            for s in stocks[:5]:
                print(f"    {s.get('f12')}: {s.get('f14')}")
        else:
            print(f"✗ 返回为空: {data2}")
    except Exception as e:
        print(f"✗ 失败: {e}")
