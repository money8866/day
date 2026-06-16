import requests
import time
import json

# 东方财富 行情中心 - 正确filter格式（URL编码的字符串）
print("=== 东方财富 涨停池（URL编码filter）===")
try:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    # 正确格式：filter 作为一个完整字符串参数
    params = {
        "pn": 1, "pz": 5000, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14",
        "filter": "f3>=9.5",
        "_": int(time.time() * 1000)
    }
    r = requests.get(url, params=params, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=15)
    data = r.json()
    total = data.get("data", {}).get("total", 0)
    zt_list = data.get("data", {}).get("diff", [])
    print(f"涨停数: {total}")
    for item in zt_list[:3]:
        print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
except Exception as e:
    print(f"失败: {e}")

print("\n=== 东方财富 跌停池 ===")
try:
    url2 = "https://push2.eastmoney.com/api/qt/clist/get"
    params2 = {
        "pn": 1, "pz": 5000, "po": 0, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14",
        "filter": "f3<=-9.5",
        "_": int(time.time() * 1000)
    }
    r2 = requests.get(url2, params=params2, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=15)
    data2 = r2.json()
    total2 = data2.get("data", {}).get("total", 0)
    dt_list = data2.get("data", {}).get("diff", [])
    print(f"跌停数: {total2}")
    for item in dt_list[:3]:
        print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
except Exception as e:
    print(f"失败: {e}")

# 东方财富 全市场涨跌家数（分市场统计接口）
print("\n=== 东方财富 全市场统计 ===")
try:
    url3 = "https://push2.eastmoney.com/api/qt/stock/get"
    params3 = {
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fltt": 2,
        "invt": 2,
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
        "secid": "1.000001",
        "_": int(time.time() * 1000)
    }
    r3 = requests.get(url3, params=params3, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data3 = r3.json()
    d = data3.get("data", {})
    print(f"上证指数: {d.get('f14')} 现价{d.get('f43')} 涨幅{d.get('f3')}%")
except Exception as e:
    print(f"失败: {e}")

# 尝试东方财富 大盘统计（涨跌家数）
print("\n=== 东方财富 大盘涨跌家数 ===")
try:
    url4 = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params4 = {
        "fltt": 2,
        "invt": 2,
        "fields": "f1,f2,f3,f4,f12,f13,f14",
        "secids": "1.000001,0.399001,0.399006,1.000688,0.399673,0.399005,0.399300",
        "_": int(time.time() * 1000)
    }
    r4 = requests.get(url4, params=params4, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data4 = r4.json()
    if data4.get("data"):
        for item in data4["data"]:
            print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
except Exception as e:
    print(f"失败: {e}")

# 通达信 Level2 数据接口
print("\n=== 通达信 Level2 涨停统计 ===")
try:
    import sys
    sys.path.insert(0, 'd:/mystock/solo')
    from mootdx.quotes import Quotes
    client = Quotes(market='std', ip='60.191.117.167', port=7709)
    # 获取涨停池
    # 涨停 = 涨幅 >= 9.5%
    result = client.block(instrument='A', category=3)  # category=3可能是涨停池
    print(f"block A: {str(result)[:300]}")
except Exception as e:
    print(f"mootdx std失败: {e}")

# 最后确认：用东方财富过滤接口测试（直接看结果数）
print("\n=== 东方财富 filter 确认测试 ===")
try:
    # 全量请求
    url_all = "https://push2.eastmoney.com/api/qt/clist/get"
    params_all = {
        "pn": 1, "pz": 5, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f3",
        "_": int(time.time() * 1000)
    }
    r_all = requests.get(url_all, params=params_all, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=15)
    data_all = r_all.json()
    total_all = data_all.get("data", {}).get("total", 0)
    diff_all = data_all.get("data", {}).get("diff", [])
    # 手动过滤
    zt_count = sum(1 for item in diff_all if item.get('f3', 0) >= 9.5)
    dt_count = sum(1 for item in diff_all if item.get('f3', 0) <= -9.5)
    print(f"全量返回: {total_all}条")
    print(f"前5中 涨停: {zt_count}, 跌停: {dt_count}")
    for item in diff_all:
        print(f"  {item.get('f3','?')}%")
except Exception as e:
    print(f"失败: {e}")
