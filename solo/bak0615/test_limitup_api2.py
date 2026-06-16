import requests
import time

# 东方财富 涨停池（涨幅 >= 9.5%，排序取最大）
print("=== 东方财富 涨停池（实时）===")
try:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 1, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",  # 按涨幅排序
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14",
        "filter": "(f3>=9.5)",
        "_": int(time.time() * 1000)
    }
    r = requests.get(url, params=params, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data = r.json()
    total_zt = data.get("data", {}).get("total", "N/A")
    print(f"涨停数: {total_zt}")
    if data.get("data", {}).get("diff"):
        for item in list(data["data"]["diff"])[:3]:
            print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
except Exception as e:
    print(f"失败: {e}")

# 东方财富 跌停池（涨幅 <= -9.5%）
print("\n=== 东方财富 跌停池（实时）===")
try:
    url2 = "https://push2.eastmoney.com/api/qt/clist/get"
    params2 = {
        "pn": 1, "pz": 1, "po": 0, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14",
        "filter": "(f3<=-9.5)",
        "_": int(time.time() * 1000)
    }
    r2 = requests.get(url2, params=params2, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data2 = r2.json()
    total_dt = data2.get("data", {}).get("total", "N/A")
    print(f"跌停数: {total_dt}")
    if data2.get("data", {}).get("diff"):
        for item in list(data2["data"]["diff"])[:3]:
            print(f"  {item.get('f14','?')} {item.get('f3','?')}%")
except Exception as e:
    print(f"失败: {e}")

# 东方财富 昨日涨停（昨曾涨停但未封住）
print("\n=== 东方财富 炸板池 ===")
try:
    url3 = "https://push2.eastmoney.com/api/qt/clist/get"
    params3 = {
        "pn": 1, "pz": 1, "po": 0, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f1,f2,f3,f12,f14",
        "filter": "(f3>0)(f3<9.5)(f2>=9.8)",
        "_": int(time.time() * 1000)
    }
    r3 = requests.get(url3, params=params3, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data3 = r3.json()
    total_zb = data3.get("data", {}).get("total", "N/A")
    print(f"炸板数: {total_zb}")
except Exception as e:
    print(f"失败(炸板): {e}")

# 东方财富 全市场统计（上涨/下跌家数）
print("\n=== 东方财富 全市场涨跌家数 ===")
try:
    url4 = "https://push2.eastmoney.com/api/qt/stock/get"
    params4 = {
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fltt": 2,
        "invt": 2,
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f12,f14",
        "secid": "1.000001",
        "_": int(time.time() * 1000)
    }
    r4 = requests.get(url4, params=params4, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    data4 = r4.json()
    print(f"上证指数数据: {str(data4)[:300]}")
except Exception as e:
    print(f"失败: {e}")

# 通达信 涨跌停数据
print("\n=== 通达信 涨跌停数据（通过mootdx）===")
try:
    from mootdx.quotes import Quotes
    client = Quotes(method=' TCP', ip='60.191.117.167', port=7709)
    if client._sd is None:
        print("通达信连接失败")
    else:
        # 获取涨停跌停统计
        # 涨停: f3 >= 9.5
        zt_data = client.stock(instrument='000001.SH', category=1)
        print(f"上证日线: {str(zt_data)[:200]}")
except Exception as e:
    print(f"mootdx失败: {e}")
