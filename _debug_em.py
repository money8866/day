# -*- coding: utf-8 -*-
"""调试东财API原始响应"""
import urllib.request
import json

# 尝试不同接口
tests = [
    "https://push2.eastmoney.com/api/qt/stock/get?secid=1.932000&fields=f43,f44,f45,f46,f60,f168,f169,f170&fltt=2&invt=2",
    "https://push2.eastmoney.com/api/qt/stock/get?secid=1.932000&fields=f43,f57,f58,f60,f169,f170&fltt=1&invt=2",
    "https://82.push2.eastmoney.com/api/qt/stock/get?secid=1.932000&fields=f43,f57,f58,f60,f169,f170&fltt=2&invt=2&_=",
]

for i, url in enumerate(tests):
    print(f"\n=== Test {i+1} ===")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode('utf-8')
        print(f"原始响应(前300字): {raw[:300]}")
        data = json.loads(raw)
        print(f"rc: {data.get('rc')}  rcv: {data.get('rcv')}  data存在: {'data' in data}")
        if data.get('data'):
            print(f"data: {data['data']}")
    except Exception as e:
        print(f"失败: {e}")
